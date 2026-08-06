import pytest

from advisorai.gateway import LiteLLMGatewayAdapter, OmniRouteGatewayAdapter
from advisorai.ports import GatewayMessage, GatewayRequest, GatewayRoute


def test_litellm_adapter_preserves_pinned_route_and_typed_payload():
    route = GatewayRoute(
        provider="provider", model="model", gateway="litellm", endpoint_variant="provider/model"
    )
    adapter = LiteLLMGatewayAdapter(
        route,
        lambda request: {
            "content": "ok",
            "typed_payload": {"decision": "abstain"},
            "provider_request_id": "p-1",
            "actual_provider": "provider",
            "actual_model": "model",
            "actual_gateway": "litellm",
            "actual_endpoint_variant": "provider/model",
        },
    )
    response = adapter.complete(
        GatewayRequest(
            route=route,
            messages=(GatewayMessage(role="user", content="test"),),
            prompt_version="p1",
        )
    )
    assert response.route == route
    assert response.typed_payload == {"decision": "abstain"}


def test_omniroute_challenger_uses_the_same_explicit_typed_boundary():
    route = GatewayRoute(
        provider="provider", model="model", gateway="omniroute", endpoint_variant="provider/model"
    )
    response = OmniRouteGatewayAdapter(
        route,
        lambda request: {
            "content": "challenger",
            "typed_payload": {"ok": True},
            "actual_provider": "provider",
            "actual_model": "model",
            "actual_gateway": "omniroute",
            "actual_endpoint_variant": "provider/model",
        },
    ).complete(
        GatewayRequest(
            route=route,
            messages=(GatewayMessage(role="user", content="test"),),
            prompt_version="p1",
        )
    )
    assert response.route == route
    assert response.content == "challenger"


def test_gateway_adapter_does_not_silently_drop_malformed_tool_calls():
    route = GatewayRoute(
        provider="provider", model="model", gateway="litellm", endpoint_variant="provider/model"
    )
    request = GatewayRequest(
        route=route,
        messages=(GatewayMessage(role="user", content="test"),),
        prompt_version="p1",
    )
    adapter = LiteLLMGatewayAdapter(
        route,
        lambda _: {
            "content": "bad",
            "tool_calls": ({"arguments": {}},),
            "actual_provider": "provider",
            "actual_model": "model",
            "actual_gateway": "litellm",
            "actual_endpoint_variant": "provider/model",
        },
    )
    with pytest.raises(ValueError, match="non-blank name"):
        adapter.complete(request)
