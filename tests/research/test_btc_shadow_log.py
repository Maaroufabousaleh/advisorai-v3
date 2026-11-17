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
)


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
    first = log_forward_day(view=view, forward_hash="f" * 64, state_dir=state, date="2024-03-07")
    assert first.abstained is False
    assert (first.signal, first.position) == (1, 1)
    assert first.model_id == MODEL_ID
    second = log_forward_day(view=view, forward_hash="f" * 64, state_dir=state, date="2024-03-08")
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
    missing = log_forward_day(view=view, forward_hash="f" * 64, state_dir=state, date="2024-03-06")
    assert missing.abstained is True
    assert "not a qualified bar" in missing.reason
    early = log_forward_day(view=view, forward_hash="f" * 64, state_dir=state, date="2024-01-10")
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
