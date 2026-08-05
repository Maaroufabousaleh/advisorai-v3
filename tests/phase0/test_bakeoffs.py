import pytest

from advisorai.gateway import LocalDeterministicGateway
from advisorai.ledger import SqliteLedgers
from advisorai.phase0.bakeoffs import (
    BakeoffGate,
    BakeoffResult,
    ComponentKind,
    benchmark_callable,
    benchmark_gateway_adapter,
    default_candidates,
    record_bakeoff_gate,
    recorded_bakeoff_gates,
    run_availability_inventory,
)
from advisorai.ports import GatewayMessage, GatewayRequest, GatewayRoute


def test_default_candidate_inventory_contains_the_architecture_set():
    names = {candidate.name for candidate in default_candidates()}
    assert {
        "direct_api",
        "pydantic-ai",
        "pydantic-graph",
        "litellm",
        "omniroute",
        "ttm-r2",
        "tspulse",
        "chronos-2-small",
        "kronos-mini-small",
        "tabpfn-ts",
        "nautilus-trader",
        "prefect",
        "hamilton",
        "ducklake",
        "hermes-agent",
        "rclone-crypt",
    } <= names


def test_availability_inventory_quarantines_missing_dependencies():
    result = run_availability_inventory(default_candidates())
    by_name = {item.candidate.name: item for item in result}
    assert by_name["direct_api"].status == "available"
    assert by_name["litellm"].status in {"available", "quarantined"}
    assert all(item.status in {"available", "quarantined"} for item in result)


def test_benchmark_probe_records_reproducible_result():
    result = benchmark_callable(
        candidate_name="test-component",
        kind=ComponentKind.GATEWAY,
        runner=lambda: {"typed": True},
        version="test-v1",
        route_identity="test/direct/test-v1",
    )
    assert result.status == "measured"
    assert result.benchmark_hash is not None
    assert len(result.resource_samples) == 2


def test_benchmark_hash_does_not_include_wall_clock_latency():
    first = benchmark_callable(
        candidate_name="test-component",
        kind=ComponentKind.GATEWAY,
        runner=lambda: {"typed": True},
        version="test-v1",
        route_identity="test/direct/test-v1",
    )
    second = benchmark_callable(
        candidate_name="test-component",
        kind=ComponentKind.GATEWAY,
        runner=lambda: {"typed": True},
        version="test-v1",
        route_identity="test/direct/test-v1",
    )
    assert first.benchmark_hash == second.benchmark_hash


def test_gateway_bakeoff_hashes_typed_request_and_response_without_latency():
    route = GatewayRoute(provider="local", model="recovery", gateway="direct")
    request = GatewayRequest(
        route=route,
        messages=(GatewayMessage(role="user", content="probe"),),
        prompt_version="phase0-probe-v1",
    )
    first = benchmark_gateway_adapter(
        candidate_name="direct_api",
        adapter=LocalDeterministicGateway(),
        request=request,
        version="local-v1",
    )
    second = benchmark_gateway_adapter(
        candidate_name="direct_api",
        adapter=LocalDeterministicGateway(),
        request=request,
        version="local-v1",
    )
    assert first.status == "measured"
    assert first.request_hash == request.content_hash()
    assert first.response_hash is not None
    assert first.benchmark_hash == second.benchmark_hash
    assert first.route_identity == "local/direct/recovery"


def test_phase0_gate_rejects_false_pass_without_24_hour_evidence():
    result = BakeoffResult(
        candidate_name="direct_api",
        kind=ComponentKind.GATEWAY,
        status="selected",
        version="provider-v1",
        route_identity="provider/direct/provider-v1",
        stability_hours_measured=1,
        stability_passed=False,
    )
    with pytest.raises(ValueError, match="24-hour"):
        BakeoffGate(
            selected_components=("direct_api",),
            results=(result,),
            exact_versions_reproducible=True,
            unexplained_memory_growth=False,
            decision="passed",
        )


def test_phase0_gate_requires_privacy_and_failure_evidence():
    result = BakeoffResult(
        candidate_name="direct_api",
        kind=ComponentKind.GATEWAY,
        status="selected",
        version="provider-v1",
        route_identity="provider/direct/provider-v1",
        privacy_passed=False,
        failure_handling_passed=True,
        stability_hours_measured=24,
        stability_passed=True,
    )
    with pytest.raises(ValueError, match="route/resource evidence"):
        BakeoffGate(
            selected_components=("direct_api",),
            results=(result,),
            exact_versions_reproducible=True,
            unexplained_memory_growth=False,
            decision="passed",
        )


def test_phase0_gate_records_are_durable_and_replayable(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "phase0.sqlite")
    gate = BakeoffGate(
        selected_components=(),
        results=(),
        exact_versions_reproducible=False,
        unexplained_memory_growth=False,
        decision="pending",
    )
    record_bakeoff_gate(ledgers, gate)
    record_bakeoff_gate(ledgers, gate)
    assert recorded_bakeoff_gates(ledgers) == (gate,)
