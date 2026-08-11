from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from advisorai.collectors.public_market_data import reviewed_public_market_data_sources
from scripts.run_phase3_public_data_qualification import (
    _clock_adjusted_event_ages,
    _collect_source_window,
    _terminal_sample_due,
    run_qualification,
)

TARGET = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)


def test_direct_entrypoint_help_resolves_repository_imports_without_pythonpath():
    repository_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, "scripts/run_phase3_public_data_qualification.py", "--help"],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "Run durable, append-only public-data qualification" in completed.stdout
    assert completed.stderr == ""


def test_terminal_sample_is_not_marked_before_window_boundary():
    assert not _terminal_sample_due(TARGET - timedelta(microseconds=1), TARGET, False)


def test_terminal_sample_is_marked_once_at_or_after_boundary():
    assert _terminal_sample_due(TARGET, TARGET, False)
    assert _terminal_sample_due(TARGET + timedelta(seconds=1), TARGET, False)
    assert not _terminal_sample_due(TARGET + timedelta(seconds=1), TARGET, True)


def test_clock_adjusted_event_ages_never_persist_negative_durations():
    ages, future_count = _clock_adjusted_event_ages((0.2, -0.4, 1.1), 0.1)

    assert ages == pytest.approx((0.3, 0.0, 1.2))
    assert future_count == 1


def test_clock_adjusted_event_ages_are_unmeasured_without_clock_offset():
    ages, future_count = _clock_adjusted_event_ages((0.2, 1.1), None)

    assert ages == ()
    assert future_count == 0


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


def test_binance_source_observation_uses_window_end_before_close_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    measured_at = TARGET + timedelta(seconds=3)

    monkeypatch.setattr(
        "scripts.run_phase3_public_data_qualification._run_rest",
        lambda *_args: {
            "required_read_state": "pass",
            "server_time": {"status": "pass", "clock_offset_seconds": 0},
            "markets": {},
        },
    )
    monkeypatch.setattr(
        "scripts.run_phase3_public_data_qualification._run_binance_public_ws",
        lambda *_args: {"measurement_ended_at": measured_at.isoformat()},
    )

    source = next(
        item
        for item in reviewed_public_market_data_sources()
        if item.source_id == "binance_spot_public_market_data"
    )
    _rest, _websocket, observed_at = _collect_source_window(source, tmp_path, 1)

    assert observed_at == measured_at
