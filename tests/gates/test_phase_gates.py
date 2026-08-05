from datetime import UTC, datetime

import pytest

from advisorai.gates import (
    GateDecision,
    GateEvidence,
    GateEvidenceKind,
    PhaseGateRecord,
    PhaseGateRegistry,
    local_test_evidence,
)
from advisorai.ledger import LedgerNamespace, SqliteLedgers


def _evidence(name: str, *, passed: bool = True) -> GateEvidence:
    return GateEvidence(
        name=name,
        kind=GateEvidenceKind.EXTERNAL_TIMED,
        passed=passed,
        artifact_hash="a" * 64 if passed else None,
        source="fixture",
        verified_by="reviewer",
        observed_at=datetime(2026, 8, 5, tzinfo=UTC),
    )


def test_passed_gate_requires_valid_required_evidence():
    with pytest.raises(ValueError, match="missing evidence"):
        PhaseGateRecord(
            phase=0,
            name="phase-0",
            decision=GateDecision.PASSED,
            required_evidence=("24h-stability",),
            recorded_by="reviewer",
        )

    record = PhaseGateRecord(
        phase=0,
        name="phase-0",
        decision=GateDecision.PASSED,
        required_evidence=("24h-stability",),
        evidence=(_evidence("24h-stability"),),
        recorded_by="reviewer",
    )
    assert record.decision is GateDecision.PASSED


def test_timed_and_human_gates_require_their_external_evidence_kind():
    with pytest.raises(ValueError, match="Phase 7 requires external timed evidence"):
        PhaseGateRecord(
            phase=7,
            name="phase-7",
            decision=GateDecision.PASSED,
            required_evidence=("local",),
            evidence=(
                GateEvidence(
                    name="local",
                    kind=GateEvidenceKind.LOCAL_TEST,
                    passed=True,
                    artifact_hash="a" * 64,
                    source="fixture",
                    verified_by="reviewer",
                ),
            ),
            recorded_by="reviewer",
        )

    with pytest.raises(ValueError, match="Phase 10 requires explicit human approval"):
        PhaseGateRecord(
            phase=10,
            name="phase-10",
            decision=GateDecision.PASSED,
            required_evidence=("timed",),
            evidence=(_evidence("timed"),),
            recorded_by="reviewer",
        )


def test_phase_registry_enforces_sequential_admission_and_replays(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "gates.sqlite")
    registry = PhaseGateRegistry(ledgers)
    pending = PhaseGateRecord(
        phase=0,
        name="phase-0",
        decision=GateDecision.PENDING,
        reasons=("24-hour measurements pending",),
        recorded_by="runner",
    )
    registry.record(pending)
    with pytest.raises(ValueError, match="phase 0 passes"):
        registry.record(
            PhaseGateRecord(
                phase=1,
                name="phase-1",
                decision=GateDecision.PASSED,
                required_evidence=("local",),
                evidence=(_evidence("local"),),
                recorded_by="runner",
            )
        )

    phase_zero = PhaseGateRecord(
        phase=0,
        name="phase-0",
        decision=GateDecision.PASSED,
        required_evidence=("local",),
        evidence=(_evidence("local"),),
        recorded_by="reviewer",
    )
    registry.record(phase_zero)
    phase_one = PhaseGateRecord(
        phase=1,
        name="phase-1",
        decision=GateDecision.PASSED,
        required_evidence=("local",),
        evidence=(_evidence("local"),),
        recorded_by="reviewer",
    )
    registry.record(phase_one)
    restarted = PhaseGateRegistry(ledgers)
    assert restarted.is_admitted(0)
    assert restarted.is_admitted(1)
    assert restarted.latest(1) == phase_one
    assert len(ledgers.events(LedgerNamespace.MODEL)) == 3


def test_pending_and_failed_gates_require_reasons():
    with pytest.raises(ValueError, match="pending phase gate"):
        PhaseGateRecord(
            phase=0,
            name="phase-0",
            decision=GateDecision.PENDING,
            recorded_by="runner",
        )
    with pytest.raises(ValueError, match="failed phase gate"):
        PhaseGateRecord(
            phase=0,
            name="phase-0",
            decision=GateDecision.FAILED,
            recorded_by="runner",
        )


def test_local_test_evidence_hash_is_reproducible():
    first = local_test_evidence(
        name="pytest", passed=True, command="pytest -q", output="212 passed\n"
    )
    second = local_test_evidence(
        name="pytest", passed=True, command="pytest -q", output="212 passed\n"
    )
    assert first.artifact_hash == second.artifact_hash
    assert first.kind is GateEvidenceKind.LOCAL_TEST


def test_expired_gate_evidence_closes_admission():
    observed = datetime(2026, 8, 5, tzinfo=UTC)
    record = PhaseGateRecord(
        phase=0,
        name="phase-0",
        decision=GateDecision.PASSED,
        required_evidence=("stability",),
        evidence=(
            GateEvidence(
                name="stability",
                kind=GateEvidenceKind.EXTERNAL_TIMED,
                passed=True,
                artifact_hash="a" * 64,
                source="fixture",
                verified_by="reviewer",
                observed_at=observed,
                expires_at=observed.replace(hour=13),
            ),
        ),
        recorded_at=observed.replace(hour=12, minute=30),
        recorded_by="reviewer",
    )
    registry = PhaseGateRegistry()
    registry.record(record)
    assert registry.is_admitted(0, at=observed.replace(hour=12, minute=45))
    assert not registry.is_admitted(0, at=observed.replace(hour=13))
