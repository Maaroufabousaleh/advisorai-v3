"""Policy-enforced, four-class model gateway.

The gateway is deliberately a narrow wrapper around the existing typed model
adapters.  It decides *whether* a request may leave the process and which
provider tier may receive it; it never grants a model trading authority.  Risk,
portfolio, OMS, reconciliation, and execution code remain deterministic and do
not depend on this module.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from time import perf_counter_ns
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.contracts.core import is_forbidden_authority_action, normalize_authority_action
from advisorai.ports import (
    DecisionImpact,
    GatewayDataClass,
    GatewayMessage,
    GatewayOutputKind,
    GatewayRequest,
    GatewayResponse,
    GatewayRoute,
    GatewayTier,
    GatewayTool,
    ModelGatewayPort,
    RouteTier,
    validate_gateway_output,
)

from .core import GatewayAttempt, GatewayFailure, GatewayRecorder


class GatewayPolicyError(PermissionError):
    """Raised when a request or provider route violates the gateway policy."""


_CLASS_RANK = {
    GatewayDataClass.UNCLASSIFIED: -1,
    GatewayDataClass.PUBLIC: 0,
    GatewayDataClass.INTERNAL_SANITIZED: 1,
    GatewayDataClass.CONFIDENTIAL: 2,
    GatewayDataClass.SECRET_EXECUTION: 3,
}

_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|authorization|cookie|password|passphrase|secret|token|private[_-]?key|nkey)",
    re.I,
)
_EXECUTION_KEY = re.compile(
    r"(?:account|broker|balance|credential|fill|kill[_-]?switch|order|execution|reconciliation)",
    re.I,
)
_CONFIDENTIAL_KEY = re.compile(
    r"(?:strategy|feature(?:[_-]?definition)?|model[_-]?(?:result|output)|portfolio|position|exposure|holdings|risk|notebook|research)",
    re.I,
)
_SANITIZED_KEY = re.compile(r"(?:_bucket|_status|_band)$", re.I)
_SECRET_TEXT = re.compile(
    r"(?:"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----|"
    r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}|"
    r"\b(?:sk|rk|ghp|hf|xox)[-_][A-Za-z0-9_-]{12,}|"
    r"\b(?:api[_-]?key|authorization|cookie|password|passphrase|secret|token)"
    r"\s*[:=]\s*(?!\[REDACTED\])[^\s,;]+"
    r")",
    re.I | re.S,
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?P<name>api[_-]?key|authorization|cookie|password|passphrase|secret|token)"
    r"(?P<separator>\s*[:=]\s*)(?!\[REDACTED\])(?P<value>[^\s,;]+)",
    re.I,
)
_BEARER_VALUE = re.compile(r"(?P<prefix>\bBearer\s+)(?!\[REDACTED\])[^\s,;]+", re.I)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.I | re.S
)
_TASK_BLOCK = re.compile(r"(?:submit|place|cancel|execute|execution|reconcile|broker[_-]?action)", re.I)
_TASK_PRIVATE = re.compile(
    r"(?:risk|portfolio|sizing|allocation|decision|thesis|conflict|synthesis|council|"
    r"high[_-]?value|final[_-]?(?:research|thesis|review))",
    re.I,
)
_FORBIDDEN_OUTPUT_KEYS = {
    "order",
    "orders",
    "broker_action",
    "execute",
    "execution",
    "kill_switch",
    "risk_limit",
    "target_weight",
    "trade_instruction",
}
_FORBIDDEN_TOOL_KEYS = {
    "account",
    "account_id",
    "balance",
    "broker",
    "credential",
    "fill",
    "order",
    "orders",
    "position",
    "secret",
    "token",
}


def _coerce_data_class(value: GatewayDataClass | str | None) -> GatewayDataClass:
    if value is None:
        return GatewayDataClass.UNCLASSIFIED
    if isinstance(value, GatewayDataClass):
        return value
    try:
        return GatewayDataClass(str(value).strip().lower())
    except ValueError as exc:
        raise GatewayPolicyError(f"unknown gateway data class: {value!r}") from exc


def _max_data_class(*values: GatewayDataClass) -> GatewayDataClass:
    return max(values, key=lambda item: _CLASS_RANK[item])


def _is_no_training_policy(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return normalized in {
        "no_training",
        "no_train",
        "zdr",
        "zero_data_retention",
        "zero_data_retention_no_training",
        "no_training_zdr",
        "zdr_no_training",
        "no_training_zero_data_retention",
        "opt_out_no_training",
    }


def _normalise_sensitive_values(
    values: Mapping[str, str] | Sequence[str] | None,
) -> tuple[str, ...]:
    if values is None:
        return ()
    raw = tuple(values.values()) if isinstance(values, Mapping) else tuple(values)
    return tuple(sorted({item for item in raw if isinstance(item, str) and item}, key=len, reverse=True))


def contains_secret_material(value: str, *, sensitive_values: Sequence[str] = ()) -> bool:
    """Return whether text still contains an unredacted credential-like value."""

    if any(secret and secret in value for secret in sensitive_values):
        return True
    return _SECRET_TEXT.search(value) is not None


def redact_text(value: str, *, sensitive_values: Sequence[str] = ()) -> str:
    """Redact supplied credentials and common credential encodings in text."""

    redacted = value
    for secret in sensitive_values:
        redacted = redacted.replace(secret, "[REDACTED]")
    redacted = _PRIVATE_KEY.sub("[REDACTED]", redacted)
    redacted = _BEARER_VALUE.sub(r"\g<prefix>[REDACTED]", redacted)
    redacted = _SECRET_ASSIGNMENT.sub(r"\g<name>\g<separator>[REDACTED]", redacted)
    return redacted


def redact_request(
    request: GatewayRequest,
    *,
    sensitive_values: Mapping[str, str] | Sequence[str] | None = None,
    policy_version: str | None = None,
) -> tuple[GatewayRequest, bool]:
    """Return a credential-free request copy and whether any text changed."""

    values = _normalise_sensitive_values(sensitive_values)
    messages: list[GatewayMessage] = []
    changed = False
    for message in request.messages:
        content = redact_text(message.content, sensitive_values=values)
        changed = changed or content != message.content
        messages.append(message.model_copy(update={"content": content}))
    updates: dict[str, object] = {"messages": tuple(messages)}
    if policy_version is not None:
        updates["redaction_policy_version"] = policy_version
    return request.model_copy(update=updates), changed


def classify_payload(
    payload: object,
    *,
    declared: GatewayDataClass | str | None = None,
) -> GatewayDataClass:
    """Infer a conservative class from structured keys and credential text.

    A caller may declare a stronger class, but never a weaker class than the
    structural evidence requires.  Raw broker/account/order material is always
    classified as ``SECRET_EXECUTION`` and must not reach a model tier.
    """

    inferred = _coerce_data_class(declared)

    def visit(value: object) -> None:
        nonlocal inferred
        if isinstance(value, Mapping):
            for key, child in value.items():
                token = str(key)
                if _SANITIZED_KEY.search(token) and (
                    _EXECUTION_KEY.search(token) or _CONFIDENTIAL_KEY.search(token)
                ):
                    inferred = _max_data_class(inferred, GatewayDataClass.INTERNAL_SANITIZED)
                elif _EXECUTION_KEY.search(token) or _SECRET_KEY.search(token):
                    inferred = _max_data_class(inferred, GatewayDataClass.SECRET_EXECUTION)
                elif _CONFIDENTIAL_KEY.search(token):
                    inferred = _max_data_class(inferred, GatewayDataClass.CONFIDENTIAL)
                visit(child)
        elif isinstance(value, str):
            if contains_secret_material(value):
                inferred = _max_data_class(inferred, GatewayDataClass.SECRET_EXECUTION)
        elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
            for child in value:
                visit(child)

    visit(payload)
    return inferred


def _hash_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


class ProviderRoutePolicy(BaseModel):
    """Provider-selection and privacy constraints for one route class."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route_tier: RouteTier
    provider_only: tuple[str, ...] = ()
    provider_order: tuple[str, ...] = ()
    model_only: tuple[str, ...] = ()
    data_collection: Literal["deny", "allow"] = "deny"
    zdr: bool = False
    allow_fallbacks: bool = False
    require_parameters: bool = True
    max_prompt_price: float = Field(default=0, ge=0)
    max_completion_price: float = Field(default=0, ge=0)
    max_request_price: float = Field(default=0, ge=0)
    require_billed_cost: bool = True
    actual_identity_mode: Literal["exact", "allowlisted", "dynamic"] = "exact"
    reproducible: bool = True
    policy_version: str = Field(default="provider-policy-v1", min_length=1)

    @field_validator("provider_only", "provider_order", "model_only")
    @classmethod
    def normalize_identity_lists(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("provider policy identities cannot be blank")
        if len(normalized) != len(set(item.lower() for item in normalized)):
            raise ValueError("provider policy identities must be unique")
        return normalized

    @field_validator("policy_version")
    @classmethod
    def normalize_policy_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("provider policy version cannot be blank")
        return value.strip()

    @model_validator(mode="after")
    def validate_provider_policy(self) -> ProviderRoutePolicy:
        if self.route_tier is RouteTier.BLOCKED:
            raise ValueError("blocked routes cannot have provider policies")
        if self.route_tier is RouteTier.CONTRIBUTOR_PUBLIC:
            if self.data_collection != "deny":
                raise ValueError("public contributor routes must deny provider data collection")
            if any(
                price != 0
                for price in (
                    self.max_prompt_price,
                    self.max_completion_price,
                    self.max_request_price,
                )
            ):
                raise ValueError("public contributor routes must have zero price caps")
            if self.actual_identity_mode == "dynamic" and not self.allow_fallbacks:
                raise ValueError("dynamic public routes must make fallback behavior explicit")
            if self.actual_identity_mode == "dynamic" and self.reproducible:
                raise ValueError("dynamic public routes cannot claim reproducible admission")
        else:
            if not self.zdr:
                raise ValueError("private routes require ZDR/no-training admission")
            if self.data_collection != "deny":
                raise ValueError("private routes must deny provider data collection")
            if self.allow_fallbacks:
                raise ValueError("private routes must disable silent provider fallback")
            if self.actual_identity_mode == "dynamic":
                raise ValueError("private routes require exact or allowlisted provider identity")
        return self

    def policy_hash(self) -> str:
        return _hash_json(self.model_dump(mode="json", round_trip=True))

    def request_options(self) -> dict[str, object]:
        """Return provider-native routing constraints for OpenAI-compatible APIs."""

        provider: dict[str, object] = {
            "data_collection": self.data_collection,
            "allow_fallbacks": self.allow_fallbacks,
            "require_parameters": self.require_parameters,
            "max_price": {
                "prompt": self.max_prompt_price,
                "completion": self.max_completion_price,
                "request": self.max_request_price,
            },
        }
        if self.zdr:
            provider["zdr"] = True
        if self.provider_only:
            provider["only"] = list(self.provider_only)
        if self.provider_order:
            provider["order"] = list(self.provider_order)
        return {"provider": provider}

    @property
    def max_price(self) -> dict[str, float]:
        return {
            "prompt": self.max_prompt_price,
            "completion": self.max_completion_price,
            "request": self.max_request_price,
        }

    def admits_route(self, route: GatewayRoute, *, actual: bool = False) -> bool:
        if self.provider_only and route.provider not in self.provider_only:
            return False
        if self.model_only and route.model not in self.model_only:
            return False
        if actual and self.actual_identity_mode == "dynamic":
            return True
        return True

    def validate_response(self, response: GatewayResponse, *, pinned_route: GatewayRoute) -> None:
        actual = response.route
        actual_endpoint_variant = response.actual_endpoint_variant
        if not response.actual_provider or not response.actual_model or not response.actual_gateway:
            raise GatewayPolicyError("provider response omitted actual provider/model/gateway identity")
        if not actual_endpoint_variant:
            raise GatewayPolicyError("provider response omitted actual endpoint identity")
        if (
            actual.provider != response.actual_provider
            or actual.model != response.actual_model
            or actual.gateway != response.actual_gateway
        ):
            raise GatewayPolicyError("provider response identity fields do not match its actual route")
        if actual.endpoint_variant and actual.endpoint_variant != actual_endpoint_variant:
            raise GatewayPolicyError("provider response endpoint fields do not match its actual route")
        if self.actual_identity_mode == "exact":
            if (
                actual.provider != pinned_route.provider
                or actual.model != pinned_route.model
                or actual.gateway != pinned_route.gateway
            ):
                raise GatewayPolicyError("provider returned an identity different from its pinned route")
        elif self.actual_identity_mode == "allowlisted" and not self.admits_route(actual, actual=True):
            raise GatewayPolicyError("provider returned an identity outside the reviewed allowlist")
        elif self.actual_identity_mode == "dynamic" and self.route_tier is not RouteTier.CONTRIBUTOR_PUBLIC:
            raise GatewayPolicyError("dynamic provider identities are public-only")
        if pinned_route.endpoint_variant and actual_endpoint_variant != pinned_route.endpoint_variant:
            raise GatewayPolicyError("provider returned an endpoint variant different from its pinned route")
        if not self.admits_route(actual, actual=True):
            raise GatewayPolicyError("provider returned an identity outside the route policy")
        if self.require_parameters and (
            response.input_price_per_million is None
            or response.output_price_per_million is None
            or response.request_price_usd is None
        ):
            raise GatewayPolicyError("provider response omitted required pricing parameters")
        if self.require_billed_cost and response.billed_cost_usd is None:
            raise GatewayPolicyError("provider response omitted billed usage cost")
        if response.input_price_per_million is not None and (
            response.input_price_per_million > self.max_prompt_price
        ):
            raise GatewayPolicyError("provider prompt price exceeds the route policy cap")
        if response.output_price_per_million is not None and (
            response.output_price_per_million > self.max_completion_price
        ):
            raise GatewayPolicyError("provider completion price exceeds the route policy cap")
        if response.request_price_usd is not None and response.request_price_usd > self.max_request_price:
            raise GatewayPolicyError("provider request price exceeds the route policy cap")


@dataclass(frozen=True, slots=True)
class RouteProfile:
    """One admitted route identity and its provider contract."""

    profile_id: str
    route_tier: RouteTier
    adapter: ModelGatewayPort
    provider_policy: ProviderRoutePolicy
    terms: ProviderTerms
    fallback_profile_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise ValueError("route profile ID cannot be blank")
        if self.provider_policy.route_tier is not self.route_tier:
            raise ValueError("route profile and provider policy tiers must match")
        if self.route_tier is RouteTier.CONTRIBUTOR_PUBLIC:
            if self.terms.tier is not GatewayTier.CONTRIBUTOR:
                raise ValueError("public contributor profiles require contributor terms")
        elif self.terms.tier is not GatewayTier.PRIVATE:
            raise ValueError("private profiles require private terms")
        if len(self.fallback_profile_ids) != len(set(self.fallback_profile_ids)):
            raise ValueError("route profile fallback IDs must be unique")

    @property
    def route(self) -> GatewayRoute | None:
        return getattr(self.adapter, "route", None)


class ProviderTerms(BaseModel):
    """Explicit provider contract admission for one gateway tier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    tier: GatewayTier
    allowed_data_classes: tuple[GatewayDataClass, ...]
    retention_policy: str = Field(min_length=1)
    training_policy: str = Field(min_length=1)
    terms_verified: bool = False
    terms_reference: str | None = None

    @field_validator("name", "retention_policy", "training_policy")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("provider terms fields cannot be blank")
        return value.strip()

    @field_validator("terms_reference")
    @classmethod
    def normalize_reference(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("provider terms reference cannot be blank")
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_terms(self) -> ProviderTerms:
        if self.tier is GatewayTier.BLOCKED:
            raise ValueError("blocked tier cannot have provider terms")
        if not self.allowed_data_classes:
            raise ValueError("provider terms must allow at least one data class")
        if GatewayDataClass.SECRET_EXECUTION in self.allowed_data_classes:
            raise ValueError("model providers can never be admitted for secret/execution data")
        if self.terms_verified and self.terms_reference is None:
            raise ValueError("verified provider terms require a reference")
        return self

    def permits(self, data_class: GatewayDataClass) -> bool:
        return self.terms_verified and data_class in self.allowed_data_classes

    @classmethod
    def from_route(
        cls,
        route: Any,
        *,
        tier: GatewayTier,
        allowed_data_classes: tuple[GatewayDataClass, ...],
    ) -> ProviderTerms:
        """Build admission terms from a pinned route's explicit contract fields."""

        return cls(
            name=f"{route.provider}/{route.model}/{route.gateway}",
            tier=tier,
            allowed_data_classes=allowed_data_classes,
            retention_policy=route.retention_policy,
            training_policy=route.training_policy,
            terms_verified=route.terms_verified,
            terms_reference=route.terms_reference,
        )


class GatewayPolicyConfig(BaseModel):
    """Configuration for the contributor/private policy router."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contributor_terms: ProviderTerms
    private_terms: ProviderTerms
    provider_policies: tuple[ProviderRoutePolicy, ...] = ()
    route_order: tuple[str, ...] = ()
    allow_contributor_worker_fallback: bool = True
    allow_worker_reviewer_fallback: bool = True
    abstain_on_provider_failure: bool = True
    policy_version: str = Field(default="gateway-policy-v1", min_length=1)
    redaction_policy_version: str = Field(default="redaction-v1", min_length=1)
    low_confidence_threshold: float = Field(default=0.6, ge=0, le=1)
    private_tool_allowlist: tuple[str, ...] = (
        "read_evidence",
        "read_artifact",
        "search_evidence",
        "lookup_evidence",
        "read_market_context",
        "read_orderbook",
    )

    @field_validator("policy_version", "redaction_policy_version")
    @classmethod
    def normalize_versions(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("gateway policy versions cannot be blank")
        return value.strip()

    @field_validator("private_tool_allowlist")
    @classmethod
    def normalize_tool_allowlist(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().lower() for item in value)
        if any(not item for item in normalized):
            raise ValueError("private tool allowlist cannot contain blank names")
        if len(normalized) != len(set(normalized)):
            raise ValueError("private tool allowlist must be unique")
        return normalized

    @field_validator("route_order")
    @classmethod
    def normalize_route_order(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("route profile order cannot contain blank IDs")
        if len(normalized) != len(set(normalized)):
            raise ValueError("route profile order must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_tiers(self) -> GatewayPolicyConfig:
        if self.contributor_terms.tier is not GatewayTier.CONTRIBUTOR:
            raise ValueError("contributor_terms must describe the contributor tier")
        if self.private_terms.tier is not GatewayTier.PRIVATE:
            raise ValueError("private_terms must describe the private tier")
        if GatewayDataClass.CONFIDENTIAL in self.contributor_terms.allowed_data_classes:
            raise ValueError("contributors cannot be admitted for confidential data")
        if not _is_no_training_policy(self.private_terms.training_policy):
            raise ValueError("private tier requires a verified no-training/ZDR policy")
        return self

    def route_policy_hash(self) -> str:
        return _hash_json(
            {
                "policy_version": self.policy_version,
                "provider_policies": [
                    policy.model_dump(mode="json", round_trip=True)
                    for policy in self.provider_policies
                ],
                "route_order": self.route_order,
                "allow_contributor_worker_fallback": self.allow_contributor_worker_fallback,
                "allow_worker_reviewer_fallback": self.allow_worker_reviewer_fallback,
            }
        )

    def redaction_policy_hash(self) -> str:
        return _hash_json(
            {
                "version": self.redaction_policy_version,
                "rules": (
                    "explicit_sensitive_values",
                    "private_key",
                    "bearer",
                    "secret_assignment",
                ),
            }
        )


class GatewayDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tier: GatewayTier
    data_class: GatewayDataClass
    reason: str = Field(min_length=1)
    redacted: bool = False
    route_tier: RouteTier = RouteTier.CONTRIBUTOR_PUBLIC
    decision_impact: DecisionImpact = DecisionImpact.NON_CRITICAL


@dataclass(frozen=True, slots=True)
class _SelectedRoute:
    tier: GatewayTier
    adapter: ModelGatewayPort
    terms: ProviderTerms


class PolicyGateway:
    """A three-tier gateway that enforces classification before every call.

    ``contributor`` and ``private`` may be a direct typed adapter or a
    ``GatewayChain``.  A missing tier is a hard failure; sensitive requests are
    never silently downgraded to a contributor route.
    """

    name = "policy_gateway"

    def __init__(
        self,
        *,
        contributor: ModelGatewayPort | None = None,
        private: ModelGatewayPort | None = None,
        config: GatewayPolicyConfig,
        recorder: GatewayRecorder | None = None,
        profiles: tuple[RouteProfile, ...] | None = None,
    ) -> None:
        self.contributor = contributor
        self.private = private
        self.config = config
        self.recorder = recorder
        self.decisions: list[GatewayDecision] = []
        self.profiles = tuple(profiles or ())
        if self.profiles:
            profile_ids = {profile.profile_id for profile in self.profiles}
            for profile in self.profiles:
                if any(fallback not in profile_ids for fallback in profile.fallback_profile_ids):
                    raise ValueError(f"route profile {profile.profile_id!r} references an unknown fallback")
            if self.config.route_order and set(self.config.route_order) != profile_ids:
                raise ValueError("route_order must name every configured route profile exactly once")

    def route_policy_hash(self) -> str:
        return _hash_json(
            {
                "config": self.config.route_policy_hash(),
                "profiles": [
                    {
                        "id": profile.profile_id,
                        "tier": profile.route_tier.value,
                        "route": profile.route.model_dump(mode="json", round_trip=True)
                        if profile.route is not None
                        else None,
                        "provider_policy": profile.provider_policy.policy_hash(),
                        "fallbacks": profile.fallback_profile_ids,
                    }
                    for profile in self.profiles
                ],
            }
        )

    def classify(self, request: GatewayRequest, *, payload: object | None = None) -> GatewayDataClass:
        declared = _coerce_data_class(request.data_class)
        if declared is GatewayDataClass.UNCLASSIFIED:
            return declared
        if request.privacy_class.lower() in {"secret", "credential"}:
            declared = _max_data_class(declared, GatewayDataClass.SECRET_EXECUTION)
        return classify_payload(payload, declared=declared) if payload is not None else declared

    def decide(
        self,
        request: GatewayRequest,
        *,
        payload: object | None = None,
        redacted: bool = False,
    ) -> GatewayDecision:
        data_class = self.classify(request, payload=payload)
        task = request.task_kind.strip().lower()
        impact = request.decision_impact
        if request.portfolio_influence:
            impact = DecisionImpact.PORTFOLIO_INFLUENCING
        if _TASK_BLOCK.search(task):
            impact = DecisionImpact.EXECUTION
        elif _TASK_PRIVATE.search(task) and impact is DecisionImpact.NON_CRITICAL:
            impact = DecisionImpact.PORTFOLIO_INFLUENCING
        if data_class is GatewayDataClass.UNCLASSIFIED:
            decision = GatewayDecision(
                tier=GatewayTier.BLOCKED,
                data_class=data_class,
                reason="explicit data classification is required before model routing",
                redacted=redacted,
                route_tier=RouteTier.BLOCKED,
                decision_impact=impact,
            )
            self.decisions.append(decision)
            return decision
        if data_class is GatewayDataClass.SECRET_EXECUTION or impact is DecisionImpact.EXECUTION:
            decision = GatewayDecision(
                tier=GatewayTier.BLOCKED,
                data_class=GatewayDataClass.SECRET_EXECUTION,
                reason="secret/execution data and execution tasks are deterministic-only",
                redacted=redacted,
                route_tier=RouteTier.BLOCKED,
                decision_impact=impact,
            )
            self.decisions.append(decision)
            return decision

        reasons: list[str] = []
        if data_class is GatewayDataClass.CONFIDENTIAL:
            reasons.append("confidential data")
        if impact is DecisionImpact.PORTFOLIO_INFLUENCING:
            reasons.append("portfolio-influencing request")
        elif impact is DecisionImpact.RESEARCH:
            reasons.append("research request")
        if request.conflicting_evidence:
            reasons.append("conflicting evidence")
        if request.confidence is not None and request.confidence < self.config.low_confidence_threshold:
            reasons.append("low confidence")
        critical = (
            impact is DecisionImpact.PORTFOLIO_INFLUENCING
            or request.conflicting_evidence
            or request.confidence is not None
            and request.confidence < self.config.low_confidence_threshold
        )
        if data_class in {
            GatewayDataClass.INTERNAL_SANITIZED,
            GatewayDataClass.CONFIDENTIAL,
        }:
            route_tier = RouteTier.PRIVATE_REVIEWER if critical else RouteTier.PRIVATE_WORKER
        elif critical:
            route_tier = RouteTier.PRIVATE_REVIEWER
        else:
            route_tier = RouteTier.CONTRIBUTOR_PUBLIC
        tier = (
            GatewayTier.CONTRIBUTOR
            if route_tier is RouteTier.CONTRIBUTOR_PUBLIC
            else GatewayTier.PRIVATE
        )
        decision = GatewayDecision(
            tier=tier,
            data_class=data_class,
            reason="; ".join(reasons) if reasons else "public/sanitized worker task",
            redacted=redacted,
            route_tier=route_tier,
            decision_impact=impact,
        )
        self.decisions.append(decision)
        return decision

    def complete(
        self,
        request: GatewayRequest,
        *,
        payload: object | None = None,
        sensitive_values: Mapping[str, str] | Sequence[str] | None = None,
    ) -> GatewayResponse:
        """Route one request after redaction, classification, and tool checks."""

        values = _normalise_sensitive_values(sensitive_values)
        raw_has_secret = any(
            contains_secret_material(message.content, sensitive_values=values)
            for message in request.messages
        )
        if raw_has_secret and not values:
            decision = self.decide(request, payload=payload)
            raise GatewayPolicyError(
                f"gateway request blocked: credential-like text requires explicit redaction"
                f" ({decision.reason})"
            )
        candidate, redacted = redact_request(
            request,
            sensitive_values=values,
            policy_version=self.config.redaction_policy_version,
        )
        candidate = candidate.model_copy(
            update={
                "redaction_policy_hash": self.config.redaction_policy_hash(),
                "route_policy_hash": self.route_policy_hash(),
            }
        )
        # If a credential-looking value remains after the caller's explicit
        # redaction pass, fail closed rather than guessing that it is harmless.
        if any(contains_secret_material(message.content) for message in candidate.messages):
            decision = self.decide(candidate, payload=payload, redacted=redacted)
            raise GatewayPolicyError(f"gateway request blocked: {decision.reason}")

        decision = self.decide(candidate, payload=payload, redacted=redacted)
        if decision.tier is GatewayTier.BLOCKED:
            raise GatewayPolicyError(f"gateway request blocked: {decision.reason}")
        if self.profiles:
            return self._complete_profiles(candidate, decision)
        selected = self._select(decision, candidate)
        self._validate_tools(candidate.tools, selected.tier)
        attempt_request = self._align_route(candidate, selected.adapter)
        started = perf_counter_ns()
        try:
            response = selected.adapter.complete(attempt_request)
            response = GatewayResponse.model_validate(response)
            if response.request_id != candidate.request_id:
                raise GatewayFailure("policy gateway received a response for another request")
            permitted_routes = {attempt_request.route.gateway, *attempt_request.route.fallback_chain}
            if response.route.gateway not in permitted_routes:
                raise GatewayFailure("policy gateway received an unpinned route response")
            self._validate_response(response, selected.tier, attempt_request)
            elapsed = max(0, (perf_counter_ns() - started) // 1_000_000)
            enriched = response.model_copy(
                update={
                    "tier": selected.tier,
                    "route_tier": decision.route_tier,
                    "data_class": decision.data_class,
                    "decision_impact": decision.decision_impact,
                    "output_kind": candidate.output_kind,
                    "policy_version": self.config.policy_version,
                    "redaction_policy_version": self.config.redaction_policy_version,
                    "escalation_reason": decision.reason if selected.tier is GatewayTier.PRIVATE else None,
                    "retention_policy": selected.terms.retention_policy,
                    "training_policy": selected.terms.training_policy,
                    "terms_verified": selected.terms.terms_verified,
                    "terms_reference": selected.terms.terms_reference,
                    "requested_route": attempt_request.route,
                    "actual_provider": response.route.provider,
                    "actual_model": response.route.model,
                    "actual_gateway": response.route.gateway,
                    "prompt_hash": candidate.prompt_hash(),
                    "evidence_hash": candidate.evidence_hash(),
                    "redaction_policy_hash": candidate.redaction_policy_hash,
                    "route_policy_hash": candidate.route_policy_hash,
                    "authoritative": False,
                    "latency_ms": max(response.latency_ms, elapsed),
                }
            )
            if self.recorder is not None:
                attempt = GatewayAttempt(
                    adapter=getattr(selected.adapter, "name", type(selected.adapter).__name__),
                    route=enriched.route,
                    succeeded=True,
                    latency_ms=elapsed,
                )
                self.recorder.record(attempt)
                self.recorder.record_call(attempt_request, attempt, enriched)
            return enriched
        except Exception as exc:
            elapsed = max(0, (perf_counter_ns() - started) // 1_000_000)
            if self.recorder is not None:
                attempt = GatewayAttempt(
                    adapter=getattr(selected.adapter, "name", type(selected.adapter).__name__),
                    route=attempt_request.route,
                    succeeded=False,
                    latency_ms=elapsed,
                    error=f"{type(exc).__name__}: provider failure",
                )
                self.recorder.record(attempt)
                self.recorder.record_call(attempt_request, attempt)
            raise

    def _select(self, decision: GatewayDecision, request: GatewayRequest) -> _SelectedRoute:
        if decision.tier is GatewayTier.CONTRIBUTOR:
            adapter, terms = self.contributor, self.config.contributor_terms
        else:
            adapter, terms = self.private, self.config.private_terms
        if adapter is None:
            raise GatewayPolicyError(f"{decision.tier.value} tier is not configured")
        if not terms.permits(decision.data_class):
            raise GatewayPolicyError(
                f"provider terms {terms.name!r} do not permit {decision.data_class.value} data"
            )
        return _SelectedRoute(decision.tier, adapter, terms)

    def _complete_profiles(
        self,
        request: GatewayRequest,
        decision: GatewayDecision,
    ) -> GatewayResponse:
        candidates = self._profile_candidates(decision)
        failures: list[str] = []
        for attempt_number, profile in enumerate(candidates):
            if not profile.terms.permits(decision.data_class):
                failures.append(f"{profile.profile_id}:provider_terms_denied")
                continue
            pinned_route = profile.route
            if pinned_route is None:
                failures.append(f"{profile.profile_id}:missing_pinned_route")
                continue
            if not pinned_route.endpoint_variant:
                failures.append(f"{profile.profile_id}:missing_pinned_endpoint_identity")
                continue
            if not profile.provider_policy.admits_route(pinned_route):
                failures.append(f"{profile.profile_id}:route_not_admitted")
                continue
            try:
                self._validate_tools_for_route(request.tools, profile.route_tier)
            except GatewayPolicyError as exc:
                failures.append(f"{profile.profile_id}:tool_policy:{exc}")
                continue
            attempt_request = self._align_route(
                request,
                profile.adapter,
                provider_policy=profile.provider_policy,
            )
            started = perf_counter_ns()
            try:
                response = GatewayResponse.model_validate(profile.adapter.complete(attempt_request))
                if response.request_id != request.request_id:
                    raise GatewayFailure("policy gateway received a response for another request")
                if response.route.gateway != attempt_request.route.gateway:
                    raise GatewayFailure("provider returned an unpinned gateway route")
                profile.provider_policy.validate_response(
                    response,
                    pinned_route=attempt_request.route,
                )
                self._validate_response(
                    response,
                    GatewayTier.CONTRIBUTOR
                    if profile.route_tier is RouteTier.CONTRIBUTOR_PUBLIC
                    else GatewayTier.PRIVATE,
                    attempt_request,
                )
                elapsed = max(0, (perf_counter_ns() - started) // 1_000_000)
                enriched = response.model_copy(
                    update={
                        "tier": (
                            GatewayTier.CONTRIBUTOR
                            if profile.route_tier is RouteTier.CONTRIBUTOR_PUBLIC
                            else GatewayTier.PRIVATE
                        ),
                        "route_tier": profile.route_tier,
                        "data_class": decision.data_class,
                        "decision_impact": decision.decision_impact,
                        "output_kind": request.output_kind,
                        "policy_version": self.config.policy_version,
                        "redaction_policy_version": self.config.redaction_policy_version,
                        "escalation_reason": decision.reason
                        if profile.route_tier is not RouteTier.CONTRIBUTOR_PUBLIC
                        else None,
                        "retention_policy": profile.terms.retention_policy,
                        "training_policy": profile.terms.training_policy,
                        "terms_verified": profile.terms.terms_verified,
                        "terms_reference": profile.terms.terms_reference,
                        "requested_route": attempt_request.route,
                        "actual_provider": response.route.provider,
                        "actual_model": response.route.model,
                        "actual_gateway": response.route.gateway,
                        "prompt_hash": request.prompt_hash(),
                        "evidence_hash": request.evidence_hash(),
                        "redaction_policy_hash": request.redaction_policy_hash,
                        "route_policy_hash": request.route_policy_hash,
                        "authoritative": False,
                        "latency_ms": max(response.latency_ms, elapsed),
                    }
                )
                self._record_profile_attempt(
                    request=attempt_request,
                    profile=profile,
                    response=enriched,
                    succeeded=True,
                    latency_ms=elapsed,
                    attempt_number=attempt_number,
                )
                return enriched
            except Exception as exc:
                elapsed = max(0, (perf_counter_ns() - started) // 1_000_000)
                # Keep provider exceptions out of the deterministic abstention
                # payload; adapters must not be able to smuggle raw prompts or
                # response bodies into logs/evidence.
                failures.append(f"{profile.profile_id}:{type(exc).__name__}")
                self._record_profile_attempt(
                    request=attempt_request,
                    profile=profile,
                    response=None,
                    succeeded=False,
                    latency_ms=elapsed,
                    error=f"{type(exc).__name__}: provider failure",
                    attempt_number=attempt_number,
                )
        if self.config.abstain_on_provider_failure:
            return self._abstain_response(
                request,
                decision,
                "all admitted routes failed or were unavailable: " + " | ".join(failures),
            )
        raise GatewayFailure("all admitted gateway routes failed: " + " | ".join(failures))

    def _profile_candidates(self, decision: GatewayDecision) -> tuple[RouteProfile, ...]:
        profiles_by_id = {profile.profile_id: profile for profile in self.profiles}
        ordered = (
            [profiles_by_id[item] for item in self.config.route_order]
            if self.config.route_order
            else list(self.profiles)
        )
        primary = next(
            (profile for profile in ordered if profile.route_tier is decision.route_tier),
            None,
        )
        if primary is None:
            return ()
        candidate_ids = list(primary.fallback_profile_ids)
        candidates = [primary]
        allowed_tiers = {decision.route_tier}
        if (
            decision.route_tier is RouteTier.CONTRIBUTOR_PUBLIC
            and self.config.allow_contributor_worker_fallback
        ):
            allowed_tiers.add(RouteTier.PRIVATE_WORKER)
        if (
            decision.route_tier is RouteTier.PRIVATE_WORKER
            and self.config.allow_worker_reviewer_fallback
        ):
            allowed_tiers.add(RouteTier.PRIVATE_REVIEWER)
        for profile_id in candidate_ids:
            profile = profiles_by_id[profile_id]
            if profile.route_tier in allowed_tiers:
                candidates.append(profile)
        return tuple(candidates)

    def _record_profile_attempt(
        self,
        *,
        request: GatewayRequest,
        profile: RouteProfile,
        response: GatewayResponse | None,
        succeeded: bool,
        latency_ms: int,
        error: str | None = None,
        attempt_number: int = 0,
    ) -> None:
        if self.recorder is None:
            return
        route = response.route if response is not None else request.route
        attempt = GatewayAttempt(
            adapter=getattr(profile.adapter, "name", type(profile.adapter).__name__),
            route=route,
            succeeded=succeeded,
            latency_ms=latency_ms,
            error=error,
            profile_id=profile.profile_id,
            attempt_number=attempt_number,
        )
        self.recorder.record(attempt)
        self.recorder.record_call(request, attempt, response)

    def _abstain_response(
        self,
        request: GatewayRequest,
        decision: GatewayDecision,
        reason: str,
    ) -> GatewayResponse:
        return GatewayResponse(
            request_id=request.request_id,
            route=request.route,
            requested_route=request.route,
            content="abstain",
            typed_payload={"decision": "abstain", "reason": reason},
            latency_ms=0,
            input_tokens=0,
            output_tokens=0,
            estimated_cost_usd=0,
            tier=GatewayTier.BLOCKED,
            route_tier=RouteTier.BLOCKED,
            data_class=decision.data_class,
            decision_impact=decision.decision_impact,
            output_kind=request.output_kind,
            policy_version=self.config.policy_version,
            redaction_policy_version=self.config.redaction_policy_version,
            redaction_policy_hash=request.redaction_policy_hash,
            route_policy_hash=request.route_policy_hash,
            prompt_hash=request.prompt_hash(),
            evidence_hash=request.evidence_hash(),
            authoritative=False,
        )

    @staticmethod
    def _align_route(
        request: GatewayRequest,
        adapter: ModelGatewayPort,
        *,
        provider_policy: ProviderRoutePolicy | None = None,
    ) -> GatewayRequest:
        adapter_route = getattr(adapter, "route", None)
        updates: dict[str, object] = {}
        if adapter_route is not None and adapter_route != request.route:
            updates["route"] = adapter_route
        if provider_policy is not None:
            updates["provider_options"] = provider_policy.request_options()
        if not updates:
            return request
        # Tier selection is itself an explicit, policy-owned route decision;
        # the caller need not pre-populate a contributor->private fallback.
        # Provider-specific chains still enforce their own fallback allowlist.
        return request.model_copy(update=updates)

    def _validate_tools(self, tools: tuple[GatewayTool, ...], tier: GatewayTier) -> None:
        self._validate_tools_for_route(
            tools,
            RouteTier.CONTRIBUTOR_PUBLIC if tier is GatewayTier.CONTRIBUTOR else RouteTier.PRIVATE_REVIEWER,
        )

    def _validate_tools_for_route(
        self,
        tools: tuple[GatewayTool, ...],
        route_tier: RouteTier,
    ) -> None:
        if route_tier is RouteTier.CONTRIBUTOR_PUBLIC and tools:
            raise GatewayPolicyError("contributor routes cannot receive tools")
        if route_tier in {RouteTier.PRIVATE_WORKER, RouteTier.PRIVATE_REVIEWER}:
            allowlist = set(self.config.private_tool_allowlist)
            for tool in tools:
                normalized = tool.name.strip().lower()
                if normalized not in allowlist or not self._is_read_only_tool(normalized):
                    raise GatewayPolicyError(
                        f"private route tool is not an approved read-only evidence tool: {tool.name}"
                    )

    @staticmethod
    def _is_read_only_tool(name: str) -> bool:
        return (
            name.startswith(("read_", "fetch_", "get_", "search_", "lookup_", "inspect_"))
            and not any(token in name for token in ("order", "broker", "account", "balance", "position"))
            or name == "read_orderbook"
        )

    def _validate_response(
        self,
        response: GatewayResponse,
        tier: GatewayTier,
        request: GatewayRequest,
    ) -> None:
        actual_endpoint_variant = response.actual_endpoint_variant
        if not response.actual_provider or not response.actual_model or not response.actual_gateway:
            raise GatewayPolicyError("provider response omitted actual provider/model/gateway identity")
        if not actual_endpoint_variant:
            raise GatewayPolicyError("provider response omitted actual endpoint identity")
        if (
            response.route.provider != response.actual_provider
            or response.route.model != response.actual_model
            or response.route.gateway != response.actual_gateway
        ):
            raise GatewayPolicyError("provider response identity fields do not match its actual route")
        if response.route.endpoint_variant and response.route.endpoint_variant != actual_endpoint_variant:
            raise GatewayPolicyError("provider response endpoint fields do not match its actual route")
        if request.route.endpoint_variant and request.route.endpoint_variant != actual_endpoint_variant:
            raise GatewayPolicyError("provider response endpoint differs from its requested route")
        if response.authoritative:
            raise GatewayPolicyError("model output cannot be authoritative")
        if response.typed_payload is None and not response.tool_calls:
            raise GatewayPolicyError(
                "governed model outputs must be typed structured payloads or approved tool calls"
            )
        if tier is GatewayTier.CONTRIBUTOR and response.typed_payload is None:
            raise GatewayPolicyError("contributor outputs must be typed structured payloads")
        if request.output_kind is not GatewayOutputKind.GENERIC and response.typed_payload is None:
            raise GatewayPolicyError(
                f"{request.output_kind.value} output requires a typed payload"
            )
        for call in response.tool_calls:
            name = str(call.get("name", "")).strip().lower()
            if tier is GatewayTier.CONTRIBUTOR:
                raise GatewayPolicyError("contributor routes cannot return tool calls")
            if name not in set(self.config.private_tool_allowlist) or not self._is_read_only_tool(name):
                raise GatewayPolicyError(f"model returned an unapproved tool call: {name}")
            definition = next(
                (tool for tool in request.tools if tool.name.strip().lower() == name),
                None,
            )
            if definition is None:
                raise GatewayPolicyError(f"model returned a tool that was not requested: {name}")
            arguments = call.get("arguments")
            if isinstance(arguments, Mapping):
                parsed_arguments: Mapping[str, object] = arguments
            elif isinstance(arguments, str):
                try:
                    decoded_arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise GatewayPolicyError("model tool-call arguments are not valid JSON") from exc
                if not isinstance(decoded_arguments, Mapping):
                    raise GatewayPolicyError("model tool-call arguments must be a JSON object")
                parsed_arguments = decoded_arguments
            else:
                raise GatewayPolicyError("model tool-call arguments are required")
            self._validate_tool_mapping(parsed_arguments)
            self._validate_json_schema(parsed_arguments, definition.input_schema)
        if response.typed_payload is not None:
            self._validate_output_mapping(response.typed_payload)
            try:
                validate_gateway_output(request.output_kind, response.typed_payload)
            except (TypeError, ValueError) as exc:
                raise GatewayPolicyError(
                    f"model output does not satisfy {request.output_kind.value} schema"
                ) from exc

    @classmethod
    def _validate_output_mapping(cls, value: object) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = normalize_authority_action(str(key))
                if normalized in _FORBIDDEN_OUTPUT_KEYS or is_forbidden_authority_action(normalized):
                    raise GatewayPolicyError(
                        f"model output contains a forbidden authority field: {key}"
                    )
                cls._validate_output_mapping(child)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for child in value:
                cls._validate_output_mapping(child)

    @classmethod
    def _validate_tool_mapping(cls, value: Mapping[object, object]) -> None:
        for key, child in value.items():
            normalized = normalize_authority_action(str(key))
            if normalized in _FORBIDDEN_TOOL_KEYS or is_forbidden_authority_action(normalized):
                raise GatewayPolicyError(
                    f"model tool-call arguments contain a forbidden field: {key}"
                )
            if isinstance(child, Mapping):
                cls._validate_tool_mapping(child)
            elif isinstance(child, Sequence) and not isinstance(child, (str, bytes, bytearray)):
                for item in child:
                    if isinstance(item, Mapping):
                        cls._validate_tool_mapping(item)

    @classmethod
    def _validate_json_schema(cls, value: object, schema: Mapping[str, object], path: str = "$") -> None:
        schema_type = schema.get("type")
        if schema_type == "object":
            if not isinstance(value, Mapping):
                raise GatewayPolicyError(f"tool arguments at {path} must be an object")
            properties = schema.get("properties", {})
            if not isinstance(properties, Mapping):
                raise GatewayPolicyError(f"tool schema properties at {path} are invalid")
            required = schema.get("required", ())
            for name in required if isinstance(required, (list, tuple)) else ():
                if name not in value:
                    raise GatewayPolicyError(f"tool arguments missing required field: {name}")
            additional = schema.get("additionalProperties", True)
            if additional is False:
                unknown = set(value) - set(properties)
                if unknown:
                    raise GatewayPolicyError(
                        f"tool arguments contain unknown fields: {', '.join(sorted(map(str, unknown)))}"
                    )
            for name, child_schema in properties.items():
                if name in value and isinstance(child_schema, Mapping):
                    cls._validate_json_schema(value[name], child_schema, f"{path}.{name}")
            return
        if schema_type == "array":
            if not isinstance(value, (list, tuple)):
                raise GatewayPolicyError(f"tool arguments at {path} must be an array")
            item_schema = schema.get("items")
            if isinstance(item_schema, Mapping):
                for index, item in enumerate(value):
                    cls._validate_json_schema(item, item_schema, f"{path}[{index}]")
            return
        if schema_type == "string":
            if not isinstance(value, str):
                raise GatewayPolicyError(f"tool arguments at {path} must be a string")
        elif schema_type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise GatewayPolicyError(f"tool arguments at {path} must be an integer")
        elif schema_type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise GatewayPolicyError(f"tool arguments at {path} must be a number")
        elif schema_type == "boolean" and not isinstance(value, bool):
            raise GatewayPolicyError(f"tool arguments at {path} must be boolean")
        allowed = schema.get("enum")
        if isinstance(allowed, (list, tuple)) and value not in allowed:
            raise GatewayPolicyError(f"tool arguments at {path} contain a value outside the enum")


# ``ModelGateway`` is the public name used by application wiring; the alias is
# retained to make the policy boundary obvious when reading call sites.
ModelGateway = PolicyGateway
ThreeTierModelGateway = PolicyGateway


__all__ = [
    "GatewayDecision",
    "GatewayPolicyConfig",
    "GatewayPolicyError",
    "ModelGateway",
    "PolicyGateway",
    "ProviderTerms",
    "ThreeTierModelGateway",
    "classify_payload",
    "contains_secret_material",
    "redact_request",
    "redact_text",
]
