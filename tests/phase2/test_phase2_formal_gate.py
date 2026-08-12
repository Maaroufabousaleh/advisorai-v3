from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from advisorai.gates import GateDecision, GateEvidence, GateEvidenceKind, PhaseGateRecord
from scripts.evaluate_phase2_gate import (
    EvidenceReference,
    Phase2EvidenceRefused,
    _phase2_checks,
    evaluate,
    validate_phase2_record,
)


def _reports() -> tuple[dict, dict]:
    read_only_path = Path(
        "artifacts/phase2/binance-spot-testnet/read-only-smoke/"
        "20260810T193840.598161Z/binance-spot-testnet-read-only-smoke.json"
    )
    lifecycle_path = Path(
        "artifacts/phase2/binance-spot-testnet/paper-lifecycle/"
        "20260810T195818.312420Z/binance-spot-testnet-paper-lifecycle.json"
    )
    return json.loads(read_only_path.read_text()), json.loads(lifecycle_path.read_text())


def _write_record(
    path: Path,
    *,
    phase: int = 2,
    decision: GateDecision = GateDecision.PASSED,
    expires: bool = False,
) -> None:
    observed = datetime(2026, 8, 11, tzinfo=UTC)
    evidence = GateEvidence(
        name="binance-paper-lifecycle",
        kind=GateEvidenceKind.OPERATIONAL,
        passed=decision is GateDecision.PASSED,
        artifact_hash="a" * 64 if decision is GateDecision.PASSED else None,
        source="fixture",
        verified_by="test",
        observed_at=observed,
        expires_at=datetime(2026, 8, 12, tzinfo=UTC) if expires else None,
    )
    record = PhaseGateRecord(
        phase=phase,
        name="Phase 2",
        decision=decision,
        required_evidence=("binance-paper-lifecycle",) if decision is GateDecision.PASSED else (),
        evidence=(evidence,),
        prerequisite_phase=1 if phase == 2 else 2,
        recorded_by="test",
        recorded_at=observed,
        reasons=("fixture is pending",) if decision is GateDecision.PENDING else (),
    )
    path.write_text(record.model_dump_json(), encoding="utf-8")


def test_existing_binance_evidence_formalizes_phase2_without_network():
    checklist, record = evaluate(evaluated_at=datetime(2026, 8, 11, 23, tzinfo=UTC))

    assert checklist.decision is GateDecision.PASSED
    assert checklist.blocking_requirement_ids == ()
    assert record.phase == 2
    assert record.decision is GateDecision.PASSED
    assert checklist.venue.symbols == ("BTCUSDT", "ETHUSDT")
    assert checklist.venue.real_fill_observed is False
    assert any(
        item.requirement_id == "phase2_real_fill" and not item.gating
        for item in checklist.requirements
    )


def test_missing_required_read_operation_fails_closed():
    read_only, lifecycle = _reports()
    read_only = copy.deepcopy(read_only)
    read_only["result"]["operations"] = [
        item for item in read_only["result"]["operations"] if item["name"] != "fills"
    ]

    checks = _phase2_checks(read_only, lifecycle)

    assert checks["authenticated_account_reads"] is False


def test_wrong_venue_identity_is_not_admitted():
    read_only, lifecycle = _reports()
    read_only = copy.deepcopy(read_only)
    read_only["result"]["venue"] = "coinbase_exchange_sandbox"

    checks = _phase2_checks(read_only, lifecycle)

    assert checks["venue_identity_and_host"] is False


def test_production_endpoint_contamination_is_not_admitted():
    read_only, lifecycle = _reports()
    lifecycle = copy.deepcopy(lifecycle)
    lifecycle["result"]["endpoint"] = "https://api.binance.com"

    checks = _phase2_checks(read_only, lifecycle)

    assert checks["venue_identity_and_host"] is False
    assert checks["no_production_execution"] is False


def test_missing_eth_provider_truth_is_not_admitted():
    read_only, lifecycle = _reports()
    read_only = copy.deepcopy(read_only)
    for operation in read_only["result"]["operations"]:
        if operation["name"] == "products":
            operation["required_symbols"] = ["BTCUSDT"]
        if operation["name"] == "product_mapping_verification":
            operation["admitted_symbols"] = ["BTCUSDT"]

    checks = _phase2_checks(read_only, lifecycle)

    assert checks["provider_truth_btc_eth"] is False


def test_missing_risk_or_oms_binding_is_not_admitted():
    read_only, lifecycle = _reports()
    lifecycle = copy.deepcopy(lifecycle)
    lifecycle["result"]["risk_decision"]["outcome"] = "rejected"
    lifecycle["result"]["intent_persisted_before_submission"] = False

    checks = _phase2_checks(read_only, lifecycle)

    assert checks["risk_kernel_approval"] is False
    assert checks["oms_intent_before_submission"] is False


def test_invalid_evidence_hash_is_rejected():
    with pytest.raises(ValueError, match="SHA-256"):
        EvidenceReference(path="evidence.json", sha256="g" * 64)


def test_expired_phase2_record_is_rejected(tmp_path: Path):
    path = tmp_path / "phase2.json"
    _write_record(path, expires=True)

    with pytest.raises(Phase2EvidenceRefused, match="not valid"):
        validate_phase2_record(path, at=datetime(2026, 8, 12, tzinfo=UTC))


def test_wrong_phase_number_is_rejected(tmp_path: Path):
    path = tmp_path / "phase3.json"
    _write_record(path, phase=3)

    with pytest.raises(Phase2EvidenceRefused, match="phase=2"):
        validate_phase2_record(path, at=datetime(2026, 8, 11, 12, tzinfo=UTC))


def test_non_passed_phase2_decision_is_rejected(tmp_path: Path):
    path = tmp_path / "pending.json"
    _write_record(path, decision=GateDecision.PENDING)

    with pytest.raises(Phase2EvidenceRefused, match="passed Phase-2"):
        validate_phase2_record(path, at=datetime(2026, 8, 11, 12, tzinfo=UTC))


def test_passed_phase2_record_validates_offline(tmp_path: Path):
    _checklist, record = evaluate(evaluated_at=datetime(2026, 8, 11, 23, tzinfo=UTC))
    path = tmp_path / "phase2.json"
    path.write_text(record.model_dump_json(), encoding="utf-8")

    validated = validate_phase2_record(path, at=datetime(2026, 8, 11, 23, 1, tzinfo=UTC))

    assert validated.canonical_hash() == record.canonical_hash()
