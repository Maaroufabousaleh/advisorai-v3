"""Safety regressions for explicit model invocation modes and cost admission."""

from __future__ import annotations

import json

import pytest

from advisorai.gateway.core import GatewayAttempt, GatewayRecorder
from advisorai.integrations import HttpClientConfig, OpenAICompatibleGatewayAdapter, SafeHttpClient
from advisorai.integrations.llm import GatewayTransportError
from advisorai.ports import (
    GATEWAY_OUTPUT_SCHEMAS,
    GatewayInvocationMode,
    GatewayMessage,
    GatewayOutputKind,
    GatewayRequest,
    GatewayResponse,
    GatewayRoute,
    GatewayTool,
    GenerationBudget,
    ToolExecutionStatus,
)


def _route() -> GatewayRoute:
    return GatewayRoute(
        provider="reviewed-provider",
        model="reviewed-model",
        gateway="direct",
        endpoint_variant="reviewed-endpoint",
        schema_mode="typed_json",
        retention_policy="zero",
        training_policy="no_training_zdr",
        terms_verified=True,
        terms_reference="inventory:test",
    )


def _tool(*, description_size: int = 0) -> GatewayTool:
    return GatewayTool(
        name="read_evidence",
        input_schema_version="v1",
        output_schema_version="v1",
        input_schema={
            "type": "object",
            "description": "x" * description_size,
            "properties": {"evidence_id": {"type": "string"}},
            "required": ["evidence_id"],
            "additionalProperties": False,
        },
    )


def _success(*, content: object = "{}", tool_call: bool = False) -> bytes:
    message: dict[str, object] = {"content": content}
    if tool_call:
        message = {
            "content": None,
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": "read_evidence",
                        "arguments": '{"evidence_id":"e-1"}',
                    },
                }
            ],
        }
    return json.dumps(
        {
            "id": "provider-request-1",
            "provider": "reviewed-provider",
            "model": "reviewed-model",
            "provider_variant": "reviewed-endpoint",
            "choices": [{"message": message}],
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "cost": 0.000001,
            },
        }
    ).encode()


def _adapter(
    responses: list[tuple[int, bytes, tuple[tuple[str, str], ...]]],
    calls: list[dict[str, object]],
) -> OpenAICompatibleGatewayAdapter:
    def requester(method, url, headers, body, timeout):
        calls.append(json.loads(body))
        return responses.pop(0)

    client = SafeHttpClient(
        HttpClientConfig(allowed_hosts=("provider.example.test",), requests_per_second=100),
        base_url="https://provider.example.test/v1",
        requester=requester,
        sleeper=lambda _: None,
    )
    return OpenAICompatibleGatewayAdapter(_route(), client, api_key="test-key")


def _admitted_options(*, include_prices: bool = True) -> dict[str, object]:
    provider: dict[str, object] = {
        "only": ["reviewed-provider"],
        "allow_fallbacks": False,
        "zdr": True,
        "data_collection": "deny",
        "require_parameters": True,
    }
    if include_prices:
        provider["max_price"] = {"prompt": 0.01, "completion": 0.01, "request": 0}
    return {"provider": provider}


def _request(
    *,
    mode: GatewayInvocationMode = GatewayInvocationMode.STRUCTURED_OUTPUT,
    tools: tuple[GatewayTool, ...] = (),
    options: dict[str, object] | None = None,
    **updates: object,
) -> GatewayRequest:
    values: dict[str, object] = {
        "route": _route(),
        "messages": (GatewayMessage(role="user", content="return safe evidence"),),
        "prompt_version": "invocation-v1",
        "invocation_mode": mode,
        "tools": tools,
        "provider_options": options or _admitted_options(),
        # This is the exact capability snapshot a RouteProfile writes before
        # invoking a remote adapter.
        "admission_capabilities_enforced": True,
        "admission_supports_tools": True,
        "admission_supports_tool_choice_required": True,
        "admission_supports_structured_output": True,
    }
    values.update(updates)
    return GatewayRequest(**values)


