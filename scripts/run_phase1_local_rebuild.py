#!/usr/bin/env python3
"""Run the local Phase-1 configuration and Bronze rebuild evidence drill.

The drill exercises the same immutable configuration and lake boundaries used
by the application. It creates a disposable local deployment state, activates
two content-addressed configuration bundles, rolls back to the first bundle,
and rebuilds one raw Bronze artifact in a clean lake root. It never accesses a
network, credentials, venue, or live-capital path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from advisorai.config import ConfigBundleStore
from advisorai.lake import DataLake

CONFIG_FILES = (
    Path("configs/v3_core.yaml"),
    Path("configs/execution/v3_core.yaml"),
    Path("configs/risk/v3_core.yaml"),
    Path("configs/modes/standard.yaml"),
    Path("configs/resources/v3_core.yaml"),
)
SCHEMA = "advisorai.phase1.local-rebuild-evidence.v1"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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


def _source_config(config_root: Path) -> dict[str, object]:
    files: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for relative_path in CONFIG_FILES:
        path = config_root / relative_path
        payload = path.read_bytes()
        files[relative_path.as_posix()] = payload.decode("utf-8")
        hashes[relative_path.as_posix()] = _sha256_bytes(payload)
    return {
        "schema": "advisorai.v3.local-config-bundle.v1",
        "source_files": files,
        "source_hashes": hashes,
    }


def _configuration_drill(state_root: Path, config_root: Path) -> dict[str, object]:
    store = ConfigBundleStore(state_root / "config")
    base = _source_config(config_root)
    first = store.create({**base, "operator_revision": "initial"})
    second = store.create({**base, "operator_revision": "tightened-test-revision"})
    store.activate(first.content_hash, actor="phase1-evidence", reason="initial local activation")
    store.activate(second.content_hash, actor="phase1-evidence", reason="rollback drill revision")
    restored = store.rollback(
        first.content_hash,
        actor="phase1-evidence",
        reason="deterministic rollback drill",
    )
    active = store.active()
    active_after_restart = ConfigBundleStore(state_root / "config").active()
    activation_lines = store.activation_log.read_text(encoding="utf-8").splitlines()
    activation_events = [json.loads(line) for line in activation_lines]
    rollback_event = activation_events[-1] if activation_events else {}
    passed = (
        active is not None
        and active.content_hash == first.content_hash
        and active_after_restart is not None
        and active_after_restart.content_hash == first.content_hash
        and restored.content_hash == first.content_hash
        and len(activation_events) == 3
        and str(rollback_event.get("reason", "")).startswith("rollback:")
        and store.get(first.content_hash).content == first.content
        and store.get(second.content_hash).content == second.content
    )
    return {
        "passed": passed,
        "bundle_hashes": {
            "initial": first.content_hash,
            "tightened_test_revision": second.content_hash,
            "restored": restored.content_hash,
        },
        "active_bundle_hash": active.content_hash if active is not None else None,
        "active_bundle_hash_after_restart": (
            active_after_restart.content_hash if active_after_restart is not None else None
        ),
        "activation_count": len(activation_events),
        "rollback_reason": rollback_event.get("reason"),
        "source_config_hashes": base["source_hashes"],
    }


def _bronze_rebuild_drill(state_root: Path, rebuild_root: Path) -> dict[str, object]:
    timestamp = datetime(2026, 8, 8, 0, 0, tzinfo=UTC)
    payload = b'{"event":"trade","price":"100000.00","symbol":"BTCUSDT"}'
    kwargs = {
        "dataset": "native_market_messages",
        "payload": payload,
        "source_family": "native_venue",
        "origin": "approved-venue",
        "first_available_at": timestamp,
        "ingested_at": timestamp,
        "parser_version": "native-v1",
    }
    source_lake = DataLake(state_root / "lake")
    rebuilt_lake = DataLake(rebuild_root)
    original = source_lake.write_bronze(**kwargs)
    rebuilt = rebuilt_lake.write_bronze(**kwargs)
    original_rows = source_lake.read_rows(original)
    rebuilt_rows = rebuilt_lake.read_rows(rebuilt)
    original_manifest = (state_root / "lake" / original.manifest_uri).read_bytes()
    rebuilt_manifest = (rebuild_root / rebuilt.manifest_uri).read_bytes()
    original_artifact = (state_root / "lake" / original.uri).read_bytes()
    rebuilt_artifact = (rebuild_root / rebuilt.uri).read_bytes()
    passed = (
        original == rebuilt
        and original_rows == rebuilt_rows
        and original_manifest == rebuilt_manifest
        and original_artifact == rebuilt_artifact
    )
    return {
        "passed": passed,
        "artifact_id": str(original.artifact_id),
        "content_hash": original.content_hash,
        "manifest_sha256": _sha256_bytes(original_manifest),
        "artifact_sha256": _sha256_bytes(original_artifact),
        "row_count": original.row_count,
        "manifest_bytes_equal": original_manifest == rebuilt_manifest,
        "artifact_bytes_equal": original_artifact == rebuilt_artifact,
        "rows_equal": original_rows == rebuilt_rows,
    }


def run_evidence(output_root: Path, *, config_root: Path) -> tuple[Path, dict[str, object]]:
    """Write one immutable local evidence run and return its path and report."""

    output_root = output_root.expanduser().resolve()
    config_root = config_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    run_id_base = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_id = run_id_base
    suffix = 1
    while (output_root / run_id).exists():
        suffix += 1
        run_id = f"{run_id_base}-{suffix}"
    run_directory = output_root / run_id
    state_root = run_directory / "deployed-state"
    report = {
        "schema": SCHEMA,
        "run_id": run_id,
        "measured_at": datetime.now(UTC).isoformat(),
        "network_calls": 0,
        "config_root": str(config_root),
        "configuration_rollback": _configuration_drill(state_root, config_root),
        "bronze_rebuild": _bronze_rebuild_drill(state_root, run_directory / "rebuilt-lake"),
    }
    report["passed"] = bool(
        report["configuration_rollback"]["passed"] and report["bronze_rebuild"]["passed"]
    )
    report_path = _write_immutable_json(run_directory / "phase1-local-rebuild.json", report)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/phase1/local-rebuild"),
        help="ignored local evidence root",
    )
    parser.add_argument(
        "--config-root",
        type=Path,
        default=Path("."),
        help="repository root containing the deployed V3-Core config files",
    )
    args = parser.parse_args()
    report_path, report = run_evidence(args.output, config_root=args.config_root)
    print(json.dumps({"report": str(report_path), **report}, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
