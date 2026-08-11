from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from advisorai.api.dashboard import AuthConfiguration, DashboardProjection, create_dashboard_app
from advisorai.collectors import (
    DisagreementAction,
    SourceCandidate,
    SourceDisagreementPolicy,
    SourceHealthLedger,
    SourceHealthObservation,
    SourceHealthState,
    SourceQuote,
    SourceSelectionState,
    compare_source_quotes,
    recover_binance_depth,
    replay_equivalent,
    select_source,
    transition_source_health,
)
from advisorai.collectors.public_market_data import reviewed_public_market_data_sources
from scripts.run_phase3_public_data_qualification import (
    BINANCE_DEPTH_SNAPSHOT_LIMIT,
    _AppendOnlyLog,
    _binance_depth_snapshot_url,
    _fault_drills,
)

NOW = datetime(2026, 8, 10, 22, 0, tzinfo=UTC)


def _observation(**updates: object) -> SourceHealthObservation:
    payload: dict[str, object] = {
        "observed_at": NOW,
        "source_id": "binance-public",
        "provider_identity": "binance-public",
        "endpoint": "wss://stream.binance.com:9443/ws",
        "symbol": "BTC",
        "connected": True,
        "valid_event_count": 10,
        "last_valid_event_at": NOW,
        "last_valid_event_age_seconds": 0.2,
        "sequence_state": "pass",
        "snapshot_state": "pass",
        "reconnect_state": "stable",
        "clock_confidence": "high",
        "malformed_event_rate": 0,
    }
    payload.update(updates)
    return SourceHealthObservation(**payload)


def test_source_health_is_deterministic_and_fails_closed_for_gaps_and_staleness():
    healthy = transition_source_health(None, _observation())
    assert healthy.state is SourceHealthState.HEALTHY
    assert healthy.fail_closed is False

    gap = transition_source_health(
        healthy.state,
        _observation(sequence_state="gap", snapshot_state="recovery_required"),
    )
    assert gap.state is SourceHealthState.RECOVERING
    assert gap.fail_closed is True
    assert "sequence_gap" in gap.reason_codes

    stale = transition_source_health(
        gap.state,
        _observation(last_valid_event_age_seconds=30),
    )
    assert stale.state is SourceHealthState.STALE
    assert stale.fail_closed is True

    quarantined = transition_source_health(
        stale.state,
        _observation(malformed_event_rate=0.2),
    )
    assert quarantined.state is SourceHealthState.QUARANTINED
    assert quarantined.fail_closed is True


def test_source_health_ledger_resume_verifies_hash_chain(tmp_path: Path):
    path = tmp_path / "health.jsonl"
    ledger = SourceHealthLedger(path)
    record = ledger.append(transition_source_health(None, _observation()))
    assert record.record_hash is not None
    resumed = SourceHealthLedger(path)
    assert resumed.read() == (record,)

    path.write_text(path.read_text().replace(record.record_hash or "", "0" * 64), encoding="utf-8")
    with pytest.raises(RuntimeError, match="hash"):
        SourceHealthLedger(path)


def test_disagreement_policy_abstains_without_averaging_sources():
    left = SourceQuote(
        source_id="binance",
        provider_identity="binance",
        symbol="BTC",
        bid=Decimal("100"),
        ask=Decimal("101"),
        received_at=NOW,
    )
    right = left.model_copy(
        update={
            "source_id": "coinbase",
            "provider_identity": "coinbase",
            "bid": Decimal("103"),
            "ask": Decimal("104"),
        }
    )
    result = compare_source_quotes(left, right, policy=SourceDisagreementPolicy())
    assert result.action is DisagreementAction.NO_TRADE_ABSTAIN
    assert result.fail_closed is True
    assert result.left_source == "binance"
    assert result.right_source == "coinbase"


