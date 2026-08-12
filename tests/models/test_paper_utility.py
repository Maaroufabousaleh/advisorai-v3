from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from advisorai.phase4 import (
    MANDATORY_BASELINES,
    Phase4MarketObservation,
    Phase4Prediction,
    Phase4PrerequisiteError,
    Phase4UtilityPolicy,
    build_preparation_manifest,
    evaluate_paper_utility,
)

HASH = "a" * 64
START = datetime(2026, 8, 1, 12, tzinfo=UTC)


def _observations(*, admitted: bool = True) -> tuple[Phase4MarketObservation, ...]:
    returns = (Decimal("10"), Decimal("-10"), Decimal("10"))
    return tuple(
        Phase4MarketObservation(
            observation_id=f"obs-{index}",
            instrument="BTCUSDT",
            cutoff=START + timedelta(minutes=index),
            realized_at=START + timedelta(minutes=index + 1),
            realized_return_bps=value,
            spread_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            regime="trend" if index != 1 else "reversal",
            source_id="binance-public",
            provider_identity="binance-public",
            endpoint="https://data.example.test/public",
            source_snapshot_hash=HASH,
            phase3_admitted=admitted,
        )
        for index, value in enumerate(returns)
    )


def _predictions(
    observations: tuple[Phase4MarketObservation, ...],
    model_name: str,
    values: tuple[Decimal, ...],
) -> tuple[Phase4Prediction, ...]:
    return tuple(
        Phase4Prediction(
            observation_id=observation.observation_id,
            model_name=model_name,
            predicted_return_bps=value,
            confidence=Decimal("0.8"),
            interval_lower_bps=Decimal("-100"),
            interval_upper_bps=Decimal("100"),
            model_code_hash=HASH,
            model_artifact_hash=HASH,
        )
        for observation, value in zip(observations, values, strict=True)
    )


def test_phase4_preparation_manifest_is_closed_and_lists_current_roster():
    manifest = build_preparation_manifest(
        policy=Phase4UtilityPolicy(minimum_observations=3),
        generated_at=START,
    )

    assert manifest["state"] == "ready_for_admitted_input"
    assert manifest["phase3_admission_required"] is True
    assert manifest["phase4_admission_opened"] is False
    assert manifest["mandatory_baselines"] == list(MANDATORY_BASELINES)
    assert manifest["execution_authority"]["model_order_authority"] is False


def test_phase4_utility_refuses_unadmitted_phase3_observations():
    observations = _observations(admitted=False)

    with pytest.raises(Phase4PrerequisiteError, match="Phase-3 admission"):
        evaluate_paper_utility(observations, ())


def test_phase4_utility_measures_costs_regimes_and_incremental_value():
    observations = _observations()
    policy = Phase4UtilityPolicy(minimum_observations=3, fee_bps=Decimal("1"))
    predictions = [
        _predictions(observations, name, (Decimal("0"), Decimal("0"), Decimal("0")))
        for name in MANDATORY_BASELINES
    ]
    predictions.append(
        _predictions(observations, "ttm-r3", (Decimal("5"), Decimal("-5"), Decimal("5")))
    )

    report = evaluate_paper_utility(
        observations,
        tuple(item for group in predictions for item in group),
        policy=policy,
        phase3_gate_record_hash=HASH,
        evaluated_at=START,
    )

    result = next(item for item in report.results if item.model_name == "ttm-r3")
    assert report.phase4_admission_opened is False
    assert result.trade_count == 3
    assert result.abstention_count == 0
    assert result.mae_bps == Decimal("5")
    assert result.rmse_bps == Decimal("5")
    assert result.directional_accuracy == Decimal("1")
    assert result.calibration_coverage == Decimal("1")
    assert result.confidence_brier_score == Decimal("0.04")
    assert result.net_utility_bps == Decimal("25")
    assert result.strongest_baseline_net_utility_bps == Decimal("0")
    assert result.incremental_net_utility_bps == Decimal("25")
    assert result.adds_marginal_value is True
    assert {slice_.regime for slice_ in result.regime_slices} == {"trend", "reversal"}


def test_phase4_utility_requires_all_mandatory_baselines():
    observations = _observations()
    predictions = _predictions(observations, "ttm-r3", (Decimal("1"),) * 3)

    with pytest.raises(ValueError, match="mandatory baseline"):
        evaluate_paper_utility(
            observations,
            predictions,
            phase3_gate_record_hash=HASH,
            policy=Phase4UtilityPolicy(minimum_observations=3),
        )


def test_phase4_utility_rejects_duplicate_prediction_identity():
    observations = _observations()
    baseline = _predictions(observations, MANDATORY_BASELINES[0], (Decimal("0"),) * 3)

    with pytest.raises(ValueError, match="duplicate model prediction"):
        evaluate_paper_utility(
            observations,
            (*baseline, baseline[0]),
            phase3_gate_record_hash=HASH,
            policy=Phase4UtilityPolicy(minimum_observations=3),
        )
