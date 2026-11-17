"""Forward shadow log mechanics; offline contract tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from advisorai.research.btc_shadow_log import (
    MODEL_ID,
    decide,
    load_view,
    log_forward_day,
    observe_fill,
)

RECEIVED_MS = 1700000000000
RECORDED_MS = 1710000000000
LATE_MS = 1720000000000


def _day_rows(start: str, count: int, close: str = "100.0", open_: str = "100.0") -> list[dict]:
    base = datetime.fromisoformat(start).date()
    return [
        {
            "date": (base + timedelta(days=i)).isoformat(),
            "open": open_,
            "high": close,
            "low": open_,
            "adj_close": close,
            "volume": "10.0",
        }
        for i in range(count)
    ]


def _write_snapshot(root: Path, rows: list[dict]) -> None:
    import hashlib

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


def _receipts(view: tuple[dict, ...], at_ms: int = RECEIVED_MS) -> dict[str, int]:
    return {str(row["date"]): at_ms for row in view}


def test_decide_needs_formation_history():
    closes = [Decimal("100")] * 64
    with pytest.raises(ValueError, match="formation history"):
        decide(closes, 62)
    assert decide(closes, 63) == 0


def test_log_scores_two_consecutive_long_days(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    rows = _day_rows("2024-01-01", 64, close="100.0")
    rows += _day_rows("2024-03-05", 2, close="200.0")
    _write_snapshot(snap, rows)
    forward = tmp_path / "forward"
    forward.mkdir()
    _write_snapshot(forward, _day_rows("2024-03-07", 2, close="200.0", open_="205.0"))
    view = load_view(snap, forward)
    assert len(view) == 68
    state = tmp_path / "state"
    receipts = _receipts(view)
    first = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-07",
        received_at=receipts,
        recorded_at_ms=RECORDED_MS,
    )
    assert first.abstained is False
    assert (first.signal, first.position) == (1, 1)
    assert first.model_id == MODEL_ID
    assert first.recorded_at_ms == RECORDED_MS
    second = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-08",
        received_at=receipts,
        recorded_at_ms=RECORDED_MS,
    )
    assert second.abstained is False
    assert (second.signal, second.position) == (1, 1)
    lines = (state / "decisions.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["abstained"] is False for line in lines)


def test_log_quarantines_unknown_and_gapped_days(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    _write_snapshot(snap, _day_rows("2024-01-01", 64))
    forward = tmp_path / "forward"
    forward.mkdir()
    _write_snapshot(forward, _day_rows("2024-03-05", 1))
    view = load_view(snap, forward)
    state = tmp_path / "state"
    receipts = _receipts(view)
    missing = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-06",
        received_at=receipts,
        recorded_at_ms=RECORDED_MS,
    )
    assert missing.abstained is True
    assert "not a qualified bar" in missing.reason
    early = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-01-10",
        received_at=receipts,
        recorded_at_ms=RECORDED_MS,
    )
    assert early.abstained is True
    assert "formation history" in early.reason
    gapped = tmp_path / "gapped"
    gapped.mkdir()
    _write_snapshot(gapped, _day_rows("2024-03-06", 1))
    with pytest.raises(ValueError, match="handoff gap"):
        load_view(snap, gapped)


def test_view_rejects_duplicate_dates(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    _write_snapshot(snap, _day_rows("2024-01-01", 64))
    forward = tmp_path / "forward"
    forward.mkdir()
    _write_snapshot(forward, _day_rows("2024-03-04", 2))
    with pytest.raises(ValueError, match="duplicate"):
        load_view(snap, forward)


def test_fill_requires_next_bar_received(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    _write_snapshot(snap, _day_rows("2024-01-01", 64, close="100.0"))
    forward = tmp_path / "forward"
    forward.mkdir()
    _write_snapshot(forward, _day_rows("2024-03-05", 1, close="200.0", open_="205.0"))
    view = load_view(snap, forward)
    receipts = _receipts(view)
    with pytest.raises(ValueError, match="not yet observable"):
        observe_fill(view, "2024-03-05", received_at=receipts, recorded_at_ms=RECORDED_MS)
    fill = observe_fill(view, "2024-03-04", received_at=receipts, recorded_at_ms=RECORDED_MS)
    assert fill.fill_date == "2024-03-05"
    assert fill.fill_price == "205.0"
    assert fill.observable_after == "2024-03-05"
    assert fill.recorded_at_ms == RECORDED_MS
    with pytest.raises(ValueError, match="not a received bar"):
        observe_fill(view, "2024-03-06", received_at=receipts, recorded_at_ms=RECORDED_MS)
    unordered = (
        {"date": "2024-03-05", "open": "200.0"},
        {"date": "2024-03-04", "open": "100.0"},
    )
    with pytest.raises(ValueError, match="precedes recorded decision"):
        observe_fill(unordered, "2024-03-05", received_at=receipts, recorded_at_ms=RECORDED_MS)


def test_fill_rejects_unreceived_fill_bar(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    _write_snapshot(snap, _day_rows("2024-01-01", 64, close="100.0"))
    forward = tmp_path / "forward"
    forward.mkdir()
    _write_snapshot(forward, _day_rows("2024-03-05", 1, close="200.0", open_="205.0"))
    view = load_view(snap, forward)
    late = _receipts(view)
    late["2024-03-05"] = LATE_MS
    with pytest.raises(ValueError, match="not received by the fill record time"):
        observe_fill(view, "2024-03-04", received_at=late, recorded_at_ms=RECORDED_MS)
    missing = _receipts(view)
    del missing["2024-03-05"]
    with pytest.raises(ValueError, match="not received by the fill record time"):
        observe_fill(view, "2024-03-04", received_at=missing, recorded_at_ms=RECORDED_MS)


def test_decision_uses_only_received_bars(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    _write_snapshot(snap, _day_rows("2024-01-01", 64, close="100.0"))
    forward = tmp_path / "forward"
    forward.mkdir()
    _write_snapshot(forward, _day_rows("2024-03-05", 1, close="200.0"))
    view = load_view(snap, forward)
    state = tmp_path / "state"
    receipts = _receipts(view)
    flat = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-04",
        received_at=receipts,
        recorded_at_ms=RECORDED_MS,
    )
    assert flat.abstained is False
    assert (flat.signal, flat.position) == (0, 0)
    assert flat.received_through == "2024-03-05"
    record = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-05",
        received_at=receipts,
        recorded_at_ms=RECORDED_MS,
    )
    assert record.abstained is False
    assert (record.signal, record.position) == (1, 1)
    assert record.bars_hash == "f" * 64
    assert record.received_through == "2024-03-05"
    assert record.recorded_at_ms == RECORDED_MS


def test_late_formation_input_quarantines(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    _write_snapshot(snap, _day_rows("2024-01-01", 64, close="100.0"))
    forward = tmp_path / "forward"
    forward.mkdir()
    _write_snapshot(forward, _day_rows("2024-03-05", 1, close="200.0"))
    view = load_view(snap, forward)
    state = tmp_path / "state"
    late = _receipts(view)
    late["2024-01-15"] = LATE_MS
    quarantined = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-05",
        received_at=late,
        recorded_at_ms=RECORDED_MS,
    )
    assert quarantined.abstained is True
    assert "received after the record time" in quarantined.reason
    missing = _receipts(view)
    del missing["2024-02-10"]
    quarantined_missing = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-05",
        received_at=missing,
        recorded_at_ms=RECORDED_MS,
    )
    assert quarantined_missing.abstained is True
    assert "no receipt timestamp" in quarantined_missing.reason


def test_decision_then_fill_end_to_end_fixture(tmp_path):
    """Timestamped: the 2024-03-04 and 2024-03-05 decisions use their own
    completed closes (opposite signals) with receipts pinned; the 2024-03-05
    fill records only after 2024-03-06 arrives with its own receipt."""
    snap = tmp_path / "snap"
    snap.mkdir()
    _write_snapshot(snap, _day_rows("2024-01-01", 64, close="100.0"))
    forward = tmp_path / "forward"
    forward.mkdir()
    _write_snapshot(forward, _day_rows("2024-03-05", 1, close="200.0"))
    view = load_view(snap, forward)
    state = tmp_path / "state"
    receipts = _receipts(view)
    prior = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-04",
        received_at=receipts,
        recorded_at_ms=RECORDED_MS,
    )
    assert (prior.signal, prior.position) == (0, 0)
    record = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-05",
        received_at=receipts,
        recorded_at_ms=RECORDED_MS,
    )
    assert record.abstained is False
    assert (record.date, record.signal, record.position) == ("2024-03-05", 1, 1)
    assert record.received_through == "2024-03-05"
    assert record.recorded_at_ms == RECORDED_MS
    with pytest.raises(ValueError, match="not yet observable"):
        observe_fill(view, "2024-03-05", received_at=receipts, recorded_at_ms=RECORDED_MS)
    stored = [json.loads(line) for line in (state / "decisions.jsonl").read_text().splitlines()]
    assert [entry["signal"] for entry in stored] == [0, 1]
    assert [entry["recorded_at_ms"] for entry in stored] == [RECORDED_MS, RECORDED_MS]
    assert "fill" not in json.dumps(stored)
    rows = _day_rows("2024-03-05", 1, close="200.0") + _day_rows(
        "2024-03-06", 1, close="201.0", open_="207.50"
    )
    _write_snapshot(forward, rows)
    view2 = load_view(snap, forward)
    receipts2 = _receipts(view2)
    fill = observe_fill(view2, "2024-03-05", received_at=receipts2, recorded_at_ms=RECORDED_MS)
    assert fill.date == "2024-03-05"
    assert fill.fill_date == "2024-03-06"
    assert fill.fill_price == "207.50"
    assert fill.observable_after == "2024-03-06"
    assert fill.recorded_at_ms == RECORDED_MS
