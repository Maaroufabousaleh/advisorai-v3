from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from advisorai.gates import GateDecision, GateEvidence, GateEvidenceKind, PhaseGateRecord
from advisorai.phase4 import MANDATORY_BASELINES, Phase4MarketObservation, Phase4Prediction
from scripts.review_phase4_utility import (
    Phase4ReviewPolicy,
    Phase4ReviewRefused,
    _coverage_metrics,
    _delayed,
    _prediction_index,
    _rolling_calibration,
    review_phase4,
)
from scripts.run_phase4_paper_utility import INPUT_SCHEMA, run_evaluation

HASH = "a" * 64
START = datetime(2026, 8, 1, 12, tzinfo=UTC)


def _observations(count_per_symbol: int = 16) -> tuple[Phase4MarketObservation, ...]:
    values: list[Phase4MarketObservation] = []
    for instrument_index, instrument in enumerate(("BTCUSDT", "ETHUSDT")):
        for index in range(count_per_symbol):
            cutoff = START + timedelta(days=index + instrument_index * 100)
            values.append(
                Phase4MarketObservation(
                    observation_id=f"{instrument}-{index}",
                    instrument=instrument,
                    cutoff=cutoff,
                    realized_at=cutoff + timedelta(days=1),
                    realized_return_bps=Decimal("10") if index % 2 == 0 else Decimal("-10"),
                    spread_bps=Decimal("2"),
                    slippage_bps=Decimal("2"),
                    regime="range" if index % 3 else "trend_up",
                    source_id="binance_spot_public_market_data",
                    provider_identity="binance_spot_public_market_data",
                    endpoint="https://api.binance.com/api/v3/klines",
                    source_snapshot_hash=HASH,
                    phase3_admitted=True,
                )
            )
    return tuple(values)


def _predictions(
    observations: tuple[Phase4MarketObservation, ...],
    model_name: str,
    value: Decimal = Decimal("1"),
) -> tuple[Phase4Prediction, ...]:
    return tuple(
        Phase4Prediction(
            observation_id=observation.observation_id,
            model_name=model_name,
            predicted_return_bps=value,
            confidence=Decimal("0.5"),
            model_code_hash=HASH,
            model_artifact_hash=HASH,
        )
        for observation in observations
    )


def _single_instrument_observations(
    realized: tuple[str, ...],
) -> tuple[Phase4MarketObservation, ...]:
    base = _observations(len(realized))[: len(realized)]
    return tuple(
        observation.model_copy(update={"realized_return_bps": Decimal(value)})
        for observation, value in zip(base, realized, strict=True)
    )


def _predictions_for_values(
    observations: tuple[Phase4MarketObservation, ...],
    values: tuple[str, ...],
    model_name: str = "ttm-r2",
) -> tuple[Phase4Prediction, ...]:
    return tuple(
        prediction
        for prediction in (
            Phase4Prediction(
                observation_id=observation.observation_id,
                model_name=model_name,
                predicted_return_bps=Decimal(value),
                confidence=Decimal("0.5"),
                model_code_hash=HASH,
                model_artifact_hash=HASH,
            )
            for observation, value in zip(observations, values, strict=True)
        )
    )


def _phase3_gate(path: Path) -> None:
    evidence = GateEvidence(
        name="phase3-source",
        kind=GateEvidenceKind.OPERATIONAL,
        passed=True,
        artifact_hash=HASH,
        source="/evidence/phase3.json",
        verified_by="test",
        observed_at=START,
    )
    path.write_text(
        PhaseGateRecord(
            phase=3,
            name="Phase 3",
            decision=GateDecision.PASSED,
            required_evidence=(evidence.name,),
            evidence=(evidence,),
            prerequisite_phase=2,
            recorded_by="test",
            recorded_at=START,
        ).model_dump_json(),
        encoding="utf-8",
    )


