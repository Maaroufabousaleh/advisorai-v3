from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from advisorai.phase4 import (
    Phase4MarketObservation,
    Phase4Prediction,
    SignalCostScenario,
    SignalPolicyFamily,
    SignalPolicySpec,
    apply_signal_policy,
    candidate_policy_specs,
    compare_policy_paths,
    evaluate_policy_signals,
    summarize_prediction_distribution,
)
from scripts.evaluate_phase4_signal_policies import _partition, _select_attempt

HASH = "a" * 64
START = datetime(2026, 8, 1, tzinfo=UTC)


def _observations(
    instruments: tuple[str, ...] = ("BTCUSDT",),
    count: int = 5,
) -> tuple[Phase4MarketObservation, ...]:
    return tuple(
        Phase4MarketObservation(
            observation_id=f"{instrument}-{index}",
            instrument=instrument,
            cutoff=START + timedelta(days=index + instrument_index * 100),
            realized_at=START + timedelta(days=index + 1 + instrument_index * 100),
            realized_return_bps=Decimal(("100", "-100", "80", "-80", "60")[index % 5]),
            spread_bps=Decimal("2"),
            slippage_bps=Decimal("2"),
            regime="trend" if index % 2 == 0 else "reversal",
            source_id="binance_spot_public_market_data",
            provider_identity="binance_spot_public_market_data",
            endpoint="https://data.example.test/public",
            source_snapshot_hash=HASH,
            phase3_admitted=True,
        )
        for instrument_index, instrument in enumerate(instruments)
        for index in range(count)
    )


def _predictions(
    observations: tuple[Phase4MarketObservation, ...],
    model_name: str = "ttm-r2",
    values: tuple[str, ...] = ("100", "-100", "80", "-80", "60"),
    confidence: str = "0.8",
) -> tuple[Phase4Prediction, ...]:
    return tuple(
        Phase4Prediction(
            observation_id=observation.observation_id,
            model_name=model_name,
            predicted_return_bps=Decimal(values[index % len(values)]),
            confidence=Decimal(confidence),
            model_code_hash=HASH,
            model_artifact_hash=HASH,
        )
        for index, observation in enumerate(observations)
    )


def test_candidate_search_space_is_small_and_typed():
    specs = candidate_policy_specs()

    assert len(specs) == 13
    assert len({spec.policy_id for spec in specs}) == len(specs)
    assert {spec.family for spec in specs} == {
        SignalPolicyFamily.SIGN_ONLY,
        SignalPolicyFamily.MAGNITUDE_THRESHOLD,
        SignalPolicyFamily.EDGE_OVER_COST,
        SignalPolicyFamily.HYSTERESIS,
        SignalPolicyFamily.COOLDOWN,
        SignalPolicyFamily.CONFIDENCE_THRESHOLD,
    }


def test_magnitude_policy_holds_low_conviction_without_outcome_access():
    observations = _observations(count=3)
    predictions = _predictions(observations, values=("40", "-100", "20"))
    policy = SignalPolicySpec(
        policy_id="magnitude-50-test",
        family=SignalPolicyFamily.MAGNITUDE_THRESHOLD,
        threshold_bps=Decimal("50"),
    )

    signals = apply_signal_policy(observations, predictions, policy)

    assert [signal.raw_position for signal in signals] == [1, -1, 1]
    assert [signal.target_position for signal in signals] == [0, -1, -1]
    assert [signal.changed for signal in signals] == [False, True, False]


def test_hysteresis_and_cooldown_are_causal_and_instrument_local():
    observations = _observations(("BTCUSDT", "ETHUSDT"), count=3)
    predictions = _predictions(observations, values=("100", "-60", "-100"))
    hysteresis = SignalPolicySpec(
        policy_id="hysteresis-test",
        family=SignalPolicyFamily.HYSTERESIS,
        threshold_bps=Decimal("50"),
        flip_threshold_bps=Decimal("80"),
    )
    cooldown = SignalPolicySpec(
        policy_id="cooldown-test",
        family=SignalPolicyFamily.COOLDOWN,
        min_hold_observations=2,
    )

    hysteresis_signals = apply_signal_policy(observations, predictions, hysteresis)
    cooldown_signals = apply_signal_policy(observations, predictions, cooldown)

    assert [signal.target_position for signal in hysteresis_signals] == [1, 1, -1, 1, 1, -1]
    assert [signal.target_position for signal in cooldown_signals] == [1, 1, -1, 1, 1, -1]
    assert hysteresis_signals[3].previous_position == 0


def test_confidence_and_edge_thresholds_use_strict_typed_rules():
    observations = _observations(count=2)
    predictions = _predictions(observations, values=("23", "50"), confidence="0.5")
    edge = SignalPolicySpec(
        policy_id="edge-test",
        family=SignalPolicyFamily.EDGE_OVER_COST,
        expected_all_in_cost_bps=Decimal("23"),
    )
    confidence = SignalPolicySpec(
        policy_id="confidence-test",
        family=SignalPolicyFamily.CONFIDENCE_THRESHOLD,
        confidence_threshold=Decimal("0.6"),
    )

    assert [
        signal.target_position for signal in apply_signal_policy(observations, predictions, edge)
    ] == [0, 1]
    assert [
        signal.target_position
        for signal in apply_signal_policy(observations, predictions, confidence)
    ] == [0, 0]


