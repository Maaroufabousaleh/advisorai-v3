from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
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
from scripts import run_phase3_public_data_qualification as phase3_qualification
from scripts.run_phase3_public_data_qualification import (
    BINANCE_DEPTH_SNAPSHOT_LIMIT,
    _AppendOnlyLog,
    _binance_depth_snapshot_url,
    _build_disagreement,
    _connection_disconnected,
    _fault_drills,
    _source_symbol_result,
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


def test_source_health_ledger_rejects_provider_identity_change(tmp_path: Path):
    path = tmp_path / "health.jsonl"
    ledger = SourceHealthLedger(path)
    first = ledger.append(transition_source_health(None, _observation()))
    changed_provider = transition_source_health(
        first.state,
        _observation(
            provider_identity="coinbase-public",
            endpoint="wss://ws-feed.exchange.coinbase.com",
        ),
    )

    with pytest.raises(ValueError, match="silently change provider identity"):
        ledger.append(changed_provider)


def test_source_health_ledger_requires_declared_previous_state(tmp_path: Path):
    path = tmp_path / "health.jsonl"
    ledger = SourceHealthLedger(path)
    first = ledger.append(transition_source_health(None, _observation()))
    forged_predecessor = transition_source_health(
        None,
        _observation(last_valid_event_age_seconds=30),
    )

    assert first.state is SourceHealthState.HEALTHY
    with pytest.raises(ValueError, match="previous state"):
        ledger.append(forged_predecessor)


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


def test_disagreement_downgrades_when_provider_clock_probe_failed():
    market = {
        "order_book": {
            "top_bid": {"price": "100", "size": "1"},
            "top_ask": {"price": "101", "size": "1"},
        }
    }
    result = _build_disagreement(
        {
            "binance_spot_public_market_data": {
                "server_time": {"status": "failed"},
                "markets": {"BTCUSDT": market},
            },
            "coinbase_exchange_public_market_data": {
                "server_time": {"status": "pass", "clock_offset_seconds": 0},
                "markets": {"BTC-USD": market},
            },
        },
        NOW,
    )["BTC"]

    assert result.timestamp_confident is False
    assert result.state.value == "severe"
    assert result.action is DisagreementAction.NO_TRADE_ABSTAIN
    assert result.fail_closed is True


def test_disagreement_retains_normal_state_with_healthy_clock_probes():
    market = {
        "order_book": {
            "top_bid": {"price": "100", "size": "1"},
            "top_ask": {"price": "101", "size": "1"},
        }
    }
    result = _build_disagreement(
        {
            "binance_spot_public_market_data": {
                "server_time": {"status": "pass", "clock_offset_seconds": 0},
                "markets": {"BTCUSDT": market},
            },
            "coinbase_exchange_public_market_data": {
                "server_time": {"status": "pass", "clock_offset_seconds": 0},
                "markets": {"BTC-USD": market},
            },
        },
        NOW,
    )["BTC"]

    assert result.timestamp_confident is True
    assert result.state.value == "normal"
    assert result.action is DisagreementAction.ALLOW
    assert result.fail_closed is False


def test_disagreement_records_provider_event_freshness_difference():
    market = {
        "order_book": {
            "top_bid": {"price": "100", "size": "1"},
            "top_ask": {"price": "101", "size": "1"},
        }
    }
    rest = {
        "binance_spot_public_market_data": {
            "server_time": {"status": "pass", "clock_offset_seconds": 0},
            "markets": {"BTCUSDT": market},
        },
        "coinbase_exchange_public_market_data": {
            "server_time": {"status": "pass", "clock_offset_seconds": 0},
            "markets": {"BTC-USD": market},
        },
    }
    result = _build_disagreement(
        rest,
        NOW,
        provider_event_times={
            ("binance_spot_public_market_data", "BTC"): NOW - timedelta(seconds=1),
            ("coinbase_exchange_public_market_data", "BTC"): NOW - timedelta(seconds=4),
        },
        received_at_by_source={
            "binance_spot_public_market_data": NOW,
            "coinbase_exchange_public_market_data": NOW,
        },
    )["BTC"]

    assert result.freshness_difference_seconds == 3
    assert result.state.value == "degraded"
    assert result.action is DisagreementAction.TIGHTER_CONFIDENCE
    assert result.fail_closed is True


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


def test_binance_symbol_windows_are_collected_concurrently(tmp_path: Path, monkeypatch):
    source = next(
        item
        for item in reviewed_public_market_data_sources()
        if item.source_id == "binance_spot_public_market_data"
    )
    in_flight = 0
    maximum_in_flight = 0

    async def fake_collect(*_args, symbol: str, **_kwargs):
        nonlocal in_flight, maximum_in_flight
        in_flight += 1
        maximum_in_flight = max(maximum_in_flight, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return {
            "symbol": symbol,
            "status": "pass",
            "snapshot_payload": {"lastUpdateId": 1},
        }

    monkeypatch.setattr(
        phase3_qualification,
        "_collect_binance_public_connection",
        fake_collect,
    )

    result = phase3_qualification._run_binance_public_ws(source, tmp_path, 1)

    assert maximum_in_flight == len(source.symbols)
    assert result["state"] == "pass"
    assert [item["symbol"] for item in result["connections"]] == list(source.symbols)


def test_binance_public_window_records_one_bounded_reconnect_per_failed_symbol(
    tmp_path: Path, monkeypatch
):
    source = next(
        item
        for item in reviewed_public_market_data_sources()
        if item.source_id == "binance_spot_public_market_data"
    )
    attempts: dict[str, int] = {}

    async def fake_collect(*_args, symbol: str, connection_number: int, **_kwargs):
        attempts[symbol] = attempts.get(symbol, 0) + 1
        if connection_number == 1:
            return {
                "symbol": symbol,
                "connection_number": connection_number,
                "status": "failed",
                "snapshot_payload": None,
            }
        return {
            "symbol": symbol,
            "connection_number": connection_number,
            "status": "pass",
            "snapshot_payload": {"lastUpdateId": connection_number},
        }

    monkeypatch.setattr(
        phase3_qualification,
        "_collect_binance_public_connection",
        fake_collect,
    )

    result = phase3_qualification._run_binance_public_ws(source, tmp_path, 1)

    assert result["state"] == "pass"
    assert attempts == {"BTCUSDT": 2, "ETHUSDT": 2}
    assert len(result["connections"]) == 4
    assert all(item["attempt_count"] == 2 for item in result["reconnect"].values())
    assert all(item["reconnect_count"] == 1 for item in result["reconnect"].values())
    assert set(result["snapshots"]) == {"BTCUSDT", "ETHUSDT"}


def test_connection_metrics_distinguish_window_close_from_disconnect():
    assert _connection_disconnected({"status": "pass", "timed_window_completed": True}) is False
    assert (
        _connection_disconnected({"status": "failed", "collection_error_class": "TimeoutError"})
        is True
    )
    assert (
        _connection_disconnected(
            {"status": "pass", "transport_error_class": "WebSocketTransportError"}
        )
        is True
    )


def test_source_sample_preserves_only_sanitized_failure_labels(tmp_path: Path):
    source = next(
        item
        for item in reviewed_public_market_data_sources()
        if item.source_id == "coinbase_exchange_public_market_data"
    )
    result = _source_symbol_result(
        source,
        tmp_path,
        {
            "required_read_state": "failed",
            "error_class": "ProbeFailure",
            "server_time": {"status": "failed", "error_class": "TimeoutError"},
            "markets": {
                "BTC-USD": {
                    "order_book": {"state": "failed", "error_class": "HTTPStatusError"},
                    "public_trades": {"state": "failed"},
                }
            },
        },
        {
            "state": "failed",
            "error_class": "WebSocketTransportError",
            "connections": [
                {
                    "expected_symbols": ["BTC-USD"],
                    "status": "failed",
                    "failure_layer": "first_message_timeout",
                    "error_class": "TimeoutError",
                    "raw_spool": str(tmp_path / "missing.jsonl"),
                }
            ],
        },
        symbol="BTC-USD",
        asset="BTC",
        now=NOW,
        previous_connected=None,
    )

    assert result["failure_classes"] == [
        "HTTPStatusError",
        "ProbeFailure",
        "TimeoutError",
        "WebSocketTransportError",
    ]
    assert result["failure_layers"] == ["first_message_timeout"]
    assert all("secret" not in value.lower() for value in result["failure_classes"])


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
                        "failure_classes": ["TimeoutError"],
                        "failure_layers": ["first_message_timeout"],
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
    assert view[0].failure_classes == ("TimeoutError",)
    assert view[0].failure_layers == ("first_message_timeout",)
    assert projection.overview().source_health == view

    pytest.importorskip("fastapi")
    app = create_dashboard_app(
        projection=projection,
        config=AuthConfiguration(auth_required=False),
    )
    assert any(
        getattr(route, "path", "") == "/api/v1/dashboard/source-health" for route in app.routes
    )
