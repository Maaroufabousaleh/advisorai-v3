from datetime import UTC, datetime
from math import inf

import pytest

from advisorai.ports import (
    EventEnvelope,
    GatewayMessage,
    GatewayRequest,
    GatewayResponse,
    GatewayRoute,
    GatewayTool,
)


def test_gateway_request_hash_is_stable_without_request_identity():
    route = GatewayRoute(provider="test", model="test-model", gateway="direct")
    first = GatewayRequest(
        route=route,
        messages=(GatewayMessage(role="user", content="ping"),),
        tools=(
            GatewayTool(name="typed_tool", input_schema_version="v1", output_schema_version="v1"),
        ),
        prompt_version="prompt-v1",
        created_at=datetime(2026, 8, 4, tzinfo=UTC),
    )
    second = first.model_copy(update={"request_id": first.request_id.hex})
    assert first.content_hash() == second.content_hash()


def test_event_envelope_normalizes_to_utc():
    envelope = EventEnvelope(
        event_type="market_message", occurred_at=datetime(2026, 8, 4, 8, tzinfo=UTC)
    )
    assert envelope.occurred_at.tzinfo is UTC


def test_gateway_contract_cannot_expose_trading_tools():
    with pytest.raises(ValueError, match="trading authority"):
        GatewayTool(name="submit_order", input_schema_version="v1", output_schema_version="v1")


def test_gateway_contract_rejects_order_aliases_and_nonfinite_costs():
    with pytest.raises(ValueError, match="trading authority"):
        GatewayTool(name="place-order", input_schema_version="v1", output_schema_version="v1")
    with pytest.raises(ValueError, match="finite"):
        from uuid import uuid4

        GatewayResponse(
            request_id=uuid4(),
            route=GatewayRoute(provider="test", model="test-model", gateway="direct"),
            content="ok",
            latency_ms=0,
            input_tokens=0,
            output_tokens=0,
            estimated_cost_usd=inf,
        )


def test_gateway_response_rejects_malformed_or_unauthorized_tool_calls():
    route = GatewayRoute(provider="test", model="test-model", gateway="direct")
    base = {
        "request_id": __import__("uuid").uuid4(),
        "route": route,
        "content": "ok",
        "latency_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": 0,
    }
    with pytest.raises(ValueError, match="non-blank name"):
        GatewayResponse(**base, tool_calls=({"arguments": {}},))
    with pytest.raises(ValueError, match="trading authority"):
        GatewayResponse(**base, tool_calls=({"name": "orders.create", "arguments": {}},))
    with pytest.raises(ValueError, match="arguments"):
        GatewayResponse(**base, tool_calls=({"name": "read_orderbook", "arguments": []},))
