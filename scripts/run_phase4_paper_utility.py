#!/usr/bin/env python3
"""Measure Phase-4 utility from an admitted Phase-3 gate and typed input.

This entrypoint is deliberately offline.  It reads a previously recorded,
passed ``PhaseGateRecord`` and a strict JSON input envelope, then writes one
immutable measurement report.  It cannot create a gate record, promote a
model, load credentials or weights, access the network, or submit an order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    # Keep the documented direct script invocation importable from the repo.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from advisorai.gates import GateDecision, PhaseGateRecord
from advisorai.phase4 import (
    Phase4MarketObservation,
    Phase4Prediction,
    evaluate_paper_utility,
)

INPUT_SCHEMA = "advisorai.phase4.paper-utility-input.v1"
EVIDENCE_SCHEMA = "advisorai.phase4.paper-utility-evidence.v1"


class Phase4EvidenceRefused(ValueError):
    """Raised when the offline input or prerequisite gate is not admissible."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _write_immutable(path: Path, payload: object) -> str:
    encoded = _canonical_json(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return hashlib.sha256(encoded).hexdigest()


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Phase4EvidenceRefused(f"cannot read JSON input: {type(exc).__name__}") from exc


def _load_input(
    path: Path,
) -> tuple[tuple[Phase4MarketObservation, ...], tuple[Phase4Prediction, ...]]:
    payload = _load_json(path)
    if not isinstance(payload, dict) or payload.get("schema") != INPUT_SCHEMA:
        raise Phase4EvidenceRefused("input schema is not the reviewed Phase-4 utility schema")
    if set(payload) != {"schema", "observations", "predictions"}:
        raise Phase4EvidenceRefused("input envelope has unexpected fields")
    raw_observations = payload.get("observations")
    raw_predictions = payload.get("predictions")
    if not isinstance(raw_observations, list) or not isinstance(raw_predictions, list):
        raise Phase4EvidenceRefused("input observations and predictions must be arrays")
    try:
        observations = tuple(
            Phase4MarketObservation.model_validate(item) for item in raw_observations
        )
        predictions = tuple(Phase4Prediction.model_validate(item) for item in raw_predictions)
    except (TypeError, ValueError) as exc:
        raise Phase4EvidenceRefused("typed Phase-4 input validation failed") from exc
    if not observations or not predictions:
        raise Phase4EvidenceRefused("Phase-4 utility input cannot be empty")
    if not all(item.phase3_admitted for item in observations):
        raise Phase4EvidenceRefused("every observation must carry phase3_admitted=true")
    return observations, predictions


def _load_passed_phase3_gate(path: Path, *, at: datetime) -> PhaseGateRecord:
    payload = _load_json(path)
    try:
        record = PhaseGateRecord.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise Phase4EvidenceRefused("Phase-3 gate record validation failed") from exc
    if record.phase != 3 or record.decision is not GateDecision.PASSED:
        raise Phase4EvidenceRefused("a passed Phase-3 gate record is required")
    if not record.is_valid_at(at):
        raise Phase4EvidenceRefused("the Phase-3 gate record is not valid at evaluation time")
    return record


def run_evaluation(
    input_path: Path,
    phase3_gate_record: Path,
    output_root: Path,
    *,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    """Run one immutable offline utility measurement without opening admission."""

    input_path = input_path.resolve()
    phase3_gate_record = phase3_gate_record.resolve()
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError("output root must be new; Phase-4 evidence is immutable")
    timestamp = evaluated_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise Phase4EvidenceRefused("evaluation timestamp must include a timezone")
    timestamp = timestamp.astimezone(UTC)
    observations, predictions = _load_input(input_path)
    gate = _load_passed_phase3_gate(phase3_gate_record, at=timestamp)
    input_sha256 = _sha256(input_path)
    gate_sha256 = _sha256(phase3_gate_record)
    report = evaluate_paper_utility(
        observations,
        predictions,
        phase3_gate_record_hash=gate_sha256,
        evaluated_at=timestamp,
    )
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "generated_at": timestamp.isoformat(),
        "state": report.state.value,
        "phase4_admission_opened": False,
        "network_calls": 0,
        "credentials_loaded": False,
        "model_weights_loaded": False,
        "phase3_gate": {
            "phase": gate.phase,
            "decision": gate.decision.value,
            "file_sha256": gate_sha256,
            "canonical_hash": gate.canonical_hash(),
        },
        "input": {
            "sha256": input_sha256,
            "observation_count": len(observations),
            "prediction_count": len(predictions),
        },
        "report": report.model_dump(mode="json"),
        "execution_authority": {
            "risk_kernel": "unchanged_external_authority",
            "oms": "unchanged_external_authority",
            "model_order_authority": False,
            "dashboard_order_authority": False,
        },
    }
    report_path = output_root / "phase4-paper-utility-evidence.json"
    evidence_sha256 = _write_immutable(report_path, evidence)
    (output_root / "phase4-paper-utility-evidence.sha256").write_text(
        f"{evidence_sha256}  {report_path.name}\n", encoding="ascii"
    )
    return {
        "state": report.state.value,
        "phase4_admission_opened": False,
        "evidence": str(report_path),
        "sha256": evidence_sha256,
        "phase3_gate_sha256": gate_sha256,
        "observation_count": len(observations),
        "prediction_count": len(predictions),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--phase3-gate-record", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run_evaluation(args.input, args.phase3_gate_record, args.output_root)
    except (FileExistsError, Phase4EvidenceRefused, ValueError, OSError) as exc:
        # Do not echo parsed payloads, validation values, paths from secrets,
        # or provider response bodies in a command-line failure.
        raise SystemExit(f"phase4 utility evaluation refused ({type(exc).__name__})") from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
