from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from advisorai.phase4 import (
    V3_CORE_BASELINES,
    V3_CORE_CANDIDATES,
    V3CoreBar,
    V3CoreCadencePolicy,
    V3CoreEvaluationInput,
    V3CorePhase4Preregistration,
    build_v3core_cases,
    derive_regime_from_context,
)

HASH = "a" * 64
START = datetime(2026, 8, 5, 1, 5, tzinfo=UTC)
SOURCE_ID = "binance_spot_public_market_data"
ENDPOINT = "https://api.binance.com/api/v3/klines"


def _bars(
    instrument: str = "BTCUSDT",
    *,
    available_offset: timedelta = timedelta(0),
    count: int = 60,
) -> tuple[V3CoreBar, ...]:
    values: list[V3CoreBar] = []
    for index in range(count):
        close = Decimal("10000") + Decimal(index)
        at = START + timedelta(minutes=5 * index)
        values.append(
            V3CoreBar(
                instrument=instrument,
                interval_end=at,
                available_at=at + available_offset,
                provider_event_at=at,
                open=close - Decimal("1"),
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=Decimal("1"),
                source_id=SOURCE_ID,
                provider_identity=SOURCE_ID,
                endpoint=ENDPOINT,
                source_snapshot_hash=HASH,
            )
        )
    return tuple(values)


def test_v3_core_cadence_is_fixed_and_preregistered():
    policy = V3CoreCadencePolicy()
    plan = V3CorePhase4Preregistration()

    assert policy.observations_per_decision == 12
    assert policy.observations_per_context == 48
    assert policy.universe == ("BTCUSDT", "ETHUSDT")
    assert plan.candidate_models == V3_CORE_CANDIDATES
    assert plan.mandatory_baselines == V3_CORE_BASELINES
    assert plan.latency_delays_seconds == (0, 300, 600, 900, 1800, 3600)
    assert plan.network_calls is False
    assert plan.credentials_loaded is False
    assert plan.order_writes_attempted is False


def test_builder_creates_one_causal_case_from_contiguous_context_and_outcome():
    result = build_v3core_cases(
        _bars(),
        source_id=SOURCE_ID,
        provider_identity=SOURCE_ID,
        endpoint=ENDPOINT,
        source_snapshot_hash=HASH,
        phase3_admitted=True,
    )

    assert len(result.cases) == 1
    case = result.cases[0]
    assert len(case.context_bars) == 48
    assert len(case.future_bars) == 12
    assert case.cutoff == datetime(2026, 8, 5, 5, tzinfo=UTC)
    assert case.context_bars[-1].interval_end == case.cutoff
    assert case.future_bars[0].interval_end == case.cutoff + timedelta(minutes=5)
    assert case.realized_at == datetime(2026, 8, 5, 6, tzinfo=UTC)
    assert case.phase3_admitted is True


def test_builder_retains_missing_or_unavailable_cutoffs_instead_of_filling_them():
    bars = list(_bars())
    bars.pop(48)
    result = build_v3core_cases(
        bars,
        source_id=SOURCE_ID,
        provider_identity=SOURCE_ID,
        endpoint=ENDPOINT,
        source_snapshot_hash=HASH,
        phase3_admitted=True,
    )

    assert not result.cases
    assert any(item.reason == "missing_one_hour_outcome_bars" for item in result.rejected_cutoffs)

    late = _bars(available_offset=timedelta(minutes=1))
    late_result = build_v3core_cases(
        late,
        source_id=SOURCE_ID,
        provider_identity=SOURCE_ID,
        endpoint=ENDPOINT,
        source_snapshot_hash=HASH,
        phase3_admitted=True,
    )
    assert not late_result.cases
    assert any(
        item.reason == "context_not_available_at_cutoff" for item in late_result.rejected_cutoffs
    )


def test_builder_rejects_source_switches_and_duplicate_bars():
    bars = list(_bars())
    with pytest.raises(ValueError, match="source identity"):
        build_v3core_cases(
            [*bars, bars[-1].model_copy(update={"source_id": "other"})],
            source_id=SOURCE_ID,
            provider_identity=SOURCE_ID,
            endpoint=ENDPOINT,
            source_snapshot_hash=HASH,
        )

    with pytest.raises(ValueError, match="duplicate"):
        build_v3core_cases(
            [*bars, bars[-1]],
            source_id=SOURCE_ID,
            provider_identity=SOURCE_ID,
            endpoint=ENDPOINT,
            source_snapshot_hash=HASH,
        )


def test_case_validation_binds_outcome_to_future_bars_and_phase3_input():
    build = build_v3core_cases(
        _bars(),
        source_id=SOURCE_ID,
        provider_identity=SOURCE_ID,
        endpoint=ENDPOINT,
        source_snapshot_hash=HASH,
        phase3_admitted=True,
    )
    case = build.cases[0]
    with pytest.raises(ValueError, match="realized return"):
        case.model_validate({**case.model_dump(mode="python"), "realized_return_bps": Decimal("0")})

    typed = V3CoreEvaluationInput(
        plan_id="phase4-v3-core-1h-5m-v1",
        phase3_gate_record_sha256=HASH,
        build=build,
    )
    assert typed.case_counts() == {"BTCUSDT": 1, "ETHUSDT": 0}
    assert not typed.meets_minimum()

    not_admitted = build_v3core_cases(
        _bars(),
        source_id=SOURCE_ID,
        provider_identity=SOURCE_ID,
        endpoint=ENDPOINT,
        source_snapshot_hash=HASH,
        phase3_admitted=False,
    )
    with pytest.raises(ValueError, match="Phase-3 admission"):
        V3CoreEvaluationInput(
            plan_id="phase4-v3-core-1h-5m-v1",
            phase3_gate_record_sha256=HASH,
            build=not_admitted,
        )


def test_regime_uses_context_only():
    context = tuple(Decimal("10000") + Decimal(20 * index) for index in range(48))
    assert derive_regime_from_context(context) == "trend_up"
    with pytest.raises(ValueError, match="positive finite"):
        derive_regime_from_context((Decimal("0"), Decimal("1")))
