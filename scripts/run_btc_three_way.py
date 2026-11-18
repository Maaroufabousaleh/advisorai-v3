"""Three-way historical replay on the pinned BTCUSDT snapshot (plumbing only).

Runs three independent implementations and fails unless they agree exactly.
Output is historical characterization, never a strategy verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from advisorai.research.btc_replay import agree
from advisorai.research.replay import load_snapshot_bars


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, help="Pinned snapshot directory.")
    args = parser.parse_args()
    bars, snapshot_hash = load_snapshot_bars(Path(args.snapshot).expanduser())
    try:
        result = agree(bars["BTCUSDT"])
    except ValueError as exc:
        print(f"DISAGREE: {exc}")
        raise SystemExit(1) from exc
    print(
        json.dumps(
            {
                "kind": "HISTORICAL PLUMBING, not a verdict",
                "symbol": "BTCUSDT",
                "snapshot_hash": snapshot_hash,
                **result,
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
