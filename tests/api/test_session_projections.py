"""Dashboard projections are scoped per session, never global totals."""

from __future__ import annotations

from datetime import UTC, datetime

from advisorai.api.dashboard import build_ledger_overview
from advisorai.ledger import SqliteLedgers
from advisorai.session import DataGapRecord, SessionSupervisor


def test_session_projections_are_per_session(tmp_path):
    def supervisor():
        return SessionSupervisor(
            SqliteLedgers(tmp_path / "proj.sqlite3"),
            state_dir=tmp_path / "state",
            lock_identity="owner-workstation",
            code_hash="c" * 64,
            config_hash="d" * 64,
        )

    first = supervisor()
    first.start(reason="first")
    first.checkpoint()
    first.checkpoint()
    first.record_data_gap(
        DataGapRecord(
            source="binance",
            instrument="BTCUSDT",
            gap_start=datetime(2026, 8, 1, tzinfo=UTC),
            gap_end=datetime(2026, 8, 1, 1, tzinfo=UTC),
            last_valid_watermark="wm-1",
        )
    )
    first.stop()

    second = supervisor()
    second.start(reason="second")
    lease = second.acquire_lease(worker_identity="chronos", resource_envelope="gpu")
    second.checkpoint()
    second.release_lease(lease.lease_id)
    second.stop()

    overview = build_ledger_overview(SqliteLedgers(tmp_path / "proj.sqlite3"))
    assert len(overview.sessions) == 2
    by_class = {session.recovery_classification: session for session in overview.sessions}
    first_view = by_class["first_start"]
    second_view = by_class["clean_start"]
    # First session: 2 checkpoints, 1 gap, 0 leases.
    assert first_view.checkpoints == 2
    assert first_view.data_gaps == 1
    assert first_view.backfilled_gaps == 0
    assert first_view.open_leases == 0
    # Second session: 1 checkpoint, no gaps, lease opened and released.
    assert second_view.checkpoints == 1
    assert second_view.data_gaps == 0
    assert second_view.open_leases == 0
