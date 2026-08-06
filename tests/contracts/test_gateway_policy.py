import pytest

from advisorai.gateway import (
    GatewayChain,
    GatewayPolicyConfig,
    GatewayPolicyError,
    PolicyGateway,
    ProviderRoutePolicy,
    ProviderTerms,
    RouteProfile,
    classify_payload,
)
from advisorai.gateway.adapters import TypedGatewayAdapter
from advisorai.gateway.core import GatewayRecorder
from advisorai.ports import (
    DecisionImpact,
    GatewayDataClass,
    GatewayMessage,
    GatewayOutputKind,
    GatewayRequest,
    GatewayRoute,
    GatewayTier,
    GatewayTool,
    RouteTier,
)


def _routes() -> tuple[GatewayRoute, GatewayRoute]:
    contributor = GatewayRoute(
        provider="contributor-provider",
        model="worker-v1",
        gateway="contributor-direct",
        retention_policy="30d",
        training_policy="opt_out_no_training",
        terms_verified=True,
        terms_reference="contract:contributor-v1",
    )
    private = GatewayRoute(
        provider="private-provider",
        model="private-v1",
        gateway="private-direct",
        retention_policy="zero",
        training_policy="no_training_zdr",
        terms_verified=True,
        terms_reference="contract:private-v1",
    )
    return contributor, private


def _gateway(*, contributor_transport=None, private_transport=None, recorder=None):
    contributor_route, private_route = _routes()
    contributor = TypedGatewayAdapter(
        "contributor",
        contributor_route,
        contributor_transport
        or (lambda _: {"content": "worker", "typed_payload": {"claims": []}}),
    )
    private = TypedGatewayAdapter(
        "private",
        private_route,
        private_transport
        or (lambda _: {"content": "private", "typed_payload": {"thesis": "review"}}),
    )
    config = GatewayPolicyConfig(
        contributor_terms=ProviderTerms.from_route(
            contributor_route,
            tier=GatewayTier.CONTRIBUTOR,
            allowed_data_classes=(GatewayDataClass.PUBLIC, GatewayDataClass.INTERNAL_SANITIZED),
        ),
        private_terms=ProviderTerms.from_route(
            private_route,
            tier=GatewayTier.PRIVATE,
            allowed_data_classes=(
                GatewayDataClass.PUBLIC,
                GatewayDataClass.INTERNAL_SANITIZED,
                GatewayDataClass.CONFIDENTIAL,
            ),
        ),
    )
    return (
        PolicyGateway(contributor=contributor, private=private, config=config, recorder=recorder),
        contributor_route,
        private_route,
    )


def _request(route: GatewayRoute, **updates) -> GatewayRequest:
    values = {
        "route": route,
        "messages": (GatewayMessage(role="user", content="summarize this public article"),),
        "prompt_version": "policy-test-v1",
    }
    values.update(updates)
    return GatewayRequest(**values)


def test_public_worker_call_uses_contributor_and_records_non_authoritative_metadata():
    recorder = GatewayRecorder()
    gateway, contributor_route, _ = _gateway(recorder=recorder)
    request = _request(
        contributor_route,
        output_kind=GatewayOutputKind.CLAIM_LIST,
        evidence_ids=("evidence-1", "evidence-2"),
    )

    response = gateway.complete(request)

    assert response.tier is GatewayTier.CONTRIBUTOR
    assert response.data_class is GatewayDataClass.PUBLIC
    assert response.output_kind is GatewayOutputKind.CLAIM_LIST
    assert response.authoritative is False
    assert response.policy_version == "gateway-policy-v1"
    assert response.retention_policy == contributor_route.retention_policy
    assert response.training_policy == contributor_route.training_policy
    assert response.terms_verified is True
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call.tier is GatewayTier.CONTRIBUTOR
    assert call.data_class is GatewayDataClass.PUBLIC
    assert call.prompt_hash == request.prompt_hash()
    assert call.evidence_hash == request.evidence_hash()
    assert call.retention_policy == contributor_route.retention_policy
    assert call.training_policy == contributor_route.training_policy
    assert call.terms_verified is True


def test_confidential_and_decision_critical_requests_escalate_to_private():
    gateway, contributor_route, private_route = _gateway()
    request = _request(
        contributor_route,
        data_class=GatewayDataClass.CONFIDENTIAL,
        portfolio_influence=True,
        confidence=0.95,
        task_kind="final_thesis",
    )

    response = gateway.complete(request)

    assert response.route.gateway == private_route.gateway
    assert response.tier is GatewayTier.PRIVATE
    assert "confidential" in (response.escalation_reason or "")
    assert "portfolio" in (response.escalation_reason or "")


