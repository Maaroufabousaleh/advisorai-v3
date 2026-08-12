from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.evaluate_phase4_predecessor import (
    DependencyStatus,
    Phase4DependencyRefused,
    evaluate,
    write_evidence,
)

PHASE3 = Path(
    "artifacts/phase3/formal-admission/20260812T013505Z-with-passed-phase2-post-phase2-commit/"
    "phase3-gate-record.json"
)
ROSTER = Path("configs/models/phase0_local_roster.json")


def test_phase4_dependency_opens_only_measurement_from_phase3_and_roles():
    decision = evaluate(
        phase3_gate_path=PHASE3,
        local_roster_path=ROSTER,
        evaluated_at=datetime(2026, 8, 12, 2, 0, tzinfo=UTC),
    )

    assert decision.decision == "OPEN_FOR_MEASUREMENT"
    assert decision.measurement_allowed is True
    assert decision.phase4_admission_opened is False
    assert decision.global_phase0_status == "PENDING_SEPARATE_PRIVATE_ROUTE_AND_ARCHIVE"
    assert any(
        item.requirement_id == "global_phase0_route_archive"
        and item.status is DependencyStatus.EXTERNALLY_BLOCKED
        and not item.gating
        for item in decision.checks
    )


def test_phase4_dependency_refuses_unpassed_phase3(tmp_path: Path):
    payload = json.loads(PHASE3.read_text())
    payload["decision"] = "pending"
    payload["reasons"] = ["fixture pending"]
    path = tmp_path / "pending-phase3.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Phase4DependencyRefused, match="passed Phase-3"):
        evaluate(phase3_gate_path=path, local_roster_path=ROSTER)


def test_phase4_dependency_evidence_is_immutable(tmp_path: Path):
    decision = evaluate(
        phase3_gate_path=PHASE3,
        local_roster_path=ROSTER,
        evaluated_at=datetime(2026, 8, 12, 2, 0, tzinfo=UTC),
    )
    result = write_evidence(tmp_path / "dependency", decision)
    assert result["measurement_allowed"] == "true"
    assert (tmp_path / "dependency" / "phase4-predecessor-dependency.json").is_file()
    with pytest.raises(FileExistsError):
        write_evidence(tmp_path / "dependency", decision)
