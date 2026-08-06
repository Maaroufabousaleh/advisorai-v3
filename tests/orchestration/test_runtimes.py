from datetime import UTC, datetime

import pytest

from advisorai.gates import (
    GateDecision,
    GateEvidence,
    GateEvidenceKind,
    PhaseGateRecord,
    PhaseGateRegistry,
)
from advisorai.orchestration import HamiltonRuntime, PrefectRuntime, PydanticRuntime


@pytest.mark.parametrize("runtime", (PydanticRuntime(), PrefectRuntime(), HamiltonRuntime()))
def test_optional_canonical_runtimes_do_not_silently_fallback(runtime):
    if runtime.status.production_admitted:
        return
    with pytest.raises(RuntimeError, match="quarantined"):
        if isinstance(runtime, PrefectRuntime):
            runtime.run_flow(lambda: None)
        elif isinstance(runtime, HamiltonRuntime):
            runtime.compute_features(lambda values: values, {})
        else:
            runtime.run_typed_agent(lambda values: values, {})


def test_installed_canonical_runtimes_require_and_use_a_recorded_phase_gate():
    registry = PhaseGateRegistry()
    registry.record(
        PhaseGateRecord(
            phase=0,
            name="phase-0-fixture",
            decision=GateDecision.PASSED,
            required_evidence=("timed",),
            evidence=(
                GateEvidence(
                    name="timed",
                    kind=GateEvidenceKind.EXTERNAL_TIMED,
                    passed=True,
                    artifact_hash="a" * 64,
                    source="fixture",
                    verified_by="reviewer",
                    observed_at=datetime(2026, 8, 5, 15, 0, tzinfo=UTC),
                ),
            ),
            recorded_by="reviewer",
        )
    )
    pydantic = PydanticRuntime(phase0_admitted=True, gate_registry=registry)
    prefect = PrefectRuntime(phase0_admitted=True, gate_registry=registry)
    hamilton = HamiltonRuntime(phase0_admitted=True, gate_registry=registry)
    assert pydantic.status.production_admitted
    assert pydantic.run_typed_agent(lambda payload: {"ok": payload["value"]}, {"value": 1}) == {
        "ok": 1
    }
    assert prefect.status.production_admitted
    assert prefect.run_flow(lambda: "flow-ok") == "flow-ok"
    assert hamilton.status.production_admitted
    assert hamilton.compute_features(lambda payload: {"x": payload["value"]}, {"value": 2}) == {
        "x": 2
    }
