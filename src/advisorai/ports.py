"""Stable ports for replaceable infrastructure components.

The ports are intentionally dependency-light. Concrete adapters for gateways,
archives, and event transport can be selected by a Phase 0 bake-off without
leaking provider-specific objects into the contracts or trading boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from pathlib import PurePosixPath
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

from advisorai.contracts.core import is_forbidden_authority_action, normalize_authority_action


def _require_digest(value: str, info: object) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        field = getattr(info, "field_name", "digest")
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


class GatewayDataClass(StrEnum):
    """The maximum sensitivity a model request is allowed to carry."""

    UNCLASSIFIED = "unclassified"
    PUBLIC = "public"
    INTERNAL_SANITIZED = "internal_sanitized"
    CONFIDENTIAL = "confidential"
    SECRET_EXECUTION = "secret_execution"


class GatewayTier(StrEnum):
    """Policy route selected before a provider request is made."""

    CONTRIBUTOR = "contributor"
    PRIVATE = "private"
    BLOCKED = "blocked"


class RouteTier(StrEnum):
    """Security/quality route classes selected by policy, never by the caller."""

    CONTRIBUTOR_PUBLIC = "contributor_public"
    PRIVATE_WORKER = "private_worker"
    PRIVATE_REVIEWER = "private_reviewer"
    BLOCKED = "blocked"


class DecisionImpact(StrEnum):
    """How close a research request is to portfolio or execution authority."""

    NON_CRITICAL = "non_critical"
    RESEARCH = "research"
    PORTFOLIO_INFLUENCING = "portfolio_influencing"
    EXECUTION = "execution"


# Public contract names used by the routing policy brief.  The Gateway* names
# remain canonical for compatibility with the Phase-0 ports.
DataClassification = GatewayDataClass


class GatewayOutputKind(StrEnum):
    """Non-authoritative typed worker outputs accepted at the model boundary."""

    GENERIC = "generic"
    NEWS_EXTRACTION = "news_extraction"
    CLAIM_LIST = "claim_list"
    CODE_PATCH_PROPOSAL = "code_patch_proposal"
    RESEARCH_QUESTION = "research_question"
    COUNTERARGUMENT = "counterargument"


class GatewayInvocationMode(StrEnum):
    """The one admitted interaction shape for a model generation."""

    STRUCTURED_OUTPUT = "structured_output"
    TOOL_OPTIONAL = "tool_optional"
    TOOL_REQUIRED = "tool_required"


class ToolExecutionStatus(StrEnum):
    """Status of the separate deterministic tool-execution stage.

    A model response can only report that it *called* a reviewed tool.  It
    cannot claim that AdvisorAI executed that tool or obtained evidence from
    it; gateway-response and generation-ledger records therefore always use
    ``NOT_EXECUTED``.  A future deterministic tool-execution ledger may use
    the remaining statuses after it has actually run the tool.
    """

    NOT_EXECUTED = "not_executed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class GenerationBudget(BaseModel):
    """Caller-visible limits for one model generation.

    Routing policy and generation limits are deliberately separate objects:
    provider selection is owned by the gateway policy, while this budget only
    bounds tokens, cost, timeout, and retry attempts.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_output_tokens: int = Field(default=2048, ge=1, le=1_000_000)
    max_expected_cost_usd: float = Field(default=1.0, ge=0)
    max_billed_cost_usd: float = Field(default=1.0, ge=0)
    timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    maximum_attempts: int = Field(default=2, ge=1, le=3)
    max_input_tokens: int | None = Field(default=None, ge=1, le=10_000_000)

    @field_validator("max_expected_cost_usd", "max_billed_cost_usd")
    @classmethod
    def require_finite_budget_cost(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("generation budget cost must be finite")
        return value


class GenericOutput(RootModel[dict[str, object]]):
    """Schema for a deliberately generic, non-authoritative mapping."""


class NewsExtraction(BaseModel):
    """Typed extraction of public news or filing content."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    headline: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    entities: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    text: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    confidence: float | None = Field(default=None, ge=0, le=1)


class ClaimList(BaseModel):
    """Typed list of claims whose evidence remains separately verifiable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claims: tuple[Claim, ...] = ()


class CodePatchProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    summary: str = Field(min_length=1)
    patch: str = Field(min_length=1)
    files: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()


class ResearchQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    question: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    priority: Literal["low", "medium", "high"] = "medium"


class Counterargument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    claim: str = Field(min_length=1)
    counterargument: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    confidence: float | None = Field(default=None, ge=0, le=1)


GATEWAY_OUTPUT_SCHEMAS: dict[GatewayOutputKind, type[BaseModel]] = {
    GatewayOutputKind.GENERIC: GenericOutput,
    GatewayOutputKind.NEWS_EXTRACTION: NewsExtraction,
    GatewayOutputKind.CLAIM_LIST: ClaimList,
    GatewayOutputKind.CODE_PATCH_PROPOSAL: CodePatchProposal,
    GatewayOutputKind.RESEARCH_QUESTION: ResearchQuestion,
    GatewayOutputKind.COUNTERARGUMENT: Counterargument,
}


def validate_gateway_output(
    output_kind: GatewayOutputKind, payload: Mapping[str, object]
) -> Mapping[str, object]:
    """Validate and normalize one concrete output schema."""

    schema = GATEWAY_OUTPUT_SCHEMAS[output_kind]
    model = schema.model_validate(payload)
    normalized = model.model_dump(mode="json", round_trip=True)
    if not isinstance(normalized, Mapping):  # pragma: no cover - all schemas are mappings
        raise ValueError("gateway output schema must serialize to an object")
    return dict(normalized)


class GatewayMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    role: str = Field(min_length=1)
    content: str = Field(min_length=1)


class GatewayTool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1)
    input_schema_version: str = Field(min_length=1)
    output_schema_version: str = Field(min_length=1)
    input_schema: Mapping[str, object] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    )

    @field_validator("name")
    @classmethod
    def forbid_trading_tools(cls, value: str) -> str:
        normalized = normalize_authority_action(value)
        if not normalized:
            raise ValueError("gateway tool name cannot be blank")
        if is_forbidden_authority_action(normalized):
            raise ValueError("model gateways cannot expose trading authority")
        return value.strip()

    @field_validator("input_schema")
    @classmethod
    def validate_input_schema(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        if value.get("type") != "object":
            raise ValueError("gateway tool input schemas must describe an object")
        properties = value.get("properties", {})
        if not isinstance(properties, Mapping):
            raise ValueError("gateway tool input schema properties must be an object")
        required = value.get("required", ())
        if not isinstance(required, (list, tuple)) or any(
            not isinstance(item, str) or not item.strip() for item in required
        ):
            raise ValueError("gateway tool input schema required must be a list of names")
        if any(item not in properties for item in required):
            raise ValueError("gateway tool input schema required names must be declared properties")
        forbidden_tokens = (
            "account",
            "balance",
            "broker",
            "credential",
            "order",
            "position",
            "secret",
        )

        def visit_schema(schema: Mapping[str, object]) -> None:
            nested_properties = schema.get("properties", {})
            if nested_properties is not None and not isinstance(nested_properties, Mapping):
                raise ValueError("gateway tool input schema properties must be an object")
            if isinstance(nested_properties, Mapping):
                nested_required = schema.get("required", ())
                if not isinstance(nested_required, (list, tuple)) or any(
                    not isinstance(item, str) or not item.strip() for item in nested_required
                ):
                    raise ValueError("gateway tool input schema required must be a list of names")
                if any(item not in nested_properties for item in nested_required):
                    raise ValueError(
                        "gateway tool input schema required names must be declared properties"
                    )
                for name, child in nested_properties.items():
                    if not isinstance(name, str) or not name.strip():
                        raise ValueError("gateway tool input schema property names must be text")
                    normalized = normalize_authority_action(name)
                    if is_forbidden_authority_action(normalized) or any(
                        token in normalized for token in forbidden_tokens
                    ):
                        raise ValueError(
                            "gateway tool input schema cannot expose trading authority"
                        )
                    if isinstance(child, Mapping):
                        visit_schema(child)
            items = schema.get("items")
            if isinstance(items, Mapping):
                visit_schema(items)

        visit_schema(value)
        return dict(value)


class GatewayRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    gateway: str = Field(min_length=1)
    fallback_chain: tuple[str, ...] = ()
    schema_mode: str = Field(default="typed_json", min_length=1)
    retention_policy: str = Field(default="unspecified", min_length=1)
    training_policy: str = Field(default="unspecified", min_length=1)
    terms_verified: bool = False
    terms_reference: str | None = None
    endpoint_variant: str | None = None

    @field_validator("fallback_chain")
    @classmethod
    def require_unique_fallbacks(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("gateway fallback identities cannot be blank")
        normalized = tuple(item.strip() for item in value)
        if len(normalized) != len({item.lower() for item in normalized}):
            raise ValueError("gateway fallback identities must be unique")
        return normalized

    @field_validator(
        "provider",
        "model",
        "gateway",
        "schema_mode",
        "retention_policy",
        "training_policy",
    )
    @classmethod
    def require_route_tokens(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("gateway route identity fields cannot be blank")
        return value.strip()

    @field_validator("terms_reference", "endpoint_variant")
    @classmethod
    def normalize_route_metadata(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("gateway route metadata cannot be blank")
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def prohibit_primary_fallback(self) -> GatewayRoute:
        if self.gateway.lower() in {item.lower() for item in self.fallback_chain}:
            raise ValueError("gateway fallback chain cannot repeat the primary gateway")
        return self


class GatewayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    request_id: UUID = Field(default_factory=uuid4)
    route: GatewayRoute
    messages: tuple[GatewayMessage, ...]
    tools: tuple[GatewayTool, ...] = ()
    invocation_mode: GatewayInvocationMode = GatewayInvocationMode.STRUCTURED_OUTPUT
    prompt_version: str
    tool_version: str | None = None
    privacy_class: str = "non_secret"
    data_class: GatewayDataClass = GatewayDataClass.UNCLASSIFIED
    task_kind: str = Field(default="research", min_length=1)
    portfolio_influence: bool = False
    confidence: float | None = Field(default=None, ge=0, le=1)
    conflicting_evidence: bool = False
    decision_impact: DecisionImpact = DecisionImpact.NON_CRITICAL
    output_kind: GatewayOutputKind = GatewayOutputKind.GENERIC
    redaction_policy_version: str = Field(default="redaction-v1", min_length=1)
    redaction_policy_hash: str | None = None
    route_policy_hash: str | None = None
    provider_options: Mapping[str, object] = Field(default_factory=dict)
    generation_budget: GenerationBudget = Field(default_factory=GenerationBudget)
    # This flag is policy-owned: RouteProfile admission may opt a reviewed
    # endpoint into the otherwise unsafe combined tool + structured-output
    # protocol.  A remote adapter is never eligible for the legacy path, so a
    # caller cannot use this field to bypass a provider admission.
    response_format_with_tools_admitted: bool = False
    # These capability observations are populated only by an admitted
    # RouteProfile.  Direct/local test adapters leave enforcement disabled.
    admission_capabilities_enforced: bool = False
    admission_supports_tools: bool | None = None
    admission_supports_tool_choice_required: bool | None = None
    admission_supports_structured_output: bool | None = None
    requested_provider_selector: str | None = None
    requested_model: str | None = None
    requested_gateway: str | None = None
    requested_endpoint_selector: str | None = None
    evidence_ids: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("created_at")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("gateway request timestamp must include a timezone")
        return value.astimezone(UTC)

    @field_validator("privacy_class")
    @classmethod
    def normalize_privacy_class(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("gateway privacy class cannot be blank")
        return value.strip().lower()

    @field_validator("task_kind", "redaction_policy_version")
    @classmethod
    def normalize_policy_tokens(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("gateway policy tokens cannot be blank")
        return value.strip()

    @field_validator("evidence_ids")
    @classmethod
    def normalize_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("gateway evidence IDs cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("gateway evidence IDs must be unique")
        return normalized

    @field_validator("redaction_policy_hash", "route_policy_hash")
    @classmethod
    def require_optional_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("gateway policy hashes must be lowercase SHA-256 digests")
        return normalized

    @model_validator(mode="before")
    @classmethod
    def require_explicit_tool_invocation_mode(cls, value: object) -> object:
        """Reject tool-bearing requests whose authority shape is unspecified."""

        if isinstance(value, Mapping) and "invocation_mode" not in value and value.get("tools"):
            raise ValueError("gateway requests with tools require an explicit invocation_mode")
        return value

    @classmethod
    def from_legacy_payload(
        cls,
        payload: Mapping[str, object],
        *,
        tool_invocation_mode: GatewayInvocationMode | None = None,
    ) -> GatewayRequest:
        """Migrate a pre-invocation-mode payload through an explicit choice.

        Normal ``GatewayRequest`` construction intentionally refuses an
        omitted mode when tools are present.  A migration caller must state
        whether its legacy tool use was optional or required; this helper is
        the only compatibility path that can make that conversion.
        """

        values = dict(payload)
        if "invocation_mode" in values:
            return cls.model_validate(values)
        if values.get("tools"):
            if tool_invocation_mode not in {
                GatewayInvocationMode.TOOL_OPTIONAL,
                GatewayInvocationMode.TOOL_REQUIRED,
            }:
                raise ValueError(
                    "legacy tool-bearing payloads require an explicit optional or required tool mode"
                )
            values["invocation_mode"] = tool_invocation_mode
        else:
            values["invocation_mode"] = GatewayInvocationMode.STRUCTURED_OUTPUT
        return cls.model_validate(values)

    @model_validator(mode="after")
    def require_typed_request(self) -> GatewayRequest:
        if not self.messages:
            raise ValueError("gateway requests require at least one message")
        if not self.prompt_version.strip():
            raise ValueError("gateway requests require a prompt version")
        if self.tool_version is not None and not self.tool_version.strip():
            raise ValueError("gateway tool version cannot be blank")
        if self.invocation_mode is GatewayInvocationMode.STRUCTURED_OUTPUT and self.tools:
            raise ValueError("structured-output gateway requests cannot expose tools")
        if (
            self.invocation_mode
            in {
                GatewayInvocationMode.TOOL_OPTIONAL,
                GatewayInvocationMode.TOOL_REQUIRED,
            }
            and not self.tools
        ):
            raise ValueError("tool invocation modes require at least one reviewed tool")
        forbidden_provider_options = {
            "model",
            "messages",
            "tools",
            "api_key",
            "authorization",
            "endpoint_variants",
            "tool_choice",
        }

        def has_forbidden_provider_option(value: object) -> bool:
            if isinstance(value, Mapping):
                for key, child in value.items():
                    if str(key).strip().lower() in forbidden_provider_options:
                        return True
                    if has_forbidden_provider_option(child):
                        return True
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                return any(has_forbidden_provider_option(child) for child in value)
            return False

        if has_forbidden_provider_option(self.provider_options):
            raise ValueError(
                "gateway provider options cannot override model content, credentials, or endpoint routing"
            )

        def has_credential_key(value: object) -> bool:
            if isinstance(value, Mapping):
                for key, child in value.items():
                    normalized = str(key).strip().lower()
                    if normalized in {"max_tokens", "max_completion_tokens"}:
                        if has_credential_key(child):
                            return True
                        continue
                    if any(
                        token in normalized
                        for token in (
                            "api_key",
                            "authorization",
                            "credential",
                            "password",
                            "secret",
                            "token",
                        )
                    ):
                        return True
                    if has_credential_key(child):
                        return True
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                return any(has_credential_key(child) for child in value)
            return False

        if has_credential_key(self.provider_options):
            raise ValueError("gateway provider options cannot contain credentials")
        if self.requested_provider_selector is not None and (
            self.requested_provider_selector != self.route.provider
        ):
            raise ValueError("requested provider selector must match the route")
        if self.requested_model is not None and self.requested_model != self.route.model:
            raise ValueError("requested model must match the route")
        if self.requested_gateway is not None and self.requested_gateway != self.route.gateway:
            raise ValueError("requested gateway must match the route")
        if self.requested_endpoint_selector is not None and (
            self.requested_endpoint_selector != self.route.endpoint_variant
        ):
            raise ValueError("requested endpoint selector must match the route")
        return self

    def content_hash(self) -> str:
        payload = self.model_dump_json(indent=None, exclude={"request_id", "created_at"})
        return sha256(payload.encode("utf-8")).hexdigest()

    def prompt_hash(self) -> str:
        """Hash prompt/tool instructions without route or request identity."""

        payload = self.model_dump_json(
            indent=None,
            include={"messages", "prompt_version", "tool_version", "task_kind"},
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    def evidence_hash(self) -> str | None:
        if not self.evidence_ids:
            return None
        payload = "\n".join(self.evidence_ids).encode("utf-8")
        return sha256(payload).hexdigest()

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> GatewayRequest:
        """Copy through validation so immutable Pydantic updates stay fail-closed.

        ``BaseModel.model_copy`` deliberately skips validation. That is unsafe
        for this boundary because adding a tool to a structured-output request
        could otherwise bypass the explicit invocation-mode requirement.
        """

        values = self.model_dump(mode="python", round_trip=True)
        if update:
            values.update(update)
        if deep:
            values = deepcopy(values)
        copied = type(self).model_validate(values)
        # ``model_validate`` sees every dumped default as supplied. Preserve
        # Pydantic's field-set semantics so policy can still distinguish an
        # omitted generation budget from an explicitly supplied one.
        object.__setattr__(
            copied,
            "__pydantic_fields_set__",
            set(self.model_fields_set) | set((update or {}).keys()),
        )
        return copied


class GatewayResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    request_id: UUID
    route: GatewayRoute
    content: str = ""
    typed_payload: Mapping[str, object] | None = None
    tool_calls: tuple[Mapping[str, object], ...] = ()
    invocation_mode: GatewayInvocationMode | None = None
    # ``tool_used`` is the deprecated compatibility spelling.  It means only
    # that the provider returned a tool call; it never means the tool ran.
    tool_used: bool | None = None
    tool_called: bool | None = None
    tool_execution_status: ToolExecutionStatus = ToolExecutionStatus.NOT_EXECUTED
    latency_ms: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    provider_request_id: str | None = None
    tier: GatewayTier | None = None
    route_tier: RouteTier | None = None
    data_class: GatewayDataClass | None = None
    decision_impact: DecisionImpact | None = None
    output_kind: GatewayOutputKind = GatewayOutputKind.GENERIC
    policy_version: str | None = None
    redaction_policy_version: str | None = None
    escalation_reason: str | None = None
    retention_policy: str | None = None
    training_policy: str | None = None
    terms_verified: bool | None = None
    terms_reference: str | None = None
    endpoint_variant: str | None = None
    actual_endpoint_variant: str | None = None
    requested_route: GatewayRoute | None = None
    actual_provider: str | None = None
    actual_model: str | None = None
    actual_gateway: str | None = None
    requested_provider_selector: str | None = None
    requested_gateway: str | None = None
    observed_provider_name: str | None = None
    requested_model: str | None = None
    top_level_response_model: str | None = None
    resolved_model: str | None = None
    resolved_endpoint_model: str | None = None
    requested_endpoint_selector: str | None = None
    endpoint_selector_proof: str | None = None
    endpoint_selected: bool | None = None
    routing_strategy: str | None = None
    routing_attempt: int | None = Field(default=None, ge=1)
    is_byok: bool | None = None
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)
    request_price_usd: float | None = Field(default=None, ge=0)
    expected_cost_usd: float | None = Field(default=None, ge=0)
    billed_cost_usd: float | None = Field(default=None, ge=0)
    cost_difference_usd: float | None = None
    cost_metadata: Mapping[str, object] = Field(default_factory=dict)
    routing_metadata: Mapping[str, object] = Field(default_factory=dict)
    failure_metadata: Mapping[str, object] = Field(default_factory=dict)
    attempt_metadata: tuple[Mapping[str, object], ...] = ()
    prompt_hash: str | None = None
    evidence_hash: str | None = None
    redaction_policy_hash: str | None = None
    route_policy_hash: str | None = None
    authoritative: bool = False

    @field_validator("estimated_cost_usd")
    @classmethod
    def require_finite_cost(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("gateway estimated cost must be finite")
        return value

    @model_validator(mode="after")
    def forbid_trading_tool_calls(self) -> GatewayResponse:
        if not self.content.strip() and not self.tool_calls and self.typed_payload is None:
            raise ValueError("gateway response requires text, typed payload, or tool calls")
        if self.authoritative:
            raise ValueError("model gateway responses cannot be authoritative")
        tool_called = bool(self.tool_calls)
        if self.tool_used is not None and self.tool_used != tool_called:
            raise ValueError("gateway tool_used must agree with returned tool calls")
        if self.tool_called is not None and self.tool_called != tool_called:
            raise ValueError("gateway tool_called must agree with returned tool calls")
        if self.tool_execution_status is not ToolExecutionStatus.NOT_EXECUTED:
            raise ValueError("model gateway responses cannot claim tool execution")
        for call in self.tool_calls:
            if not isinstance(call, Mapping):
                raise ValueError("gateway tool calls must be mapping objects")
            name = call.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("gateway tool calls require a non-blank name")
            normalized = normalize_authority_action(name)
            if is_forbidden_authority_action(normalized):
                raise ValueError("model gateways cannot return trading authority tool calls")
            if "arguments" in call and not isinstance(call["arguments"], (Mapping, str)):
                raise ValueError("gateway tool-call arguments must be a mapping or JSON string")
        if self.provider_request_id is not None and not self.provider_request_id.strip():
            raise ValueError("provider request IDs cannot be blank")
        for field_name in (
            "policy_version",
            "redaction_policy_version",
            "escalation_reason",
            "retention_policy",
            "training_policy",
            "terms_reference",
            "endpoint_variant",
            "actual_endpoint_variant",
            "actual_provider",
            "actual_model",
            "actual_gateway",
            "requested_provider_selector",
            "requested_gateway",
            "observed_provider_name",
            "requested_model",
            "top_level_response_model",
            "resolved_model",
            "resolved_endpoint_model",
            "requested_endpoint_selector",
            "endpoint_selector_proof",
            "routing_strategy",
        ):
            value = getattr(self, field_name)
            if value is not None and not value.strip():
                raise ValueError(f"gateway {field_name} cannot be blank")
        for field_name in (
            "prompt_hash",
            "evidence_hash",
            "redaction_policy_hash",
            "route_policy_hash",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            ):
                raise ValueError(f"gateway {field_name} must be a lowercase SHA-256 digest")
        for field_name in ("expected_cost_usd", "billed_cost_usd", "cost_difference_usd"):
            value = getattr(self, field_name)
            if value is not None and not isfinite(value):
                raise ValueError(f"gateway {field_name} must be finite")
        if self.resolved_model is not None and self.resolved_endpoint_model is not None:
            if self.resolved_model != self.resolved_endpoint_model:
                raise ValueError("gateway resolved model fields must agree")
        if self.endpoint_selected is False and any(
            value is not None
            for value in (
                self.actual_provider,
                self.actual_model,
                self.actual_gateway,
                self.actual_endpoint_variant,
                self.observed_provider_name,
                self.top_level_response_model,
                self.resolved_model,
                self.resolved_endpoint_model,
            )
        ):
            raise ValueError("unselected gateway endpoints cannot be actual identities")
        return self


@runtime_checkable
class ModelGatewayPort(Protocol):
    """A replaceable API model gateway with route identity in every response."""

    name: str

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        """Complete one typed request or raise a provider-specific failure."""


class ArchiveObject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    content_hash: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)
    encrypted: bool

    @field_validator("key")
    @classmethod
    def require_safe_key(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            not value.strip()
            or "\\" in value
            or path.is_absolute()
            or ".." in path.parts
            or any(not part or part == "." for part in path.parts)
        ):
            raise ValueError("archive object key must be a safe relative path")
        return value.strip()

    _content_hash = field_validator("content_hash")(_require_digest)


@runtime_checkable
class ArchiveBackend(Protocol):
    """Cold archive port; local data/ledgers remain authoritative."""

    name: str

    def put(self, key: str, payload: bytes) -> ArchiveObject: ...

    def get(self, key: str) -> bytes: ...

    def verify(self, obj: ArchiveObject) -> bool: ...


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID = Field(default_factory=uuid4)
    event_type: str = Field(min_length=1)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    artifact_ids: tuple[UUID, ...] = ()
    payload_ref: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event timestamp must include a timezone")
        return value.astimezone(UTC)

    @field_validator("event_type")
    @classmethod
    def require_event_type(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("event_type cannot be blank")
        return value.strip()

    @field_validator("payload_ref")
    @classmethod
    def normalize_payload_ref(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("payload_ref cannot be blank")
        return value

    @model_validator(mode="after")
    def require_unique_artifacts(self) -> EventEnvelope:
        if len(self.artifact_ids) != len(set(self.artifact_ids)):
            raise ValueError("event artifact IDs must be unique")
        return self


@runtime_checkable
class EventBusPort(Protocol):
    """Durable hand-off port; SQLite outbox is the Phase 1 default."""

    def publish(self, envelope: EventEnvelope) -> None: ...

    def replay(self, event_type: str | None = None) -> Sequence[EventEnvelope]: ...