def test_rolling_calibration_uses_only_prior_residuals():
    observations = _observations(4)[:4]
    predictions = _predictions(observations, "ttm-r2", Decimal("0"))
    calibrated, stats = _rolling_calibration(
        observations,
        predictions,
        Phase4ReviewPolicy(minimum_calibration_history=2),
    )

    by_id = {item.observation_id: item for item in calibrated}
    assert by_id[observations[0].observation_id].interval_lower_bps is None
    assert by_id[observations[1].observation_id].interval_lower_bps is None
    assert by_id[observations[2].observation_id].interval_lower_bps == Decimal("-10")
    assert by_id[observations[2].observation_id].interval_upper_bps == Decimal("10")
    assert stats["ttm-r2"]["past_only"] is True
    assert stats["ttm-r2"]["derived_interval_count"] == 2


@pytest.mark.parametrize(
    ("realized", "predicted", "expected_width"),
    [
        (("10", "10", "10", "10"), ("0", "0", "0", "0"), Decimal("10")),
        (("-10", "-10", "-10", "-10"), ("0", "0", "0", "0"), Decimal("10")),
        (("0", "10", "-20", "40"), ("0", "0", "0", "0"), Decimal("10")),
        (("0", "0", "0", "0"), ("0", "0", "0", "0"), Decimal("0")),
    ],
)
def test_rolling_calibration_uses_nonnegative_absolute_history(
    realized: tuple[str, ...],
    predicted: tuple[str, ...],
    expected_width: Decimal,
):
    observations = _single_instrument_observations(realized)
    predictions = _predictions_for_values(observations, predicted)

    calibrated, stats = _rolling_calibration(
        observations,
        predictions,
        Phase4ReviewPolicy(minimum_calibration_history=2),
    )
    point = next(
        item for item in calibrated if item.observation_id == observations[2].observation_id
    )

    assert point.interval_lower_bps == point.predicted_return_bps - expected_width
    assert point.interval_upper_bps == point.predicted_return_bps + expected_width
    assert point.interval_lower_bps <= point.predicted_return_bps <= point.interval_upper_bps
    assert stats["ttm-r2"]["residual_definition"].startswith("abs(")


def test_rolling_calibration_is_deterministic_before_minimum_history_and_at_first_eligible():
    observations = _single_instrument_observations(("10", "20", "30"))
    predictions = _predictions_for_values(observations, ("0", "0", "0"))

    calibrated, stats = _rolling_calibration(
        observations,
        predictions,
        Phase4ReviewPolicy(minimum_calibration_history=4),
    )
    assert all(item.interval_lower_bps is None for item in calibrated)
    assert stats["ttm-r2"]["interval_count"] == 0

    observations = _single_instrument_observations(("10", "20", "30", "40", "50"))
    predictions = _predictions_for_values(observations, ("0", "0", "0", "0", "0"))
    calibrated, stats = _rolling_calibration(
        observations,
        predictions,
        Phase4ReviewPolicy(minimum_calibration_history=4),
    )
    point = next(
        item for item in calibrated if item.observation_id == observations[4].observation_id
    )
    assert point.interval_lower_bps == Decimal("-40")
    assert point.interval_upper_bps == Decimal("40")
    assert stats["ttm-r2"]["derived_interval_count"] == 1


def test_rolling_calibration_does_not_use_future_outcomes():
    observations = _single_instrument_observations(("10", "20", "30", "40"))
    predictions = _predictions_for_values(observations, ("0", "0", "0", "0"))
    first, _ = _rolling_calibration(
        observations,
        predictions,
        Phase4ReviewPolicy(minimum_calibration_history=2),
    )

    changed_future = observations[3].model_copy(update={"realized_return_bps": Decimal("9999")})
    second, _ = _rolling_calibration(
        (*observations[:3], changed_future),
        predictions,
        Phase4ReviewPolicy(minimum_calibration_history=2),
    )
    first_point = next(
        item for item in first if item.observation_id == observations[2].observation_id
    )
    second_point = next(
        item for item in second if item.observation_id == observations[2].observation_id
    )
    assert first_point.interval_lower_bps == second_point.interval_lower_bps
    assert first_point.interval_upper_bps == second_point.interval_upper_bps


