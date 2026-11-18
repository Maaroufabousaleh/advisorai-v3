"""BTCUSDT spot input snapshot and qualification; offline contract tests.

Builds snapshots from crafted klines (no network) and proves the
qualification gate admits only complete, continuous, well-formed bars
available at the decision cutoff.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from advisorai.research.btc_spot import (
    CUTOFF_RULES,
    INTERVAL,
    INTERVAL_MS,
    SOURCE_ARCHIVE,
    SYMBOL,
    TIMESTAMP_UNITS,
    build_snapshot,
    parse_kline_row,
    qualify_snapshot,
)


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


def _row(day: str, open_p: str = "100.0", close: str = "101.0") -> list[str]:
    open_ms = _ms(day)
    return [
        str(open_ms),
        open_p,
        "102.0",
        "99.0",
        close,
        "123.456",
        str(open_ms + INTERVAL_MS - 1),
        "1000.0",
        "10",
        "50.0",
        "500.0",
        "0",
    ]


def _days(start: str, count: int) -> list[list[str]]:
    base = datetime.fromisoformat(start).date()
    from datetime import timedelta

    return [_row((base + timedelta(days=i)).isoformat()) for i in range(count)]


def test_build_and_qualify_records_input_contract(tmp_path):
    klines = _days("2024-01-01", 5)
    root = tmp_path / "snap"
    build_snapshot(klines, root, fetched_at="2024-01-06T00:00:00Z")
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["source"] == SOURCE_ARCHIVE
    assert manifest["symbol"] == SYMBOL
    assert manifest["interval"] == INTERVAL
    assert manifest["timestamp_units"] == TIMESTAMP_UNITS
    assert manifest["cutoff_rules"] == CUTOFF_RULES
    report = qualify_snapshot(root, as_of_ms=_ms("2024-01-06"))
    assert report.verdict == "QUALIFIED"
    assert report.rows == 5
    assert (report.start_date, report.end_date) == ("2024-01-01", "2024-01-05")
    assert report.snapshot_hash == manifest["bars_sha256"]


def test_build_rejects_duplicate_and_gapped_dates(tmp_path):
    klines = _days("2024-01-01", 3)
    with pytest.raises(ValueError, match="duplicate"):
        build_snapshot(klines + [klines[1]], tmp_path / "dup", fetched_at="x")
    gapped = [klines[0], klines[2]]
    with pytest.raises(ValueError, match="date gap"):
        build_snapshot(gapped, tmp_path / "gap", fetched_at="x")


def test_parse_rejects_malformed_klines():
    good = _row("2024-01-01")
    with pytest.raises(ValueError, match="12 fields"):
        parse_kline_row(good[:11])
    bad_span = list(good)
    bad_span[6] = str(int(bad_span[6]) - 1)
    with pytest.raises(ValueError, match="one UTC day"):
        parse_kline_row(bad_span)
    bad_boundary = list(good)
    bad_boundary[0] = str(int(bad_boundary[0]) + 3600000)
    bad_boundary[6] = str(int(bad_boundary[6]) + 3600000)
    with pytest.raises(ValueError, match="day boundary"):
        parse_kline_row(bad_boundary)
    bad_range = _row("2024-01-01", open_p="200.0", close="201.0")
    with pytest.raises(ValueError, match="high/low range"):
        parse_kline_row(bad_range)
    bad_price = _row("2024-01-01", open_p="0.0", close="0.0")
    with pytest.raises(ValueError, match="non-positive price"):
        parse_kline_row(bad_price)
    bad_volume = list(good)
    bad_volume[5] = "-1.0"
    with pytest.raises(ValueError, match="negative volume"):
        parse_kline_row(bad_volume)


def test_parse_normalizes_microsecond_archive_timestamps():
    micro = _row("2024-01-01")
    micro[0] = micro[0] + "000"
    micro[6] = micro[6] + "000"
    parsed = parse_kline_row(micro)
    assert parsed["date"] == "2024-01-01"
    assert parsed["timestamp_raw_unit"] == "us"
    assert parsed["close_time_ms"] == _ms("2024-01-01") + INTERVAL_MS - 1
    mixed = _row("2024-01-01")
    mixed[6] = mixed[6] + "000"
    with pytest.raises(ValueError, match="mixed timestamp units"):
        parse_kline_row(mixed)


def test_qualify_uses_only_bars_complete_at_cutoff(tmp_path):
    root = tmp_path / "snap"
    build_snapshot(_days("2024-01-01", 5), root, fetched_at="x")
    with pytest.raises(ValueError, match="not complete at decision cutoff"):
        qualify_snapshot(root, as_of_ms=_ms("2024-01-05"))
    report = qualify_snapshot(root, as_of_ms=_ms("2024-01-06"))
    assert report.verdict == "QUALIFIED"


def test_qualify_rejects_stale_feed(tmp_path):
    root = tmp_path / "snap"
    build_snapshot(_days("2024-01-01", 5), root, fetched_at="x")
    with pytest.raises(ValueError, match="stale"):
        qualify_snapshot(root, as_of_ms=_ms("2024-01-09"))


def test_qualify_rejects_tampered_bars(tmp_path):
    root = tmp_path / "snap"
    build_snapshot(_days("2024-01-01", 5), root, fetched_at="x")
    bars = json.loads((root / "bars.json").read_text())
    bars[SYMBOL][0]["adj_close"] = "999.0"
    (root / "bars.json").write_text(json.dumps(bars, indent=1) + "\n")
    with pytest.raises(ValueError, match="does not match manifest"):
        qualify_snapshot(root, as_of_ms=_ms("2024-01-06"))


def test_qualified_snapshot_loads_through_replay_interface(tmp_path):
    from advisorai.research.replay import load_snapshot_bars

    root = tmp_path / "snap"
    build_snapshot(_days("2024-01-01", 5), root, fetched_at="x")
    bars, snapshot_hash = load_snapshot_bars(root)
    assert set(bars) == {SYMBOL}
    assert len(bars[SYMBOL]) == 5
    assert bars[SYMBOL][0]["date"] == "2024-01-01"
    assert len(snapshot_hash) == 64
