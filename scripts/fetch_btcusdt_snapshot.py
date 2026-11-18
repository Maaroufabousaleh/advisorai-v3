"""Fetch a Binance public BTCUSDT 1d spot snapshot (owner-run, network).

Downloads monthly archive files, verifies each checksum, and builds a
frozen snapshot out of tree. Only the manifest hash travels in evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from advisorai.research.btc_spot import build_snapshot, fetch_month


def _months(start: str, end: str) -> list[tuple[int, int]]:
    start_date = datetime.strptime(start, "%Y-%m").date()
    end_date = datetime.strptime(end, "%Y-%m").date()
    if end_date < start_date:
        raise ValueError("end month precedes start month")
    out: list[tuple[int, int]] = []
    year, month = start_date.year, start_date.month
    while (year, month) <= (end_date.year, end_date.month):
        out.append((year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="First month YYYY-MM.")
    parser.add_argument("--end", required=True, help="Last month YYYY-MM.")
    parser.add_argument("--out", required=True, help="Snapshot directory (out of tree).")
    args = parser.parse_args()
    klines: list[list[str]] = []
    for year, month in _months(args.start, args.end):
        rows = fetch_month(year, month)
        print(f"{year}-{month:02d}: {len(rows)} klines, checksum ok")
        klines.extend(rows)
    out = Path(args.out).expanduser()
    build_snapshot(
        klines,
        out,
        fetched_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    manifest = json.loads((out / "manifest.json").read_text())
    print(
        f"snapshot: {out} rows={manifest['rows']} {manifest['start_date']}..{manifest['end_date']}"
    )
    print(f"bars_sha256: {manifest['bars_sha256']}")


if __name__ == "__main__":
    main()
