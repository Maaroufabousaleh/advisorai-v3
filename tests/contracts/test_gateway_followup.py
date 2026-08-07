import json
from pathlib import Path

import pytest

from advisorai.gateway import (
    GatewayPolicyConfig,
    GatewayPolicyError,
    PolicyGateway,
    ProviderRoutePolicy,
    ProviderTerms,
    RouteProfile,
    TypedGatewayAdapter,
    validate_gateway_output,
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
    GatewayInvocationMode,
    GatewayMessage,
    GatewayOutputKind,
    GatewayRequest,
    GatewayResponse,
    GatewayRoute,
    GatewayTier,
    GatewayTool,
    GenerationBudget,
    RouteTier,
)


def _profile_gateway(
    *, public_transport=None, worker_transport=None, recorder=None, raw_identity=False
):
    public_route = GatewayRoute(
        provider="public-provider",
        model="public-model",
        gateway="openrouter",
        endpoint_variant="public-endpoint",
        retention_policy="public",
        training_policy="public-only",
        terms_verified=True,
        terms_reference="terms:public",
    )
    worker_route = GatewayRoute(
        provider="private-provider",
        model="private-model",
        gateway="openrouter",
        endpoint_variant="private-endpoint",
        retention_policy="zero",
        training_policy="no_training_zdr",
        terms_verified=True,
        terms_reference="terms:private",
    )
    contributor_terms = ProviderTerms.from_route(
        public_route,
        tier=GatewayTier.CONTRIBUTOR,
        allowed_data_classes=(GatewayDataClass.PUBLIC,),
    )
    private_terms = ProviderTerms.from_route(
        worker_route,
        tier=GatewayTier.PRIVATE,
        allowed_data_classes=(
            GatewayDataClass.PUBLIC,
            GatewayDataClass.INTERNAL_SANITIZED,
            GatewayDataClass.CONFIDENTIAL,
        ),
    )

    def payload(request, body):
        return {
            **body,
            "actual_provider": body.get("actual_provider", request.route.provider),
            "actual_model": body.get("actual_model", request.route.model),
            "actual_gateway": body.get("actual_gateway", request.route.gateway),
            "actual_endpoint_variant": body.get(
                "actual_endpoint_variant", request.route.endpoint_variant
            ),
            "billed_cost_usd": body.get("billed_cost_usd", 0),
        }

    public_transport = public_transport or (
        lambda _: {
            "content": "public",
            "typed_payload": {"ok": True},
            "billed_cost_usd": 0,
        }
    )
    worker_transport = worker_transport or (
        lambda _: {
            "content": "worker",
            "typed_payload": {"ok": True},
            "input_price_per_million": 0.01,
            "output_price_per_million": 0.03,
            "request_price_usd": 0,
            "billed_cost_usd": 0,
        }
    )
    public_policy = ProviderRoutePolicy(
        route_tier=RouteTier.CONTRIBUTOR_PUBLIC,
        provider_only=(public_route.provider,),
        model_only=(public_route.model,),
        data_collection="deny",
        allow_fallbacks=False,
        actual_identity_mode="exact",
    )
    worker_policy = ProviderRoutePolicy(
        route_tier=RouteTier.PRIVATE_WORKER,
        provider_only=(worker_route.provider,),
        model_only=(worker_route.model,),
        data_collection="deny",
        zdr=True,
        allow_fallbacks=False,
        max_prompt_price=0.1,
        max_completion_price=0.3,
    )
    public_adapter = TypedGatewayAdapter(
        "public",
        public_route,
        lambda request: public_transport(request)
        if raw_identity
        else payload(request, public_transport(request)),
    )
    worker_adapter = TypedGatewayAdapter(
        "worker",
        worker_route,
        lambda request: payload(request, worker_transport(request)),
    )
    profiles = (
        RouteProfile(
            "public",
            RouteTier.CONTRIBUTOR_PUBLIC,
            public_adapter,
            public_policy,
            contributor_terms,
            fallback_profile_ids=("worker",),
        ),
        RouteProfile("worker", RouteTier.PRIVATE_WORKER, worker_adapter, worker_policy, private_terms),
    )
    config = GatewayPolicyConfig(
        contributor_terms=contributor_terms,
        private_terms=private_terms,
        route_order=("public", "worker"),
    )
    return (
        PolicyGateway(config=config, profiles=profiles, recorder=recorder),
        public_route,
        worker_route,
    )


