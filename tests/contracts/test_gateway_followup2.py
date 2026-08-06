"""Regression coverage for the real OpenRouter observation/budget boundary."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from advisorai.gateway import (
    GatewayPolicyConfig,
    GatewayPolicyError,
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
from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.ports import (
    GatewayDataClass,
    GatewayMessage,
    GatewayRequest,
    GatewayRoute,
    GatewayTier,
    GenerationBudget,
    RouteTier,
)

INVENTORY_HASH = "a" * 64


def _admission(*, resolved: str = "inclusionai/ling-2.6-flash-20260421") -> ProviderEndpointAdmission:
    return ProviderEndpointAdmission(
        provider_selector_slug="novita",
        allowed_provider_display_names=("Novita",),
        requested_model="inclusionai/ling-2.6-flash",
        allowed_top_level_models=("inclusionai/ling-2.6-flash",),
        allowed_resolved_models=(resolved,),
        gateway="openrouter",
        zdr=True,
        data_collection="deny",
        input_price_per_million=0.1,
        output_price_per_million=0.3,
        request_price=0,
        inventory_artifact_hash=INVENTORY_HASH,
        inventory_timestamp=datetime(2026, 8, 6, tzinfo=UTC),
        terms_reference="inventory:novita:2026-08-06",
        admission_version="v1",
        supports_tools=True,
        supports_tool_choice_required=True,
        supports_structured_output=True,
        allow_response_format_with_tools=False,
    )


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


def _body(*, selected: bool = True, resolved: str = "inclusionai/ling-2.6-flash-20260421", cost: float = 0.00000102) -> bytes:
    return json.dumps(
        {
            "id": "gen-real-observation",
            "model": "inclusionai/ling-2.6-flash",
            "provider": "Novita",
            "choices": [{"message": {"content": "{}"}}],
            "openrouter_metadata": {
                "strategy": "direct",
                "attempt": 1,
                "endpoints": {
                    "available": [
                        {
                            "provider": "Novita",
                            "model": resolved,
                            "selected": selected,
                            "is_byok": False,
                        }
                    ]
                },
            },
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "cost": cost,
            },
        }
    ).encode()


def _request(route: GatewayRoute, **updates: object) -> GatewayRequest:
    values: dict[str, object] = {
        "route": route,
        "messages": (GatewayMessage(role="user", content="observe public evidence"),),
        "prompt_version": "followup2-v1",
        "data_class": GatewayDataClass.PUBLIC,
        "provider_options": {
            "provider": {
                "only": ["novita"],
                "allow_fallbacks": False,
                "zdr": True,
                "data_collection": "deny",
                "require_parameters": True,
                "max_price": {"prompt": 0.1, "completion": 0.3, "request": 0},
            }
        },
    }
    values.update(updates)
    return GatewayRequest(**values)


def _adapter(
    responses: list[tuple[int, bytes, tuple[tuple[str, str], ...]]],
    *,
    sleeps: list[float] | None = None,
    budget: GenerationBudget | None = None,
    jitter: bool = False,
) -> tuple[OpenAICompatibleGatewayAdapter, list[dict[str, object]]]:
    calls: list[dict[str, object]] = []

    def requester(method, url, headers, body, timeout):
        calls.append(json.loads(body))
        return responses.pop(0)

    client = SafeHttpClient(
        HttpClientConfig(allowed_hosts=("openrouter.ai",), requests_per_second=100),
        base_url="https://openrouter.ai/api/v1",
        requester=requester,
        sleeper=lambda _: None,
    )
    route = _route()
    adapter = OpenAICompatibleGatewayAdapter(
        route,
        client,
        api_key="test-key",
        retry_sleeper=(sleeps.append if sleeps is not None else (lambda _: None)),
        retry_jitter=(lambda _: 0) if not jitter else None,
    )
    return adapter, calls


def test_real_success_separates_selector_display_and_observed_models():
    route = _route()
    adapter, _ = _adapter([(200, _body(), ())])

    response = adapter.complete(_request(route))

    assert response.requested_provider_selector == "novita"
    assert response.observed_provider_name == "Novita"
    assert response.requested_model == "inclusionai/ling-2.6-flash"
    assert response.top_level_response_model == "inclusionai/ling-2.6-flash"
    assert response.resolved_endpoint_model == "inclusionai/ling-2.6-flash-20260421"
    assert response.requested_endpoint_selector == "novita"
    assert response.actual_endpoint_variant != "Novita"
    assert response.endpoint_selector_proof and response.endpoint_selector_proof != "novita"
    assert response.endpoint_selected is True
    assert response.routing_strategy == "direct"
    assert response.is_byok is False
    assert response.billed_cost_usd == pytest.approx(0.00000102)
    assert response.cost_metadata["usage_cost_usd"] == pytest.approx(0.00000102)


def test_frozen_admission_accepts_fixture_and_rejects_unknown_resolved_version():
    route = _route()
    admission = _admission()
    inventory = ProviderEndpointInventory(inventory_artifact_hash=INVENTORY_HASH, admissions=(admission,))
    policy = ProviderRoutePolicy(
        route_tier=RouteTier.PRIVATE_WORKER,
        provider_only=("novita",),
        model_only=(route.model,),
        data_collection="deny",
        zdr=True,
        allow_fallbacks=False,
        endpoint_admission=admission,
        endpoint_inventory=inventory,
    )
    adapter, _ = _adapter([(200, _body(), ())])
    response = adapter.complete(_request(route))
    policy.validate_response(response, pinned_route=route, request=_request(route))

    unknown_adapter, _ = _adapter([(200, _body(resolved="inclusionai/ling-2.6-flash-20260901"), ())])
    unknown = unknown_adapter.complete(_request(route))
    with pytest.raises(GatewayPolicyError, match="resolved endpoint model"):
        policy.validate_response(unknown, pinned_route=route, request=_request(route))


@pytest.mark.parametrize("available", [[], [{"provider": "Novita", "model": "inclusionai/ling-2.6-flash-20260421", "selected": True}, {"provider": "Novita", "model": "inclusionai/ling-2.6-flash-20260421", "selected": True}]])
def test_missing_or_multiple_selected_endpoints_fail_closed(available):
    body = json.loads(_body())
    body["openrouter_metadata"]["endpoints"]["available"] = available
    adapter, _ = _adapter([(200, json.dumps(body).encode(), ())])

    with pytest.raises(GatewayTransportError) as caught:
        adapter.complete(
            _request(_route(), generation_budget=GenerationBudget(maximum_attempts=1))
        )

    assert caught.value.failure_metadata["error_type"] == "selected_endpoint_count"
    assert caught.value.attempt_metadata[0]["attempted_endpoints"] == tuple(available)


def test_unselected_429_preserves_attempted_identity_but_never_actual_identity():
    body = json.dumps(
        {
            "error": {"type": "rate_limit", "code": "shared_pool"},
            "limit_source": "upstream_provider_shared_pool",
            "is_byok": False,
            "openrouter_metadata": {
                "endpoints": {
                    "available": [
                        {
                            "provider": "Novita",
                            "model": "inclusionai/ling-2.6-flash-20260421",
                            "selected": False,
                        }
                    ]
                }
            },
        }
    ).encode()
    adapter, _ = _adapter([(429, body, (("Retry-After", "17"),))], budget=GenerationBudget(maximum_attempts=1))

    with pytest.raises(GatewayTransportError) as caught:
        adapter.complete(
            _request(_route(), generation_budget=GenerationBudget(maximum_attempts=1))
        )

    metadata = caught.value.failure_metadata
    assert metadata["provider_name"] == "Novita"
    assert metadata["resolved_model"] == "inclusionai/ling-2.6-flash-20260421"
    assert metadata["limit_source"] == "upstream_provider_shared_pool"
    assert metadata["retry_after_seconds"] == 17.0
    assert metadata["attempted_endpoints"][0]["selected"] is False
    assert not hasattr(caught.value, "actual_provider")


def test_generation_controls_are_injected_and_caller_overrides_do_not_win():
    adapter, calls = _adapter([(200, _body(), ())])
    request = _request(
        _route(),
        generation_budget=GenerationBudget(max_output_tokens=37, max_expected_cost_usd=1),
        provider_options={
            "stream": True,
            "max_tokens": 999999,
            "max_completion_tokens": 999999,
            "temperature": 1,
            "response_format": {"type": "text"},
            "provider": {
                "only": ["novita"],
                "allow_fallbacks": False,
                "zdr": True,
                "data_collection": "deny",
                "require_parameters": True,
                "max_price": {"prompt": 0.1, "completion": 0.3, "request": 0},
            },
        },
    )
    adapter.complete(request)

    sent = calls[0]
    assert sent["stream"] is False
    assert sent["max_tokens"] == 37
    assert "max_completion_tokens" not in sent
    assert sent["temperature"] == 0
    assert sent["response_format"]["json_schema"]["name"] == "generic"
    assert sent["response_format"]["json_schema"]["strict"] is True


def test_billed_cost_and_output_budget_fail_deterministically():
    expensive, _ = _adapter([(200, _body(cost=2), ())])
    with pytest.raises(GatewayTransportError, match="billed cost"):
        expensive.complete(
            _request(_route(), generation_budget=GenerationBudget(max_billed_cost_usd=1, max_expected_cost_usd=3))
        )

    oversized_body = json.loads(_body())
    oversized_body["usage"]["completion_tokens"] = 99
    oversized, _ = _adapter([(200, json.dumps(oversized_body).encode(), ())])
    with pytest.raises(GatewayTransportError, match="output exceeds"):
        oversized.complete(
            _request(_route(), generation_budget=GenerationBudget(max_output_tokens=2, max_expected_cost_usd=1))
        )


def test_retry_after_retries_same_route_and_preserves_attempts():
    sleeps: list[float] = []
    failed = json.dumps(
        {
            "error": {"type": "rate_limit"},
            "openrouter_metadata": {
                "endpoints": {
                    "available": [
                        {"provider": "Novita", "model": "inclusionai/ling-2.6-flash-20260421", "selected": False}
                    ]
                }
            },
        }
    ).encode()
    adapter, calls = _adapter(
        [(429, failed, (("Retry-After", "3"),)), (200, _body(), ())],
        sleeps=sleeps,
    )

    response = adapter.complete(_request(_route(), generation_budget=GenerationBudget(maximum_attempts=2)))

    assert len(calls) == 2
    assert calls[0]["model"] == calls[1]["model"] == "inclusionai/ling-2.6-flash"
    assert calls[0]["provider"]["only"] == calls[1]["provider"]["only"] == ["novita"]
    assert sleeps == [3.0]
    assert len(response.attempt_metadata) == 1


def test_retry_exhaustion_abstains_without_cross_provider_fallback_and_ledger_attempts_are_unique(tmp_path: Path):
    failures = json.dumps({"error": {"type": "rate_limit"}}).encode()
    primary, _ = _adapter(
        [(429, failures, ()), (429, failures, ())],
        budget=GenerationBudget(maximum_attempts=2),
    )
    fallback_route = _route().model_copy(update={"provider": "other", "model": "other-model", "endpoint_variant": "other"})
    fallback = TypedGatewayAdapter(
        "other",
        fallback_route,
        lambda _: (_ for _ in ()).throw(AssertionError("cross-provider fallback")),
    )
    terms = ProviderTerms(
        name="private",
        tier=GatewayTier.PRIVATE,
        allowed_data_classes=(GatewayDataClass.PUBLIC, GatewayDataClass.INTERNAL_SANITIZED),
        retention_policy="zero",
        training_policy="no_training_zdr",
        terms_verified=True,
        terms_reference="t",
    )
    public_terms = ProviderTerms(
        name="public",
        tier=GatewayTier.CONTRIBUTOR,
        allowed_data_classes=(GatewayDataClass.PUBLIC,),
        retention_policy="public",
        training_policy="public",
        terms_verified=True,
        terms_reference="t",
    )
    primary_policy = ProviderRoutePolicy(
        route_tier=RouteTier.PRIVATE_WORKER,
        provider_only=("novita",),
        model_only=("inclusionai/ling-2.6-flash",),
        data_collection="deny",
        zdr=True,
        allow_fallbacks=False,
        endpoint_admission=_admission(),
        endpoint_inventory=ProviderEndpointInventory(
            inventory_artifact_hash=INVENTORY_HASH,
            admissions=(_admission(),),
        ),
    )
    fallback_policy = ProviderRoutePolicy(
        route_tier=RouteTier.PRIVATE_REVIEWER,
        provider_only=("other",),
        model_only=("other-model",),
        data_collection="deny",
        zdr=True,
        allow_fallbacks=False,
    )
    recorder = GatewayRecorder(SqliteLedgers(tmp_path / "gateway.sqlite3"))
    gateway = PolicyGateway(
        config=GatewayPolicyConfig(
            contributor_terms=public_terms,
            private_terms=terms,
            route_order=("worker", "reviewer"),
        ),
        profiles=(
            RouteProfile("worker", RouteTier.PRIVATE_WORKER, primary, primary_policy, terms, fallback_profile_ids=("reviewer",)),
            RouteProfile("reviewer", RouteTier.PRIVATE_REVIEWER, fallback, fallback_policy, terms),
        ),
        recorder=recorder,
    )

    response = gateway.complete(_request(_route(), data_class=GatewayDataClass.INTERNAL_SANITIZED))

    assert response.route_tier is RouteTier.BLOCKED
    assert response.actual_provider is None
    assert response.actual_model is None
    assert response.actual_gateway is None
    assert len(recorder.calls) == 2
    assert len({
        event.idempotency_key
        for event in recorder.ledgers.events(LedgerNamespace.MODEL)
    }) == 2
    assert all(record.actual_provider is None for record in recorder.calls)
    assert all(record.failure_metadata.get("status_code") == 429 for record in recorder.calls)
