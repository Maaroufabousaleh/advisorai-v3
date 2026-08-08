"""Opt-in, read-only paper/testnet connector smoke test.

The smoke intentionally exercises only account, balance, position, fill, and
open-order reads.  It never submits, cancels, transfers, or withdraws.  A
provider-specific path set must be supplied by the operator; the defaults are
only the generic paths used by the injected transport fixtures.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

from advisorai.config import SecretSettings, load_env_file
from advisorai.integrations import (
    ConnectorCard,
    ConnectorState,
    build_paper_venue_transport,
)


def _host(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise ValueError("connector endpoint has no hostname")
    return parsed.hostname.lower().rstrip(".")


def _read_path(value: str) -> str:
    normalized = value.strip()
    if not normalized.startswith("/") or any(
        token in normalized.lower() for token in ("withdraw", "transfer", "live", "prod")
    ):
        raise ValueError("smoke paths must be absolute read-only paths")
    return normalized


def _config_hash(settings: SecretSettings) -> str | None:
    if not settings.venue_base_url:
        return None
    card = ConnectorCard(
        name="paper-venue-transition",
        owner="operator",
        purpose="read-only paper/testnet connector smoke",
        endpoint=settings.venue_base_url,
        allowed_hosts=(_host(settings.venue_base_url),),
        environment=settings.venue_environment,
        credential_refs=tuple(
            name
            for name in settings.credential_references()
            if name.startswith("ADVISORAI_VENUE_")
        ),
        source_grade="execution_grade",
        quota_and_cost="operator review required",
        adapter_version="transition-v2",
        rollback_procedure="revoke connector and return to deterministic paper fixture",
        state=ConnectorState.CONFIGURED,
    )
    return card.canonical_hash()


def _write_evidence(payload: dict[str, object], evidence_dir: Path | None) -> dict[str, object]:
    if evidence_dir is None:
        return payload
    evidence_dir.mkdir(parents=True, exist_ok=True)
    run_id_base = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_id = run_id_base
    suffix = 1
    while (evidence_dir / run_id).exists():
        suffix += 1
        run_id = f"{run_id_base}-{suffix}"
    run_dir = evidence_dir / run_id
    run_dir.mkdir()
    record = {
        "schema": "advisorai.phase1.paper-venue-read-only-smoke.v1",
        "run_id": run_id,
        "result": payload,
    }
    encoded = (json.dumps(record, sort_keys=True, indent=2) + "\n").encode()
    manifest_path = run_dir / "read-only-smoke.json"
    manifest_path.write_bytes(encoded)
    digest = sha256(encoded).hexdigest()
    pointer = {
        "schema": "advisorai.phase1.paper-venue-read-only-smoke.latest.v1",
        "run_id": run_id,
        "manifest_sha256": digest,
    }
    (evidence_dir / "latest.json").write_text(
        json.dumps(pointer, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return {
        **payload,
        "evidence_run_id": run_id,
        "evidence_sha256": digest,
    }


def _emit(payload: dict[str, object], evidence_dir: Path | None = None) -> int:
    payload = _write_evidence(payload, evidence_dir)
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if payload.get("status") in {"passed", "not_ready"} else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--secrets",
        type=Path,
        default=Path(os.getenv("ADVISORAI_SECRETS_FILE", "secrets.env")),
    )
    parser.add_argument(
        "--venue-allowed-host",
        action="append",
        default=[],
        help="reviewed paper/testnet hostname; repeat for an explicit allowlist",
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        help="optional append-only directory for sanitized smoke evidence",
    )
    parser.add_argument("--venue-account-path", type=_read_path, default="/account")
    parser.add_argument("--venue-orders-path", type=_read_path, default="/orders")
    parser.add_argument("--venue-fills-path", type=_read_path, default="/fills")
    parser.add_argument("--venue-positions-path", type=_read_path, default="/positions")
    parser.add_argument("--venue-balances-path", type=_read_path, default="/balances")
    args = parser.parse_args()

    if os.getenv("ADVISORAI_RUN_NETWORK_SMOKE") != "1":
        raise SystemExit("refusing network access; set ADVISORAI_RUN_NETWORK_SMOKE=1 explicitly")

    settings = SecretSettings.from_mapping(load_env_file(args.secrets))
    venue_key = settings.secret_for("ADVISORAI_VENUE_API_KEY")
    venue_secret = settings.secret_for("ADVISORAI_VENUE_API_SECRET")
    if not settings.venue_name or not settings.venue_base_url or not venue_key or not venue_secret:
        return _emit(
            {
                "status": "not_ready",
                "reason": "paper_venue_configuration_missing",
                "environment": settings.venue_environment,
                "venue": settings.venue_name or None,
                "network_calls": 0,
                "config_hash": _config_hash(settings),
            },
            args.evidence_dir,
        )
    if not args.venue_allowed_host:
        return _emit(
            {
                "status": "not_ready",
                "reason": "reviewed_venue_host_allowlist_required",
                "environment": settings.venue_environment,
                "venue": settings.venue_name,
                "network_calls": 0,
                "config_hash": _config_hash(settings),
            },
            args.evidence_dir,
        )

    try:
        transport = build_paper_venue_transport(
            settings,
            allowed_hosts=tuple(args.venue_allowed_host),
            orders_path=args.venue_orders_path,
        )
    except Exception as exc:
        return _emit(
            {
                "status": "failed",
                "reason": "connector_construction_failed",
                "error_class": type(exc).__name__,
                "network_calls": 0,
                "config_hash": _config_hash(settings),
            },
            args.evidence_dir,
        )

    checks: tuple[tuple[str, object], ...] = (
        ("account", lambda: transport.account_state(path=args.venue_account_path)),
        ("open_orders", transport.list_open_orders),
        ("fills", lambda: transport.list_fills(path=args.venue_fills_path)),
        ("positions", lambda: transport.list_positions(path=args.venue_positions_path)),
        ("balances", lambda: transport.list_balances(path=args.venue_balances_path)),
    )
    outcomes: list[dict[str, object]] = []
    for name, operation in checks:
        try:
            value = operation()
            if isinstance(value, tuple | list | dict):
                record_count = len(value)
            else:
                record_count = None
            outcomes.append({"name": name, "status": "ok", "record_count": record_count})
        except Exception as exc:
            outcomes.append({"name": name, "status": "failed", "error_class": type(exc).__name__})
            break

    passed = all(item["status"] == "ok" for item in outcomes) and len(outcomes) == len(checks)
    return _emit(
        {
            "status": "passed" if passed else "failed",
            "environment": settings.venue_environment,
            "venue": settings.venue_name,
            "network_calls": transport.client.request_count,
            "read_operations_attempted": len(outcomes),
            "checks": outcomes,
            "config_hash": _config_hash(settings),
        },
        args.evidence_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
