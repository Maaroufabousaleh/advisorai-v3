"""Emergency-stop regression: P09 prep for the supervised stop path.

An emergency request records the explicit `emergency` outcome (not a
silent clean stop) with exit code 2.

Runs a real local owner session offline, issues an emergency stop request
mid-run through the same file contract as `advisorai.app stop --outcome
emergency`, and proves the session ends UNCLEAN with explicit state, stops
scheduling new work, and preserves durable state for a later restart.
No network, no credentials, no venue writes.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.session.application import OwnerApplicationConfig, PaperOwnerApplication


def _write_emergency_request(state_dir: Path, session_id: UUID) -> None:
    request_path = state_dir / f"stop-request-{session_id}.json"
    request_path.write_text(
        json.dumps(
            {
                "session_id": str(session_id),
                "outcome": "emergency",
                "requested_at": datetime.now(UTC).isoformat(),
                "requester_pid": 0,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_emergency_stop_ends_unclean_without_new_orders(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        PaperOwnerApplication, "_apply_resource_envelope", staticmethod(lambda: None)
    )
    state_dir = tmp_path / "emergency-state"
    ledgers = SqliteLedgers(state_dir / "ledgers.sqlite3")
    app = PaperOwnerApplication(
        ledgers=ledgers,
        state_dir=state_dir,
        lock_identity="emergency-test",
        code_hash="a" * 64,
        config_hash="b" * 64,
        config=OwnerApplicationConfig(
            scenario="quiet",
            interval_seconds=0.2,
            accelerated_clock=True,
        ),
    )
    outcome: dict[str, int] = {}

    def runner() -> None:
        outcome["exit_code"] = app.run(reason="emergency stop test")

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        session_id: UUID | None = None
        while time.monotonic() < deadline:
            session_id = app._owner_session_id
            if session_id is not None:
                break
            time.sleep(0.05)
        assert session_id is not None, "session never started"
        orders_before = len(ledgers.events(LedgerNamespace.ORDER))
        _write_emergency_request(state_dir, session_id)
        thread.join(timeout=60)
        assert not thread.is_alive(), "session did not stop after emergency request"
        assert outcome["exit_code"] == 2
        output = capsys.readouterr().out
        assert "outcome=emergency" in output
        assert "SESSION READY" in output
        orders_after = len(ledgers.events(LedgerNamespace.ORDER))
        session_ended = [
            event
            for event in ledgers.events(LedgerNamespace.MISSION)
            if event.event_type == "session_ended"
        ]
        assert len(session_ended) == 1
        end = session_ended[0].payload["end"]
        assert end["outcome"] == "emergency"
        assert end["session_id"] == str(session_id)
        # Emergency stop halts the scheduler; it must not create new orders
        # while shutting down.
        assert orders_after == orders_before
        assert app.supervisor.active_session_id() is None
    finally:
        app.stop_requested.set()
        thread.join(timeout=30)
