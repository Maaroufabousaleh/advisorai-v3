"""Typed evidence and budget contracts for the Phase-0 remote bake-off.

This module deliberately does not construct credentials or make network calls.
The acquisition/probe script uses these contracts around the already-admitted
``PolicyGateway``.  The contracts are also useful for offline review of a
sanitized route registry and therefore contain no prompts, provider messages,
user identifiers, or secret values.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.ports import GatewayInvocationMode


class RemoteRouteRole(StrEnum):
    CONTRIBUTOR_PUBLIC = "contributor_public"
    PRIVATE_WORKER = "private_worker"
    PRIVATE_REVIEWER = "private_reviewer"
    BLOCKED_EXECUTION = "blocked_execution"


class RemoteProbeStatus(StrEnum):
    MEASURED = "measured"
    FAILED = "failed"
    BLOCKED = "blocked"
    QUARANTINED = "quarantined"
    PENDING = "pending"


# Re-export the existing gateway invocation contract instead of creating a
# second, remote-only mode enum.
RemoteInvocation = GatewayInvocationMode


class RemoteBudget(BaseModel):
    """A spend cap that cannot exceed either the provider balance fraction or $0.25."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    absolute_cap_usd: float = Field(default=0.25, ge=0)
    remaining_allowed_usd: float = Field(ge=0)
    fraction_of_remaining: float = Field(default=0.25, gt=0, le=1)
    spent_usd: float = Field(default=0, ge=0)

    @field_validator("absolute_cap_usd", "remaining_allowed_usd", "spent_usd")
    @classmethod
    def finite_money(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("remote budget values must be finite")
        return float(value)

    @property
    def cap_usd(self) -> float:
        return min(self.absolute_cap_usd, self.remaining_allowed_usd * self.fraction_of_remaining)

    @property
    def remaining_cap_usd(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)

    def authorize(self, expected_cost_usd: float) -> RemoteBudget:
        if not isfinite(expected_cost_usd) or expected_cost_usd < 0:
            raise ValueError("expected remote cost must be finite and non-negative")
        if expected_cost_usd > self.remaining_cap_usd:
            raise ValueError("remote probe exceeds the Phase-0 spend cap")
        return self.model_copy(update={"spent_usd": self.spent_usd + expected_cost_usd})


class RemoteRouteCandidate(BaseModel):
    """A live, sanitized route candidate; model/provider identity is explicit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: str = Field(min_length=1)
    role: RemoteRouteRole
    gateway: str = Field(min_length=1)
    provider_selector: str | None = None
    requested_model: str = Field(min_length=1)
    requested_endpoint_selector: str | None = None
    observed_provider_names: tuple[str, ...] = ()
    allowed_top_level_models: tuple[str, ...] = ()
    allowed_resolved_models: tuple[str, ...] = ()
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)
    request_price: float | None = Field(default=None, ge=0)
    zdr: bool = False
    data_collection: str = "deny"
    allow_fallbacks: bool = False
    supports_tools: bool = False
    supports_tool_choice_required: bool = False
    supports_structured_output: bool = False
    allow_response_format_with_tools: bool = False
    inventory_reference: str = Field(min_length=1)
    inventory_artifact_hash: str = Field(min_length=64, max_length=64)
    terms_reference: str = Field(min_length=1)
    reproducible: bool = True
    notes: tuple[str, ...] = ()

    @field_validator("inventory_artifact_hash")
    @classmethod
    def digest(cls, value: str) -> str:
        value = value.strip().lower()
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("inventory_artifact_hash must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def validate_route(self) -> RemoteRouteCandidate:
        if self.role is RemoteRouteRole.BLOCKED_EXECUTION:
            raise ValueError(
                "execution is represented by a blocked roster entry, not a provider candidate"
            )
        if self.role is RemoteRouteRole.CONTRIBUTOR_PUBLIC and self.provider_selector is None:
            # A dynamic public router intentionally has no exact provider selector.
            if self.reproducible:
                raise ValueError("dynamic public routes cannot claim reproducibility")
        if self.reproducible:
            for value, field_name in (
                (self.provider_selector, "provider_selector"),
                (self.requested_endpoint_selector, "requested_endpoint_selector"),
            ):
                if not value:
                    raise ValueError(f"reproducible candidates require {field_name}")
            if not self.allowed_top_level_models or not self.allowed_resolved_models:
                raise ValueError("reproducible candidates require admitted model identities")
            if not self.observed_provider_names:
                raise ValueError("reproducible candidates require observed provider names")
        return self


class RemoteProbeResult(BaseModel):
    """Sanitized result of one gateway probe."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: str = Field(min_length=1)
    role: RemoteRouteRole
    status: RemoteProbeStatus
    invocation: RemoteInvocation | None = None
    latency_ms: int = Field(default=0, ge=0)
    billed_cost_usd: float | None = Field(default=None, ge=0)
    expected_cost_usd: float | None = Field(default=None, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    requested_provider_selector: str | None = None
    requested_model: str | None = None
    requested_gateway: str | None = None
    requested_endpoint_selector: str | None = None
    observed_provider_name: str | None = None
    top_level_response_model: str | None = None
    resolved_endpoint_model: str | None = None
    actual_gateway: str | None = None
    endpoint_selector_proof: str | None = None
    endpoint_selected: bool | None = None
    routing_strategy: str | None = None
    routing_attempt: int | None = Field(default=None, ge=1)
    is_byok: bool | None = None
    tool_called: bool | None = None
    tool_execution_status: str = "not_executed"
    failure_class: str | None = None
    failure_metadata: Mapping[str, object] = Field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @field_validator("billed_cost_usd", "expected_cost_usd")
    @classmethod
    def finite_cost(cls, value: float | None) -> float | None:
        if value is not None and not isfinite(value):
            raise ValueError("probe costs must be finite")
        return value

    @field_validator("failure_metadata", mode="before")
    @classmethod
    def sanitize_failure_metadata(cls, value: object) -> Mapping[str, object]:
        safe = sanitize_metadata(value)
        return safe if isinstance(safe, Mapping) else {}

    @model_validator(mode="after")
    def no_identity_on_non_success(self) -> RemoteProbeResult:
        if self.status is not RemoteProbeStatus.MEASURED and any(
            value is not None
            for value in (
                self.observed_provider_name,
                self.top_level_response_model,
                self.resolved_endpoint_model,
                self.actual_gateway,
            )
        ):
            raise ValueError("failed/blocked probes cannot claim actual provider identity")
        if self.status is RemoteProbeStatus.MEASURED:
            required_identity = (
                self.observed_provider_name,
                self.top_level_response_model,
                self.resolved_endpoint_model,
                self.actual_gateway,
                self.endpoint_selector_proof,
            )
            if any(value in (None, "") for value in required_identity):
                raise ValueError("measured probes require complete actual route identity")
            if self.endpoint_selected is not True:
                raise ValueError("measured probes require a selected endpoint")
            if self.billed_cost_usd is None:
                raise ValueError("measured paid probes require billed cost")
        if self.tool_execution_status != "not_executed":
            raise ValueError("gateway probes cannot claim external tool execution")
        return self


class RemoteBakeoffReport(BaseModel):
    """Versioned report consumed by the route roster and operator runbook."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "remote-bakeoff-v1"
    run_id: str = Field(min_length=1)
    measured_at: datetime
    inventory_reference: str = Field(min_length=1)
    inventory_artifact_hash: str = Field(min_length=64, max_length=64)
    budget: RemoteBudget
    billed_spend_usd: float = Field(default=0, ge=0)
    candidates: tuple[RemoteRouteCandidate, ...] = ()
    probes: tuple[RemoteProbeResult, ...] = ()
    roster: Mapping[str, object] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @field_validator("measured_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("remote bake-off timestamp must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("inventory_artifact_hash")
    @classmethod
    def inventory_digest(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("inventory_artifact_hash must be a lowercase SHA-256 digest")
        return value

    @field_validator("billed_spend_usd")
    @classmethod
    def finite_billed_spend(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("billed_spend_usd must be finite")
        return float(value)

    @model_validator(mode="after")
    def enforce_budget_cap(self) -> RemoteBakeoffReport:
        if self.budget.spent_usd > self.budget.cap_usd + 1e-12:
            raise ValueError("remote bake-off reserved spend exceeds its cap")
        measured = sum(
            item.billed_cost_usd or 0
            for item in self.probes
            if item.status is RemoteProbeStatus.MEASURED
        )
        if abs(self.billed_spend_usd - measured) > 1e-12:
            raise ValueError("billed_spend_usd must equal the sum of measured probe costs")
        return self


def canonical_hash(value: object) -> str:
    """Hash sanitized JSON deterministically."""

    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def sanitize_metadata(value: object, *, _key: str = "") -> object:
    """Keep only safe scalar metadata; drop raw messages and user identifiers."""

    forbidden = {"raw", "message", "user_id", "user", "prompt", "content", "authorization"}
    allowed = {
        "attempt",
        "attempts",
        "attempt_metadata",
        "attempted_endpoints",
        "available",
        "deadline_exhausted",
        "error_type",
        "http_status",
        "is_byok",
        "limit_source",
        "model",
        "provider",
        "provider_code",
        "provider_name",
        "requested_model",
        "requested_provider_selector",
        "resolved_model",
        "retry_after_seconds",
        "retry_delay_seconds",
        "selected",
        "selected_count",
        "status_code",
        "timeout_seconds",
    }
    if isinstance(value, Mapping):
        output: dict[str, object] = {}
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if (
                normalized in forbidden
                or normalized.startswith("raw")
                or normalized.endswith("_message")
                or "user_id" in normalized
                or normalized not in allowed
            ):
                continue
            safe = sanitize_metadata(child, _key=normalized)
            if safe is not None:
                output[normalized] = safe
        return output
    if isinstance(value, (list, tuple)):
        return [
            item
            for item in (sanitize_metadata(child, _key=_key) for child in value)
            if item is not None
        ]
    if isinstance(value, (str, bool, int, float)):
        # Provider error text is intentionally omitted even when a caller puts
        # it under a non-standard field.
        if _key in {"raw", "message", "user_id"}:
            return None
        return value
    return None


def write_remote_report(report: RemoteBakeoffReport, path: Path) -> str:
    """Write one immutable sanitized report and return its content hash."""

    if path.exists():
        raise FileExistsError(f"remote bake-off evidence already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump(mode="json", round_trip=True)
    encoded = (
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    )
    digest = sha256(encoded).hexdigest()
    path.write_bytes(encoded)
    return digest


__all__ = [
    "RemoteBakeoffReport",
    "RemoteBudget",
    "RemoteInvocation",
    "RemoteProbeResult",
    "RemoteProbeStatus",
    "RemoteRouteCandidate",
    "RemoteRouteRole",
    "canonical_hash",
    "sanitize_metadata",
    "write_remote_report",
]
