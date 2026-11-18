"""Offline multi-day scoring rehearsal; fixture only, no forward window."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from advisorai.research.btc_scoring import score_window
from advisorai.research.btc_shadow_job import run_shadow_days
from advisorai.research.btc_shadow_log import bar_close_ms, load_view

HOUR_MS = 3_600_000


def _rows(start: str, spec: list[tuple[str, str]]) -> list[dict]:
    base = datetime.fromisoformat(start).date()
    return [
        {
            "date": (base + timedelta(days=i)).isoformat(),
            "open": open_,
            "high": max(close, open_),
            "low": min(close, open_),
            "adj_close": close,
            "volume": "10.0",
        }
        for i, (close, open_) in enumerate(spec)
    ]


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


def _rehearsal_view(tmp_path: Path) -> tuple[tuple[dict, ...], dict[str, int]]:
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
    view = load_view(snap, forward)
    receipts = {str(row["date"]): bar_close_ms(str(row["date"])) + HOUR_MS for row in view}
    return view, receipts


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_scoring_rehearsal_with_entry_miss_restart_and_valuation(tmp_path):
    view, receipts = _rehearsal_view(tmp_path)
    state = tmp_path / "state"

    def daily(date: str):
        return run_shadow_days(
            view=view,
            forward_hash="f" * 64,
            state_dir=state,
            dates=[date],
            received_at=receipts,
            recorded_at_ms=bar_close_ms(date) + 2 * HOUR_MS,
        )

    assert daily("2024-03-04")["processed"] == ["2024-03-04"]
    assert daily("2024-03-05")["processed"] == ["2024-03-05"]
    before_outage = (state / "decisions.jsonl").read_bytes()

    # Outage: no run records March 6 inside its window. The restart records
    # March 7 honestly and marks March 6 missed even though its bar is here.
    restarted = run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        dates=["2024-03-06", "2024-03-07"],
        received_at=receipts,
        recorded_at_ms=bar_close_ms("2024-03-07") + 2 * HOUR_MS,
    )
    assert restarted["processed"] == ["2024-03-06", "2024-03-07"]
    assert restarted["quarantined"] == ["2024-03-06"]
    assert (state / "decisions.jsonl").read_bytes().startswith(before_outage)

    assert daily("2024-03-08")["processed"] == ["2024-03-08"]
    assert daily("2024-03-09")["processed"] == ["2024-03-09"]
    assert daily("2024-03-10")["processed"] == ["2024-03-10"]

    decisions = _read_jsonl(state / "decisions.jsonl")
    fills = _read_jsonl(state / "fills.jsonl")
    assert {entry["date"]: entry["missed"] for entry in decisions}["2024-03-06"] is True

    report = score_window(view=view, decisions=decisions, fills=fills, costs_bps="20")
    assert report.mark_date == "2024-03-10"
    assert [(entry.date, entry.side, entry.fill_date) for entry in report.executions] == [
        ("2024-03-05", "buy", "2024-03-07")
    ]
    assert report.executions[0].fill_price == Decimal("209.0")
    assert dict(report.positions) == {
        "2024-03-04": 0,
        "2024-03-05": 1,
        "2024-03-06": 1,
        "2024-03-07": 1,
        "2024-03-08": 1,
        "2024-03-09": 1,
        "2024-03-10": 1,
    }
    # Hand calculation: 20 bps entry at the March 7 open (209.0), held
    # through the missed day, marked at the March 10 close (205.0).
    assert report.strategy_value == Decimal("998") / 1000 * Decimal("205") / Decimal("209")
    # Benchmark: same 20 bps, entered at the first open after the first
    # decision record (March 6 open, 207.50), same final mark.
    assert report.benchmark_entry_date == "2024-03-06"
    assert report.benchmark_value == (Decimal("998") / 1000 * Decimal("205") / Decimal("207.50"))
    assert len(fills) > len(report.executions)


def test_scorer_fails_closed_without_traded_day_fill(tmp_path):
    view, receipts = _rehearsal_view(tmp_path)
    state = tmp_path / "state"
    run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        dates=["2024-03-05"],
        received_at=receipts,
        recorded_at_ms=bar_close_ms("2024-03-05") + 2 * HOUR_MS,
    )
    decisions = _read_jsonl(state / "decisions.jsonl")
    try:
        score_window(view=view, decisions=decisions, fills=[])
    except ValueError as exc:
        assert "fill missing for traded day 2024-03-05" in str(exc)
    else:
        raise AssertionError("scorer accepted a traded day without a fill")


def test_scorer_rejects_fill_printing_after_mark(tmp_path):
    view, receipts = _rehearsal_view(tmp_path)
    state = tmp_path / "state"

    def daily(date: str):
        return run_shadow_days(
            view=view,
            forward_hash="f" * 64,
            state_dir=state,
            dates=[date],
            received_at=receipts,
            recorded_at_ms=bar_close_ms(date) + 2 * HOUR_MS,
        )

    assert daily("2024-03-04")["processed"] == ["2024-03-04"]
    assert daily("2024-03-05")["processed"] == ["2024-03-05"]
    assert daily("2024-03-06")["processed"] == ["2024-03-06"]
    assert daily("2024-03-07")["processed"] == ["2024-03-07"]
    decisions = _read_jsonl(state / "decisions.jsonl")
    fills = _read_jsonl(state / "fills.jsonl")
    # The Mar 5 entry fills at the Mar 7 open; scoring at the Mar 6 mark
    # must refuse rather than let a post-mark price into the valuation.
    assert {item["date"]: item["fill_date"] for item in fills}["2024-03-05"] == "2024-03-07"
    try:
        score_window(view=view, decisions=decisions, fills=fills, mark_date="2024-03-06")
    except ValueError as exc:
        assert "prints after the mark" in str(exc)
        assert "2024-03-07" in str(exc) and "2024-03-06" in str(exc)
    else:
        raise AssertionError("scorer applied a fill printing after the mark")


def test_scorer_rejects_benchmark_entry_after_mark(tmp_path):
    view, receipts = _rehearsal_view(tmp_path)
    state = tmp_path / "state"
    run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        dates=["2024-03-04"],
        received_at=receipts,
        recorded_at_ms=bar_close_ms("2024-03-04") + 2 * HOUR_MS,
    )
    decisions = _read_jsonl(state / "decisions.jsonl")
    fills = _read_jsonl(state / "fills.jsonl")
    assert decisions[0]["signal"] == 0  # flat: no strategy executions
    # First record is 2h after the Mar 4 close, so benchmark entry is the
    # Mar 6 open — after the Mar 4 mark. It must refuse, not price the
    # benchmark off a post-mark open.
    try:
        score_window(view=view, decisions=decisions, fills=fills, mark_date="2024-03-04")
    except ValueError as exc:
        assert "benchmark entry" in str(exc) and "after the mark" in str(exc)
    else:
        raise AssertionError("scorer entered the benchmark after the mark")


def test_scorer_reports_pending_fill_without_moving_window(tmp_path):
    view, receipts = _rehearsal_view(tmp_path)
    state = tmp_path / "state"
    run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        dates=["2024-03-05"],
        received_at=receipts,
        recorded_at_ms=bar_close_ms("2024-03-05") + 2 * HOUR_MS,
    )
    decisions = _read_jsonl(state / "decisions.jsonl")
    # No fill bar received yet: the trade is pending, and the report must
    # name when it becomes observable instead of moving the window.
    try:
        score_window(view=view, decisions=decisions, fills=[], mark_date="2024-03-05")
    except ValueError as exc:
        assert "pending until 2024-03-07" in str(exc)
    else:
        raise AssertionError("scorer accepted a traded day with a pending fill")