def test_low_confidence_and_conflicting_evidence_escalate_without_answer_length_rule():
    seen: list[str] = []
    gateway, contributor_route, private_route = _gateway(
        private_transport=lambda request: (
            seen.append(request.messages[0].content)
            or {"content": "review", "typed_payload": {"review": True}}
        )
    )
    request = _request(
        contributor_route.model_copy(update={"fallback_chain": (private_route.gateway,)}),
        confidence=0.2,
        conflicting_evidence=True,
        messages=(GatewayMessage(role="user", content="short"),),
    )

    response = gateway.complete(request)

    assert response.tier is GatewayTier.PRIVATE
    assert "low confidence" in (response.escalation_reason or "")
    assert "conflicting evidence" in (response.escalation_reason or "")
    assert seen == ["short"]


def test_secret_execution_payload_is_blocked_before_any_provider_call():
    calls: list[str] = []
    gateway, contributor_route, _ = _gateway(
        contributor_transport=lambda _: calls.append("contributor")
        or {"content": "bad", "typed_payload": {"ok": True}}
    )
    request = _request(contributor_route)

    with pytest.raises(GatewayPolicyError, match="blocked"):
        gateway.complete(request, payload={"account_id": "acct-1", "orders": []})
    assert calls == []


def test_raw_credential_text_requires_explicit_redaction_and_redaction_is_visible_to_provider():
    seen: list[str] = []
    gateway, contributor_route, _ = _gateway(
        contributor_transport=lambda request: (
            seen.append(request.messages[0].content)
            or {"content": "redacted", "typed_payload": {"ok": True}}
        )
    )
    raw = _request(
        contributor_route,
        messages=(GatewayMessage(role="user", content="token=top-secret summarize"),),
    )
    with pytest.raises(GatewayPolicyError, match="redaction"):
        gateway.complete(raw)

    gateway.complete(raw, sensitive_values={"token": "top-secret"})
    assert seen == ["token=[REDACTED] summarize"]


def test_contributor_has_no_tools_and_private_accepts_only_allowlisted_read_tools():
    gateway, contributor_route, private_route = _gateway()
    contributor_request = _request(
        contributor_route,
        tools=(GatewayTool(name="read_evidence", input_schema_version="v1", output_schema_version="v1"),),
    )
    with pytest.raises(GatewayPolicyError, match="cannot receive tools"):
        gateway.complete(contributor_request)

    private_request = _request(
        contributor_route.model_copy(update={"fallback_chain": (private_route.gateway,)}),
        data_class=GatewayDataClass.CONFIDENTIAL,
        tools=(GatewayTool(name="read_evidence", input_schema_version="v1", output_schema_version="v1"),),
    )
    assert gateway.complete(private_request).tier is GatewayTier.PRIVATE

    bad_private_request = private_request.model_copy(
        update={
            "tools": (
                GatewayTool(
                    name="read_private_notebook",
                    input_schema_version="v1",
                    output_schema_version="v1",
                ),
            )
        }
    )
    with pytest.raises(GatewayPolicyError, match="approved read-only"):
        gateway.complete(bad_private_request)


def test_private_tool_calls_cannot_smuggle_account_or_execution_arguments():
    gateway, contributor_route, _ = _gateway(
        private_transport=lambda _: {
            "content": None,
            "tool_calls": (
                {"name": "read_evidence", "arguments": {"account_id": "acct-1"}},
            ),
        }
    )
    request = _request(
        contributor_route,
        data_class=GatewayDataClass.CONFIDENTIAL,
    )
    with pytest.raises(GatewayPolicyError, match="forbidden field"):
        gateway.complete(request)


def test_model_output_cannot_smuggle_authority_fields():
    gateway, contributor_route, _ = _gateway(
        contributor_transport=lambda _: {
            "content": "proposal",
            "typed_payload": {"order": {"symbol": "BTC"}},
        }
    )
    with pytest.raises(GatewayPolicyError, match="authority field"):
        gateway.complete(_request(contributor_route))


def test_contributor_text_only_output_is_not_admitted_as_authority():
    gateway, contributor_route, _ = _gateway(
        contributor_transport=lambda _: {"content": "untyped worker prose"}
    )
    with pytest.raises(GatewayPolicyError, match="typed structured"):
        gateway.complete(_request(contributor_route))


