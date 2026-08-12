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
