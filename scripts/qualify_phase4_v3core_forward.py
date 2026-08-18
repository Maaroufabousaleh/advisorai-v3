#!/usr/bin/env python3
"""Run the sealed-root Phase-4 integrity workflow without scoring utility.

This is an offline composition boundary for an already sealed forward root. It
refuses a running root, validates the resource sidecar, runs the existing raw /
normalized integrity auditor, writes a separate exclusion overlay, and invokes
the existing materializer only when every integrity and resource prerequisite is
ready. It never edits an input spool, acquires data, loads credentials, runs a
model, scores utility, or submits an order.
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

from advisorai.phase4.v3core_integrity import (
    IntegrityAuditReport,
    audit_forward_root,
    build_exclusion_overlay,
)
from scripts.audit_phase4_v3core_resources import audit as audit_resources
from scripts.link_phase4_v3core_prediction_outcomes import link_predictions_to_cases
from scripts.materialize_phase4_v3core_forward_input import materialize

WORKFLOW_SCHEMA = "advisorai.phase4.v3-core.forward-terminal-workflow.v1"
TERMINAL_COLLECTOR_STATES = {"target_reached", "deadline_reached", "stopped_with_evidence"}


class TerminalWorkflowRefused(ValueError):
    """Raised when a root cannot enter the sealed terminal workflow."""


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _validate_sha256(value: str, *, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise TerminalWorkflowRefused(f"{field} is not a lowercase SHA-256 digest")


def _write_new(path: Path, payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise TerminalWorkflowRefused(f"immutable output already exists: {path}") from exc
    return _sha256_bytes(encoded)


def _load_json(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TerminalWorkflowRefused(f"{description} is unreadable") from exc
    if not isinstance(value, dict):
        raise TerminalWorkflowRefused(f"{description} must be an object")
    return value


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _git_head(repository_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise TerminalWorkflowRefused("repository commit identity is unavailable") from exc
    return result.stdout.strip().lower()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _assert_output_is_separate(output_root: Path, input_paths: tuple[Path, ...]) -> None:
    output = output_root.resolve()
    if output.exists():
        raise TerminalWorkflowRefused(f"workflow output root already exists: {output}")
    for input_path in input_paths:
        if _is_within(output, input_path) or _is_within(input_path, output):
            raise TerminalWorkflowRefused(
                "workflow output root must be separate from every evidence input"
            )


def _load_sealed_root(run_directory: Path) -> dict[str, Any]:
    run = run_directory.resolve()
    status = _load_json(run / "status.json", "forward status")
    state = status.get("state")
    if state == "running":
        raise TerminalWorkflowRefused("refusing a running forward root")
    if state not in TERMINAL_COLLECTOR_STATES:
        raise TerminalWorkflowRefused(f"forward root has no supported terminal state: {state!r}")
    if state == "target_reached" and status.get("minimum_reached") is not True:
        raise TerminalWorkflowRefused("target-reached root does not attest its frozen minimum")
    return status


def _paired_paths(
    prediction_ledgers: tuple[Path, ...], prediction_manifests: tuple[Path, ...]
) -> None:
    if len(prediction_ledgers) != len(prediction_manifests):
        raise TerminalWorkflowRefused(
            "each prediction ledger must be paired with exactly one prediction manifest"
        )


def _workflow_decision(
    *,
    status: dict[str, Any],
    integrity_report: IntegrityAuditReport,
    resource_report: dict[str, Any],
) -> tuple[str, str, bool]:
    if status.get("state") != "target_reached" or status.get("minimum_reached") is not True:
        return (
            "SAMPLE_MINIMUM_NOT_REACHED",
            "preserve terminal partial evidence; no materialization is permitted",
            False,
        )
    if not integrity_report.admission_evidence_ready:
        return (
            "INTEGRITY_NOT_READY",
            "preserve the integrity report and exclusion overlay; do not score utility",
            False,
        )
    issues = resource_report.get("issues", [])
    if not isinstance(issues, list) or issues:
        return (
            "RESOURCE_NOT_READY",
            "preserve resource evidence and resolve resource-integrity issues before materialization",
            False,
        )
    return ("READY_FOR_MATERIALIZATION", "materialize the frozen Phase-4 input", True)


def _relative(path: Path, repository_root: Path) -> str:
    try:
        return path.resolve().relative_to(repository_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def run(
    *,
    run_directory: Path,
    resource_root: Path,
    preregistration: Path,
    phase3_gate_sha256: str,
    terminal_observed_at: datetime,
    output_root: Path,
    prediction_ledger_paths: tuple[Path, ...] = (),
    prediction_manifest_paths: tuple[Path, ...] = (),
    outcome_link_ledger_paths: tuple[Path, ...] = (),
    minimum_terminal_closed_observations: int = 2,
    minimum_cases_per_symbol: int = 64,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    run = run_directory.resolve()
    resource = resource_root.resolve()
    prereg = preregistration.resolve()
    output = output_root.resolve()
    repo = (repository_root or Path(__file__).resolve().parents[1]).resolve()
    _paired_paths(prediction_ledger_paths, prediction_manifest_paths)
    _validate_sha256(phase3_gate_sha256, field="phase3_gate_sha256")
    status = _load_sealed_root(run)
    input_paths = (
        run,
        run / "raw-responses.jsonl",
        run / "normalized-bars.jsonl",
        run / "completed-cases.jsonl",
        run / "manifest.json",
        run / "status.json",
        run / "config.json",
        run / "source-health.jsonl",
        resource,
        prereg,
        *prediction_ledger_paths,
        *prediction_manifest_paths,
        *outcome_link_ledger_paths,
    )
    _assert_output_is_separate(output, tuple(path.resolve() for path in input_paths))
    if not run.is_dir() or not resource.is_dir():
        raise TerminalWorkflowRefused("sealed forward and resource roots must be directories")
    output.mkdir(parents=True, exist_ok=False)
    integrity_dir = output / "integrity"
    resource_dir = output / "resources"
    effective_outcome_link_paths = tuple(path.resolve() for path in outcome_link_ledger_paths)
    outcome_links_generated = False
    try:
        cases_path = run / "completed-cases.jsonl"
        if (
            not effective_outcome_link_paths
            and prediction_ledger_paths
            and status.get("state") == "target_reached"
            and cases_path.is_file()
        ):
            generated_outcome_links = output / "outcome-links" / "outcome-links.jsonl"
            link_predictions_to_cases(
                prediction_ledger_paths=tuple(path.resolve() for path in prediction_ledger_paths),
                completed_cases_path=cases_path,
                output_path=generated_outcome_links,
            )
            effective_outcome_link_paths = (generated_outcome_links.resolve(),)
            outcome_links_generated = True
        integrity_report = audit_forward_root(
            run / "raw-responses.jsonl",
            run / "normalized-bars.jsonl",
            completed_cases_path=cases_path if cases_path.is_file() else None,
            prediction_ledger_paths=tuple(path.resolve() for path in prediction_ledger_paths),
            outcome_link_ledger_paths=effective_outcome_link_paths,
            prediction_manifest_paths=tuple(path.resolve() for path in prediction_manifest_paths),
            terminal_observed_at=terminal_observed_at,
            minimum_terminal_closed_observations=minimum_terminal_closed_observations,
            minimum_cases_per_symbol=minimum_cases_per_symbol,
            auditor_cli_sha256=_sha256_file(
                Path(__file__).resolve().with_name("audit_phase4_v3core_integrity.py")
            ),
            auditor_repository_commit=_git_head(repo),
            source_manifest_path=run / "manifest.json",
            source_status_path=run / "status.json",
            source_health_path=run / "source-health.jsonl",
            source_config_path=run / "config.json",
        )
        integrity_report_path = integrity_dir / "integrity-audit.json"
        integrity_report_sha256 = _write_new(
            integrity_report_path, integrity_report.model_dump(mode="json")
        )
        overlay = build_exclusion_overlay(integrity_report, report_sha256=integrity_report_sha256)
        exclusion_overlay_path = integrity_dir / "exclusion-overlay.json"
        exclusion_overlay_sha256 = _write_new(
            exclusion_overlay_path, overlay.model_dump(mode="json")
        )
        resource_report = audit_resources(resource)
        resource_report_path = resource_dir / "resource-audit.json"
        resource_report_sha256 = _write_new(resource_report_path, resource_report)
        decision, next_action, materialization_allowed = _workflow_decision(
            status=status,
            integrity_report=integrity_report,
            resource_report=resource_report,
        )
        materialization_result: dict[str, Any] | None = None
        if materialization_allowed:
            materialization_result = materialize(
                run_directory=run,
                preregistration=prereg,
                output_root=output / "materialized",
                phase3_gate_sha256=phase3_gate_sha256,
                integrity_report_path=integrity_report_path,
                exclusion_overlay_path=exclusion_overlay_path,
                prediction_ledger_paths=tuple(path.resolve() for path in prediction_ledger_paths),
                prediction_manifest_paths=tuple(
                    path.resolve() for path in prediction_manifest_paths
                ),
                outcome_link_ledger_paths=effective_outcome_link_paths,
            )
            decision = "MATERIALIZED"
            next_action = "run only the preregistered single-pass Phase-4 utility evaluation"
        workflow = {
            "schema": WORKFLOW_SCHEMA,
            "generated_at": datetime.now(UTC).isoformat(),
            "repository_commit": _git_head(repo),
            "workflow_code_sha256": _sha256_file(Path(__file__).resolve()),
            "run_directory": _relative(run, repo),
            "resource_root": _relative(resource, repo),
            "preregistration": {"path": _relative(prereg, repo), "sha256": _sha256_file(prereg)},
            "phase3_gate_sha256": phase3_gate_sha256,
            "terminal_observed_at": terminal_observed_at.isoformat().replace("+00:00", "Z"),
            "tool_identities": {
                "workflow_cli_sha256": _sha256_file(Path(__file__).resolve()),
                "integrity_module_sha256": _sha256_file(
                    repo / "src/advisorai/phase4/v3core_integrity.py"
                ),
                "integrity_cli_sha256": _sha256_file(
                    repo / "scripts/audit_phase4_v3core_integrity.py"
                ),
                "resource_cli_sha256": _sha256_file(
                    repo / "scripts/audit_phase4_v3core_resources.py"
                ),
                "materializer_cli_sha256": _sha256_file(
                    repo / "scripts/materialize_phase4_v3core_forward_input.py"
                ),
            },
            "source_inputs": {
                name: {
                    "path": _relative(path, repo),
                    "sha256": _sha256_file(path) if path.is_file() else None,
                }
                for name, path in {
                    "raw_responses": run / "raw-responses.jsonl",
                    "normalized_bars": run / "normalized-bars.jsonl",
                    "completed_cases": cases_path,
                    "manifest": run / "manifest.json",
                    "status": run / "status.json",
                    "config": run / "config.json",
                    "source_health": run / "source-health.jsonl",
                }.items()
            },
            "collector_status": {
                "state": status.get("state"),
                "minimum_reached": status.get("minimum_reached"),
                "raw_response_count": status.get("raw_response_count"),
                "normalized_bar_count": status.get("normalized_bar_count"),
                "completed_case_count": status.get("completed_case_count"),
                "case_counts": status.get("case_counts"),
            },
            "integrity": {
                "path": _relative(integrity_report_path, repo),
                "sha256": integrity_report_sha256,
                "overlay_path": _relative(exclusion_overlay_path, repo),
                "overlay_sha256": exclusion_overlay_sha256,
                "audit_fingerprint": integrity_report.audit_fingerprint,
                "sample_minimum_met": integrity_report.sample_minimum_met,
                "integrity_ready": integrity_report.integrity_ready,
                "admission_evidence_ready": integrity_report.admission_evidence_ready,
                "prediction_model_identity_valid": integrity_report.prediction_model_identity_valid,
            },
            "resources": {
                "path": _relative(resource_report_path, repo),
                "sha256": resource_report_sha256,
                "audit_fingerprint": resource_report.get("audit_fingerprint"),
                "issues": resource_report.get("issues", []),
            },
            "prediction_ledgers": [
                {"path": _relative(path, repo), "sha256": _sha256_file(path)}
                for path in prediction_ledger_paths
            ],
            "prediction_manifests": [
                {"path": _relative(path, repo), "sha256": _sha256_file(path)}
                for path in prediction_manifest_paths
            ],
            "outcome_link_ledgers": [
                {"path": _relative(path, repo), "sha256": _sha256_file(path)}
                for path in effective_outcome_link_paths
            ],
            "outcome_links_generated_by_workflow": outcome_links_generated,
            "decision": decision,
            "materialization_attempted": materialization_allowed,
            "materialization": materialization_result,
            "utility_scoring_invoked": False,
            "network_calls": 0,
            "credentials_loaded": False,
            "order_writes_attempted": False,
            "source_inputs_mutated": False,
            "next_action": next_action,
        }
        workflow_path = output / "phase4-terminal-workflow.json"
        workflow_sha256 = _write_new(workflow_path, workflow)
        return {"workflow": str(workflow_path), "workflow_sha256": workflow_sha256, **workflow}
    except (
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as exc:
        refusal = {
            "schema": f"{WORKFLOW_SCHEMA}.refusal",
            "generated_at": datetime.now(UTC).isoformat(),
            "repository_commit": _git_head(repo),
            "workflow_code_sha256": _sha256_file(Path(__file__).resolve()),
            "run_directory": _relative(run, repo),
            "resource_root": _relative(resource, repo),
            "phase3_gate_sha256": phase3_gate_sha256,
            "terminal_observed_at": terminal_observed_at.isoformat().replace("+00:00", "Z"),
            "error_class": type(exc).__name__,
            "decision": "REFUSED",
            "utility_scoring_invoked": False,
            "network_calls": 0,
            "credentials_loaded": False,
            "order_writes_attempted": False,
            "source_inputs_mutated": False,
        }
        _write_new(output / "workflow-refusal.json", refusal)
        raise TerminalWorkflowRefused("sealed-root workflow refused") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--resource-root", type=Path, required=True)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--phase3-gate-sha256", required=True)
    parser.add_argument("--terminal-observed-at", type=_timestamp, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--prediction-ledger", type=Path, action="append", default=[])
    parser.add_argument("--prediction-manifest", type=Path, action="append", default=[])
    parser.add_argument("--outcome-link-ledger", type=Path, action="append", default=[])
    parser.add_argument("--minimum-terminal-closed-observations", type=int, default=2)
    parser.add_argument("--minimum-cases-per-symbol", type=int, default=64)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = run(
            run_directory=args.run_directory,
            resource_root=args.resource_root,
            preregistration=args.preregistration,
            phase3_gate_sha256=args.phase3_gate_sha256,
            terminal_observed_at=args.terminal_observed_at,
            output_root=args.output_root,
            prediction_ledger_paths=tuple(args.prediction_ledger),
            prediction_manifest_paths=tuple(args.prediction_manifest),
            outcome_link_ledger_paths=tuple(args.outcome_link_ledger),
            minimum_terminal_closed_observations=args.minimum_terminal_closed_observations,
            minimum_cases_per_symbol=args.minimum_cases_per_symbol,
        )
    except (
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as exc:
        raise SystemExit(f"sealed-root workflow refused ({type(exc).__name__})") from exc
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("workflow", "workflow_sha256", "decision", "next_action")
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