def test_policy_gateway_accepts_only_explicit_fallback_routes():
    contributor_route, _ = _routes()
    fallback_route = contributor_route.model_copy(
        update={"gateway": "contributor-fallback", "fallback_chain": ()}
    )

    class Failing:
        name = "primary-failing"

        def complete(self, request):
            raise RuntimeError("primary unavailable")

    chain = GatewayChain(
        (
            Failing(),
            TypedGatewayAdapter(
                "fallback",
                fallback_route,
                lambda _: {"content": "fallback", "typed_payload": {"ok": True}},
            ),
        )
    )
    gateway, _, _ = _gateway()
    gateway.contributor = chain
    request = _request(
        contributor_route.model_copy(update={"fallback_chain": (fallback_route.gateway,)})
    )

    response = gateway.complete(request)

    assert response.route.gateway == fallback_route.gateway
    assert response.tier is GatewayTier.CONTRIBUTOR


def test_classify_payload_is_conservative_for_internal_artifacts():
    assert classify_payload({"article_text": "public"}) is GatewayDataClass.PUBLIC
    assert classify_payload({"position_weight_bucket": "high"}) is GatewayDataClass.INTERNAL_SANITIZED
    assert classify_payload({"position_exposure": 0.2}) is GatewayDataClass.CONFIDENTIAL
    assert classify_payload({"api_key": "secret"}) is GatewayDataClass.SECRET_EXECUTION


def test_private_terms_must_prove_no_training_or_zdr():
    contributor_route, private_route = _routes()
    with pytest.raises(ValueError, match="no-training/ZDR"):
        GatewayPolicyConfig(
            contributor_terms=ProviderTerms.from_route(
                contributor_route,
                tier=GatewayTier.CONTRIBUTOR,
                allowed_data_classes=(GatewayDataClass.PUBLIC,),
            ),
            private_terms=ProviderTerms(
                name="private",
                tier=GatewayTier.PRIVATE,
                allowed_data_classes=(GatewayDataClass.CONFIDENTIAL,),
                retention_policy="zero",
                training_policy="standard_training",
                terms_verified=True,
                terms_reference="contract:private-v1",
            ),
        )


def test_provider_route_policy_rejects_public_collection_and_unreviewed_dynamic_routes():
    with pytest.raises(ValueError, match="data collection"):
        ProviderRoutePolicy(
            route_tier=RouteTier.CONTRIBUTOR_PUBLIC,
            data_collection="allow",
            allow_fallbacks=True,
        )
    with pytest.raises(ValueError, match="reproducible"):
        ProviderRoutePolicy(
            route_tier=RouteTier.CONTRIBUTOR_PUBLIC,
            data_collection="deny",
            allow_fallbacks=True,
            actual_identity_mode="dynamic",
            reproducible=True,
        )


