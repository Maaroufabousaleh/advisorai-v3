"""Versioned, deterministic policy for independent public-source disagreement."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.collectors.source_health import DisagreementState


class DisagreementAction(StrEnum):
    ALLOW = "allow"
    TIGHTER_CONFIDENCE = "tighter_confidence"
    NO_TRADE_ABSTAIN = "no_trade_abstain"


class SourceQuote(BaseModel):
    """Comparable top-of-book observation from one independent source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1)
    provider_identity: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)
    provider_event_at: datetime | None = None
    received_at: datetime
    clock_confident: bool = True

    @field_validator("received_at", "provider_event_at")
    @classmethod
    def require_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source quote timestamps must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_ordered_book(self) -> SourceQuote:
        if self.bid >= self.ask:
            raise ValueError("source quote must have bid below ask")
        return self


class SourceDisagreementPolicy(BaseModel):
    """Thresholds are deliberately conservative and cannot loosen risk limits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(default="source-disagreement-v1", min_length=1)
    normal_mid_relative_difference: Decimal = Field(default=Decimal("0.003"), ge=0)
    severe_mid_relative_difference: Decimal = Field(default=Decimal("0.010"), ge=0)
    normal_spread_relative_difference: Decimal = Field(default=Decimal("0.005"), ge=0)
    severe_spread_relative_difference: Decimal = Field(default=Decimal("0.020"), ge=0)
    normal_freshness_difference_seconds: float = Field(default=2.0, ge=0)
    severe_freshness_difference_seconds: float = Field(default=10.0, ge=0)

    @model_validator(mode="after")
    def require_ordered_thresholds(self) -> SourceDisagreementPolicy:
        if self.severe_mid_relative_difference < self.normal_mid_relative_difference:
            raise ValueError("severe mid threshold must not be below normal threshold")
        if self.severe_spread_relative_difference < self.normal_spread_relative_difference:
            raise ValueError("severe spread threshold must not be below normal threshold")
        if self.severe_freshness_difference_seconds < self.normal_freshness_difference_seconds:
            raise ValueError("severe freshness threshold must not be below normal threshold")
        return self


class SourceDisagreementObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    measured_at: datetime
    policy_version: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    left_source: str = Field(min_length=1)
    right_source: str = Field(min_length=1)
    mid_relative_difference: Decimal | None = None
    spread_relative_difference: Decimal | None = None
    freshness_difference_seconds: float | None = Field(default=None, ge=0)
    timestamp_confident: bool
    state: DisagreementState
    action: DisagreementAction
    fail_closed: bool

    @field_validator("measured_at")
    @classmethod
    def require_aware_measured_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("disagreement measured_at must include a timezone")
        return value.astimezone(UTC)


def _relative_difference(left: Decimal, right: Decimal) -> Decimal:
    return abs(left - right) / max(left, right)


def compare_source_quotes(
    left: SourceQuote,
    right: SourceQuote,
    *,
    policy: SourceDisagreementPolicy | None = None,
    measured_at: datetime | None = None,
) -> SourceDisagreementObservation:
    """Compare quotes without averaging, replacing, or mutating either source."""

    policy = policy or SourceDisagreementPolicy()
    if left.symbol.upper() != right.symbol.upper():
        raise ValueError("source disagreement requires the same canonical symbol")
    if left.source_id == right.source_id:
        raise ValueError("source disagreement requires independent source identities")
    try:
        left_mid = (left.bid + left.ask) / 2
        right_mid = (right.bid + right.ask) / 2
        left_spread = left.ask - left.bid
        right_spread = right.ask - right.bid
        mid_difference = _relative_difference(left_mid, right_mid)
        spread_difference = _relative_difference(left_spread, right_spread)
    except (InvalidOperation, ZeroDivisionError) as exc:
        raise ValueError("source disagreement quote arithmetic failed") from exc

    freshness: float | None = None
    if left.provider_event_at is not None and right.provider_event_at is not None:
        freshness = abs(
            (left.received_at - left.provider_event_at).total_seconds()
            - (right.received_at - right.provider_event_at).total_seconds()
        )
    timestamp_confident = left.clock_confident and right.clock_confident
    severe = (
        mid_difference > policy.severe_mid_relative_difference
        or spread_difference > policy.severe_spread_relative_difference
        or (freshness is not None and freshness > policy.severe_freshness_difference_seconds)
        or not timestamp_confident
        and freshness is None
    )
    normal = (
        mid_difference <= policy.normal_mid_relative_difference
        and spread_difference <= policy.normal_spread_relative_difference
        and (freshness is None or freshness <= policy.normal_freshness_difference_seconds)
        and timestamp_confident
    )
    if severe:
        state = DisagreementState.SEVERE
        action = DisagreementAction.NO_TRADE_ABSTAIN
        fail_closed = True
    elif normal:
        state = DisagreementState.NORMAL
        action = DisagreementAction.ALLOW
        fail_closed = False
    else:
        state = DisagreementState.DEGRADED
        action = DisagreementAction.TIGHTER_CONFIDENCE
        fail_closed = True
    at = measured_at or max(left.received_at, right.received_at)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("disagreement measured_at must include a timezone")
    return SourceDisagreementObservation(
        measured_at=at.astimezone(UTC),
        policy_version=policy.policy_version,
        symbol=left.symbol.upper(),
        left_source=left.source_id,
        right_source=right.source_id,
        mid_relative_difference=mid_difference,
        spread_relative_difference=spread_difference,
        freshness_difference_seconds=round(freshness, 6) if freshness is not None else None,
        timestamp_confident=timestamp_confident,
        state=state,
        action=action,
        fail_closed=fail_closed,
    )


__all__ = [
    "DisagreementAction",
    "SourceDisagreementObservation",
    "SourceDisagreementPolicy",
    "SourceQuote",
    "compare_source_quotes",
]
