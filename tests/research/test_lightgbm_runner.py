"""LightGBM shadow path: real training/inference, past-only proof, quarantine.

Proves features read only past bars, training labels complete before the
cutoff, checkpoints are pinned SHA-256 with recorded dependency versions,
inference is deterministic, per-cutoff training failures abstain (never
skip silently), and missing dependencies quarantine without fallback.
Real-inference tests require the models extra and skip cleanly without it.
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

from advisorai.models.forecasting import LightGBMForecastAdapter, ModelUnavailable
from advisorai.research.lightgbm_runner import (
    FEATURE_NAMES,
    build_lightgbm_adapter,
    lightgbm_dependency_version,
    past_only_features,
    train_cutoff_model,
)
from advisorai.research.replay import load_snapshot_bars

HAS_LIGHTGBM = importlib.util.find_spec("lightgbm") is not None
needs_lightgbm = pytest.mark.skipif(not HAS_LIGHTGBM, reason="models extra not installed")

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "research-replay"
    / "snapshots"
    / "synthetic-daily-2015-2025-v1"
)


def _closes(count: int = 400) -> tuple[float, ...]:
    price = 100.0
    out = []
    for index in range(count):
        price *= 1.0 + 0.001 * ((index % 7) - 3)
        out.append(round(price, 6))
    return tuple(out)


def test_features_are_past_only_and_require_history():
    closes = _closes()
    first = past_only_features(closes, 300)
    assert len(first) == len(FEATURE_NAMES) == 4
    # Mutating a future close cannot change features at an earlier bar.
    mutated = list(closes)
    mutated[350] *= 2.0
    assert past_only_features(tuple(mutated), 300) == first
    with pytest.raises(ValueError, match="252 bars"):
        past_only_features(closes, 100)


def test_checkpoint_hash_is_pinned_sha256():
    adapter = LightGBMForecastAdapter(runner=lambda values, horizon: values[:horizon])
    # No checkpoint hash: quarantined even with a runner present.
    with pytest.raises(ModelUnavailable, match="pinned checkpoint"):
        adapter.predict((Decimal("1"), Decimal("2")), 1)


def test_missing_dependency_quarantines_without_fallback(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "lightgbm", None)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    with pytest.raises(ModelUnavailable, match="unavailable"):
        lightgbm_dependency_version()
    adapter = LightGBMForecastAdapter(
        runner=lambda values, horizon: values[:horizon], checkpoint_hash="a" * 64
    )
    assert adapter.available is False
    with pytest.raises(ModelUnavailable, match="unavailable"):
        adapter.predict((Decimal("1"), Decimal("2")), 1)


@needs_lightgbm
def test_training_labels_complete_before_cutoff():
    closes = _closes(400)
    model_bytes, provenance = train_cutoff_model(closes, 350)
    assert len(model_bytes) > 100
    assert provenance["training_rows"] > 10
    assert provenance["dependency"].startswith("lightgbm==")
    assert provenance["seed"] == 20261114
    # Too-early cutoff: no label window completes -> quarantine, not garbage.
    with pytest.raises(ModelUnavailable, match="training rows"):
        train_cutoff_model(closes, 259)


@needs_lightgbm
def test_real_inference_is_deterministic_and_labeled():
    closes = _closes(600)
    adapter, identity = build_lightgbm_adapter(tuple(Decimal(str(v)) for v in closes))
    assert identity["candidate"] == "lightgbm"
    assert identity["revision"] == lightgbm_dependency_version()
    assert len(identity["checkpoint_hash"]) == 64
    history = tuple(Decimal(str(v)) for v in closes[:500])
    first = adapter.predict(history, 1)
    second = adapter.predict(history, 1)
    assert first == second
    assert len(first) == 1 and first[0] > 0
    # Inference used the trained model: differs from naive last-value repeat
    # on this non-flat series (not a quality claim, a ran-the-model proof).
    assert first[0] != history[-1]


@needs_lightgbm
def test_fixture_shadow_runs_real_inference_with_abstentions(tmp_path):
    if not (FIXTURE / "bars.json").exists():
        pytest.skip("synthetic fixture snapshot not present")
    from advisorai.research import ResearchRegistry
    from advisorai.research.lightgbm_runner import build_lightgbm_adapter as build
    from advisorai.research.shadow import run_shadow

    bars, _ = load_snapshot_bars(FIXTURE)

    def factory(symbol, symbol_bars):
        closes = tuple(bar["adj_close"] for bar in symbol_bars)
        return build(closes)

    report = run_shadow(
        registry=ResearchRegistry(tmp_path / "lgbm.sqlite3"),
        snapshot_root=FIXTURE,
        symbols=("SPY",),
        model_factory=factory,
    )
    assert len(report.exposures) == 1
    result = report.exposures[0]
    assert result.quarantined is False
    assert result.observations == 120
    assert result.abstentions >= 1  # early cutoffs lack training rows
    assert report.model_identity["candidate"] == "lightgbm"
    assert len(report.model_identity["checkpoint_hash"]) == 64
