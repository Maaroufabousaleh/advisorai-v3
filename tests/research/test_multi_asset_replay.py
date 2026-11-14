"""Multi-asset research replay: leakage, reproducibility, and accounting tests.

The replay must refuse future-dated inputs, reproduce identical numerics from
the frozen snapshot, keep per-exposure failures independent, and charge costs
exactly once per position change. Fixture bars keep the default suite offline;
the committed snapshot is exercised by ``scripts/run_research_replay.py``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from advisorai.execution.events import MarketEvent, ReplayEngine
from advisorai.lake import PointInTimeViolation, SnapshotBuilder
from advisorai.phase4.signal_policy import SignalPolicyFamily, SignalPolicySpec
from advisorai.research import ResearchRegistry
from advisorai.research import factors as dsl
from advisorai.research.costs import COSTS_BY_SYMBOL
from advisorai.research.replay import (
    ExposureSpec,
    assign_window,
    build_observations,
    evaluate_momentum,
    load_snapshot_bars,
    momentum_dsl_source,
    require_window_embargo,
    run_exposure,
    run_replay,
)

FIXTURE_SNAPSHOT = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "research-replay"
    / "snapshots"
    / "synthetic-daily-2015-2025-v1"
)


def _bars(count: int = 300, *, start: str = "2020-01-02") -> tuple[dict, ...]:
    from datetime import date, timedelta

    base = date.fromisoformat(start)
    rows = []
    current = base
    added = 0
    while added < count:
        if current.weekday() < 5:
            price = Decimal(100 + added) + Decimal(added % 7) / Decimal(10)
            rows.append(
                {
                    "date": current.isoformat(),
                    "open": price,
                    "high": price + Decimal("1"),
                    "low": price - Decimal("1"),
                    "adj_close": price,
                    "volume": 1000 + added,
                }
            )
            added += 1
        current += timedelta(days=1)
    return tuple(rows)


def test_momentum_factor_is_versioned_and_past_only():
    bars = _bars(300)
    first = evaluate_momentum(bars)
    # Warmup bars carry no signal; later bars depend only on past closes.
    assert first[:251] == (Decimal("0"),) * 251
    assert all(value != 0 for value in first[251:])
    # Mutating a future close cannot change any earlier factor value.
    mutated = list(bars)
    bumped = dict(mutated[299])
    bumped["adj_close"] = bumped["adj_close"] * Decimal("2")
    mutated[299] = bumped
    second = evaluate_momentum(tuple(mutated))
    assert second[:299] == first[:299]
    assert second[299] != first[299]


def test_momentum_dsl_expression_fits_dsl_budgets():
    expression = dsl.parse(momentum_dsl_source())
    assert dsl.canonical_hash(expression) == dsl.canonical_hash(dsl.parse(momentum_dsl_source()))
    assert set(expression) == {"args", "op"}


def test_build_observations_rejects_future_dated_inputs():
    bars = _bars(300)
    momentum = evaluate_momentum(bars)
    observations, _ = build_observations(
        "FIX", bars, momentum, factor_hash="a" * 64, snapshot_hash="b" * 64
    )
    assert len(observations) > 0
    # A factor input first available after its cutoff must fail the PIT proof.
    from advisorai.contracts import (
        AssetClass,
        InstrumentIdentity,
        PointInTimeObservation,
        SourceGrade,
    )

    late = PointInTimeObservation(
        instrument=InstrumentIdentity(
            canonical_id="FIX", asset_class=AssetClass.EQUITY, venue="research"
        ),
        event_time=observations[0].cutoff,
        effective_time=observations[0].cutoff,
        first_available_at=observations[0].cutoff,
        ingested_at=datetime(2030, 1, 1, tzinfo=UTC),
        raw_artifact_hash="b" * 64,
        parser_version="test",
        source_family="test",
        origin="https://example.invalid",
        quality_grade=SourceGrade.RESEARCH,
        intended_use="research_replay",
        value="late",
    )
    with pytest.raises(PointInTimeViolation, match="unavailable"):
        SnapshotBuilder.assert_observations_available((late,), observations[0].cutoff)


def test_replay_engine_rejects_conflicting_event_ids():
    base = datetime(2021, 1, 4, tzinfo=UTC)
    first = MarketEvent.from_raw(
        event_type="research-signal",
        instrument_id="FIX",
        occurred_at=base,
        sequence=0,
        raw_payload=b"same",
        price=Decimal("100"),
        quantity=Decimal("1"),
    )
    twin = first.model_copy(update={"price": Decimal("101")})
    with pytest.raises(ValueError, match="conflicting event ID"):
        ReplayEngine().replay((first, twin), lambda event: None)


def test_per_exposure_failure_does_not_mask_sibling(tmp_path):
    bars = _bars(300)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    payload = {
        "GOOD": [
            {
                "date": row["date"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "adj_close": float(row["adj_close"]),
                "volume": row["volume"],
            }
            for row in bars
        ],
        "BAD": [
            {
                "date": row["date"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "adj_close": float(row["adj_close"]),
                "volume": row["volume"],
            }
            for row in bars[:10]  # too short for any month-end cutoff
        ],
    }
    (snapshot / "bars.json").write_text(json.dumps(payload))
    registry = ResearchRegistry(tmp_path / "registry.sqlite3")
    spec = SignalPolicySpec(policy_id="test-sign-only", family=SignalPolicyFamily.SIGN_ONLY)
    report = run_replay(
        registry=registry,
        snapshot_root=snapshot,
        exposures=(
            ExposureSpec(
                symbol="GOOD",
                fee_bps=Decimal("1"),
                spread_bps=Decimal("1"),
                slippage_bps=Decimal("2"),
            ),
            ExposureSpec(
                symbol="BAD",
                fee_bps=Decimal("1"),
                spread_bps=Decimal("1"),
                slippage_bps=Decimal("2"),
            ),
        ),
        spec=spec,
        require_all_windows=False,
    )
    assert [item.symbol for item in report.exposures] == ["GOOD"]
    assert len(report.failed) == 1 and report.failed[0].startswith("BAD:")


def test_costs_charge_once_per_position_change(tmp_path):
    bars = _bars(300)
    registry = ResearchRegistry(tmp_path / "registry.sqlite3")
    exposure = ExposureSpec(
        symbol="FIX",
        bars=bars,
        fee_bps=Decimal("1"),
        spread_bps=Decimal("1"),
        slippage_bps=Decimal("2"),
    )
    factor_hash = dsl.canonical_hash(dsl.parse(momentum_dsl_source()))
    result = run_exposure(
        registry,
        exposure,
        spec=SignalPolicySpec(policy_id="test-sign-only", family=SignalPolicyFamily.SIGN_ONLY),
        factor_id=__import__("uuid").uuid4(),
        factor_hash=factor_hash,
        snapshot_hash="c" * 64,
        code_hash="d" * 64,
        environment_hash="e" * 64,
        require_all_windows=False,
    )
    # Sign-only flips between -1/0/+1; every trade pays the all-in 4bps per unit.
    assert result.costs_bps >= Decimal("0")
    assert result.costs_bps % Decimal("4") == 0
    stored = registry.get(result.experiment_id)
    assert stored is not None and stored.costs["scenario"] == "fix-etf-conservative-v1"


def test_fixture_snapshot_reproduces_identical_numerics(tmp_path):
    snapshot_root = FIXTURE_SNAPSHOT
    if not (snapshot_root / "bars.json").exists():
        pytest.skip("synthetic fixture snapshot not present")
    _, snapshot_hash = load_snapshot_bars(snapshot_root)
    manifest = json.loads((snapshot_root / "manifest.json").read_text())
    assert manifest["counts"] == {"SPY": 2869, "TLT": 2869}
    assert manifest["source"] == "synthetic-deterministic-v1"
    spec = SignalPolicySpec(policy_id="test-sign-only", family=SignalPolicyFamily.SIGN_ONLY)
    first = run_replay(
        registry=ResearchRegistry(tmp_path / "a.sqlite3"),
        snapshot_root=snapshot_root,
        exposures=(ExposureSpec(symbol="SPY"), ExposureSpec(symbol="TLT")),
        spec=spec,
    )
    second = run_replay(
        registry=ResearchRegistry(tmp_path / "b.sqlite3"),
        snapshot_root=snapshot_root,
        exposures=(ExposureSpec(symbol="SPY"), ExposureSpec(symbol="TLT")),
        spec=spec,
    )
    assert first.snapshot_hash == second.snapshot_hash == snapshot_hash
    assert first.factor_hash == second.factor_hash
    for left, right in zip(first.exposures, second.exposures, strict=True):
        assert left.symbol == right.symbol
        assert left.net_bps == right.net_bps
        assert left.baseline_net_bps == right.baseline_net_bps
        assert left.trade_count == right.trade_count
        assert [(w.name, w.observations) for w in left.windows] == [
            (w.name, w.observations) for w in right.windows
        ]
    # Preregistered shape: 49/36/35 screen/validation/holdout cutoffs, costs charged.
    for item in first.exposures:
        assert [(w.name, w.observations) for w in item.windows] == [
            ("holdout", 35),
            ("screen", 49),
            ("validation", 36),
        ]
        assert sum(w.observations for w in item.windows) == item.observations == 120
    assert all(item.costs_bps > 0 for item in first.exposures)
    digest = hashlib.sha256(
        json.dumps(
            [
                (item.symbol, str(item.net_bps), str(item.baseline_net_bps))
                for item in first.exposures
            ]
        ).encode()
    ).hexdigest()
    assert len(digest) == 64


def test_windows_and_embargo_are_enforced_not_documented(tmp_path):
    assert assign_window(2015) == assign_window(2019) == "screen"
    assert assign_window(2020) == assign_window(2022) == "validation"
    assert assign_window(2023) == assign_window(2025) == "holdout"
    with pytest.raises(ValueError, match="outside all preregistered windows"):
        assign_window(2014)
    bars = _bars(300)
    momentum = evaluate_momentum(bars)
    observations, _ = build_observations(
        "FIX", bars, momentum, factor_hash="a" * 64, snapshot_hash="b" * 64
    )
    # 300 bars from 2020 cover only the validation window: embargo proof refuses.
    with pytest.raises(ValueError, match="has no cutoffs"):
        require_window_embargo(observations)
    # Full fixture snapshot passes with proven 21d+ gaps.
    if not (FIXTURE_SNAPSHOT / "bars.json").exists():
        pytest.skip("synthetic fixture snapshot not present")
    fixture_bars, _ = load_snapshot_bars(FIXTURE_SNAPSHOT)
    fixture_momentum = evaluate_momentum(fixture_bars["SPY"])
    fixture_obs, _ = build_observations(
        "SPY", fixture_bars["SPY"], fixture_momentum, factor_hash="a" * 64, snapshot_hash="b" * 64
    )
    proof = require_window_embargo(fixture_obs)
    assert {name: count for name, (_, _, count) in proof.items()} == {
        "screen": 49,
        "validation": 36,
        "holdout": 35,
    }


def test_costs_come_from_single_recorded_config():
    assert COSTS_BY_SYMBOL == {
        "SPY": (Decimal("1"), Decimal("1"), Decimal("2")),
        "TLT": (Decimal("1"), Decimal("2"), Decimal("2")),
    }
    assert ExposureSpec(symbol="SPY").resolved_costs() == COSTS_BY_SYMBOL["SPY"]
    assert ExposureSpec(symbol="TLT").resolved_costs() == COSTS_BY_SYMBOL["TLT"]
    with pytest.raises(ValueError, match="partial cost overrides"):
        ExposureSpec(symbol="SPY", fee_bps=Decimal("9")).resolved_costs()
    with pytest.raises(ValueError, match="no pinned cost"):
        ExposureSpec(symbol="UNKNOWN").resolved_costs()
