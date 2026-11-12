"""Session lifecycle: lock, start classification, leases, clean stop/restart."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers
from advisorai.session import (
    DataGapRecord,
    SessionOutcome,
    SessionSupervisor,
    WorkerRegistry,
)


def _supervisor(tmp_path: Path, name: str = "ledgers.sqlite3") -> SessionSupervisor:
    return SessionSupervisor(
        SqliteLedgers(tmp_path / name),
        state_dir=tmp_path / "state",
        lock_identity="owner-workstation",
        code_hash="c" * 64,
        config_hash="d" * 64,
    )


def test_first_start_records_normal_recovery(tmp_path):
    supervisor = _supervisor(tmp_path)
    start, recovery = supervisor.start(reason="owner start")
    assert start.recovery_classification == "first_start"
    assert recovery.recovery_mode.value == "normal"
    end = supervisor.stop()
    assert end.outcome is SessionOutcome.CLEAN
    assert end.leases_released is True


def test_clean_stop_then_restart_is_clean(tmp_path):
    first = _supervisor(tmp_path)
    first.start(reason="first")
    first.checkpoint()
    first.stop()
    second = SessionSupervisor(
        SqliteLedgers(tmp_path / "ledgers.sqlite3"),
        state_dir=tmp_path / "state",
        lock_identity="owner-workstation",
        code_hash="c" * 64,
        config_hash="d" * 64,
    )
    start, recovery = second.start(reason="restart")
    assert start.recovery_classification == "clean_start"
    # A restart without fresh verification artifacts enters recovery.
    assert recovery.recovery_mode.value in {"normal", "recovery"}
    second.checkpoint()
    second.stop()


def test_unclean_stop_triggers_recovery_mode(tmp_path):
    first = _supervisor(tmp_path)
    first.start(reason="first")
    first.stop(outcome=SessionOutcome.UNCLEAN, unfinished_work=("cycle-1",))
    second = SessionSupervisor(
        SqliteLedgers(tmp_path / "ledgers.sqlite3"),
        state_dir=tmp_path / "state",
        lock_identity="owner-workstation",
        code_hash="c" * 64,
        config_hash="d" * 64,
    )
    _, recovery = second.start(reason="restart after crash")
    assert recovery.recovery_mode.value == "recovery"
    second.checkpoint()
    second.stop()


def test_unmatched_start_is_crash_despite_older_clean_end(tmp_path):
    """An unmatched session_started after a clean end is a crash, not clean."""
    ledgers = SqliteLedgers(tmp_path / "ledgers.sqlite3")
    first = SessionSupervisor(
        ledgers,
        state_dir=tmp_path / "state",
        lock_identity="owner-workstation",
        code_hash="c" * 64,
        config_hash="d" * 64,
    )
    first.start(reason="first")
    first.checkpoint()
    first.stop()
    # Simulate a crashed session: a start event with no matching end.
    from uuid import uuid4

    from advisorai.session import SessionStartRecord

    orphan = SessionStartRecord(
        session_id=uuid4(),
        lock_identity="owner-workstation",
        code_hash="c" * 64,
        config_hash="d" * 64,
        start_reason="crashed session",
        recovery_classification="clean_start",
    )
    ledgers.append(
        LedgerEvent(
            namespace=LedgerNamespace.MISSION,
            event_type="session_started",
            idempotency_key=f"session-started:{orphan.session_id}",
            payload={"start": orphan.model_dump(mode="json", round_trip=True)},
        )
    )
    second = SessionSupervisor(
        SqliteLedgers(tmp_path / "ledgers.sqlite3"),
        state_dir=tmp_path / "state",
        lock_identity="owner-workstation",
        code_hash="c" * 64,
        config_hash="d" * 64,
    )
    start, recovery = second.start(reason="restart after orphan")
    assert start.recovery_classification == "crash_recovery"
    assert recovery.recovery_mode.value == "recovery"
    second.checkpoint()
    second.stop()


def test_second_live_supervisor_is_rejected(tmp_path):
    first = _supervisor(tmp_path)
    first.start(reason="first")
    second = _supervisor(tmp_path)
    with pytest.raises(RuntimeError, match="another AdvisorAI session"):
        second.start(reason="second")
    first.stop()


def test_leases_are_released_on_stop(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.start(reason="leases")
    lease = supervisor.acquire_lease(worker_identity="chronos", resource_envelope="gpu")
    released = supervisor.release_lease(lease.lease_id)
    assert released.cleanup_outcome == "released"
    supervisor.acquire_lease(worker_identity="ttm", resource_envelope="gpu")
    end = supervisor.stop()
    assert end.leases_released is True


def test_live_worker_blocks_clean_stop(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.start(reason="leak check")
    registry = WorkerRegistry()
    registry.register("chronos-worker")
    end = supervisor.stop(worker_registry=registry)
    assert end.outcome is SessionOutcome.UNCLEAN
    # A still-live worker is a leak: leases_released must stay False, honestly.
    assert end.leases_released is False
    assert any("worker_still_live" in item for item in end.unfinished_work)


def test_checkpoint_hash_is_verified_not_placeholder(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.start(reason="work")
    checkpoint = supervisor.checkpoint(pending_work=("cycle-2",))
    assert checkpoint.checkpoint_hash != "0" * 64
    assert len(checkpoint.checkpoint_hash) == 64
    with pytest.raises(ValueError, match="checkpoint hash does not match"):
        supervisor.checkpoint(checkpoint_hash="f" * 64)
    supervisor.stop()


def test_unbackfilled_gap_blocks_normal_recovery(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.start(reason="work")
    supervisor.record_data_gap(
        DataGapRecord(
            source="binance",
            instrument="BTCUSDT",
            gap_start=datetime(2026, 8, 1, tzinfo=UTC),
            gap_end=datetime(2026, 8, 1, 1, tzinfo=UTC),
            last_valid_watermark="wm-1",
        )
    )
    supervisor.checkpoint()
    supervisor.stop()
    second = _supervisor(tmp_path)
    _, recovery = second.start(reason="restart with gap")
    # Failed mandatory checks on a clean history fail closed to ABSTAIN.
    assert recovery.recovery_mode.value == "abstain"
    assert any("source_freshness" in item for item in recovery.decisions)
    second.checkpoint()
    second.stop()


def test_checkpoint_and_data_gap_are_recorded(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.start(reason="work")
    checkpoint = supervisor.checkpoint(pending_work=("cycle-2",))
    assert checkpoint.pending_work == ("cycle-2",)
    gap = supervisor.record_data_gap(
        DataGapRecord(
            source="binance",
            instrument="BTCUSDT",
            gap_start=datetime(2026, 8, 1, tzinfo=UTC),
            gap_end=datetime(2026, 8, 1, 1, tzinfo=UTC),
            last_valid_watermark="wm-1",
        )
    )
    assert gap.backfilled is False
    supervisor.stop()


def test_dead_pid_lease_is_refused(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.start(reason="phantom lease check")
    with pytest.raises(RuntimeError, match="not alive"):
        supervisor.acquire_lease(worker_identity="pid:99999999", resource_envelope="gpu")
    import os

    live = supervisor.acquire_lease(worker_identity=f"pid:{os.getpid()}", resource_envelope="test")
    assert live.worker_identity == f"pid:{os.getpid()}"
    supervisor.stop()
