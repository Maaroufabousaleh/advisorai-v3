"""Daily shadow job durability; offline contract tests."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

from advisorai.research.btc_shadow_job import run_shadow_days
from advisorai.research.btc_shadow_log import bar_close_ms, load_view

HOUR_MS = 3_600_000


def _rows(start: str, spec: list[tuple[str, str]]) -> list[dict]:
    base = datetime.fromisoformat(start).date()
    out = []
    for i, (close, open_) in enumerate(spec):
        out.append(
            {
                "date": (base + timedelta(days=i)).isoformat(),
                "open": open_,
                "high": max(close, open_),
                "low": min(close, open_),
                "adj_close": close,
                "volume": "10.0",
            }
        )
    return out


def _write_snapshot(root: Path, rows: list[dict]) -> None:
    payload = (json.dumps({"BTCUSDT": rows}, indent=1) + "\n").encode()
    (root / "bars.json").write_bytes(payload)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "source": "x",
                "symbol": "BTCUSDT",
                "interval": "1d",
                "timestamp_units": "ms-utc",
                "cutoff_rules": "x",
                "bars_sha256": hashlib.sha256(payload).hexdigest(),
            },
            indent=1,
        )
        + "\n"
    )


def _view(tmp_path: Path) -> tuple[tuple[dict, ...], Path, Path]:
    snap = tmp_path / "snap"
    snap.mkdir()
    _write_snapshot(snap, _rows("2024-01-01", [("100.0", "100.0")] * 64))
    forward = tmp_path / "forward"
    forward.mkdir()
    _write_snapshot(
        forward,
        _rows(
            "2024-03-05",
            [
                ("200.0", "200.0"),
                ("201.0", "207.50"),
                ("202.0", "209.0"),
                ("203.0", "210.0"),
                ("204.0", "211.0"),
                ("205.0", "212.0"),
            ],
        ),
    )
    return load_view(snap, forward), snap, forward


def _receipts(view: tuple[dict, ...]) -> dict[str, int]:
    return {str(row["date"]): bar_close_ms(str(row["date"])) + HOUR_MS for row in view}


def _daily_stamp(date: str) -> int:
    return bar_close_ms(date) + 2 * HOUR_MS


def _entries(state: Path) -> dict[str, dict]:
    return {
        json.loads(line)["date"]: json.loads(line)
        for line in (state / "decisions.jsonl").read_text().splitlines()
    }


def test_duplicate_run_appends_nothing(tmp_path):
    view, _, _ = _view(tmp_path)
    state = tmp_path / "state"
    receipts = _receipts(view)
    first = run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        dates=["2024-03-04"],
        received_at=receipts,
        recorded_at_ms=_daily_stamp("2024-03-04"),
    )
    assert first["processed"] == ["2024-03-04"]
    assert first["quarantined"] == []
    second = run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        dates=["2024-03-04", "2024-03-05"],
        received_at=receipts,
        recorded_at_ms=_daily_stamp("2024-03-05"),
    )
    assert second["processed"] == ["2024-03-05"]
    third = run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        dates=["2024-03-04", "2024-03-05"],
        received_at=receipts,
        recorded_at_ms=_daily_stamp("2024-03-07"),
    )
    assert third["processed"] == []
    assert third["fills_logged"] == ["2024-03-04", "2024-03-05"]
    fills = {
        json.loads(line)["date"]: json.loads(line)
        for line in (state / "fills.jsonl").read_text().splitlines()
    }
    assert fills["2024-03-04"]["fill_date"] == "2024-03-06"
    assert fills["2024-03-05"]["fill_date"] == "2024-03-07"
    before = (state / "decisions.jsonl").read_bytes()
    before_fills = (state / "fills.jsonl").read_bytes()
    fourth = run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        dates=["2024-03-04", "2024-03-05"],
        received_at=receipts,
        recorded_at_ms=_daily_stamp("2024-03-07") + HOUR_MS,
    )
    assert fourth["processed"] == []
    assert fourth["fills_logged"] == []
    assert (state / "decisions.jsonl").read_bytes() == before
    assert (state / "fills.jsonl").read_bytes() == before_fills


def test_catch_up_after_cutoff_is_missed(tmp_path):
    view, _, _ = _view(tmp_path)
    state = tmp_path / "state"
    receipts = _receipts(view)
    run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        dates=["2024-03-05"],
        received_at=receipts,
        recorded_at_ms=_daily_stamp("2024-03-05"),
    )
    mar5_line = (state / "decisions.jsonl").read_bytes()
    catchup = run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        dates=["2024-03-04", "2024-03-05"],
        received_at=receipts,
        recorded_at_ms=_daily_stamp("2024-03-06"),
    )
    assert catchup["processed"] == ["2024-03-04"]
    assert catchup["quarantined"] == ["2024-03-04"]
    entries = _entries(state)
    assert entries["2024-03-04"]["missed"] is True
    assert "missed its recording cutoff" in entries["2024-03-04"]["reason"]
    assert entries["2024-03-05"]["missed"] is False
    assert (state / "decisions.jsonl").read_bytes().startswith(mar5_line)
    fills_run = run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        dates=["2024-03-04", "2024-03-05"],
        received_at=receipts,
        recorded_at_ms=_daily_stamp("2024-03-07"),
    )
    assert fills_run["fills_logged"] == ["2024-03-05"]


def test_late_receipt_quarantines_without_scoring(tmp_path):
    view, _, _ = _view(tmp_path)
    state = tmp_path / "state"
    stamp = _daily_stamp("2024-03-05")
    receipts = _receipts(view)
    receipts["2024-01-15"] = stamp + 24 * HOUR_MS
    result = run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        dates=["2024-03-05"],
        received_at=receipts,
        recorded_at_ms=stamp,
    )
    assert result["quarantined"] == ["2024-03-05"]
    assert result["fills_logged"] == []
    entry = _entries(state)["2024-03-05"]
    assert entry["abstained"] is True
    assert entry["missed"] is False
    assert "received after the record time" in entry["reason"]


def test_restart_resumes_after_last_completed_date(tmp_path):
    view, _, _ = _view(tmp_path)
    state = tmp_path / "state"
    receipts = _receipts(view)
    run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        dates=["2024-03-04"],
        received_at=receipts,
        recorded_at_ms=_daily_stamp("2024-03-04"),
    )
    assert (
        json.loads((state / "shadow_state.json").read_text())["last_completed_date"] == "2024-03-04"
    )
    resumed = run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        dates=["2024-03-04", "2024-03-05"],
        received_at=receipts,
        recorded_at_ms=_daily_stamp("2024-03-05"),
    )
    assert resumed["processed"] == ["2024-03-05"]
    assert (
        json.loads((state / "shadow_state.json").read_text())["last_completed_date"] == "2024-03-05"
    )
    lines = (state / "decisions.jsonl").read_text().splitlines()
    assert [json.loads(line)["date"] for line in lines] == ["2024-03-04", "2024-03-05"]


def test_missed_day_recorded_as_missed_and_never_filled(tmp_path):
    view, _, _ = _view(tmp_path)
    state = tmp_path / "state"
    receipts = _receipts(view)
    run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        dates=["2024-03-04"],
        received_at=receipts,
        recorded_at_ms=_daily_stamp("2024-03-04"),
    )
    result = run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        dates=["2024-03-11"],
        received_at=receipts,
        recorded_at_ms=bar_close_ms("2024-03-12") - HOUR_MS,
    )
    assert result["processed"] == ["2024-03-11"]
    assert result["quarantined"] == ["2024-03-11"]
    assert result["fills_logged"] == ["2024-03-04"]
    entries = _entries(state)
    assert entries["2024-03-11"]["missed"] is True
    assert entries["2024-03-04"]["missed"] is False
    fills_run = run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        dates=["2024-03-04", "2024-03-11"],
        received_at=receipts,
        recorded_at_ms=bar_close_ms("2024-03-12") + HOUR_MS,
    )
    assert fills_run["fills_logged"] == []


def test_uncompleted_day_skips_without_recording(tmp_path):
    view, _, _ = _view(tmp_path)
    state = tmp_path / "state"
    result = run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        dates=["2024-03-04", "2024-03-20"],
        received_at=_receipts(view),
        recorded_at_ms=_daily_stamp("2024-03-04"),
    )
    assert result["processed"] == ["2024-03-04"]
    assert result["skipped"] == ["2024-03-20"]
    assert result["last_completed_date"] == "2024-03-04"
    assert list(_entries(state)) == ["2024-03-04"]
