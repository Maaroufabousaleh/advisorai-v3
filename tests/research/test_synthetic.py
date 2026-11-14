"""Synthetic fixture generator: byte-identical, hash-pinned, count-checked.

Proves the generator reproduces the exact committed bytes (SHA-256 pinned),
materializes a loadable snapshot, and detects drift instead of silently
changing full-run numerics.
"""

from __future__ import annotations

import hashlib

import pytest

from advisorai.research.replay import load_snapshot_bars
from advisorai.research.synthetic import (
    EXPECTED_COUNTS,
    EXPECTED_SHA256,
    bars_payload,
    generate_bars,
    materialize,
    verify_bytes,
)


def test_generator_reproduces_pinned_bytes():
    payload = bars_payload()
    assert hashlib.sha256(payload).hexdigest() == EXPECTED_SHA256
    assert verify_bytes(payload) == EXPECTED_SHA256
    bars = generate_bars()
    assert {symbol: len(rows) for symbol, rows in bars.items()} == EXPECTED_COUNTS
    assert EXPECTED_COUNTS == {"SPY": 2869, "TLT": 2869}


def test_generator_is_deterministic_across_calls():
    assert bars_payload() == bars_payload()


def test_verify_rejects_drifted_bytes():
    with pytest.raises(ValueError, match="generator drift"):
        verify_bytes(bars_payload().replace(b"2015-01-02", b"2015-01-03", 1))


def test_materialized_snapshot_loads_with_pinned_hash(tmp_path):
    root = tmp_path / "synthetic-daily-2015-2025-v1"
    bars_path = materialize(root)
    assert bars_path == root / "bars.json"
    assert (root / "manifest.json").exists()
    bars, snapshot_hash = load_snapshot_bars(root)
    assert snapshot_hash == EXPECTED_SHA256
    assert sorted(bars) == ["SPY", "TLT"]
