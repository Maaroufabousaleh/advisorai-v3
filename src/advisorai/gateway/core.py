"""ModelGatewayPort implementations for Phase 0 tests and recovery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter_ns
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers
from advisorai.ports import (
    DecisionImpact,
    GatewayDataClass,
    GatewayOutputKind,
    GatewayRequest,
    GatewayResponse,
    GatewayRoute,
    GatewayTier,
    ModelGatewayPort,
    RouteTier,
)


class GatewayFailure(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GatewayAttempt:
    adapter: str
    route: GatewayRoute
    succeeded: bool
    latency_ms: int
    error: str | None = None
    profile_id: str | None = None
    attempt_number: int = 0


class GatewayCallRecord(BaseModel):
    """Credential-free durable metadata for one pinned gateway attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: UUID
    request_hash: str = Field(min_length=64, max_length=64)
    adapter: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    gateway: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    tool_version: str | None = None
    succeeded: bool
    latency_ms: int = Field(ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0, ge=0)
    provider_request_id: str | None = None
    error_class: str | None = None
    recorded_at: datetime
    prompt_hash: str | None = None
    evidence_hash: str | None = None
    tier: GatewayTier | None = None
    data_class: GatewayDataClass | None = None
    output_kind: GatewayOutputKind = GatewayOutputKind.GENERIC
    policy_version: str | None = None
    redaction_policy_version: str | None = None
    escalation_reason: str | None = None
    retention_policy: str | None = None
    training_policy: str | None = None
    terms_verified: bool | None = None
    terms_reference: str | None = None
    requested_provider: str | None = None
    requested_model: str | None = None
    requested_gateway: str | None = None
    requested_endpoint_variant: str | None = None
    actual_provider: str | None = None
    actual_model: str | None = None
    actual_gateway: str | None = None
    actual_endpoint_variant: str | None = None
    profile_id: str | None = None
    attempt_number: int = Field(default=0, ge=0)
    route_tier: RouteTier | None = None
    decision_impact: DecisionImpact | None = None
    redaction_policy_hash: str | None = None
    route_policy_hash: str | None = None
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)
    request_price_usd: float | None = Field(default=None, ge=0)
    billed_cost_usd: float | None = Field(default=None, ge=0)
    cost_metadata: dict[str, object] = Field(default_factory=dict)
    routing_metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("request_hash")
    @classmethod
    def require_digest(cls, value: str) -> str:
        normalized = value.lower().strip()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("gateway request_hash must be a lowercase SHA-256 digest")
        return normalized

    @field_validator("prompt_hash", "evidence_hash")
    @classmethod
    def require_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.lower().strip()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("gateway optional hashes must be lowercase SHA-256 digests")
        return normalized

    @field_validator("adapter", "provider", "model", "gateway", "prompt_version")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("gateway call identity fields cannot be blank")
        return value.strip()

    @field_validator(
        "tool_version",
        "provider_request_id",
        "error_class",
        "requested_provider",
        "requested_model",
        "requested_gateway",
        "requested_endpoint_variant",
        "actual_provider",
        "actual_model",
        "actual_gateway",
        "actual_endpoint_variant",
        "policy_version",
        "redaction_policy_version",
        "escalation_reason",
        "retention_policy",
        "training_policy",
        "terms_reference",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("gateway optional metadata cannot be blank")
        return value.strip() if value is not None else None

    @field_validator("estimated_cost_usd", "billed_cost_usd")
    @classmethod
    def require_finite_cost(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("gateway call cost must be finite")
        return value

    @field_validator("recorded_at")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("gateway call timestamp must include a timezone")
        return value.astimezone(UTC)


class GatewayRecorder:
    def __init__(self, ledgers: SqliteLedgers | None = None) -> None:
        self.attempts: list[GatewayAttempt] = []
        self.calls: list[GatewayCallRecord] = []
        self.ledgers = ledgers

    def record(self, attempt: GatewayAttempt) -> None:
        self.attempts.append(attempt)

    def record_call(
        self,
        request: GatewayRequest,
        attempt: GatewayAttempt,
        response: GatewayResponse | None = None,
        *,
        route_tier: RouteTier | None = None,
        decision_impact: DecisionImpact | None = None,
        redaction_policy_hash: str | None = None,
        route_policy_hash: str | None = None,
    ) -> GatewayCallRecord:
        """Record route/cost/latency metadata without prompt or credential text."""

        route = response.route if response is not None else attempt.route
        requested_route = (
            response.requested_route if response is not None and response.requested_route else request.route
        )
        record = GatewayCallRecord(
            request_id=request.request_id,
            request_hash=request.content_hash(),
            adapter=attempt.adapter,
            provider=route.provider,
            model=route.model,
            gateway=route.gateway,
            prompt_version=request.prompt_version,
            tool_version=request.tool_version,
            succeeded=attempt.succeeded,
            latency_ms=attempt.latency_ms,
            input_tokens=response.input_tokens if response is not None else 0,
            output_tokens=response.output_tokens if response is not None else 0,
            estimated_cost_usd=response.estimated_cost_usd if response is not None else 0,
            provider_request_id=response.provider_request_id if response is not None else None,
            error_class=(attempt.error.split(":", 1)[0] if attempt.error else None),
            prompt_hash=request.prompt_hash(),
            evidence_hash=request.evidence_hash(),
            tier=response.tier if response is not None else None,
            data_class=(response.data_class if response is not None else request.data_class),
            output_kind=(response.output_kind if response is not None else request.output_kind),
            policy_version=response.policy_version if response is not None else None,
            redaction_policy_version=(
                response.redaction_policy_version
                if response is not None
                else request.redaction_policy_version
            ),
            escalation_reason=response.escalation_reason if response is not None else None,
            retention_policy=(
                response.retention_policy if response is not None else route.retention_policy
            ),
            training_policy=(
                response.training_policy if response is not None else route.training_policy
            ),
            terms_verified=(
                response.terms_verified if response is not None else route.terms_verified
            ),
            terms_reference=(
                response.terms_reference if response is not None else route.terms_reference
            ),
            requested_provider=requested_route.provider,
            requested_model=requested_route.model,
            requested_gateway=requested_route.gateway,
            requested_endpoint_variant=requested_route.endpoint_variant,
            actual_provider=(
                response.actual_provider
                if response is not None
                else None
            ),
            actual_model=(
                response.actual_model if response is not None else None
            ),
            actual_gateway=(
                response.actual_gateway
                if response is not None
                else None
            ),
            actual_endpoint_variant=(
                response.actual_endpoint_variant
                if response is not None
                else None
            ),
            profile_id=attempt.profile_id,
            attempt_number=attempt.attempt_number,
            route_tier=response.route_tier if response is not None else route_tier,
            decision_impact=(
                response.decision_impact
                if response is not None
                else decision_impact or request.decision_impact
            ),
            redaction_policy_hash=(
                response.redaction_policy_hash
                if response is not None and response.redaction_policy_hash
                else redaction_policy_hash or request.redaction_policy_hash
            ),
            route_policy_hash=(
                response.route_policy_hash
                if response is not None and response.route_policy_hash
                else route_policy_hash or request.route_policy_hash
            ),
            input_price_per_million=(
                response.input_price_per_million if response is not None else None
            ),
            output_price_per_million=(
                response.output_price_per_million if response is not None else None
            ),
            request_price_usd=response.request_price_usd if response is not None else None,
            billed_cost_usd=response.billed_cost_usd if response is not None else None,
            cost_metadata=dict(response.cost_metadata) if response is not None else {},
            routing_metadata=dict(response.routing_metadata) if response is not None else {},
            # The request creation time is stable across a retry/restart. It
            # keeps the ledger idempotent while latency still captures the
            # actual attempt duration.
            recorded_at=request.created_at,
        )
        self.calls.append(record)
        if self.ledgers is not None:
            self.ledgers.append(
                LedgerEvent(
                    namespace=LedgerNamespace.MODEL,
                    event_type="gateway_call_recorded",
                    idempotency_key=(
                        f"gateway-call:{request.request_id}:"
                        f"{attempt.profile_id or attempt.adapter}:{attempt.attempt_number}:"
                        f"{route.gateway}:{attempt.succeeded}"
                    ),
                    occurred_at=request.created_at,
                    payload={"call": record.model_dump(mode="json", round_trip=True)},
                )
            )
        return record


class LocalDeterministicGateway:
    """Typed contract/recovery gateway; never represents a production LLM."""

    name = "direct_recovery"

    def __init__(
        self, responder: Callable[[GatewayRequest], dict[str, object]] | None = None
    ) -> None:
        self.responder = responder or (
            lambda request: {"ok": True, "request_hash": request.content_hash()}
        )

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        if request.privacy_class.lower() in {"secret", "credential"}:
            raise GatewayFailure("recovery gateway refuses secret or credential-class requests")
        started = perf_counter_ns()
        payload = self.responder(request)
        return GatewayResponse(
            request_id=request.request_id,
            route=request.route,
            requested_route=request.route,
            content="typed local recovery response",
            typed_payload=payload,
            latency_ms=max(0, (perf_counter_ns() - started) // 1_000_000),
            input_tokens=0,
            output_tokens=0,
            estimated_cost_usd=0,
            provider_request_id=f"local-{request.request_id}",
            actual_provider=request.route.provider,
            actual_model=request.route.model,
            actual_gateway=request.route.gateway,
            output_kind=request.output_kind,
        )


class GatewayChain:
    """Pinned ordered routes; fallback is explicit and recorded, never opaque."""

    def __init__(
        self, adapters: tuple[ModelGatewayPort, ...], recorder: GatewayRecorder | None = None
    ) -> None:
        if not adapters:
            raise ValueError("gateway chain requires at least one adapter")
        self.adapters = adapters
        self.recorder = recorder or GatewayRecorder()

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        failures: list[str] = []
        permitted_gateways = {request.route.gateway, *request.route.fallback_chain}
        for attempt_number, adapter in enumerate(self.adapters):
            started = perf_counter_ns()
            attempt_request = request
            adapter_route = getattr(adapter, "route", None)
            if adapter_route is not None:
                if adapter_route.gateway not in permitted_gateways:
                    error = f"{adapter.name}:route_not_in_fallback_chain:{adapter_route.gateway}"
                    failures.append(error)
                    attempt = GatewayAttempt(
                        adapter=adapter.name,
                        route=adapter_route,
                        succeeded=False,
                        latency_ms=0,
                        error="RouteNotPermitted: route not in fallback chain",
                        profile_id=adapter.name,
                        attempt_number=attempt_number,
                    )
                    self.recorder.record(attempt)
                    self.recorder.record_call(request, attempt)
                    continue
                # Each adapter receives the same typed content, but its pinned
                # route identity is explicit.  This is what makes fallback
                # ordering auditable instead of relying on opaque gateway
                # auto-routing.
                attempt_request = request.model_copy(update={"route": adapter_route})
            try:
                response = adapter.complete(attempt_request)
                if response.request_id != request.request_id:
                    raise GatewayFailure(
                        f"{adapter.name} returned a response for a different request"
                    )
                if response.route != attempt_request.route:
                    raise GatewayFailure(
                        f"{adapter.name} returned a route identity different from its pinned request"
                    )
                if response.route.gateway not in permitted_gateways:
                    raise GatewayFailure(
                        f"{adapter.name} returned an unpinned route {response.route.gateway!r}"
                    )
                elapsed = max(0, (perf_counter_ns() - started) // 1_000_000)
                attempt = GatewayAttempt(
                    adapter=adapter.name,
                    route=response.route,
                    succeeded=True,
                    latency_ms=elapsed,
                    profile_id=adapter.name,
                    attempt_number=attempt_number,
                )
                self.recorder.record(attempt)
                self.recorder.record_call(attempt_request, attempt, response)
                return response
            except Exception as exc:
                elapsed = max(0, (perf_counter_ns() - started) // 1_000_000)
                failures.append(f"{adapter.name}:{type(exc).__name__}:{exc}")
                attempt = GatewayAttempt(
                    adapter=adapter.name,
                    route=adapter_route or request.route,
                    succeeded=False,
                    latency_ms=elapsed,
                    error=f"{type(exc).__name__}: provider failure",
                    profile_id=adapter.name,
                    attempt_number=attempt_number,
                )
                self.recorder.record(attempt)
                self.recorder.record_call(attempt_request, attempt)
        raise GatewayFailure("all pinned gateway routes failed: " + " | ".join(failures))
