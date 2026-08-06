# Policy-routed model gateway

AdvisorAI's model boundary is policy-enforced, provider-neutral, and separate
from the deterministic trading boundary. Use `PolicyGateway` (exported as
`ModelGateway`) for every remote model call. Do not call a provider adapter
directly from portfolio, risk, OMS, reconciliation, or execution code.

## Route policy

| Route class | Typical admission | Purpose |
| --- | --- | --- |
| `CONTRIBUTOR_PUBLIC` | Public only; free/dynamic routes may be non-reproducible | Extraction and low-risk worker volume |
| `PRIVATE_WORKER` | Sanitized internal and limited confidential data; ZDR/no-training | High-volume structured research |
| `PRIVATE_REVIEWER` | Minimal confidential evidence; ZDR/no-training | Portfolio-influencing synthesis and critical review |
| `BLOCKED` | Secrets/execution | Deterministic risk, OMS, reconciliation, and abstention |

The public contributor route is intentionally not required to be ZDR: the
current inventory has no free ZDR text-generation endpoint. A free router must
instead deny provider data collection, enforce a zero price cap, and be marked
`reproducible=False` when its actual model can vary.

`DecisionImpact` is separate from data sensitivity. Public non-critical work
uses `CONTRIBUTOR_PUBLIC`; sanitized research uses `PRIVATE_WORKER`;
portfolio-influencing, conflicting, or low-confidence work uses
`PRIVATE_REVIEWER`; `EXECUTION` is always `BLOCKED`.

The router never uses answer length as a routing signal. A missing tier,
unverified provider terms, unknown actual provider identity, price/parameter
mismatch, or an unapproved tool produces abstention or a policy rejection.

## Provider admission and profiles

Provider terms must be verified outside the code and represented explicitly:

```python
from advisorai.gateway import GatewayPolicyConfig, ProviderTerms, PolicyGateway
from advisorai.ports import GatewayDataClass, GatewayTier

config = GatewayPolicyConfig(
    contributor_terms=ProviderTerms(
        name="contributor/provider-model",
        tier=GatewayTier.CONTRIBUTOR,
        allowed_data_classes=(GatewayDataClass.PUBLIC,),
        retention_policy="documented-provider-retention",
        training_policy="public-only-contract",
        terms_verified=True,
        terms_reference="legal-review:2026-08-01",
    ),
    private_terms=ProviderTerms(
        name="private/provider-model",
        tier=GatewayTier.PRIVATE,
        allowed_data_classes=(
            GatewayDataClass.PUBLIC,
            GatewayDataClass.INTERNAL_SANITIZED,
            GatewayDataClass.CONFIDENTIAL,
        ),
        retention_policy="zero",
        training_policy="no_training_zdr",
        terms_verified=True,
        terms_reference="contract:private-zdr-v1",
    ),
)

gateway = PolicyGateway(
    contributor=contributor_adapter,
    private=private_adapter,
    config=config,
    recorder=gateway_recorder,
)
```

For the four route classes, construct `ProviderRoutePolicy` and
`RouteProfile` objects. The caller supplies task/data/impact metadata, not a
model name:

```python
from advisorai.gateway import ProviderRoutePolicy, RouteProfile
from advisorai.ports import RouteTier

worker_policy = ProviderRoutePolicy(
    route_tier=RouteTier.PRIVATE_WORKER,
    provider_only=("novita",),
    model_only=("inclusionai/ling-2.6-flash",),
    data_collection="deny",
    zdr=True,
    allow_fallbacks=False,       # no silent provider fallback
    require_parameters=True,
    max_prompt_price=0.1,
    max_completion_price=0.3,
)
worker = RouteProfile(
    "ling-worker", RouteTier.PRIVATE_WORKER, worker_adapter,
    worker_policy, worker_terms, fallback_profile_ids=("reviewer",),
)
```

Fallback profile IDs are an explicitly reviewed list. A contributor may fall
back to another public profile and, when configured, a private worker. A
worker may fall back to a reviewed private reviewer. A reviewer never falls
back to a contributor; when reviewed private routes are exhausted, the gateway
returns a deterministic abstention.

The adapters remain ordinary typed direct/LiteLLM/OmniRoute adapters. Route
objects should carry the exact provider, model, gateway, endpoint variant,
retention policy, training policy, terms reference, and `terms_verified=True`
values used for admission. `build_policy_gateway` in
`advisorai.integrations` is the composition helper for application wiring.

## Request contract

Set `GatewayRequest.data_class`, `decision_impact`, `task_kind`, `output_kind`,
confidence, and evidence IDs at the call site. Supply `sensitive_values` to
`gateway.complete(...)` only while converting a raw payload into a request;
the values are replaced before the provider call and are never recorded.

Contributor requests have no tools. Private requests may use only the
configured read-only evidence allowlist. Neither tier can receive secrets,
broker/account/order data, or execution tools. Model outputs are typed,
validated, and always non-authoritative; deterministic validation must decide
whether an output becomes evidence.

Every recorded call includes request/prompt/evidence hashes, selected route
tier and decision impact, requested and actual provider/model/gateway
identities, endpoint variant, retention/training terms, route-policy and
redaction-policy hashes, escalation reason, provider request ID, latency,
tokens, price parameters, and cost. Prompt text and credentials are never
stored in the gateway-call ledger.

Muse Spark or any other contributor is admitted by the same policy: unless its
current written terms are verified for the intended data class, it is
`PUBLIC`-only and cannot receive internal, confidential, portfolio, risk,
strategy, broker, or execution material.
