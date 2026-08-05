import pytest

from advisorai.gateway import (
    GatewayChain,
    GatewayFailure,
    GatewayRecorder,
    LiteLLMGatewayAdapter,
    LocalDeterministicGateway,
    OmniRouteGatewayAdapter,
)
from advisorai.ports import GatewayMessage, GatewayRequest, GatewayRoute


def _request():
    return GatewayRequest(
        route=GatewayRoute(provider="test", model="test", gateway="direct"),
        messages=(GatewayMessage(role="user", content="ping"),),
        prompt_version="p1",
    )


def test_gateway_records_route_identity_and_explicit_fallback():
    recorder = GatewayRecorder()

    class Failing:
        name = "failing"

        def complete(self, request):
            raise RuntimeError("provider down")

    chain = GatewayChain((Failing(), LocalDeterministicGateway()), recorder)
    response = chain.complete(_request())
    assert response.route.gateway == "direct"
    assert [attempt.succeeded for attempt in recorder.attempts] == [False, True]


def test_gateway_refuses_secret_recovery_request():
    with pytest.raises(GatewayFailure, match="secret"):
        LocalDeterministicGateway().complete(
            _request().model_copy(update={"privacy_class": "secret"})
        )


def test_gateway_chain_dispatches_each_adapter_on_its_pinned_fallback_route():
    primary_route = GatewayRoute(
        provider="provider", model="model", gateway="litellm", fallback_chain=("omniroute",)
    )
    fallback_route = GatewayRoute(provider="provider", model="model", gateway="omniroute")

    class FailingTransport:
        def __call__(self, request):
            raise RuntimeError("primary unavailable")

    chain = GatewayChain(
        (
            LiteLLMGatewayAdapter(primary_route, FailingTransport()),
            OmniRouteGatewayAdapter(
                fallback_route,
                lambda request: {"content": "fallback", "typed_payload": {"ok": True}},
            ),
        )
    )
    response = chain.complete(_request().model_copy(update={"route": primary_route}))
    assert response.route.gateway == "omniroute"
    assert chain.recorder.attempts[-1].succeeded