def _profile_gateway(
    *, public_transport=None, worker_transport=None, reviewer_transport=None, recorder=None
):
    public_route = GatewayRoute(
        provider="openrouter",
        model="openrouter/free",
        gateway="openrouter",
        retention_policy="provider-terms",
        training_policy="public-only-contract",
        terms_verified=True,
        terms_reference="contract:public-v1",
    )
    worker_route = GatewayRoute(
        provider="novita",
        model="inclusionai/ling-2.6-flash",
        gateway="openrouter",
        endpoint_variant="novita",
        retention_policy="zero",
        training_policy="no_training_zdr",
        terms_verified=True,
        terms_reference="contract:novita-zdr-v1",
    )
    reviewer_route = GatewayRoute(
        provider="coreweave/fp4",
        model="openai/gpt-oss-120b",
        gateway="openrouter",
        endpoint_variant="coreweave/fp4",
        retention_policy="zero",
        training_policy="no_training_zdr",
        terms_verified=True,
        terms_reference="contract:reviewer-zdr-v1",
    )

    def terms(route: GatewayRoute, tier: GatewayTier, allowed):
        return ProviderTerms.from_route(route, tier=tier, allowed_data_classes=allowed)

    contributor_terms = terms(public_route, GatewayTier.CONTRIBUTOR, (GatewayDataClass.PUBLIC,))
    worker_terms = terms(
        worker_route,
        GatewayTier.PRIVATE,
        (
            GatewayDataClass.PUBLIC,
            GatewayDataClass.INTERNAL_SANITIZED,
            GatewayDataClass.CONFIDENTIAL,
        ),
    )
    reviewer_terms = terms(
        reviewer_route,
        GatewayTier.PRIVATE,
        (
            GatewayDataClass.PUBLIC,
            GatewayDataClass.INTERNAL_SANITIZED,
            GatewayDataClass.CONFIDENTIAL,
        ),
    )

    public_policy = ProviderRoutePolicy(
        route_tier=RouteTier.CONTRIBUTOR_PUBLIC,
        data_collection="deny",
        allow_fallbacks=True,
        require_parameters=True,
        max_prompt_price=0,
        max_completion_price=0,
        max_request_price=0,
        actual_identity_mode="dynamic",
        reproducible=False,
    )
    worker_policy = ProviderRoutePolicy(
        route_tier=RouteTier.PRIVATE_WORKER,
        provider_only=(worker_route.provider,),
        model_only=(worker_route.model,),
        endpoint_variants=(worker_route.endpoint_variant,),
        data_collection="deny",
        zdr=True,
        allow_fallbacks=False,
        require_parameters=True,
        max_prompt_price=0.1,
        max_completion_price=0.3,
        max_request_price=0,
    )
    reviewer_policy = ProviderRoutePolicy(
        route_tier=RouteTier.PRIVATE_REVIEWER,
        provider_only=(reviewer_route.provider,),
        model_only=(reviewer_route.model,),
        endpoint_variants=(reviewer_route.endpoint_variant,),
        data_collection="deny",
        zdr=True,
        allow_fallbacks=False,
        require_parameters=True,
        max_prompt_price=1,
        max_completion_price=3,
        max_request_price=0,
    )

    def response(content: str, payload: dict[str, object], *, prices=(0.0, 0.0, 0.0)):
        return {
            "content": content,
            "typed_payload": payload,
            "input_price_per_million": prices[0],
            "output_price_per_million": prices[1],
            "request_price_usd": prices[2],
        }

    public_adapter = TypedGatewayAdapter(
        "public",
        public_route,
        public_transport
        or (
            lambda _: response(
                "public",
                {"claims": []},
            )
            | {"actual_provider": "openrouter", "actual_model": "free-worker"}
        ),
    )
    worker_adapter = TypedGatewayAdapter(
        "worker",
        worker_route,
        worker_transport or (lambda _: response("worker", {"claims": []}, prices=(0.01, 0.03, 0.0))),
    )
    reviewer_adapter = TypedGatewayAdapter(
        "reviewer",
        reviewer_route,
        reviewer_transport or (lambda _: response("reviewer", {"review": True}, prices=(0.1, 0.3, 0.0))),
    )
    config = GatewayPolicyConfig(
        contributor_terms=contributor_terms,
        private_terms=reviewer_terms,
        route_order=("public", "worker", "reviewer"),
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
        RouteProfile(
            "worker",
            RouteTier.PRIVATE_WORKER,
            worker_adapter,
            worker_policy,
            worker_terms,
            fallback_profile_ids=("reviewer",),
        ),
        RouteProfile(
            "reviewer",
            RouteTier.PRIVATE_REVIEWER,
            reviewer_adapter,
            reviewer_policy,
            reviewer_terms,
        ),
    )
    return (
        PolicyGateway(config=config, profiles=profiles, recorder=recorder),
        public_route,
        worker_route,
        reviewer_route,
    )


def test_profile_router_uses_public_route_and_records_actual_dynamic_identity():
    recorder = GatewayRecorder()
    gateway, public_route, _, _ = _profile_gateway(recorder=recorder)
    response = gateway.complete(_request(public_route))

    assert response.route_tier is RouteTier.CONTRIBUTOR_PUBLIC
    assert response.route.model == "free-worker"
    assert response.actual_model == "free-worker"
    assert response.requested_route == public_route
    assert response.route_policy_hash
    assert response.redaction_policy_hash
    assert recorder.calls[0].requested_model == public_route.model
    assert recorder.calls[0].actual_model == "free-worker"
    assert recorder.calls[0].route_tier is RouteTier.CONTRIBUTOR_PUBLIC
    assert recorder.calls[0].prompt_hash == response.prompt_hash
    assert recorder.calls[0].route_policy_hash == response.route_policy_hash


def test_profile_router_injects_provider_policy_without_allowing_model_override():
    seen: list[dict[str, object]] = []
    gateway, public_route, _, _ = _profile_gateway(
        public_transport=lambda request: (
            seen.append(dict(request.provider_options))
            or {
                "content": "public",
                "typed_payload": {"ok": True},
                "input_price_per_million": 0,
                "output_price_per_million": 0,
                "request_price_usd": 0,
            }
        )
    )
    gateway.complete(
        _request(public_route, provider_options={"provider": {"data_collection": "allow"}})
    )
    assert seen[0]["provider"] == {
        "data_collection": "deny",
        "allow_fallbacks": True,
        "require_parameters": True,
        "max_price": {"prompt": 0.0, "completion": 0.0, "request": 0.0},
    }