def _request(route: GatewayRoute, **updates) -> GatewayRequest:
    values = {
        "route": route,
        "messages": (GatewayMessage(role="user", content="public request"),),
        "prompt_version": "followup-v1",
        "data_class": GatewayDataClass.PUBLIC,
    }
    values.update(updates)
    return GatewayRequest(**values)


def test_governed_route_fails_closed_without_actual_identity_and_recorder_does_not_fallback():
    recorder = GatewayRecorder()
    gateway, public_route, _ = _profile_gateway(
        recorder=recorder,
        raw_identity=True,
        public_transport=lambda _: {"content": "missing identity", "typed_payload": {"ok": True}},
        worker_transport=lambda _: (_ for _ in ()).throw(RuntimeError("worker unavailable")),
    )

    response = gateway.complete(_request(public_route))

    assert response.route_tier is RouteTier.BLOCKED
    assert response.actual_provider is None
    assert response.actual_model is None
    assert response.actual_gateway is None
    assert recorder.calls[0].actual_provider is None
    assert recorder.calls[0].actual_model is None
    assert recorder.calls[0].actual_gateway is None


def test_openrouter_metadata_header_exact_provider_tag_and_billed_cost_are_preserved():
    body = json.dumps(
        {
            "id": "gen-1",
            "choices": [{"message": {"content": '{"ok":true}'}}],
            "openrouter_metadata": {
                "requested": "private-model",
                "strategy": "direct",
                "endpoints": {
                    "available": [
                        {"provider": "private-provider", "model": "private-model", "selected": True},
                        {"provider": "other-provider", "model": "private-model", "selected": False},
                    ]
                },
            },
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "cost": 0.0123,
                "cost_details": {"input": 0.004, "output": 0.0083, "cache_read": 0},
            },
        }
    ).encode()
    calls = []

    def requester(method, url, headers, request_body, timeout):
        calls.append((headers, json.loads(request_body)))
        return 200, body, ()

    client = SafeHttpClient(
        HttpClientConfig(allowed_hosts=("openrouter.ai",), requests_per_second=100),
        base_url="https://openrouter.ai/api/v1",
        requester=requester,
        sleeper=lambda _: None,
    )
    route = GatewayRoute(
        provider="private-provider",
        model="private-model",
        gateway="openrouter",
        endpoint_variant="private-provider",
        schema_mode="typed_json",
    )
    adapter = OpenAICompatibleGatewayAdapter(route, client, api_key="key")

    response = adapter.complete(
        _request(
            route,
            generation_budget=GenerationBudget(
                max_expected_cost_usd=0.1,
                max_billed_cost_usd=0.1,
            ),
            provider_options={
                "provider": {
                    "only": ["private-provider"],
                    "allow_fallbacks": False,
                }
            },
        )
    )

    assert calls[0][0]["X-OpenRouter-Metadata"] == "enabled"
    assert calls[0][1]["provider"] == {
        "only": ["private-provider"],
        "allow_fallbacks": False,
    }
    assert response.actual_provider == "private-provider"
    assert response.actual_model == "private-model"
    assert response.actual_endpoint_variant == "private-provider"
    assert response.billed_cost_usd == pytest.approx(0.0123)
    assert response.estimated_cost_usd == pytest.approx(0.0123)
    assert response.cost_metadata["cost_details"] == {
        "input": 0.004,
        "output": 0.0083,
        "cache_read": 0,
    }
    assert response.cost_metadata["usage"]["cost"] == 0.0123
    assert response.routing_metadata["strategy"] == "direct"

    missing_metadata_client = SafeHttpClient(
        HttpClientConfig(allowed_hosts=("openrouter.ai",), requests_per_second=100),
        base_url="https://openrouter.ai/api/v1",
        requester=lambda method, url, headers, request_body, timeout: (
            200,
            b'{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}',
            (),
        ),
        sleeper=lambda _: None,
    )
    with pytest.raises(GatewayTransportError, match="openrouter_metadata"):
        OpenAICompatibleGatewayAdapter(route, missing_metadata_client, api_key="key").complete(
            _request(route)
        )


