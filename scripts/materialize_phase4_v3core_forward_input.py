#!/usr/bin/env python3
"""Materialize a completed forward PIT root into the frozen Phase-4 input.

This is an offline boundary.  It reads only the completed-case ledger and
sanitized run metadata, validates every case again, and writes a new immutable
evaluation input.  It never acquires data, loads credentials, runs a model, or
submits an order.  Incomplete or non-target roots are refused so a partial
window cannot be mistaken for the independent admission set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from advisorai.phase4 import (
    EVALUATION_INPUT_SCHEMA,
    FORWARD_CASE_SCHEMA,
    V3_CORE_SYMBOLS,
    V3CoreCaseBuild,
    V3CoreEvaluationInput,
    V3CoreForecastCase,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MATERIALIZATION_SCHEMA = "advisorai.phase4.v3-core-forward.materialization.v1"
MANIFEST_SCHEMA = f"{MATERIALIZATION_SCHEMA}.manifest"


class MaterializationRefused(ValueError):
    """Raised when a forward root cannot support a truthful evaluation input."""


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().lower()


def _write_new(path: Path, payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise MaterializationRefused(f"immutable output already exists: {path}") from exc
    return _sha256_bytes(encoded)


def _load_json(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MaterializationRefused(f"{description} is unreadable") from exc
    if not isinstance(value, dict):
        raise MaterializationRefused(f"{description} must be an object")
    return value


def _load_cases(path: Path) -> tuple[V3CoreForecastCase, ...]:
    if not path.is_file():
        raise MaterializationRefused("completed-case ledger is missing")
    cases: list[V3CoreForecastCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if record.get("schema") != FORWARD_CASE_SCHEMA:
                raise ValueError("case schema is not the forward schema")
            case_payload = record["case"]
            expected_hash = str(record["case_hash"])
            if _sha256_bytes(_canonical(case_payload)) != expected_hash:
                raise ValueError("case hash mismatch")
            case = V3CoreForecastCase.model_validate(case_payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MaterializationRefused(f"invalid completed case at line {line_number}") from exc
        if case.case_id in seen:
            raise MaterializationRefused("completed case ledger contains a duplicate case")
        if case.evidence_class != "forward_pit_admission" or not case.phase3_admitted:
            raise MaterializationRefused("completed cases must be admitted forward PIT cases")
        seen.add(case.case_id)
        cases.append(case)
    return tuple(cases)


def materialize(
    *,
    run_directory: Path,
    preregistration: Path,
    output_root: Path,
    phase3_gate_sha256: str,
) -> dict[str, str | int | bool]:
    run_directory = run_directory.resolve()
    preregistration = preregistration.resolve()
    manifest_path = run_directory / "manifest.json"
    status_path = run_directory / "status.json"
    manifest = _load_json(manifest_path, "forward manifest")
    status = _load_json(status_path, "forward status")
    preregistration_payload = _load_json(preregistration, "preregistration")

    if manifest.get("evidence_class") == "PROSPECTIVE_CANARY_ONLY":
        raise MaterializationRefused("prospective canary evidence cannot enter the Phase-4 materializer")
    if status.get("state") != "target_reached" or not status.get("minimum_reached"):
        raise MaterializationRefused("forward root has not reached its frozen sample minimum")
    if manifest.get("preregistration_sha256") != _sha256(preregistration):
        raise MaterializationRefused("forward manifest/preregistration hash mismatch")
    if manifest.get("phase3_gate_record_sha256") != phase3_gate_sha256:
        raise MaterializationRefused("forward manifest/Phase-3 gate hash mismatch")
    if manifest.get("credentials_loaded") or manifest.get("order_writes_attempted"):
        raise MaterializationRefused("forward root is not credential-free and write-free")
    if manifest.get("evidence_class") != "forward_pit_admission":
        raise MaterializationRefused(
            "only forward_pit_admission roots may enter the Phase-4 materializer"
        )
    if manifest.get("admission_eligible") is False:
        raise MaterializationRefused("non-admission evidence cannot enter the Phase-4 materializer")
    if preregistration_payload.get("measurement_status") != "PENDING_FRESH_PIT_DATA":
        raise MaterializationRefused("input must bind the frozen pre-outcome preregistration")
    if preregistration_payload.get("network_calls") != 0:
        raise MaterializationRefused("preregistration must remain offline")

    cases = _load_cases(run_directory / "completed-cases.jsonl")
    counts = {
        symbol: sum(case.instrument == symbol for case in cases) for symbol in V3_CORE_SYMBOLS
    }
    target = int(preregistration_payload["plan"]["minimum_cases_per_symbol"])
    if any(counts[symbol] < target for symbol in V3_CORE_SYMBOLS):
        raise MaterializationRefused("completed case ledger is below the frozen per-symbol minimum")
    if len(cases) < int(preregistration_payload["plan"]["minimum_total_cases"]):
        raise MaterializationRefused("completed case ledger is below the frozen total minimum")

    first = cases[0]
    if any(
        case.source_id != first.source_id
        or case.provider_identity != first.provider_identity
        or case.endpoint != first.endpoint
        or case.source_snapshot_hash != first.source_snapshot_hash
        for case in cases
    ):
        raise MaterializationRefused("completed cases contain source or snapshot substitution")
    normalized_bars = run_directory / "normalized-bars.jsonl"
    build = V3CoreCaseBuild(
        schema_version=EVALUATION_INPUT_SCHEMA,
        evidence_class="forward_pit_admission",
        source_id=first.source_id,
        provider_identity=first.provider_identity,
        endpoint=first.endpoint,
        source_snapshot_hash=first.source_snapshot_hash,
        bar_count=(
            len(normalized_bars.read_text(encoding="utf-8").splitlines())
            if normalized_bars.is_file()
            else 0
        ),
        cases=cases,
    )
    evaluation_input = V3CoreEvaluationInput(
        schema_version=EVALUATION_INPUT_SCHEMA,
        plan_id=str(preregistration_payload["plan"]["plan_id"]),
        phase3_gate_record_sha256=phase3_gate_sha256,
        build=build,
    )
    input_payload = evaluation_input.model_dump(mode="json")
    generated_at = datetime.now(UTC).isoformat()
    input_path = output_root / "phase4-v3core-cadence-input.json"
    input_sha256 = _write_new(input_path, input_payload)
    generation = {
        "schema": MATERIALIZATION_SCHEMA,
        "generated_at": generated_at,
        "repository_commit": _git_head(),
        "run_directory": _relative(run_directory),
        "run_manifest_sha256": _sha256(manifest_path),
        "run_status_sha256": _sha256(status_path),
        "raw_responses_sha256": _sha256(run_directory / "raw-responses.jsonl"),
        "normalized_bars_sha256": _sha256(normalized_bars),
        "preregistration": {
            "path": _relative(preregistration),
            "sha256": _sha256(preregistration),
        },
        "phase3_gate_record_sha256": phase3_gate_sha256,
        "provider_identity": first.provider_identity,
        "endpoint": first.endpoint,
        "symbols": list(V3_CORE_SYMBOLS),
        "case_counts": counts,
        "evidence_class": "forward_pit_admission",
        "network_calls": 0,
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "input": {"path": input_path.name, "sha256": input_sha256},
        "selection_policy": "single_pass_frozen_forward_window_no_tuning_or_holdout_selection",
        "notes": [
            "This materializer is offline and does not modify the source run.",
            "Predictions must be generated before outcomes and evaluated only after this input is frozen.",
            "LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.",
        ],
    }
    generation_path = output_root / "phase4-v3core-cadence-materialization.json"
    generation_sha256 = _write_new(generation_path, generation)
    manifest_payload = {
        "schema": MANIFEST_SCHEMA,
        "input": input_path.name,
        "input_sha256": input_sha256,
        "generation": generation_path.name,
        "generation_sha256": generation_sha256,
    }
    manifest_sha256 = _write_new(output_root / "evidence-manifest.json", manifest_payload)
    return {
        "input": str(input_path),
        "input_sha256": input_sha256,
        "generation": str(generation_path),
        "generation_sha256": generation_sha256,
        "manifest_sha256": manifest_sha256,
        "case_count": len(cases),
        "network_calls": False,
        "credentials_loaded": False,
        "order_writes_attempted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--phase3-gate-sha256", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                materialize(
                    run_directory=args.run_directory,
                    preregistration=args.preregistration,
                    output_root=args.output_root,
                    phase3_gate_sha256=args.phase3_gate_sha256,
                ),
                sort_keys=True,
            )
        )
    except (OSError, KeyError, TypeError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"forward input materialization refused ({type(exc).__name__})") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
