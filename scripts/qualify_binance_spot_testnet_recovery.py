#!/usr/bin/env python3
"""Measure read-only Binance provider restart and configuration rollback.

This is a deliberately narrow Phase-1 operational check.  It uses the
existing content-addressed configuration store and the provider-specific
Binance Spot Testnet transport, performs only the authenticated read contract,
and starts a fresh child process to prove that the active bundle and read-only
provider projection survive a process boundary.  It never submits, cancels,
transfers, or withdraws an order.

The report is intentionally aggregate-only: response bodies, account values,
account identifiers, headers, signatures, and credential values are never
persisted or printed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from advisorai.config import ConfigBundleStore, CredentialResolver, CredentialScope, SecretSettings
from advisorai.integrations import (
    BINANCE_SPOT_TESTNET_ADAPTER_VERSION,
    BINANCE_SPOT_TESTNET_BASE_URL,
    BINANCE_SPOT_TESTNET_HOST,
    BinanceSpotTestnetTransport,
    build_binance_spot_testnet_transport,
)

SCHEMA = "advisorai.phase1.binance-spot-testnet.read-only-recovery.v1"
CHILD_SCHEMA = f"{SCHEMA}.child"
_CREDENTIAL_REFS = (
    "ADVISORAI_VENUE_API_KEY",
    "ADVISORAI_VENUE_API_SECRET",
)
_READ_ONLY_OPERATIONS = (
    "server_time",
    "products",
    "product_mapping_verification",
    "account_state",
    "balances",
    "positions",
    "open_orders",
    "fills",
)


def _sha256_bytes(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _write_immutable_json(path: Path, payload: object) -> Path:
    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise FileExistsError(f"immutable evidence differs: {path}")
        return path
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return path


def _schema_fields(value: object) -> tuple[str, ...] | None:
    if isinstance(value, Mapping):
        return tuple(sorted(str(key) for key in value))
    if (
        isinstance(value, (tuple, list))
        and value
        and all(isinstance(item, Mapping) for item in value)
    ):
        return tuple(sorted({str(key) for item in value for key in item}))
    return None


def _record_operation(
    name: str,
    endpoint: str,
    operation: Callable[[], object],
    *,
    summarize: Callable[[object], Mapping[str, object]] | None = None,
) -> tuple[dict[str, object], object | None]:
    started = time.perf_counter()
    try:
        value = operation()
    except Exception as exc:  # evidence must preserve class, never response text
        return (
            {
                "name": name,
                "endpoint": endpoint,
                "status": "failed",
                "error_class": type(exc).__name__,
                "status_code": getattr(exc, "status_code", None),
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            },
            None,
        )
    record: dict[str, object] = {
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


def _read_only_probe(transport: BinanceSpotTestnetTransport) -> dict[str, object]:
    """Run the existing Binance read contract without exposing response values."""

    operations: list[dict[str, object]] = []
    server_record, server_time = _record_operation(
        "server_time", "/api/v3/time", transport.server_time
    )
    operations.append(server_record)
    if server_time is None:
        return {
            "status": "failed",
            "reason": "server_time_failed",
            "operations": operations,
            "network_calls": transport.client.request_count,
            "writes_attempted": False,
        }

    product_record, products = _record_operation(
        "products",
        "/api/v3/exchangeInfo",
        transport.list_products,
        summarize=lambda value: {
            "symbol_count": len(value),
            "required_symbols": tuple(
                sorted(
                    str(item.get("symbol", "")).upper()
                    for item in value
                    if isinstance(item, Mapping)
                    and str(item.get("symbol", "")).upper() in {"BTCUSDT", "ETHUSDT"}
                )
            ),
        },
    )
    operations.append(product_record)
    if products is None:
        return {
            "status": "failed",
            "reason": "product_catalogue_failed",
            "operations": operations,
            "network_calls": transport.client.request_count,
            "writes_attempted": False,
        }

    mapping_record, mappings = _record_operation(
        "product_mapping_verification",
        "/api/v3/exchangeInfo",
        lambda: transport.verify_symbol_mappings(products),
        summarize=lambda value: {
            "admitted_symbols": tuple(item.symbol for item in value),
            "admitted_pairs": tuple((item.base_asset, item.quote_asset) for item in value),
        },
    )
    operations.append(mapping_record)
    if mappings is None:
        return {
            "status": "failed",
            "reason": "required_symbol_mapping_failed",
            "operations": operations,
            "network_calls": transport.client.request_count,
            "writes_attempted": False,
        }

    checks: tuple[tuple[str, str, Callable[[], object]], ...] = (
        ("account_state", "/api/v3/account", transport.account_state),
        ("balances", "/api/v3/account", transport.list_balances),
        ("positions", "/api/v3/account", transport.list_positions),
        ("open_orders", "/api/v3/openOrders", transport.list_open_orders),
        ("fills", "/api/v3/myTrades", transport.list_fills),
    )
    for name, endpoint, operation in checks:
        record, _value = _record_operation(name, endpoint, operation)
        operations.append(record)
        if record["status"] != "ok":
            break

    passed = len(operations) == len(_READ_ONLY_OPERATIONS) and all(
        record["status"] == "ok" for record in operations
    )
    return {
        "status": "passed" if passed else "failed",
        "reason": "read_only_contract_passed" if passed else "read_only_operation_failed",
        "operations": operations,
        "network_calls": transport.client.request_count,
        "writes_attempted": False,
    }


def _configuration_bundle_content(
    settings: SecretSettings,
    credential_refs: tuple[str, ...],
    configuration_hash: str,
    adapter_source_sha256: str,
    revision: str,
) -> dict[str, object]:
    return {
        "schema": "advisorai.phase1.provider-config-bundle.v1",
        "venue_name": settings.venue_name,
        "venue_environment": settings.venue_environment,
        "venue_base_url": settings.venue_base_url,
        "venue_ws_url": settings.venue_ws_url,
        "reviewed_host": BINANCE_SPOT_TESTNET_HOST,
        "adapter_version": BINANCE_SPOT_TESTNET_ADAPTER_VERSION,
        "adapter_source_sha256": adapter_source_sha256,
        "operator_configuration_hash": configuration_hash,
        "credential_refs": list(credential_refs),
        "execution_write_endpoints": [],
        "withdrawal_transfer_endpoints": [],
        "revision": revision,
    }


def _configuration_rollback(
    state_root: Path,
    settings: SecretSettings,
    credential_refs: tuple[str, ...],
    configuration_hash: str,
    adapter_source_sha256: str,
) -> dict[str, object]:
    store = ConfigBundleStore(state_root / "config")
    initial = store.create(
        _configuration_bundle_content(
            settings,
            credential_refs,
            configuration_hash,
            adapter_source_sha256,
            "initial",
        )
    )
    revision = store.create(
        _configuration_bundle_content(
            settings,
            credential_refs,
            configuration_hash,
            adapter_source_sha256,
            "rollback-test-revision",
        )
    )
    store.activate(
        initial.content_hash, actor="phase1-provider-recovery", reason="initial activation"
    )
    store.activate(
        revision.content_hash, actor="phase1-provider-recovery", reason="rollback drill revision"
    )
    restored = store.rollback(
        initial.content_hash,
        actor="phase1-provider-recovery",
        reason="restore prior provider configuration",
    )
    active = store.active()
    active_after_reopen = ConfigBundleStore(state_root / "config").active()
    activation_lines = store.activation_log.read_text(encoding="utf-8").splitlines()
    activation_events = [json.loads(line) for line in activation_lines]
    passed = (
        restored.content_hash == initial.content_hash
        and active is not None
        and active.content_hash == initial.content_hash
        and active_after_reopen is not None
        and active_after_reopen.content_hash == initial.content_hash
        and store.get(initial.content_hash).content == initial.content
        and store.get(revision.content_hash).content == revision.content
        and len(activation_events) == 3
        and str(activation_events[-1].get("reason", "")).startswith("rollback:")
    )
    return {
        "status": "passed" if passed else "failed",
        "bundle_hashes": {
            "initial": initial.content_hash,
            "rollback_test_revision": revision.content_hash,
            "restored": restored.content_hash,
        },
        "active_bundle_hash": active.content_hash if active is not None else None,
        "active_bundle_hash_after_reopen": (
            active_after_reopen.content_hash if active_after_reopen is not None else None
        ),
        "activation_count": len(activation_events),
        "rollback_event_recorded": bool(
            activation_events
            and str(activation_events[-1].get("reason", "")).startswith("rollback:")
        ),
        "writes_attempted": False,
        "in_flight_decisions_mutated": False,
    }


def _child_environment(repository_root: Path) -> dict[str, str]:
    """Provide only import/runtime controls; credential values stay file-scoped."""

    return {
        "ADVISORAI_RUN_NETWORK_SMOKE": "1",
        "PYTHONPATH": repository_root.as_posix(),
    }


def _restart_command(
    *,
    secrets: Path,
    state_root: Path,
    child_report: Path,
    expected_bundle_hash: str,
    configuration_hash: str,
) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--child",
        "--secrets",
        str(secrets),
        "--state-root",
        str(state_root),
        "--child-report",
        str(child_report),
        "--expected-bundle-hash",
        expected_bundle_hash,
        "--configuration-hash",
        configuration_hash,
    ]


def _run_restart_child(
    *,
    repository_root: Path,
    secrets: Path,
    state_root: Path,
    child_report: Path,
    expected_bundle_hash: str,
    configuration_hash: str,
    timeout_seconds: float,
) -> dict[str, object]:
    command = _restart_command(
        secrets=secrets,
        state_root=state_root,
        child_report=child_report,
        expected_bundle_hash=expected_bundle_hash,
        configuration_hash=configuration_hash,
    )
    started_at = datetime.now(UTC).isoformat()
    result: dict[str, object] = {
        "status": "failed",
        "reason": "restart_child_not_completed",
        "process_boundary": "fresh_subprocess",
        "started_at": started_at,
        "command": command,
        "credential_environment_names": [],
    }
    try:
        completed = subprocess.run(
            command,
            cwd=repository_root,
            env=_child_environment(repository_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        result.update(
            {
                "reason": "restart_child_timeout",
                "process_exit_code": None,
                "finished_at": datetime.now(UTC).isoformat(),
            }
        )
        return result
    result.update(
        {
            "status": "failed" if completed.returncode else "passed",
            "reason": "restart_child_completed"
            if completed.returncode == 0
            else "restart_child_failed",
            "process_exit_code": completed.returncode,
            "finished_at": datetime.now(UTC).isoformat(),
        }
    )
    if not child_report.exists():
        result["status"] = "failed"
        result["reason"] = "restart_child_report_missing"
        return result
    encoded = child_report.read_bytes()
    result["child_report_sha256"] = _sha256_bytes(encoded)
    try:
        child = json.loads(encoded)
    except json.JSONDecodeError:
        result["status"] = "failed"
        result["reason"] = "restart_child_report_malformed"
        return result
    if not isinstance(child, dict) or child.get("schema") != CHILD_SCHEMA:
        result["status"] = "failed"
        result["reason"] = "restart_child_report_schema_invalid"
        return result
    result["child_status"] = child.get("status")
    result["child_active_bundle_hash"] = child.get("active_bundle_hash")
    result["child_expected_bundle_hash"] = child.get("expected_bundle_hash")
    result["child_probe"] = child.get("probe")
    if (
        completed.returncode != 0
        or child.get("status") != "passed"
        or child.get("active_bundle_hash") != expected_bundle_hash
        or child.get("expected_bundle_hash") != expected_bundle_hash
        or child.get("configuration_hash") != configuration_hash
    ):
        result["status"] = "failed"
        result["reason"] = "restart_child_read_only_recovery_failed"
    return result


def _new_run_directory(output_root: Path) -> tuple[str, Path]:
    run_base = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_id = run_base
    suffix = 1
    while (output_root / run_id).exists():
        suffix += 1
        run_id = f"{run_base}-{suffix}"
    run_directory = output_root / run_id
    run_directory.mkdir(parents=True)
    return run_id, run_directory


def run_evidence(
    output_root: Path,
    *,
    secrets: Path,
    configuration_hash: str,
    repository_root: Path,
    child_timeout_seconds: float = 90.0,
) -> tuple[Path, dict[str, object]]:
    """Run one immutable provider read/restart/rollback qualification."""

    output_root = output_root.expanduser().resolve()
    secrets = secrets.expanduser().resolve()
    repository_root = repository_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    run_id, run_directory = _new_run_directory(output_root)
    adapter_source_sha256 = _sha256_bytes(
        (repository_root / "src/advisorai/integrations/binance_spot.py").read_bytes()
    )
    qualifier_source_sha256 = _sha256_bytes(Path(__file__).resolve().read_bytes())
    base: dict[str, object] = {
        "schema": SCHEMA,
        "run_id": run_id,
        "measured_at": datetime.now(UTC).isoformat(),
        "venue": "binance_spot_testnet",
        "environment": "paper_testnet",
        "endpoint": BINANCE_SPOT_TESTNET_BASE_URL,
        "reviewed_host": BINANCE_SPOT_TESTNET_HOST,
        "adapter_version": BINANCE_SPOT_TESTNET_ADAPTER_VERSION,
        "adapter_source_sha256": adapter_source_sha256,
        "qualifier_source_sha256": qualifier_source_sha256,
        "secrets_path_reference": str(secrets),
        "credential_refs": [],
        "configuration_hash": configuration_hash,
        "network_calls": 0,
        "writes_attempted": False,
        "order_operations": [],
        "execution_state": {
            "in_flight_decisions_mutated": False,
            "open_order_mutations": False,
            "production_endpoint_calls": 0,
        },
    }
    report: dict[str, object] = dict(base)
    try:
        resolver = CredentialResolver.from_env_file(secrets)
        scoped_names = resolver.available_names(CredentialScope.PAPER_VENUE)
        credential_refs = tuple(name for name in _CREDENTIAL_REFS if name in scoped_names)
        report["credential_refs"] = list(credential_refs)
        scoped = resolver.resolve(CredentialScope.PAPER_VENUE)
        settings = SecretSettings.from_mapping(scoped)
        configuration = _configuration_rollback(
            run_directory / "deployed-state",
            settings,
            credential_refs,
            configuration_hash,
            adapter_source_sha256,
        )
        report["configuration_rollback"] = configuration
        if configuration["status"] != "passed":
            raise RuntimeError("configuration rollback evidence failed")
        transport = build_binance_spot_testnet_transport(resolver)
        pre_restart = _read_only_probe(transport)
        report["pre_restart_read_only"] = pre_restart
        report["network_calls"] = pre_restart["network_calls"]
        if pre_restart["status"] != "passed":
            report["restart"] = {
                "status": "not_run",
                "reason": "pre_restart_read_only_failed",
                "credential_environment_names": [],
            }
        else:
            expected_bundle_hash = str(configuration["bundle_hashes"]["initial"])
            child_report = run_directory / "restart-child.json"
            restart = _run_restart_child(
                repository_root=repository_root,
                secrets=secrets,
                state_root=run_directory / "deployed-state",
                child_report=child_report,
                expected_bundle_hash=expected_bundle_hash,
                configuration_hash=configuration_hash,
                timeout_seconds=child_timeout_seconds,
            )
            report["restart"] = restart
            child_probe = restart.get("child_probe") if isinstance(restart, Mapping) else None
            if isinstance(child_probe, Mapping):
                report["network_calls"] = int(pre_restart["network_calls"]) + int(
                    child_probe.get("network_calls", 0)
                )
        restart = report["restart"]
        passed = (
            configuration["status"] == "passed"
            and pre_restart["status"] == "passed"
            and isinstance(restart, Mapping)
            and restart.get("status") == "passed"
        )
        report["status"] = "passed" if passed else "failed"
        report["qualification_state"] = (
            "EXTERNALLY_MEASURED / PROVIDER_READ_ONLY_RESTART_AND_CONFIG_ROLLBACK_MEASURED"
            if passed
            else "EXTERNALLY_MEASURED / PROVIDER_READ_ONLY_RECOVERY_FAILED"
        )
        report["admission"] = "NOT_ADMITTED"
    except Exception as exc:
        report.update(
            {
                "status": "failed",
                "reason": "qualification_failed",
                "error_class": type(exc).__name__,
                "qualification_state": "EXTERNALLY_MEASURED / PROVIDER_READ_ONLY_RECOVERY_FAILED",
                "admission": "NOT_ADMITTED",
            }
        )

    report_path = _write_immutable_json(
        run_directory / "binance-spot-testnet-recovery.json", report
    )
    digest = _sha256_bytes(report_path.read_bytes())
    pointer = {
        "schema": f"{SCHEMA}.latest",
        "run_id": run_id,
        "report_sha256": digest,
    }
    temporary = output_root / ".latest.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(pointer, sort_keys=True, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, output_root / "latest.json")
    return report_path, {**report, "report_sha256": digest}


def _child_main(args: argparse.Namespace) -> int:
    status = "failed"
    report: dict[str, object] = {
        "schema": CHILD_SCHEMA,
        "status": status,
        "expected_bundle_hash": args.expected_bundle_hash,
        "configuration_hash": args.configuration_hash,
        "writes_attempted": False,
        "credential_environment_names": [],
    }
    try:
        config_root = args.state_root / "config"
        if not (config_root / "active.json").exists():
            raise RuntimeError("active configuration pointer is missing after restart")
        store = ConfigBundleStore(config_root)
        active = store.active()
        if active is None or active.content_hash != args.expected_bundle_hash:
            raise RuntimeError("active configuration bundle changed across restart")
        if active.content.get("operator_configuration_hash") != args.configuration_hash:
            raise RuntimeError("active configuration hash changed across restart")
        resolver = CredentialResolver.from_env_file(args.secrets)
        transport = build_binance_spot_testnet_transport(resolver)
        probe = _read_only_probe(transport)
        report.update(
            {
                "status": probe["status"],
                "active_bundle_hash": active.content_hash,
                "probe": probe,
            }
        )
    except Exception as exc:
        report.update({"error_class": type(exc).__name__})
    _write_immutable_json(args.child_report, report)
    return 0 if report["status"] == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--secrets",
        type=Path,
        default=Path(os.getenv("ADVISORAI_SECRETS_FILE", "secrets.env")),
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path("artifacts/phase1/binance-spot-testnet/recovery"),
    )
    parser.add_argument(
        "--configuration-hash",
        required=True,
        help="zero-network configuration hash; never a credential value",
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--state-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--child-report", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--expected-bundle-hash", help=argparse.SUPPRESS)
    parser.add_argument("--child-timeout-seconds", type=float, default=90.0)
    args = parser.parse_args()
    if not _valid_sha256(args.configuration_hash):
        raise SystemExit("--configuration-hash must be a lowercase SHA-256 digest")
    if args.child:
        if (
            args.state_root is None
            or args.child_report is None
            or not args.expected_bundle_hash
            or not _valid_sha256(args.expected_bundle_hash)
        ):
            raise SystemExit("child restart arguments are incomplete")
        if os.getenv("ADVISORAI_RUN_NETWORK_SMOKE") != "1":
            raise SystemExit(
                "refusing network access; set ADVISORAI_RUN_NETWORK_SMOKE=1 explicitly"
            )
        return _child_main(args)
    if os.getenv("ADVISORAI_RUN_NETWORK_SMOKE") != "1":
        raise SystemExit("refusing network access; set ADVISORAI_RUN_NETWORK_SMOKE=1 explicitly")
    report_path, report = run_evidence(
        args.evidence_dir,
        secrets=args.secrets,
        configuration_hash=args.configuration_hash,
        repository_root=args.repository_root,
        child_timeout_seconds=args.child_timeout_seconds,
    )
    print(json.dumps({"report": str(report_path), **report}, sort_keys=True, separators=(",", ":")))
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
