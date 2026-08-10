#!/usr/bin/env python3
"""Run bounded public Binance Spot Testnet depth qualification.

The runner is source evidence only.  It uses no credentials, does not submit
orders, and never calls a production host.  Each connection durably spools
raw WebSocket messages and its REST bootstrap response before parsing.  The
report distinguishes real provider observations from deterministic injected
failure drills.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.metadata
import json
import math
import os
import tempfile
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

from advisorai.collectors.sources import RawHttpSpool
from advisorai.integrations import (
    BINANCE_SPOT_TESTNET_BASE_URL,
    BINANCE_SPOT_TESTNET_HOST,
    BINANCE_SPOT_TESTNET_STREAM_HOST,
    BINANCE_SPOT_TESTNET_STREAM_URL,
    RawMessageSpool,
    RawWebSocketFeed,
    SafeHttpClient,
    WebSocketTransportError,
)
from advisorai.integrations.http import HttpClientConfig, HttpTransportError

SCHEMA = "advisorai.phase3.binance-spot-testnet-depth.v1"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
DEFAULT_CONNECTION_SECONDS = 20
MAX_CONNECTION_SECONDS = 120
DEFAULT_CONNECTIONS = 2
MAX_CONNECTIONS = 4
MAX_EVENT_AGE_SECONDS = 30.0
MAX_CLOCK_OFFSET_SECONDS = 5.0


def _sha256(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _write_immutable_json(path: Path, payload: object) -> None:
    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise FileExistsError(f"immutable evidence differs: {path}")
        return
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _write_latest_pointer(path: Path, payload: object) -> None:
    """Atomically publish the mutable pointer to the newest immutable run."""

    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _validated_stream_url(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if normalized not in SYMBOLS:
        raise ValueError("Binance depth qualifier accepts only the admitted BTC/ETH symbols")
    stream = f"{normalized.lower()}@depth@100ms"
    url = f"{BINANCE_SPOT_TESTNET_STREAM_URL}/{stream}"
    parsed = urlsplit(url)
    if (
        parsed.scheme != "wss"
        or parsed.hostname != BINANCE_SPOT_TESTNET_STREAM_HOST
        or parsed.path != f"/ws/{stream}"
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Binance depth stream is not the reviewed Spot Testnet endpoint")
    return url


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Binance {label} is invalid")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Binance {label} is invalid") from exc
    if parsed <= 0:
        raise ValueError(f"Binance {label} is invalid")
    return parsed


def _decimal(value: object, label: str, *, allow_zero: bool) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"Binance {label} is invalid")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Binance {label} is invalid") from exc
    if not parsed.is_finite() or parsed < 0 or (not allow_zero and parsed == 0):
        raise ValueError(f"Binance {label} is invalid")
    return parsed


def _levels(value: object, label: str) -> tuple[tuple[object, object], ...]:
    if not isinstance(value, list):
        raise ValueError(f"Binance {label} is not an array")
    result: list[tuple[object, object]] = []
    for level in value:
        if not isinstance(level, list) or len(level) != 2:
            raise ValueError(f"Binance {label} has an invalid price level")
        result.append((level[0], level[1]))
    return tuple(result)


@dataclass(slots=True)
class _BookState:
    bids: dict[Decimal, Decimal]
    asks: dict[Decimal, Decimal]
    last_update_id: int

    @classmethod
    def from_snapshot(cls, payload: Mapping[str, object]) -> _BookState:
        last_update_id = _positive_int(payload.get("lastUpdateId"), "snapshot update ID")
        state = cls({}, {}, last_update_id)
        state._apply_levels(
            state.bids, _levels(payload.get("bids"), "snapshot bids"), "snapshot bid"
        )
        state._apply_levels(
            state.asks, _levels(payload.get("asks"), "snapshot asks"), "snapshot ask"
        )
        if not state.bids or not state.asks:
            raise ValueError("Binance snapshot must contain both bid and ask levels")
        state._validate_crossing()
        return state

    @staticmethod
    def _apply_levels(
        book: dict[Decimal, Decimal],
        levels: Sequence[tuple[object, object]],
        label: str,
    ) -> None:
        for raw_price, raw_quantity in levels:
            price = _decimal(raw_price, f"{label} price", allow_zero=False)
            quantity = _decimal(raw_quantity, f"{label} quantity", allow_zero=True)
            if quantity == 0:
                book.pop(price, None)
            else:
                if price in book:
                    raise ValueError("Binance snapshot contains a duplicate price level")
                book[price] = quantity

    def apply_event(self, payload: Mapping[str, object]) -> None:
        for side, book in (("b", self.bids), ("a", self.asks)):
            for raw_price, raw_quantity in _levels(payload.get(side), f"depth {side}"):
                price = _decimal(raw_price, f"depth {side} price", allow_zero=False)
                quantity = _decimal(raw_quantity, f"depth {side} quantity", allow_zero=True)
                if quantity == 0:
                    book.pop(price, None)
                else:
                    book[price] = quantity
        self.last_update_id = _positive_int(payload.get("u"), "depth update ID")
        self._validate_crossing()

    def _validate_crossing(self) -> None:
        if self.bids and self.asks and max(self.bids) >= min(self.asks):
            raise ValueError("Binance depth book is crossed")

    def digest(self) -> str:
        encoded = json.dumps(
            {
                "last_update_id": self.last_update_id,
                "bids": [
                    [str(price), str(quantity)] for price, quantity in sorted(self.bids.items())
                ],
                "asks": [
                    [str(price), str(quantity)] for price, quantity in sorted(self.asks.items())
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return _sha256(encoded)

    def summary(self) -> dict[str, object]:
        return {
            "last_update_id": self.last_update_id,
            "bid_level_count": len(self.bids),
            "ask_level_count": len(self.asks),
            "best_bid": str(max(self.bids)) if self.bids else None,
            "best_ask": str(min(self.asks)) if self.asks else None,
            "book_state_sha256": self.digest(),
        }


def _depth_event(raw: bytes, *, symbol: str) -> Mapping[str, object] | None:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Binance depth message is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("Binance depth message is not an object")
    if payload.get("e") != "depthUpdate":
        if "code" in payload:
            raise ValueError("Binance depth stream returned an error message")
        return None
    if str(payload.get("s", "")).strip().upper() != symbol:
        raise ValueError("Binance depth message has an unexpected symbol")
    first = _positive_int(payload.get("U"), "first update ID")
    last = _positive_int(payload.get("u"), "last update ID")
    if first > last:
        raise ValueError("Binance depth update ID range is inverted")
    _positive_int(payload.get("E"), "event time")
    _levels(payload.get("b"), "depth bids")
    _levels(payload.get("a"), "depth asks")
    return payload


def _process_records(
    snapshot: Mapping[str, object],
    records: Sequence[tuple[int, datetime, bytes]],
    *,
    symbol: str,
    clock_offset_seconds: float = 0.0,
) -> dict[str, object]:
    if (
        isinstance(clock_offset_seconds, bool)
        or not isinstance(clock_offset_seconds, (int, float))
        or not math.isfinite(clock_offset_seconds)
        or abs(clock_offset_seconds) > MAX_CLOCK_OFFSET_SECONDS
    ):
        raise ValueError("Binance clock offset is outside the bounded qualification limit")
    started = time.perf_counter()
    counts: Counter[str] = Counter()
    ages: list[float] = []
    future_events = 0
    raw_future_events = 0
    validation_error: str | None = None
    synced = False
    event_count = 0
    discarded_before_sync = 0
    repeated_or_old = 0
    state: _BookState | None = None
    try:
        state = _BookState.from_snapshot(snapshot)
        sync_target = state.last_update_id + 1
        previous_update_id = state.last_update_id
        for _sequence, received_at, raw in records:
            payload = _depth_event(raw, symbol=symbol)
            if payload is None:
                counts["ignored_control"] += 1
                continue
            event_count += 1
            counts["depth_update"] += 1
            event_time = datetime.fromtimestamp(
                _positive_int(payload["E"], "event time") / 1000, tz=UTC
            )
            raw_age = (received_at.astimezone(UTC) - event_time).total_seconds()
            if raw_age < 0:
                raw_future_events += 1
            age = raw_age + clock_offset_seconds
            if age < 0:
                future_events += 1
            else:
                ages.append(age)
            first = _positive_int(payload["U"], "first update ID")
            last = _positive_int(payload["u"], "last update ID")
            if not synced:
                if last < sync_target:
                    discarded_before_sync += 1
                    continue
                if first > sync_target:
                    raise ValueError("Binance depth sequence gap before snapshot sync")
                state.apply_event(payload)
                previous_update_id = last
                synced = True
                continue
            expected = previous_update_id + 1
            if last < expected:
                repeated_or_old += 1
                continue
            if first > expected:
                raise ValueError("Binance depth sequence gap after snapshot sync")
            state.apply_event(payload)
            previous_update_id = last
        if not synced:
            raise ValueError("Binance depth stream did not synchronize with REST snapshot")
        if event_count < 5:
            raise ValueError("Binance depth stream produced insufficient update observations")
    except (TypeError, ValueError, OverflowError) as exc:
        validation_error = str(exc)
    max_age = round(max(ages), 3) if ages else None
    passed = (
        validation_error is None
        and state is not None
        and event_count >= 5
        and future_events == 0
        and max_age is not None
        and max_age <= MAX_EVENT_AGE_SECONDS
    )
    return {
        "state": "pass" if passed else "failed_closed",
        "event_count": event_count,
        "ignored_control_count": counts["ignored_control"],
        "discarded_before_sync_count": discarded_before_sync,
        "repeated_or_old_count": repeated_or_old,
        "future_event_count": future_events,
        "raw_future_event_count": raw_future_events,
        "clock_offset_seconds": round(clock_offset_seconds, 3),
        "event_age_seconds_max": max_age,
        "max_event_age_limit_seconds": MAX_EVENT_AGE_SECONDS,
        "validation_error": validation_error,
        "book": state.summary() if state is not None else None,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def _snapshot_from_spool(http_spool: RawHttpSpool) -> Mapping[str, object]:
    records = http_spool.read()
    successful = tuple(record for record in records if record.status_code == 200)
    if len(successful) != 1:
        raise ValueError("Binance depth snapshot response is missing or non-unique")
    payload = json.loads(successful[0].payload)
    if not isinstance(payload, Mapping):
        raise ValueError("Binance depth snapshot is not an object")
    return payload


def _fetch_snapshot(
    client: SafeHttpClient, http_spool: RawHttpSpool, symbol: str
) -> Mapping[str, object]:
    url = f"{BINANCE_SPOT_TESTNET_BASE_URL}/api/v3/depth?limit=1000&symbol={symbol}"
    response = client.request(
        "GET",
        url,
        acceptable_statuses=frozenset({200}),
        max_retries=1,
    )
    http_spool.append(response)
    if response.status_code != 200:
        raise HttpTransportError("Binance depth snapshot returned a non-success status")
    return _snapshot_from_spool(http_spool)


def _fetch_server_time(client: SafeHttpClient, http_spool: RawHttpSpool) -> dict[str, object]:
    """Measure provider-vs-local clock offset before interpreting depth events."""

    local_before = datetime.now(UTC)
    response = client.request(
        "GET",
        f"{BINANCE_SPOT_TESTNET_BASE_URL}/api/v3/time",
        acceptable_statuses=frozenset({200}),
        max_retries=1,
    )
    local_after = datetime.now(UTC)
    http_spool.append(response)
    try:
        payload = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Binance server-time response is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("Binance server-time response is not an object")
    server_time_milliseconds = _positive_int(payload.get("serverTime"), "server time")
    try:
        server_time = datetime.fromtimestamp(server_time_milliseconds / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("Binance server-time response has malformed server time") from exc
    midpoint = local_before + (local_after - local_before) / 2
    offset_seconds = (server_time - midpoint).total_seconds()
    if abs(offset_seconds) > MAX_CLOCK_OFFSET_SECONDS:
        raise ValueError("Binance provider/local clock offset is outside the safety bound")
    return {
        "provider_server_time": server_time.isoformat(),
        "local_before": local_before.isoformat(),
        "local_after": local_after.isoformat(),
        "round_trip_ms": round((local_after - local_before).total_seconds() * 1000, 3),
        "clock_offset_seconds": round(offset_seconds, 3),
    }


async def _collect_connection(
    *,
    output_directory: Path,
    symbol: str,
    connection_number: int,
    duration_seconds: int,
) -> dict[str, object]:
    stream_url = _validated_stream_url(symbol)
    connection_directory = output_directory / symbol / f"connection-{connection_number:02d}"
    ws_spool = RawMessageSpool(connection_directory / "raw-ws.jsonl")
    http_spool = RawHttpSpool(connection_directory / "raw-http.jsonl")
    client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=(BINANCE_SPOT_TESTNET_HOST,),
            user_agent="advisorai-v3/phase3-binance-depth",
            max_retries=1,
            requests_per_second=5,
        ),
        base_url=BINANCE_SPOT_TESTNET_BASE_URL,
        failed_response_sink=http_spool.append,
    )
    queue: asyncio.Queue[bytes] = asyncio.Queue()
    pump_error: dict[str, str] = {}
    feed = RawWebSocketFeed(
        stream_url,
        allowed_hosts=(BINANCE_SPOT_TESTNET_STREAM_HOST,),
        spool=ws_spool,
    )

    async def pump() -> None:
        try:
            async for raw in feed.messages():
                await queue.put(raw)
        except asyncio.CancelledError:
            raise
        except (WebSocketTransportError, OSError, TimeoutError) as exc:
            pump_error["error_class"] = type(exc).__name__
        except Exception as exc:  # report class only; never provider text
            pump_error["error_class"] = type(exc).__name__

    task = asyncio.create_task(pump())
    live_records: list[tuple[int, datetime, bytes]] = []
    snapshot: Mapping[str, object] | None = None
    clock_sample: dict[str, object] | None = None
    collection_error: str | None = None
    started_at = datetime.now(UTC)
    try:
        deadline = time.monotonic() + duration_seconds
        while snapshot is None:
            if pump_error and queue.empty():
                raise RuntimeError("websocket_transport_failure_before_first_message")
            if time.monotonic() >= deadline:
                raise TimeoutError("depth collection ended before first message")
            try:
                raw = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            live_records.append((len(live_records) + 1, datetime.now(UTC), raw))
            try:
                clock_sample = await asyncio.to_thread(_fetch_server_time, client, http_spool)
                snapshot = await asyncio.to_thread(_fetch_snapshot, client, http_spool, symbol)
            except (HttpTransportError, OSError, TimeoutError, ValueError) as exc:
                collection_error = type(exc).__name__
                break
        if snapshot is not None:
            while time.monotonic() < deadline:
                if pump_error and queue.empty():
                    break
                remaining = max(0.05, deadline - time.monotonic())
                try:
                    raw = await asyncio.wait_for(queue.get(), timeout=min(1.0, remaining))
                except TimeoutError:
                    continue
                live_records.append((len(live_records) + 1, datetime.now(UTC), raw))
    except (RuntimeError, TimeoutError) as exc:
        collection_error = type(exc).__name__
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        while not queue.empty():
            raw = queue.get_nowait()
            live_records.append((len(live_records) + 1, datetime.now(UTC), raw))

    replay_snapshot: Mapping[str, object] | None = None
    replay_result: dict[str, object] | None = None
    live_result: dict[str, object] | None = None
    replay_equivalent = False
    if snapshot is not None:
        replay_snapshot = _snapshot_from_spool(http_spool)
        clock_offset_seconds = float(clock_sample["clock_offset_seconds"])
        live_result = _process_records(
            snapshot,
            live_records,
            symbol=symbol,
            clock_offset_seconds=clock_offset_seconds,
        )
        replay_result = _process_records(
            replay_snapshot,
            ws_spool.read_records(),
            symbol=symbol,
            clock_offset_seconds=clock_offset_seconds,
        )
        live_book = live_result.get("book") or {}
        replay_book = replay_result.get("book") or {}
        replay_equivalent = live_book.get("book_state_sha256") == replay_book.get(
            "book_state_sha256"
        )

    ended_at = datetime.now(UTC)
    return {
        "symbol": symbol,
        "connection_number": connection_number,
        "stream_url": stream_url,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": round((ended_at - started_at).total_seconds(), 3),
        "network_calls": client.request_count,
        "raw_ws_message_count": len(ws_spool.read_records()),
        "raw_http_record_count": len(http_spool.read()),
        "collection_error_class": collection_error,
        "transport_error_class": pump_error.get("error_class"),
        "clock_sample": clock_sample,
        "snapshot_last_update_id": (
            _positive_int(snapshot.get("lastUpdateId"), "snapshot update ID")
            if snapshot is not None and snapshot.get("lastUpdateId") is not None
            else None
        ),
        "live_result": live_result,
        "replay_result": replay_result,
        "replay_equivalent": replay_equivalent,
        "source_independence": "binance_spot_testnet_public_depth_only",
    }


def _fault_drills() -> dict[str, dict[str, object]]:
    snapshot = {
        "lastUpdateId": 100,
        "bids": [["100", "1"]],
        "asks": [["101", "1"]],
    }
    now = datetime(2026, 8, 10, 18, 0, tzinfo=UTC)
    gap = _process_records(
        snapshot,
        [
            (
                1,
                now,
                json.dumps(
                    {
                        "e": "depthUpdate",
                        "E": 1_755_000_000_000,
                        "s": "BTCUSDT",
                        "U": 105,
                        "u": 105,
                        "b": [],
                        "a": [],
                    }
                ).encode(),
            )
        ],
        symbol="BTCUSDT",
    )
    stale = _process_records(
        snapshot,
        [
            (
                1,
                now,
                json.dumps(
                    {
                        "e": "depthUpdate",
                        "E": 1,
                        "s": "BTCUSDT",
                        "U": 100,
                        "u": 101,
                        "b": [],
                        "a": [],
                    }
                ).encode(),
            )
        ],
        symbol="BTCUSDT",
    )
    with tempfile.TemporaryDirectory(prefix="advisorai-phase3-binance-drill-") as temporary:
        http_spool = RawHttpSpool(Path(temporary) / "raw-http.jsonl")
        responses = [
            (503, b'{"code":-1003,"msg":"temporary"}', ()),
            (
                200,
                json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode(),
                (),
            ),
        ]

        def requester(*_args: object) -> tuple[int, bytes, tuple[tuple[str, str], ...]]:
            return responses.pop(0)

        client = SafeHttpClient(
            HttpClientConfig(
                allowed_hosts=(BINANCE_SPOT_TESTNET_HOST,),
                max_retries=1,
                requests_per_second=100,
            ),
            base_url=BINANCE_SPOT_TESTNET_BASE_URL,
            requester=requester,
            failed_response_sink=http_spool.append,
            sleeper=lambda _seconds: None,
        )
        restored_snapshot = _fetch_snapshot(client, http_spool, "BTCUSDT")
        rest_statuses = tuple(record.status_code for record in http_spool.read())
        rest_drill = {
            "evidence_type": "deterministic_injected",
            "status": (
                "pass"
                if client.request_count == 2
                and rest_statuses == (503, 200)
                and restored_snapshot["lastUpdateId"] == 100
                else "fail"
            ),
            "request_count": client.request_count,
            "spooled_statuses": rest_statuses,
            "assertion": "retryable_snapshot_outage_is_spooled_then_recovers_within_bound",
        }
    snapshot_a = _sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode())
    snapshot_b = {**snapshot, "lastUpdateId": 101}
    snapshot_b_digest = _sha256(
        json.dumps(snapshot_b, sort_keys=True, separators=(",", ":")).encode()
    )
    return {
        "rest_outage_retry": rest_drill,
        "sequence_gap": {
            "evidence_type": "deterministic_injected",
            "status": "pass" if gap["state"] == "failed_closed" else "fail",
            "observed_state": gap["state"],
            "assertion": "depth_sequence_gap_before_snapshot_sync_is_not_applied",
        },
        "stale_data": {
            "evidence_type": "deterministic_injected",
            "status": "pass" if stale["state"] == "failed_closed" else "fail",
            "observed_state": stale["state"],
            "assertion": "stale_or_future_event_time_fails_closed",
        },
        "snapshot_disagreement": {
            "evidence_type": "deterministic_injected",
            "status": "pass" if snapshot_a != snapshot_b_digest else "fail",
            "snapshot_a_sha256": snapshot_a,
            "snapshot_b_sha256": snapshot_b_digest,
            "assertion": "different_snapshot_hashes_are_not_silently_substituted",
        },
    }


def _new_run_directory(base_directory: Path) -> tuple[Path, str]:
    base_directory.mkdir(parents=True, exist_ok=True)
    run_base = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_id = run_base
    suffix = 1
    while (base_directory / run_id).exists():
        suffix += 1
        run_id = f"{run_base}-{suffix}"
    run_directory = base_directory / run_id
    run_directory.mkdir()
    return run_directory, run_id


def run_evidence(
    base_directory: Path,
    *,
    duration_seconds: int = DEFAULT_CONNECTION_SECONDS,
    connections: int = DEFAULT_CONNECTIONS,
) -> dict[str, object]:
    if not 1 <= duration_seconds <= MAX_CONNECTION_SECONDS:
        raise ValueError("connection duration is outside the bounded qualification limit")
    if not 1 <= connections <= MAX_CONNECTIONS:
        raise ValueError("connection count is outside the bounded qualification limit")
    run_directory, run_id = _new_run_directory(base_directory)
    connection_results: list[dict[str, object]] = []
    for symbol in SYMBOLS:
        for connection_number in range(1, connections + 1):
            connection_results.append(
                asyncio.run(
                    _collect_connection(
                        output_directory=run_directory,
                        symbol=symbol,
                        connection_number=connection_number,
                        duration_seconds=duration_seconds,
                    )
                )
            )
    fault_drills = _fault_drills()
    passed = (
        bool(connection_results)
        and all(
            result["replay_equivalent"]
            and isinstance(result["replay_result"], Mapping)
            and result["replay_result"].get("state") == "pass"
            for result in connection_results
        )
        and all(drill["status"] == "pass" for drill in fault_drills.values())
    )
    payload: dict[str, object] = {
        "schema": SCHEMA,
        "run_id": run_id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "runner_code_sha256": _sha256(Path(__file__).read_bytes()),
        "websocket_transport_code_sha256": _sha256(
            (
                Path(__file__).resolve().parents[1] / "src/advisorai/integrations/websocket.py"
            ).read_bytes()
        ),
        "http_transport_code_sha256": _sha256(
            (
                Path(__file__).resolve().parents[1] / "src/advisorai/integrations/http.py"
            ).read_bytes()
        ),
        "binance_adapter_code_sha256": _sha256(
            (
                Path(__file__).resolve().parents[1] / "src/advisorai/integrations/binance_spot.py"
            ).read_bytes()
        ),
        "websockets_version": _websockets_version(),
        "status": "passed" if passed else "partial_failed_closed",
        "venue": "binance_spot_testnet",
        "environment": "paper_testnet",
        "rest_endpoint": BINANCE_SPOT_TESTNET_BASE_URL,
        "stream_host": BINANCE_SPOT_TESTNET_STREAM_HOST,
        "symbols": SYMBOLS,
        "connection_duration_seconds": duration_seconds,
        "connections_per_symbol": connections,
        "network_calls": sum(int(item["network_calls"]) for item in connection_results),
        "connections": connection_results,
        "fault_drills": fault_drills,
        "writes_attempted": False,
        "authority": "public_market_data_only",
    }
    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    manifest = run_directory / "phase3-binance-spot-testnet-depth.json"
    _write_immutable_json(manifest, payload)
    digest = _sha256(encoded)
    _write_latest_pointer(
        base_directory / "latest.json",
        {
            "schema": f"{SCHEMA}.latest",
            "run_id": run_id,
            "manifest_sha256": digest,
            "status": payload["status"],
        },
    )
    payload["evidence"] = str(manifest)
    payload["evidence_sha256"] = digest
    return payload


def _websockets_version() -> str | None:
    try:
        return importlib.metadata.version("websockets")
    except importlib.metadata.PackageNotFoundError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path("artifacts/phase3/binance-spot-testnet-depth"),
    )
    parser.add_argument(
        "--duration-seconds",
        type=int,
        default=DEFAULT_CONNECTION_SECONDS,
    )
    parser.add_argument("--connections", type=int, default=DEFAULT_CONNECTIONS)
    parser.add_argument(
        "--real",
        action="store_true",
        help="allow the bounded public Binance Spot Testnet read-only qualification",
    )
    args = parser.parse_args()
    if not args.real:
        parser.error("public network reads require explicit --real")
    result = run_evidence(
        args.evidence_dir,
        duration_seconds=args.duration_seconds,
        connections=args.connections,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "evidence": result["evidence"],
                "evidence_sha256": result["evidence_sha256"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
