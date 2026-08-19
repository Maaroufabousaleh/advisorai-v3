#!/usr/bin/env python3
"""Run the offline preflight/readiness check for a fresh Phase-4 generation.

The command only reads a sanitized JSON specification and emits a typed report.
It never starts a collector, obtains a GPU lease, loads a model, reads secrets,
opens a network connection, or exposes an execution operation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from advisorai.phase4.v3core_generation_readiness import (
    GenerationCoverageInput,
    GenerationPreflightSpec,
    evaluate_generation_readiness,
    evaluate_preflight,
)


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("preflight input must be a JSON object")
    return value


def _write_report(path: Path, report: object) -> None:
    encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("preflight", "readiness"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.mode == "preflight":
            report = evaluate_preflight(
                GenerationPreflightSpec.model_validate(_load(arguments.input))
            )
            exit_code = 0 if report.decision == "READY_TO_LAUNCH" else 2
        else:
            report = evaluate_generation_readiness(
                GenerationCoverageInput.model_validate(_load(arguments.input))
            )
            exit_code = 0 if report.status == "CANDIDATE_COVERAGE_POSSIBLE" else 2
        payload = report.model_dump(mode="json", by_alias=True)
        if arguments.output is None:
            print(json.dumps(payload, sort_keys=True, indent=2, allow_nan=False))
        else:
            _write_report(arguments.output, payload)
        return exit_code
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"phase-4 generation check refused: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
