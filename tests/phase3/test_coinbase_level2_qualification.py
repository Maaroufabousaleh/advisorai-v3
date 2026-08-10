import json
from datetime import UTC, datetime

import pytest

from advisorai.integrations import RawMessageSpool
from scripts.qualify_phase3_coinbase_level2 import (
    _apply_message,
    _BookState,
    _freshness_summary,
    _message_metadata,
    _replay,
    _validated_ws_url,
    run_evidence,
)


def _raw(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


def test_level2_reducer_validates_snapshot_update_and_replay(tmp_path):
    snapshot = _raw(
        {
            "type": "snapshot",
            "product_id": "BTC-USD",
            "bids": [["100", "1.5"]],
            "asks": [["101", "2"]],
        }
    )
    update = _raw(
        {
            "type": "l2update",
            "product_id": "BTC-USD",
            "time": "2026-08-10T04:00:01Z",
            "changes": [["buy", "100", "2"], ["sell", "101", "0"]],
        }
    )
    state = _BookState()
    assert _apply_message(state, snapshot, product_id="BTC-USD") == "snapshot"
    assert _apply_message(state, update, product_id="BTC-USD") == "l2update"
    assert state.summary()["best_bid"] == "100"
    assert state.summary()["best_ask"] is None

    spool = RawMessageSpool(tmp_path / "raw.jsonl")
    received = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)
    spool.append(snapshot, received_at=received, sequence=1)
    spool.append(update, received_at=received, sequence=2)
    replay_state, _counts = _replay(spool, product_id="BTC-USD")
    assert replay_state.digest() == state.digest()


def test_level2_reducer_rejects_invalid_order_book_updates():
    state = _BookState()
    with pytest.raises(ValueError, match="before snapshot"):
        _apply_message(
            state,
            _raw(
                {
                    "type": "l2update",
                    "product_id": "BTC-USD",
                    "changes": [["buy", "100", "1"]],
                }
            ),
            product_id="BTC-USD",
        )
    with pytest.raises(ValueError, match="crossed"):
        _apply_message(
            state,
            _raw(
                {
                    "type": "snapshot",
                    "product_id": "BTC-USD",
                    "bids": [["101", "1"]],
                    "asks": [["100", "1"]],
                }
            ),
            product_id="BTC-USD",
        )


def test_level2_replay_and_freshness_are_deterministic(tmp_path):
    spool = RawMessageSpool(tmp_path / "raw.jsonl")
    received = datetime(2026, 8, 10, 4, 0, 2, tzinfo=UTC)
    messages = (
        _raw({"type": "subscriptions", "channels": [{"name": "level2"}]}),
        _raw(
            {
                "type": "snapshot",
                "product_id": "BTC-USD",
                "bids": [["100", "1"]],
                "asks": [["101", "1"]],
            }
        ),
        _raw(
            {
                "type": "heartbeat",
                "product_id": "BTC-USD",
                "sequence": 1,
                "time": "2026-08-10T04:00:00Z",
            }
        ),
        _raw(
            {
                "type": "l2update",
                "product_id": "BTC-USD",
                "time": "2026-08-10T04:00:01Z",
                "changes": [["buy", "100", "2"]],
            }
        ),
        _raw(
            {
                "type": "heartbeat",
                "product_id": "BTC-USD",
                "sequence": 2,
                "time": "2026-08-10T04:00:01Z",
            }
        ),
    )
    for sequence, message in enumerate(messages, start=1):
        spool.append(message, received_at=received, sequence=sequence)
    replay_state, counts = _replay(spool, product_id="BTC-USD")
    assert counts["snapshot"] == 1
    assert counts["l2update"] == 1
    assert replay_state.summary()["best_bid"] == "100"
    assert _freshness_summary(spool, product_id="BTC-USD")["state"] == "pass"


def test_level2_metadata_does_not_copy_provider_payload():
    raw = _raw({"type": "error", "message": "secret-looking provider text"})
    metadata = _message_metadata(raw, product_id="BTC-USD")
    assert metadata["type"] == "error"
    assert "secret-looking provider text" not in json.dumps(metadata)


def test_level2_url_and_product_guards_are_fail_closed():
    assert _validated_ws_url("wss://ws-feed-public.sandbox.exchange.coinbase.com/") == (
        "wss://ws-feed-public.sandbox.exchange.coinbase.com"
    )
    with pytest.raises(ValueError, match="production Coinbase"):
        _validated_ws_url("wss://ws-feed.exchange.coinbase.com")
    with pytest.raises(ValueError, match="reviewed sandbox"):
        _validated_ws_url("wss://example.test")


def test_level2_runner_rejects_unreviewed_channel_before_network(tmp_path):
    with pytest.raises(ValueError, match="unsupported channel"):
        run_evidence(tmp_path, channel="ticker")
