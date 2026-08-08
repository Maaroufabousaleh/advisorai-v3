import pytest

from advisorai.gateway import (
    GatewayChain,
    GatewayFailure,
    GatewayRecorder,
    LiteLLMGatewayAdapter,
    LocalDeterministicGateway,
    OmniRouteGatewayAdapter,
)
from advisorai.ledger import LedgerNamespace, SqliteLedgers
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
        provider="provider",
        model="model",
        gateway="litellm",
        endpoint_variant="primary",
        fallback_chain=("omniroute",),
    )
    fallback_route = GatewayRoute(
        provider="provider", model="model", gateway="omniroute", endpoint_variant="fallback"
    )

    class FailingTransport:
        def __call__(self, request):
            raise RuntimeError("primary unavailable")

    chain = GatewayChain(
        (
            LiteLLMGatewayAdapter(primary_route, FailingTransport()),
            OmniRouteGatewayAdapter(
                fallback_route,
                lambda request: {
                    "content": "fallback",
                    "typed_payload": {"ok": True},
                    "actual_provider": "provider",
                    "actual_model": "model",
                    "actual_gateway": "omniroute",
                    "actual_endpoint_variant": "fallback",
                },
            ),
        )
    )
    response = chain.complete(_request().model_copy(update={"route": primary_route}))
    assert response.route.gateway == "omniroute"
    assert chain.recorder.attempts[-1].succeeded


def test_gateway_call_records_are_durable_and_do_not_store_prompt_or_secret(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "gateway.sqlite3")
    recorder = GatewayRecorder(ledgers)
    chain = GatewayChain((LocalDeterministicGateway(),), recorder)
    request = _request().model_copy(
        update={
            "messages": (GatewayMessage(role="user", content="token=do-not-store"),),
            "tool_version": "tools-v1",
        }
    )
    chain.complete(request)

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call.request_hash == request.content_hash()
    assert call.succeeded
    assert call.tool_version == "tools-v1"
    event = ledgers.events(LedgerNamespace.MODEL)[0]
    assert event.event_type == "gateway_call_recorded"
    serialized = str(event.payload)
    assert "token=do-not-store" not in serialized
    assert "secret" not in serialized.lower()

    # Retrying the same request is a ledger-idempotent replay.
    chain.complete(request)
    assert len(ledgers.events(LedgerNamespace.MODEL)) == 1
