"""Read-only forward collection for BTCUSDT (no credentials, no orders).

Fetches daily klines after the frozen snapshot end via the public API,
drops the still-forming bar at the explicit cutoff, and writes a forward
segment qualified independently. Never touches the frozen snapshot.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from advisorai.research.btc_spot import (
    PUBLIC_KLINES_API,
    SOURCE_FEED,
    SYMBOL,
    build_snapshot,
    check_continuity,
    qualify_snapshot,
)


def _api(url: str, params: dict[str, str]) -> object:
    query = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{url}?{query}", timeout=60) as response:
        return json.loads(response.read().decode())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, help="Frozen snapshot directory.")
    parser.add_argument("--out", required=True, help="Forward segment directory (out of tree).")
    args = parser.parse_args()
    snapshot = Path(args.snapshot).expanduser()
    manifest = json.loads((snapshot / "manifest.json").read_text())
    start_ms = int(manifest["max_close_time_ms"]) + 1
    server_ms = int(_api("https://api.binance.com/api/v3/time", {})["serverTime"])
    rows = _api(
        PUBLIC_KLINES_API,
        {"symbol": SYMBOL, "interval": "1d", "startTime": str(start_ms), "limit": "1000"},
    )
    assert isinstance(rows, list)
    complete = [row for row in rows if int(row[6]) < server_ms]
    dropped = len(rows) - len(complete)
    if not complete:
        raise ValueError("no complete forward bars at the cutoff")
    out = Path(args.out).expanduser()
    build_snapshot(complete, out, fetched_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    forward_manifest = json.loads((out / "manifest.json").read_text())
    forward_manifest["source"] = SOURCE_FEED
    forward_manifest["prev_snapshot_hash"] = manifest["bars_sha256"]
    forward_manifest["cutoff_ms"] = server_ms
    (out / "manifest.json").write_text(json.dumps(forward_manifest, indent=1) + "\n")
    check_continuity(manifest["end_date"], forward_manifest["start_date"])
    report = qualify_snapshot(out, as_of_ms=server_ms, expect_source=SOURCE_FEED)
    print(
        json.dumps(
            {
                "handoff": f"{manifest['end_date']} -> {forward_manifest['start_date']}",
                "complete_bars": len(complete),
                "dropped_forming_bars": dropped,
                "cutoff_ms": server_ms,
                "report": {
                    "rows": report.rows,
                    "start_date": report.start_date,
                    "end_date": report.end_date,
                    "snapshot_hash": report.snapshot_hash,
                    "verdict": report.verdict,
                },
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