def test_rolling_calibration_excludes_same_cutoff_from_prior_history():
    observations = _single_instrument_observations(("10", "20", "100"))
    observations = (
        observations[0],
        observations[1].model_copy(update={"cutoff": observations[0].cutoff}),
        observations[2],
    )
    predictions = _predictions_for_values(observations, ("0", "0", "0"))

    calibrated, _ = _rolling_calibration(
        observations,
        predictions,
        Phase4ReviewPolicy(minimum_calibration_history=1),
    )
    by_id = {item.observation_id: item for item in calibrated}

    assert by_id[observations[0].observation_id].interval_lower_bps is None
    assert by_id[observations[1].observation_id].interval_lower_bps is None
    assert by_id[observations[2].observation_id].interval_lower_bps == Decimal("-20")
    assert by_id[observations[2].observation_id].interval_upper_bps == Decimal("20")


def test_rolling_calibration_keeps_btc_and_eth_history_separate():
    observations = _observations(4)
    realized = tuple(
        Decimal("10") if item.instrument == "BTCUSDT" else Decimal("100") for item in observations
    )
    observations = tuple(
        item.model_copy(update={"realized_return_bps": value})
        for item, value in zip(observations, realized, strict=True)
    )
    predictions = _predictions(observations, "ttm-r2", Decimal("0"))

    calibrated, _ = _rolling_calibration(
        observations,
        predictions,
        Phase4ReviewPolicy(minimum_calibration_history=2),
    )
    by_id = {item.observation_id: item for item in calibrated}
    assert by_id["BTCUSDT-2"].interval_upper_bps == Decimal("10")
    assert by_id["ETHUSDT-2"].interval_upper_bps == Decimal("100")


def test_rolling_calibration_preserves_valid_native_intervals_and_rejects_invalid_ones():
    observations = _single_instrument_observations(("10", "10", "10"))
    predictions = list(_predictions_for_values(observations, ("0", "0", "0")))
    predictions[2] = predictions[2].model_copy(
        update={"interval_lower_bps": Decimal("-5"), "interval_upper_bps": Decimal("5")}
    )
    calibrated, stats = _rolling_calibration(
        observations,
        tuple(predictions),
        Phase4ReviewPolicy(minimum_calibration_history=2),
    )
    native = next(
        item for item in calibrated if item.observation_id == observations[2].observation_id
    )
    assert native.interval_lower_bps == Decimal("-5")
    assert native.interval_upper_bps == Decimal("5")
    assert stats["ttm-r2"]["native_interval_count"] == 1
    assert stats["ttm-r2"]["derived_interval_count"] == 0

    predictions[2] = predictions[2].model_copy(
        update={"interval_lower_bps": Decimal("1"), "interval_upper_bps": Decimal("5")}
    )
    with pytest.raises(Phase4ReviewRefused, match="native prediction interval"):
        _rolling_calibration(
            observations,
            tuple(predictions),
            Phase4ReviewPolicy(minimum_calibration_history=2),
        )

    predictions[2] = predictions[2].model_copy(
        update={"interval_lower_bps": Decimal("-5"), "interval_upper_bps": None}
    )
    with pytest.raises(Phase4ReviewRefused, match="bounds must be supplied together"):
        _rolling_calibration(
            observations,
            tuple(predictions),
            Phase4ReviewPolicy(minimum_calibration_history=2),
        )


def test_daily_latency_policy_separates_operational_proxy_from_bar_stress():
    policy = Phase4ReviewPolicy()

    assert policy.data_cadence == "1d"
    assert policy.operational_latency_scenarios == ("normal_10s", "degraded_1h")
    assert "next_bar_stress" not in policy.operational_latency_scenarios
    assert "next_bar_stress" in policy.severe_signal_decay_scenarios


