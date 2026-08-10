import json
import time
from datetime import UTC, datetime, timedelta

import pytest

from advisorai.collectors.sources import RawHttpSpool
from advisorai.integrations import SafeHttpClient
from advisorai.integrations.http import HttpClientConfig
from scripts.qualify_phase3_binance_spot_testnet_depth import (
    _BookState,
    _depth_event,
    _fault_drills,
    _fetch_server_time,
    _fetch_snapshot,
    _process_records,
    _snapshot_from_spool,
    _validated_stream_url,
    _write_latest_pointer,
)


def _raw(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


def _snapshot() -> dict[str, object]:
    return {"lastUpdateId": 100, "bids": [["100", "1"]], "asks": [["101", "1"]]}


def _event(first: int, last: int, *, event_time: int = 1_755_000_000_000) -> bytes:
    return _raw(
        {
            "e": "depthUpdate",
            "E": event_time,
            "s": "BTCUSDT",
            "U": first,
            "u": last,
            "b": [["100", "2"]],
            "a": [],
        }
    )


def test_binance_depth_state_applies_snapshot_and_updates():
    state = _BookState.from_snapshot(_snapshot())
    state.apply_event(json.loads(_event(101, 101)))
    assert state.last_update_id == 101
    assert state.bids[100] == 2


def test_binance_depth_parser_rejects_wrong_symbol_and_inverted_range():
    with pytest.raises(ValueError, match="unexpected symbol"):
        _depth_event(_raw({"e": "depthUpdate", "s": "ETHUSDT"}), symbol="BTCUSDT")
    with pytest.raises(ValueError, match="range is inverted"):
        _depth_event(_event(102, 101), symbol="BTCUSDT")


def test_binance_depth_replay_requires_contiguous_sequence_after_snapshot():
    received = datetime(2026, 8, 10, 18, 0, tzinfo=UTC)
    result = _process_records(
        _snapshot(),
        [(1, received, _event(101, 101)), (2, received, _event(103, 103))],
        symbol="BTCUSDT",
    )
    assert result["state"] == "failed_closed"
    assert "sequence gap" in result["validation_error"]


def test_binance_depth_replay_rejects_stale_event_and_requires_observations():
    received = datetime(2026, 8, 10, 18, 0, tzinfo=UTC)
    records = [
        (index, received, _event(101 + index - 1, 101 + index - 1, event_time=1))
        for index in range(1, 7)
    ]
    result = _process_records(_snapshot(), records, symbol="BTCUSDT")
    assert result["state"] == "failed_closed"
    assert result["event_age_seconds_max"] is not None


def test_binance_depth_records_raw_future_events_but_uses_measured_clock_offset():
    received = datetime(2026, 8, 10, 18, 0, tzinfo=UTC)
    provider_event_time = int((received + timedelta(seconds=0.5)).timestamp() * 1000)
    records = [
        (
            index,
            received,
            _event(
                100 + index,
                100 + index,
                event_time=provider_event_time,
            ),
        )
        for index in range(1, 7)
    ]
    result = _process_records(
        _snapshot(),
        records,
        symbol="BTCUSDT",
        clock_offset_seconds=1.0,
    )
    assert result["state"] == "pass"
    assert result["raw_future_event_count"] == 6
    assert result["future_event_count"] == 0
    assert result["clock_offset_seconds"] == 1.0


def test_binance_depth_rejects_unbounded_clock_offset():
    with pytest.raises(ValueError, match="clock offset"):
        _process_records(
            _snapshot(),
            (),
            symbol="BTCUSDT",
            clock_offset_seconds=6.0,
        )


def test_binance_server_time_is_spooled_and_offset_is_measured(tmp_path):
    server_time = int(time.time() * 1000) + 250

    def requester(*_args):
        return 200, json.dumps({"serverTime": server_time}).encode(), ()

    spool = RawHttpSpool(tmp_path / "raw-http.jsonl")
    client = SafeHttpClient(
        HttpClientConfig(allowed_hosts=("testnet.binance.vision",), requests_per_second=100),
        base_url="https://testnet.binance.vision",
        requester=requester,
    )
    sample = _fetch_server_time(client, spool)

    assert sample["clock_offset_seconds"] > 0
    assert sample["clock_offset_seconds"] < 1
    assert client.request_count == 1
    assert len(spool.read()) == 1
    assert spool.read()[0].url.endswith("/api/v3/time")


def test_binance_snapshot_selector_ignores_spooled_server_time(tmp_path):
    responses = [
        (200, json.dumps({"serverTime": int(time.time() * 1000)}).encode(), ()),
        (200, json.dumps(_snapshot(), separators=(",", ":")).encode(), ()),
    ]

    def requester(_method, url, _headers, _body, _timeout):
        response = responses.pop(0)
        if url.endswith("/api/v3/time"):
            return response
        return response[0], response[1], response[2]

    spool = RawHttpSpool(tmp_path / "raw-http.jsonl")
    client = SafeHttpClient(
        HttpClientConfig(allowed_hosts=("testnet.binance.vision",), requests_per_second=100),
        base_url="https://testnet.binance.vision",
        requester=requester,
        failed_response_sink=spool.append,
    )
    _fetch_server_time(client, spool)
    snapshot = _fetch_snapshot(client, spool, "BTCUSDT")

    assert snapshot["lastUpdateId"] == 100
    assert _snapshot_from_spool(spool, symbol="BTCUSDT")["lastUpdateId"] == 100
    assert len(spool.read()) == 2


def test_binance_depth_fault_drills_are_explicitly_injected():
    drills = _fault_drills()
    assert all(item["evidence_type"] == "deterministic_injected" for item in drills.values())
    assert all(item["status"] == "pass" for item in drills.values())


def test_binance_depth_url_guard_is_testnet_only():
    assert _validated_stream_url("BTCUSDT") == (
        "wss://stream.testnet.binance.vision/ws/btcusdt@depth@100ms"
    )
    with pytest.raises(ValueError, match="admitted BTC/ETH"):
        _validated_stream_url("DOGEUSDT")


def test_binance_depth_latest_pointer_is_atomic_and_mutable(tmp_path):
    pointer = tmp_path / "latest.json"
    _write_latest_pointer(pointer, {"run_id": "first", "status": "partial_failed_closed"})
    _write_latest_pointer(pointer, {"run_id": "second", "status": "partial_failed_closed"})
    assert '"run_id": "second"' in pointer.read_text()