def test_provider_policy_has_no_undocumented_endpoint_variants_field_and_uses_documented_selection():
    policy = ProviderRoutePolicy(
        route_tier=RouteTier.CONTRIBUTOR_PUBLIC,
        provider_only=("provider-tag",),
        provider_order=("provider-tag",),
        data_collection="deny",
        allow_fallbacks=False,
        actual_identity_mode="exact",
    )

    options = policy.request_options()

    assert options["provider"]["only"] == ["provider-tag"]
    assert options["provider"]["order"] == ["provider-tag"]
    assert "endpoint_variants" not in options["provider"]
    with pytest.raises(ValueError, match="endpoint_variants"):
        ProviderRoutePolicy.model_validate(
            {
                "route_tier": RouteTier.CONTRIBUTOR_PUBLIC,
                "data_collection": "deny",
                "allow_fallbacks": False,
                "actual_identity_mode": "exact",
                "endpoint_variants": ["not-documented"],
            }
        )

    route = GatewayRoute(
        provider="provider-tag",
        model="model",
        gateway="openrouter",
        endpoint_variant="provider-tag",
    )
    with pytest.raises(GatewayPolicyError, match="different from its pinned route"):
        policy.validate_response(
            # A different provider tag must not be normalized or accepted.
            GatewayResponse(
                request_id=GatewayRequest(
                    route=route,
                    messages=(GatewayMessage(role="user", content="x"),),
                    prompt_version="p",
                    data_class=GatewayDataClass.PUBLIC,
                ).request_id,
                route=route.model_copy(update={"provider": "Provider-Tag"}),
                actual_provider="Provider-Tag",
                actual_model="model",
                actual_gateway="openrouter",
                endpoint_variant="provider-tag",
                actual_endpoint_variant="provider-tag",
                content="ok",
                typed_payload={"ok": True},
                latency_ms=0,
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=0,
                billed_cost_usd=0,
                input_price_per_million=0,
                output_price_per_million=0,
                request_price_usd=0,
            ),
            pinned_route=route,
        )

    requested_only = GatewayResponse(
        request_id=GatewayRequest(
            route=route,
            messages=(GatewayMessage(role="user", content="x"),),
            prompt_version="p",
            data_class=GatewayDataClass.PUBLIC,
        ).request_id,
        route=route,
        actual_provider=route.provider,
        actual_model=route.model,
        actual_gateway=route.gateway,
        endpoint_variant=route.endpoint_variant,
        content="ok",
        typed_payload={"ok": True},
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        estimated_cost_usd=0,
        billed_cost_usd=0,
    )
    with pytest.raises(GatewayPolicyError, match="actual endpoint identity"):
        policy.validate_response(requested_only, pinned_route=route)

    mismatched_endpoint = requested_only.model_copy(
        update={
            "actual_endpoint_variant": "provider-tag/other",
            "endpoint_variant": "provider-tag/other",
            "route": route.model_copy(update={"endpoint_variant": "provider-tag/other"}),
        }
    )
    with pytest.raises(GatewayPolicyError, match="endpoint variant"):
        policy.validate_response(mismatched_endpoint, pinned_route=route)

    with pytest.raises(ValueError, match="endpoint routing"):
        GatewayRequest(
            route=route,
            messages=(GatewayMessage(role="user", content="x"),),
            prompt_version="p",
            data_class=GatewayDataClass.PUBLIC,
            provider_options={"provider": {"endpoint_variants": ["provider-tag"]}},
        )


