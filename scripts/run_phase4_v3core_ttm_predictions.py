#!/usr/bin/env python3
"""Run the credential-free prospective TTM-R2 V3-Core prediction worker.

The worker reads only a normalized forward bar spool and an immutable local
runtime-admission record.  It has no market-data client, credential resolver,
account operation, or order operation.  A mismatched qualified runtime is
recorded as quarantine evidence instead of being adapted silently.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from advisorai.phase4.v3core_ttm import run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--preregistration-sha256", required=True)
    parser.add_argument("--phase3-gate-sha256", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--worker-timeout-seconds", type=float, default=120.0)
    args = parser.parse_args()
    if args.poll_seconds <= 0 or args.worker_timeout_seconds <= 0:
        raise SystemExit("poll and worker timeout values must be positive")
    try:
        until = datetime.fromisoformat(args.until.replace("Z", "+00:00")).astimezone(UTC)
        result = run(
            admission_path=args.admission,
            source_root=args.source_root,
            run_root=args.run_root,
            repository_root=args.repository_root.resolve(),
            preregistration_sha256=args.preregistration_sha256,
            phase3_gate_sha256=args.phase3_gate_sha256,
            until=until,
            poll_seconds=args.poll_seconds,
            worker_timeout_seconds=args.worker_timeout_seconds,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"forward TTM-R2 prediction run refused ({type(exc).__name__})") from exc
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
