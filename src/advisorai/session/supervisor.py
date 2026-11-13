"""Single-owner session supervisor: one lock, crash-aware start, clean stop.

The supervisor owns the workstation lifecycle, not trading authority. It
acquires an application lock, classifies the prior stop (clean vs crash),
records immutable lifecycle events in the mission ledger, tracks worker
leases, and releases everything on stop. It never invents account, order,
or market state.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers

from .records import (
    DataGapRecord,
    DataGapResolutionRecord,
    PITSnapshotVerification,
    RecoveryMode,
    RecoveryRecord,
    RuntimeLeaseRecord,
    SessionCheckpoint,
    SessionEndRecord,
    SessionOutcome,
    SessionStartRecord,
    SourceWatermarkRecord,
)


class SessionZeroStateError(RuntimeError):
    """Ledger state is unreadable; restart must not pretend recovery succeeded."""


class SessionCheckpointHash:
    """Deterministic, verifiable checkpoint identity over real session state."""

    @staticmethod
    def compute(
        *,
        session_id: UUID,
        ledger_offsets: dict[str, int],
        artifact_ids: tuple[UUID, ...],
        watermarks: dict[str, str],
        pending_work: tuple[str, ...],
        code_hash: str,
        config_hash: str,
    ) -> str:
        import hashlib
        import json

        payload = {
            "session_id": str(session_id),
            "ledger_offsets": dict(sorted(ledger_offsets.items())),
            "artifact_ids": sorted(str(item) for item in artifact_ids),
            "watermarks": dict(sorted(watermarks.items())),
            "pending_work": sorted(pending_work),
            "code_hash": code_hash,
            "config_hash": config_hash,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class SessionRecoveryChecks:
    """Mandatory, testable recovery verifications run at session start.

    Each check queries real state — ledgers, order/account stores, source
    watermarks — and returns pass/fail. A NORMAL recovery mode is recorded
    only when every check passes. Subclass or inject test doubles; the
    default implementation performs honest structural checks against the
    ledgers and fails closed when stores are absent.
    """

    def __init__(
        self,
        *,
        current_order_account_verifier: Callable[[SqliteLedgers], bool] | None = None,
        current_watermark_verifier: Callable[[SqliteLedgers, dict[str, str]], bool] | None = None,
        current_pit_verifier: Callable[[SqliteLedgers, PITSnapshotVerification], bool]
        | None = None,
        current_gap_coverage_verifier: (
            Callable[[SqliteLedgers, DataGapRecord, DataGapResolutionRecord], bool] | None
        ) = None,
        prepare_recovery: Callable[[SqliteLedgers], bool] | None = None,
    ) -> None:
        self.current_order_account_verifier = current_order_account_verifier
        self.current_watermark_verifier = current_watermark_verifier
        self.current_pit_verifier = current_pit_verifier
        self.current_gap_coverage_verifier = current_gap_coverage_verifier
        self.prepare_recovery = prepare_recovery

    def verify_all(self, ledgers: SqliteLedgers) -> dict[str, bool]:
        if self.prepare_recovery is not None:
            try:
                if not self.prepare_recovery(ledgers):
                    return {
                        "ledger_integrity": self.verify_ledger_integrity(ledgers),
                        "account_order_reconciliation": False,
                        "source_freshness": self.verify_source_freshness(ledgers),
                        "pit_rebuild": self.verify_pit_rebuild(ledgers),
                    }
            except Exception:
                return {
                    "ledger_integrity": self.verify_ledger_integrity(ledgers),
                    "account_order_reconciliation": False,
                    "source_freshness": self.verify_source_freshness(ledgers),
                    "pit_rebuild": self.verify_pit_rebuild(ledgers),
                }
        return {
            "ledger_integrity": self.verify_ledger_integrity(ledgers),
            "account_order_reconciliation": self.verify_account_order_reconciliation(ledgers),
            "source_freshness": self.verify_source_freshness(ledgers),
            "pit_rebuild": self.verify_pit_rebuild(ledgers),
        }

    def verify_ledger_integrity(self, ledgers: SqliteLedgers) -> bool:
        try:
            for namespace in LedgerNamespace:
                ledgers.events(namespace)
        except Exception:
            return False
        return True

    def verify_account_order_reconciliation(self, ledgers: SqliteLedgers) -> bool:
        """Verify each order's latest state, then query the permitted account source.

        An old ``order_ack_ambiguous`` or ``order_routed`` event is historical
        evidence, not the current state. The latest immutable order transition
        decides whether local work is still unresolved. Once orders exist, a
        callback must also compare the current account/open-order projection
        with the permitted paper/testnet venue adapter.
        """
        try:
            latest = _latest_order_states(ledgers)
            if any(
                event_type == "order_ack_ambiguous" or state == "routed"
                for event_type, state in latest.values()
            ):
                return False
            if not latest:
                return True
            if self.current_order_account_verifier is None:
                return False
            return bool(self.current_order_account_verifier(ledgers))
        except Exception:
            return False

    def verify_source_freshness(self, ledgers: SqliteLedgers) -> bool:
        """Require explicit, coverage-verified resolution for every recorded gap."""
        try:
            gaps: dict[UUID, DataGapRecord] = {}
            resolutions: dict[UUID, DataGapResolutionRecord] = {}
            for event in ledgers.events(LedgerNamespace.MISSION):
                if event.event_type == "data_gap_recorded":
                    payload = event.payload.get("gap")
                    if isinstance(payload, dict):
                        gap = DataGapRecord.model_validate(payload)
                        gaps[gap.artifact_id] = gap
                elif event.event_type == "data_gap_resolved":
                    payload = event.payload.get("resolution")
                    if isinstance(payload, dict):
                        resolution = DataGapResolutionRecord.model_validate(payload)
                        resolutions[resolution.gap_id] = resolution
            for gap_id, gap in gaps.items():
                resolution = resolutions.get(gap_id)
                if resolution is None:
                    return False
                if (
                    resolution.source != gap.source
                    or resolution.instrument != gap.instrument
                    or resolution.covered_through < gap.gap_end
                ):
                    return False
                if self.current_gap_coverage_verifier is None or not bool(
                    self.current_gap_coverage_verifier(ledgers, gap, resolution)
                ):
                    return False
            watermarks = _latest_source_watermarks(ledgers.events(LedgerNamespace.MISSION))
            if watermarks:
                if self.current_watermark_verifier is None or not bool(
                    self.current_watermark_verifier(ledgers, watermarks)
                ):
                    return False
        except Exception:
            return False
        return True

    def verify_pit_rebuild(self, ledgers: SqliteLedgers) -> bool:
        """Require a verified snapshot/checkpoint when prior history exists.

        A first start on empty history has nothing to rebuild. A restart needs
        the latest session's latest resumable checkpoint and a PIT verification
        referenced by that checkpoint; an older checkpoint cannot satisfy it.
        """
        try:
            events = ledgers.events(LedgerNamespace.MISSION)
        except Exception:
            return False
        starts = [
            SessionStartRecord.model_validate(event.payload["start"])
            for event in events
            if event.event_type == "session_started"
            and isinstance(event.payload.get("start"), dict)
        ]
        if not starts:
            return True
        try:
            latest_start = starts[-1]
            checkpoint = _latest_checkpoint(events, latest_start.session_id)
            if checkpoint is None or not checkpoint.resumable:
                return False
            if not _checkpoint_is_valid(checkpoint, latest_start):
                return False
            verifications = [
                PITSnapshotVerification.model_validate(event.payload["verification"])
                for event in events
                if event.event_type == "pit_snapshot_verified"
                and isinstance(event.payload.get("verification"), dict)
                and str(event.payload["verification"].get("session_id"))
                == str(latest_start.session_id)
            ]
            if not verifications:
                # Empty sessions with no source-derived state have nothing to
                # rebuild. A paper cycle or any source watermark requires a
                # source-bound verification record.
                has_cycle = any(
                    event.event_type == "paper_runtime_cycle"
                    and isinstance(event.payload.get("cycle"), dict)
                    and str(event.payload["cycle"].get("owner_session_id"))
                    == str(latest_start.session_id)
                    for event in events
                )
                return not has_cycle and not checkpoint.artifact_ids and not checkpoint.watermarks
            verification = verifications[-1]
            if (
                verification.snapshot_id not in checkpoint.artifact_ids
                or checkpoint.watermarks != verification.watermarks
            ):
                return False
            return self.current_pit_verifier is not None and bool(
                self.current_pit_verifier(ledgers, verification)
            )
        except Exception:
            return False


def _latest_order_states(ledgers: SqliteLedgers) -> dict[UUID, tuple[str, str]]:
    latest: dict[UUID, tuple[str, str]] = {}
    for event in ledgers.events(LedgerNamespace.ORDER):
        if not event.event_type.startswith("order_"):
            continue
        artifact = event.payload.get("artifact")
        if not isinstance(artifact, dict):
            continue
        raw_id = artifact.get("artifact_id", event.payload.get("artifact_id"))
        state = artifact.get("state")
        if raw_id is None or not isinstance(state, str):
            continue
        latest[UUID(str(raw_id))] = (event.event_type, state)
    return latest


def _latest_checkpoint(events, session_id: UUID) -> SessionCheckpoint | None:
    latest = None
    for event in events:
        if event.event_type != "session_checkpoint":
            continue
        payload = event.payload.get("checkpoint")
        if not isinstance(payload, dict):
            continue
        checkpoint = SessionCheckpoint.model_validate(payload)
        if checkpoint.session_id == session_id:
            latest = checkpoint
    return latest


def _checkpoint_is_valid(checkpoint: SessionCheckpoint, start: SessionStartRecord) -> bool:
    if checkpoint.session_id != start.session_id:
        return False
    expected_hash = SessionCheckpointHash.compute(
        session_id=checkpoint.session_id,
        ledger_offsets=checkpoint.ledger_offsets,
        artifact_ids=checkpoint.artifact_ids,
        watermarks=checkpoint.watermarks,
        pending_work=checkpoint.pending_work,
        code_hash=start.code_hash,
        config_hash=start.config_hash,
    )
    return checkpoint.checkpoint_hash == expected_hash


def _latest_source_watermarks(events) -> dict[str, str]:
    latest: dict[str, str] = {}
    for event in events:
        if event.event_type != "source_watermark_recorded":
            continue
        payload = event.payload.get("watermark")
        if not isinstance(payload, dict):
            continue
        watermark = SourceWatermarkRecord.model_validate(payload)
        latest[watermark.key] = watermark.value
    return latest


class SessionStopChecks:
    """Mandatory, testable shutdown verifications run at session stop."""

    def __init__(
        self,
        *,
        current_order_account_verifier: Callable[[SqliteLedgers], bool] | None = None,
        current_watermark_verifier: Callable[[SqliteLedgers, dict[str, str]], bool] | None = None,
    ) -> None:
        self.current_order_account_verifier = current_order_account_verifier
        self.current_watermark_verifier = current_watermark_verifier

    def verify_all(self, ledgers: SqliteLedgers) -> dict[str, bool]:
        return {
            "order_reconciliation": self.verify_order_reconciliation(ledgers),
            "ledger_flush": self.verify_ledger_flush(ledgers),
            "watermark_persistence": self.verify_watermark_persistence(ledgers),
        }

    def verify_order_reconciliation(self, ledgers: SqliteLedgers) -> bool:
        try:
            latest = _latest_order_states(ledgers)
            if any(
                event_type == "order_ack_ambiguous"
                or state
                in {"routed", "risk_approved", "created", "partially_filled", "cancel_pending"}
                or state in {"filled", "cancelled", "rejected", "expired"}
                for event_type, state in latest.values()
            ):
                return False
            if not latest:
                return True
            if self.current_order_account_verifier is None:
                return False
            return bool(self.current_order_account_verifier(ledgers))
        except Exception:
            return False

    def verify_ledger_flush(self, ledgers: SqliteLedgers) -> bool:
        try:
            for namespace in LedgerNamespace:
                ledgers.events(namespace)
        except Exception:
            return False
        return True

    def verify_watermark_persistence(self, ledgers: SqliteLedgers) -> bool:
        try:
            events = ledgers.events(LedgerNamespace.MISSION)
            starts = [
                SessionStartRecord.model_validate(event.payload["start"])
                for event in events
                if event.event_type == "session_started"
                and isinstance(event.payload.get("start"), dict)
            ]
            if not starts:
                return False
            start = starts[-1]
            checkpoint = _latest_checkpoint(events, start.session_id)
            if checkpoint is None or not _checkpoint_is_valid(checkpoint, start):
                return False
            latest_watermarks = _latest_source_watermarks(events)
            if checkpoint.watermarks != latest_watermarks:
                return False
            if latest_watermarks:
                return self.current_watermark_verifier is not None and bool(
                    self.current_watermark_verifier(ledgers, latest_watermarks)
                )
            return True
        except Exception:
            return False


class WorkerRegistry:
    """Actual worker/resource liveness, not ledger bookkeeping.

    The default registry tracks processes spawned through it. Production
    wiring registers real model/collector worker PIDs; tests register fakes.
    Anything still live at stop time is a leak and fails the stop.
    """

    def __init__(self) -> None:
        self._live: set[str] = set()
        self._workers: dict[str, object] = {}

    def register(self, worker_identity: str) -> None:
        self._live.add(worker_identity.strip())

    def deregister(self, worker_identity: str) -> None:
        self._live.discard(worker_identity.strip())
        self._workers.pop(worker_identity.strip(), None)

    def register_worker(self, worker_identity: str, worker: object) -> None:
        identity = worker_identity.strip()
        if not identity:
            raise ValueError("worker identity cannot be blank")
        if not callable(getattr(worker, "is_alive", None)) and not callable(
            getattr(worker, "poll", None)
        ):
            raise TypeError("managed worker must expose is_alive() or poll()")
        self._live.add(identity)
        self._workers[identity] = worker

    def join_workers(self, *, timeout: float | None = None) -> None:
        for worker in tuple(self._workers.values()):
            join = getattr(worker, "join", None)
            if callable(join):
                join(timeout=timeout)

    def live_workers(self) -> tuple[str, ...]:
        import os

        still_live: list[str] = []
        for identity in sorted(self._live):
            worker = self._workers.get(identity)
            if worker is not None:
                is_alive = getattr(worker, "is_alive", None)
                poll = getattr(worker, "poll", None)
                if callable(is_alive) and not is_alive():
                    self._live.discard(identity)
                    self._workers.pop(identity, None)
                    continue
                if callable(poll) and poll() is not None:
                    self._live.discard(identity)
                    self._workers.pop(identity, None)
                    continue
                still_live.append(identity)
                continue
            if identity.startswith("pid:"):
                try:
                    os.kill(int(identity[4:]), 0)
                except (OSError, ValueError):
                    continue
                still_live.append(identity)
            else:
                still_live.append(identity)
        return tuple(still_live)


class SessionSupervisor:
    """Own exactly one supervised session for this owner workstation."""

    def __init__(
        self,
        ledgers: SqliteLedgers,
        *,
        state_dir: Path,
        lock_identity: str,
        code_hash: str,
        config_hash: str,
    ) -> None:
        if not lock_identity.strip():
            raise ValueError("lock identity cannot be blank")
        if not code_hash.strip() or not config_hash.strip():
            raise ValueError("code and config hashes are required")
        self.ledgers = ledgers
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.state_dir.chmod(0o700)
        except OSError:
            pass
        self.lock_identity = lock_identity.strip()
        self.code_hash = code_hash.strip()
        self.config_hash = config_hash.strip()
        self.session_id: UUID | None = None
        self._lock_handle = None
        self._lock_retained_for_workers = False

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "advisorai-session.lock"

    def start(
        self,
        *,
        reason: str,
        recovery_checks: SessionRecoveryChecks | None = None,
    ) -> tuple[SessionStartRecord, RecoveryRecord]:
        """Acquire the owner lock, classify history, verify recovery, record start.

        Crash detection reads the full authoritative event history: every
        ``session_started`` must have a matching ``session_ended`` for the
        same session ID. An unmatched start — even when an older clean
        ``session_ended`` exists — is a crash. Recovery is verified through
        the injected checks (account/order reconciliation, ledger integrity,
        source freshness, PIT rebuild); a ``recovery_mode`` of NORMAL is
        recorded only when every mandatory check passes, otherwise RECOVERY
        or ABSTAIN and no new proposals may proceed.
        """
        if self.session_id is not None:
            raise RuntimeError("a session is already supervised by this object")
        if self._lock_retained_for_workers:
            raise RuntimeError("owner lock is retained until the prior managed worker exits")
        if self._lock_handle is None:
            self._acquire_lock()
        try:
            history = self._session_history()
        except Exception as exc:
            self._release_lock()
            raise SessionZeroStateError(f"ledger state is unreadable: {exc}") from exc
        classification, fault, prior_session_id = self._classify_history(history)
        base_mode = (
            RecoveryMode.NORMAL
            if classification in {"first_start", "clean_start"}
            else RecoveryMode.RECOVERY
        )
        checks = recovery_checks or SessionRecoveryChecks()
        verified = checks.verify_all(self.ledgers)
        stale_leases: tuple[RuntimeLeaseRecord, ...] = ()
        if classification == "crash_recovery" and prior_session_id is not None:
            stale_leases, live_leases = self._classify_crash_leases(prior_session_id)
            verified["stale_worker_leases"] = not live_leases
        failed = [name for name, passed in verified.items() if not passed]
        if failed:
            mode = RecoveryMode.ABSTAIN
            decisions = ("abstain_new_proposals", *[f"failed_check:{name}" for name in failed])
        elif base_mode is RecoveryMode.RECOVERY:
            mode = RecoveryMode.RECOVERY
            decisions = ("reconcile_before_new_proposals",)
        else:
            mode = RecoveryMode.NORMAL
            decisions = ()
        released_leases = self._release_stale_crash_leases(stale_leases) if not failed else ()
        rebuild_actions = (
            *tuple(f"verified:{name}" for name, passed in verified.items() if passed),
            *tuple(f"released_stale_lease:{lease_id}" for lease_id in released_leases),
        )
        reconciliation_actions = tuple(
            f"pending:{name}" for name, passed in verified.items() if not passed
        )
        session_id = uuid4()
        prior_end_id = (
            history["latest_end"].artifact_id if history["latest_end"] is not None else None
        )
        start_record = SessionStartRecord(
            session_id=session_id,
            lock_identity=self.lock_identity,
            code_hash=self.code_hash,
            config_hash=self.config_hash,
            prior_end_record_id=prior_end_id,
            start_reason=reason.strip() or "owner_start",
            recovery_classification=classification,
        )
        recovery = RecoveryRecord(
            session_id=session_id,
            prior_session_id=prior_session_id,
            detected_fault=fault,
            rebuild_actions=rebuild_actions,
            reconciliation_actions=reconciliation_actions,
            decisions=decisions,
            recovery_mode=mode,
        )
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type="session_started",
                idempotency_key=f"session-started:{session_id}",
                payload={"start": start_record.model_dump(mode="json", round_trip=True)},
            )
        )
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type="recovery_recorded",
                idempotency_key=f"session-recovery:{session_id}",
                payload={"recovery": recovery.model_dump(mode="json", round_trip=True)},
            )
        )
        self.session_id = session_id
        self._write_lock_metadata()
        return start_record, recovery

    def checkpoint(
        self,
        *,
        ledger_offsets: dict[str, int] | None = None,
        artifact_ids: tuple[UUID, ...] | None = None,
        watermarks: dict[str, str] | None = None,
        pending_work: tuple[str, ...] | None = None,
        resumable: bool = True,
        checkpoint_hash: str | None = None,
    ) -> SessionCheckpoint:
        self._require_active()
        assert self.session_id is not None
        previous = None
        for event in reversed(self.ledgers.events(LedgerNamespace.MISSION)):
            if event.event_type != "session_checkpoint":
                continue
            raw = event.payload.get("checkpoint")
            if isinstance(raw, dict):
                candidate = SessionCheckpoint.model_validate(raw)
                if candidate.session_id == self.session_id:
                    previous = candidate
                    break
        offsets = dict(ledger_offsets) if ledger_offsets is not None else self._ledger_offsets()
        artifacts = tuple(
            previous.artifact_ids if artifact_ids is None and previous else artifact_ids or ()
        )
        marks = dict(previous.watermarks if watermarks is None and previous else watermarks or {})
        pending = tuple(
            previous.pending_work if pending_work is None and previous else pending_work or ()
        )
        computed = SessionCheckpointHash.compute(
            session_id=self.session_id,
            ledger_offsets=offsets,
            artifact_ids=artifacts,
            watermarks=marks,
            pending_work=pending,
            code_hash=self.code_hash,
            config_hash=self.config_hash,
        )
        if checkpoint_hash is not None and checkpoint_hash != computed:
            raise ValueError("checkpoint hash does not match verified session state")
        existing = self._checkpoint_by_hash(computed)
        if existing is not None:
            return existing
        record = SessionCheckpoint(
            session_id=self.session_id,
            ledger_offsets=offsets,
            artifact_ids=artifacts,
            watermarks=marks,
            pending_work=pending,
            resumable=resumable,
            checkpoint_hash=computed,
        )
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type="session_checkpoint",
                idempotency_key=f"session-checkpoint:{self.session_id}:{record.checkpoint_hash}",
                payload={"checkpoint": record.model_dump(mode="json", round_trip=True)},
            )
        )
        return record

    def _checkpoint_by_hash(self, checkpoint_hash: str) -> SessionCheckpoint | None:
        assert self.session_id is not None
        for event in reversed(self.ledgers.events(LedgerNamespace.MISSION)):
            if event.event_type != "session_checkpoint":
                continue
            payload = event.payload.get("checkpoint")
            if not isinstance(payload, dict):
                continue
            try:
                checkpoint = SessionCheckpoint.model_validate(payload)
            except Exception:
                continue
            if (
                checkpoint.session_id == self.session_id
                and checkpoint.checkpoint_hash == checkpoint_hash
            ):
                return checkpoint
        return None

    def record_data_gap(self, gap: DataGapRecord) -> DataGapRecord:
        self._require_active()
        if gap.backfilled:
            raise ValueError("record a gap as unresolved; use resolve_data_gap after verification")
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type="data_gap_recorded",
                idempotency_key=f"data-gap:{gap.source}:{gap.instrument}:{gap.gap_start.isoformat()}",
                payload={"gap": gap.model_dump(mode="json", round_trip=True)},
            )
        )
        return gap

    def resolve_data_gap(
        self,
        gap_id: UUID,
        *,
        covered_through: datetime,
        watermark: str,
        evidence_id: UUID,
    ) -> DataGapResolutionRecord:
        self._require_active()
        gaps = {
            gap.artifact_id: gap
            for event in self.ledgers.events(LedgerNamespace.MISSION)
            if event.event_type == "data_gap_recorded"
            and isinstance(event.payload.get("gap"), dict)
            for gap in (DataGapRecord.model_validate(event.payload["gap"]),)
        }
        gap = gaps.get(gap_id)
        if gap is None:
            raise KeyError(f"unknown data gap {gap_id}")
        resolution = DataGapResolutionRecord(
            gap_id=gap_id,
            source=gap.source,
            instrument=gap.instrument,
            covered_through=covered_through,
            watermark=watermark.strip(),
            evidence_id=evidence_id,
        )
        if resolution.covered_through < gap.gap_end:
            raise ValueError("backfill does not cover the complete data gap")
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type="data_gap_resolved",
                idempotency_key=f"data-gap-resolved:{gap_id}:{resolution.watermark}",
                payload={"resolution": resolution.model_dump(mode="json", round_trip=True)},
            )
        )
        return resolution

    def record_watermark(
        self,
        *,
        source: str,
        dataset: str,
        value: str,
        observed_at: datetime,
    ) -> SourceWatermarkRecord:
        self._require_active()
        watermark = SourceWatermarkRecord(
            source=source.strip(),
            dataset=dataset.strip(),
            value=value.strip(),
            observed_at=observed_at,
        )
        latest = _latest_source_watermarks(self.ledgers.events(LedgerNamespace.MISSION))
        if latest.get(watermark.key) == watermark.value:
            return watermark
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type="source_watermark_recorded",
                idempotency_key=f"source-watermark:{watermark.key}:{watermark.value}",
                payload={"watermark": watermark.model_dump(mode="json", round_trip=True)},
            )
        )
        return watermark

    def record_pit_snapshot(
        self,
        *,
        snapshot_id: UUID,
        snapshot_hash: str,
        source: str,
        cutoff: datetime,
        first_available_at: datetime,
        watermarks: dict[str, str] | None = None,
    ) -> PITSnapshotVerification:
        self._require_active()
        assert self.session_id is not None
        verification = PITSnapshotVerification(
            session_id=self.session_id,
            snapshot_id=snapshot_id,
            snapshot_hash=snapshot_hash,
            source=source.strip(),
            cutoff=cutoff,
            first_available_at=first_available_at,
            watermarks=dict(watermarks or {}),
        )
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type="pit_snapshot_verified",
                idempotency_key=(
                    f"pit-snapshot:{self.session_id}:{snapshot_id}:{verification.snapshot_hash}"
                ),
                payload={"verification": verification.model_dump(mode="json", round_trip=True)},
            )
        )
        return verification

    def acquire_lease(self, *, worker_identity: str, resource_envelope: str) -> RuntimeLeaseRecord:
        self._require_active()
        assert self.session_id is not None
        identity = worker_identity.strip() or "unknown-worker"
        self._verify_worker_alive(identity)
        for existing in self._open_leases():
            if existing.worker_identity == identity:
                raise RuntimeError(f"lease already held for worker {identity!r}")
        lease = RuntimeLeaseRecord(
            session_id=self.session_id,
            worker_identity=identity,
            resource_envelope=resource_envelope.strip() or "default",
        )
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type="lease_acquired",
                idempotency_key=f"lease-acquired:{lease.lease_id}",
                payload={"lease": lease.model_dump(mode="json", round_trip=True)},
            )
        )
        return lease

    def release_lease(self, lease_id: UUID, *, outcome: str = "released") -> RuntimeLeaseRecord:
        self._require_active()
        open_leases = {lease.lease_id: lease for lease in self._open_leases()}
        lease = open_leases.get(lease_id)
        if lease is None:
            raise KeyError(f"no open lease {lease_id}")
        released = lease.model_copy(
            update={
                "released_at": datetime.now(UTC),
                "cleanup_outcome": outcome.strip() or "released",
            }
        )
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type="lease_released",
                idempotency_key=f"lease-released:{lease_id}",
                payload={"lease": released.model_dump(mode="json", round_trip=True)},
            )
        )
        return released

    def stop(
        self,
        *,
        outcome: SessionOutcome = SessionOutcome.CLEAN,
        stop_checks: SessionStopChecks | None = None,
        open_orders: tuple[UUID, ...] = (),
        unfinished_work: tuple[str, ...] = (),
        worker_registry: WorkerRegistry | None = None,
    ) -> SessionEndRecord:
        """Stop only after verified reconciliation and real lease cleanup.

        ``stop_checks`` runs the mandatory shutdown verifications (order
        reconciliation, ledger flush, watermark persistence). A CLEAN outcome
        is recorded only when every check passes; otherwise the outcome is
        forced to UNCLEAN and the failures stay explicit. Lease cleanup is
        verified against the worker registry, not ledger bookkeeping alone.
        """
        self._require_active()
        assert self.session_id is not None
        # Persist the exact shutdown projection before asking stop checks to
        # verify watermarks and recovery material.
        self.checkpoint()
        checks = stop_checks or SessionStopChecks()
        verified = checks.verify_all(self.ledgers)
        failed = [name for name, passed in verified.items() if not passed]
        active_session_id = self.session_id
        registry = worker_registry or WorkerRegistry()
        leaked = registry.live_workers()
        for lease in self._open_leases():
            if lease.session_id == active_session_id and lease.worker_identity not in leaked:
                self.release_lease(lease.lease_id, outcome="released_on_stop")
        if leaked:
            failed = [*failed, *[f"worker_still_live:{name}" for name in leaked]]
        leases_released = not self._open_leases() and not leaked
        if failed or not leases_released:
            outcome = SessionOutcome.UNCLEAN
            unfinished = (*unfinished_work, *[f"unverified:{name}" for name in failed])
            reconciliation_state = "verification_failed"
        else:
            unfinished = tuple(unfinished_work)
            reconciliation_state = "reconciled"
        record = SessionEndRecord(
            session_id=self.session_id,
            outcome=outcome,
            end_offsets=self._ledger_offsets(),
            reconciliation_state=reconciliation_state,
            open_orders=tuple(open_orders),
            unfinished_work=unfinished,
            leases_released=leases_released,
        )
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type="session_ended",
                idempotency_key=f"session-ended:{self.session_id}",
                payload={"end": record.model_dump(mode="json", round_trip=True)},
            )
        )
        self.session_id = None
        if not self._open_leases() and not leaked:
            self._lock_retained_for_workers = False
            self._release_lock()
        else:
            # The owner process must not admit a replacement while a managed
            # worker or unresolved resource lease could still be active. A
            # process exit releases the OS lock after its non-daemon workers
            # have stopped.
            self._lock_retained_for_workers = True
        return record

    def active_session_id(self) -> UUID | None:
        return self.session_id

    def _require_active(self) -> None:
        if self.session_id is None:
            raise RuntimeError("no supervised session is active")

    def _session_history(self) -> dict:
        """Read the full authoritative session event history."""
        starts: dict[UUID, SessionStartRecord] = {}
        ends: dict[UUID, SessionEndRecord] = {}
        latest_end: SessionEndRecord | None = None
        for event in self.ledgers.events(LedgerNamespace.MISSION):
            payload_start = event.payload.get("start")
            payload_end = event.payload.get("end")
            if event.event_type == "session_started" and isinstance(payload_start, dict):
                record = SessionStartRecord.model_validate(payload_start)
                starts[record.session_id] = record
            elif event.event_type == "session_ended" and isinstance(payload_end, dict):
                record = SessionEndRecord.model_validate(payload_end)
                ends[record.session_id] = record
                latest_end = record
        return {"starts": starts, "ends": ends, "latest_end": latest_end}

    def _classify_history(self, history: dict) -> tuple[str, str, UUID | None]:
        """Classify from full history: any unmatched start is a crash.

        An older clean ``session_ended`` does not excuse a later
        ``session_started`` without its own matching end. The prior session
        ID is the unmatched start when one exists, else the latest end.
        """
        starts: dict[UUID, SessionStartRecord] = history["starts"]
        ends: dict[UUID, SessionEndRecord] = history["ends"]
        if not starts:
            return "first_start", "no prior session record", None
        unmatched = [sid for sid in starts if sid not in ends]
        if unmatched:
            crashed = unmatched[-1]
            return (
                "crash_recovery",
                f"unmatched session_started without session_ended: {crashed}",
                crashed,
            )
        latest_end: SessionEndRecord | None = history["latest_end"]
        assert latest_end is not None
        if latest_end.outcome is SessionOutcome.CLEAN:
            return "clean_start", "prior session stopped cleanly", latest_end.session_id
        return (
            "crash_recovery",
            f"prior session ended {latest_end.outcome.value}",
            latest_end.session_id,
        )

    def _open_leases(self) -> tuple[RuntimeLeaseRecord, ...]:
        acquired: dict[UUID, RuntimeLeaseRecord] = {}
        released: set[UUID] = set()
        for event in self.ledgers.events(LedgerNamespace.MISSION):
            payload = event.payload.get("lease")
            if not isinstance(payload, dict):
                continue
            if event.event_type == "lease_acquired":
                lease = RuntimeLeaseRecord.model_validate(payload)
                acquired[lease.lease_id] = lease
            elif event.event_type == "lease_released":
                lease = RuntimeLeaseRecord.model_validate(payload)
                released.add(lease.lease_id)
        return tuple(lease for lease_id, lease in acquired.items() if lease_id not in released)

    def _classify_crash_leases(
        self, prior_session_id: UUID
    ) -> tuple[tuple[RuntimeLeaseRecord, ...], tuple[RuntimeLeaseRecord, ...]]:
        """Separate dead owner-thread leases from workers that may still live."""
        import os

        stale: list[RuntimeLeaseRecord] = []
        live_or_unknown: list[RuntimeLeaseRecord] = []
        for lease in self._open_leases():
            if lease.session_id != prior_session_id:
                continue
            identity = lease.worker_identity
            if identity.startswith("thread:"):
                # The old owner process no longer holds the OS lock, so a
                # thread owned by that process cannot still be executing.
                stale.append(lease)
                continue
            if identity.startswith("pid:"):
                try:
                    os.kill(int(identity[4:]), 0)
                except ProcessLookupError:
                    stale.append(lease)
                except (OSError, ValueError):
                    live_or_unknown.append(lease)
                else:
                    live_or_unknown.append(lease)
                continue
            # A name without process ownership cannot be proven dead from
            # ledger history. Keep it active and fail closed.
            live_or_unknown.append(lease)
        return tuple(stale), tuple(live_or_unknown)

    def _release_stale_crash_leases(
        self, leases: tuple[RuntimeLeaseRecord, ...]
    ) -> tuple[UUID, ...]:
        released_ids: list[UUID] = []
        for lease in leases:
            released = lease.model_copy(
                update={
                    "released_at": datetime.now(UTC),
                    "cleanup_outcome": "released_after_owner_crash",
                }
            )
            self.ledgers.append(
                LedgerEvent(
                    namespace=LedgerNamespace.MISSION,
                    event_type="lease_released",
                    idempotency_key=f"lease-released:{lease.lease_id}",
                    payload={"lease": released.model_dump(mode="json", round_trip=True)},
                )
            )
            released_ids.append(lease.lease_id)
        return tuple(released_ids)

    @staticmethod
    def _verify_worker_alive(identity: str) -> None:
        """Refuse phantom leases: a pid-backed identity must resolve live now.

        Plain named workers (test doubles, named services) pass through;
        ``pid:<int>`` identities are checked with signal 0 so a dead PID
        fails at acquire time instead of recording a phantom lease.
        """
        if not identity.startswith("pid:"):
            return
        import os

        try:
            os.kill(int(identity[4:]), 0)
        except (OSError, ValueError):
            raise RuntimeError(f"worker {identity!r} is not alive; lease refused") from None

    def _ledger_offsets(self) -> dict[str, int]:
        offsets = {
            namespace.value: len(self.ledgers.events(namespace)) for namespace in LedgerNamespace
        }
        # Checkpoint rows are self-describing snapshots and are excluded from
        # the mission offset to keep a repeated checkpoint of unchanged state
        # idempotent instead of making the checkpoint itself change its hash.
        offsets[LedgerNamespace.MISSION.value] -= sum(
            event.event_type == "session_checkpoint"
            for event in self.ledgers.events(LedgerNamespace.MISSION)
        )
        return offsets

    def _acquire_lock(self) -> None:
        if self._lock_handle is not None:
            return
        if fcntl is None:  # pragma: no cover - non-POSIX fallback
            raise RuntimeError("session lock requires fcntl on this workstation")
        import os

        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.lock_path, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "r+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise RuntimeError("another AdvisorAI session holds the application lock") from None
        self._lock_handle = handle
        self._write_lock_metadata()

    def _write_lock_metadata(self) -> None:
        if self._lock_handle is None:
            return
        import json
        import os

        metadata = {
            "pid": os.getpid(),
            "lock_identity": self.lock_identity,
            "session_id": str(self.session_id) if self.session_id is not None else None,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        handle = self._lock_handle
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(metadata, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())

    def _release_lock(self) -> None:
        handle, self._lock_handle = self._lock_handle, None
        if handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


__all__ = [
    "SessionCheckpointHash",
    "SessionRecoveryChecks",
    "SessionStopChecks",
    "SessionSupervisor",
    "SessionZeroStateError",
    "WorkerRegistry",
]
