import json
from datetime import UTC, datetime

import pytest

from advisorai.integrations import RawMessageSpool
from scripts.qualify_phase3_coinbase_wss import (
    _freshness_summary,
    _message_metadata,
    _replay_ticker_events,
    _sequence_summary,
    _validated_ws_url,
)


def test_coinbase_wss_url_is_pinned_to_reviewed_sandbox_host():
    assert _validated_ws_url("wss://ws-feed-public.sandbox.exchange.coinbase.com/") == (
        "wss://ws-feed-public.sandbox.exchange.coinbase.com"
    )
    with pytest.raises(ValueError, match="production Coinbase"):
        _validated_ws_url("wss://ws-feed.exchange.coinbase.com")
    with pytest.raises(ValueError, match="reviewed sandbox"):
        _validated_ws_url("wss://another.example.test")


def test_coinbase_wss_sequence_summary_records_gaps_and_reordering():
    metadata = [
        {"product_id": "BTC-USD", "sequence": 10},
        {"product_id": "BTC-USD", "sequence": 12},
        {"product_id": "BTC-USD", "sequence": 11},
    ]
    summary = _sequence_summary(metadata)
    assert summary["state"] == "observed_gap_or_reordering"
    assert summary["products"]["BTC-USD"]["gap_total"] == 1
    assert summary["products"]["BTC-USD"]["out_of_order_count"] == 1


def test_coinbase_wss_replays_only_supported_ticker_events(tmp_path):
    spool = RawMessageSpool(tmp_path / "raw-ws.jsonl")
    received = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)
    subscription = (
        b'{"type":"subscriptions","channels":[{"name":"ticker","product_ids":["BTC-USD"]}]}'
    )
    ticker = json.dumps(
        {
            "type": "ticker",
            "product_id": "BTC-USD",
            "sequence": 10,
            "price": "100",
            "best_bid": "99",
            "best_ask": "101",
            "time": "2026-08-10T03:59:59Z",
        },
        separators=(",", ":"),
    ).encode()
    heartbeat = (
        b'{"type":"heartbeat","product_id":"BTC-USD","sequence":11,"time":"2026-08-10T03:59:59Z"}'
    )
    spool.append(subscription, received_at=received, sequence=1)
    spool.append(ticker, received_at=received, sequence=2)
    spool.append(heartbeat, received_at=received, sequence=3)

    assert _message_metadata(ticker, product_id="BTC-USD")["type"] == "ticker"
    assert len(_replay_ticker_events(spool, product_id="BTC-USD")) == 1


def test_coinbase_wss_freshness_summary_measures_event_age_and_heartbeats(tmp_path):
    spool = RawMessageSpool(tmp_path / "fresh-ws.jsonl")
    received = datetime(2026, 8, 10, 4, 0, 2, tzinfo=UTC)
    for sequence, payload in enumerate(
        (
            {
                "type": "heartbeat",
                "product_id": "BTC-USD",
                "sequence": 1,
                "time": "2026-08-10T04:00:00Z",
            },
            {
                "type": "heartbeat",
                "product_id": "BTC-USD",
                "sequence": 2,
                "time": "2026-08-10T04:00:01Z",
            },
            {
                "type": "ticker",
                "product_id": "BTC-USD",
                "sequence": 3,
                "price": "100",
                "time": "2026-08-10T04:00:01Z",
            },
        ),
        start=1,
    ):
        spool.append(
            json.dumps(payload, separators=(",", ":")).encode(),
            received_at=received,
            sequence=sequence,
        )

    summary = _freshness_summary(spool, product_id="BTC-USD")
    assert summary["state"] == "pass"
    assert summary["event_time_present_count"] == 3
    assert summary["heartbeat_count"] == 2
    assert summary["event_age_seconds_max"] == 2.0


def test_coinbase_wss_freshness_summary_rejects_future_timestamp(tmp_path):
    spool = RawMessageSpool(tmp_path / "future-ws.jsonl")
    received = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)
    payload = {
        "type": "heartbeat",
        "product_id": "BTC-USD",
        "sequence": 1,
        "time": "2026-08-10T04:00:01Z",
    }
    spool.append(
        json.dumps(payload, separators=(",", ":")).encode(), received_at=received, sequence=1
    )
    spool.append(json.dumps({**payload, "sequence": 2}).encode(), received_at=received, sequence=2)

    assert _freshness_summary(spool, product_id="BTC-USD")["state"] == "stale_or_malformed"


def test_coinbase_wss_metadata_does_not_copy_provider_payload():
    raw = b'{"type":"error","message":"secret-looking provider text"}'
    metadata = _message_metadata(raw, product_id="BTC-USD")
    assert metadata["type"] == "error"
    assert "secret-looking provider text" not in json.dumps(metadata)


def test_coinbase_wss_real_runner_requires_explicit_network_opt_in(tmp_path, monkeypatch):
    del monkeypatch
    from scripts import qualify_phase3_coinbase_wss as runner

    with pytest.raises(ValueError, match="production Coinbase"):
        runner.run_evidence(tmp_path, ws_url="wss://ws-feed.exchange.coinbase.com")


@pytest.mark.parametrize("duration", [0, 121])
def test_coinbase_wss_runner_bounds_connection_duration(tmp_path, duration):
    from scripts import qualify_phase3_coinbase_wss as runner

    with pytest.raises(ValueError, match="bounded qualification limit"):
        runner.run_evidence(tmp_path, duration_seconds=duration)
