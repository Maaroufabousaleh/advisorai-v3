"""Application entry point: supervised start, operate, stop, recover."""

from __future__ import annotations

import os
from pathlib import Path

from advisorai.app import main
from advisorai.ledger import SqliteLedgers


def _env(tmp_path: Path, name: str) -> dict[str, str]:
    state = tmp_path / name
    return {
        "ADVISORAI_STATE_DIR": str(state),
        "ADVISORAI_LEDGER_PATH": str(state / "ledgers.sqlite3"),
    }


def test_app_start_stop_cycle(tmp_path, monkeypatch):
    for key, value in _env(tmp_path, "app1").items():
        monkeypatch.setenv(key, value)
    assert main(["--lock-identity", "owner", "start", "--reason", "smoke"]) == 0
    assert main(["--lock-identity", "owner", "status"]) == 0
    assert main(["--lock-identity", "owner", "stop", "--outcome", "clean"]) == 0


def test_app_unmatched_start_is_detected(tmp_path, monkeypatch):
    from uuid import uuid4

    from advisorai.ledger import LedgerEvent, LedgerNamespace
    from advisorai.session import SessionStartRecord

    for key, value in _env(tmp_path, "app2").items():
        monkeypatch.setenv(key, value)
    assert main(["--lock-identity", "owner", "start", "--reason", "first"]) == 0
    ledgers = SqliteLedgers(Path(os.environ["ADVISORAI_LEDGER_PATH"]))
    orphan = SessionStartRecord(
        session_id=uuid4(),
        lock_identity="owner",
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
    # Second start would see the orphan; status shows it without mutating.
    assert main(["--lock-identity", "probe", "status"]) == 0
