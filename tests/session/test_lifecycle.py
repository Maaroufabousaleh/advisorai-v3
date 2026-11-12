"""Session lifecycle: lock, start classification, leases, clean stop/restart."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from advisorai.ledger import SqliteLedgers
from advisorai.session import (
    DataGapRecord,
    SessionOutcome,
    SessionSupervisor,
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
    first.stop()
    second = SessionSupervisor(
        SqliteLedgers(tmp_path / "ledgers.sqlite3"),
        state_dir=tmp_path / "state",
        lock_identity="owner-workstation",
        code_hash="c" * 64,
        config_hash="d" * 64,
    )
    start, _ = second.start(reason="restart")
    assert start.recovery_classification == "clean_start"
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
