import pytest

from advisorai.gateway import LiteLLMGatewayAdapter, OmniRouteGatewayAdapter
from advisorai.ports import GatewayMessage, GatewayRequest, GatewayRoute


def test_litellm_adapter_preserves_pinned_route_and_typed_payload():
    route = GatewayRoute(provider="provider", model="model", gateway="litellm")
    adapter = LiteLLMGatewayAdapter(
        route,
        lambda request: {
            "content": "ok",
            "typed_payload": {"decision": "abstain"},
            "provider_request_id": "p-1",
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
    route = GatewayRoute(provider="provider", model="model", gateway="omniroute")
    response = OmniRouteGatewayAdapter(
        route, lambda request: {"content": "challenger", "typed_payload": {"ok": True}}
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
    route = GatewayRoute(provider="provider", model="model", gateway="litellm")
    request = GatewayRequest(
        route=route,
        messages=(GatewayMessage(role="user", content="test"),),
        prompt_version="p1",
    )
    adapter = LiteLLMGatewayAdapter(
        route, lambda _: {"content": "bad", "tool_calls": ({"arguments": {}},)}
    )
    with pytest.raises(ValueError, match="non-blank name"):
        adapter.complete(request)
