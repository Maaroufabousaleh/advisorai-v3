#!/usr/bin/env python3
"""Qualify independent public market-data routes for the V3-Core data plane.

The qualifier is intentionally not a venue adapter.  It loads no credentials,
offers no write methods, never calls a trading endpoint, and keeps public
market-data hosts separate from ``BinanceSpotTestnetTransport``.  REST bytes
are spooled before parsing and WSS bytes are spooled by ``RawWebSocketFeed``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from advisorai.collectors import RawHttpSpool
from advisorai.collectors.public_market_data import (
    PublicMarketDataSource,
    reviewed_public_market_data_sources,
)
from advisorai.integrations import RawMessageSpool, RawWebSocketFeed, SafeHttpClient
from advisorai.integrations.http import HttpClientConfig, HttpTransportError

SCHEMA = "advisorai.phase3.public-market-data-qualification.v2"
DEFAULT_CONNECTION_SECONDS = 8
MAX_CONNECTION_SECONDS = 30
DEFAULT_CONNECTION_ROUNDS = 2
MAX_CONNECTION_ROUNDS = 3
REQUIRED_ASSETS = ("BTC", "ETH")
EXECUTION_VENUE = "binance_spot_testnet"
EXECUTION_HOSTS = frozenset(
    {"testnet.binance.vision", "stream.testnet.binance.vision", "ws-api.testnet.binance.vision"}
)


class _ProbeFailure(RuntimeError):
    def __init__(self, error_class: str, *, status_code: int | None = None) -> None:
        self.error_class = error_class
        self.status_code = status_code


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
    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _new_run_directory(base_directory: Path) -> tuple[Path, str]:
    base_directory.mkdir(parents=True, exist_ok=True)
    run_base = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_id = run_base
    suffix = 1
    while (base_directory / run_id).exists():
        suffix += 1
        run_id = f"{run_base}-{suffix}"
    directory = base_directory / run_id
    directory.mkdir()
    return directory, run_id


def _url(source: PublicMarketDataSource, path: str, **params: object) -> str:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    return f"{source.rest_base_url}{path}" + (f"?{query}" if query else "")


def _public_client(source: PublicMarketDataSource, spool: RawHttpSpool) -> SafeHttpClient:
    return SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=(source.rest_host,),
            timeout_seconds=15,
            max_retries=1,
            requests_per_second=2,
            user_agent="advisorai-v3/phase3-public-market-data",
        ),
        base_url=source.rest_base_url,
        failed_response_sink=spool.append,
    )


def _json_get(
    client: SafeHttpClient,
    spool: RawHttpSpool,
    url: str,
) -> tuple[object, dict[str, object]]:
    try:
        response = client.request(
            "GET",
            url,
            acceptable_statuses=frozenset({200}),
            max_retries=1,
        )
    except HttpTransportError as exc:
        raise _ProbeFailure(type(exc).__name__, status_code=exc.status_code) from exc
    spool.append(response)
    try:
        payload = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _ProbeFailure(type(exc).__name__, status_code=response.status_code) from exc
    return payload, {
        "status_code": response.status_code,
        "raw_sha256": _sha256(response.body),
        "payload_bytes": len(response.body),
        "path": urlsplit(url).path,
    }


def _provider_time(payload: object, source_id: str) -> datetime | None:
    value: object = payload
    if isinstance(payload, Mapping) and "result" in payload:
        value = payload["result"]
    if source_id == "binance_spot_public_market_data" and isinstance(payload, Mapping):
        value = payload.get("serverTime")
    if source_id == "coinbase_exchange_public_market_data" and isinstance(payload, Mapping):
        value = payload.get("iso") or payload.get("epoch")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                return parsed.astimezone(UTC)
    return None


def _server_time(
    source: PublicMarketDataSource,
    client: SafeHttpClient,
    spool: RawHttpSpool,
) -> dict[str, object]:
    if source.source_id == "binance_spot_public_market_data":
        url = _url(source, "/api/v3/time")
    elif source.source_id == "coinbase_exchange_public_market_data":
        url = _url(source, "/time")
    else:
        url = _url(source, "/api/v2/public/get_time")
    before = datetime.now(UTC)
    started = time.perf_counter()
    try:
        payload, response = _json_get(client, spool, url)
    except _ProbeFailure as exc:
        return {
            "status": "failed",
            "error_class": exc.error_class,
            "http_status": exc.status_code,
            "path": urlsplit(url).path,
        }
    after = datetime.now(UTC)
    provider = _provider_time(payload, source.source_id)
    midpoint = before + (after - before) / 2
    return {
        "status": "pass" if provider is not None else "failed",
        "http_status": response["status_code"],
        "raw_sha256": response["raw_sha256"],
        "provider_time": provider.isoformat() if provider else None,
        "local_before": before.isoformat(),
        "local_after": after.isoformat(),
        "round_trip_ms": round((after - before).total_seconds() * 1000, 3),
        "clock_offset_seconds": round((provider - midpoint).total_seconds(), 3)
        if provider
        else None,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "path": urlsplit(url).path,
    }


def _binance_filter_summary(record: Mapping[str, object]) -> dict[str, object]:
    filters = {
        item.get("filterType"): item
        for item in record.get("filters", [])
        if isinstance(item, Mapping) and isinstance(item.get("filterType"), str)
    }
    price = filters.get("PRICE_FILTER", {})
    lot = filters.get("LOT_SIZE", {})
    notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
    return {
        "tick_size": price.get("tickSize"),
        "step_size": lot.get("stepSize"),
        "minimum_quantity": lot.get("minQty"),
        "minimum_notional": notional.get("minNotional"),
    }


def _product_summary(
    source: PublicMarketDataSource,
    payloads: Sequence[object],
) -> dict[str, object]:
    records: dict[str, Mapping[str, object]] = {}
    if source.source_id == "binance_spot_public_market_data":
        payload = payloads[0] if payloads else {}
        for record in payload.get("symbols", []) if isinstance(payload, Mapping) else []:
            if isinstance(record, Mapping) and isinstance(record.get("symbol"), str):
                records[record["symbol"].upper()] = record
        required = {}
        for symbol in source.symbols:
            record = records.get(symbol, {})
            required[symbol] = {
                "present": bool(record),
                "status": record.get("status"),
                "base_asset": record.get("baseAsset"),
                "quote_asset": record.get("quoteAsset"),
                "filters": _binance_filter_summary(record) if record else {},
            }
        passed = all(
            item["present"]
            and item["status"] == "TRADING"
            and all(value is not None for value in item["filters"].values())
            for item in required.values()
        )
    elif source.source_id == "coinbase_exchange_public_market_data":
        payload = payloads[0] if payloads else []
        for record in payload if isinstance(payload, list) else []:
            if isinstance(record, Mapping) and isinstance(record.get("id"), str):
                records[record["id"].upper()] = record
        required = {}
        for symbol in source.symbols:
            record = records.get(symbol, {})
            required[symbol] = {
                "present": bool(record),
                "status": record.get("status"),
                "base_asset": record.get("base_currency"),
                "quote_asset": record.get("quote_currency"),
                "filters": {
                    "tick_size": record.get("quote_increment"),
                    "step_size": record.get("base_increment"),
                    "minimum_quantity": record.get("base_min_size"),
                    "minimum_notional": record.get("min_market_funds"),
                },
            }
        passed = all(
            item["present"]
            and str(item["status"]).lower() in {"online", "auction"}
            and all(value is not None for value in item["filters"].values())
            for item in required.values()
        )
    else:
        for payload in payloads:
            instruments = payload.get("result", []) if isinstance(payload, Mapping) else []
            for record in instruments if isinstance(instruments, list) else []:
                if isinstance(record, Mapping) and isinstance(record.get("instrument_name"), str):
                    records[record["instrument_name"].upper()] = record
        required = {
            symbol: {
                "present": symbol in records,
                "status": records.get(symbol, {}).get("instrument_state"),
                "kind": records.get(symbol, {}).get("kind"),
                "filters": {
                    "tick_size": records.get(symbol, {}).get("price_step"),
                    "step_size": records.get(symbol, {}).get("min_trade_amount"),
                    "minimum_quantity": records.get(symbol, {}).get("min_trade_amount"),
                    "minimum_notional": None,
                },
            }
            for symbol in source.symbols
        }
        passed = all(item["present"] for item in required.values())
    return {
        "state": "pass" if passed else "failed",
        "required_products": required,
        "product_count_observed": len(records),
        "role": source.role,
    }


def _quote_level(value: object) -> dict[str, object] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) < 2:
        return None
    price, size = value[0], value[1]
    if not isinstance(price, (str, int, float)) or isinstance(price, bool):
        return None
    if not isinstance(size, (str, int, float)) or isinstance(size, bool):
        return None
    return {"price": str(price), "size": str(size)}


def _book_summary(payload: object) -> dict[str, object]:
    value = payload
    if isinstance(payload, Mapping) and "result" in payload:
        value = payload["result"]
    if not isinstance(value, Mapping):
        return {"state": "failed", "bid_count": 0, "ask_count": 0}
    bids = value.get("bids", [])
    asks = value.get("asks", [])
    return {
        "state": "pass" if isinstance(bids, list) and isinstance(asks, list) else "failed",
        "bid_count": len(bids) if isinstance(bids, list) else 0,
        "ask_count": len(asks) if isinstance(asks, list) else 0,
        "top_bid": _quote_level(bids[0]) if isinstance(bids, list) and bids else None,
        "top_ask": _quote_level(asks[0]) if isinstance(asks, list) and asks else None,
    }


def _trades_summary(payload: object) -> dict[str, object]:
    value = payload.get("result") if isinstance(payload, Mapping) else payload
    if isinstance(value, Mapping):
        value = value.get("trades", [])
    return {
        "state": "pass" if isinstance(value, list) and bool(value) else "failed",
        "trade_count": len(value) if isinstance(value, list) else 0,
    }


def _market_paths(source: PublicMarketDataSource, symbol: str) -> tuple[str, str]:
    if source.source_id == "binance_spot_public_market_data":
        return (
            _url(source, "/api/v3/depth", symbol=symbol, limit=5),
            _url(source, "/api/v3/trades", symbol=symbol, limit=5),
        )
    if source.source_id == "coinbase_exchange_public_market_data":
        return (
            _url(source, f"/products/{symbol}/book", level=1),
            _url(source, f"/products/{symbol}/trades", limit=5),
        )
    instrument = symbol
    return (
        _url(source, "/api/v2/public/get_order_book", instrument_name=instrument, depth=5),
        _url(source, "/api/v2/public/get_last_trades_by_instrument", instrument_name=instrument),
    )


def _run_rest(
    source: PublicMarketDataSource,
    run_directory: Path,
) -> dict[str, object]:
    spool = RawHttpSpool(run_directory / "raw-http.jsonl")
    client = _public_client(source, spool)
    server_time = _server_time(source, client, spool)
    catalogue_url = (
        _url(source, "/api/v3/exchangeInfo")
        if source.source_id == "binance_spot_public_market_data"
        else _url(source, "/products")
        if source.source_id == "coinbase_exchange_public_market_data"
        else _url(source, "/api/v2/public/get_instruments", currency="BTC", kind="future")
    )
    catalogue_payloads: list[object] = []
    try:
        payload, response = _json_get(client, spool, catalogue_url)
        catalogue_payloads.append(payload)
        catalogue_operation: dict[str, object] = {
            "status": "pass",
            "http_status": response["status_code"],
            "raw_sha256": response["raw_sha256"],
            "path": response["path"],
        }
    except _ProbeFailure as exc:
        catalogue_operation = {
            "status": "failed",
            "error_class": exc.error_class,
            "http_status": exc.status_code,
            "path": urlsplit(catalogue_url).path,
        }
    if source.source_id == "deribit_public_context":
        catalogue_payloads = []
        for currency in ("BTC", "ETH"):
            try:
                payload, _response = _json_get(
                    client,
                    spool,
                    _url(
                        source, "/api/v2/public/get_instruments", currency=currency, kind="future"
                    ),
                )
                catalogue_payloads.append(payload)
            except _ProbeFailure:
                pass
    products = _product_summary(source, catalogue_payloads)
    markets: dict[str, object] = {}
    for symbol in source.symbols:
        book_url, trades_url = _market_paths(source, symbol)
        book_record: dict[str, object]
        try:
            book_payload, response = _json_get(client, spool, book_url)
            book_record = {**_book_summary(book_payload), "raw_sha256": response["raw_sha256"]}
        except _ProbeFailure as exc:
            book_record = {
                "state": "failed",
                "error_class": exc.error_class,
                "http_status": exc.status_code,
            }
        trade_record: dict[str, object]
        try:
            trade_payload, response = _json_get(client, spool, trades_url)
            trade_record = {**_trades_summary(trade_payload), "raw_sha256": response["raw_sha256"]}
        except _ProbeFailure as exc:
            trade_record = {
                "state": "failed",
                "error_class": exc.error_class,
                "http_status": exc.status_code,
            }
        markets[symbol] = {"order_book": book_record, "public_trades": trade_record}
    market_passed = all(
        item["order_book"]["state"] == "pass" and item["public_trades"]["state"] == "pass"
        for item in markets.values()
    )
    return {
        "status": "pass"
        if server_time["status"] == "pass" and catalogue_operation["status"] == "pass"
        else "failed",
        "network_calls": client.request_count,
        "server_time": server_time,
        "catalogue": {**catalogue_operation, "product_truth": products},
        "markets": markets,
        "public_order_book_and_trades_state": "pass" if market_passed else "failed",
        "required_read_state": (
            "pass"
            if products["state"] == "pass" and market_passed and server_time["status"] == "pass"
            else "failed"
        ),
    }


def _provider_event_time(payload: Mapping[str, object], source_id: str) -> datetime | None:
    value: object | None = None
    if source_id == "binance_spot_public_market_data":
        value = payload.get("E")
    elif source_id == "coinbase_exchange_public_market_data":
        value = payload.get("time")
    else:
        params = payload.get("params")
        data = params.get("data") if isinstance(params, Mapping) else None
        if isinstance(data, Mapping):
            value = data.get("timestamp")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        with contextlib.suppress(OverflowError, OSError, ValueError):
            return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                return parsed.astimezone(UTC)
    return None


def _message_metadata(raw: bytes, source_id: str) -> tuple[str, set[str], datetime | None]:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "non_json", set(), None
    if not isinstance(payload, Mapping):
        return "json_non_object", set(), None
    symbols: set[str] = set()
    if source_id == "binance_spot_public_market_data":
        value = payload.get("s")
        if isinstance(value, str):
            symbols.add(value.upper())
        kind = str(payload.get("e", "object"))
    elif source_id == "coinbase_exchange_public_market_data":
        value = payload.get("product_id")
        if isinstance(value, str):
            symbols.add(value.upper())
        kind = str(payload.get("type", "object"))
    else:
        if payload.get("id") is not None and "result" in payload:
            kind = "subscription_ack"
        else:
            kind = str(payload.get("method", payload.get("result", "object")))
        text = json.dumps(payload, sort_keys=True)
        for symbol in ("BTC-PERPETUAL", "ETH-PERPETUAL"):
            if symbol in text:
                symbols.add(symbol)
    return kind[:64], symbols, _provider_event_time(payload, source_id)


async def _collect_ws(
    source: PublicMarketDataSource,
    *,
    url: str,
    expected: set[str],
    subscription: Mapping[str, object] | None,
    path: Path,
    duration_seconds: int,
    connection_round: int,
    clock_offset_seconds: float | None,
) -> dict[str, object]:
    spool = RawMessageSpool(path)
    feed = RawWebSocketFeed(url, allowed_hosts=(source.ws_host,), spool=spool)
    started = time.perf_counter()
    observed: set[str] = set()
    message_types: Counter[str] = Counter()
    provider_event_times: list[tuple[datetime, datetime]] = []
    count = 0
    first_received_at: datetime | None = None
    last_received_at: datetime | None = None
    failure_layer: str | None = None
    error_class: str | None = None
    timed_window_completed = False
    subscription_acknowledged = False
    try:
        async with asyncio.timeout(duration_seconds):
            async for raw in feed.messages(subscription=subscription):
                count += 1
                received_at = datetime.now(UTC)
                first_received_at = first_received_at or received_at
                last_received_at = received_at
                kind, symbols, provider_time = _message_metadata(raw, source.source_id)
                message_types[kind] += 1
                if kind in {"subscriptions", "subscription_ack"}:
                    subscription_acknowledged = True
                observed.update(symbols)
                if provider_time is not None:
                    provider_event_times.append((provider_time, received_at))
    except TimeoutError:
        if count == 0:
            failure_layer = "first_message_timeout"
        elif not expected.issubset(observed):
            failure_layer = "required_symbol_timeout"
        else:
            timed_window_completed = True
    except Exception as exc:  # only the class is persisted
        failure_layer = "websocket_runtime"
        error_class = type(exc).__name__
    ages = [
        (received_at - provider_time).total_seconds()
        for provider_time, received_at in provider_event_times
    ]
    adjusted_ages = [age + clock_offset_seconds for age in ages if clock_offset_seconds is not None]
    adjusted_future_count = sum(age < 0 for age in adjusted_ages)
    freshness_state = (
        "pass"
        if adjusted_ages and adjusted_future_count == 0
        else "failed_closed"
        if adjusted_ages
        else "unmeasured"
    )
    return {
        "url": url,
        "connection_round": connection_round,
        "status": "pass" if expected.issubset(observed) and timed_window_completed else "failed",
        "failure_layer": failure_layer,
        "error_class": error_class,
        "raw_message_count": count,
        "raw_message_bytes": sum(len(raw) for _seq, _received, raw in spool.read_records()),
        "message_type_counts": dict(sorted(message_types.items())),
        "first_message_received_at": (
            first_received_at.isoformat() if first_received_at is not None else None
        ),
        "last_message_received_at": (
            last_received_at.isoformat() if last_received_at is not None else None
        ),
        "provider_event_time_count": len(provider_event_times),
        "provider_event_age_seconds_max": round(max(ages), 3) if ages else None,
        "future_provider_event_count": sum(age < 0 for age in ages),
        "clock_offset_seconds": clock_offset_seconds,
        "adjusted_provider_event_age_seconds_max": (
            round(max(adjusted_ages), 3) if adjusted_ages else None
        ),
        "adjusted_provider_event_age_seconds_min": (
            round(min(adjusted_ages), 3) if adjusted_ages else None
        ),
        "adjusted_future_provider_event_count": adjusted_future_count,
        "freshness_state": freshness_state,
        "expected_symbols": sorted(expected),
        "observed_symbols": sorted(observed),
        "timed_window_completed": timed_window_completed,
        "subscription_acknowledged": subscription_acknowledged,
        "reconnect_policy": "bounded caller retry required; no silent source substitution",
        "duration_seconds": round(time.perf_counter() - started, 3),
        "raw_spool": str(path),
    }


def _run_ws(
    source: PublicMarketDataSource,
    run_directory: Path,
    duration_seconds: int,
    connection_rounds: int,
    clock_offset_seconds: float | None,
) -> dict[str, object]:
    async def collect() -> dict[str, object]:
        results: list[dict[str, object]] = []
        for connection_round in range(1, connection_rounds + 1):
            if source.source_id == "binance_spot_public_market_data":
                for symbol in source.symbols:
                    results.append(
                        await _collect_ws(
                            source,
                            url=f"{source.ws_url}/{symbol.lower()}@depth@100ms",
                            expected={symbol},
                            subscription=None,
                            path=run_directory
                            / "raw-ws"
                            / f"{symbol}-round-{connection_round}.jsonl",
                            duration_seconds=duration_seconds,
                            connection_round=connection_round,
                            clock_offset_seconds=clock_offset_seconds,
                        )
                    )
            elif source.source_id == "coinbase_exchange_public_market_data":
                results.append(
                    await _collect_ws(
                        source,
                        url=source.ws_url,
                        expected=set(source.symbols),
                        subscription={
                            "type": "subscribe",
                            "product_ids": list(source.symbols),
                            "channels": ["heartbeat", "ticker"],
                        },
                        path=run_directory / "raw-ws" / f"coinbase-round-{connection_round}.jsonl",
                        duration_seconds=duration_seconds,
                        connection_round=connection_round,
                        clock_offset_seconds=clock_offset_seconds,
                    )
                )
            else:
                results.append(
                    await _collect_ws(
                        source,
                        url=source.ws_url,
                        expected=set(source.symbols),
                        subscription={
                            "jsonrpc": "2.0",
                            "id": connection_round,
                            "method": "public/subscribe",
                            "params": {
                                "channels": [
                                    f"book.{symbol}.none.1.100ms" for symbol in source.symbols
                                ]
                            },
                        },
                        path=run_directory / "raw-ws" / f"deribit-round-{connection_round}.jsonl",
                        duration_seconds=duration_seconds,
                        connection_round=connection_round,
                        clock_offset_seconds=clock_offset_seconds,
                    )
                )
        by_symbol: dict[str, list[dict[str, object]]] = {symbol: [] for symbol in source.symbols}
        for result in results:
            for symbol in result["expected_symbols"]:
                if symbol in by_symbol:
                    by_symbol[symbol].append(result)
        reconnect = {
            symbol: {
                "attempt_count": len(items),
                "status": "pass"
                if len(items) >= 2 and all(item["status"] == "pass" for item in items)
                else "failed",
                "evidence_type": "real_external",
            }
            for symbol, items in by_symbol.items()
        }
        resubscription = {
            "status": (
                "pass"
                if source.source_id == "binance_spot_public_market_data"
                or all(item["subscription_acknowledged"] for item in results)
                else "failed"
            ),
            "not_applicable": source.source_id == "binance_spot_public_market_data",
            "subscription_acknowledgements": sum(
                bool(item["subscription_acknowledged"]) for item in results
            ),
        }
        freshness = {
            "state": "pass"
            if results and all(item["freshness_state"] == "pass" for item in results)
            else "failed_closed"
            if any(item["freshness_state"] == "failed_closed" for item in results)
            else "unmeasured",
            "clock_offset_seconds": clock_offset_seconds,
            "raw_future_event_count": sum(item["future_provider_event_count"] for item in results),
            "adjusted_future_event_count": sum(
                item["adjusted_future_provider_event_count"] for item in results
            ),
        }
        return {
            "state": "pass"
            if results and all(item["status"] == "pass" for item in results)
            else "failed",
            "connections": results,
            "reconnect": reconnect,
            "resubscription": resubscription,
            "freshness": freshness,
        }

    return asyncio.run(collect())


def _select_primary(candidates: Sequence[Mapping[str, object]]) -> str | None:
    for candidate in candidates:
        if candidate.get("role") != "primary_candidate":
            continue
        rest = candidate.get("rest")
        ws = candidate.get("websocket")
        if (
            isinstance(rest, Mapping)
            and rest.get("required_read_state") == "pass"
            and isinstance(ws, Mapping)
            and ws.get("state") == "pass"
            and isinstance(ws.get("reconnect"), Mapping)
            and all(
                isinstance(item, Mapping) and item.get("status") == "pass"
                for item in ws["reconnect"].values()
            )
            and isinstance(ws.get("freshness"), Mapping)
            and ws["freshness"].get("state") == "pass"
        ):
            value = candidate.get("source_id")
            if isinstance(value, str):
                return value
    return None


def _cross_source_comparison(candidates: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Compare contemporaneous public top-of-book observations without substitution."""

    by_id = {
        candidate.get("source_id"): candidate
        for candidate in candidates
        if isinstance(candidate.get("source_id"), str)
    }
    mappings = {
        "BTC": (
            "binance_spot_public_market_data",
            "BTCUSDT",
            "coinbase_exchange_public_market_data",
            "BTC-USD",
        ),
        "ETH": (
            "binance_spot_public_market_data",
            "ETHUSDT",
            "coinbase_exchange_public_market_data",
            "ETH-USD",
        ),
    }
    observations: dict[str, object] = {}
    for asset, (left_id, left_symbol, right_id, right_symbol) in mappings.items():
        left = by_id.get(left_id)
        right = by_id.get(right_id)
        left_book = (
            left.get("rest", {}).get("markets", {}).get(left_symbol, {}).get("order_book", {})
            if isinstance(left, Mapping)
            else {}
        )
        right_book = (
            right.get("rest", {}).get("markets", {}).get(right_symbol, {}).get("order_book", {})
            if isinstance(right, Mapping)
            else {}
        )
        left_bid = left_book.get("top_bid", {}) if isinstance(left_book, Mapping) else {}
        right_bid = right_book.get("top_bid", {}) if isinstance(right_book, Mapping) else {}
        left_ask = left_book.get("top_ask", {}) if isinstance(left_book, Mapping) else {}
        right_ask = right_book.get("top_ask", {}) if isinstance(right_book, Mapping) else {}

        def relative_delta(left_quote: object, right_quote: object) -> float | None:
            if not isinstance(left_quote, Mapping) or not isinstance(right_quote, Mapping):
                return None
            try:
                left_value = Decimal(str(left_quote["price"]))
                right_value = Decimal(str(right_quote["price"]))
                if left_value <= 0 or right_value <= 0:
                    return None
            except (KeyError, InvalidOperation, TypeError, ValueError):
                return None
            return round(float(abs(left_value - right_value) / max(left_value, right_value)), 8)

        observations[asset] = {
            "status": "measured"
            if left_bid.get("price") is not None and right_bid.get("price") is not None
            else "not_measured",
            "comparison_kind": "independent_public_observation",
            "left_source": left_id,
            "left_symbol": left_symbol,
            "right_source": right_id,
            "right_symbol": right_symbol,
            "left_top_bid": left_bid,
            "right_top_bid": right_bid,
            "left_top_ask": left_ask,
            "right_top_ask": right_ask,
            "bid_relative_difference": relative_delta(left_bid, right_bid),
            "ask_relative_difference": relative_delta(left_ask, right_ask),
            "silent_substitution": False,
        }
    return {
        "status": "measured"
        if any(item["status"] == "measured" for item in observations.values())
        else "not_measured",
        "source_count": 2,
        "observations": observations,
    }


