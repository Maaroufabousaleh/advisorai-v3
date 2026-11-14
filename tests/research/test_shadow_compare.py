"""Shadow comparison: model path vs baseline on identical windows and costs.

Proves the typed Forecast path covers the replay cutoffs exactly, abstention
is recorded (never silent), missing weight identities quarantine instead of
substituting, and shadow numerics reproduce from the frozen snapshot.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from advisorai.models.forecasting import (
    AbstentionPolicy,
    LinearForecaster,
    NaiveForecaster,
)
from advisorai.research import ResearchRegistry
from advisorai.research.replay import ExposureSpec, evaluate_momentum, load_snapshot_bars
from advisorai.research.shadow import (
    SHADOW_MODEL_IDENTITY,
    reference_baseline_model,
    run_shadow,
    run_shadow_exposure,
    shadow_forecasts,
)


def _bars(count: int = 300) -> tuple[dict, ...]:
    from datetime import date, timedelta

    base = date(2020, 1, 2)
    rows = []
    current = base
    added = 0
    while added < count:
        if current.weekday() < 5:
            price = Decimal(100 + added)
            rows.append(
                {
                    "date": current.isoformat(),
                    "open": price,
                    "high": price + Decimal("1"),
                    "low": price - Decimal("1"),
                    "adj_close": price,
                    "volume": 1000,
                }
            )
            added += 1
        current += timedelta(days=1)
    return tuple(rows)


def test_shadow_forecasts_cover_replay_cutoffs_exactly():
    bars = _bars()
    forecasts, stats = shadow_forecasts("FIX", bars, model=LinearForecaster())
    momentum = evaluate_momentum(bars)
    from advisorai.research.replay import build_observations

    observations, _ = build_observations(
        "FIX", bars, momentum, factor_hash="0" * 64, snapshot_hash="0" * 64
    )
    assert len(forecasts) == len(observations) > 0
    assert {item.cutoff.date().isoformat() for item in forecasts} == {
        item.cutoff.date().isoformat() for item in observations
    }
    assert all(not item.abstained for item in forecasts)
    assert stats["latency_p50_ms"] >= 0 and stats["peak_ram_mib"] > 0


def test_forced_abstention_is_recorded_not_silent(tmp_path):
    bars = _bars()
    registry = ResearchRegistry(tmp_path / "shadow.sqlite3")
    result = run_shadow_exposure(
        registry,
        ExposureSpec(
            symbol="FIX",
            bars=bars,
            fee_bps=Decimal("1"),
            spread_bps=Decimal("1"),
            slippage_bps=Decimal("2"),
        ),
        model=LinearForecaster(),
        model_identity=dict(SHADOW_MODEL_IDENTITY),
        factor_id=__import__("uuid").uuid4(),
        snapshot_hash="a" * 64,
        code_hash="b" * 64,
        environment_hash="c" * 64,
        abstention_policy=AbstentionPolicy(max_scale=Decimal("0"), min_history=10**9),
    )
    assert result.abstentions == result.observations > 0
    assert result.quarantined is False
    stored = registry.get(result.experiment_id)
    assert stored is not None and stored.metrics["abstentions"] == str(result.observations)


def test_missing_weight_identity_quarantines_without_substitution(tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    rows = [
        {
            "date": row["date"],
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "adj_close": float(row["adj_close"]),
            "volume": row["volume"],
        }
        for row in _bars()
    ]
    (snapshot / "bars.json").write_text(json.dumps({"FIX": rows}))
    registry = ResearchRegistry(tmp_path / "shadow-q.sqlite3")
    report = run_shadow(
        registry=registry,
        snapshot_root=snapshot,
        symbols=("FIX",),
        model=LinearForecaster(),
        require_checkpoint=True,
    )
    assert len(report.exposures) == 1
    assert report.exposures[0].quarantined is True
    assert "no pinned repository_id" in (report.exposures[0].quarantine_reason or "")
    assert report.exposures[0].experiment_id is None
    assert registry.count("experiment") == 0


def test_reference_adapter_is_untouched_naive():
    assert isinstance(reference_baseline_model(), NaiveForecaster)


def test_shadow_reproduces_on_fixture_snapshot(tmp_path):
    snapshot_root = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "research-replay"
        / "snapshots"
        / "synthetic-daily-2015-2025-v1"
    )
    if not (snapshot_root / "bars.json").exists():
        import pytest

        pytest.skip("synthetic fixture snapshot not present")
    _, snapshot_hash = load_snapshot_bars(snapshot_root)
    first = run_shadow(
        registry=ResearchRegistry(tmp_path / "a.sqlite3"),
        snapshot_root=snapshot_root,
        symbols=("SPY", "TLT"),
        model=LinearForecaster(),
    )
    second = run_shadow(
        registry=ResearchRegistry(tmp_path / "b.sqlite3"),
        snapshot_root=snapshot_root,
        symbols=("SPY", "TLT"),
        model=LinearForecaster(),
    )
    assert first.snapshot_hash == second.snapshot_hash == snapshot_hash
    for left, right in zip(first.exposures, second.exposures, strict=True):
        assert left.symbol == right.symbol
        assert left.net_utility_bps == right.net_utility_bps
        assert left.baseline_net_bps == right.baseline_net_bps
    # Preregistered shape: 120 cutoffs per exposure, zero abstentions unforced.
    assert [item.observations for item in first.exposures] == [120, 120]
    assert [item.abstentions for item in first.exposures] == [0, 0]
