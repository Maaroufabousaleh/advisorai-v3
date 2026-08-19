from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from advisorai.phase4 import (
    MANDATORY_BASELINES,
    Phase4MarketObservation,
    Phase4Prediction,
    Phase4UtilityPolicy,
    evaluate_paper_utility,
)

HASH = "a" * 64
START = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


def _development_observations() -> tuple[Phase4MarketObservation, ...]:
    observations = []
    for index in range(64):
        for instrument in ("BTCUSDT", "ETHUSDT"):
            offset = index * 2 + (0 if instrument == "BTCUSDT" else 1)
            cutoff = START + timedelta(hours=offset)
            realized_return = Decimal("100") if index % 2 == 0 else Decimal("-100")
            observations.append(
                Phase4MarketObservation(
                    observation_id=f"{instrument}:{cutoff.isoformat()}",
                    instrument=instrument,
                    cutoff=cutoff,
                    realized_at=cutoff + timedelta(hours=1),
                    realized_return_bps=realized_return,
                    spread_bps=Decimal("2"),
                    slippage_bps=Decimal("2"),
                    regime="trend_up" if index % 2 == 0 else "trend_down",
                    source_id="binance_spot_public_market_data",
                    provider_identity="binance_spot_public_market_data",
                    endpoint="https://data-api.binance.vision/api/v3/klines",
                    source_snapshot_hash=HASH,
                    phase3_admitted=True,
                )
            )
    return tuple(observations)


def _predictions(
    observations: tuple[Phase4MarketObservation, ...], model: str, *, accurate: bool
) -> tuple[Phase4Prediction, ...]:
    return tuple(
        Phase4Prediction(
            observation_id=observation.observation_id,
            model_name=model,
            predicted_return_bps=(observation.realized_return_bps if accurate else Decimal("0")),
            confidence=Decimal("0.8"),
            interval_lower_bps=Decimal("-100"),
            interval_upper_bps=Decimal("100"),
            model_code_hash=HASH,
            model_artifact_hash=HASH,
        )
        for observation in observations
    )


def _complete_input() -> tuple[tuple[Phase4MarketObservation, ...], tuple[Phase4Prediction, ...]]:
    observations = _development_observations()
    predictions = [
        prediction
        for model in MANDATORY_BASELINES
        for prediction in _predictions(observations, model, accurate=False)
    ]
    predictions.extend(_predictions(observations, "chronos-2-small", accurate=True))
    return observations, tuple(predictions)


def test_128_case_development_fixture_enforces_common_coverage() -> None:
    observations, predictions = _complete_input()
    report = evaluate_paper_utility(
        observations,
        predictions,
        policy=Phase4UtilityPolicy(minimum_observations=30),
        phase3_gate_record_hash=HASH,
    )
    assert len(observations) == 128
    assert {item.instrument for item in observations} == {"BTCUSDT", "ETHUSDT"}
    assert len(predictions) == 128 * (len(MANDATORY_BASELINES) + 1)
    candidate = next(item for item in report.results if item.model_name == "chronos-2-small")
    assert candidate.observations == 128
    assert candidate.adds_marginal_value is True


def test_missing_candidate_row_refuses_materialization() -> None:
    observations, predictions = _complete_input()
    missing = next(
        item
        for item in predictions
        if item.model_name == "chronos-2-small" and item.observation_id.startswith("BTCUSDT:")
    )
    with pytest.raises(ValueError, match="complete observation set"):
        evaluate_paper_utility(
            observations,
            tuple(item for item in predictions if item is not missing),
            policy=Phase4UtilityPolicy(minimum_observations=30),
            phase3_gate_record_hash=HASH,
        )


def test_duplicate_candidate_row_refuses_materialization() -> None:
    observations, predictions = _complete_input()
    duplicate = next(item for item in predictions if item.model_name == "chronos-2-small")
    with pytest.raises(ValueError, match="duplicate model prediction"):
        evaluate_paper_utility(
            observations,
            (*predictions, duplicate),
            policy=Phase4UtilityPolicy(minimum_observations=30),
            phase3_gate_record_hash=HASH,
        )
