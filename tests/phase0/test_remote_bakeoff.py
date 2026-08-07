from datetime import UTC, datetime
from hashlib import sha256

import pytest

from advisorai.phase0 import (
    RemoteBakeoffReport,
    RemoteBudget,
    RemoteInvocation,
    RemoteProbeResult,
    RemoteProbeStatus,
    RemoteRouteCandidate,
    RemoteRouteRole,
    sanitize_metadata,
    write_remote_report,
)

HASH = "a" * 64


def _candidate(**updates):
    values = {
        "candidate": "ling",
        "role": RemoteRouteRole.PRIVATE_WORKER,
        "gateway": "openrouter",
        "provider_selector": "novita",
        "requested_model": "inclusionai/ling-2.6-flash",
        "requested_endpoint_selector": "novita",
        "observed_provider_names": ("Novita",),
        "allowed_top_level_models": ("inclusionai/ling-2.6-flash",),
        "allowed_resolved_models": ("inclusionai/ling-2.6-flash-20260421",),
        "input_price_per_million": 0.01,
        "output_price_per_million": 0.03,
        "request_price": 0,
        "supports_tools": True,
        "supports_tool_choice_required": True,
        "supports_structured_output": True,
        "inventory_reference": "inventory.json",
        "inventory_artifact_hash": HASH,
        "terms_reference": "inventory://test",
    }
    values.update(updates)
    return RemoteRouteCandidate(**values)


def test_budget_is_bounded_by_absolute_cap_and_remaining_fraction():
    budget = RemoteBudget(remaining_allowed_usd=0.20)
    assert budget.cap_usd == pytest.approx(0.05)
    budget = budget.authorize(0.01)
    assert budget.remaining_cap_usd == pytest.approx(0.04)
    with pytest.raises(ValueError, match="spend cap"):
        budget.authorize(0.05)


def test_reproducible_candidate_requires_explicit_provider_and_model_identities():
    with pytest.raises(ValueError, match="provider_selector"):
        _candidate(provider_selector=None)
    with pytest.raises(ValueError, match="admitted model identities"):
        _candidate(allowed_resolved_models=())


def test_dynamic_public_candidate_is_explicitly_non_reproducible():
    candidate = _candidate(
        candidate="free",
        role=RemoteRouteRole.CONTRIBUTOR_PUBLIC,
        provider_selector=None,
        requested_model="openrouter/free",
        requested_endpoint_selector=None,
        observed_provider_names=(),
        allowed_top_level_models=(),
        allowed_resolved_models=(),
        supports_tools=False,
        supports_tool_choice_required=False,
        supports_structured_output=False,
        reproducible=False,
    )
    assert candidate.reproducible is False


def test_failed_probe_cannot_claim_actual_identity():
    with pytest.raises(ValueError, match="actual provider identity"):
        RemoteProbeResult(
            candidate="ling",
            role=RemoteRouteRole.PRIVATE_WORKER,
            status=RemoteProbeStatus.FAILED,
            invocation=RemoteInvocation.STRUCTURED_OUTPUT,
            observed_provider_name="Novita",
        )


def test_measured_probe_requires_complete_selected_route_identity():
    with pytest.raises(ValueError, match="complete actual route identity"):
        RemoteProbeResult(
            candidate="ling",
            role=RemoteRouteRole.PRIVATE_WORKER,
            status=RemoteProbeStatus.MEASURED,
            invocation=RemoteInvocation.STRUCTURED_OUTPUT,
            billed_cost_usd=0.000001,
        )


def test_measured_probe_requires_billed_cost():
    with pytest.raises(ValueError, match="billed cost"):
        RemoteProbeResult(
            candidate="ling",
            role=RemoteRouteRole.PRIVATE_WORKER,
            status=RemoteProbeStatus.MEASURED,
            invocation=RemoteInvocation.STRUCTURED_OUTPUT,
            observed_provider_name="Novita",
            top_level_response_model="inclusionai/ling-2.6-flash",
            resolved_endpoint_model="inclusionai/ling-2.6-flash-20260421",
            actual_gateway="openrouter",
            endpoint_selector_proof="sha256:" + "a" * 64,
            endpoint_selected=True,
        )


def test_tool_probe_cannot_claim_execution():
    with pytest.raises(ValueError, match="tool execution"):
        RemoteProbeResult(
            candidate="ling",
            role=RemoteRouteRole.PRIVATE_WORKER,
            status=RemoteProbeStatus.MEASURED,
            invocation=RemoteInvocation.TOOL_REQUIRED,
            observed_provider_name="Novita",
            top_level_response_model="inclusionai/ling-2.6-flash",
            resolved_endpoint_model="inclusionai/ling-2.6-flash-20260421",
            actual_gateway="openrouter",
            endpoint_selector_proof="sha256:" + "a" * 64,
            endpoint_selected=True,
            billed_cost_usd=0.000001,
            tool_execution_status="succeeded",
        )


def test_sanitizer_removes_raw_provider_text_and_user_identifiers():
    safe = sanitize_metadata(
        {
            "provider_name": "Novita",
            "user_id": "must-not-persist",
            "raw": "provider text must not persist",
            "attempt": 1,
            "attempted_endpoints": [
                {"message": "also omitted", "provider": "Novita", "selected": False}
            ],
        }
    )
    assert safe == {
        "provider_name": "Novita",
        "attempt": 1,
        "attempted_endpoints": [{"provider": "Novita", "selected": False}],
    }


def test_report_hash_and_immutable_write(tmp_path):
    report = RemoteBakeoffReport(
        run_id="run-1",
        measured_at=datetime.now(UTC),
        inventory_reference="inventory.json",
        inventory_artifact_hash=HASH,
        budget=RemoteBudget(remaining_allowed_usd=1),
        candidates=(_candidate(),),
    )
    path = tmp_path / "report.json"
    digest = write_remote_report(report, path)
    assert digest == sha256(path.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        write_remote_report(report, path)


def test_report_billed_spend_is_reconciled_to_measured_probes():
    probe = RemoteProbeResult(
        candidate="ling",
        role=RemoteRouteRole.PRIVATE_WORKER,
        status=RemoteProbeStatus.MEASURED,
        invocation=RemoteInvocation.STRUCTURED_OUTPUT,
        observed_provider_name="Novita",
        top_level_response_model="inclusionai/ling-2.6-flash",
        resolved_endpoint_model="inclusionai/ling-2.6-flash-20260421",
        actual_gateway="openrouter",
        endpoint_selector_proof="sha256:" + "a" * 64,
        endpoint_selected=True,
        billed_cost_usd=0.000001,
    )
    with pytest.raises(ValueError, match="sum of measured probe costs"):
        RemoteBakeoffReport(
            run_id="run-2",
            measured_at=datetime.now(UTC),
            inventory_reference="inventory.json",
            inventory_artifact_hash=HASH,
            budget=RemoteBudget(remaining_allowed_usd=1),
            billed_spend_usd=0,
            probes=(probe,),
        )
