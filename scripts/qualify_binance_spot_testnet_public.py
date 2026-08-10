"""Bounded public Binance Spot Testnet truth qualification.

This runner makes no authenticated requests and never submits an order.  It
records only server/product metadata needed to establish that the reviewed
testnet currently exposes the required BTC and ETH symbols.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from pydantic import SecretStr

from advisorai.integrations import (
    BINANCE_SPOT_TESTNET_ADAPTER_VERSION,
    BINANCE_SPOT_TESTNET_BASE_URL,
    BINANCE_SPOT_TESTNET_HOST,
    BinanceSpotSigner,
    BinanceSpotTestnetTransport,
)
from advisorai.integrations.http import HttpClientConfig, SafeHttpClient


def _write_evidence(payload: dict[str, object], evidence_dir: Path) -> tuple[Path, str]:
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
        "schema": "advisorai.phase2.binance-spot-testnet.public-truth.v1",
        "run_id": run_id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "result": payload,
    }
    encoded = (json.dumps(record, sort_keys=True, indent=2) + "\n").encode()
    path = run_dir / "binance-spot-testnet-public-truth.json"
    path.write_bytes(encoded)
    digest = sha256(encoded).hexdigest()
    (evidence_dir / "latest.json").write_text(
        json.dumps(
            {
                "schema": "advisorai.phase2.binance-spot-testnet.public-truth.latest.v1",
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
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path("artifacts/phase2/binance-spot-testnet/public-truth"),
    )
    args = parser.parse_args()
    client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=(BINANCE_SPOT_TESTNET_HOST,),
            user_agent=f"advisorai-v3/{BINANCE_SPOT_TESTNET_ADAPTER_VERSION}",
            max_retries=1,
            requests_per_second=2,
        ),
        base_url=BINANCE_SPOT_TESTNET_BASE_URL,
    )
    payload: dict[str, object] = {
        "status": "failed",
        "venue": "binance_spot_testnet",
        "environment": "paper_testnet",
        "endpoint": BINANCE_SPOT_TESTNET_BASE_URL,
        "reviewed_host": BINANCE_SPOT_TESTNET_HOST,
        "adapter_version": BINANCE_SPOT_TESTNET_ADAPTER_VERSION,
        "adapter_source_sha256": sha256(
            Path("src/advisorai/integrations/binance_spot.py").read_bytes()
        ).hexdigest(),
        "operations": [],
        "network_calls": 0,
    }
    started = time.perf_counter()
    try:
        server = client.request(
            "GET",
            f"{BINANCE_SPOT_TESTNET_BASE_URL}/api/v3/time",
            acceptable_statuses=frozenset({200}),
        )
        server_payload = json.loads(server.body)
        if not isinstance(server_payload, dict) or not isinstance(
            server_payload.get("serverTime"), int
        ):
            raise ValueError("server time schema failed")
        payload["operations"].append(
            {"name": "server_time", "status": "ok", "schema_fields": ("serverTime",)}
        )
        exchange = client.request(
            "GET",
            f"{BINANCE_SPOT_TESTNET_BASE_URL}/api/v3/exchangeInfo",
            acceptable_statuses=frozenset({200}),
        )
        exchange_payload = json.loads(exchange.body)
        if not isinstance(exchange_payload, dict):
            raise ValueError("exchange info schema failed")
        symbols = exchange_payload.get("symbols")
        if not isinstance(symbols, list):
            raise ValueError("exchange info symbols schema failed")
        transport = BinanceSpotTestnetTransport(
            client,
            # Public operations never use this signer; it is present only to
            # reuse the provider-truth parser without creating credentials.
            BinanceSpotSigner(
                api_key="public-fixture-key",
                api_secret=SecretStr("public-fixture-secret"),
            ),
        )
        mappings = transport.verify_symbol_mappings(symbols)
        required = {
            spec.symbol: {
                "base_asset": spec.base_asset,
                "quote_asset": spec.quote_asset,
                "status": spec.status,
                "base_increment": str(spec.base_increment),
                "quote_increment": str(spec.quote_increment),
            }
            for spec in mappings
        }
        payload["operations"].append(
            {
                "name": "exchange_info",
                "status": "ok",
                "schema_fields": ("symbols",),
                "symbol_count": len(symbols),
                "required_symbols": required,
            }
        )
        payload.update(
            {
                "status": "passed",
                "reason": "public_btc_eth_symbol_truth_passed",
                "required_symbols": tuple(sorted(required)),
            }
        )
    except Exception as exc:
        payload.update({"reason": type(exc).__name__, "error_class": type(exc).__name__})
    payload["network_calls"] = client.request_count
    payload["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
    path, digest = _write_evidence(payload, args.evidence_dir)
    print(
        json.dumps(
            {"status": payload["status"], "evidence": str(path), "evidence_sha256": digest},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
