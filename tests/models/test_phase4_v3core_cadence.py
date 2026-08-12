from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from advisorai.phase4 import (
    V3_CORE_BASELINES,
    V3_CORE_CANDIDATES,
    V3CoreBar,
    V3CoreBarProvenance,
    V3CoreCadencePolicy,
    V3CoreEvaluationInput,
    V3CoreMarketDataSurface,
    V3CorePhase4Preregistration,
    build_v3core_cases,
    derive_regime_from_context,
)

HASH = "a" * 64
RAW_HASH = "b" * 64
NORMALIZED_HASH = "c" * 64
CONTRACT_HASH = "d" * 64
START = datetime(2026, 8, 5, 1, 5, tzinfo=UTC)
SOURCE_ID = "binance_spot_public_market_data"
ENDPOINT = "https://data-api.binance.vision/api/v3/klines"


def _bars(
    instrument: str = "BTCUSDT",
    *,
    collected_offset: timedelta = timedelta(0),
    evidence_class: str = "forward_pit_admission",
    count: int = 60,
) -> tuple[V3CoreBar, ...]:
    values: list[V3CoreBar] = []
    for index in range(count):
        close = Decimal("10000") + Decimal(index)
        at = START + timedelta(minutes=5 * index)
        values.append(
            V3CoreBar(
                instrument=instrument,
                provenance=V3CoreBarProvenance(
                    interval_end=at,
                    provider_available_at=at,
                    collected_at=(
                        at + collected_offset
                        if evidence_class == "forward_pit_admission"
                        else at + timedelta(days=30)
                    ),
                    provider_event_at=at,
                    availability_basis=(
                        "forward_observed"
                        if evidence_class == "forward_pit_admission"
                        else "historical_backfill"
                    ),
                    evidence_class=evidence_class,
                    source_snapshot_hash=HASH,
                    raw_record_hash=RAW_HASH,
                    normalized_record_hash=NORMALIZED_HASH,
                    source_health_state="HEALTHY",
                    historical_availability_contract_id=(
                        "binance-public-klines-close-semantics-v1"
                        if evidence_class == "historical_development"
                        else None
                    ),
                    historical_availability_contract_sha256=(
                        CONTRACT_HASH if evidence_class == "historical_development" else None
                    ),
                ),
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
    assert plan.market_data_rest_endpoint == ENDPOINT
    assert plan.market_data_websocket_endpoint == "wss://data-stream.binance.vision/ws"
    assert plan.network_calls is False
    assert plan.credentials_loaded is False
    assert plan.order_writes_attempted is False


def test_builder_creates_one_causal_case_from_contiguous_context_and_outcome():
    result = build_v3core_cases(
        _bars(),
        evidence_class="forward_pit_admission",
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
        evidence_class="forward_pit_admission",
        source_id=SOURCE_ID,
        provider_identity=SOURCE_ID,
        endpoint=ENDPOINT,
        source_snapshot_hash=HASH,
        phase3_admitted=True,
    )

    assert not result.cases
    assert any(item.reason == "missing_one_hour_outcome_bars" for item in result.rejected_cutoffs)

    late = _bars(collected_offset=timedelta(minutes=1))
    late_result = build_v3core_cases(
        late,
        evidence_class="forward_pit_admission",
        source_id=SOURCE_ID,
        provider_identity=SOURCE_ID,
        endpoint=ENDPOINT,
        source_snapshot_hash=HASH,
        phase3_admitted=True,
    )
    assert not late_result.cases
    assert any(
        item.reason == "context_not_collected_at_cutoff" for item in late_result.rejected_cutoffs
    )


def test_builder_rejects_source_switches_and_duplicate_bars():
    bars = list(_bars())
    with pytest.raises(ValueError, match="source identity"):
        build_v3core_cases(
            [*bars, bars[-1].model_copy(update={"source_id": "other"})],
            evidence_class="forward_pit_admission",
            source_id=SOURCE_ID,
            provider_identity=SOURCE_ID,
            endpoint=ENDPOINT,
            source_snapshot_hash=HASH,
        )

    with pytest.raises(ValueError, match="duplicate"):
        build_v3core_cases(
            [*bars, bars[-1]],
            evidence_class="forward_pit_admission",
            source_id=SOURCE_ID,
            provider_identity=SOURCE_ID,
            endpoint=ENDPOINT,
            source_snapshot_hash=HASH,
        )


def test_case_validation_binds_outcome_to_future_bars_and_phase3_input():
    build = build_v3core_cases(
        _bars(),
        evidence_class="forward_pit_admission",
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
        plan_id="phase4-v3-core-1h-5m-v2",
        phase3_gate_record_sha256=HASH,
        build=build,
    )
    assert typed.case_counts() == {"BTCUSDT": 1, "ETHUSDT": 0}
    assert not typed.meets_minimum()

    not_admitted = build_v3core_cases(
        _bars(),
        evidence_class="forward_pit_admission",
        source_id=SOURCE_ID,
        provider_identity=SOURCE_ID,
        endpoint=ENDPOINT,
        source_snapshot_hash=HASH,
        phase3_admitted=False,
    )
    with pytest.raises(ValueError, match="Phase-3 admission"):
        V3CoreEvaluationInput(
            plan_id="phase4-v3-core-1h-5m-v2",
            phase3_gate_record_sha256=HASH,
            build=not_admitted,
        )


def test_regime_uses_context_only():
    context = tuple(Decimal("10000") + Decimal(20 * index) for index in range(48))
    assert derive_regime_from_context(context) == "trend_up"
    with pytest.raises(ValueError, match="positive finite"):
        derive_regime_from_context((Decimal("0"), Decimal("1")))


@pytest.mark.parametrize(
    "rest_base_url",
    (
        "https://api.binance.com",
        "https://testnet.binance.vision",
        "https://market-data.example.test",
        "http://data-api.binance.vision",
    ),
)
def test_v3core_market_data_surface_rejects_non_reviewed_rest_hosts(rest_base_url):
    with pytest.raises(ValueError, match="market-data-only host"):
        V3CoreMarketDataSurface(rest_base_url=rest_base_url)


@pytest.mark.parametrize(
    "websocket_url",
    (
        "wss://stream.binance.com:9443/ws",
        "wss://testnet.binance.vision/ws",
        "wss://stream.example.test/ws",
        "https://data-stream.binance.vision/ws",
    ),
)
def test_v3core_market_data_surface_rejects_non_reviewed_websocket_hosts(websocket_url):
    with pytest.raises(ValueError, match="market-data-only host"):
        V3CoreMarketDataSurface(websocket_url=websocket_url)


def test_v3core_market_data_surface_rejects_credentials_or_write_capability():
    with pytest.raises(ValueError, match="credential-free"):
        V3CoreMarketDataSurface(credentials_required=True)
    with pytest.raises(ValueError, match="credential-free"):
        V3CoreMarketDataSurface(write_capability=True)
    with pytest.raises(ValueError, match="credential-free"):
        V3CoreMarketDataSurface(market_data_only=False)


def test_historical_backfill_is_distinct_and_uses_reviewed_provider_availability():
    result = build_v3core_cases(
        _bars(evidence_class="historical_development"),
        evidence_class="historical_development",
        source_id=SOURCE_ID,
        provider_identity=SOURCE_ID,
        endpoint=ENDPOINT,
        source_snapshot_hash=HASH,
        phase3_admitted=True,
    )

    assert len(result.cases) == 1
    case = result.cases[0]
    assert case.evidence_class == "historical_development"
    assert case.context_bars[-1].collected_at > case.cutoff
    assert case.context_bars[-1].provider_available_at <= case.cutoff
    typed = V3CoreEvaluationInput(
        plan_id="phase4-v3-core-1h-5m-v1",
        phase3_gate_record_sha256=HASH,
        build=result,
    )
    assert typed.is_forward_admission_input() is False


def test_backfilled_collection_timestamp_cannot_masquerade_as_forward_pit():
    result = build_v3core_cases(
        _bars(collected_offset=timedelta(days=30)),
        evidence_class="forward_pit_admission",
        source_id=SOURCE_ID,
        provider_identity=SOURCE_ID,
        endpoint=ENDPOINT,
        source_snapshot_hash=HASH,
        phase3_admitted=True,
    )

    assert not result.cases
    assert any(item.reason == "context_not_collected_at_cutoff" for item in result.rejected_cutoffs)


def test_historical_bar_requires_availability_contract_and_cannot_use_forward_basis():
    provenance = _bars(evidence_class="historical_development")[0].provenance
    with pytest.raises(ValueError, match="availability contract"):
        V3CoreBarProvenance(
            **{
                **provenance.model_dump(mode="python"),
                "historical_availability_contract_id": None,
            }
        )
    with pytest.raises(ValueError, match="historical_backfill"):
        V3CoreBarProvenance(
            **{
                **provenance.model_dump(mode="python"),
                "availability_basis": "forward_observed",
            }
        )