def test_all_non_generic_output_kinds_use_concrete_pydantic_schemas():
    valid = {
        GatewayOutputKind.GENERIC: {},
        GatewayOutputKind.NEWS_EXTRACTION: {"headline": "Headline", "summary": "Summary"},
        GatewayOutputKind.CLAIM_LIST: {"claims": [{"text": "Claim", "confidence": 0.8}]},
        GatewayOutputKind.CODE_PATCH_PROPOSAL: {"summary": "Fix", "patch": "diff --git"},
        GatewayOutputKind.RESEARCH_QUESTION: {"question": "Why?", "rationale": "Evidence"},
        GatewayOutputKind.COUNTERARGUMENT: {
            "claim": "Claim",
            "counterargument": "Counter",
        },
    }
    for kind, payload in valid.items():
        assert isinstance(validate_gateway_output(kind, payload), dict)

    with pytest.raises(ValueError):
        validate_gateway_output(GatewayOutputKind.NEWS_EXTRACTION, {"summary": "missing headline"})
    with pytest.raises(ValueError):
        validate_gateway_output(
            GatewayOutputKind.CODE_PATCH_PROPOSAL,
            {"summary": "Fix", "patch": "diff", "order": "forbidden"},
        )

    gateway, public_route, _ = _profile_gateway(
        public_transport=lambda _: {
            "content": "invalid",
            "typed_payload": {"summary": "missing patch"},
        }
    )
    response = gateway.complete(
        _request(public_route, output_kind=GatewayOutputKind.CODE_PATCH_PROPOSAL)
    )
    assert response.route_tier is RouteTier.BLOCKED


def test_tool_input_schema_is_transmitted_and_returned_arguments_are_validated():
    schema = {
        "type": "object",
        "properties": {"instrument": {"type": "string"}},
        "required": ["instrument"],
        "additionalProperties": False,
    }
    tool = GatewayTool(
        name="read_evidence",
        input_schema_version="v1",
        output_schema_version="v1",
        input_schema=schema,
    )
    seen = []

    def worker(request):
        seen.append(request.tools[0].input_schema)
        return {
            "content": None,
            "tool_calls": ({"name": "read_evidence", "arguments": '{"instrument": 7}'},),
        }

    gateway, public_route, worker_route = _profile_gateway(worker_transport=worker)
    response = gateway.complete(
        _request(
            public_route,
            data_class=GatewayDataClass.CONFIDENTIAL,
            tools=(tool,),
            invocation_mode=GatewayInvocationMode.TOOL_REQUIRED,
        )
    )

    assert response.route_tier is RouteTier.BLOCKED
    assert seen == [schema]


def test_unclassified_requests_are_blocked_before_routing():
    gateway, public_route, _ = _profile_gateway()
    request = GatewayRequest(
        route=public_route,
        messages=(GatewayMessage(role="user", content="missing classification"),),
        prompt_version="followup-v1",
    )

    assert request.data_class is GatewayDataClass.UNCLASSIFIED
    with pytest.raises(GatewayPolicyError, match="explicit data classification"):
        gateway.complete(request, payload={"portfolio": "internal"})


def test_profile_attempts_have_unique_ledger_idempotency_keys(tmp_path: Path):
    ledgers = SqliteLedgers(tmp_path / "gateway.sqlite3")
    recorder = GatewayRecorder(ledgers)
    gateway, public_route, _ = _profile_gateway(
        recorder=recorder,
        public_transport=lambda _: (_ for _ in ()).throw(RuntimeError("public down")),
    )

    gateway.complete(_request(public_route))

    events = ledgers.events(LedgerNamespace.MODEL)
    keys = [event.idempotency_key for event in events]
    assert len(keys) == len(set(keys)) == 2
    assert any(":public:0:" in key for key in keys)
    assert any(":worker:1:" in key for key in keys)


def test_exact_pinned_contributor_can_disable_provider_fallbacks():
    policy = ProviderRoutePolicy(
        route_tier=RouteTier.CONTRIBUTOR_PUBLIC,
        provider_only=("exact-provider",),
        model_only=("exact-model",),
        data_collection="deny",
        allow_fallbacks=False,
        actual_identity_mode="exact",
    )

    assert policy.request_options()["provider"]["allow_fallbacks"] is False
