from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scripts.run_phase3_public_data_qualification import _terminal_sample_due

TARGET = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)


def test_terminal_sample_is_not_marked_before_window_boundary():
    assert not _terminal_sample_due(TARGET - timedelta(microseconds=1), TARGET, False)


def test_terminal_sample_is_marked_once_at_or_after_boundary():
    assert _terminal_sample_due(TARGET, TARGET, False)
    assert _terminal_sample_due(TARGET + timedelta(seconds=1), TARGET, False)
    assert not _terminal_sample_due(TARGET + timedelta(seconds=1), TARGET, True)
