"""Binance public BTCUSDT spot input snapshot and qualification.

Public market data only: the Binance public data archive plus the public
read-only klines API. No credentials, no Testnet adapter imports, no order
or risk paths anywhere in this module. Real snapshots live out of tree;
only hashes travel in evidence.

Snapshot layout reuses the research replay bar schema (``bars.json`` with
``date``/``open``/``high``/``low``/``adj_close``/``volume`` per symbol) so
``load_snapshot_bars`` reads a qualified BTCUSDT snapshot unchanged. Spot
has no splits or dividends, so ``adj_close`` equals the venue close; that is
recorded in the manifest, not assumed silently.

Cutoff rules (recorded verbatim in every manifest):
- Bars are UTC calendar days identified by kline ``open_time``.
- Archive files carry ms or microsecond timestamps; both normalize to
  ``ms-utc`` by magnitude, and the raw units are recorded in the manifest.
- A bar is complete at decision time T iff ``close_time < T``.
- Crypto trades 24/7: a qualified snapshot contains every UTC day in its
  stated range exactly once. No exchange holidays exist to excuse gaps.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.request import urlopen

ARCHIVE_BASE = "https://data.binance.vision/data/spot/monthly/klines"
PUBLIC_KLINES_API = "https://api.binance.com/api/v3/klines"
SYMBOL = "BTCUSDT"
INTERVAL = "1d"
INTERVAL_MS = 86_400_000
TIMESTAMP_UNITS = "ms-utc"

CUTOFF_RULES = (
    "bars are UTC days keyed by kline open_time; a bar is complete at "
    "decision time T iff close_time_ms < T; every UTC day in [start, end] "
    "appears exactly once (24/7 market, no holidays)"
)

SOURCE_ARCHIVE = "binance-public-spot-archive"
SOURCE_FEED = "binance-public-klines-api"

# A feed that has not printed a bar this far behind the decision cutoff is
# stale rather than quiet (crypto has no close).
STALE_TOLERANCE_MS = 2 * INTERVAL_MS


@dataclass(frozen=True, slots=True)
class QualificationReport:
    source: str
    symbol: str
    interval: str
    timestamp_units: str
    cutoff_rules: str
    snapshot_hash: str
    rows: int
    start_date: str
    end_date: str
    max_close_time_ms: int
    qualified_as_of_ms: int
    verdict: str


def _kline_urls(year: int, month: int) -> tuple[str, str]:
    stem = f"{ARCHIVE_BASE}/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{year}-{month:02d}"
    return f"{stem}.zip", f"{stem}.zip.CHECKSUM"


def _http_bytes(url: str) -> bytes:
    with urlopen(url, timeout=60) as response:
        return response.read()


def fetch_month(year: int, month: int) -> list[list[str]]:
    """Download one monthly 1d archive file; verify its checksum first."""
    zip_url, checksum_url = _kline_urls(year, month)
    digest = _http_bytes(checksum_url).decode().strip().split()[0]
    payload = _http_bytes(zip_url)
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError(f"checksum mismatch for {zip_url}")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if name.endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"unexpected archive layout for {zip_url}: {names}")
        text = archive.read(names[0]).decode()
    return [row for row in csv.reader(io.StringIO(text)) if row]


def _decimal(raw: str, field: str) -> Decimal:
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"malformed kline {field}: {raw!r}") from exc
    return value


def _to_ms(raw: str, field: str) -> tuple[int, str]:
    """Normalize an archive timestamp to ms; newer files use microseconds."""
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"malformed kline {field}: {raw!r}") from exc
    if value >= 10**15:
        return value // 1000, "us"
    return value, "ms"


def parse_kline_row(row: list[str]) -> dict[str, Any]:
    """Parse one Binance spot kline row; reject malformed rows fail-closed."""
    if len(row) != 12:
        raise ValueError(f"malformed kline: expected 12 fields, got {len(row)}")
    open_ms, open_unit = _to_ms(row[0], "open_time")
    close_ms, close_unit = _to_ms(row[6], "close_time")
    if open_unit != close_unit:
        raise ValueError(f"malformed kline: mixed timestamp units: {row[0]!r}/{row[6]!r}")
    if close_ms - open_ms != INTERVAL_MS - 1:
        raise ValueError(f"kline does not span one UTC day: {row[0]!r}/{row[6]!r}")
    if open_ms % INTERVAL_MS != 0:
        raise ValueError(f"kline open_time not on a UTC day boundary: {row[0]!r}")
    open_p = _decimal(row[1], "open")
    high = _decimal(row[2], "high")
    low = _decimal(row[3], "low")
    close = _decimal(row[4], "close")
    volume = _decimal(row[5], "volume")
    if min(open_p, high, low, close) <= 0:
        raise ValueError("malformed kline: non-positive price")
    if volume < 0:
        raise ValueError("malformed kline: negative volume")
    if not (low <= min(open_p, close) and max(open_p, close) <= high):
        raise ValueError("kline high/low range violates open/close")
    day = datetime.fromtimestamp(open_ms // 1000, tz=UTC).date().isoformat()
    return {
        "date": day,
        "open": str(open_p),
        "high": str(high),
        "low": str(low),
        "adj_close": str(close),
        "volume": str(volume),
        "open_time_ms": open_ms,
        "close_time_ms": close_ms,
        "timestamp_raw_unit": open_unit,
    }


def build_snapshot(klines: list[list[str]], snapshot_root: Path, *, fetched_at: str) -> Path:
    """Write ``bars.json`` + ``manifest.json``; return the bars path.

    Rows must already be complete archive klines. Dates must be unique and
    gapless; violations fail closed here, not later.
    """
    parsed = [parse_kline_row(row) for row in klines]
    dates = [item["date"] for item in parsed]
    if len(set(dates)) != len(dates):
        raise ValueError("snapshot build: duplicate bar dates")
    ordered = sorted(parsed, key=lambda item: item["date"])
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        gap_days = (
            datetime.fromisoformat(cur["date"]).date() - datetime.fromisoformat(prev["date"]).date()
        ).days
        if gap_days != 1:
            raise ValueError(f"snapshot build: date gap {prev['date']}..{cur['date']}")
    bars = {
        row["date"]: {
            "date": row["date"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "adj_close": row["adj_close"],
            "volume": row["volume"],
        }
        for row in ordered
    }
    snapshot_root.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps({SYMBOL: list(bars.values())}, indent=1) + "\n").encode()
    digest = hashlib.sha256(payload).hexdigest()
    (snapshot_root / "bars.json").write_bytes(payload)
    manifest = {
        "source": SOURCE_ARCHIVE,
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "timestamp_units": TIMESTAMP_UNITS,
        "timestamp_raw_units": sorted({row["timestamp_raw_unit"] for row in ordered}),
        "cutoff_rules": CUTOFF_RULES,
        "corporate_actions": "spot has no splits/dividends; adj_close equals venue close",
        "bars_sha256": digest,
        "rows": len(ordered),
        "start_date": ordered[0]["date"],
        "end_date": ordered[-1]["date"],
        "max_close_time_ms": max(row["close_time_ms"] for row in ordered),
        "fetched_at": fetched_at,
    }
    (snapshot_root / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return snapshot_root / "bars.json"


def validate_manifest_values(
    manifest: dict[str, Any], *, source: str, symbol: str, interval: str, timestamp_units: str
) -> None:
    """Enforce manifest values, not just keys; fail closed on mismatch."""
    expected = {
        "source": source,
        "symbol": symbol,
        "interval": interval,
        "timestamp_units": timestamp_units,
    }
    for key, want in expected.items():
        if manifest.get(key) != want:
            raise ValueError(f"snapshot manifest {key} is {manifest.get(key)!r}, expected {want!r}")


def check_continuity(snapshot_end_date: str, first_forward_date: str) -> None:
    """Prove the archive-to-feed handoff is gapless at the seam."""
    gap = (
        datetime.fromisoformat(first_forward_date).date()
        - datetime.fromisoformat(snapshot_end_date).date()
    ).days
    if gap != 1:
        raise ValueError(f"archive-to-feed handoff gap: {snapshot_end_date}..{first_forward_date}")


def qualify_snapshot(
    snapshot_root: Path, *, as_of_ms: int, expect_source: str = SOURCE_ARCHIVE
) -> QualificationReport:
    """Qualify a snapshot for decisions taken at ``as_of_ms``; fail closed.

    Rejects missing/duplicate/malformed bars, manifest value mismatches, date
    gaps, any bar completing at or after the decision cutoff, and a stale
    feed. Returns the report; the caller records it as evidence.
    """
    manifest_path = snapshot_root / "manifest.json"
    bars_path = snapshot_root / "bars.json"
    if not manifest_path.is_file() or not bars_path.is_file():
        raise ValueError("snapshot is missing bars.json or manifest.json")
    manifest = json.loads(manifest_path.read_text())
    for key in ("cutoff_rules", "bars_sha256"):
        if not manifest.get(key):
            raise ValueError(f"snapshot manifest lacks {key}")
    validate_manifest_values(
        manifest,
        source=expect_source,
        symbol=SYMBOL,
        interval=INTERVAL,
        timestamp_units=TIMESTAMP_UNITS,
    )
    payload = bars_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != manifest["bars_sha256"]:
        raise ValueError("snapshot bars hash does not match manifest")
    data = json.loads(payload.decode("utf-8"))
    rows = data.get(SYMBOL)
    if not isinstance(rows, list) or not rows:
        raise ValueError("snapshot has no BTCUSDT rows")
    required = ("date", "open", "high", "low", "adj_close", "volume")
    seen: set[str] = set()
    prev_date: str | None = None
    max_close = 0
    for row in rows:
        if any(key not in row for key in required):
            raise ValueError(f"snapshot row lacks fields: {row}")
        day = str(row["date"])
        if day in seen:
            raise ValueError(f"snapshot has duplicate bar date: {day}")
        seen.add(day)
        if prev_date is not None:
            gap = (
                datetime.fromisoformat(day).date() - datetime.fromisoformat(prev_date).date()
            ).days
            if gap != 1:
                raise ValueError(f"snapshot has date gap {prev_date}..{day}")
        prev_date = day
        prices = {key: Decimal(str(row[key])) for key in ("open", "high", "low", "adj_close")}
        if min(prices.values()) <= 0 or Decimal(str(row["volume"])) < 0:
            raise ValueError(f"snapshot has non-positive price or negative volume: {day}")
        if not (
            prices["low"] <= min(prices["open"], prices["adj_close"])
            and max(prices["open"], prices["adj_close"]) <= prices["high"]
        ):
            raise ValueError(f"snapshot has inconsistent high/low range: {day}")
        day_open_ms = int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)
        close_ms = day_open_ms + INTERVAL_MS - 1
        if close_ms >= as_of_ms:
            raise ValueError(f"snapshot bar not complete at decision cutoff: {day}")
        max_close = max(max_close, close_ms)
    if as_of_ms - max_close > STALE_TOLERANCE_MS:
        raise ValueError("snapshot feed is stale at the decision cutoff")
    return QualificationReport(
        source=str(manifest["source"]),
        symbol=str(manifest["symbol"]),
        interval=str(manifest["interval"]),
        timestamp_units=str(manifest["timestamp_units"]),
        cutoff_rules=str(manifest["cutoff_rules"]),
        snapshot_hash=digest,
        rows=len(rows),
        start_date=str(rows[0]["date"]),
        end_date=str(rows[-1]["date"]),
        max_close_time_ms=max_close,
        qualified_as_of_ms=as_of_ms,
        verdict="QUALIFIED",
    )


__all__ = [
    "ARCHIVE_BASE",
    "CUTOFF_RULES",
    "INTERVAL",
    "INTERVAL_MS",
    "PUBLIC_KLINES_API",
    "SOURCE_ARCHIVE",
    "SOURCE_FEED",
    "STALE_TOLERANCE_MS",
    "SYMBOL",
    "TIMESTAMP_UNITS",
    "QualificationReport",
    "build_snapshot",
    "check_continuity",
    "fetch_month",
    "parse_kline_row",
    "qualify_snapshot",
    "validate_manifest_values",
]