def test_internal_sanitized_routes_to_private_worker_without_model_selection():
    gateway, public_route, worker_route, _ = _profile_gateway()
    response = gateway.complete(
        _request(public_route, data_class=GatewayDataClass.INTERNAL_SANITIZED)
    )

    assert response.route_tier is RouteTier.PRIVATE_WORKER
    assert response.route == worker_route


def test_portfolio_influencing_confidential_routes_to_reviewer():
    gateway, public_route, _, reviewer_route = _profile_gateway()
    response = gateway.complete(
        _request(
            public_route,
            data_class=GatewayDataClass.CONFIDENTIAL,
            decision_impact=DecisionImpact.PORTFOLIO_INFLUENCING,
        )
    )

    assert response.route_tier is RouteTier.PRIVATE_REVIEWER
    assert response.route == reviewer_route


def test_public_failure_can_use_only_explicit_private_worker_fallback():
    calls: list[str] = []
    gateway, public_route, worker_route, reviewer_route = _profile_gateway(
        public_transport=lambda _: calls.append("public") or (_ for _ in ()).throw(RuntimeError("down")),
        worker_transport=lambda _: calls.append("worker")
        or {
            "content": "worker",
            "typed_payload": {"ok": True},
            "input_price_per_million": 0.01,
            "output_price_per_million": 0.03,
            "request_price_usd": 0,
        },
    )
    response = gateway.complete(_request(public_route))

    assert response.route_tier is RouteTier.PRIVATE_WORKER
    assert response.route == worker_route
    assert calls == ["public", "worker"]
    assert response.route != reviewer_route


def test_worker_failure_can_escalate_to_reviewer_but_never_to_public():
    calls: list[str] = []
    gateway, public_route, _, reviewer_route = _profile_gateway(
        worker_transport=lambda _: calls.append("worker")
        or (_ for _ in ()).throw(RuntimeError("worker down")),
        reviewer_transport=lambda _: calls.append("reviewer")
        or {
            "content": "reviewer",
            "typed_payload": {"review": True},
            "input_price_per_million": 0.1,
            "output_price_per_million": 0.3,
            "request_price_usd": 0,
        },
    )
    response = gateway.complete(
        _request(public_route, data_class=GatewayDataClass.INTERNAL_SANITIZED)
    )

    assert response.route_tier is RouteTier.PRIVATE_REVIEWER
    assert response.route == reviewer_route
    assert calls == ["worker", "reviewer"]


def test_reviewer_exhaustion_returns_deterministic_abstention_not_public_downgrade():
    calls: list[str] = []
    gateway, public_route, _, _ = _profile_gateway(
        reviewer_transport=lambda _: calls.append("reviewer")
        or (_ for _ in ()).throw(RuntimeError("reviewer down")),
    )
    response = gateway.complete(
        _request(
            public_route,
            data_class=GatewayDataClass.CONFIDENTIAL,
            decision_impact=DecisionImpact.PORTFOLIO_INFLUENCING,
        )
    )

    assert response.route_tier is RouteTier.BLOCKED
    assert response.typed_payload["decision"] == "abstain"
    assert calls == ["reviewer"]


def test_unknown_private_provider_identity_abstains_instead_of_downgrading():
    gateway, public_route, _, _ = _profile_gateway(
        reviewer_transport=lambda _: {
            "content": "unreviewed",
            "typed_payload": {"review": True},
            "actual_model": "unreviewed-model",
            "input_price_per_million": 0.1,
            "output_price_per_million": 0.3,
            "request_price_usd": 0,
        }
    )
    response = gateway.complete(
        _request(
            public_route,
            data_class=GatewayDataClass.CONFIDENTIAL,
            decision_impact=DecisionImpact.PORTFOLIO_INFLUENCING,
        )
    )
    assert response.route_tier is RouteTier.BLOCKED
    assert response.typed_payload["decision"] == "abstain"


def test_profile_provider_price_or_parameter_policy_failure_abstains():
    gateway, public_route, _, _ = _profile_gateway(
        public_transport=lambda _: {
            "content": "too expensive",
            "typed_payload": {"ok": True},
            "input_price_per_million": 0.01,
            "output_price_per_million": 0,
            "request_price_usd": 0,
        }
    )
    response = gateway.complete(_request(public_route))
    assert response.route_tier is RouteTier.PRIVATE_WORKER
    assert response.typed_payload == {"claims": []}