def test_tool_required_sends_admitted_tool_choice_and_accepts_valid_tool_call():
    calls: list[dict[str, object]] = []
    response = _adapter([(200, _success(tool_call=True), ())], calls).complete(
        _request(mode=GatewayInvocationMode.TOOL_REQUIRED, tools=(_tool(),))
    )

    assert calls[0]["tool_choice"] == "required"
    assert "response_format" not in calls[0]
    assert response.tool_used is True
    assert response.tool_calls[0]["name"] == "read_evidence"


def test_tool_optional_accepts_a_valid_tool_call_with_provider_explanatory_text():
    calls: list[dict[str, object]] = []
    body = _success(tool_call=True)
    decoded = json.loads(body)
    decoded["choices"][0]["message"]["content"] = "I selected the reviewed tool."
    response = _adapter([(200, json.dumps(decoded).encode(), ())], calls).complete(
        _request(mode=GatewayInvocationMode.TOOL_OPTIONAL, tools=(_tool(),))
    )

    assert response.tool_used is True
    assert response.typed_payload is None


def test_tool_required_content_only_output_fails_closed():
    calls: list[dict[str, object]] = []
    adapter = _adapter([(200, _success(content="{}"), ())], calls)

    with pytest.raises(GatewayTransportError, match="required-tool response omitted"):
        adapter.complete(_request(mode=GatewayInvocationMode.TOOL_REQUIRED, tools=(_tool(),)))

    assert len(calls) == 1


def test_tool_optional_typed_result_records_that_no_tool_was_used():
    calls: list[dict[str, object]] = []
    request = _request(mode=GatewayInvocationMode.TOOL_OPTIONAL, tools=(_tool(),))
    response = _adapter([(200, _success(), ())], calls).complete(request)
    recorder = GatewayRecorder()
    attempt = GatewayAttempt("direct_provider", request.route, succeeded=True, latency_ms=0)
    record = recorder.record_call(request, attempt, response)

    assert response.tool_used is False
    assert response.tool_called is False
    assert response.tool_execution_status is ToolExecutionStatus.NOT_EXECUTED
    assert record.tool_used is False
    assert record.tool_called is False
    assert record.tool_execution_status is ToolExecutionStatus.NOT_EXECUTED
    assert record.invocation_mode is GatewayInvocationMode.TOOL_OPTIONAL


def test_structured_output_unexpected_tool_call_fails_closed():
    calls: list[dict[str, object]] = []
    adapter = _adapter([(200, _success(tool_call=True), ())], calls)

    with pytest.raises(GatewayTransportError, match="unexpected tool calls"):
        adapter.complete(_request())

    assert calls[0]["response_format"]["json_schema"]["strict"] is True


def test_invocation_mode_request_contract_rejects_invalid_tool_shapes():
    with pytest.raises(ValueError, match="cannot expose tools"):
        _request(tools=(_tool(),))
    with pytest.raises(ValueError, match="require at least one reviewed tool"):
        _request(mode=GatewayInvocationMode.TOOL_REQUIRED)


def test_tool_bearing_requests_require_an_explicit_invocation_mode():
    with pytest.raises(ValueError, match="require an explicit invocation_mode"):
        GatewayRequest(
            route=_route(),
            messages=(GatewayMessage(role="user", content="read reviewed evidence"),),
            tools=(_tool(),),
            prompt_version="missing-tool-mode-v1",
        )


def test_model_copy_cannot_bypass_the_explicit_tool_mode_contract():
    structured = GatewayRequest(
        route=_route(),
        messages=(GatewayMessage(role="user", content="return typed evidence"),),
        prompt_version="model-copy-tool-mode-v1",
    )

    with pytest.raises(ValueError, match="cannot expose tools"):
        structured.model_copy(update={"tools": (_tool(),)})


def test_requests_without_tools_default_to_structured_output():
    request = GatewayRequest(
        route=_route(),
        messages=(GatewayMessage(role="user", content="return typed evidence"),),
        prompt_version="default-structured-v1",
    )

    assert request.invocation_mode is GatewayInvocationMode.STRUCTURED_OUTPUT


