#!/usr/bin/env python3
"""Audit a sealed forward V3-Core root without mutating its evidence.

The report and optional exclusion overlay must be written outside the input
spools.  A running root is refused by default because a terminal classification
requires a fixed observation boundary; ``--allow-unsealed`` is intended only
for diagnostic work and does not make the result admission evidence.
"""

from __future__ import annotations

import argparse
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run-directory", type=Path)
    source.add_argument("--raw-responses", type=Path)
    parser.add_argument("--normalized-bars", type=Path)
    parser.add_argument("--completed-cases", type=Path)
    parser.add_argument("--prediction-ledger", type=Path, action="append", default=[])
    parser.add_argument("--outcome-link-ledger", type=Path, action="append", default=[])
    parser.add_argument("--terminal-observed-at", type=_timestamp, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exclusion-output", type=Path)
    parser.add_argument(
        "--minimum-terminal-closed-observations",
        type=int,
        default=2,
    )
    parser.add_argument("--minimum-cases-per-symbol", type=int, default=64)
    parser.add_argument(
        "--allow-unsealed",
        action="store_true",
        help="allow diagnostic auditing of a running root; never admission evidence",
    )
    return parser


def _resolve_inputs(args: argparse.Namespace) -> tuple[Path, Path, Path | None]:
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
        )
    if args.normalized_bars is None:
        raise SystemExit("--normalized-bars is required without --run-directory")
    return (
        args.raw_responses.resolve(),
        args.normalized_bars.resolve(),
        (args.completed_cases.resolve() if args.completed_cases is not None else None),
    )


def _ensure_output_is_separate(output: Path, inputs: list[Path]) -> None:
    if output.resolve() in {path.resolve() for path in inputs if path is not None}:
        raise SystemExit("audit output must be separate from every evidence input")


def main() -> int:
    args = _parser().parse_args()
    raw_path, normalized_path, cases_path = _resolve_inputs(args)
    input_paths = [
        raw_path,
        normalized_path,
        cases_path,
        *args.prediction_ledger,
        *args.outcome_link_ledger,
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
        terminal_observed_at=args.terminal_observed_at,
        minimum_terminal_closed_observations=args.minimum_terminal_closed_observations,
        minimum_cases_per_symbol=args.minimum_cases_per_symbol,
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
                "admission_minimum_met": report.admission_minimum_met,
                "contaminated_case_count": len(report.contaminated_cases),
                "excluded_prediction_count": len(report.excluded_predictions),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
