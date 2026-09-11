#!/usr/bin/env python3
"""Check whether a completed Phase-4 generation can be shut down safely.

This is a read-only, fail-closed command.  It does not stop processes, write
evidence, acquire a lock, start a model, use credentials, or shut down the
host.  Exit status 0 means ``SAFE_TO_SHUT_DOWN``; exit status 2 means the
operator must not shut down yet.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from advisorai.phase4.shutdown_readiness import evaluate_shutdown_readiness


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--now must include a timezone")
    return parsed.astimezone(UTC)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--resource-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--source-pid-file", type=Path)
    parser.add_argument("--resource-pid-file", type=Path)
    parser.add_argument("--candidate-pid-file", type=Path)
    parser.add_argument("--now", type=_timestamp)
    parser.add_argument(
        "--source-process-token",
        action="append",
        default=["collect_phase4_v3core_forward.py"],
        help="command-line token required when the source PID is alive",
    )
    parser.add_argument(
        "--resource-process-token",
        action="append",
        default=["monitor_phase3_process_resources.py"],
        help="command-line token required when the sidecar PID is alive",
    )
    parser.add_argument(
        "--candidate-process-token",
        action="append",
        default=["run_phase4_v3core_chronos_predictions.py"],
        help="command-line token required when the candidate PID is alive",
    )
    arguments = parser.parse_args()
    try:
        result = evaluate_shutdown_readiness(
            source_root=arguments.source_root,
            resource_root=arguments.resource_root,
            candidate_root=arguments.candidate_root,
            source_pid_file=arguments.source_pid_file,
            resource_pid_file=arguments.resource_pid_file,
            candidate_pid_file=arguments.candidate_pid_file,
            now=arguments.now,
            source_process_tokens=tuple(arguments.source_process_token),
            resource_process_tokens=tuple(arguments.resource_process_token),
            candidate_process_tokens=tuple(arguments.candidate_process_token),
        )
    except (OSError, TypeError, ValueError) as exc:
        print("NOT_SAFE_TO_SHUT_DOWN")
        print(f"- checker_error:{type(exc).__name__}:{exc}")
        return 2

    print(result.decision)
    for reason in result.reasons:
        print(f"- {reason}")
    return 0 if result.safe else 2


if __name__ == "__main__":
    raise SystemExit(main())
