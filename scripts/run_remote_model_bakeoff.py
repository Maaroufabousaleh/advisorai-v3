#!/usr/bin/env python3
"""Run a bounded OpenRouter bake-off through the admitted PolicyGateway.

The script only reads the operator secret inventory through the scoped
``DIRECT_LLM`` resolver.  It never sources the file, prints a credential, or
passes the master inventory to a provider/model worker.  Evidence is sanitized
and written to an ignored, run-scoped directory.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from urllib.parse import quote
from uuid import uuid4

from advisorai.config import CredentialResolver, CredentialScope, SecretSettings
from advisorai.gateway import (
    GatewayPolicyConfig,
    PolicyGateway,
    ProviderEndpointAdmission,
    ProviderEndpointInventory,
    ProviderRoutePolicy,
    ProviderTerms,
    RouteProfile,
)
from advisorai.integrations.http import HttpClientConfig, SafeHttpClient
from advisorai.integrations.llm import OpenAICompatibleGatewayAdapter
from advisorai.phase0.remote_bakeoff import (
    RemoteBakeoffReport,
    RemoteBudget,
    RemoteInvocation,
    RemoteProbeResult,
    RemoteProbeStatus,
    RemoteRouteCandidate,
    RemoteRouteRole,
    canonical_hash,
    sanitize_metadata,
    write_remote_report,
)
from advisorai.ports import (
    DecisionImpact,
    GatewayDataClass,
    GatewayMessage,
    GatewayOutputKind,
    GatewayRequest,
    GatewayRoute,
    GatewayTier,
    GatewayTool,
    GenerationBudget,
    RouteTier,
)

OPENROUTER_HOST = "openrouter.ai"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
PHASE0_BUDGET = GenerationBudget(
    max_output_tokens=256,
    max_expected_cost_usd=0.001,
    max_billed_cost_usd=0.001,
    timeout_seconds=30,
    maximum_attempts=2,
)


def _get_json(client: SafeHttpClient, path: str, *, key: str) -> object:
    response = client.request(
        "GET",
        f"{OPENROUTER_BASE}{path}",
        headers={"Authorization": f"Bearer {key}"},
        acceptable_statuses=frozenset({200}),
        max_retries=0,
        timeout_seconds=30,
    )
    try:
        value = json.loads(response.body)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("OpenRouter returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("OpenRouter returned a non-object JSON document")
    return value


def _price_per_million(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("OpenRouter pricing was not numeric") from exc
    if number < 0:
        raise ValueError("OpenRouter pricing cannot be negative")
    # /models prices are USD/token; the AdvisorAI admission contract stores
    # USD per million tokens.
    return number * 1_000_000


def _endpoint_snapshot(endpoint: dict[str, object]) -> dict[str, object]:
    """Keep only reviewed inventory fields; omit descriptions and identifiers."""

    pricing = endpoint.get("pricing")
    pricing = pricing if isinstance(pricing, dict) else {}
    return {
        "name": endpoint.get("name"),
        "provider_name": endpoint.get("provider_name"),
        "tag": endpoint.get("tag"),
        "model_name": endpoint.get("model_name"),
        "quantization": endpoint.get("quantization"),
        "pricing": {
            "prompt": pricing.get("prompt"),
            "completion": pricing.get("completion"),
            "request": pricing.get("request", "0"),
        },
    }


def _model_snapshot(model: dict[str, object]) -> dict[str, object]:
    pricing = model.get("pricing")
    architecture = model.get("architecture")
    top_provider = model.get("top_provider")
    return {
        "id": model.get("id"),
        "canonical_slug": model.get("canonical_slug"),
        "created": model.get("created"),
        "context_length": model.get("context_length"),
        "architecture": architecture if isinstance(architecture, dict) else {},
        "pricing": pricing if isinstance(pricing, dict) else {},
        "supported_parameters": sorted(
            item for item in model.get("supported_parameters", ()) if isinstance(item, str)
        ),
        "top_provider": top_provider if isinstance(top_provider, dict) else {},
    }


def _safe_error_metadata(value: object) -> dict[str, object]:
    safe = sanitize_metadata(value)
    return safe if isinstance(safe, dict) else {}


def _route_terms(role: RemoteRouteRole) -> ProviderTerms:
    if role is RemoteRouteRole.CONTRIBUTOR_PUBLIC:
        return ProviderTerms(
            name="openrouter-public-contributor",
            tier=GatewayTier.CONTRIBUTOR,
            allowed_data_classes=(GatewayDataClass.PUBLIC,),
            retention_policy="public_only",
            training_policy="deny_required",
            terms_verified=True,
            terms_reference="inventory://openrouter/public-route",
        )
    return ProviderTerms(
        name=f"openrouter-{role.value}",
        tier=GatewayTier.PRIVATE,
        allowed_data_classes=(
            GatewayDataClass.PUBLIC,
            GatewayDataClass.INTERNAL_SANITIZED,
            GatewayDataClass.CONFIDENTIAL,
        ),
        retention_policy="zero",
        training_policy="no_training_zdr",
        terms_verified=True,
        terms_reference="inventory://openrouter/provider-policy",
    )


def _make_profile(
    candidate: RemoteRouteCandidate,
    admission: ProviderEndpointAdmission,
    adapter: OpenAICompatibleGatewayAdapter,
) -> tuple[RouteProfile, GatewayPolicyConfig]:
    if candidate.role is RemoteRouteRole.CONTRIBUTOR_PUBLIC:
        raise ValueError("dynamic contributor routes do not have an exact profile")
    route_tier = (
        RouteTier.PRIVATE_REVIEWER
        if candidate.role is RemoteRouteRole.PRIVATE_REVIEWER
        else RouteTier.PRIVATE_WORKER
    )
    policy = ProviderRoutePolicy(
        route_tier=route_tier,
        provider_only=(candidate.provider_selector or "",),
        model_only=(candidate.requested_model,),
        data_collection="deny",
        zdr=True,
        allow_fallbacks=False,
        actual_identity_mode="exact",
        endpoint_admission=admission,
        endpoint_inventory=ProviderEndpointInventory(
            inventory_artifact_hash=candidate.inventory_artifact_hash,
            admissions=(admission,),
        ),
    )
    terms = _route_terms(candidate.role)
    contributor_terms = _route_terms(RemoteRouteRole.CONTRIBUTOR_PUBLIC)
    config = GatewayPolicyConfig(
        contributor_terms=contributor_terms,
        private_terms=terms,
        route_order=(candidate.candidate,),
    )
    return RouteProfile(candidate.candidate, route_tier, adapter, policy, terms), config


def _request(candidate: RemoteRouteCandidate, *, invocation: RemoteInvocation) -> GatewayRequest:
    route = GatewayRoute(
        provider=candidate.provider_selector or candidate.requested_model,
        model=candidate.requested_model,
        gateway=candidate.gateway,
        endpoint_variant=candidate.requested_endpoint_selector,
        retention_policy="zero",
        training_policy="no_training_zdr",
        terms_verified=True,
        terms_reference=candidate.terms_reference,
    )
    tools: tuple[GatewayTool, ...] = ()
    if invocation in {RemoteInvocation.TOOL_OPTIONAL, RemoteInvocation.TOOL_REQUIRED}:
        tools = (
            GatewayTool(
                name="read_evidence",
                input_schema_version="evidence-v1",
                output_schema_version="evidence-result-v1",
                input_schema={
                    "type": "object",
                    "properties": {"evidence_id": {"type": "string"}},
                    "required": ["evidence_id"],
                    "additionalProperties": False,
                },
            ),
        )
    return GatewayRequest(
        route=route,
        messages=(
            GatewayMessage(
                role="user",
                content=(
                    "This is a synthetic gateway qualification probe. "
                    "Return only a short public research artifact. "
                    "Do not request or infer accounts, orders, positions, or credentials."
                ),
            ),
        ),
        tools=tools,
        invocation_mode=invocation.value,
        prompt_version="remote-bakeoff-v1",
        tool_version="read-evidence-v1" if tools else None,
        data_class=(
            GatewayDataClass.CONFIDENTIAL
            if candidate.role is RemoteRouteRole.PRIVATE_REVIEWER
            else GatewayDataClass.INTERNAL_SANITIZED
        ),
        decision_impact=(
            DecisionImpact.PORTFOLIO_INFLUENCING
            if candidate.role is RemoteRouteRole.PRIVATE_REVIEWER
            else DecisionImpact.RESEARCH
        ),
        output_kind=GatewayOutputKind.CLAIM_LIST,
        generation_budget=PHASE0_BUDGET,
    )


def _probe(
    candidate: RemoteRouteCandidate,
    admission: ProviderEndpointAdmission,
    adapter: OpenAICompatibleGatewayAdapter,
    config: GatewayPolicyConfig,
    *,
    invocation: RemoteInvocation,
) -> RemoteProbeResult:
    if invocation is RemoteInvocation.TOOL_REQUIRED and not candidate.supports_tool_choice_required:
        return RemoteProbeResult(
            candidate=candidate.candidate,
            role=candidate.role,
            status=RemoteProbeStatus.BLOCKED,
            invocation=invocation,
            failure_class="unsupported_tool_choice",
            notes=("endpoint inventory did not admit tool_choice=required",),
        )
    if invocation is not RemoteInvocation.STRUCTURED_OUTPUT and not candidate.supports_tools:
        return RemoteProbeResult(
            candidate=candidate.candidate,
            role=candidate.role,
            status=RemoteProbeStatus.BLOCKED,
            invocation=invocation,
            failure_class="unsupported_tools",
        )
    request = _request(candidate, invocation=invocation)
    started = perf_counter()
    try:
        gateway = PolicyGateway(
            config=config,
            profiles=(_make_profile(candidate, admission, adapter)[0],),
        )
        response = gateway.complete(request)
        elapsed = int((perf_counter() - started) * 1000)
        if response.route_tier is RouteTier.BLOCKED or response.typed_payload == {
            "decision": "abstain",
            "reason": response.typed_payload.get("reason")
            if isinstance(response.typed_payload, dict)
            else None,
        }:
            return RemoteProbeResult(
                candidate=candidate.candidate,
                role=candidate.role,
                status=RemoteProbeStatus.FAILED,
                invocation=invocation,
                latency_ms=elapsed,
                failure_class="gateway_abstention",
                failure_metadata=_safe_error_metadata(response.failure_metadata),
                notes=("the admitted route did not produce a successful response",),
            )
        return RemoteProbeResult(
            candidate=candidate.candidate,
            role=candidate.role,
            status=RemoteProbeStatus.MEASURED,
            invocation=invocation,
            latency_ms=elapsed,
            billed_cost_usd=response.billed_cost_usd,
            expected_cost_usd=response.expected_cost_usd,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            requested_provider_selector=response.requested_provider_selector,
            requested_model=response.requested_model,
            requested_gateway=response.requested_gateway,
            requested_endpoint_selector=response.requested_endpoint_selector,
            observed_provider_name=response.observed_provider_name,
            top_level_response_model=response.top_level_response_model,
            resolved_endpoint_model=response.resolved_endpoint_model or response.resolved_model,
            actual_gateway=response.actual_gateway,
            endpoint_selector_proof=response.endpoint_selector_proof,
            endpoint_selected=response.endpoint_selected,
            routing_strategy=response.routing_strategy,
            routing_attempt=response.routing_attempt,
            is_byok=response.is_byok,
            tool_called=response.tool_called,
            tool_execution_status="not_executed",
        )
    except Exception as exc:
        return RemoteProbeResult(
            candidate=candidate.candidate,
            role=candidate.role,
            status=RemoteProbeStatus.FAILED,
            invocation=invocation,
            latency_ms=int((perf_counter() - started) * 1000),
            failure_class=type(exc).__name__,
            failure_metadata=_safe_error_metadata(
                getattr(exc, "failure_metadata", {"error_type": type(exc).__name__})
            ),
        )


def _fetch_candidates(
    client: SafeHttpClient, key: str, *, inventory_timestamp: datetime
) -> tuple[list[RemoteRouteCandidate], dict[str, object]]:
    model_ids = (
        "inclusionai/ling-2.6-flash",
        "openai/gpt-oss-20b",
        "deepseek/deepseek-v4-flash",
    )
    models_payload = _get_json(client, "/models", key=key)
    models = models_payload.get("data", []) if isinstance(models_payload, dict) else []
    by_id = {
        item.get("id"): item
        for item in models
        if isinstance(item, dict) and item.get("id") in model_ids
    }
    endpoints: dict[str, list[dict[str, object]]] = {}
    for model_id in model_ids:
        # The OpenRouter endpoint resource keeps the model namespace slash in
        # the path (``/models/org/model/endpoints``); quote only unsafe
        # characters while preserving that routing separator.
        payload = _get_json(client, f"/models/{quote(model_id, safe='/')}/endpoints", key=key)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        raw = data.get("endpoints", []) if isinstance(data, dict) else []
        endpoints[model_id] = [item for item in raw if isinstance(item, dict)]
    inventory_payload = {
        "schema_version": "openrouter-inventory-v1",
        "gateway": "openrouter",
        "retrieved_at": inventory_timestamp.isoformat(),
        "models": [_model_snapshot(by_id[model_id]) for model_id in model_ids if model_id in by_id],
        "endpoints": {
            model_id: [_endpoint_snapshot(item) for item in values]
            for model_id, values in endpoints.items()
        },
        "dynamic_public_route": {
            "model": "openrouter/free",
            "reproducible": False,
            "reason": "free router selects a changing provider/model pool",
        },
    }
    inventory_hash = canonical_hash(inventory_payload)
    specs = (
        (
            "private-ling-novita",
            RemoteRouteRole.PRIVATE_WORKER,
            "inclusionai/ling-2.6-flash",
            "novita",
            "Novita",
        ),
        (
            "private-gpt-oss-coreweave",
            RemoteRouteRole.PRIVATE_REVIEWER,
            "openai/gpt-oss-20b",
            "coreweave/fp4",
            "CoreWeave",
        ),
        (
            "private-deepseek-digitalocean",
            RemoteRouteRole.PRIVATE_WORKER,
            "deepseek/deepseek-v4-flash",
            "digitalocean",
            "DigitalOcean",
        ),
    )
    candidates: list[RemoteRouteCandidate] = []
    for name, role, model_id, selector, display_name in specs:
        model = by_id.get(model_id)
        selected_endpoint = next(
            (item for item in endpoints[model_id] if item.get("tag") == selector),
            None,
        )
        if model is None or selected_endpoint is None:
            candidates.append(
                RemoteRouteCandidate(
                    candidate=name,
                    role=role,
                    gateway="openrouter",
                    provider_selector=selector,
                    requested_model=model_id,
                    requested_endpoint_selector=selector,
                    zdr=True,
                    data_collection="deny",
                    allow_fallbacks=False,
                    inventory_reference="artifacts/phase0/remote-model-bakeoff/inventory.json",
                    inventory_artifact_hash=inventory_hash,
                    terms_reference="inventory://openrouter/2026-08-07",
                    reproducible=False,
                    notes=("live inventory did not expose the requested exact endpoint",),
                )
            )
            continue
        pricing = selected_endpoint.get("pricing")
        pricing = pricing if isinstance(pricing, dict) else {}
        supported = set(model.get("supported_parameters", ()))
        resolved = selected_endpoint.get("name", "").split(" | ", 1)[-1]
        resolved_model = resolved if "/" in resolved else model_id
        candidates.append(
            RemoteRouteCandidate(
                candidate=name,
                role=role,
                gateway="openrouter",
                provider_selector=selector,
                requested_model=model_id,
                requested_endpoint_selector=selector,
                observed_provider_names=(display_name,),
                allowed_top_level_models=(model_id,),
                allowed_resolved_models=(resolved_model,),
                input_price_per_million=_price_per_million(pricing.get("prompt")),
                output_price_per_million=_price_per_million(pricing.get("completion")),
                request_price=0,
                zdr=True,
                data_collection="deny",
                allow_fallbacks=False,
                supports_tools="tools" in supported,
                supports_tool_choice_required="tool_choice" in supported,
                supports_structured_output=bool(
                    {"response_format", "structured_outputs"} & supported
                ),
                allow_response_format_with_tools=False,
                inventory_reference="artifacts/phase0/remote-model-bakeoff/inventory.json",
                inventory_artifact_hash=inventory_hash,
                terms_reference="inventory://openrouter/2026-08-07",
                notes=("live /models and /models/{id}/endpoints inventory",),
            )
        )
    return candidates, inventory_payload


def _write_roster(
    path: Path,
    report: RemoteBakeoffReport,
    report_hash: str,
    *,
    report_reference: str,
) -> None:
    roster: dict[str, object] = {
        "schema_version": "phase0-remote-roster-v1",
        "report_reference": report_reference,
        "report_hash": report_hash,
        "roles": {
            "contributor_public": {
                "state": "blocked",
                "candidate": "openrouter/free",
                "reason": "dynamic provider/model pool has no reproducible endpoint admission",
            },
            "private_worker": [],
            "private_reviewer": [],
            "blocked_execution": {"state": "blocked", "authority": "deterministic_only"},
        },
    }
    for candidate in report.candidates:
        entries = roster["roles"].get(candidate.role.value)
        if isinstance(entries, list):
            candidate_probes = [
                item for item in report.probes if item.candidate == candidate.candidate
            ]
            entries.append(
                {
                    "candidate": candidate.candidate,
                    "provider_selector": candidate.provider_selector,
                    "requested_model": candidate.requested_model,
                    "zdr": candidate.zdr,
                    "data_collection": candidate.data_collection,
                    "allow_fallbacks": candidate.allow_fallbacks,
                    "revision": candidate.allowed_resolved_models,
                    "inventory_hash": candidate.inventory_artifact_hash,
                    "probe_status": [item.status.value for item in candidate_probes],
                    "state": (
                        "pending_quality_and_stability"
                        if any(
                            item.status is RemoteProbeStatus.MEASURED for item in candidate_probes
                        )
                        else "quarantined_or_failed"
                    ),
                    "observed_successes": [
                        {
                            "invocation": item.invocation.value if item.invocation else None,
                            "observed_provider_name": item.observed_provider_name,
                            "resolved_endpoint_model": item.resolved_endpoint_model,
                            "endpoint_selector_proof": item.endpoint_selector_proof,
                            "billed_cost_usd": item.billed_cost_usd,
                            "tool_called": item.tool_called,
                            "tool_execution_status": item.tool_execution_status,
                        }
                        for item in candidate_probes
                        if item.status is RemoteProbeStatus.MEASURED
                    ],
                }
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(roster, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--secrets",
        type=Path,
        default=Path(os.getenv("ADVISORAI_SECRETS_FILE", "secrets.env")),
    )
    parser.add_argument(
        "--output-root", type=Path, default=Path("artifacts/phase0/remote-model-bakeoff")
    )
    parser.add_argument(
        "--roster", type=Path, default=Path("configs/models/phase0_remote_roster.json")
    )
    args = parser.parse_args()
    resolver = CredentialResolver.from_env_file(args.secrets)
    key = resolver.get(CredentialScope.DIRECT_LLM, "ADVISORAI_LLM_API_KEY")
    if not key:
        raise SystemExit("ADVISORAI_LLM_API_KEY is not populated in the direct_llm scope")
    settings = SecretSettings.from_env_file(args.secrets)
    if settings.llm_provider.lower() != "openrouter" or not settings.llm_base_url:
        raise SystemExit("remote bake-off requires the configured OpenRouter direct route")
    client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=(OPENROUTER_HOST,), user_agent="advisorai-v3/remote-bakeoff"
        ),
        base_url=OPENROUTER_BASE,
        secret_values={"ADVISORAI_LLM_API_KEY": key},
    )
    key_payload = _get_json(client, "/key", key=key)
    key_data = key_payload.get("data", key_payload) if isinstance(key_payload, dict) else {}
    remaining = float(key_data.get("limit_remaining", 0)) if isinstance(key_data, dict) else 0.0
    budget = RemoteBudget(remaining_allowed_usd=max(0.0, remaining))
    measured_at = datetime.now(UTC)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid4().hex[:8]
    candidates, inventory_payload = _fetch_candidates(client, key, inventory_timestamp=measured_at)
    output_dir = args.output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    inventory_path = output_dir / "inventory.json"
    inventory_bytes = json.dumps(inventory_payload, indent=2, sort_keys=True) + "\n"
    inventory_path.write_text(inventory_bytes, encoding="utf-8")
    # The admission hash is the SHA-256 of the immutable artifact on disk,
    # not merely a hash of an in-memory object with different serialization.
    inventory_hash = sha256(inventory_bytes.encode("utf-8")).hexdigest()
    candidates = [
        candidate.model_copy(
            update={
                "inventory_artifact_hash": inventory_hash,
                "inventory_reference": str(inventory_path),
            }
        )
        for candidate in candidates
    ]
    probes: list[RemoteProbeResult] = []
    for candidate in candidates:
        if not candidate.reproducible:
            probes.append(
                RemoteProbeResult(
                    candidate=candidate.candidate,
                    role=candidate.role,
                    status=RemoteProbeStatus.QUARANTINED,
                    failure_class="incomplete_endpoint_inventory",
                    notes=candidate.notes,
                )
            )
            continue
        if budget.remaining_cap_usd <= 0:
            probes.append(
                RemoteProbeResult(
                    candidate=candidate.candidate,
                    role=candidate.role,
                    status=RemoteProbeStatus.BLOCKED,
                    failure_class="budget_exhausted",
                )
            )
            continue
        admission = ProviderEndpointAdmission(
            provider_selector_slug=candidate.provider_selector or "",
            allowed_provider_display_names=candidate.observed_provider_names,
            requested_model=candidate.requested_model,
            allowed_top_level_models=candidate.allowed_top_level_models,
            allowed_resolved_models=candidate.allowed_resolved_models,
            gateway=candidate.gateway,
            zdr=True,
            data_collection="deny",
            input_price_per_million=candidate.input_price_per_million or 0,
            output_price_per_million=candidate.output_price_per_million or 0,
            request_price=candidate.request_price or 0,
            inventory_artifact_hash=inventory_hash,
            inventory_timestamp=measured_at,
            terms_reference=candidate.terms_reference,
            admission_version="remote-bakeoff-v1",
            supports_tools=candidate.supports_tools,
            supports_tool_choice_required=candidate.supports_tool_choice_required,
            supports_structured_output=candidate.supports_structured_output,
            allow_response_format_with_tools=candidate.allow_response_format_with_tools,
        )
        route = GatewayRoute(
            provider=candidate.provider_selector or candidate.requested_model,
            model=candidate.requested_model,
            gateway=candidate.gateway,
            endpoint_variant=candidate.requested_endpoint_selector,
            retention_policy="zero",
            training_policy="no_training_zdr",
            terms_verified=True,
            terms_reference=candidate.terms_reference,
        )
        adapter = OpenAICompatibleGatewayAdapter(
            route,
            client,
            api_key=key,
            input_price_per_million=candidate.input_price_per_million,
            output_price_per_million=candidate.output_price_per_million,
            request_price_usd=candidate.request_price,
        )
        _, config = _make_profile(candidate, admission, adapter)
        # Reserve the hard per-call Phase-0 maximum before each dispatch. This
        # is intentionally conservative; actual usage is recorded separately.
        invocations = (
            (
                RemoteInvocation.STRUCTURED_OUTPUT,
                RemoteInvocation.TOOL_OPTIONAL,
                RemoteInvocation.TOOL_REQUIRED,
            )
            if candidate.supports_tool_choice_required
            else (RemoteInvocation.STRUCTURED_OUTPUT, RemoteInvocation.TOOL_OPTIONAL)
        )
        for invocation in invocations:
            if budget.remaining_cap_usd < PHASE0_BUDGET.max_billed_cost_usd:
                probes.append(
                    RemoteProbeResult(
                        candidate=candidate.candidate,
                        role=candidate.role,
                        status=RemoteProbeStatus.BLOCKED,
                        invocation=invocation,
                        failure_class="budget_exhausted",
                    )
                )
                continue
            budget = budget.authorize(PHASE0_BUDGET.max_billed_cost_usd)
            probes.append(_probe(candidate, admission, adapter, config, invocation=invocation))
        time.sleep(0.2)
    public_candidate = RemoteRouteCandidate(
        candidate="contributor-openrouter-free",
        role=RemoteRouteRole.CONTRIBUTOR_PUBLIC,
        gateway="openrouter",
        provider_selector=None,
        requested_model="openrouter/free",
        requested_endpoint_selector=None,
        inventory_reference=str(inventory_path),
        inventory_artifact_hash=inventory_hash,
        terms_reference="inventory://openrouter/free-router",
        reproducible=False,
        notes=("dynamic free router intentionally not admitted as a reproducible route",),
    )
    candidates.append(public_candidate)
    probes.append(
        RemoteProbeResult(
            candidate=public_candidate.candidate,
            role=public_candidate.role,
            status=RemoteProbeStatus.BLOCKED,
            failure_class="dynamic_route_not_admitted",
            notes=("openrouter/free can change provider/model identity between requests",),
        )
    )
    report = RemoteBakeoffReport(
        run_id=run_id,
        measured_at=measured_at,
        inventory_reference=str(inventory_path),
        inventory_artifact_hash=inventory_hash,
        budget=budget,
        billed_spend_usd=sum(item.billed_cost_usd or 0 for item in probes),
        candidates=tuple(candidates),
        probes=tuple(probes),
        roster={
            "contributor_public": "blocked_dynamic_openrouter_free",
            "private_worker": [
                item.candidate for item in candidates if item.role is RemoteRouteRole.PRIVATE_WORKER
            ],
            "private_reviewer": [
                item.candidate
                for item in candidates
                if item.role is RemoteRouteRole.PRIVATE_REVIEWER
            ],
            "blocked_execution": "deterministic_only",
        },
        warnings=(
            "No remote route is granted trading, broker, OMS, risk, reconciliation, or execution authority.",
            "A blocked dynamic contributor route is not a quality failure; it is an admission reproducibility decision.",
        ),
    )
    report_path = output_dir / "remote-model-bakeoff.json"
    report_hash = write_remote_report(report, report_path)
    _write_roster(
        args.roster,
        report,
        report_hash,
        report_reference=str(report_path),
    )
    print(f"run_id={run_id}")
    print(f"report={report_path}")
    print(f"report_sha256={report_hash}")
    print(f"inventory_sha256={inventory_hash}")
    print(f"reserved_budget_usd={budget.spent_usd:.9f}")
    for item in probes:
        print(f"probe={item.candidate}:{item.invocation or 'none'}:{item.status.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
