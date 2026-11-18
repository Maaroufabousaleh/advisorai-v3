"""Qualify a BTCUSDT spot snapshot for a decision cutoff (offline).

Prints the qualification report as JSON. Exits non-zero when the input is
not qualified. Defaults to the snapshot's own completion (pinned
historical); pass an explicit cutoff for forward collection checks.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from advisorai.research.btc_spot import qualify_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, help="Snapshot directory.")
    parser.add_argument(
        "--as-of-ms",
        type=int,
        default=None,
        help="Decision cutoff in ms; default: snapshot max close + 1.",
    )
    parser.add_argument(
        "--expect-source",
        default="binance-public-spot-archive",
        help="Expected manifest source value.",
    )
    args = parser.parse_args()
    root = Path(args.snapshot).expanduser()
    as_of_ms = args.as_of_ms
    if as_of_ms is None:
        manifest = json.loads((root / "manifest.json").read_text())
        as_of_ms = int(manifest["max_close_time_ms"]) + 1
    report = qualify_snapshot(root, as_of_ms=as_of_ms, expect_source=args.expect_source)
    print(json.dumps(asdict(report), indent=1))


if __name__ == "__main__":
    main()
