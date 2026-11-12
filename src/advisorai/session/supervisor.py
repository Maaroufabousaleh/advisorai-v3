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

    def start(self, *, reason: str) -> tuple[SessionStartRecord, RecoveryRecord]:
        """Acquire the owner lock, classify the prior stop, record start+recovery."""
        if self.session_id is not None:
            raise RuntimeError("a session is already supervised by this object")
        self._acquire_lock()
        try:
            prior_end = self._last_end_record()
        except Exception as exc:
            self._release_lock()
            raise SessionZeroStateError(f"ledger state is unreadable: {exc}") from exc
        if prior_end is None:
            classification = "first_start"
            mode = RecoveryMode.NORMAL
            fault = "no prior session record"
        elif prior_end.outcome is SessionOutcome.CLEAN:
            classification = "clean_start"
            mode = RecoveryMode.NORMAL
            fault = "prior session stopped cleanly"
        else:
            classification = "crash_recovery"
            mode = RecoveryMode.RECOVERY
            fault = f"prior session ended {prior_end.outcome.value}"
        session_id = uuid4()
        start_record = SessionStartRecord(
            session_id=session_id,
            lock_identity=self.lock_identity,
            code_hash=self.code_hash,
            config_hash=self.config_hash,
            prior_end_record_id=prior_end.artifact_id if prior_end is not None else None,
            start_reason=reason.strip() or "owner_start",
            recovery_classification=classification,
        )
        recovery = RecoveryRecord(
            session_id=session_id,
            prior_session_id=prior_end.session_id if prior_end is not None else None,
            detected_fault=fault,
            rebuild_actions=(),
            reconciliation_actions=(),
            decisions=("reconcile_before_new_proposals",) if mode is RecoveryMode.RECOVERY else (),
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
        record = SessionCheckpoint(
            session_id=self.session_id,
            ledger_offsets=dict(ledger_offsets or {}),
            artifact_ids=tuple(artifact_ids),
            watermarks=dict(watermarks or {}),
            pending_work=tuple(pending_work),
            resumable=resumable,
            checkpoint_hash=checkpoint_hash or ("0" * 64),
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
        for existing in self._open_leases():
            if existing.worker_identity == worker_identity.strip():
                raise RuntimeError(f"lease already held for worker {worker_identity!r}")
        lease = RuntimeLeaseRecord(
            session_id=self.session_id,
            worker_identity=worker_identity.strip() or "unknown-worker",
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
        reconciliation_state: str = "reconciled",
        open_orders: tuple[UUID, ...] = (),
        unfinished_work: tuple[str, ...] = (),
    ) -> SessionEndRecord:
        self._require_active()
        assert self.session_id is not None
        for lease in self._open_leases():
            self.release_lease(lease.lease_id, outcome="released_on_stop")
        record = SessionEndRecord(
            session_id=self.session_id,
            outcome=outcome,
            end_offsets=self._ledger_offsets(),
            reconciliation_state=reconciliation_state,
            open_orders=tuple(open_orders),
            unfinished_work=tuple(unfinished_work),
            leases_released=True,
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

    def _last_end_record(self) -> SessionEndRecord | None:
        latest: SessionEndRecord | None = None
        for event in self.ledgers.events(LedgerNamespace.MISSION):
            if event.event_type != "session_ended":
                continue
            payload = event.payload.get("end")
            if isinstance(payload, dict):
                latest = SessionEndRecord.model_validate(payload)
        return latest

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


__all__ = ["SessionSupervisor", "SessionZeroStateError"]
