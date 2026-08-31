#!/usr/bin/env python3
"""Evaluate the V3-Core long-run release candidate without launching it.

The input is a sanitized, identity-pinned JSON contract.  This command only
reads that contract and emits a fingerprinted readiness report.  It never
creates a preregistration, acquires a GPU lease, starts a process, opens a
network connection, loads credentials, or touches scientific evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from advisorai.phase4.v3core_longrun import (
    LongRunReleaseCandidateSpec,
    evaluate_release_candidate,
)


def _load(path: Path) -> dict[str, object]:
    value = (
        json.load(sys.stdin) if str(path) == "-" else json.loads(path.read_text(encoding="utf-8"))
    )
    if not isinstance(value, dict):
        raise ValueError("release-candidate input must be a JSON object")
    return value


def _write_exclusive(path: Path, payload: object) -> None:
    encoded = json.dumps(payload, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        report = evaluate_release_candidate(
            LongRunReleaseCandidateSpec.model_validate(_load(arguments.input))
        )
        payload = report.model_dump(mode="json")
        if arguments.output is None:
            print(json.dumps(payload, sort_keys=True, indent=2, allow_nan=False))
        else:
            _write_exclusive(arguments.output, payload)
        return 0 if report.decision == "RELEASE_CANDIDATE_READY_FOR_HUMAN_REVIEW" else 2
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"long-run release-candidate preflight refused: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