def test_policy_evaluation_decomposes_costs_and_respects_instrument_splits():
    observations = _observations(("BTCUSDT", "ETHUSDT"), count=3)
    predictions = _predictions(observations, values=("100", "-100", "80"))
    policy = SignalPolicySpec(policy_id="sign-test", family=SignalPolicyFamily.SIGN_ONLY)
    signals = apply_signal_policy(observations, predictions, policy)
    scenario = SignalCostScenario(
        scenario_id="conservative",
        fee_bps=Decimal("15"),
        spread_bps=Decimal("4"),
        slippage_bps=Decimal("4"),
    )

    metrics = evaluate_policy_signals(observations, signals, scenario)

    assert metrics.observations == 6
    assert metrics.signal_change_count == 6
    assert metrics.turnover_units == Decimal("10")
    assert metrics.estimated_cost_bps == Decimal("230")
    assert metrics.net_utility_bps == Decimal("330")
    assert {metric.label for metric in metrics.instrument_metrics} == {"BTCUSDT", "ETHUSDT"}


def test_path_comparison_exposes_primary_only_turnover_and_net():
    observations = _observations(count=3)
    primary_predictions = _predictions(observations, values=("100", "-100", "80"))
    baseline_predictions = _predictions(
        observations, model_name="lightgbm", values=("0", "0", "80")
    )
    primary_policy = SignalPolicySpec(policy_id="primary", family=SignalPolicyFamily.SIGN_ONLY)
    baseline_policy = SignalPolicySpec(policy_id="baseline", family=SignalPolicyFamily.SIGN_ONLY)
    scenario = SignalCostScenario(
        scenario_id="base",
        fee_bps=Decimal("10"),
        spread_bps=Decimal("2"),
        slippage_bps=Decimal("2"),
    )

    comparison = compare_policy_paths(
        observations,
        apply_signal_policy(observations, primary_predictions, primary_policy),
        apply_signal_policy(observations, baseline_predictions, baseline_policy),
        scenario,
    )

    assert comparison.raw_direction_disagreement_count == 2
    assert comparison.primary_active_when_baseline_flat_count == 2
    assert comparison.primary_only_turnover_units == Decimal("3")
    assert comparison.primary_only_net_utility_bps == Decimal("158")


def test_distribution_summary_requires_one_model_and_instrument():
    observations = _observations(count=3)
    predictions = _predictions(observations, values=("100", "-50", "25"))

    summary = summarize_prediction_distribution(observations, predictions)

    assert summary.absolute_prediction_p50_bps == Decimal("50")
    assert summary.positive_prediction_count == 2
    assert summary.negative_prediction_count == 1


def test_policy_contract_rejects_invalid_family_parameters():
    with pytest.raises(ValueError, match="hysteresis"):
        SignalPolicySpec(
            policy_id="bad",
            family=SignalPolicyFamily.HYSTERESIS,
            threshold_bps=Decimal("100"),
            flip_threshold_bps=Decimal("50"),
        )


def test_research_partition_keeps_final_sixteen_per_instrument_out_of_selection():
    observations = _observations(("BTCUSDT", "ETHUSDT"), count=64)

    partitions = _partition(observations)

    assert len(partitions["tuning"]) == 64
    assert len(partitions["validation"]) == 32
    assert len(partitions["holdout_consumed"]) == 32
    assert {item.instrument for item in partitions["holdout_consumed"]} == {"BTCUSDT", "ETHUSDT"}
    for instrument in ("BTCUSDT", "ETHUSDT"):
        validation = [item for item in partitions["validation"] if item.instrument == instrument]
        holdout = [item for item in partitions["holdout_consumed"] if item.instrument == instrument]
        assert max(item.cutoff for item in validation) < min(item.cutoff for item in holdout)


def test_research_selection_does_not_call_a_nonpositive_policy_selected():
    attempts = (
        {
            "policy": {"policy_id": "positive"},
            "selection_metrics": {
                "incremental_net_utility_bps": "-1",
                "ttm_r2_turnover": "1",
                "ttm_r2_net_utility_bps": "-1",
            },
        },
        {
            "policy": {"policy_id": "flat"},
            "selection_metrics": {
                "incremental_net_utility_bps": "0",
                "ttm_r2_turnover": "0",
                "ttm_r2_net_utility_bps": "0",
            },
        },
    )

    result = _select_attempt(attempts)

    assert result["status"] == "NO_DEVELOPMENT_POLICY_HAS_POSITIVE_INCREMENTAL_VALUE"
    assert result["selected_policy_id"] is None
    assert result["holdout_used"] is False