def run_evidence(
    evidence_directory: Path,
    *,
    duration_seconds: int = DEFAULT_CONNECTION_SECONDS,
    connection_rounds: int = DEFAULT_CONNECTION_ROUNDS,
) -> dict[str, object]:
    if duration_seconds <= 0 or duration_seconds > MAX_CONNECTION_SECONDS:
        raise ValueError("public market-data duration is outside the bounded limit")
    if connection_rounds <= 0 or connection_rounds > MAX_CONNECTION_ROUNDS:
        raise ValueError("connection_rounds is outside the bounded limit")
    run_directory, run_id = _new_run_directory(evidence_directory)
    candidates: list[dict[str, object]] = []
    for source in reviewed_public_market_data_sources():
        source_directory = run_directory / source.source_id
        source_directory.mkdir(parents=True)
        rest = _run_rest(source, source_directory)
        clock_offset = rest["server_time"].get("clock_offset_seconds")
        websocket = _run_ws(
            source,
            source_directory,
            duration_seconds,
            connection_rounds,
            float(clock_offset) if isinstance(clock_offset, (int, float)) else None,
        )
        candidates.append(
            {
                "source_id": source.source_id,
                "role": source.role,
                "rest_host": source.rest_host,
                "websocket_host": source.ws_host,
                "rest_base_url": source.rest_base_url,
                "websocket_url": source.ws_url,
                "symbols": list(source.symbols),
                "adapter_version": source.adapter_version,
                "credentials_required": source.credentials_required,
                "write_capability": source.write_capability,
                "rest": rest,
                "websocket": websocket,
            }
        )
    selected = _select_primary(candidates)
    execution_host_overlap = sorted(
        {
            host
            for candidate in candidates
            for host in (candidate["rest_host"], candidate["websocket_host"])
            if host in EXECUTION_HOSTS
        }
    )
    payload: dict[str, object] = {
        "schema": SCHEMA,
        "run_id": run_id,
        "measured_at": datetime.now(UTC).isoformat(),
        "runner_code_sha256": _sha256(Path(__file__).read_bytes()),
        "public_market_data_source_code_sha256": _sha256(
            (
                Path(__file__).resolve().parents[1]
                / "src/advisorai/collectors/public_market_data.py"
            ).read_bytes()
        ),
        "execution_venue": EXECUTION_VENUE,
        "execution_hosts": sorted(EXECUTION_HOSTS),
        "candidates": candidates,
        "selection": {
            "selected_primary_source": selected,
            "state": "measured_candidate_selected" if selected else "pending_external_source",
            "selection_order": [
                "binance_spot_public_market_data",
                "coinbase_exchange_public_market_data",
            ],
            "rule": "both BTC/ETH public product truth, REST server time/book/trades, and WSS observations",
        },
        "execution_separation": {
            "public_connectors_load_credentials": False,
            "public_connectors_expose_write_methods": False,
            "public_connectors_call_execution_endpoints": False,
            "execution_host_overlap": execution_host_overlap,
            "execution_transport_must_reject_public_production_hosts": True,
            "market_data_to_execution_chain": (
                "public read source -> normalized data plane -> model/council -> target -> RiskKernel -> OMS -> Binance Spot Testnet"
            ),
        },
        "independence": {
            "selected_source_relationship_to_execution": (
                "same-provider-public-read-route"
                if selected and selected.startswith("binance")
                else "different-provider-public-read-route"
            ),
            "independent_council_context_sources": [
                candidate["source_id"]
                for candidate in candidates
                if candidate["source_id"] != selected
                and candidate["rest"]["required_read_state"] == "pass"
            ],
            "silent_substitution": False,
            "cross_source_comparison": _cross_source_comparison(candidates),
        },
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "status": "passed_candidate_selection" if selected else "partial_pending_external",
        "phase3_admission_opened": False,
    }
    report = run_directory / "phase3-public-market-data-qualification.json"
    _write_immutable_json(report, payload)
    digest = _sha256(report.read_bytes())
    _write_immutable_json(
        run_directory / "evidence-manifest.json",
        {
            "schema": f"{SCHEMA}.manifest",
            "run_id": run_id,
            "report": str(report),
            "evidence_sha256": digest,
        },
    )
    _write_latest_pointer(
        evidence_directory / "latest.json",
        {
            "schema": f"{SCHEMA}.latest",
            "run_id": run_id,
            "evidence_sha256": digest,
            "status": payload["status"],
        },
    )
    return {
        "status": payload["status"],
        "evidence": str(report),
        "evidence_sha256": digest,
        "selected_primary_source": selected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path("artifacts/phase3/public-market-data-qualification"),
    )
    parser.add_argument("--duration-seconds", type=int, default=DEFAULT_CONNECTION_SECONDS)
    parser.add_argument("--connection-rounds", type=int, default=DEFAULT_CONNECTION_ROUNDS)
    parser.add_argument(
        "--real",
        action="store_true",
        help="allow bounded public market-data REST/WSS reads",
    )
    args = parser.parse_args()
    if not args.real:
        parser.error("public network reads require explicit --real")
    print(
        json.dumps(
            run_evidence(
                args.evidence_dir,
                duration_seconds=args.duration_seconds,
                connection_rounds=args.connection_rounds,
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
