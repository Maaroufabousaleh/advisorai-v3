from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from advisorai.gates import GateDecision, GateEvidence, GateEvidenceKind, PhaseGateRecord
from advisorai.phase4 import MANDATORY_BASELINES
from scripts.run_phase4_paper_utility import (
    INPUT_SCHEMA,
    Phase4EvidenceRefused,
    run_evaluation,
)

HASH = "a" * 64
START = datetime(2026, 8, 1, 12, tzinfo=UTC)


def _gate(*, passed: bool = True) -> PhaseGateRecord:
    if passed:
        evidence = (
            GateEvidence(
                name="phase3-real-source",
                kind=GateEvidenceKind.OPERATIONAL,
                passed=True,
                artifact_hash=HASH,
                source="/evidence/phase3-gate.json",
                verified_by="supervised-review",
                observed_at=START,
            ),
        )
        return PhaseGateRecord(
            phase=3,
            name="Phase 3",
            decision=GateDecision.PASSED,
            required_evidence=("phase3-real-source",),
            evidence=evidence,
            prerequisite_phase=2,
            recorded_by="supervised-review",
            recorded_at=START,
        )
    return PhaseGateRecord(
        phase=3,
        name="Phase 3",
        decision=GateDecision.PENDING,
        prerequisite_phase=2,
        recorded_by="supervised-review",
        recorded_at=START,
        reasons=("source evidence remains pending",),
    )


def _observation(index: int) -> dict[str, object]:
    return {
        "observation_id": f"obs-{index}",
        "instrument": "BTCUSDT",
        "cutoff": (START + timedelta(minutes=index)).isoformat(),
        "realized_at": (START + timedelta(minutes=index + 1)).isoformat(),
        "realized_return_bps": "10" if index % 2 == 0 else "-10",
        "spread_bps": "0",
        "slippage_bps": "0",
        "regime": "trend",
        "source_id": "binance-public",
        "provider_identity": "binance-public",
        "endpoint": "https://data.example.test/public",
        "source_snapshot_hash": HASH,
        "phase3_admitted": True,
    }


def _prediction(observation_id: str, model_name: str, value: str = "0") -> dict[str, object]:
    return {
        "observation_id": observation_id,
        "model_name": model_name,
        "predicted_return_bps": value,
        "confidence": "0.5",
        "interval_lower_bps": "-100",
        "interval_upper_bps": "100",
        "model_code_hash": HASH,
        "model_artifact_hash": HASH,
    }


def _write_input(path: Path) -> None:
    observations = [_observation(index) for index in range(3)]
    predictions = [
        _prediction(observation["observation_id"], model)
        for model in MANDATORY_BASELINES
        for observation in observations
    ]
    path.write_text(
        json.dumps(
            {"schema": INPUT_SCHEMA, "observations": observations, "predictions": predictions}
        ),
        encoding="utf-8",
    )


def test_runner_requires_passed_current_phase3_gate_and_keeps_output_closed(tmp_path: Path):
    input_path = tmp_path / "input.json"
    gate_path = tmp_path / "pending-gate.json"
    output_root = tmp_path / "evidence"
    _write_input(input_path)
    gate_path.write_text(_gate(passed=False).model_dump_json(), encoding="utf-8")

    with pytest.raises(Phase4EvidenceRefused, match="passed Phase-3"):
        run_evaluation(input_path, gate_path, output_root, evaluated_at=START + timedelta(days=1))

    assert not output_root.exists()


def test_runner_writes_immutable_measurement_without_opening_admission(tmp_path: Path):
    input_path = tmp_path / "input.json"
    gate_path = tmp_path / "passed-gate.json"
    output_root = tmp_path / "evidence"
    _write_input(input_path)
    gate_path.write_text(_gate().model_dump_json(), encoding="utf-8")

    result = run_evaluation(
        input_path,
        gate_path,
        output_root,
        evaluated_at=START + timedelta(days=1),
    )

    assert result["state"] == "measured_pending_review"
    assert result["phase4_admission_opened"] is False
    report = json.loads((output_root / "phase4-paper-utility-evidence.json").read_text())
    assert report["phase4_admission_opened"] is False
    assert report["network_calls"] == 0
    assert report["credentials_loaded"] is False
    assert report["model_weights_loaded"] is False
    assert report["execution_authority"]["model_order_authority"] is False
    assert report["phase3_gate"]["decision"] == "passed"
    assert report["input"]["observation_count"] == 3
    assert (output_root / "phase4-paper-utility-evidence.sha256").is_file()

    with pytest.raises(FileExistsError, match="output root must be new"):
        run_evaluation(input_path, gate_path, output_root, evaluated_at=START + timedelta(days=1))


def test_runner_rejects_extra_input_fields_before_creating_evidence(tmp_path: Path):
    input_path = tmp_path / "input.json"
    gate_path = tmp_path / "passed-gate.json"
    output_root = tmp_path / "evidence"
    _write_input(input_path)
    payload = json.loads(input_path.read_text())
    payload["credentials"] = "must-not-be-accepted"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    gate_path.write_text(_gate().model_dump_json(), encoding="utf-8")

    with pytest.raises(Phase4EvidenceRefused, match="unexpected fields"):
        run_evaluation(input_path, gate_path, output_root, evaluated_at=START + timedelta(days=1))

    assert not output_root.exists()
