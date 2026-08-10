"""Credential-free comparison of reviewed BTC/ETH paper venue candidates.

The runner uses only public, non-production market-data endpoints.  It never
resolves credentials and never calls an order, cancel, transfer, or withdrawal
endpoint.  The output is deliberately limited to provider schemas, filters,
market truth, and documented private-operation capabilities.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from advisorai.integrations.http import HttpClientConfig, SafeHttpClient

REQUIRED_SYMBOLS = ("BTCUSDT", "ETHUSDT")


@dataclass(frozen=True, slots=True)
class VenueCandidate:
    name: str
    rest_base_url: str
    reviewed_rest_host: str
    websocket_url: str
    public_paths: Mapping[str, str]
    official_documentation: tuple[str, ...]
    private_capabilities: Mapping[str, object]
    rejected_production_hosts: tuple[str, ...]


CANDIDATES = (
    VenueCandidate(
        name="binance_spot_testnet",
        rest_base_url="https://testnet.binance.vision",
        reviewed_rest_host="testnet.binance.vision",
        websocket_url="wss://stream.testnet.binance.vision/ws",
        public_paths={
            "server_time": "/api/v3/time",
            "products": "/api/v3/exchangeInfo",
            "orderbook": "/api/v3/depth?symbol={symbol}&limit=5",
            "trades": "/api/v3/trades?symbol={symbol}&limit=5",
        },
        official_documentation=(
            "https://github.com/binance/binance-spot-api-docs/tree/master/testnet",
            "https://developers.binance.com/en/docs/products/spot/rest-api",
            "https://developers.binance.com/zh-CN/docs/products/spot/testnet/web-socket-streams",
        ),
        private_capabilities={
            "account": "/api/v3/account",
            "balances": "/api/v3/account",
            "open_orders": "/api/v3/openOrders",
            "order_history": "/api/v3/allOrders",
            "fills": "/api/v3/myTrades",
            "client_order_identity": "newClientOrderId",
            "fake_funds": True,
            "private_api_requires_credentials": True,
        },
        rejected_production_hosts=(
            "api.binance.com",
            "api-gcp.binance.com",
            "api1.binance.com",
            "api2.binance.com",
            "api3.binance.com",
            "api4.binance.com",
            "stream.binance.com",
        ),
    ),
    VenueCandidate(
        name="bybit_spot_testnet",
        rest_base_url="https://api-testnet.bybit.com",
        reviewed_rest_host="api-testnet.bybit.com",
        websocket_url="wss://stream-testnet.bybit.com/v5/public/spot",
        public_paths={
            "server_time": "/v5/market/time",
            "product": "/v5/market/instruments-info?category=spot&symbol={symbol}",
            "orderbook": "/v5/market/orderbook?category=spot&symbol={symbol}&limit=5",
            "trades": "/v5/market/recent-trade?category=spot&symbol={symbol}&limit=5",
        },
        official_documentation=(
            "https://bybit-exchange.github.io/docs/v5/guide",
            "https://bybit-exchange.github.io/docs/v5/market/instrument",
            "https://bybit-exchange.github.io/docs/v5/market/orderbook",
            "https://bybit-exchange.github.io/docs/v5/market/recent-trade",
            "https://bybit-exchange.github.io/docs/v5/ws/connect",
            "https://bybit-exchange.github.io/docs/v5/order/create-order",
            "https://bybit-exchange.github.io/docs/v5/account/wallet-balance",
            "https://bybit-exchange.github.io/docs/v5/order/open-order",
            "https://bybit-exchange.github.io/docs/v5/order/execution",
        ),
        private_capabilities={
            "account": "/v5/account/wallet-balance",
            "balances": "/v5/account/wallet-balance",
            "open_orders": "/v5/order/realtime",
            "order_history": "/v5/order/history",
            "fills": "/v5/execution/list",
            "client_order_identity": "orderLinkId",
            "fake_funds": True,
            "private_api_requires_credentials": True,
        },
        rejected_production_hosts=("api.bybit.com", "stream.bybit.com"),
    ),
)


def _json(response: object) -> object:
    body = getattr(response, "body", None)
    if not isinstance(body, bytes):
        raise ValueError("response body is not bytes")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("response body is not JSON") from exc


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} response is not an object")
    return value


def _binance_products(payload: object) -> dict[str, object]:
    record = _mapping(payload, "Binance exchange info")
    symbols = record.get("symbols")
    if not isinstance(symbols, list) or not all(isinstance(item, Mapping) for item in symbols):
        raise ValueError("Binance exchange info symbols are malformed")
    by_symbol = {str(item.get("symbol", "")).upper(): item for item in symbols}
    required: dict[str, object] = {}
    for symbol in REQUIRED_SYMBOLS:
        item = by_symbol.get(symbol)
        if item is None:
            raise ValueError(f"Binance product missing {symbol}")
        filters = item.get("filters")
        if not isinstance(filters, list) or not all(
            isinstance(entry, Mapping) for entry in filters
        ):
            raise ValueError(f"Binance filters missing for {symbol}")
        by_type = {str(entry.get("filterType")): entry for entry in filters}
        price = by_type.get("PRICE_FILTER")
        lot = by_type.get("LOT_SIZE")
        notional = by_type.get("NOTIONAL") or by_type.get("MIN_NOTIONAL")
        if not isinstance(price, Mapping) or not isinstance(lot, Mapping):
            raise ValueError(f"Binance order filters missing for {symbol}")
        required[symbol] = {
            "base_asset": item.get("baseAsset"),
            "quote_asset": item.get("quoteAsset"),
            "status": item.get("status"),
            "tick_size": price.get("tickSize"),
            "step_size": lot.get("stepSize"),
            "min_quantity": lot.get("minQty"),
            "min_notional": notional.get("minNotional") if isinstance(notional, Mapping) else None,
        }
        if item.get("status") != "TRADING":
            raise ValueError(f"Binance product {symbol} is not TRADING")
    return {"symbol_count": len(symbols), "required_symbols": required}


def _bybit_product(payload: object) -> dict[str, object]:
    record = _mapping(payload, "Bybit instrument info")
    if record.get("retCode") != 0:
        raise ValueError("Bybit instrument request did not return retCode 0")
    result = _mapping(record.get("result"), "Bybit instrument result")
    entries = result.get("list")
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], Mapping):
        raise ValueError("Bybit instrument result does not contain one symbol")
    item = entries[0]
    price_filter = _mapping(item.get("priceFilter"), "Bybit price filter")
    lot_filter = _mapping(item.get("lotSizeFilter"), "Bybit lot-size filter")
    symbol = str(item.get("symbol", "")).upper()
    if symbol not in REQUIRED_SYMBOLS or item.get("status") != "Trading":
        raise ValueError(f"Bybit product {symbol or '<missing>'} is not Trading")
    return {
        "symbol": symbol,
        "base_asset": item.get("baseCoin"),
        "quote_asset": item.get("quoteCoin"),
        "product_status": item.get("status"),
        "tick_size": price_filter.get("tickSize"),
        "step_size": lot_filter.get("basePrecision"),
        "min_quantity": lot_filter.get("minOrderQty"),
        "min_notional": lot_filter.get("minOrderAmt"),
    }


def _binance_server_time(payload: object) -> dict[str, object]:
    record = _mapping(payload, "Binance server time")
    if not isinstance(record.get("serverTime"), int):
        raise ValueError("Binance server time is missing")
    return {"schema_fields": tuple(sorted(record)), "server_time_present": True}


def _bybit_server_time(payload: object) -> dict[str, object]:
    record = _mapping(payload, "Bybit server time")
    if record.get("retCode") != 0:
        raise ValueError("Bybit server time did not return retCode 0")
    result = _mapping(record.get("result"), "Bybit server time result")
    if not result.get("timeSecond") or not result.get("timeNano"):
        raise ValueError("Bybit server time is missing")
    return {
        "schema_fields": tuple(sorted(record)),
        "result_fields": tuple(sorted(result)),
        "server_time_present": True,
    }


def _binance_depth(payload: object) -> dict[str, object]:
    record = _mapping(payload, "Binance order book")
    bids, asks = record.get("bids"), record.get("asks")
    if not isinstance(bids, list) or not isinstance(asks, list) or not bids or not asks:
        raise ValueError("Binance order book has no bid/ask levels")
    return {
        "schema_fields": tuple(sorted(record)),
        "bid_levels": len(bids),
        "ask_levels": len(asks),
        "update_id_present": isinstance(record.get("lastUpdateId"), int),
    }


def _bybit_depth(payload: object) -> dict[str, object]:
    record = _mapping(payload, "Bybit order book")
    if record.get("retCode") != 0:
        raise ValueError("Bybit order book did not return retCode 0")
    result = _mapping(record.get("result"), "Bybit order book result")
    bids, asks = result.get("b"), result.get("a")
    if not isinstance(bids, list) or not isinstance(asks, list) or not bids or not asks:
        raise ValueError("Bybit order book has no bid/ask levels")
    return {
        "schema_fields": tuple(sorted(record)),
        "result_fields": tuple(sorted(result)),
        "bid_levels": len(bids),
        "ask_levels": len(asks),
        "update_id_present": isinstance(result.get("u"), int),
    }


def _binance_trades(payload: object) -> dict[str, object]:
    if (
        not isinstance(payload, list)
        or not payload
        or not all(isinstance(item, Mapping) for item in payload)
    ):
        raise ValueError("Binance public trades are empty or malformed")
    return {"trade_count": len(payload), "schema_fields": tuple(sorted(payload[0]))}


def _bybit_trades(payload: object) -> dict[str, object]:
    record = _mapping(payload, "Bybit public trades")
    if record.get("retCode") != 0:
        raise ValueError("Bybit public trades did not return retCode 0")
    result = _mapping(record.get("result"), "Bybit public trades result")
    entries = result.get("list")
    if (
        not isinstance(entries, list)
        or not entries
        or not all(isinstance(item, Mapping) for item in entries)
    ):
        raise ValueError("Bybit public trades are empty or malformed")
    return {
        "trade_count": len(entries),
        "schema_fields": tuple(sorted(entries[0])),
        "result_fields": tuple(sorted(result)),
    }


def _operation(
    client: SafeHttpClient,
    candidate: VenueCandidate,
    name: str,
    path: str,
    parser: Callable[[object], dict[str, object]],
) -> tuple[dict[str, object], dict[str, object] | None]:
    started = time.perf_counter()
    endpoint = f"{candidate.rest_base_url}{path}"
    try:
        response = client.request("GET", endpoint, acceptable_statuses=frozenset({200}))
        summary = parser(_json(response))
    except Exception as exc:
        return (
            {
                "name": name,
                "path": path,
                "status": "failed",
                "error_class": type(exc).__name__,
                "status_code": getattr(exc, "status_code", None),
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            },
            None,
        )
    return (
        {
            "name": name,
            "path": path,
            "response_status": getattr(response, "status_code", None),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            **summary,
            "status": "ok",
        },
        summary,
    )


def qualify_candidate(candidate: VenueCandidate) -> dict[str, object]:
    client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=(candidate.reviewed_rest_host,),
            user_agent="advisorai-v3/paper-venue-bakeoff-v1",
            max_retries=1,
            requests_per_second=2,
        ),
        base_url=candidate.rest_base_url,
    )
    operations: list[dict[str, object]] = []
    if candidate.name == "binance_spot_testnet":
        server_parser = _binance_server_time
        product_path = candidate.public_paths["products"]
        product_parser = _binance_products
        depth_parser = _binance_depth
        trades_parser = _binance_trades
    else:
        server_parser = _bybit_server_time
        product_path = ""
        product_parser = _bybit_product
        depth_parser = _bybit_depth
        trades_parser = _bybit_trades

    server_record, _ = _operation(
        client, candidate, "server_time", candidate.public_paths["server_time"], server_parser
    )
    operations.append(server_record)
    required: dict[str, object] = {}
    if candidate.name == "binance_spot_testnet":
        product_record, product_summary = _operation(
            client, candidate, "products", product_path, product_parser
        )
        operations.append(product_record)
        if product_summary is not None:
            required = dict(product_summary.get("required_symbols", {}))
    else:
        for symbol in REQUIRED_SYMBOLS:
            product_record, product_summary = _operation(
                client,
                candidate,
                f"product_{symbol.lower()}",
                candidate.public_paths["product"].format(symbol=symbol),
                product_parser,
            )
            operations.append(product_record)
            if product_summary is not None:
                required[symbol] = product_summary

    for symbol in REQUIRED_SYMBOLS:
        for kind, parser in (("orderbook", depth_parser), ("public_trades", trades_parser)):
            record, _ = _operation(
                client,
                candidate,
                f"{kind}_{symbol.lower()}",
                candidate.public_paths["trades" if kind == "public_trades" else kind].format(
                    symbol=symbol
                ),
                parser,
            )
            operations.append(record)

    required_ok = set(required) == set(REQUIRED_SYMBOLS)
    public_ops_ok = all(record["status"] == "ok" for record in operations)
    public_pass = required_ok and public_ops_ok
    return {
        "venue": candidate.name,
        "environment": "paper_testnet",
        "rest_endpoint": candidate.rest_base_url,
        "reviewed_rest_host": candidate.reviewed_rest_host,
        "websocket_endpoint": candidate.websocket_url,
        "official_documentation": candidate.official_documentation,
        "rejected_production_hosts": candidate.rejected_production_hosts,
        "credential_free": True,
        "writes_attempted": False,
        "required_symbols": required,
        "private_capabilities_documented": candidate.private_capabilities,
        "operations": operations,
        "network_calls": client.request_count,
        "public_status": "passed" if public_pass else "failed",
        "public_reason": (
            "btc_eth_product_truth_and_public_market_data_passed"
            if public_pass
            else "one_or_more_public_truth_checks_failed"
        ),
    }


def select_candidate(results: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    binance = results.get("binance_spot_testnet", {})
    bybit = results.get("bybit_spot_testnet", {})
    if binance.get("public_status") == "passed":
        return {
            "status": "selected_for_authenticated_qualification",
            "venue": "binance_spot_testnet",
            "reason": "preferred_first_candidate_passed_credential_free_public_truth",
            "alternative": "bybit_spot_testnet",
        }
    if bybit.get("public_status") == "passed":
        return {
            "status": "selected_for_authenticated_qualification",
            "venue": "bybit_spot_testnet",
            "reason": "binance_public_truth_failed_and_bybit_public_truth_passed",
            "alternative": "binance_spot_testnet",
        }
    return {
        "status": "blocked",
        "venue": None,
        "reason": "both_credential_free_public_truth_checks_failed",
    }


def write_evidence(payload: Mapping[str, object], evidence_dir: Path) -> tuple[Path, str]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    base = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_id = base
    suffix = 1
    while (evidence_dir / run_id).exists():
        suffix += 1
        run_id = f"{base}-{suffix}"
    run_dir = evidence_dir / run_id
    run_dir.mkdir()
    record = {
        "schema": "advisorai.phase2.paper-venue-candidate-bakeoff.v1",
        "run_id": run_id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "result": payload,
    }
    encoded = (json.dumps(record, sort_keys=True, indent=2) + "\n").encode()
    path = run_dir / "paper-venue-candidate-bakeoff.json"
    path.write_bytes(encoded)
    digest = sha256(encoded).hexdigest()
    (evidence_dir / "latest.json").write_text(
        json.dumps(
            {
                "schema": "advisorai.phase2.paper-venue-candidate-bakeoff.latest.v1",
                "run_id": run_id,
                "manifest_sha256": digest,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path, digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", help="allow bounded public testnet requests")
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path("artifacts/phase2/paper-venue-bakeoff"),
    )
    args = parser.parse_args()
    if not args.real:
        raise SystemExit("refusing network access; pass --real explicitly")
    started = time.perf_counter()
    results = {candidate.name: qualify_candidate(candidate) for candidate in CANDIDATES}
    selection = select_candidate(results)
    payload = {
        "status": "passed"
        if selection["status"] == "selected_for_authenticated_qualification"
        else "blocked",
        "qualification_scope": "credential_free_public_only",
        "credentials_resolved": False,
        "writes_attempted": False,
        "selection": selection,
        "candidates": results,
        "runner_source_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    path, digest = write_evidence(payload, args.evidence_dir)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "selection": selection,
                "evidence": str(path),
                "evidence_sha256": digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
