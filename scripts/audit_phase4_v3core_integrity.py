#!/usr/bin/env python3
"""Audit a sealed forward V3-Core root without mutating its evidence.

The report and optional exclusion overlay must be written outside the input
spools.  A running root is refused by default because a terminal classification
requires a fixed observation boundary; ``--allow-unsealed`` is intended only
for diagnostic work and does not make the result admission evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from advisorai.phase4.v3core_integrity import (
    IntegrityAuditReport,
    audit_forward_root,
    build_exclusion_overlay,
)


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _write_new(path: Path, payload: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    with path.open("xb") as handle:
        handle.write(encoded)
    from hashlib import sha256

    return sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run-directory", type=Path)
    source.add_argument("--raw-responses", type=Path)
    parser.add_argument("--normalized-bars", type=Path)
    parser.add_argument("--completed-cases", type=Path)
    parser.add_argument("--prediction-ledger", type=Path, action="append", default=[])
    parser.add_argument("--prediction-manifest", type=Path, action="append", default=[])
    parser.add_argument("--outcome-link-ledger", type=Path, action="append", default=[])
    parser.add_argument("--terminal-observed-at", type=_timestamp, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exclusion-output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--status", type=Path)
    parser.add_argument("--source-health", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--minimum-terminal-closed-observations",
        type=int,
        default=2,
    )
    parser.add_argument("--minimum-cases-per-symbol", type=int, default=64)
    parser.add_argument(
        "--repository-commit",
        help="optional immutable repository commit for the auditor provenance record",
    )
    parser.add_argument(
        "--allow-unsealed",
        action="store_true",
        help="allow diagnostic auditing of a running root; never admission evidence",
    )
    return parser


def _resolve_inputs(
    args: argparse.Namespace,
) -> tuple[Path, Path, Path | None, Path | None, Path | None, Path | None, Path | None]:
    if args.run_directory is not None:
        run = args.run_directory.resolve()
        status_path = run / "status.json"
        if status_path.is_file():
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if status.get("state") == "running" and not args.allow_unsealed:
                raise SystemExit(
                    "refusing to classify a running root; seal it first or use "
                    "--allow-unsealed for diagnostics"
                )
        return (
            run / "raw-responses.jsonl",
            run / "normalized-bars.jsonl",
            (run / "completed-cases.jsonl") if (run / "completed-cases.jsonl").is_file() else None,
            (args.manifest.resolve() if args.manifest else run / "manifest.json")
            if (args.manifest or (run / "manifest.json").is_file())
            else None,
            (args.status.resolve() if args.status else run / "status.json")
            if (args.status or (run / "status.json").is_file())
            else None,
            (args.source_health.resolve() if args.source_health else run / "source-health.jsonl")
            if (args.source_health or (run / "source-health.jsonl").is_file())
            else None,
            (args.config.resolve() if args.config else run / "config.json")
            if (args.config or (run / "config.json").is_file())
            else None,
        )
    if args.normalized_bars is None:
        raise SystemExit("--normalized-bars is required without --run-directory")
    return (
        args.raw_responses.resolve(),
        args.normalized_bars.resolve(),
        (args.completed_cases.resolve() if args.completed_cases is not None else None),
        args.manifest.resolve() if args.manifest is not None else None,
        args.status.resolve() if args.status is not None else None,
        args.source_health.resolve() if args.source_health is not None else None,
        args.config.resolve() if args.config is not None else None,
    )


def _ensure_output_is_separate(output: Path, inputs: list[Path]) -> None:
    output_path = output.resolve()
    for input_path in inputs:
        if input_path is None:
            continue
        evidence_path = input_path.resolve()
        evidence_root = evidence_path if evidence_path.is_dir() else evidence_path.parent
        if (
            output_path == evidence_root
            or output_path.is_relative_to(evidence_root)
            or evidence_root.is_relative_to(output_path)
        ):
            raise SystemExit("audit output must be separate from every evidence input")


def main() -> int:
    args = _parser().parse_args()
    (
        raw_path,
        normalized_path,
        cases_path,
        manifest_path,
        status_path,
        source_health_path,
        config_path,
    ) = _resolve_inputs(args)
    unsealed_diagnostic = False
    if args.allow_unsealed:
        if status_path is None:
            raise SystemExit("--allow-unsealed requires --status or --run-directory")
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit("--allow-unsealed requires a readable status file") from exc
        if not isinstance(status, dict):
            raise SystemExit("--allow-unsealed requires an object status file")
        unsealed_diagnostic = status.get("state") == "running"
    input_paths = [
        raw_path,
        normalized_path,
        cases_path,
        *args.prediction_ledger,
        *args.prediction_manifest,
        *args.outcome_link_ledger,
        manifest_path,
        status_path,
        source_health_path,
        config_path,
    ]
    _ensure_output_is_separate(args.output, input_paths)
    if args.exclusion_output is not None:
        _ensure_output_is_separate(args.exclusion_output, input_paths + [args.output])
    report: IntegrityAuditReport = audit_forward_root(
        raw_path,
        normalized_path,
        completed_cases_path=cases_path,
        prediction_ledger_paths=tuple(path.resolve() for path in args.prediction_ledger),
        outcome_link_ledger_paths=tuple(path.resolve() for path in args.outcome_link_ledger),
        prediction_manifest_paths=tuple(path.resolve() for path in args.prediction_manifest),
        terminal_observed_at=args.terminal_observed_at,
        minimum_terminal_closed_observations=args.minimum_terminal_closed_observations,
        minimum_cases_per_symbol=args.minimum_cases_per_symbol,
        auditor_cli_sha256=_sha256_file(Path(__file__).resolve()),
        auditor_repository_commit=args.repository_commit,
        source_manifest_path=manifest_path,
        source_status_path=status_path,
        source_health_path=source_health_path,
        source_config_path=config_path,
        terminal_evidence_eligible=not unsealed_diagnostic,
    )
    report_sha256 = _write_new(args.output, report.model_dump(mode="json"))
    if args.exclusion_output is not None:
        overlay = build_exclusion_overlay(report, report_sha256=report_sha256)
        _write_new(args.exclusion_output, overlay.model_dump(mode="json"))
    print(
        json.dumps(
            {
                "report": str(args.output.resolve()),
                "report_sha256": report_sha256,
                "classification_counts": report.classification_counts,
                "raw_completed_case_counts": report.raw_completed_case_counts,
                "integrity_eligible_case_counts": report.integrity_eligible_case_counts,
                "sample_minimum_met": report.sample_minimum_met,
                "integrity_ready": report.integrity_ready,
                "admission_evidence_ready": report.admission_evidence_ready,
                "admission_minimum_met": report.admission_minimum_met,
                "terminal_evidence_eligible": report.terminal_evidence_eligible,
                "source_health_ledger_valid": report.source_health_ledger_valid,
                "prediction_model_identity_valid": report.prediction_model_identity_valid,
                "prediction_identity_limitations": report.prediction_identity_limitations,
                "audit_fingerprint": report.audit_fingerprint,
                "contaminated_case_count": len(report.contaminated_cases),
                "excluded_prediction_count": len(report.excluded_predictions),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
