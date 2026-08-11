#!/usr/bin/env python3
"""Run durable, append-only public-data qualification for the V3-Core spine.

This runner is deliberately a read-only collector.  It loads no AdvisorAI
credentials, has no order/write method, and keeps public market data separate
from the Binance Spot Testnet execution transport.  A run is resumable in the
same directory, but its original start time, configuration, and code identity
are immutable.  A completed window is evidence for review; it does not by
itself open Phase-3 admission.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import json
import os
import shlex
import signal
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from advisorai.collectors import (
    ClockConfidence,
    DisagreementState,
    RawHttpSpool,
    SourceCandidate,
    SourceDisagreementObservation,
    SourceDisagreementPolicy,
    SourceHealthLedger,
    SourceHealthObservation,
    SourceHealthPolicy,
    SourceHealthState,
    SourceQuote,
    compare_source_quotes,
    recover_binance_depth,
    replay_equivalent,
    select_source,
    transition_source_health,
)
from advisorai.collectors.public_market_data import (
    PublicMarketDataSource,
    reviewed_public_market_data_sources,
)
from advisorai.integrations import RawMessageSpool, RawWebSocketFeed
from advisorai.integrations.http import HttpClientConfig, HttpTransportError, SafeHttpClient
from scripts.qualify_phase3_public_market_data import (
    _message_metadata,
    _run_rest,
    _run_ws,
)

SCHEMA = "advisorai.phase3.public-market-data-durable.v1"
DEFAULT_DURATION_HOURS = 2.0
DEFAULT_CYCLE_SECONDS = 90.0
DEFAULT_WINDOW_SECONDS = 5
MAX_DURATION_HOURS = 72.0
MAX_CYCLE_SECONDS = 3600.0
MAX_WINDOW_SECONDS = 30
# A recovery qualification needs provider-truth continuity, not a full-depth
# market snapshot. Keep the raw spool bounded on laptop-class disks while
# preserving the provider's lastUpdateId and enough top-of-book levels for
# deterministic reconstruction.
BINANCE_DEPTH_SNAPSHOT_LIMIT = 100
EXECUTION_HOSTS = frozenset(
    {"testnet.binance.vision", "stream.testnet.binance.vision", "ws-api.testnet.binance.vision"}
)
PRIMARY_IDS = (
    "binance_spot_public_market_data",
    "coinbase_exchange_public_market_data",
)
SOURCE_ASSETS = {
    "binance_spot_public_market_data": {"BTCUSDT": "BTC", "ETHUSDT": "ETH"},
    "coinbase_exchange_public_market_data": {"BTC-USD": "BTC", "ETH-USD": "ETH"},
    "deribit_public_context": {"BTC-PERPETUAL": "BTC", "ETH-PERPETUAL": "ETH"},
}


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _code_sha256() -> str:
    paths = (
        Path(__file__),
        Path(__file__).resolve().parents[1] / "src/advisorai/collectors/source_health.py",
        Path(__file__).resolve().parents[1] / "src/advisorai/collectors/source_disagreement.py",
        Path(__file__).resolve().parents[1] / "src/advisorai/collectors/source_failover.py",
        Path(__file__).resolve().parents[1] / "src/advisorai/collectors/market_recovery.py",
        Path(__file__).resolve().parents[1] / "src/advisorai/collectors/public_market_data.py",
    )
    return _digest(b"".join(path.read_bytes() for path in paths))


def _binance_depth_snapshot_url(source: PublicMarketDataSource, symbol: str) -> str:
    """Build the bounded public recovery snapshot request."""

    return (
        f"{source.rest_base_url}/api/v3/depth?limit={BINANCE_DEPTH_SNAPSHOT_LIMIT}&symbol={symbol}"
    )


def _connection_disconnected(connection: Mapping[str, object]) -> bool:
    """Return whether a sample ended with an observed transport failure."""

    if connection.get("status") != "pass":
        return True
    return any(
        isinstance(connection.get(field), str) and bool(connection[field])
        for field in (
            "collection_error_class",
            "transport_error_class",
            "failure_layer",
            "error_class",
        )
    )


def _sanitized_failure_details(
    *payloads: Mapping[str, object] | None,
) -> tuple[list[str], list[str]]:
    """Project only safe failure labels from transport/collector records.

    The lower-level collectors deliberately persist exception classes rather
    than messages or response bodies.  Keep that boundary when promoting the
    details into the durable source/symbol sample: failure diagnostics are
    useful only if they cannot accidentally become a second response-log
    channel.
    """

    failure_classes: set[str] = set()
    failure_layers: set[str] = set()
    for payload in payloads:
        if not isinstance(payload, Mapping):
            continue
        for field, target in (
            ("error_class", failure_classes),
            ("collection_error_class", failure_classes),
            ("transport_error_class", failure_classes),
            ("failure_layer", failure_layers),
        ):
            value = payload.get(field)
            if not isinstance(value, str) or not value:
                continue
            # Collector values are exception classes/layer identifiers.  Do
            # not carry arbitrary provider text into immutable evidence.
            sanitized = "".join(
                character if character.isalnum() or character in "._-" else "_"
                for character in value
            )[:128]
            if sanitized:
                target.add(sanitized)
    return sorted(failure_classes), sorted(failure_layers)


def _write_immutable(path: Path, payload: object) -> None:
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


def _write_atomic(path: Path, payload: object) -> None:
    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temporary.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class _AppendOnlyLog:
    """A small hash-chained JSONL log used for resumable qualification facts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[dict[str, object]] = []
        self.last_hash: str | None = None
        if not path.exists():
            return
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"append-only evidence is invalid at line {line_number}"
                ) from exc
            if not isinstance(record, dict):
                raise RuntimeError("append-only evidence records must be objects")
            record_hash = record.get("record_hash")
            previous = record.get("previous_record_hash")
            unsigned = {key: value for key, value in record.items() if key != "record_hash"}
            expected = _digest(_canonical(unsigned))
            if record_hash != expected or previous != self.last_hash:
                raise RuntimeError("append-only evidence hash chain is invalid")
            self.records.append(record)
            self.last_hash = str(record_hash)

    def append(self, payload: Mapping[str, object]) -> dict[str, object]:
        if "record_hash" in payload or "previous_record_hash" in payload:
            raise ValueError("append-only payload cannot provide chain fields")
        unsigned = {"previous_record_hash": self.last_hash, **dict(payload)}
        record = {**unsigned, "record_hash": _digest(_canonical(unsigned))}
        with self.path.open("ab") as handle:
            handle.write(
                (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
            )
            handle.flush()
            os.fsync(handle.fileno())
        self.records.append(record)
        self.last_hash = str(record["record_hash"])
        return record


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    with contextlib.suppress(ValueError):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed.astimezone(UTC)
    return None


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return round(ordered[index], 6)


def _raw_records(path: Path) -> tuple[tuple[int, datetime, bytes], ...]:
    if not path.exists():
        return ()
    return RawMessageSpool(path).read_records()


def _snapshot_from_rest(rest_directory: Path, symbol: str) -> Mapping[str, object] | None:
    spool = RawHttpSpool(rest_directory / "raw-http.jsonl")
    matches = tuple(
        record
        for record in spool.read()
        if record.status_code == 200
        and urlsplit(record.url).path == "/api/v3/depth"
        and parse_qs(urlsplit(record.url).query).get("symbol") == [symbol]
    )
    if len(matches) != 1:
        return None
    with contextlib.suppress(UnicodeDecodeError, json.JSONDecodeError):
        payload = json.loads(matches[0].payload)
        if isinstance(payload, Mapping):
            return payload
    return None


async def _collect_binance_public_connection(
    source: PublicMarketDataSource,
    *,
    symbol: str,
    output_directory: Path,
    connection_number: int,
    duration_seconds: int,
) -> dict[str, object]:
    """Buffer a public depth stream before taking its provider-truth snapshot."""

    connection_directory = (
        output_directory / "depth-recovery" / symbol / f"connection-{connection_number:02d}"
    )
    ws_spool = RawMessageSpool(connection_directory / "raw-ws.jsonl")
    http_spool = RawHttpSpool(connection_directory / "raw-http.jsonl")
    client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=(source.rest_host,),
            timeout_seconds=15,
            max_retries=1,
            requests_per_second=5,
            user_agent="advisorai-v3/phase3-public-depth-recovery",
        ),
        base_url=source.rest_base_url,
        failed_response_sink=http_spool.append,
    )
    feed = RawWebSocketFeed(
        f"{source.ws_url}/{symbol.lower()}@depth@100ms",
        allowed_hosts=(source.ws_host,),
        spool=ws_spool,
    )
    queue: asyncio.Queue[bytes] = asyncio.Queue()
    pump_error: dict[str, str] = {}

    async def pump() -> None:
        try:
            async for raw in feed.messages():
                await queue.put(raw)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # persist only the error class
            pump_error["error_class"] = type(exc).__name__

    async def snapshot() -> tuple[dict[str, object], dict[str, object]]:
        server_before = datetime.now(UTC)
        started = time.perf_counter()
        time_response = await asyncio.to_thread(
            client.request,
            "GET",
            f"{source.rest_base_url}/api/v3/time",
            acceptable_statuses=frozenset({200}),
            max_retries=1,
        )
        server_after = datetime.now(UTC)
        http_spool.append(time_response)
        time_payload = json.loads(time_response.body)
        if not isinstance(time_payload, Mapping) or not isinstance(
            time_payload.get("serverTime"), int
        ):
            raise ValueError("public Binance server-time payload is malformed")
        provider_time = datetime.fromtimestamp(time_payload["serverTime"] / 1000, tz=UTC)
        midpoint = server_before + (server_after - server_before) / 2
        clock = {
            "status": "pass",
            "provider_time": provider_time.isoformat(),
            "local_before": server_before.isoformat(),
            "local_after": server_after.isoformat(),
            "clock_offset_seconds": round((provider_time - midpoint).total_seconds(), 6),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        response = await asyncio.to_thread(
            client.request,
            "GET",
            _binance_depth_snapshot_url(source, symbol),
            acceptable_statuses=frozenset({200}),
            max_retries=1,
        )
        http_spool.append(response)
        payload = json.loads(response.body)
        if not isinstance(payload, Mapping):
            raise ValueError("public Binance depth snapshot is malformed")
        return clock, dict(payload)

    task = asyncio.create_task(pump())
    live_records: list[tuple[int, datetime, bytes]] = []
    clock_sample: dict[str, object] | None = None
    snapshot_payload: dict[str, object] | None = None
    collection_error: str | None = None
    started_at = datetime.now(UTC)
    deadline = time.monotonic() + duration_seconds
    try:
        while snapshot_payload is None and time.monotonic() < deadline:
            if pump_error and queue.empty():
                collection_error = "websocket_transport_failure_before_snapshot"
                break
            try:
                raw = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            live_records.append((len(live_records) + 1, datetime.now(UTC), raw))
            try:
                clock_sample, snapshot_payload = await snapshot()
            except (
                HttpTransportError,
                OSError,
                TimeoutError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                collection_error = type(exc).__name__
                break
        if snapshot_payload is not None:
            while time.monotonic() < deadline:
                if pump_error and queue.empty():
                    break
                try:
                    raw = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    continue
                live_records.append((len(live_records) + 1, datetime.now(UTC), raw))
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        while not queue.empty():
            live_records.append((len(live_records) + 1, datetime.now(UTC), queue.get_nowait()))
    ended_at = datetime.now(UTC)
    return {
        "symbol": symbol,
        "connection_number": connection_number,
        "stream_url": f"{source.ws_url}/{symbol.lower()}@depth@100ms",
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": round((ended_at - started_at).total_seconds(), 3),
        "network_calls": client.request_count,
        "raw_ws_message_count": len(ws_spool.read_records()),
        "raw_http_record_count": len(http_spool.read()),
        "collection_error_class": collection_error,
        "transport_error_class": pump_error.get("error_class"),
        "clock_sample": clock_sample,
        "snapshot_last_update_id": snapshot_payload.get("lastUpdateId")
        if snapshot_payload
        else None,
        "snapshot_depth_limit": BINANCE_DEPTH_SNAPSHOT_LIMIT,
        "expected_symbols": [symbol],
        "observed_symbols": [symbol] if live_records else [],
        "status": "pass" if snapshot_payload is not None and live_records else "failed",
        "timed_window_completed": snapshot_payload is not None,
        "subscription_acknowledged": False,
        "raw_spool": str(connection_directory / "raw-ws.jsonl"),
        "snapshot_payload": snapshot_payload,
    }


def _run_binance_public_ws(
    source: PublicMarketDataSource, run_directory: Path, duration_seconds: int
) -> dict[str, object]:
    async def collect() -> dict[str, object]:
        # Collect required symbols concurrently.  Sequential symbol windows
        # make the first symbol appear stale while the later symbol is being
        # measured, which is a local observation-order artifact rather than
        # provider freshness.
        connections = await asyncio.gather(
            *(
                _collect_binance_public_connection(
                    source,
                    symbol=symbol,
                    output_directory=run_directory,
                    connection_number=1,
                    duration_seconds=duration_seconds,
                )
                for symbol in source.symbols
            )
        )
        return {
            "state": "pass" if all(item["status"] == "pass" for item in connections) else "failed",
            "connections": connections,
            "reconnect": {
                symbol: {
                    "attempt_count": 1,
                    "status": item["status"],
                    "evidence_type": "real_external",
                }
                for symbol, item in zip(source.symbols, connections, strict=True)
            },
            "resubscription": {
                "status": "not_applicable",
                "not_applicable": True,
                "subscription_acknowledgements": 0,
            },
            "freshness": {
                "state": "pass"
                if all(item["status"] == "pass" for item in connections)
                else "failed_closed"
            },
            "snapshots": {
                item["symbol"]: item["snapshot_payload"]
                for item in connections
                if isinstance(item.get("snapshot_payload"), Mapping)
            },
        }

    return asyncio.run(collect())


def _binance_recovery(
    rest_directory: Path,
    connections: Sequence[Mapping[str, object]],
    *,
    symbol: str,
    snapshot_override: Mapping[str, object] | None = None,
) -> dict[str, object]:
    snapshot = snapshot_override or _snapshot_from_rest(rest_directory, symbol)
    updates: list[Mapping[str, object]] = []
    malformed = 0
    sequence_out_of_order = 0
    previous_first: int | None = None
    for connection in connections:
        path = connection.get("raw_spool")
        if not isinstance(path, str):
            continue
        for _sequence, _received_at, raw in _raw_records(Path(path)):
            try:
                payload = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                malformed += 1
                continue
            if not isinstance(payload, Mapping) or payload.get("e") != "depthUpdate":
                continue
            if str(payload.get("s", "")).upper() != symbol:
                malformed += 1
                continue
            first = payload.get("U")
            if not isinstance(first, int):
                malformed += 1
                continue
            if previous_first is not None and first < previous_first:
                sequence_out_of_order += 1
            previous_first = first
            updates.append(payload)
    if snapshot is None:
        return {
            "state": "failed_closed",
            "snapshot_state": "failed",
            "sequence_state": "unavailable",
            "replay_equivalent": False,
            "malformed_event_count": malformed,
            "out_of_order_count": sequence_out_of_order,
            "snapshot_recovery": "not_established",
        }
    live, _ = recover_binance_depth(snapshot, updates, symbol=symbol, snapshot_reacquired=True)
    replay, _ = recover_binance_depth(
        snapshot, tuple(updates), symbol=symbol, snapshot_reacquired=True
    )
    equivalent = replay_equivalent(live, replay)
    return {
        "state": live.state,
        "snapshot_state": "pass" if live.state == "pass" else "recovery_required",
        "sequence_state": "pass" if live.sequence_gap_count == 0 else "gap",
        "replay_equivalent": equivalent,
        "malformed_event_count": malformed + live.malformed_update_count,
        "out_of_order_count": sequence_out_of_order,
        "sequence_gap_count": live.sequence_gap_count,
        "snapshot_recovery": "not_required" if live.state == "pass" else "failed_closed",
        "reconstructed_book_sha256": live.reconstructed_book_sha256,
        "recovery_result": live.model_dump(mode="json"),
    }


def _generic_sequence_metrics(
    source: PublicMarketDataSource,
    symbol: str,
    connections: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    provider_sequences: list[int] = []
    provider_times: list[datetime] = []
    received_times: list[datetime] = []
    malformed = 0
    observed_symbols: set[str] = set()
    for connection in connections:
        path = connection.get("raw_spool")
        if not isinstance(path, str):
            continue
        for _sequence, received_at, raw in _raw_records(Path(path)):
            try:
                payload = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                malformed += 1
                continue
            if not isinstance(payload, Mapping):
                malformed += 1
                continue
            kind, symbols, provider_time = _message_metadata(raw, source.source_id)
            del kind
            if symbols:
                observed_symbols.update(symbols)
            matches_symbol = source.source_id == "deribit_public_context" and (
                symbol in symbols or not symbols
            )
            if source.source_id != "deribit_public_context":
                matches_symbol = symbol.upper() in symbols
            if not matches_symbol:
                continue
            if provider_time is not None:
                provider_times.append(provider_time)
                received_times.append(received_at)
            value = payload.get("sequence")
            if isinstance(value, int) and not isinstance(value, bool):
                provider_sequences.append(value)
    gaps = 0
    duplicates = 0
    out_of_order = 0
    previous: int | None = None
    for sequence in provider_sequences:
        if previous is not None:
            if sequence > previous + 1:
                gaps += sequence - previous - 1
            elif sequence == previous:
                duplicates += 1
            elif sequence < previous:
                out_of_order += 1
        previous = sequence
    ages = [
        (received - provider).total_seconds()
        for provider, received in zip(provider_times, received_times, strict=False)
    ]
    sequence_semantics = "continuous_provider_sequence"
    if source.source_id == "coinbase_exchange_public_market_data":
        # Coinbase ticker/heartbeat sequences are provider-global product
        # counters, not a contiguous sequence for every ticker message.  Keep
        # the raw values and detect reordering, but do not manufacture a gap
        # from updates that were never subscribed to.
        gaps = 0
        sequence_semantics = "provider_counter_not_continuous_for_ticker_channel"
    return {
        "valid_event_count": len(provider_times),
        "observed_symbols": sorted(observed_symbols),
        "provider_sequence_count": len(provider_sequences),
        "sequence_gap_count": gaps,
        "duplicate_count": duplicates,
        "out_of_order_count": out_of_order,
        "malformed_event_count": malformed,
        "provider_event_age_seconds_max": round(max(ages), 6) if ages else None,
        "provider_event_age_seconds_p95": _percentile(ages, 0.95),
        "provider_event_age_seconds_min": round(min(ages), 6) if ages else None,
        "provider_event_age_samples": ages,
        "last_valid_event_at": max(received_times).isoformat() if received_times else None,
        "provider_event_count": len(provider_times),
        "sequence_state": (
            "gap"
            if source.source_id != "coinbase_exchange_public_market_data" and (gaps or out_of_order)
            else "pass"
            if provider_sequences and sequence_semantics == "continuous_provider_sequence"
            else "unavailable"
        ),
        "sequence_semantics": sequence_semantics,
        "symbol": symbol,
    }


def _source_symbol_result(
    source: PublicMarketDataSource,
    source_directory: Path,
    rest: Mapping[str, object],
    websocket: Mapping[str, object],
    *,
    symbol: str,
    asset: str,
    now: datetime,
    previous_connected: bool | None,
) -> dict[str, object]:
    connections = tuple(
        item
        for item in websocket.get("connections", ())
        if isinstance(item, Mapping) and symbol in item.get("expected_symbols", ())
    )
    if source.source_id == "binance_spot_public_market_data":
        metrics = _generic_sequence_metrics(source, symbol, connections)
        recovery = _binance_recovery(
            source_directory,
            connections,
            symbol=symbol,
            snapshot_override=(
                websocket.get("snapshots", {}).get(symbol)
                if isinstance(websocket.get("snapshots"), Mapping)
                else None
            ),
        )
        sequence_state = recovery["sequence_state"]
        snapshot_state = recovery["snapshot_state"]
        replay_state = recovery["replay_equivalent"]
        metrics.update(recovery)
    else:
        metrics = _generic_sequence_metrics(source, symbol, connections)
        sequence_state = metrics["sequence_state"]
        snapshot_state = "not_applicable"
        replay_state = bool(
            connections
            and all(
                isinstance(item.get("raw_spool"), str) and Path(str(item["raw_spool"])).exists()
                for item in connections
            )
        )
        metrics["snapshot_recovery"] = "not_applicable"
        metrics["replay_equivalent"] = replay_state
    successful_connections = sum(item.get("status") == "pass" for item in connections)
    attempts = len(connections)
    last_event = _parse_time(metrics.get("last_valid_event_at"))
    age = max(0.0, (now - last_event).total_seconds()) if last_event else None
    rest_status = rest.get("required_read_state") == "pass"
    server_time = rest.get("server_time")
    clock_offset = (
        server_time.get("clock_offset_seconds") if isinstance(server_time, Mapping) else None
    )
    clock_confidence = (
        ClockConfidence.HIGH
        if rest_status and isinstance(clock_offset, (int, float)) and abs(float(clock_offset)) <= 5
        else ClockConfidence.DEGRADED
        if isinstance(clock_offset, (int, float))
        else ClockConfidence.UNKNOWN
    )
    raw_ages = tuple(
        float(value)
        for value in metrics.get("provider_event_age_samples", ())
        if isinstance(value, (int, float))
    )
    adjusted_ages = tuple(
        value + float(clock_offset) for value in raw_ages if isinstance(clock_offset, (int, float))
    )
    adjusted_future_count = sum(value < 0 for value in adjusted_ages)
    if adjusted_future_count:
        clock_confidence = ClockConfidence.DEGRADED
    connected = bool(successful_connections and metrics.get("valid_event_count", 0))
    reconnect_state = (
        "reconnecting"
        if connected and previous_connected is False
        else "stable"
        if connected
        else "reconnecting"
        if previous_connected is True
        else "failed"
    )
    raw_spool_paths = [
        Path(str(item["raw_spool"]))
        for item in connections
        if isinstance(item.get("raw_spool"), str)
    ]
    raw_spool_paths.extend(source_directory.rglob("raw-http.jsonl"))
    market = rest.get("markets")
    market_record = market.get(symbol) if isinstance(market, Mapping) else None
    market_book = market_record.get("order_book") if isinstance(market_record, Mapping) else None
    market_trades = (
        market_record.get("public_trades") if isinstance(market_record, Mapping) else None
    )
    failure_classes, failure_layers = _sanitized_failure_details(
        rest,
        rest.get("server_time") if isinstance(rest.get("server_time"), Mapping) else None,
        rest.get("catalogue") if isinstance(rest.get("catalogue"), Mapping) else None,
        market_record if isinstance(market_record, Mapping) else None,
        market_book if isinstance(market_book, Mapping) else None,
        market_trades if isinstance(market_trades, Mapping) else None,
        websocket,
        *connections,
    )
    return {
        "source_id": source.source_id,
        "provider_identity": source.source_id,
        "endpoint": source.ws_url,
        "rest_endpoint": source.rest_base_url,
        "symbol": asset,
        "provider_symbol": symbol,
        "connected": connected,
        "successful_connections": successful_connections,
        "connection_attempts": attempts,
        "disconnects": sum(_connection_disconnected(item) for item in connections),
        "reconnects": int(previous_connected is False and connected),
        "resubscriptions": int(
            websocket.get("resubscription", {}).get("subscription_acknowledgements", 0)
        )
        if isinstance(websocket.get("resubscription"), Mapping)
        else 0,
        "last_valid_event_at": metrics.get("last_valid_event_at"),
        "last_valid_event_age_seconds": round(age, 6) if age is not None else None,
        "clock_offset_seconds": clock_offset,
        "clock_confidence": clock_confidence.value,
        "sequence_state": sequence_state,
        "snapshot_state": snapshot_state,
        "reconnect_state": reconnect_state,
        "replay_equivalent": bool(replay_state),
        "raw_spool_hashes": sorted(
            _digest(path.read_bytes()) for path in raw_spool_paths if path.exists()
        ),
        "stale_interval_count": int(age is None or age > SourceHealthPolicy().stale_after_seconds),
        "max_event_age_seconds": round(max(adjusted_ages), 6) if adjusted_ages else None,
        "p95_event_age_seconds": _percentile(adjusted_ages, 0.95),
        "raw_provider_event_age_seconds_max": (round(max(raw_ages), 6) if raw_ages else None),
        "adjusted_provider_event_age_seconds_max": (
            round(max(adjusted_ages), 6) if adjusted_ages else None
        ),
        "adjusted_provider_event_age_seconds_min": (
            round(min(adjusted_ages), 6) if adjusted_ages else None
        ),
        "adjusted_future_provider_event_count": adjusted_future_count,
        "sequence_gap_count": metrics.get("sequence_gap_count", 0),
        "duplicate_count": metrics.get("duplicate_count", 0),
        "out_of_order_count": metrics.get("out_of_order_count", 0),
        "malformed_event_count": metrics.get("malformed_event_count", 0),
        "valid_event_count": metrics.get("valid_event_count", 0),
        "source_contract_valid": rest_status,
        "websocket_state": websocket.get("state"),
        "rest_state": rest.get("required_read_state"),
        "sequence_semantics": metrics.get("sequence_semantics", "continuous_provider_sequence"),
        "snapshot_recovery": metrics.get("snapshot_recovery"),
        "snapshot_recovery_attempt_count": int(
            metrics.get("snapshot_recovery") not in {None, "not_required", "not_applicable"}
        ),
        "failure_classes": failure_classes,
        "failure_layers": failure_layers,
        "downtime_ratio": 0.0 if connected else 1.0,
    }


def _top_quote(
    source_id: str, provider_identity: str, asset: str, market: Mapping[str, object], now: datetime
) -> SourceQuote | None:
    book = market.get("order_book") if isinstance(market, Mapping) else None
    if not isinstance(book, Mapping):
        return None
    bid = book.get("top_bid")
    ask = book.get("top_ask")
    if not isinstance(bid, Mapping) or not isinstance(ask, Mapping):
        return None
    try:
        return SourceQuote(
            source_id=source_id,
            provider_identity=provider_identity,
            symbol=asset,
            bid=Decimal(str(bid["price"])),
            ask=Decimal(str(ask["price"])),
            received_at=now,
            clock_confident=True,
        )
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return None


def _fault_drills() -> dict[str, object]:
    snapshot = {"lastUpdateId": 100, "bids": [["100", "1"]], "asks": [["101", "1"]]}
    first = {"e": "depthUpdate", "s": "BTCUSDT", "U": 101, "u": 101, "b": [["100", "2"]], "a": []}
    gap = {"e": "depthUpdate", "s": "BTCUSDT", "U": 103, "u": 103, "b": [], "a": [["101", "2"]]}
    failed, _ = recover_binance_depth(snapshot, (first, gap), symbol="BTCUSDT")
    recovered_snapshot = {**snapshot, "lastUpdateId": 102}
    recovered, _ = recover_binance_depth(recovered_snapshot, (gap,), symbol="BTCUSDT")
    return {
        "sequence_gap": {
            "evidence_type": "deterministic_injected",
            "status": "pass"
            if failed.state == "failed_closed" and failed.local_book_invalidated
            else "fail",
            "assertion": "gap_invalidates_local_book_and_fails_closed",
        },
        "snapshot_recovery": {
            "evidence_type": "deterministic_injected",
            "status": "pass"
            if recovered.state == "pass" and recovered.snapshot_reacquired
            else "fail",
            "assertion": "new_provider_snapshot_reestablishes_continuity",
        },
    }


def _build_disagreement(
    rest_by_source: Mapping[str, Mapping[str, object]], now: datetime
) -> dict[str, SourceDisagreementObservation]:
    result: dict[str, SourceDisagreementObservation] = {}
    mapping = {
        "binance_spot_public_market_data": {"BTCUSDT": "BTC", "ETHUSDT": "ETH"},
        "coinbase_exchange_public_market_data": {"BTC-USD": "BTC", "ETH-USD": "ETH"},
    }
    for asset in ("BTC", "ETH"):
        quotes: dict[str, SourceQuote] = {}
        for source_id, symbols in mapping.items():
            rest = rest_by_source.get(source_id)
            markets = rest.get("markets") if isinstance(rest, Mapping) else None
            if not isinstance(markets, Mapping):
                continue
            symbol = next((item for item, value in symbols.items() if value == asset), None)
            market = markets.get(symbol) if symbol else None
            quote = (
                _top_quote(source_id, source_id, asset, market, now)
                if isinstance(market, Mapping)
                else None
            )
            if quote is not None:
                quotes[source_id] = quote
        if len(quotes) == 2:
            result[asset] = compare_source_quotes(
                quotes["binance_spot_public_market_data"],
                quotes["coinbase_exchange_public_market_data"],
                policy=SourceDisagreementPolicy(),
                measured_at=now,
            )
    return result


def _source_candidates(
    latest_states: Mapping[tuple[str, str], SourceHealthState],
    source_contracts: Mapping[str, bool],
) -> tuple[SourceCandidate, ...]:
    candidates: list[SourceCandidate] = []
    for source in reviewed_public_market_data_sources():
        if source.role != "primary_candidate":
            continue
        assets = tuple(SOURCE_ASSETS[source.source_id].values())
        if not all(
            latest_states.get((source.source_id, asset)) is SourceHealthState.HEALTHY
            for asset in assets
        ):
            state = SourceHealthState.DEGRADED
        else:
            state = SourceHealthState.HEALTHY
        candidates.append(
            SourceCandidate(
                source_id=source.source_id,
                provider_identity=source.source_id,
                endpoint=source.ws_url,
                health_state=state,
                contract_valid=source_contracts.get(source.source_id, False),
                read_only=True,
                symbols=assets,
                priority=0 if source.source_id.startswith("binance") else 1,
            )
        )
    return tuple(candidates)


def _collect_source_window(
    source: PublicMarketDataSource,
    cycle_root: Path,
    window_seconds: int,
) -> tuple[dict[str, object], dict[str, object], datetime]:
    """Collect one source in its own worker so source age is not skewed by peers."""

    source_root = cycle_root / source.source_id
    source_root.mkdir(parents=True, exist_ok=True)
    try:
        rest = _run_rest(source, source_root)
    except Exception as exc:
        rest = {
            "status": "failed",
            "required_read_state": "failed",
            "error_class": type(exc).__name__,
            "markets": {},
            "server_time": {"status": "failed"},
        }
    try:
        server_time = rest.get("server_time")
        offset = (
            server_time.get("clock_offset_seconds") if isinstance(server_time, Mapping) else None
        )
        if source.source_id == "binance_spot_public_market_data":
            websocket = _run_binance_public_ws(source, source_root, window_seconds)
        else:
            websocket = _run_ws(
                source,
                source_root,
                window_seconds,
                1,
                float(offset) if isinstance(offset, (int, float)) else None,
            )
    except Exception as exc:
        websocket = {
            "state": "failed",
            "connections": [],
            "reconnect": {},
            "resubscription": {"subscription_acknowledgements": 0},
            "error_class": type(exc).__name__,
        }
    return rest, websocket, datetime.now(UTC)


def _summary(
    *,
    run_id: str,
    started_at: datetime,
    target_end: datetime,
    records: Sequence[Mapping[str, object]],
    latest_states: Mapping[tuple[str, str], SourceHealthState],
    state: str,
    code_sha256: str,
) -> dict[str, object]:
    ages = [
        float(record["adjusted_provider_event_age_seconds_max"])
        for record in records
        if isinstance(record.get("adjusted_provider_event_age_seconds_max"), (int, float))
    ]
    aggregate_fields = (
        "valid_event_count",
        "connection_attempts",
        "successful_connections",
        "disconnects",
        "reconnects",
        "resubscriptions",
        "stale_interval_count",
        "sequence_gap_count",
        "duplicate_count",
        "out_of_order_count",
        "snapshot_recovery_attempt_count",
        "malformed_event_count",
    )
    totals = {field: 0 for field in aggregate_fields}
    by_source_symbol: dict[str, dict[str, object]] = {}
    age_values: dict[str, list[float]] = {}
    for record in records:
        source_id = record.get("source_id")
        symbol = record.get("symbol")
        if not isinstance(source_id, str) or not isinstance(symbol, str):
            continue
        key = f"{source_id}:{symbol}"
        bucket = by_source_symbol.setdefault(
            key,
            {
                "source_id": source_id,
                "symbol": symbol,
                "sample_count": 0,
                "downtime_ratio": 0.0,
                "health_states": [],
                "failure_classes": [],
                "failure_layers": [],
                "failure_class_counts": {},
                "failure_layer_counts": {},
            },
        )
        bucket["sample_count"] = int(bucket["sample_count"]) + 1
        bucket["downtime_ratio"] = float(bucket["downtime_ratio"]) + (
            0.0 if bool(record.get("connected")) else 1.0
        )
        states = bucket["health_states"]
        if isinstance(states, list) and record.get("health_state") not in states:
            states.append(record.get("health_state"))
        for field in aggregate_fields:
            value = record.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[field] += int(value)
                bucket[field] = int(bucket.get(field, 0)) + int(value)
        for field, counts_field in (
            ("failure_classes", "failure_class_counts"),
            ("failure_layers", "failure_layer_counts"),
        ):
            values = record.get(field)
            if not isinstance(values, list):
                continue
            labels = bucket[field]
            counts = bucket[counts_field]
            if not isinstance(labels, list) or not isinstance(counts, dict):
                continue
            for value in values:
                if not isinstance(value, str):
                    continue
                if value not in labels:
                    labels.append(value)
                counts[value] = int(counts.get(value, 0)) + 1
        age = record.get("adjusted_provider_event_age_seconds_max")
        if isinstance(age, (int, float)):
            age_values.setdefault(key, []).append(float(age))
    for key, bucket in by_source_symbol.items():
        count = int(bucket["sample_count"])
        bucket["downtime_ratio"] = round(float(bucket["downtime_ratio"]) / max(1, count), 6)
        values = age_values.get(key, [])
        bucket["maximum_adjusted_event_age_seconds"] = round(max(values), 6) if values else None
        bucket["p95_adjusted_event_age_seconds"] = _percentile(values, 0.95)
    terminal_sample_count = sum(record.get("terminal_sample") is True for record in records)
    return {
        "schema": f"{SCHEMA}.summary",
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "target_end_at": target_end.isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "state": state,
        "qualification_result": "evidence_for_review_only",
        "phase3_admission_opened": False,
        "code_sha256": code_sha256,
        "sample_count": len(records),
        "terminal_sample_count": terminal_sample_count,
        "maximum_event_age_seconds": round(max(ages), 6) if ages else None,
        "p95_event_age_seconds": _percentile(ages, 0.95),
        "totals": totals,
        "per_source_symbol": by_source_symbol,
        "downtime_ratio": round(
            sum(1.0 for record in records if not bool(record.get("connected")))
            / max(1, len(records)),
            6,
        ),
        "source_health_states": {
            f"{source_id}:{symbol}": value.value
            for (source_id, symbol), value in sorted(latest_states.items())
        },
        "execution_separation": {
            "credentials_loaded": False,
            "order_writes_attempted": False,
            "execution_hosts": sorted(EXECUTION_HOSTS),
            "public_connectors_are_read_only": True,
        },
    }


def _health_snapshot(
    run_id: str,
    latest_states: Mapping[tuple[str, str], SourceHealthState],
    records: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    latest_by_key: dict[tuple[str, str], Mapping[str, object]] = {}
    for record in records:
        source_id = record.get("source_id")
        symbol = record.get("symbol")
        if isinstance(source_id, str) and isinstance(symbol, str):
            latest_by_key[(source_id, symbol)] = record
    return {
        "schema": f"{SCHEMA}.health-snapshot",
        "updated_at": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "phase3_admission_opened": False,
        "sources": [
            {
                "source_id": source_id,
                "symbol": symbol,
                "state": source_state.value,
                "last_event_age_seconds": latest_by_key.get((source_id, symbol), {}).get(
                    "last_valid_event_age_seconds"
                ),
                "freshness": "fresh"
                if source_state is SourceHealthState.HEALTHY
                else "fail_closed",
                "reconnect_count": latest_by_key.get((source_id, symbol), {}).get("reconnects", 0),
                "sequence_gap_count": latest_by_key.get((source_id, symbol), {}).get(
                    "sequence_gap_count", 0
                ),
                "disagreement_state": latest_by_key.get((source_id, symbol), {}).get(
                    "disagreement_state", DisagreementState.UNMEASURED.value
                ),
                "snapshot_recovery_state": latest_by_key.get((source_id, symbol), {}).get(
                    "snapshot_recovery", "unmeasured"
                ),
                "failure_classes": latest_by_key.get((source_id, symbol), {}).get(
                    "failure_classes", []
                ),
                "failure_layers": latest_by_key.get((source_id, symbol), {}).get(
                    "failure_layers", []
                ),
                "actual_provider_identity": latest_by_key.get((source_id, symbol), {}).get(
                    "provider_identity", source_id
                ),
                "fail_closed": source_state is not SourceHealthState.HEALTHY,
            }
            for (source_id, symbol), source_state in sorted(latest_states.items())
        ],
    }


def _terminal_sample_due(
    cycle_started_at: datetime,
    target_end: datetime,
    terminal_cycle_completed: bool,
) -> bool:
    """Return whether this cycle is the one explicit post-boundary sample."""

    return not terminal_cycle_completed and cycle_started_at >= target_end


def run_qualification(
    run_directory: Path,
    *,
    duration_hours: float = DEFAULT_DURATION_HOURS,
    cycle_seconds: float = DEFAULT_CYCLE_SECONDS,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    real: bool = False,
    max_cycles: int | None = None,
) -> dict[str, object]:
    if not real:
        raise ValueError("real public qualification requires explicit real=True")
    if duration_hours <= 0 or duration_hours > MAX_DURATION_HOURS:
        raise ValueError("duration_hours is outside the bounded qualification range")
    if cycle_seconds <= 0 or cycle_seconds > MAX_CYCLE_SECONDS:
        raise ValueError("cycle_seconds is outside the bounded qualification range")
    if window_seconds <= 0 or window_seconds > MAX_WINDOW_SECONDS:
        raise ValueError("window_seconds is outside the bounded qualification range")
    if max_cycles is not None and max_cycles < 1:
        raise ValueError("max_cycles must be positive")

    run_directory.mkdir(parents=True, exist_ok=True)
    code_sha256 = _code_sha256()
    config_path = run_directory / "config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config.get("code_sha256") != code_sha256:
            raise RuntimeError("qualification code identity changed; quarantine this run")
        if float(config["duration_hours"]) != duration_hours:
            raise RuntimeError("qualification duration cannot change on resume")
        if float(config["cycle_seconds"]) != cycle_seconds:
            raise RuntimeError("qualification cycle interval cannot change on resume")
        if int(config["window_seconds"]) != window_seconds:
            raise RuntimeError("qualification sample window cannot change on resume")
        if config.get("max_cycles") != max_cycles:
            raise RuntimeError("qualification max_cycles cannot change on resume")
        run_id = str(config["run_id"])
        started_at = datetime.fromisoformat(str(config["started_at"])).astimezone(UTC)
    else:
        run_id = run_directory.name
        started_at = datetime.now(UTC)
        config = {
            "schema": f"{SCHEMA}.config",
            "run_id": run_id,
            "started_at": started_at.isoformat(),
            "duration_hours": duration_hours,
            "cycle_seconds": cycle_seconds,
            "window_seconds": window_seconds,
            "binance_depth_snapshot_limit": BINANCE_DEPTH_SNAPSHOT_LIMIT,
            "max_cycles": max_cycles,
            "code_sha256": code_sha256,
            "source_health_policy": SourceHealthPolicy().model_dump(mode="json"),
            "source_disagreement_policy": SourceDisagreementPolicy().model_dump(mode="json"),
            "command": [shlex.quote(argument) for argument in os.sys.argv],
            "evidence_root": str(run_directory),
            "credentials_loaded": False,
            "order_writes_attempted": False,
        }
        _write_immutable(config_path, config)
        _write_immutable(run_directory / "fault-drills.json", _fault_drills())

    lock = (run_directory / "runner.lock").open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock.close()
        raise RuntimeError("another Phase-3 qualification process owns this run") from exc
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()

    sample_log = _AppendOnlyLog(run_directory / "samples.jsonl")
    selection_log = _AppendOnlyLog(run_directory / "source-selection.jsonl")
    disagreement_log = _AppendOnlyLog(run_directory / "disagreement.jsonl")
    observation_log = _AppendOnlyLog(run_directory / "observations.jsonl")
    health_ledger = SourceHealthLedger(run_directory / "health-transitions.jsonl")
    latest_states: dict[tuple[str, str], SourceHealthState] = {
        (record.source_id, record.symbol): record.state for record in health_ledger.read()
    }
    prior_connected: dict[tuple[str, str], bool] = {}
    prior_clock_offsets: dict[tuple[str, str], float] = {}
    for record in sample_log.records:
        key = (str(record.get("source_id")), str(record.get("symbol")))
        prior_connected[key] = bool(record.get("connected"))
        offset = record.get("clock_offset_seconds")
        if isinstance(offset, (int, float)):
            prior_clock_offsets[key] = float(offset)
    existing_cycles = {int(record["cycle"]) for record in sample_log.records if "cycle" in record}
    next_cycle = max(existing_cycles, default=0) + 1
    target_end = started_at + timedelta(hours=duration_hours)
    stop_requested = False

    def _stop(_signal: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    old_sigterm = signal.signal(signal.SIGTERM, _stop)
    old_sigint = signal.signal(signal.SIGINT, _stop)
    state = "running"
    terminal_cycle_completed = False
    try:
        while not stop_requested and not terminal_cycle_completed:
            if max_cycles is not None and next_cycle > max_cycles:
                break
            cycle_started = datetime.now(UTC)
            terminal_cycle = _terminal_sample_due(
                cycle_started, target_end, terminal_cycle_completed
            )
            cycle_root = run_directory / "cycles" / f"cycle-{next_cycle:06d}"
            rest_by_source: dict[str, dict[str, object]] = {}
            ws_by_source: dict[str, dict[str, object]] = {}
            observed_at_by_source: dict[str, datetime] = {}
            sources = reviewed_public_market_data_sources()
            with ThreadPoolExecutor(
                max_workers=len(sources), thread_name_prefix="phase3-source"
            ) as pool:
                futures = {
                    source.source_id: pool.submit(
                        _collect_source_window, source, cycle_root, window_seconds
                    )
                    for source in sources
                }
                for source in sources:
                    rest, websocket, observed_at = futures[source.source_id].result()
                    rest_by_source[source.source_id] = rest
                    ws_by_source[source.source_id] = websocket
                    observed_at_by_source[source.source_id] = observed_at

            now = datetime.now(UTC)
            disagreements = _build_disagreement(rest_by_source, now)
            for _asset, observation in disagreements.items():
                disagreement_log.append(
                    {
                        "schema": f"{SCHEMA}.disagreement",
                        "cycle": next_cycle,
                        **observation.model_dump(mode="json"),
                    }
                )
            disagreements_by_asset = {
                asset: observation.state for asset, observation in disagreements.items()
            }
            source_contracts = {
                source_id: rest.get("required_read_state") == "pass"
                for source_id, rest in rest_by_source.items()
            }
            cycle_records: list[dict[str, object]] = []
            for source in sources:
                rest = rest_by_source[source.source_id]
                websocket = ws_by_source[source.source_id]
                for provider_symbol, asset in SOURCE_ASSETS[source.source_id].items():
                    key = (source.source_id, asset)
                    result = _source_symbol_result(
                        source,
                        cycle_root / source.source_id,
                        rest,
                        websocket,
                        symbol=provider_symbol,
                        asset=asset,
                        now=observed_at_by_source[source.source_id],
                        previous_connected=prior_connected.get(key),
                    )
                    current_offset = result.get("clock_offset_seconds")
                    result["clock_offset_drift_seconds"] = (
                        round(float(current_offset) - prior_clock_offsets[key], 6)
                        if isinstance(current_offset, (int, float)) and key in prior_clock_offsets
                        else None
                    )
                    observation = SourceHealthObservation(
                        observed_at=now,
                        source_id=source.source_id,
                        provider_identity=source.source_id,
                        endpoint=source.ws_url,
                        symbol=asset,
                        connected=bool(result["connected"]),
                        valid_event_count=int(result["valid_event_count"]),
                        last_valid_event_at=_parse_time(result.get("last_valid_event_at")),
                        last_valid_event_age_seconds=result.get("last_valid_event_age_seconds"),
                        sequence_state=result["sequence_state"],
                        snapshot_state=result["snapshot_state"],
                        reconnect_state=result["reconnect_state"],
                        clock_confidence=result["clock_confidence"],
                        clock_offset_seconds=result.get("clock_offset_seconds"),
                        malformed_event_rate=(
                            min(
                                1.0,
                                float(result["malformed_event_count"])
                                / max(
                                    1,
                                    int(result["valid_event_count"])
                                    + int(result["malformed_event_count"]),
                                ),
                            )
                        ),
                        disagreement_state=disagreements_by_asset.get(
                            asset, DisagreementState.UNMEASURED
                        ),
                        contract_valid=bool(result["source_contract_valid"]),
                        source_identity_valid=result["provider_identity"] == source.source_id,
                    )
                    transition = transition_source_health(
                        latest_states.get(key), observation, policy=SourceHealthPolicy()
                    )
                    if latest_states.get(key) != transition.state:
                        health_ledger.append(transition)
                        latest_states[key] = transition.state
                    observation_log.append(
                        {
                            "schema": f"{SCHEMA}.observation",
                            "cycle": next_cycle,
                            **observation.model_dump(mode="json"),
                        }
                    )
                    record = {
                        "schema": f"{SCHEMA}.sample",
                        "cycle": next_cycle,
                        "sampled_at": now.isoformat(),
                        "cycle_started_at": cycle_started.isoformat(),
                        "cycle_ended_at": datetime.now(UTC).isoformat(),
                        **result,
                        "health_state": latest_states[key].value,
                        "disagreement_state": disagreements_by_asset.get(
                            asset, DisagreementState.UNMEASURED
                        ).value,
                        "source_independence": "public_read_only_source",
                        "execution_venue": "binance_spot_testnet",
                        "credentials_loaded": False,
                        "order_writes_attempted": False,
                        "terminal_sample": terminal_cycle,
                    }
                    sample_log.append(record)
                    cycle_records.append(record)
                    prior_connected[key] = bool(result["connected"])
                    if isinstance(current_offset, (int, float)):
                        prior_clock_offsets[key] = float(current_offset)

            terminal_cycle_completed = terminal_cycle

            candidates = _source_candidates(latest_states, source_contracts)
            for asset in ("BTC", "ETH"):
                current = next(
                    (
                        record.get("selected_source_id")
                        for record in reversed(selection_log.records)
                        if record.get("asset") == asset and record.get("selected_source_id")
                    ),
                    None,
                )
                decision = select_source(
                    candidates, required_symbols=(asset,), current_source_id=current
                )
                selection_log.append(
                    {
                        "schema": f"{SCHEMA}.selection",
                        "cycle": next_cycle,
                        "asset": asset,
                        **decision.model_dump(mode="json"),
                        "actual_source_identity": decision.actual_source_identity,
                        "silent_substitution": False,
                    }
                )
            next_cycle += 1
            summary = _summary(
                run_id=run_id,
                started_at=started_at,
                target_end=target_end,
                records=sample_log.records,
                latest_states=latest_states,
                state="running",
                code_sha256=code_sha256,
            )
            _write_atomic(
                run_directory / "heartbeat.json",
                {
                    "schema": f"{SCHEMA}.heartbeat",
                    "run_id": run_id,
                    "pid": os.getpid(),
                    "heartbeat_at": datetime.now(UTC).isoformat(),
                    "cycle": next_cycle - 1,
                    "started_at": started_at.isoformat(),
                    "target_end_at": target_end.isoformat(),
                    "evidence_root": str(run_directory),
                    "code_sha256": code_sha256,
                    "state": "running",
                },
            )
            _write_atomic(
                run_directory / "latest-health.json",
                _health_snapshot(run_id, latest_states, sample_log.records),
            )
            _write_atomic(
                run_directory / "status.json", {**summary, "pid": os.getpid(), "state": "running"}
            )
            remaining = (target_end - datetime.now(UTC)).total_seconds()
            if remaining > 0:
                time.sleep(min(cycle_seconds, remaining))
        if stop_requested:
            state = "stopped_with_evidence"
        elif max_cycles is not None and next_cycle > max_cycles:
            state = "bounded_window_complete"
        elif terminal_cycle_completed:
            state = "multi_hour_window_complete"
        else:
            state = "incomplete"
    finally:
        signal.signal(signal.SIGTERM, old_sigterm)
        signal.signal(signal.SIGINT, old_sigint)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    summary = _summary(
        run_id=run_id,
        started_at=started_at,
        target_end=target_end,
        records=sample_log.records,
        latest_states=latest_states,
        state=state,
        code_sha256=code_sha256,
    )
    _write_atomic(run_directory / "summary.json", summary)
    _write_atomic(run_directory / "status.json", {**summary, "pid": os.getpid(), "state": state})
    _write_atomic(
        run_directory / "heartbeat.json",
        {
            "schema": f"{SCHEMA}.heartbeat",
            "run_id": run_id,
            "pid": os.getpid(),
            "heartbeat_at": datetime.now(UTC).isoformat(),
            "cycle": next_cycle - 1,
            "started_at": started_at.isoformat(),
            "target_end_at": target_end.isoformat(),
            "evidence_root": str(run_directory),
            "code_sha256": code_sha256,
            "state": state,
        },
    )
    _write_atomic(
        run_directory / "latest-health.json",
        _health_snapshot(run_id, latest_states, sample_log.records),
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", help="allow public read-only network calls")
    parser.add_argument(
        "--run-directory", type=Path, required=True, help="append-only resumable evidence root"
    )
    parser.add_argument("--duration-hours", type=float, default=DEFAULT_DURATION_HOURS)
    parser.add_argument("--cycle-seconds", type=float, default=DEFAULT_CYCLE_SECONDS)
    parser.add_argument("--window-seconds", type=int, default=DEFAULT_WINDOW_SECONDS)
    parser.add_argument("--max-cycles", type=int)
    args = parser.parse_args()
    summary = run_qualification(
        args.run_directory,
        duration_hours=args.duration_hours,
        cycle_seconds=args.cycle_seconds,
        window_seconds=args.window_seconds,
        real=args.real,
        max_cycles=args.max_cycles,
    )
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0 if summary["state"] in {"bounded_window_complete", "multi_hour_window_complete"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
