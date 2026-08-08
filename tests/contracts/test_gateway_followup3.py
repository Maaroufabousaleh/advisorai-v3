"""Final regression coverage for governed OpenRouter failure and budget safety."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from advisorai.gateway import (
    GatewayPolicyConfig,
    PolicyGateway,
    ProviderEndpointAdmission,
    ProviderEndpointInventory,
    ProviderRoutePolicy,
    ProviderTerms,
    RouteProfile,
    TypedGatewayAdapter,
)
from advisorai.gateway.core import GatewayRecorder
from advisorai.integrations import (
    GatewayTransportError,
    HttpClientConfig,
    OpenAICompatibleGatewayAdapter,
    SafeHttpClient,
)
from advisorai.ports import (
    GatewayDataClass,
    GatewayInvocationMode,
    GatewayMessage,
    GatewayRequest,
    GatewayRoute,
    GatewayTier,
    GatewayTool,
    GenerationBudget,
    RouteTier,
)

_HASH = "b" * 64


def _route() -> GatewayRoute:
    return GatewayRoute(
        provider="novita",
        model="inclusionai/ling-2.6-flash",
        gateway="openrouter",
        endpoint_variant="novita",
        retention_policy="zero",
        training_policy="no_training_zdr",
        terms_verified=True,
        terms_reference="inventory:novita:2026-08-06",
    )


def _admission() -> ProviderEndpointAdmission:
    return ProviderEndpointAdmission(
        provider_selector_slug="novita",
        allowed_provider_display_names=("Novita",),
        requested_model="inclusionai/ling-2.6-flash",
        allowed_top_level_models=("inclusionai/ling-2.6-flash",),
        allowed_resolved_models=("inclusionai/ling-2.6-flash-20260421",),
        gateway="openrouter",
        zdr=True,
        data_collection="deny",
        input_price_per_million=0.1,
        output_price_per_million=0.3,
        request_price=0,
        inventory_artifact_hash=_HASH,
        inventory_timestamp=datetime(2026, 8, 6, tzinfo=UTC),
        terms_reference="inventory:novita:2026-08-06",
        admission_version="v1",
        supports_tools=True,
        supports_tool_choice_required=True,
        supports_structured_output=True,
        allow_response_format_with_tools=False,
    )


def _terms(tier: GatewayTier) -> ProviderTerms:
    return ProviderTerms(
        name=tier.value,
        tier=tier,
        allowed_data_classes=(GatewayDataClass.PUBLIC, GatewayDataClass.INTERNAL_SANITIZED),
        retention_policy="zero" if tier is GatewayTier.PRIVATE else "public",
        training_policy="no_training_zdr" if tier is GatewayTier.PRIVATE else "opt_out_no_training",
        terms_verified=True,
        terms_reference="terms:v1",
    )


def _config() -> GatewayPolicyConfig:
    return GatewayPolicyConfig(
        contributor_terms=_terms(GatewayTier.CONTRIBUTOR),
        private_terms=_terms(GatewayTier.PRIVATE),
        route_order=("private",),
    )


def _policy() -> ProviderRoutePolicy:
    admission = _admission()
    return ProviderRoutePolicy(
        route_tier=RouteTier.PRIVATE_WORKER,
        provider_only=("novita",),
        model_only=("inclusionai/ling-2.6-flash",),
        data_collection="deny",
        zdr=True,
        allow_fallbacks=False,
        endpoint_admission=admission,
        endpoint_inventory=ProviderEndpointInventory(
            inventory_artifact_hash=_HASH,
            admissions=(admission,),
        ),
    )


def _request(**updates: object) -> GatewayRequest:
    values: dict[str, object] = {
        "route": _route(),
        "messages": (GatewayMessage(role="user", content="summarize public evidence"),),
        "prompt_version": "followup3-v1",
        "data_class": GatewayDataClass.INTERNAL_SANITIZED,
    }
    values.update(updates)
    return GatewayRequest(**values)


def _success_body(*, tool_call: bool = False) -> bytes:
    message: dict[str, object] = {"content": "{}"}
    if tool_call:
        message = {
            "content": None,
            "tool_calls": [
                {
                    "type": "function",
                    "function": {"name": "read_evidence", "arguments": '{"evidence_id":"e-1"}'},
                }
            ],
        }
    return json.dumps(
        {
            "id": "request-1",
            "model": "inclusionai/ling-2.6-flash",
            "choices": [{"message": message}],
            "openrouter_metadata": {
                "strategy": "direct",
                "attempt": 1,
                "endpoints": {
                    "available": [
                        {
                            "provider": "Novita",
                            "model": "inclusionai/ling-2.6-flash-20260421",
                            "selected": True,
                            "is_byok": False,
                        }
                    ]
                },
            },
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "cost": 0.00000102},
        }
    ).encode()


def _adapter(
    responses: list[tuple[int, bytes, tuple[tuple[str, str], ...]]],
    *,
    clock=None,
    retry_sleeper=None,
    calls: list[tuple[dict[str, object], float]] | None = None,
) -> OpenAICompatibleGatewayAdapter:
    observed_calls = calls if calls is not None else []

    def requester(method, url, headers, body, timeout):
        observed_calls.append((json.loads(body), timeout))
        return responses.pop(0)

    client = SafeHttpClient(
        HttpClientConfig(allowed_hosts=("openrouter.ai",), requests_per_second=100),
        base_url="https://openrouter.ai/api/v1",
        requester=requester,
        sleeper=lambda _: None,
    )
    return OpenAICompatibleGatewayAdapter(
        _route(),
        client,
        api_key="test-key",
        clock=clock or (lambda: 0.0),
        retry_sleeper=retry_sleeper or (lambda _: None),
        retry_jitter=lambda _: 0,
    )


def _nested_real_429() -> bytes:
    # Structurally mirrors the observed 429: routing candidates sit in
    # error.openrouter_metadata while provider context is nested deeper.
    return json.dumps(
        {
            "error": {
                "message": "raw provider message must never be retained",
                "user_id": "user-should-never-be-ledgered",
                "openrouter_metadata": {
                    "attempt": 4,
                    "endpoints": {
                        "available": [
                            {
                                "provider": "Novita",
                                "model": "inclusionai/ling-2.6-flash-20260421",
                                "selected": False,
                            }
                        ]
                    },
                },
                "error": {
                    "type": "upstream_rate_limit",
                    "code": "novita_shared_pool",
                    "message": "another raw provider message",
                    "metadata": {
                        "provider_name": "Novita",
                        "limit_source": "upstream_provider_shared_pool",
                        "is_byok": False,
                        "attempted_model": "inclusionai/ling-2.6-flash-20260421",
                        "user_id": "nested-user-id",
                    },
                },
            }
        }
    ).encode()


def test_nested_openrouter_429_records_safe_attempt_evidence_only(tmp_path: Path):
    adapter = _adapter([(429, _nested_real_429(), ())])
    recorder = GatewayRecorder()
    gateway = PolicyGateway(
        config=_config(),
        profiles=(
            RouteProfile(
                "private", RouteTier.PRIVATE_WORKER, adapter, _policy(), _terms(GatewayTier.PRIVATE)
            ),
        ),
        recorder=recorder,
    )

    response = gateway.complete(
        _request(
            generation_budget=GenerationBudget(maximum_attempts=1, max_expected_cost_usd=0.001)
        )
    )

    assert response.actual_provider is None
    assert len(recorder.calls) == 1
    failure = recorder.calls[0].failure_metadata
    assert failure["provider_name"] == "Novita"
    assert failure["limit_source"] == "upstream_provider_shared_pool"
    assert failure["is_byok"] is False
    assert failure["provider_code"] == "novita_shared_pool"
    assert failure["raw_provider_error_classification"] == "upstream_rate_limit"
    assert failure["resolved_model"] == "inclusionai/ling-2.6-flash-20260421"
    assert failure["attempt"] == 4
    assert failure["attempted_endpoints"][0]["selected"] is False
    serialised = recorder.calls[0].model_dump_json()
    assert "raw provider message" not in serialised
    assert "user-should-never" not in serialised
    assert "nested-user-id" not in serialised


def test_remote_adapter_requires_profile_and_legacy_path_preserves_actual_identity():
    remote = _adapter([(200, _success_body(), ())])
    with pytest.raises(ValueError, match="RouteProfile"):
        PolicyGateway(config=_config(), private=remote)

    route = _route()
    local = TypedGatewayAdapter(
        "local-test",
        route,
        lambda _: {
            "content": "typed",
            "typed_payload": {"ok": True},
            "route_provider": route.provider,
            "route_model": route.model,
            "route_gateway": route.gateway,
            "route_endpoint_variant": route.endpoint_variant,
            "actual_provider": "Observed Provider",
            "actual_model": "resolved-model-v1",
            "actual_gateway": route.gateway,
            "actual_endpoint_variant": "sha256:proof",
            "observed_provider_name": "Observed Provider",
            "top_level_response_model": route.model,
            "resolved_endpoint_model": "resolved-model-v1",
            "requested_provider_selector": route.provider,
            "requested_model": route.model,
            "requested_gateway": route.gateway,
            "requested_endpoint_selector": route.endpoint_variant,
            "endpoint_selector_proof": "sha256:proof",
            "endpoint_selected": True,
        },
    )
    legacy_config = GatewayPolicyConfig(
        contributor_terms=_terms(GatewayTier.CONTRIBUTOR),
        private_terms=_terms(GatewayTier.PRIVATE),
    )
    response = PolicyGateway(contributor=local, config=legacy_config).complete(
        _request(route=route, data_class=GatewayDataClass.PUBLIC)
    )
    assert response.actual_provider == "Observed Provider"
    assert response.actual_model == "resolved-model-v1"


def test_pre_dispatch_cost_rejects_without_network_and_default_remote_budget_is_safe():
    calls: list[tuple[dict[str, object], float]] = []
    adapter = _adapter([(200, _success_body(), ())], calls=calls)
    priced = _request(
        provider_options={
            "provider": {
                "only": ["novita"],
                "allow_fallbacks": False,
                "zdr": True,
                "data_collection": "deny",
                "require_parameters": True,
                "max_price": {"prompt": 1.0, "completion": 1.0, "request": 0},
            }
        },
        generation_budget=GenerationBudget(max_output_tokens=1000, max_expected_cost_usd=0.0001),
    )
    with pytest.raises(GatewayTransportError, match="maximum expected cost"):
        adapter.complete(priced)
    assert calls == []

    adapter = _adapter([(200, _success_body(), ())], calls=calls)
    gateway = PolicyGateway(
        config=_config(),
        profiles=(
            RouteProfile(
                "private", RouteTier.PRIVATE_WORKER, adapter, _policy(), _terms(GatewayTier.PRIVATE)
            ),
        ),
    )
    gateway.complete(_request())
    assert calls[-1][0]["max_tokens"] == 256


def test_total_deadline_bounds_retry_wait_and_stops_new_attempts():
    now = [0.0]
    calls: list[tuple[dict[str, object], float]] = []
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    rate_limited = _nested_real_429()
    adapter = _adapter(
        [(429, rate_limited, (("Retry-After", "1"),)), (200, _success_body(), ())],
        clock=lambda: now[0],
        retry_sleeper=sleep,
        calls=calls,
    )
    response = adapter.complete(
        _request(
            data_class=GatewayDataClass.PUBLIC,
            generation_budget=GenerationBudget(timeout_seconds=2, maximum_attempts=2),
        )
    )
    assert response.endpoint_selected is True
    assert waits == [1.0]
    assert len(calls) == 2
    assert calls[0][1] == pytest.approx(2.0)
    assert calls[1][1] == pytest.approx(1.0)

    calls.clear()
    adapter = _adapter(
        [(429, rate_limited, (("Retry-After", "1"),))],
        clock=lambda: 0.0,
        calls=calls,
    )
    with pytest.raises(GatewayTransportError, match="deadline exhausted before retry"):
        adapter.complete(
            _request(
                data_class=GatewayDataClass.PUBLIC,
                generation_budget=GenerationBudget(timeout_seconds=1, maximum_attempts=2),
            )
        )
    assert len(calls) == 1


def test_tool_only_and_structured_output_modes_are_never_combined_by_default():
    calls: list[tuple[dict[str, object], float]] = []
    adapter = _adapter([(200, _success_body(tool_call=True), ())], calls=calls)
    tool = GatewayTool(
        name="read_evidence",
        input_schema_version="v1",
        output_schema_version="v1",
        input_schema={
            "type": "object",
            "properties": {"evidence_id": {"type": "string"}},
            "required": ["evidence_id"],
            "additionalProperties": False,
        },
    )
    adapter.complete(
        _request(
            data_class=GatewayDataClass.PUBLIC,
            tools=(tool,),
            invocation_mode=GatewayInvocationMode.TOOL_OPTIONAL,
        )
    )
    sent = calls[0][0]
    assert "response_format" not in sent
    assert sent["tools"][0]["function"]["parameters"] == tool.input_schema

    calls.clear()
    adapter = _adapter([(200, _success_body(tool_call=True), ())], calls=calls)
    with pytest.raises(GatewayTransportError, match="unexpected tool calls"):
        adapter.complete(_request(data_class=GatewayDataClass.PUBLIC))
    assert calls[0][0]["response_format"]["json_schema"]["strict"] is True
