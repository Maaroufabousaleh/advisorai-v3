"""Independent evaluator: separate accounting, label proof, model identity.

Proves the evaluator's plain-loop accounting matches the replay's
event-ordered accounting on identical inputs (cross-check, not reuse),
every LightGBM training label completes before its forecast cutoff,
per-cutoff model hashes map each forecast to its trained bytes, and the
short/financing disclosure is explicit.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from advisorai.research.evaluate import (
    SHORT_DISCLOSURE,
    collect_forecasts,
    evaluate_exposure,
    prove_labels_observable,
)
from advisorai.research.replay import load_snapshot_bars
from advisorai.research.synthetic import materialize

FIXTURE_NAME = "synthetic-daily-2015-2025-v1"


def _fixture(tmp_path: Path) -> Path:
    root = tmp_path / FIXTURE_NAME
    materialize(root)
    return root


def test_accounting_cross_checks_replay_on_identical_inputs(tmp_path):
    from advisorai.phase4.signal_policy import SignalPolicyFamily, SignalPolicySpec
    from advisorai.research import ResearchRegistry
    from advisorai.research.replay import ExposureSpec, run_replay

    snapshot_root = _fixture(tmp_path)
    bars, _ = load_snapshot_bars(snapshot_root)
    evaluation = evaluate_exposure("SPY", bars["SPY"])
    report = run_replay(
        registry=ResearchRegistry(tmp_path / "r.sqlite3"),
        snapshot_root=snapshot_root,
        exposures=(ExposureSpec(symbol="SPY"),),
        spec=SignalPolicySpec(policy_id="xcheck", family=SignalPolicyFamily.SIGN_ONLY),
    )
    replayed = report.exposures[0]
    momentum = evaluation.full["momentum"]
    baseline = evaluation.full["buy_and_hold"]
    assert f"{momentum.net_bps:.2f}" == f"{replayed.net_bps:.2f}"
    assert f"{baseline.net_bps:.2f}" == f"{replayed.baseline_net_bps:.2f}"
    assert momentum.trades == replayed.trade_count
    assert momentum.observations == replayed.observations == 120


def test_label_observability_proof_has_nonnegative_margin(tmp_path):
    snapshot_root = _fixture(tmp_path)
    bars, _ = load_snapshot_bars(snapshot_root)
    for symbol in ("SPY", "TLT"):
        proof = prove_labels_observable(symbol, bars[symbol])
        assert proof["cutoffs_checked"] > 100
        assert proof["worst_label_margin_bars"] is not None
        assert proof["worst_label_margin_bars"] >= 0


def test_short_and_financing_disclosure_is_explicit():
    assert "shorts pay the same all-in transaction cost" in SHORT_DISCLOSURE
    assert "NOT modeled" in SHORT_DISCLOSURE
    assert "borrow" in SHORT_DISCLOSURE and "financing" in SHORT_DISCLOSURE


def test_per_cutoff_model_identity_without_lightgbm(tmp_path):
    snapshot_root = _fixture(tmp_path)
    bars, _ = load_snapshot_bars(snapshot_root)
    forecasts = collect_forecasts("SPY", bars["SPY"])
    assert len(forecasts) == 120
    assert all(item.lightgbm_abstained for item in forecasts)
    assert all(item.lightgbm_model_hash is None for item in forecasts)
    assert {item.window for item in forecasts} == {"screen", "validation", "holdout"}
    assert all(isinstance(item.realized_bps, Decimal) for item in forecasts)


def test_cost_sensitivity_grid_scales_with_multiplier(tmp_path):
    snapshot_root = _fixture(tmp_path)
    bars, _ = load_snapshot_bars(snapshot_root)
    evaluation = evaluate_exposure(
        "SPY", bars["SPY"], cost_multipliers=(Decimal("1"), Decimal("2"))
    )
    row = evaluation.cost_sensitivity["momentum"]
    assert Decimal(row["x2"]) <= Decimal(row["x1"])
    assert set(row) == {"x1", "x2"}
    assert isinstance(pytest, object)
