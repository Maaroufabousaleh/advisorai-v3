"""Immutable, human-only authorization artifacts."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .hashing import canonical_sha256

HUMAN_ONLY_ACTIONS = frozenset(
    {
        "ENABLE_LIVE_CAPITAL",
        "INCREASE_PLANNED_CAPITAL",
        "PROGRESS_ALLOCATION_STAGE",
        "CHANGE_DAILY_LOSS_THRESHOLDS",
        "CHANGE_DRAWDOWN_THRESHOLDS",
        "INCREASE_SINGLE_ASSET_CAP",
        "ENABLE_LEVERAGE",
        "INCREASE_LEVERAGE_CEILING",
        "ADD_ASSET",
        "ADD_ASSET_CLASS",
        "ADD_LIVE_INSTRUMENT",
        "ADD_BROKER_OR_VENUE",
        "ADD_BROKER",
        "CHANGE_BROKER",
        "CHANGE_VENUE",
        "SWITCH_VENUE",
        "ENABLE_SHORT_SELLING",
        "ENABLE_SHORTING",
        "ENABLE_DERIVATIVE_CLASS",
        "ENABLE_DERIVATIVES",
        "ENABLE_OPTIONS_FUTURES_OR_MARGIN",
        "PROMOTE_MODEL",
        "PROMOTE_STRATEGY",
        "PROMOTE_ALPHA_CANDIDATE",
        "RELAX_RISK_LIMITS",
        "RELAX_RISK_LIMIT",
        "CHANGE_DAILY_LOSS_THRESHOLD",
        "CHANGE_DRAWDOWN_THRESHOLD",
        "CHANGE_CAPITAL_ALLOCATION",
        "INCREASE_CAPITAL_STAGE",
        "CHANGE_MODEL",
        "CHANGE_STRATEGY",
        "CHANGE_PRODUCTION_ENDPOINT",
        "ENABLE_PRODUCTION_FROM_TESTNET",
        "CHANGE_PORTFOLIO_OBJECTIVE",
        "CHANGE_RISK_KERNEL_RULES",
        "CHANGE_OMS_AUTHORITY",
        "CHANGE_KILL_SWITCH_BEHAVIOR",
        "RESUME_AFTER_HARD_DRAWDOWN",
        "RESUME_AFTER_HARD_KILL",
    }
)


def normalize_action_type(value: str) -> str:
    """Normalize action spelling before applying human-only controls."""

    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value.strip())
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", normalized)).strip("_").upper()


class ActorType(StrEnum):
    HUMAN = "HUMAN"
    AGENT = "AGENT"
    LLM = "LLM"
    SYSTEM = "SYSTEM"


class AuthorizationExpiryMode(StrEnum):
    ONE_TIME = "ONE_TIME"
    SESSION = "SESSION"
    FIXED_EXPIRATION = "FIXED_EXPIRATION"
    PERSISTENT_UNTIL_REVOKED = "PERSISTENT_UNTIL_REVOKED"


class HumanAuthorization(BaseModel):
    """A signed-by-record human authorization; it carries no secret."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    authorization_id: UUID = Field(default_factory=uuid4)
    created_at: datetime
    actor_type: ActorType
    actor_identity: str = Field(min_length=1)
    action_type: str = Field(min_length=1)
    target: str = Field(min_length=1)
    previous_value: str | None = None
    approved_value: str = Field(min_length=1)
    scope: tuple[str, ...] = Field(min_length=1)
    expires_at: datetime | None = None
    expiry_mode: AuthorizationExpiryMode
    policy_version: str = Field(min_length=1)
    repository_commit: str = Field(min_length=7, max_length=64)
    reason: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    authorization_hash: str = ""

    @field_validator("created_at", "expires_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("authorization timestamps must include a timezone")
        return value.astimezone(UTC)

    @field_validator("action_type")
    @classmethod
    def normalize_action(cls, value: str) -> str:
        normalized = normalize_action_type(value)
        if not normalized:
            raise ValueError("authorization action_type cannot be blank")
        return normalized

    @field_validator("repository_commit")
    @classmethod
    def validate_commit(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) < 7 or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise ValueError("repository_commit must be a hexadecimal commit identity")
        return normalized

    @field_validator("scope", "evidence_refs")
    @classmethod
    def normalize_references(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("authorization references cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("authorization references must be unique")
        return normalized

    @field_validator("authorization_hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if value and (
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError("authorization_hash must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def validate_authorization(self) -> HumanAuthorization:
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("authorization expiry must be after creation")
        if (
            self.expiry_mode
            in {
                AuthorizationExpiryMode.ONE_TIME,
                AuthorizationExpiryMode.SESSION,
                AuthorizationExpiryMode.FIXED_EXPIRATION,
            }
            and self.expires_at is None
        ):
            raise ValueError("bounded authorization modes require expires_at")
        if self.expiry_mode is AuthorizationExpiryMode.PERSISTENT_UNTIL_REVOKED:
            # A bounded persistent authorization is still allowed; it is useful
            # for a review window and remains fail-closed after expiry.
            pass
        expected_hash = self._computed_hash()
        if self.authorization_hash and self.authorization_hash != expected_hash:
            raise ValueError("authorization_hash does not match immutable authorization content")
        object.__setattr__(self, "authorization_hash", expected_hash)
        return self

    def _canonical_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"authorization_hash"})

    def _computed_hash(self) -> str:
        return canonical_sha256(self._canonical_payload())

    def is_valid_at(
        self,
        at: datetime,
        *,
        expected_action: str | None = None,
        consumed: bool = False,
        revoked: bool = False,
    ) -> bool:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("authorization check time must include a timezone")
        at = at.astimezone(UTC)
        if self.actor_type is not ActorType.HUMAN:
            return False
        if revoked:
            return False
        if expected_action is not None and self.action_type != expected_action:
            return False
        if not self.created_at <= at:
            return False
        if self.expires_at is not None and at >= self.expires_at:
            return False
        if self.expiry_mode is AuthorizationExpiryMode.ONE_TIME and consumed:
            return False
        return True


def authorization_is_valid(
    authorization: HumanAuthorization | None,
    *,
    at: datetime,
    action_type: str,
    policy_version: str | None = None,
    consumed_ids: Iterable[UUID] = (),
    revoked_ids: Iterable[UUID] = (),
) -> bool:
    """Validate a human authorization without mutating it."""

    if authorization is None:
        return False
    authorization_id = authorization.authorization_id
    if policy_version is not None and authorization.policy_version != policy_version:
        return False
    consumed = authorization_id in set(consumed_ids)
    revoked = authorization_id in set(revoked_ids)
    return authorization.is_valid_at(
        at,
        expected_action=normalize_action_type(action_type),
        consumed=consumed,
        revoked=revoked,
    )


def is_human_only_action(action_type: str) -> bool:
    """Return whether an action is reserved for explicit human governance."""

    return normalize_action_type(action_type) in HUMAN_ONLY_ACTIONS
