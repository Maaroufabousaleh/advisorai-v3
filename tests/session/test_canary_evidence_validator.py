"""Regression for the offline P02 canary-evidence validator.

Runs a real zero-submission bounded session offline (synthetic transport,
no network, no credentials), captures its stdout as the run log, then
validates the stopped state directory. A second case corrupts the outcome
to prove fail-closed behavior.
"""

from __future__ import annotations

import json
import sqlite3
import sys

sys.path.insert(0, "scripts")

from validate_testnet_canary_evidence import validate_canary

from advisorai.config import CredentialResolver
from advisorai.integrations import BINANCE_SPOT_TESTNET_BASE_URL
from advisorai.ledger import SqliteLedgers
from advisorai.session.application import OwnerApplicationConfig, PaperOwnerApplication
from advisorai.session.testnet_launch import (
    SandboxLaunchRequest,
    build_testnet_owner_application,
    read_testnet_baseline,
)
from tests.integrations.test_binance_spot import _account
from tests.session.test_testnet_baseline_hydration import _requester


def _resolver() -> CredentialResolver:
    return CredentialResolver.from_mapping(
        {
            "ADVISORAI_VENUE_NAME": "binance_spot_testnet",
            "ADVISORAI_VENUE_ENVIRONMENT": "paper_testnet",
            "ADVISORAI_VENUE_BASE_URL": BINANCE_SPOT_TESTNET_BASE_URL,
            "ADVISORAI_VENUE_API_KEY": "synthetic-testnet-key",
            "ADVISORAI_VENUE_API_SECRET": "synthetic-testnet-secret",
        }
    )


def _run_offline_canary(tmp_path, monkeypatch, capsys, *, label: str):
    import advisorai.integrations as integ

    monkeypatch.setattr(
        PaperOwnerApplication, "_apply_resource_envelope", staticmethod(lambda: None)
    )
    calls: list = []
    real_builder = integ.build_binance_spot_testnet_transport

    def seeded_builder(resolver, **kwargs):
        return real_builder(
            resolver, requester=_requester(_account(), calls), **kwargs
        )

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )
    state_dir = tmp_path / label
    ledgers = SqliteLedgers(state_dir / "ledgers.sqlite3")
    launch = SandboxLaunchRequest(
        provider="binance_spot_testnet",
        provider_instrument_id="BTCUSDT",
        quote_currency="USDT",
        max_submissions=0,
    )
    transport, baseline = read_testnet_baseline(resolver=_resolver(), launch=launch)
    app = build_testnet_owner_application(
        ledgers=ledgers,
        state_dir=state_dir,
        lock_identity=f"canary-validator-{label}",
        code_hash="c" * 64,
        config_hash="d" * 64,
        config=OwnerApplicationConfig(
            scenario="quiet",
            interval_seconds=0.5,
            max_runtime_seconds=2,
        ),
        resolver=_resolver(),
        launch=launch,
        transport=transport,
        baseline=baseline,
    )
    exit_code = app.run(reason="canary validator fixture")
    assert exit_code == 0
    run_log = tmp_path / f"{label}.log"
    run_log.write_text(capsys.readouterr().out, encoding="utf-8")
    return state_dir, run_log


def test_validator_certifies_clean_zero_submission_canary(
    tmp_path, monkeypatch, capsys
) -> None:
    state_dir, run_log = _run_offline_canary(
        tmp_path, monkeypatch, capsys, label="clean-canary"
    )
    report = validate_canary(state_dir=state_dir, run_log=run_log)
    assert report.submission_count == 0
    assert report.failed == []
    assert report.p02_certified is True


def test_validator_rejects_unclean_stop(tmp_path, monkeypatch, capsys) -> None:
    state_dir, run_log = _run_offline_canary(
        tmp_path, monkeypatch, capsys, label="dirty-canary"
    )
    connection = sqlite3.connect(state_dir / "ledgers.sqlite3")
    try:
        row = connection.execute(
            "SELECT payload_json FROM mission_events WHERE event_type='session_ended'"
        ).fetchone()
        payload = json.loads(row[0])
        payload["end"]["outcome"] = "unclean"
        payload["end"]["unfinished_work"] = ["paper_scheduler_did_not_stop"]
        connection.execute(
            "UPDATE mission_events SET payload_json=? WHERE event_type='session_ended'",
            (json.dumps(payload),),
        )
        connection.commit()
    finally:
        connection.close()
    report = validate_canary(state_dir=state_dir, run_log=run_log)
    assert report.p02_certified is False
    names = [check.name for check in report.failed]
    assert "outcome_clean" in names
    assert "no_unfinished_work" in names
