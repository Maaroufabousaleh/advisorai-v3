"""Run the non-invasive Phase 0 availability inventory.

Usage: ``uv run python -m advisorai.phase0 --output artifacts/phase0/availability.json``
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .bakeoffs import default_candidates, run_availability_inventory, write_bakeoff_record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/phase0/availability.json"),
        help="path for the immutable review input",
    )
    args = parser.parse_args()
    write_bakeoff_record(args.output, run_availability_inventory(default_candidates()))
    print(args.output)


if __name__ == "__main__":
    main()