def test_failover_records_identity_change_and_fails_closed_without_healthy_source():
    primary = SourceCandidate(
        source_id="primary",
        provider_identity="primary",
        endpoint="wss://primary",
        health_state=SourceHealthState.STALE,
        contract_valid=True,
        read_only=True,
        symbols=("BTC", "ETH"),
        priority=0,
    )
    secondary = primary.model_copy(
        update={
            "source_id": "secondary",
            "provider_identity": "secondary",
            "endpoint": "wss://secondary",
            "health_state": SourceHealthState.HEALTHY,
            "priority": 1,
        }
    )
    decision = select_source(
        (primary, secondary), required_symbols=("BTC", "ETH"), current_source_id="primary"
    )
    assert decision.state is SourceSelectionState.FAILOVER
    assert decision.continuity_reset is True
    assert decision.quality_recomputed is True
    assert decision.actual_source_identity == "secondary"

    closed = select_source((primary,), required_symbols=("BTC", "ETH"), current_source_id="primary")
    assert closed.state is SourceSelectionState.FAIL_CLOSED
    assert closed.fail_closed is True


def test_snapshot_gap_invalidates_local_book_and_recovery_reestablishes_equivalence():
    snapshot = {"lastUpdateId": 100, "bids": [["100", "1"]], "asks": [["101", "1"]]}
    first = {"e": "depthUpdate", "s": "BTCUSDT", "U": 101, "u": 101, "b": [["100", "2"]], "a": []}
    gap = {"e": "depthUpdate", "s": "BTCUSDT", "U": 103, "u": 103, "b": [], "a": [["101", "2"]]}
    failed, _ = recover_binance_depth(snapshot, (first, gap), symbol="BTCUSDT")
    assert failed.state == "failed_closed"
    assert failed.local_book_invalidated is True
    assert failed.sequence_gap_count == 1

    recovered_snapshot = {**snapshot, "lastUpdateId": 102}
    live, _ = recover_binance_depth(recovered_snapshot, (gap,), symbol="BTCUSDT")
    replay, _ = recover_binance_depth(recovered_snapshot, (gap,), symbol="BTCUSDT")
    assert live.state == "pass"
    assert replay_equivalent(live, replay)


def test_durable_public_recovery_uses_bounded_provider_snapshot():
    source = next(
        item
        for item in reviewed_public_market_data_sources()
        if item.source_id == "binance_spot_public_market_data"
    )

    url = _binance_depth_snapshot_url(source, "BTCUSDT")

    assert BINANCE_DEPTH_SNAPSHOT_LIMIT == 100
    assert parse_qs(urlsplit(url).query) == {"limit": ["100"], "symbol": ["BTCUSDT"]}


def test_fault_drills_are_injected_and_pass():
    drills = _fault_drills()
    assert all(item["evidence_type"] == "deterministic_injected" for item in drills.values())
    assert all(item["status"] == "pass" for item in drills.values())


def test_durable_append_only_log_rejects_mutation(tmp_path: Path):
    path = tmp_path / "samples.jsonl"
    log = _AppendOnlyLog(path)
    log.append({"schema": "test", "cycle": 1})
    resumed = _AppendOnlyLog(path)
    assert len(resumed.records) == 1
    payload = json.loads(path.read_text())
    payload["cycle"] = 2
    path.write_text(json.dumps(payload) + "\n")
    with pytest.raises(RuntimeError, match="hash chain"):
        _AppendOnlyLog(path)


def test_dashboard_source_health_is_read_only_and_sanitized(tmp_path: Path):
    snapshot = tmp_path / "latest-health.json"
    snapshot.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "binance-public",
                        "symbol": "BTC",
                        "state": "HEALTHY",
                        "last_event_age_seconds": 0.4,
                        "freshness": "fresh",
                        "reconnect_count": 2,
                        "sequence_gap_count": 0,
                        "disagreement_state": "normal",
                        "snapshot_recovery_state": "not_required",
                        "actual_provider_identity": "binance-public",
                        "fail_closed": False,
                        "secret_like_field": "must be ignored",
                    }
                ]
            }
        )
    )
    projection = DashboardProjection(source_health_path=snapshot)
    view = projection.source_health()
    assert view[0].actual_provider_identity == "binance-public"
    assert view[0].fail_closed is False
    assert projection.overview().source_health == view

    pytest.importorskip("fastapi")
    app = create_dashboard_app(
        projection=projection,
        config=AuthConfiguration(auth_required=False),
    )
    assert any(
        getattr(route, "path", "") == "/api/v1/dashboard/source-health" for route in app.routes
    )
