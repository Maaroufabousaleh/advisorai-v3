"""Stable ports for replaceable infrastructure components.

The ports are intentionally dependency-light. Concrete adapters for gateways,
archives, and event transport can be selected by a Phase 0 bake-off without
leaking provider-specific objects into the contracts or trading boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from math import isfinite
from pathlib import PurePosixPath
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.contracts.core import is_forbidden_authority_action, normalize_authority_action


def _require_digest(value: str, info: object) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        field = getattr(info, "field_name", "digest")
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


class GatewayMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    role: str = Field(min_length=1)
    content: str = Field(min_length=1)


class GatewayTool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1)
    input_schema_version: str = Field(min_length=1)
    output_schema_version: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def forbid_trading_tools(cls, value: str) -> str:
        normalized = normalize_authority_action(value)
        if not normalized:
            raise ValueError("gateway tool name cannot be blank")
        if is_forbidden_authority_action(normalized):
            raise ValueError("model gateways cannot expose trading authority")
        return value.strip()


class GatewayRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    gateway: str = Field(min_length=1)
    fallback_chain: tuple[str, ...] = ()
    schema_mode: str = Field(default="typed_json", min_length=1)

    @field_validator("fallback_chain")
    @classmethod
    def require_unique_fallbacks(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("gateway fallback identities cannot be blank")
        normalized = tuple(item.strip() for item in value)
        if len(normalized) != len({item.lower() for item in normalized}):
            raise ValueError("gateway fallback identities must be unique")
        return normalized

    @field_validator("provider", "model", "gateway", "schema_mode")
    @classmethod
    def require_route_tokens(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("gateway route identity fields cannot be blank")
        return value.strip()

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
    prompt_version: str
    tool_version: str | None = None
    privacy_class: str = "non_secret"
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

    @model_validator(mode="after")
    def require_typed_request(self) -> GatewayRequest:
        if not self.messages:
            raise ValueError("gateway requests require at least one message")
        if not self.prompt_version.strip():
            raise ValueError("gateway requests require a prompt version")
        if self.tool_version is not None and not self.tool_version.strip():
            raise ValueError("gateway tool version cannot be blank")
        return self

    def content_hash(self) -> str:
        payload = self.model_dump_json(indent=None, exclude={"request_id", "created_at"})
        return sha256(payload.encode("utf-8")).hexdigest()


class GatewayResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    request_id: UUID
    route: GatewayRoute
    content: str = Field(min_length=1)
    typed_payload: Mapping[str, object] | None = None
    tool_calls: tuple[Mapping[str, object], ...] = ()
    latency_ms: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    provider_request_id: str | None = None

    @field_validator("estimated_cost_usd")
    @classmethod
    def require_finite_cost(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("gateway estimated cost must be finite")
        return value

    @model_validator(mode="after")
    def forbid_trading_tool_calls(self) -> GatewayResponse:
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