def test_calibration_metrics_expose_signed_and_absolute_coverage_error():
    metrics = _coverage_metrics(
        {"calibration_coverage": "0.75"},
        Phase4ReviewPolicy(),
        interval_count=32,
    )

    assert metrics == {
        "nominal_coverage": "0.80",
        "observed_coverage": "0.75",
        "coverage_error": "-0.05",
        "absolute_coverage_error": "0.05",
        "interval_count": 32,
    }


def test_delay_reuses_only_an_earlier_cutoff_prediction():
    observations = _observations(4)[:4]
    index = _prediction_index(_predictions(observations, "ttm-r2"))

    delayed_observations, delayed_predictions = _delayed(observations, index, 1)

    assert [item.observation_id for item in delayed_observations] == [
        observations[1].observation_id,
        observations[2].observation_id,
        observations[3].observation_id,
    ]
    assert [item.observation_id for item in delayed_predictions] == [
        item.observation_id for item in delayed_observations
    ]


def test_formal_review_is_pending_for_small_or_weak_evidence(tmp_path: Path):
    observations = _observations()
    predictions = tuple(
        prediction
        for model_name in (*MANDATORY_BASELINES, "ttm-r2", "ttm-r3")
        for prediction in _predictions(observations, model_name)
    )
    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps(
            {
                "schema": INPUT_SCHEMA,
                "observations": [item.model_dump(mode="json") for item in observations],
                "predictions": [item.model_dump(mode="json") for item in predictions],
            }
        ),
        encoding="utf-8",
    )
    gate_path = tmp_path / "phase3-gate.json"
    _phase3_gate(gate_path)
    measurement_root = tmp_path / "measurement"
    run_evaluation(
        input_path,
        gate_path,
        measurement_root,
        evaluated_at=START + timedelta(hours=1),
    )
    dependency_path = tmp_path / "phase4-dependency.json"
    dependency_path.write_text(
        json.dumps({"decision": "OPEN_FOR_MEASUREMENT", "measurement_allowed": True}),
        encoding="utf-8",
    )

    result = review_phase4(
        input_path=input_path,
        measurement_path=measurement_root / "phase4-paper-utility-evidence.json",
        phase3_gate_path=gate_path,
        dependency_path=dependency_path,
        output_root=tmp_path / "review",
        reviewed_at=START + timedelta(hours=2),
    )

    assert result["decision"] == "pending"
    assert result["phase4_admission_opened"] is False
    assert "adequate_chronological_sample" in result["blocking_requirements"]
    assert (tmp_path / "review" / "phase4-gate-record.json").is_file()


def test_formal_review_rejects_measurement_input_hash_mismatch(tmp_path: Path):
    observations = _observations(16)
    predictions = tuple(
        prediction
        for model_name in MANDATORY_BASELINES
        for prediction in _predictions(observations, model_name)
    )
    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps(
            {
                "schema": INPUT_SCHEMA,
                "observations": [item.model_dump(mode="json") for item in observations],
                "predictions": [item.model_dump(mode="json") for item in predictions],
            }
        ),
        encoding="utf-8",
    )
    gate_path = tmp_path / "phase3-gate.json"
    _phase3_gate(gate_path)
    measurement_root = tmp_path / "measurement"
    run_evaluation(input_path, gate_path, measurement_root, evaluated_at=START + timedelta(hours=1))
    payload = json.loads(input_path.read_text())
    payload["observations"][0]["realized_return_bps"] = "999"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    dependency_path = tmp_path / "phase4-dependency.json"
    dependency_path.write_text(
        json.dumps({"decision": "OPEN_FOR_MEASUREMENT", "measurement_allowed": True}),
        encoding="utf-8",
    )

    with pytest.raises(Phase4ReviewRefused, match="input hash"):
        review_phase4(
            input_path=input_path,
            measurement_path=measurement_root / "phase4-paper-utility-evidence.json",
            phase3_gate_path=gate_path,
            dependency_path=dependency_path,
            output_root=tmp_path / "rejected-review",
            reviewed_at=START + timedelta(hours=2),
        )

    assert not (tmp_path / "rejected-review").exists()
