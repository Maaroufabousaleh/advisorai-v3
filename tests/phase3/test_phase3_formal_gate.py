from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from advisorai.gates import GateDecision, GateEvidence, GateEvidenceKind, PhaseGateRecord
from scripts.evaluate_phase3_gate import ChecklistStatus, _phase2_predecessor


def _phase2_record(*, decision: GateDecision = GateDecision.PASSED) -> PhaseGateRecord:
    evidence = GateEvidence(
        name="phase2-paper-lifecycle",
        kind=GateEvidenceKind.OPERATIONAL,
        passed=decision is GateDecision.PASSED,
        artifact_hash="a" * 64 if decision is GateDecision.PASSED else None,
        source="fixture",
        verified_by="test",
        observed_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    return PhaseGateRecord(
        phase=2,
        name="Phase 2",
        decision=decision,
        required_evidence=("phase2-paper-lifecycle",) if decision is GateDecision.PASSED else (),
        evidence=(evidence,),
        prerequisite_phase=1,
        recorded_by="test",
        recorded_at=datetime(2026, 8, 11, tzinfo=UTC),
        reasons=("phase 2 fixture is pending",) if decision is GateDecision.PENDING else (),
    )


def test_formal_gate_entrypoint_help_is_offline_and_explicit():
    repository_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, "scripts/evaluate_phase3_gate.py", "--help"],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "Evaluate the formal Phase-3 gate" in completed.stdout
    assert "--phase2-gate-record" in completed.stdout
    assert completed.stderr == ""


def test_missing_phase2_predecessor_is_a_blocker_not_an_inferred_pass(tmp_path: Path):
    passed, detail = _phase2_predecessor(None, datetime(2026, 8, 11, tzinfo=UTC))

    assert not passed
    assert "no Phase-2 PhaseGateRecord" in detail

    pending_path = tmp_path / "phase2-pending.json"
    pending_path.write_text(
        _phase2_record(decision=GateDecision.PENDING).model_dump_json(), encoding="utf-8"
    )
    passed, detail = _phase2_predecessor(pending_path, datetime(2026, 8, 11, tzinfo=UTC))

    assert not passed
    assert "not a passed Phase-2 record" in detail


def test_current_valid_passed_phase2_predecessor_is_accepted(tmp_path: Path):
    path = tmp_path / "phase2-passed.json"
    path.write_text(_phase2_record().model_dump_json(), encoding="utf-8")

    passed, detail = _phase2_predecessor(path, datetime(2026, 8, 11, tzinfo=UTC))

    assert passed
    assert detail == "a currently valid passed Phase-2 record was supplied"


def test_checklist_statuses_preserve_external_and_optional_distinctions():
    assert ChecklistStatus.EXTERNALLY_BLOCKED.value == "EXTERNALLY_BLOCKED"
    assert ChecklistStatus.OPTIONAL.value == "OPTIONAL"
    assert ChecklistStatus.NOT_APPLICABLE.value == "NOT_APPLICABLE"
