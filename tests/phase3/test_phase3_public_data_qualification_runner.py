from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.run_phase3_public_data_qualification import _terminal_sample_due, run_qualification

TARGET = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)


def test_terminal_sample_is_not_marked_before_window_boundary():
    assert not _terminal_sample_due(TARGET - timedelta(microseconds=1), TARGET, False)


def test_terminal_sample_is_marked_once_at_or_after_boundary():
    assert _terminal_sample_due(TARGET, TARGET, False)
    assert _terminal_sample_due(TARGET + timedelta(seconds=1), TARGET, False)
    assert not _terminal_sample_due(TARGET + timedelta(seconds=1), TARGET, True)


def test_resumed_run_rejects_changed_max_cycles_and_hydrates_without_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def fake_collect(_source: object, _cycle_root: Path, _window_seconds: int):
        return (
            {
                "required_read_state": "failed",
                "server_time": {"status": "failed"},
                "markets": {},
            },
            {
                "state": "failed",
                "connections": [],
                "resubscription": {"subscription_acknowledgements": 0},
            },
            TARGET,
        )

    monkeypatch.setattr(
        "scripts.run_phase3_public_data_qualification._collect_source_window", fake_collect
    )
    root = tmp_path / "resumable"
    kwargs = {
        "duration_hours": 1,
        "cycle_seconds": 0.01,
        "window_seconds": 1,
        "real": True,
        "max_cycles": 1,
    }

    first = run_qualification(root, **kwargs)
    samples_before = (root / "samples.jsonl").read_bytes()
    health_before = (root / "health-transitions.jsonl").read_bytes()
    assert first["sample_count"] == 6

    with pytest.raises(RuntimeError, match="max_cycles"):
        run_qualification(root, **{**kwargs, "max_cycles": 2})
    assert (root / "samples.jsonl").read_bytes() == samples_before
    assert (root / "health-transitions.jsonl").read_bytes() == health_before

    resumed = run_qualification(root, **kwargs)
    assert resumed["sample_count"] == first["sample_count"]
    assert (root / "samples.jsonl").read_bytes() == samples_before
    assert (root / "health-transitions.jsonl").read_bytes() == health_before
    assert json.loads((root / "config.json").read_text())["max_cycles"] == 1