def test_explicit_optional_and_required_tool_modes_are_accepted():
    optional = _request(mode=GatewayInvocationMode.TOOL_OPTIONAL, tools=(_tool(),))
    required = _request(mode=GatewayInvocationMode.TOOL_REQUIRED, tools=(_tool(),))

    assert optional.invocation_mode is GatewayInvocationMode.TOOL_OPTIONAL
    assert required.invocation_mode is GatewayInvocationMode.TOOL_REQUIRED


def test_legacy_tool_payload_requires_an_explicit_migration_intent():
    payload: dict[str, object] = {
        "route": _route(),
        "messages": (GatewayMessage(role="user", content="read reviewed evidence"),),
        "tools": (_tool(),),
        "prompt_version": "legacy-tool-mode-v1",
    }

    with pytest.raises(ValueError, match="require an explicit optional or required tool mode"):
        GatewayRequest.from_legacy_payload(payload)

    migrated = GatewayRequest.from_legacy_payload(
        payload,
        tool_invocation_mode=GatewayInvocationMode.TOOL_REQUIRED,
    )

    assert migrated.invocation_mode is GatewayInvocationMode.TOOL_REQUIRED


def test_model_response_cannot_claim_that_a_tool_executed():
    with pytest.raises(ValueError, match="cannot claim tool execution"):
        GatewayResponse(
            request_id=_request().request_id,
            route=_route(),
            content="typed provider output",
            latency_ms=0,
            input_tokens=0,
            output_tokens=0,
            estimated_cost_usd=0,
            tool_execution_status=ToolExecutionStatus.SUCCEEDED,
        )


def test_required_tool_choice_without_frozen_support_rejects_before_network_access():
    calls: list[dict[str, object]] = []
    request = _request(
        mode=GatewayInvocationMode.TOOL_REQUIRED,
        tools=(_tool(),),
        admission_supports_tool_choice_required=False,
    )

    with pytest.raises(GatewayTransportError, match="requires frozen endpoint admission support"):
        _adapter([(200, _success(tool_call=True), ())], calls).complete(request)

    assert calls == []


def test_large_tool_schema_is_counted_and_rejected_before_network_access():
    calls: list[dict[str, object]] = []
    request = _request(
        mode=GatewayInvocationMode.TOOL_OPTIONAL,
        tools=(_tool(description_size=12_000),),
        generation_budget=GenerationBudget(max_input_tokens=100),
    )

    with pytest.raises(GatewayTransportError, match="input exceeds") as caught:
        _adapter([(200, _success(), ())], calls).complete(request)

    assert calls == []
    assert caught.value.failure_metadata["estimated_input_tokens"] > 100


def test_large_response_schema_is_counted_and_rejected_before_network_access(monkeypatch):
    class LargeSchema:
        @classmethod
        def model_json_schema(cls) -> dict[str, object]:
            return {
                "type": "object",
                "description": "x" * 12_000,
                "properties": {},
                "additionalProperties": False,
            }

    monkeypatch.setitem(GATEWAY_OUTPUT_SCHEMAS, GatewayOutputKind.GENERIC, LargeSchema)
    calls: list[dict[str, object]] = []
    request = _request(generation_budget=GenerationBudget(max_input_tokens=100))

    with pytest.raises(GatewayTransportError, match="input exceeds") as caught:
        _adapter([(200, _success(), ())], calls).complete(request)

    assert calls == []
    assert caught.value.failure_metadata["estimated_input_tokens"] > 100


def test_direct_remote_uses_safe_budget_without_explicit_budget():
    calls: list[dict[str, object]] = []
    request = GatewayRequest(
        route=_route(),
        messages=(GatewayMessage(role="user", content="return typed JSON"),),
        prompt_version="safe-default-v1",
    )

    _adapter([(200, _success(), ())], calls).complete(request)

    assert calls[0]["max_tokens"] == 256


def test_paid_remote_without_admitted_prices_makes_zero_network_calls():
    calls: list[dict[str, object]] = []
    request = _request(options=_admitted_options(include_prices=False))

    with pytest.raises(GatewayTransportError, match="omitted admitted price limits"):
        _adapter([(200, _success(), ())], calls).complete(request)

    assert calls == []
