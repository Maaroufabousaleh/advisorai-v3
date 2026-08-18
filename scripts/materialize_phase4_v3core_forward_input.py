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
    IntegrityAuditReport,
    IntegrityExclusionOverlay,
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


def _load_integrity_boundary(
    *,
    report_path: Path,
    overlay_path: Path,
    run_directory: Path,
    cases_path: Path,
    raw_responses_path: Path,
    normalized_bars_path: Path,
    prediction_ledger_paths: tuple[Path, ...],
    prediction_manifest_paths: tuple[Path, ...],
    outcome_link_ledger_paths: tuple[Path, ...],
) -> tuple[IntegrityAuditReport, IntegrityExclusionOverlay, str, str]:
    try:
        report = IntegrityAuditReport.model_validate_json(report_path.read_text(encoding="utf-8"))
        overlay = IntegrityExclusionOverlay.model_validate_json(
            overlay_path.read_text(encoding="utf-8")
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MaterializationRefused("integrity report or exclusion overlay is invalid") from exc
    report_sha256 = _sha256(report_path)
    overlay_sha256 = _sha256(overlay_path)
    if overlay.audit_report_sha256 != report_sha256:
        raise MaterializationRefused("exclusion overlay does not bind the integrity report")
    if overlay.audit_fingerprint != report.audit_fingerprint:
        raise MaterializationRefused("exclusion overlay fingerprint does not match the report")
    if not report.admission_evidence_ready:
        raise MaterializationRefused("integrity audit is not ready for materialization")
    if report.raw_responses_sha256 != _sha256(raw_responses_path):
        raise MaterializationRefused("integrity report/raw response hash mismatch")
    if report.normalized_bars_sha256 != _sha256(normalized_bars_path):
        raise MaterializationRefused("integrity report/normalized bar hash mismatch")
    if report.completed_cases_sha256 != _sha256(cases_path):
        raise MaterializationRefused("integrity report/completed case hash mismatch")
    if tuple(_sha256(path) for path in prediction_ledger_paths) != report.prediction_ledger_sha256s:
        raise MaterializationRefused("integrity report/prediction ledger hash mismatch")
    if (
        tuple(_sha256(path) for path in prediction_manifest_paths)
        != report.prediction_manifest_sha256s
    ):
        raise MaterializationRefused("integrity report/prediction manifest hash mismatch")
    if (
        tuple(_sha256(path) for path in outcome_link_ledger_paths)
        != report.outcome_link_ledger_sha256s
    ):
        raise MaterializationRefused("integrity report/outcome-link ledger hash mismatch")
    if report.source_manifest_sha256 is not None and report.source_manifest_sha256 != _sha256(
        run_directory / "manifest.json"
    ):
        raise MaterializationRefused("integrity report/source manifest hash mismatch")
    if report.source_status_sha256 is not None and report.source_status_sha256 != _sha256(
        run_directory / "status.json"
    ):
        raise MaterializationRefused("integrity report/source status hash mismatch")
    if report.source_config_sha256 is not None and report.source_config_sha256 != _sha256(
        run_directory / "config.json"
    ):
        raise MaterializationRefused("integrity report/source config hash mismatch")
    expected_ids = tuple(case.case_id for case in report.contaminated_cases)
    if overlay.contaminated_case_ids != expected_ids:
        raise MaterializationRefused("exclusion overlay case identities do not match the report")
    if overlay.raw_completed_case_counts != report.raw_completed_case_counts:
        raise MaterializationRefused("exclusion overlay raw counts do not match the report")
    if overlay.integrity_eligible_case_counts != report.integrity_eligible_case_counts:
        raise MaterializationRefused("exclusion overlay eligible counts do not match the report")
    return report, overlay, report_sha256, overlay_sha256


def materialize(
    *,
    run_directory: Path,
    preregistration: Path,
    output_root: Path,
    phase3_gate_sha256: str,
    integrity_report_path: Path | None = None,
    exclusion_overlay_path: Path | None = None,
    prediction_ledger_paths: tuple[Path, ...] = (),
    prediction_manifest_paths: tuple[Path, ...] = (),
    outcome_link_ledger_paths: tuple[Path, ...] = (),
) -> dict[str, str | int | bool]:
    run_directory = run_directory.resolve()
    preregistration = preregistration.resolve()
    output_root = output_root.resolve()
    if output_root.exists():
        raise MaterializationRefused("materialization output root must be new")
    manifest_path = run_directory / "manifest.json"
    status_path = run_directory / "status.json"
    manifest = _load_json(manifest_path, "forward manifest")
    status = _load_json(status_path, "forward status")
    preregistration_payload = _load_json(preregistration, "preregistration")

    if status.get("state") != "target_reached" or not status.get("minimum_reached"):
        raise MaterializationRefused("forward root has not reached its frozen sample minimum")
    if manifest.get("preregistration_sha256") != _sha256(preregistration):
        raise MaterializationRefused("forward manifest/preregistration hash mismatch")
    if manifest.get("phase3_gate_record_sha256") != phase3_gate_sha256:
        raise MaterializationRefused("forward manifest/Phase-3 gate hash mismatch")
    if manifest.get("credentials_loaded") or manifest.get("order_writes_attempted"):
        raise MaterializationRefused("forward root is not credential-free and write-free")
    if preregistration_payload.get("measurement_status") != "PENDING_FRESH_PIT_DATA":
        raise MaterializationRefused("input must bind the frozen pre-outcome preregistration")
    if preregistration_payload.get("network_calls") != 0:
        raise MaterializationRefused("preregistration must remain offline")

    cases_path = run_directory / "completed-cases.jsonl"
    raw_responses_path = run_directory / "raw-responses.jsonl"
    normalized_bars = run_directory / "normalized-bars.jsonl"
    if (integrity_report_path is None) != (exclusion_overlay_path is None):
        raise MaterializationRefused(
            "integrity report and exclusion overlay must be supplied together"
        )
    cases = _load_cases(cases_path)
    integrity_report = None
    integrity_overlay = None
    integrity_report_sha256 = None
    integrity_overlay_sha256 = None
    raw_case_counts = {
        symbol: sum(case.instrument == symbol for case in cases) for symbol in V3_CORE_SYMBOLS
    }
    if integrity_report_path is not None and exclusion_overlay_path is not None:
        (
            integrity_report,
            integrity_overlay,
            integrity_report_sha256,
            integrity_overlay_sha256,
        ) = _load_integrity_boundary(
            report_path=integrity_report_path.resolve(),
            overlay_path=exclusion_overlay_path.resolve(),
            run_directory=run_directory,
            cases_path=cases_path,
            raw_responses_path=raw_responses_path,
            normalized_bars_path=normalized_bars,
            prediction_ledger_paths=tuple(path.resolve() for path in prediction_ledger_paths),
            prediction_manifest_paths=tuple(path.resolve() for path in prediction_manifest_paths),
            outcome_link_ledger_paths=tuple(path.resolve() for path in outcome_link_ledger_paths),
        )
        if raw_case_counts != integrity_report.raw_completed_case_counts:
            raise MaterializationRefused("integrity report raw counts do not match case ledger")
        contaminated_ids = set(integrity_overlay.contaminated_case_ids)
        cases = tuple(case for case in cases if case.case_id not in contaminated_ids)
    counts = {
        symbol: sum(case.instrument == symbol for case in cases) for symbol in V3_CORE_SYMBOLS
    }
    if integrity_overlay is not None and counts != integrity_overlay.integrity_eligible_case_counts:
        raise MaterializationRefused("integrity exclusion overlay does not match filtered cases")
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
        "raw_case_counts": raw_case_counts,
        "integrity": (
            {
                "report_path": str(integrity_report_path.resolve()),
                "report_sha256": integrity_report_sha256,
                "overlay_path": str(exclusion_overlay_path.resolve()),
                "overlay_sha256": integrity_overlay_sha256,
                "audit_fingerprint": integrity_report.audit_fingerprint,
                "sample_minimum_met": integrity_report.sample_minimum_met,
                "integrity_ready": integrity_report.integrity_ready,
                "admission_evidence_ready": integrity_report.admission_evidence_ready,
                "contaminated_case_count": len(integrity_overlay.contaminated_case_ids),
            }
            if integrity_report is not None and integrity_overlay is not None
            else None
        ),
        "prediction_ledgers": [
            {"path": str(path.resolve()), "sha256": _sha256(path)}
            for path in prediction_ledger_paths
        ],
        "prediction_manifests": [
            {"path": str(path.resolve()), "sha256": _sha256(path)}
            for path in prediction_manifest_paths
        ],
        "prediction_model_identity_valid": (
            integrity_report.prediction_model_identity_valid if integrity_report else None
        ),
        "outcome_link_ledgers": [
            {"path": str(path.resolve()), "sha256": _sha256(path)}
            for path in outcome_link_ledger_paths
        ],
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
    parser.add_argument("--integrity-report", type=Path)
    parser.add_argument("--exclusion-overlay", type=Path)
    parser.add_argument("--prediction-ledger", type=Path, action="append", default=[])
    parser.add_argument("--prediction-manifest", type=Path, action="append", default=[])
    parser.add_argument("--outcome-link-ledger", type=Path, action="append", default=[])
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                materialize(
                    run_directory=args.run_directory,
                    preregistration=args.preregistration,
                    output_root=args.output_root,
                    phase3_gate_sha256=args.phase3_gate_sha256,
                    integrity_report_path=args.integrity_report,
                    exclusion_overlay_path=args.exclusion_overlay,
                    prediction_ledger_paths=tuple(args.prediction_ledger),
                    prediction_manifest_paths=tuple(args.prediction_manifest),
                    outcome_link_ledger_paths=tuple(args.outcome_link_ledger),
                ),
                sort_keys=True,
            )
        )
    except (OSError, KeyError, TypeError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"forward input materialization refused ({type(exc).__name__})") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
