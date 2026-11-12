#!/usr/bin/env python3
"""Validate terminal evidence for a Chronos-2-small 24-hour stability run.

The validator is read-only with respect to the run evidence.  It emits one
immutable validation report (and a hash sidecar) outside the cycle/config
files.  A PASS is impossible without a terminal sample at or after the real
24-hour boundary, complete passing cycles, current identity continuity, and a
clean terminal runner state.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import psutil

from advisorai.phase0 import write_immutable_json
from advisorai.phase0.chronos_stability import (
    CHRONOS_STABILITY_CANDIDATE,
    ChronosStabilityConfig,
    ChronosStabilitySummary,
    payload_hash,
    read_chronos_cycles,
    summarize_chronos_stability,
)
from advisorai.phase4.v3core_chronos import ChronosRuntimeIdentity

VALIDATOR_SCHEMA = "advisorai.phase0.chronos-stability-terminal-validation.v1"


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_value(repository_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository_root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def _identity_issues(config: ChronosStabilityConfig) -> list[str]:
    issues: list[str] = []
    try:
        repository_root = Path(config.repository_root)
        identity = ChronosRuntimeIdentity.from_admission(
            Path(config.admission_path),
            qualification_evidence_path=Path(config.qualification_evidence_path),
            repository_root=repository_root,
        )
        current_commit = _git_value(repository_root, "rev-parse", "HEAD")
        if current_commit != config.repository_commit:
            issues.append("repository_commit_changed")
        if _git_value(repository_root, "status", "--porcelain"):
            issues.append("repository_not_clean")
        source_hashes = {
            "stability_runner_sha256": _sha256_file(
                repository_root / "scripts/run_chronos_runtime_stability.py"
            ),
            "chronos_contract_sha256": _sha256_file(
                repository_root / "src/advisorai/phase4/v3core_chronos.py"
            ),
            "runtime_qualification_sha256": _sha256_file(
                repository_root / "src/advisorai/phase0/runtime_qualification.py"
            ),
        }
        for field_name, actual in source_hashes.items():
            if actual != getattr(config, field_name):
                issues.append(f"{field_name}_changed")
        actual_identity_hash = payload_hash(identity.model_dump(mode="json"))
        if actual_identity_hash != config.runtime_identity_sha256:
            issues.append("runtime_identity_changed")
        for field_name, expected in (
            ("candidate_name", CHRONOS_STABILITY_CANDIDATE),
            ("checkpoint_revision", config.model_revision),
            ("checkpoint_hash", config.checkpoint_sha256),
            ("config_hash", config.config_sha256),
            ("python_launcher_hash", config.python_launcher_hash),
            ("resolved_python_binary_hash", config.resolved_python_binary_hash),
            ("pyvenv_cfg_hash", config.pyvenv_cfg_hash),
            ("installed_environment_sha256", config.installed_environment_sha256),
            ("lock_hash", config.runtime_lock_hash),
            ("runner_hash", config.runner_hash),
            ("runner_script_hash", config.worker_script_sha256),
            ("environment_fingerprint", config.environment_fingerprint),
            ("admission_sha256", config.admission_sha256),
            ("qualification_evidence_sha256", config.qualification_evidence_sha256),
        ):
            if getattr(identity, field_name) != expected:
                issues.append(f"{field_name}_changed")
        if _sha256_file(Path(config.admission_path)) != config.admission_sha256:
            issues.append("admission_artifact_changed")
        if (
            _sha256_file(Path(config.qualification_evidence_path))
            != config.qualification_evidence_sha256
        ):
            issues.append("qualification_evidence_changed")
    except Exception as exc:  # no untrusted traceback is copied to evidence
        safe_type = "".join(
            character
            for character in type(exc).__name__
            if character.isalnum() or character in "_-"
        )
        issues.append(f"runtime_identity_unverifiable:{safe_type or 'Error'}")
    return sorted(set(issues))


def _write_hash_sidecar(path: Path) -> str:
    digest = _sha256_file(path)
    sidecar = path.with_name(path.name + ".sha256")
    encoded = f"{digest}  {path.name}\n"
    if sidecar.exists() and sidecar.read_text(encoding="utf-8") != encoded:
        raise FileExistsError(f"immutable validation hash differs: {sidecar}")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(encoded, encoding="utf-8")
    return digest


def _process_is_alive(process_payload: object) -> bool:
    if not isinstance(process_payload, dict):
        return False
    try:
        pid = int(process_payload["pid"])
        expected_create_time = float(process_payload["process_create_time"])
        if pid <= 0:
            return False
        process = psutil.Process(pid)
        return abs(process.create_time() - expected_create_time) < 0.01
    except (KeyError, TypeError, ValueError, psutil.Error, OSError):
        return False


def validate_run(*, run_directory: Path, output_path: Path | None = None) -> tuple[str, Path, str]:
    run_directory = run_directory.resolve()
    output_path = (output_path or run_directory / "terminal-validation.json").resolve()
    issues: list[str] = []
    config_path = run_directory / "config.json"
    cycles_path = run_directory / "cycles.jsonl"
    status_path = run_directory / "status.json"
    summary_path = run_directory / "summary.json"

    config_payload = _load_json(config_path)
    if config_payload is None:
        issues.append("config_missing_or_invalid")
        report = {
            "schema_version": VALIDATOR_SCHEMA,
            "generated_at": datetime.now(UTC).isoformat(),
            "run_directory": str(run_directory),
            "state": "FAIL",
            "issues": sorted(set(issues)),
            "cycle_count": 0,
            "credentials_loaded": False,
            "order_writes_attempted": False,
            "execution_authority_present": False,
        }
        write_immutable_json(output_path, report)
        return "FAIL", output_path, _write_hash_sidecar(output_path)

    try:
        config = ChronosStabilityConfig.model_validate(config_payload)
    except Exception as exc:
        safe_type = "".join(
            character
            for character in type(exc).__name__
            if character.isalnum() or character in "_-"
        )
        issues.append(f"config_invalid:{safe_type or 'Error'}")
        report = {
            "schema_version": VALIDATOR_SCHEMA,
            "generated_at": datetime.now(UTC).isoformat(),
            "run_directory": str(run_directory),
            "state": "FAIL",
            "issues": sorted(set(issues)),
            "cycle_count": 0,
            "credentials_loaded": False,
            "order_writes_attempted": False,
            "execution_authority_present": False,
        }
        write_immutable_json(output_path, report)
        return "FAIL", output_path, _write_hash_sidecar(output_path)

    try:
        cycles = read_chronos_cycles(cycles_path)
    except Exception as exc:
        cycles = ()
        safe_type = "".join(
            character
            for character in type(exc).__name__
            if character.isalnum() or character in "_-"
        )
        issues.append(f"cycle_chain_invalid:{safe_type or 'Error'}")

    status = _load_json(status_path)
    if status is None:
        issues.append("status_missing_or_invalid")
    summary_payload = _load_json(summary_path)
    summary: ChronosStabilitySummary | None = None
    if summary_payload is not None:
        try:
            summary = ChronosStabilitySummary.model_validate(summary_payload)
        except Exception as exc:
            safe_type = "".join(
                character
                for character in type(exc).__name__
                if character.isalnum() or character in "_-"
            )
            issues.append(f"summary_invalid:{safe_type or 'Error'}")

    if not cycles:
        issues.append("cycle_missing_or_empty")
        computed_summary = None
    else:
        if any(cycle.run_id != config.run_id for cycle in cycles):
            issues.append("cycle_run_identity_mismatch")
        if any(cycle.sample.candidate != CHRONOS_STABILITY_CANDIDATE for cycle in cycles):
            issues.append("cycle_candidate_mismatch")
        if any(
            cycle.sample.runtime_identity_sha256 != config.runtime_identity_sha256
            for cycle in cycles
        ):
            issues.append("cycle_runtime_identity_mismatch")
        if any(not cycle.sample.passed for cycle in cycles):
            issues.append("failed_cycle_present")
        try:
            computed_summary = summarize_chronos_stability(config, cycles)
        except Exception as exc:
            computed_summary = None
            safe_type = "".join(
                character
                for character in type(exc).__name__
                if character.isalnum() or character in "_-"
            )
            issues.append(f"summary_recomputation_failed:{safe_type or 'Error'}")
        if computed_summary is not None and summary is not None:
            if summary.model_dump(mode="json") != computed_summary.model_dump(mode="json"):
                issues.append("summary_does_not_match_cycles")

    identity_issues = _identity_issues(config)
    issues.extend(identity_issues)

    state = str(status.get("state")) if status else "missing"
    process_payload = status.get("process") if status else None
    if not isinstance(process_payload, dict) or not {
        "pid",
        "process_create_time",
        "command_identity",
    }.issubset(process_payload):
        issues.append("supervisor_process_identity_missing")
    elif state == "running" and not _process_is_alive(process_payload):
        issues.append("supervisor_not_alive")
    elif state in {
        "passed",
        "failed",
        "failed_identity_drift",
        "interrupted",
    } and _process_is_alive(process_payload):
        issues.append("supervisor_still_running")
    if state == "interrupted":
        issues.append("run_interrupted")
    if state in {"failed", "failed_identity_drift"}:
        issues.append("runner_failed")
    if cycles:
        target_end = config.started_at + timedelta(hours=config.duration_hours)
        terminal_sample = cycles[-1].sampled_at >= target_end
        if not terminal_sample:
            if state in {"passed", "failed"}:
                issues.append("premature_terminal_state")
            else:
                issues.append("terminal_sample_not_reached")
        if terminal_sample and summary is None:
            issues.append("terminal_summary_missing")
        if state == "running" and terminal_sample:
            issues.append("terminal_runner_state_not_final")
    else:
        target_end = config.started_at + timedelta(hours=config.duration_hours)
        terminal_sample = False

    credentials_loaded = bool(status and status.get("credentials_loaded", False))
    order_writes_attempted = bool(status and status.get("order_writes_attempted", False))
    execution_authority_present = bool(status and status.get("execution_authority_present", False))
    if credentials_loaded:
        issues.append("credentials_loaded")
    if order_writes_attempted:
        issues.append("order_writes_attempted")
    if execution_authority_present:
        issues.append("execution_authority_present")

    all_cycles_passed = bool(cycles) and all(cycle.sample.passed for cycle in cycles)
    terminal_pass = (
        not issues
        and summary is not None
        and summary.status == "passed"
        and summary.stability_24h_passed
        and all_cycles_passed
        and terminal_sample
        and state == "passed"
    )
    if terminal_pass:
        result_state = "PASS"
    elif any(issue.startswith(("terminal_sample_not_reached",)) for issue in issues) and not any(
        issue.startswith(
            (
                "failed_cycle_present",
                "run_interrupted",
                "runner_failed",
                "supervisor_not_alive",
                "supervisor_process_identity_missing",
            )
        )
        for issue in issues
    ):
        result_state = "PENDING_STABILITY"
    else:
        result_state = "FAIL"

    report = {
        "schema_version": VALIDATOR_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "run_id": config.run_id,
        "run_directory": str(run_directory),
        "state": result_state,
        "issues": sorted(set(issues)),
        "config_sha256": _sha256_file(config_path),
        "cycles_sha256": _sha256_file(cycles_path) if cycles_path.exists() else None,
        "status_sha256": _sha256_file(status_path) if status_path.exists() else None,
        "summary_sha256": _sha256_file(summary_path) if summary_path.exists() else None,
        "cycle_count": len(cycles),
        "started_at": config.started_at.isoformat(),
        "target_end": target_end.isoformat(),
        "last_sampled_at": cycles[-1].sampled_at.isoformat() if cycles else None,
        "terminal_sample": terminal_sample,
        "all_cycles_passed": all_cycles_passed,
        "summary_status": summary.status if summary else None,
        "supervisor_process_identity": process_payload,
        "runtime_identity_sha256": config.runtime_identity_sha256,
        "identity_issues": identity_issues,
        "credentials_loaded": credentials_loaded,
        "order_writes_attempted": order_writes_attempted,
        "execution_authority_present": execution_authority_present,
    }
    write_immutable_json(output_path, report)
    return result_state, output_path, _write_hash_sidecar(output_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    state, path, digest = validate_run(run_directory=args.run_directory, output_path=args.output)
    print(json.dumps({"state": state, "report": str(path), "sha256": digest}, sort_keys=True))
    return 0 if state == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
