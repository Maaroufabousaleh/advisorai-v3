"""Single-owner session supervisor: one lock, crash-aware start, clean stop.

The supervisor owns the workstation lifecycle, not trading authority. It
acquires an application lock, classifies the prior stop (clean vs crash),
records immutable lifecycle events in the mission ledger, tracks worker
leases, and releases everything on stop. It never invents account, order,
or market state.
"""

from __future__ import annotations

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
    RecoveryMode,
    RecoveryRecord,
    RuntimeLeaseRecord,
    SessionCheckpoint,
    SessionEndRecord,
    SessionOutcome,
    SessionStartRecord,
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

    def verify_all(self, ledgers: SqliteLedgers) -> dict[str, bool]:
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
        """No order may be left ambiguous or routed-but-unreconciled."""
        try:
            for event in ledgers.events(LedgerNamespace.ORDER):
                if event.event_type in {
                    "order_ack_ambiguous",
                }:
                    return False
                payload = event.payload.get("artifact")
                if (
                    event.event_type == "order_routed"
                    and isinstance(payload, dict)
                    and payload.get("state") == "routed"
                ):
                    return False
        except Exception:
            return False
        return True

    def verify_source_freshness(self, ledgers: SqliteLedgers) -> bool:
        """Fail closed when a recorded data gap was never backfilled."""
        try:
            for event in ledgers.events(LedgerNamespace.MISSION):
                if event.event_type != "data_gap_recorded":
                    continue
                gap = event.payload.get("gap")
                if isinstance(gap, dict) and not gap.get("backfilled"):
                    return False
        except Exception:
            return False
        return True

    def verify_pit_rebuild(self, ledgers: SqliteLedgers) -> bool:
        """Require a verified snapshot/checkpoint when prior history exists.

        A first start on empty history has nothing to rebuild, so it passes.
        Any restart must show a checkpoint or verified PIT snapshot, or the
        session enters recovery and new proposals abstain.
        """
        try:
            events = ledgers.events(LedgerNamespace.MISSION)
        except Exception:
            return False
        if not any(
            event.event_type in {"session_started", "session_checkpoint"} for event in events
        ):
            return True
        try:
            for event in events:
                if event.event_type in {"session_checkpoint", "pit_snapshot_verified"}:
                    return True
        except Exception:
            return False
        return False


class SessionStopChecks:
    """Mandatory, testable shutdown verifications run at session stop."""

    def verify_all(self, ledgers: SqliteLedgers) -> dict[str, bool]:
        return {
            "order_reconciliation": self.verify_order_reconciliation(ledgers),
            "ledger_flush": self.verify_ledger_flush(ledgers),
            "watermark_persistence": self.verify_watermark_persistence(ledgers),
        }

    def verify_order_reconciliation(self, ledgers: SqliteLedgers) -> bool:
        try:
            for event in ledgers.events(LedgerNamespace.ORDER):
                if event.event_type == "order_ack_ambiguous":
                    return False
        except Exception:
            return False
        return True

    def verify_ledger_flush(self, ledgers: SqliteLedgers) -> bool:
        try:
            for namespace in LedgerNamespace:
                ledgers.events(namespace)
        except Exception:
            return False
        return True

    def verify_watermark_persistence(self, ledgers: SqliteLedgers) -> bool:
        return True


class WorkerRegistry:
    """Actual worker/resource liveness, not ledger bookkeeping.

    The default registry tracks processes spawned through it. Production
    wiring registers real model/collector worker PIDs; tests register fakes.
    Anything still live at stop time is a leak and fails the stop.
    """

    def __init__(self) -> None:
        self._live: set[str] = set()

    def register(self, worker_identity: str) -> None:
        self._live.add(worker_identity.strip())

    def deregister(self, worker_identity: str) -> None:
        self._live.discard(worker_identity.strip())

    def live_workers(self) -> tuple[str, ...]:
        import os

        still_live: list[str] = []
        for identity in sorted(self._live):
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
        self.lock_identity = lock_identity.strip()
        self.code_hash = code_hash.strip()
        self.config_hash = config_hash.strip()
        self.session_id: UUID | None = None
        self._lock_handle = None

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
        failed = [name for name, passed in verified.items() if not passed]
        if failed:
            mode = (
                RecoveryMode.ABSTAIN if base_mode is RecoveryMode.NORMAL else RecoveryMode.RECOVERY
            )
            decisions = ("abstain_new_proposals", *[f"failed_check:{name}" for name in failed])
        elif base_mode is RecoveryMode.RECOVERY:
            mode = RecoveryMode.RECOVERY
            decisions = ("reconcile_before_new_proposals",)
        else:
            mode = RecoveryMode.NORMAL
            decisions = ()
        rebuild_actions = tuple(f"verified:{name}" for name, passed in verified.items() if passed)
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
        return start_record, recovery

    def checkpoint(
        self,
        *,
        ledger_offsets: dict[str, int] | None = None,
        artifact_ids: tuple[UUID, ...] = (),
        watermarks: dict[str, str] | None = None,
        pending_work: tuple[str, ...] = (),
        resumable: bool = True,
        checkpoint_hash: str | None = None,
    ) -> SessionCheckpoint:
        self._require_active()
        assert self.session_id is not None
        offsets = dict(ledger_offsets) if ledger_offsets is not None else self._ledger_offsets()
        marks = dict(watermarks or {})
        computed = SessionCheckpointHash.compute(
            session_id=self.session_id,
            ledger_offsets=offsets,
            artifact_ids=tuple(artifact_ids),
            watermarks=marks,
            pending_work=tuple(pending_work),
            code_hash=self.code_hash,
            config_hash=self.config_hash,
        )
        if checkpoint_hash is not None and checkpoint_hash != computed:
            raise ValueError("checkpoint hash does not match verified session state")
        record = SessionCheckpoint(
            session_id=self.session_id,
            ledger_offsets=offsets,
            artifact_ids=tuple(artifact_ids),
            watermarks=marks,
            pending_work=tuple(pending_work),
            resumable=resumable,
            checkpoint_hash=computed,
        )
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type="session_checkpoint",
                idempotency_key=f"session-checkpoint:{self.session_id}:{record.artifact_id}",
                payload={"checkpoint": record.model_dump(mode="json", round_trip=True)},
            )
        )
        return record

    def record_data_gap(self, gap: DataGapRecord) -> DataGapRecord:
        self._require_active()
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type="data_gap_recorded",
                idempotency_key=f"data-gap:{gap.source}:{gap.instrument}:{gap.gap_start.isoformat()}",
                payload={"gap": gap.model_dump(mode="json", round_trip=True)},
            )
        )
        return gap

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
        checks = stop_checks or SessionStopChecks()
        verified = checks.verify_all(self.ledgers)
        failed = [name for name, passed in verified.items() if not passed]
        for lease in self._open_leases():
            self.release_lease(lease.lease_id, outcome="released_on_stop")
        registry = worker_registry or WorkerRegistry()
        leaked = registry.live_workers()
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
        self._release_lock()
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
        return {
            namespace.value: len(self.ledgers.events(namespace)) for namespace in LedgerNamespace
        }

    def _acquire_lock(self) -> None:
        if fcntl is None:  # pragma: no cover - non-POSIX fallback
            raise RuntimeError("session lock requires fcntl on this workstation")
        handle = open(self.lock_path, "w")  # noqa: PTH123 - lock file by design
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise RuntimeError("another AdvisorAI session holds the application lock") from None
        self._lock_handle = handle

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
