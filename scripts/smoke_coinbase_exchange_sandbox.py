"""Opt-in, real read-only smoke for the Coinbase Exchange Sandbox.

The command is deliberately separate from the generic venue smoke.  It loads
only the ``PAPER_VENUE`` credential scope, verifies Coinbase product truth, and
never submits, cancels, transfers, or withdraws.  Persisted output contains
only redacted connector metadata and aggregate/schema information.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from advisorai.config import CredentialResolver, CredentialScope, SecretSettings
from advisorai.integrations import (
    COINBASE_EXCHANGE_SANDBOX_ADAPTER_VERSION,
    COINBASE_EXCHANGE_SANDBOX_BASE_URL,
    COINBASE_EXCHANGE_SANDBOX_HOST,
    ConnectorCard,
    ConnectorState,
    build_coinbase_exchange_sandbox_transport,
)


def _scoped_configuration_hash(resolver: CredentialResolver) -> str:
    scoped = resolver.resolve(CredentialScope.PAPER_VENUE)
    settings = SecretSettings.from_mapping(scoped)
    card = ConnectorCard(
        name="transition-config",
        owner="operator",
        purpose="paper/testnet real API transition",
        endpoint=settings.venue_base_url or "https://unset.invalid",
        allowed_hosts=(COINBASE_EXCHANGE_SANDBOX_HOST,),
        environment=settings.venue_environment,
        credential_refs=tuple(
            name
            for name in (
                "ADVISORAI_VENUE_API_KEY",
                "ADVISORAI_VENUE_API_SECRET",
                "ADVISORAI_VENUE_PASSPHRASE",
            )
            if name in scoped
        ),
        source_grade="execution_grade",
        quota_and_cost="operator review required",
        adapter_version="transition-v1",
        rollback_procedure="revoke connector and return to deterministic paper fixture",
        state=ConnectorState.CONFIGURED,
    )
    return card.canonical_hash()


def _schema_fields(value: object) -> tuple[str, ...] | None:
    if isinstance(value, dict):
        return tuple(sorted(str(key) for key in value))
    if isinstance(value, (tuple, list)) and value and all(isinstance(item, dict) for item in value):
        return tuple(sorted({str(key) for item in value for key in item}))
    return None


def _record_operation(
    name: str,
    endpoint: str,
    operation: object,
    *,
    summarize: Any | None = None,
) -> tuple[dict[str, object], object | None]:
    started = time.perf_counter()
    try:
        value = operation()
    except Exception as exc:
        record: dict[str, object] = {
            "name": name,
            "endpoint": endpoint,
            "status": "failed",
            "error_class": type(exc).__name__,
            "status_code": getattr(exc, "status_code", None),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        return record, None
    record = {
        "name": name,
        "endpoint": endpoint,
        "status": "ok",
        "response_type": type(value).__name__,
        "record_count": len(value) if isinstance(value, (tuple, list, dict)) else None,
        "schema_fields": _schema_fields(value),
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    if summarize is not None:
        record.update(summarize(value))
    return record, value


def _write_evidence(payload: dict[str, object], evidence_dir: Path) -> dict[str, object]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    run_base = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_id = run_base
    suffix = 1
    while (evidence_dir / run_id).exists():
        suffix += 1
        run_id = f"{run_base}-{suffix}"
    run_dir = evidence_dir / run_id
    run_dir.mkdir()
    record = {
        "schema": "advisorai.phase2.coinbase-exchange-sandbox.read-only-smoke.v1",
        "run_id": run_id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "result": payload,
    }
    encoded = (json.dumps(record, sort_keys=True, indent=2) + "\n").encode()
    manifest = run_dir / "coinbase-read-only-smoke.json"
    manifest.write_bytes(encoded)
    digest = sha256(encoded).hexdigest()
    pointer = {
        "schema": "advisorai.phase2.coinbase-exchange-sandbox.read-only-smoke.latest.v1",
        "run_id": run_id,
        "manifest_sha256": digest,
    }
    (evidence_dir / "latest.json").write_text(
        json.dumps(pointer, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return {**payload, "evidence_run_id": run_id, "evidence_sha256": digest}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--secrets", type=Path, default=Path(os.getenv("ADVISORAI_SECRETS_FILE", "secrets.env"))
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path("artifacts/phase2/coinbase-exchange-sandbox/read-only-smoke"),
    )
    parser.add_argument(
        "--configuration-hash",
        required=True,
        help="hash emitted by the prior zero-network configuration check; value is not a secret",
    )
    args = parser.parse_args()
    if os.getenv("ADVISORAI_RUN_NETWORK_SMOKE") != "1":
        raise SystemExit("refusing network access; set ADVISORAI_RUN_NETWORK_SMOKE=1 explicitly")

    resolver = CredentialResolver.from_env_file(args.secrets)
    if len(args.configuration_hash) != 64 or any(
        character not in "0123456789abcdef" for character in args.configuration_hash
    ):
        raise SystemExit("--configuration-hash must be a lowercase SHA-256 digest")
    scoped_names = resolver.available_names(CredentialScope.PAPER_VENUE)
    base = {
        "status": "failed",
        "venue": "coinbase_exchange_sandbox",
        "environment": "paper_testnet",
        "endpoint": COINBASE_EXCHANGE_SANDBOX_BASE_URL,
        "reviewed_host": COINBASE_EXCHANGE_SANDBOX_HOST,
        "adapter": COINBASE_EXCHANGE_SANDBOX_ADAPTER_VERSION,
        "adapter_source_sha256": sha256(
            Path("src/advisorai/integrations/coinbase_exchange.py").read_bytes()
        ).hexdigest(),
        "credential_refs": tuple(
            name
            for name in scoped_names
            if name
            in {
                "ADVISORAI_VENUE_API_KEY",
                "ADVISORAI_VENUE_API_SECRET",
                "ADVISORAI_VENUE_PASSPHRASE",
            }
        ),
        "config_hash": args.configuration_hash,
        "scoped_venue_config_hash": _scoped_configuration_hash(resolver),
        "network_calls": 0,
        "operations": [],
    }

    try:
        transport = build_coinbase_exchange_sandbox_transport(resolver)
    except Exception as exc:
        base.update({"reason": "adapter_construction_failed", "error_class": type(exc).__name__})
        result = _write_evidence(base, args.evidence_dir)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 1

    operations: list[dict[str, object]] = []
    server_record, server_time = _record_operation("server_time", "/time", transport.server_time)
    operations.append(server_record)
    if server_time is None:
        base.update({"reason": "server_time_failed", "operations": operations})
        base["network_calls"] = transport.client.request_count
        result = _write_evidence(base, args.evidence_dir)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 1

    product_record, products = _record_operation(
        "products",
        "/products",
        transport.list_products,
        summarize=lambda value: {
            "returned_product_ids": tuple(
                sorted(
                    str(item.get("id", "")).upper()
                    for item in value
                    if isinstance(item, dict) and str(item.get("id", "")).strip()
                )
            ),
            "required_product_ids": tuple(
                sorted(
                    str(item.get("id", "")).upper()
                    for item in value
                    if isinstance(item, dict)
                    and str(item.get("id", "")).upper() in {"BTC-USD", "ETH-USD"}
                )
            ),
        },
    )
    operations.append(product_record)
    if products is None:
        base.update({"reason": "product_catalogue_failed", "operations": operations})
        base["network_calls"] = transport.client.request_count
        result = _write_evidence(base, args.evidence_dir)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 1

    mapping_record, mappings = _record_operation(
        "product_mapping_verification",
        "/products",
        lambda: transport.verify_product_mappings(products),
        summarize=lambda value: {"admitted_product_ids": tuple(item.product_id for item in value)},
    )
    operations.append(mapping_record)
    fill_product_ids = tuple(
        product_id
        for product_id in ("BTC-USD", "ETH-USD")
        if product_id in transport.catalogue_product_ids
    )
    fill_endpoint = (
        "/fills?product_id=" + ",".join(fill_product_ids)
        if fill_product_ids
        else "/fills?product_id=<required-product-unavailable>"
    )
    checks = (
        ("account_state", "/accounts", transport.account_state),
        ("balances", "/accounts", transport.list_balances),
        ("positions", "/accounts", transport.list_positions),
        ("open_orders", "/orders", transport.list_open_orders),
        (
            "fills",
            fill_endpoint,
            lambda: (
                transport.list_fills()
                if transport.verified_product_ids
                else tuple(
                    fill
                    for product_id in ("BTC-USD", "ETH-USD")
                    if product_id in transport.catalogue_product_ids
                    for fill in transport.list_fills(product_id=product_id)
                )
            ),
        ),
    )
    for name, endpoint, operation in checks:
        record, _value = _record_operation(name, endpoint, operation)
        operations.append(record)
        if record["status"] != "ok":
            break

    passed = (
        mappings is not None
        and len(operations) == len(checks) + 2
        and all(record["status"] == "ok" for record in operations)
    )
    base.update(
        {
            "status": "passed" if passed else "failed",
            "reason": (
                "read_only_smoke_passed"
                if passed
                else (
                    "required_symbol_mapping_failed"
                    if mapping_record["status"] != "ok"
                    else "read_only_operation_failed"
                )
            ),
            "operations": operations,
            "network_calls": transport.client.request_count,
        }
    )
    result = _write_evidence(base, args.evidence_dir)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
