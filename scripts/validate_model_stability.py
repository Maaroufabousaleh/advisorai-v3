#!/usr/bin/env python3
"""Validate a completed Phase-0 selected-model stability root offline.

The validator never changes the runner root and never opens a Phase-0 gate. It
replays the immutable cycle chain, checks the real terminal boundary, binds
each required role to its approved checkpoint/runtime admission bundle, and
emits a separate review artifact with truthful per-role results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from advisorai.phase0 import (
    LocalCandidateAdmission,
    ModelStabilityConfig,
    ModelStabilitySummary,
    read_cycles,
    summarize_stability,
)

SCHEMA = "advisorai.phase0.model-stability-validation.v1"
REQUIRED_ROLES = ("ttm-r2", "finsentiment-deberta-v3", "finbert-minilm")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _admission_identity(path: Path, candidate: str) -> dict[str, Any]:
    admission = LocalCandidateAdmission.model_validate_json(path.read_text(encoding="utf-8"))
    if admission.candidate_name != candidate:
        raise ValueError(f"admission candidate mismatch for {candidate}")
    checkpoint = admission.checkpoint
    runtime = admission.runtime_pin
    return {
        "admission_sha256": _sha256_file(path),
        "candidate_name": admission.candidate_name,
        "checkpoint": {
            "model_family": checkpoint.model_family,
            "repository_id": checkpoint.repository.repository_id,
            "revision": checkpoint.repository.revision,
            "quantization": checkpoint.quantization,
            "runtime_artifact_sha256": sorted(
                artifact.sha256 for artifact in checkpoint.repository.runtime_artifacts
            ),
        },
        "runtime": {
            "project": runtime.project,
            "version_or_commit": runtime.version_or_commit,
            "dependencies": list(runtime.dependencies),
            "lock_hash": runtime.lock_hash,
            "installed_environment_sha256": runtime.installed_environment_sha256,
            "environment_fingerprint": runtime.environment_fingerprint,
            "python_launcher_hash": runtime.python_launcher_hash or runtime.python_executable_hash,
            "resolved_python_binary_hash": runtime.resolved_python_binary_hash
            or runtime.python_executable_hash,
            "pyvenv_cfg_hash": runtime.pyvenv_cfg_hash,
            "runner_version": runtime.runner_version,
            "runner_hash": runtime.runner_hash,
            "worker_kind": runtime.worker_kind,
        },
    }


def _role_report(
    candidate: str,
    cycles: tuple[Any, ...],
    summary: ModelStabilitySummary | None,
    admission: dict[str, Any] | None,
    *,
    terminal: bool,
) -> dict[str, Any]:
    samples = tuple(
        sample for cycle in cycles for sample in cycle.samples if sample.candidate == candidate
    )
    window = summary.candidate_windows.get(candidate) if summary is not None else None
    all_samples_passed = bool(samples) and all(sample.passed for sample in samples)
    if not terminal:
        result = "PENDING_STABILITY"
    elif admission is None or window is None or not window.passed or not all_samples_passed:
        result = "REJECTED / QUARANTINED"
    else:
        result = "QUALIFIED"
    return {
        "candidate": candidate,
        "result": result,
        "cycle_sample_count": len(samples),
        "all_samples_passed": all_samples_passed,
        "manifest_hashes": sorted({sample.qualification_manifest_hash for sample in samples}),
        "admission": admission,
        "stability_window": window.model_dump(mode="json") if window is not None else None,
        "resource": {
            "peak_rss_mib": max((sample.peak_rss_mib for sample in samples), default=None),
            "peak_vram_mib": max((sample.peak_vram_mib for sample in samples), default=None),
            "max_resident_after_unload_mib": max(
                (sample.current_rss_after_unload_mib for sample in samples), default=None
            ),
            "failure_reasons": sorted(
                {sample.failure_reason for sample in samples if sample.failure_reason}
            ),
        },
    }


def validate(
    run_directory: Path,
    *,
    admission_root: Path,
    required_roles: tuple[str, ...] = REQUIRED_ROLES,
) -> dict[str, Any]:
    """Return an offline review report without mutating either input root."""

    run_directory = run_directory.resolve()
    admission_root = admission_root.resolve()
    config_path = run_directory / "config.json"
    cycles_path = run_directory / "cycles.jsonl"
    status_path = run_directory / "status.json"
    if not config_path.is_file() or not cycles_path.is_file() or not status_path.is_file():
        raise FileNotFoundError("stability root is missing config, cycles, or status")

    config = ModelStabilityConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    cycles = read_cycles(cycles_path)
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if tuple(config.candidates) != tuple(required_roles):
        raise ValueError("stability config candidates do not match the required Phase-0 roles")

    target_end = _aware(config.started_at, "config.started_at") + timedelta(
        hours=config.duration_hours
    )
    last_sampled_at = _aware(cycles[-1].sampled_at, "last cycle sampled_at")
    terminal = last_sampled_at >= target_end
    expected_config_hash = hashlib.sha256(
        json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    issues: list[str] = []

    if any(cycle.config_hash != expected_config_hash for cycle in cycles):
        issues.append("cycle_config_hash_mismatch")
    if any(
        set(sample.candidate for sample in cycle.samples) != set(required_roles) for cycle in cycles
    ):
        issues.append("cycle_candidate_set_incomplete")
    if status.get("last_record_hash") != cycles[-1].record_hash:
        issues.append("status_last_record_hash_mismatch")
    if terminal and status.get("state") == "running":
        issues.append("terminal_root_status_still_running")
    if not terminal:
        issues.append("terminal_sample_not_reached")

    summary: ModelStabilitySummary | None = None
    summary_path = run_directory / "summary.json"
    if summary_path.is_file():
        summary = ModelStabilitySummary.model_validate_json(
            summary_path.read_text(encoding="utf-8")
        )
        expected_summary = summarize_stability(config, cycles)
        if summary.model_dump(mode="json") != expected_summary.model_dump(mode="json"):
            issues.append("summary_does_not_match_recomputed_cycles")
        if status.get("state") != summary.status:
            issues.append("status_state_mismatch")
        if status.get("stability_24h_passed") is not summary.stability_24h_passed:
            issues.append("status_stability_result_mismatch")
    elif terminal:
        issues.append("terminal_summary_missing")

    admissions: dict[str, dict[str, Any] | None] = {}
    for candidate in required_roles:
        path = admission_root / candidate / "local-admission.json"
        try:
            admissions[candidate] = _admission_identity(path, candidate)
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            admissions[candidate] = None
            issues.append(f"admission_bundle_invalid:{candidate}:{type(exc).__name__}")

    all_role_windows_pass = bool(summary) and all(
        window is not None and window.passed for window in summary.candidate_windows.values()
    )
    all_cycles_pass = all(
        set(sample.candidate for sample in cycle.samples) == set(required_roles)
        and all(sample.passed for sample in cycle.samples)
        for cycle in cycles
    )
    if terminal and not all_cycles_pass:
        issues.append("one_or_more_stability_cycles_failed")
    if terminal and not all_role_windows_pass:
        issues.append("one_or_more_role_windows_failed")

    if not terminal:
        state = "PENDING_STABILITY"
    elif issues:
        state = "FAIL"
    else:
        state = "PASS_FOR_REVIEW"

    return {
        "schema": SCHEMA,
        "validated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "run_directory": str(run_directory),
        "admission_root": str(admission_root),
        "run_config_sha256": _sha256_file(config_path),
        "cycles_sha256": _sha256_file(cycles_path),
        "status_sha256": _sha256_file(status_path),
        "summary_sha256": _sha256_file(summary_path) if summary_path.is_file() else None,
        "run_id": config.run_id,
        "started_at": _aware(config.started_at, "config.started_at").isoformat(),
        "target_end_at": target_end.isoformat(),
        "last_sampled_at": last_sampled_at.isoformat(),
        "elapsed_hours": round((last_sampled_at - config.started_at).total_seconds() / 3600, 6),
        "cycle_count": len(cycles),
        "last_record_hash": cycles[-1].record_hash,
        "terminal_sample": terminal,
        "state": state,
        "phase0_admission": False,
        "issues": issues,
        "roles": [
            _role_report(
                candidate,
                cycles,
                summary,
                admissions[candidate],
                terminal=terminal,
            )
            for candidate in required_roles
        ],
    }


def _write_report(output_root: Path, report: Mapping[str, Any]) -> tuple[Path, str]:
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    payload = (json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    report_path = output_root / "phase0-model-stability-validation.json"
    report_path.write_bytes(payload)
    digest = _sha256_bytes(payload)
    digest_path = output_root / "phase0-model-stability-validation.sha256"
    digest_path.write_text(f"{digest}  {report_path.name}\n", encoding="ascii")
    return report_path, digest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--admission-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    report = validate(args.run_directory, admission_root=args.admission_root)
    path, digest = _write_report(args.output_root, report)
    print(json.dumps({"state": report["state"], "report": str(path), "sha256": digest}))
    raise SystemExit(0 if report["state"] == "PASS_FOR_REVIEW" else 1)
