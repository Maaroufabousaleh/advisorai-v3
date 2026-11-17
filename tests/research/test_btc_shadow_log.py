"""Forward shadow log mechanics; offline contract tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from advisorai.research.btc_shadow_log import (
    MODEL_ID,
    bar_close_ms,
    decide,
    load_view,
    log_forward_day,
    observe_fill,
)

HOUR_MS = 3_600_000


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


def _receipts(view: tuple[dict, ...]) -> dict[str, int]:
    """Honest receipts: every completed bar arrives one hour after its market close."""
    return {str(row["date"]): bar_close_ms(str(row["date"])) + HOUR_MS for row in view}


def _forward_rows() -> list[dict]:
    return (
        _day_rows("2024-03-05", 1, close="200.0", open_="200.0")
        + _day_rows("2024-03-06", 1, close="201.0", open_="207.50")
        + _day_rows("2024-03-07", 1, close="202.0", open_="209.0")
        + _day_rows("2024-03-08", 1, close="203.0", open_="210.0")
        + _day_rows("2024-03-09", 1, close="204.0", open_="211.0")
        + _day_rows("2024-03-10", 1, close="205.0", open_="212.0")
    )


def _view(tmp_path: Path, forward_rows: list[dict] | None = None) -> tuple[dict, ...]:
    snap = tmp_path / "snap"
    snap.mkdir(exist_ok=True)
    if not (snap / "bars.json").is_file():
        _write_snapshot(snap, _day_rows("2024-01-01", 64, close="100.0"))
    forward = tmp_path / "forward"
    forward.mkdir(exist_ok=True)
    _write_snapshot(forward, forward_rows if forward_rows is not None else _forward_rows())
    return load_view(snap, forward)


def test_decide_needs_formation_history():
    closes = [Decimal("100")] * 64
    with pytest.raises(ValueError, match="formation history"):
        decide(closes, 62)
    assert decide(closes, 63) == 0


def test_log_scores_two_consecutive_long_days(tmp_path):
    view = _view(tmp_path)
    assert len(view) == 70
    state = tmp_path / "state"
    receipts = _receipts(view)
    stamp = bar_close_ms("2024-03-08") + 2 * HOUR_MS
    first = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-07",
        received_at=receipts,
        recorded_at_ms=stamp,
    )
    assert first is not None and first.abstained is False
    assert (first.signal, first.position) == (1, 1)
    assert first.model_id == MODEL_ID
    assert first.recorded_at_ms == stamp
    second = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-08",
        received_at=receipts,
        recorded_at_ms=stamp,
    )
    assert second is not None and second.abstained is False
    assert (second.signal, second.position) == (1, 1)
    lines = (state / "decisions.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["abstained"] is False for line in lines)


def test_missing_past_day_is_recorded_as_missed(tmp_path):
    view = _view(tmp_path, _day_rows("2024-03-05", 1, close="200.0"))
    state = tmp_path / "state"
    receipts = _receipts(view)
    missing = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-06",
        received_at=receipts,
        recorded_at_ms=bar_close_ms("2024-03-07") + HOUR_MS,
    )
    assert missing is not None
    assert missing.abstained is True
    assert missing.missed is True
    assert "not a qualified bar" in missing.reason
    with pytest.raises(ValueError, match="not a received bar"):
        observe_fill(
            view,
            "2024-03-06",
            received_at=receipts,
            recorded_at_ms=bar_close_ms("2024-03-08"),
            decision_recorded_at_ms=missing.recorded_at_ms,
        )


def test_future_day_returns_none_without_recording(tmp_path):
    view = _view(tmp_path)
    state = tmp_path / "state"
    result = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-20",
        received_at=_receipts(view),
        recorded_at_ms=bar_close_ms("2024-03-10") + HOUR_MS,
    )
    assert result is None
    assert not (state / "decisions.jsonl").is_file()


def test_log_quarantines_short_history_and_gapped_handoff(tmp_path):
    view = _view(tmp_path, _day_rows("2024-03-05", 1))
    state = tmp_path / "state"
    early = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-01-10",
        received_at=_receipts(view),
        recorded_at_ms=bar_close_ms("2024-03-05") + HOUR_MS,
    )
    assert early is not None and early.abstained is True
    assert early.missed is False
    assert "formation history" in early.reason
    gapped = tmp_path / "gapped"
    gapped.mkdir()
    _write_snapshot(gapped, _day_rows("2024-03-06", 1))
    with pytest.raises(ValueError, match="handoff gap"):
        load_view(tmp_path / "snap", gapped)


def test_view_rejects_duplicate_dates(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    _write_snapshot(snap, _day_rows("2024-01-01", 64))
    forward = tmp_path / "forward"
    forward.mkdir()
    _write_snapshot(forward, _day_rows("2024-03-04", 2))
    with pytest.raises(ValueError, match="duplicate"):
        load_view(snap, forward)


def test_fill_uses_first_price_after_decision_time(tmp_path):
    view = _view(tmp_path)
    receipts = _receipts(view)
    decided = bar_close_ms("2024-03-06") + 2 * HOUR_MS
    fill = observe_fill(
        view,
        "2024-03-05",
        received_at=receipts,
        recorded_at_ms=bar_close_ms("2024-03-08") + 2 * HOUR_MS,
        decision_recorded_at_ms=decided,
    )
    assert fill.date == "2024-03-05"
    assert fill.fill_date == "2024-03-08"
    assert fill.fill_price == "210.0"
    assert fill.observable_after == "2024-03-08"
    with pytest.raises(ValueError, match="not a received bar"):
        observe_fill(
            view,
            "2024-03-11",
            received_at=receipts,
            recorded_at_ms=bar_close_ms("2024-03-11"),
            decision_recorded_at_ms=decided,
        )
    with pytest.raises(ValueError, match="at or before the actual decision time"):
        observe_fill(
            view,
            "2024-03-05",
            received_at=receipts,
            recorded_at_ms=bar_close_ms("2024-03-12"),
            decision_recorded_at_ms=bar_close_ms("2024-03-12"),
        )


def test_fill_rejects_unreceived_fill_bar(tmp_path):
    view = _view(tmp_path)
    decided = bar_close_ms("2024-03-05") + 2 * HOUR_MS
    recorded = bar_close_ms("2024-03-07") + 2 * HOUR_MS
    late = _receipts(view)
    late["2024-03-07"] = recorded + 24 * HOUR_MS
    with pytest.raises(ValueError, match="not received by the fill record time"):
        observe_fill(
            view,
            "2024-03-04",
            received_at=late,
            recorded_at_ms=recorded,
            decision_recorded_at_ms=decided,
        )
    missing = _receipts(view)
    del missing["2024-03-07"]
    with pytest.raises(ValueError, match="not received by the fill record time"):
        observe_fill(
            view,
            "2024-03-04",
            received_at=missing,
            recorded_at_ms=recorded,
            decision_recorded_at_ms=decided,
        )


def test_fill_bar_received_before_close_rejected(tmp_path):
    view = _view(tmp_path)
    receipts = _receipts(view)
    receipts["2024-03-07"] = bar_close_ms("2024-03-07") - 1
    with pytest.raises(ValueError, match="received before its close"):
        observe_fill(
            view,
            "2024-03-04",
            received_at=receipts,
            recorded_at_ms=bar_close_ms("2024-03-07") + 2 * HOUR_MS,
            decision_recorded_at_ms=bar_close_ms("2024-03-05") + 2 * HOUR_MS,
        )


def test_decision_uses_only_received_bars(tmp_path):
    view = _view(tmp_path)
    state = tmp_path / "state"
    receipts = _receipts(view)
    stamp = bar_close_ms("2024-03-05") + 2 * HOUR_MS
    flat = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-04",
        received_at=receipts,
        recorded_at_ms=stamp,
    )
    assert flat is not None and flat.abstained is False
    assert (flat.signal, flat.position) == (0, 0)
    assert flat.received_through == "2024-03-10"
    record = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-05",
        received_at=receipts,
        recorded_at_ms=stamp,
    )
    assert record is not None and record.abstained is False
    assert (record.signal, record.position) == (1, 1)
    assert record.bars_hash == "f" * 64
    assert record.received_through == "2024-03-10"
    assert record.recorded_at_ms == stamp


def test_late_formation_input_quarantines(tmp_path):
    view = _view(tmp_path)
    state = tmp_path / "state"
    stamp = bar_close_ms("2024-03-05") + 2 * HOUR_MS
    late = _receipts(view)
    late["2024-01-15"] = stamp + 24 * HOUR_MS
    quarantined = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-05",
        received_at=late,
        recorded_at_ms=stamp,
    )
    assert quarantined is not None and quarantined.abstained is True
    assert quarantined.missed is False
    assert "received after the record time" in quarantined.reason
    missing = _receipts(view)
    del missing["2024-02-10"]
    quarantined_missing = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-05",
        received_at=missing,
        recorded_at_ms=stamp,
    )
    assert quarantined_missing is not None and quarantined_missing.abstained is True
    assert "no receipt timestamp" in quarantined_missing.reason


def test_decision_then_fill_end_to_end_fixture(tmp_path):
    """Decisions record at their own closes; each fill prices at the first
    open printed after its decision was recorded, once that bar arrives."""
    view = _view(tmp_path, _day_rows("2024-03-05", 1, close="200.0"))
    state = tmp_path / "state"
    receipts = _receipts(view)
    decided = bar_close_ms("2024-03-05") + 2 * HOUR_MS
    fourth = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-04",
        received_at=receipts,
        recorded_at_ms=decided,
    )
    fifth = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=state,
        date="2024-03-05",
        received_at=receipts,
        recorded_at_ms=decided,
    )
    assert fourth is not None and fourth.signal == 0
    assert fifth is not None and fifth.signal == 1
    with pytest.raises(ValueError, match="at or before the actual decision time"):
        observe_fill(
            view,
            "2024-03-05",
            received_at=receipts,
            recorded_at_ms=decided,
            decision_recorded_at_ms=decided,
        )
    stored = [json.loads(line) for line in (state / "decisions.jsonl").read_text().splitlines()]
    assert [entry["signal"] for entry in stored] == [0, 1]
    assert "fill" not in json.dumps(stored)
    view2 = _view(tmp_path)
    receipts2 = _receipts(view2)
    fill = observe_fill(
        view2,
        "2024-03-05",
        received_at=receipts2,
        recorded_at_ms=bar_close_ms("2024-03-07") + 2 * HOUR_MS,
        decision_recorded_at_ms=decided,
    )
    assert fill.date == "2024-03-05"
    assert fill.fill_date == "2024-03-07"
    assert fill.fill_price == "209.0"
    assert fill.observable_after == "2024-03-07"


def test_fill_priced_before_decision_time_rejected(tmp_path):
    """March 9 decision / March 6 open: the fill printed before the decision
    was actually recorded, so it must be rejected, never recorded."""
    snap = tmp_path / "snap"
    snap.mkdir()
    _write_snapshot(snap, _day_rows("2024-01-01", 64, close="100.0"))
    forward = tmp_path / "forward"
    forward.mkdir()
    _write_snapshot(
        forward,
        _day_rows("2024-03-05", 1, close="200.0")
        + _day_rows("2024-03-06", 1, close="201.0", open_="207.50"),
    )
    view = load_view(snap, forward)
    receipts = _receipts(view)
    recorded_march_9 = 1710000000000
    with pytest.raises(ValueError, match="at or before the actual decision time"):
        observe_fill(
            view,
            "2024-03-05",
            received_at=receipts,
            recorded_at_ms=recorded_march_9,
            decision_recorded_at_ms=recorded_march_9,
        )


def test_fill_skips_stale_prices_after_late_decision(tmp_path):
    """A decision recorded March 9 fills at the March 10 open, never the
    stale March 6 open."""
    view = _view(tmp_path)
    receipts = _receipts(view)
    recorded_march_9 = 1710000000000
    fill = observe_fill(
        view,
        "2024-03-05",
        received_at=receipts,
        recorded_at_ms=bar_close_ms("2024-03-10") + 2 * HOUR_MS,
        decision_recorded_at_ms=recorded_march_9,
    )
    assert fill.fill_date == "2024-03-10"
    assert fill.fill_price == "212.0"


def test_bar_received_before_close_rejected(tmp_path):
    """A completed bar cannot arrive before it closed; such input quarantines."""
    view = _view(tmp_path)
    receipts = _receipts(view)
    receipts["2024-03-05"] = bar_close_ms("2024-03-05") - 1
    record = log_forward_day(
        view=view,
        forward_hash="f" * 64,
        state_dir=tmp_path / "state",
        date="2024-03-05",
        received_at=receipts,
        recorded_at_ms=bar_close_ms("2024-03-05") + 2 * HOUR_MS,
    )
    assert record is not None and record.abstained is True
    assert "received before its close" in record.reason
