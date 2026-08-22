"""Trading scope and authority matrix for the bounded V1 live policy.

The evaluator is a scope decision only.  It never creates an order, changes a
RiskKernel policy, enables a venue, or grants execution authority.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .authorization import HumanAuthorization, authorization_is_valid
from .decisions import DecisionOutcome
from .hashing import canonical_sha256


class ScopeClass(StrEnum):
    LIVE_ELIGIBLE = "LIVE_ELIGIBLE"
    DISABLED = "DISABLED"
    SYSTEM_FORBIDDEN = "SYSTEM_FORBIDDEN"
    HUMAN_TECHNICAL_GATE = "HUMAN_TECHNICAL_GATE"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    AUTONOMOUS_PROTECTIVE = "AUTONOMOUS_PROTECTIVE"


class ScopeDecisionOutcome(StrEnum):
    ALLOW_AUTONOMOUS = "ALLOW_AUTONOMOUS"
    ALLOW_HUMAN_GATED = "ALLOW_HUMAN_GATED"
    REQUIRE_HUMAN_TECHNICAL_GATE = "REQUIRE_HUMAN_TECHNICAL_GATE"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    DISABLED = "DISABLED"
    SYSTEM_FORBIDDEN = "SYSTEM_FORBIDDEN"
    HARD_BLOCK = "HARD_BLOCK"


class MarketType(StrEnum):
    SPOT = "SPOT"
    MARGIN = "MARGIN"
    FUTURES = "FUTURES"
    PERPETUAL = "PERPETUAL"
    OPTIONS = "OPTIONS"
    LEVERAGED_TOKENS = "LEVERAGED_TOKENS"


class PositionDirection(StrEnum):
    LONG = "LONG"
    FLAT = "FLAT"
    SHORT = "SHORT"


class ScopeAction(StrEnum):
    TRADE = "TRADE"
    REDUCE_RISK = "REDUCE_RISK"
    EMERGENCY_PROTECTIVE = "EMERGENCY_PROTECTIVE"
    RESEARCH = "RESEARCH"
    WITHDRAW = "WITHDRAW"
    EXTERNAL_TRANSFER = "EXTERNAL_TRANSFER"
    API_KEY_ADMINISTRATION = "API_KEY_ADMINISTRATION"
    ADD_ASSET = "ADD_ASSET"
    ADD_BROKER_OR_VENUE = "ADD_BROKER_OR_VENUE"
    ENABLE_LEVERAGE = "ENABLE_LEVERAGE"
    PROMOTE_MODEL = "PROMOTE_MODEL"
    PROMOTE_STRATEGY = "PROMOTE_STRATEGY"
    RELAX_RISK_LIMITS = "RELAX_RISK_LIMITS"
    RESUME_AFTER_HARD_KILL = "RESUME_AFTER_HARD_KILL"


class ScopeReasonCode(StrEnum):
    LIVE_SPOT_LONG_FLAT = "LIVE_SPOT_LONG_FLAT"
    LIVE_CAPITAL_DISABLED = "LIVE_CAPITAL_DISABLED"
    GOVERNANCE_DECISION_REQUIRED = "GOVERNANCE_DECISION_REQUIRED"
    QUALIFICATION_REQUIRED = "QUALIFICATION_REQUIRED"
    RISK_KERNEL_REQUIRED = "RISK_KERNEL_REQUIRED"
    RISK_KERNEL_REJECTED = "RISK_KERNEL_REJECTED"
    OMS_STATE_REQUIRED = "OMS_STATE_REQUIRED"
    OMS_STATE_AMBIGUOUS = "OMS_STATE_AMBIGUOUS"
    VENUE_NOT_APPROVED = "VENUE_NOT_APPROVED"
    NEW_ASSET = "NEW_ASSET"
    SHORTS_DISABLED = "SHORTS_DISABLED"
    MARGIN_DISABLED = "MARGIN_DISABLED"
    FUTURES_DISABLED = "FUTURES_DISABLED"
    PERPETUALS_DISABLED = "PERPETUALS_DISABLED"
    OPTIONS_DISABLED = "OPTIONS_DISABLED"
    LEVERAGED_TOKENS_DISABLED = "LEVERAGED_TOKENS_DISABLED"
    SYSTEM_FORBIDDEN_ACTION = "SYSTEM_FORBIDDEN_ACTION"
    HUMAN_AUTHORIZATION_REQUIRED = "HUMAN_AUTHORIZATION_REQUIRED"
    TECHNICAL_GATE_REQUIRED = "TECHNICAL_GATE_REQUIRED"
    HUMAN_TECHNICAL_GATE_SATISFIED = "HUMAN_TECHNICAL_GATE_SATISFIED"
    RESEARCH_SCOPE_ONLY = "RESEARCH_SCOPE_ONLY"
    SCOPE_INPUT_UNKNOWN = "SCOPE_INPUT_UNKNOWN"
    PROTECTIVE_ACTION = "PROTECTIVE_ACTION"
    PROTECTIVE_TRIGGER_REQUIRED = "PROTECTIVE_TRIGGER_REQUIRED"


class TradingScopePolicy(BaseModel):
    """Frozen V1 scope matrix stacked on the human-governance policy."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    human_governance_policy_version: str = Field(min_length=1)
    live_activation_permitted: bool = False
    live_symbols: tuple[str, ...]
    live_market_type: MarketType = MarketType.SPOT
    live_directions: tuple[PositionDirection, ...]
    approved_spot_venues: tuple[str, ...] = ()
    disabled_market_types: tuple[MarketType, ...]
    system_forbidden_actions: tuple[ScopeAction, ...]
    human_gate_actions: tuple[ScopeAction, ...]
    research_enabled: bool = True
    execution_authority: bool = False
    content_hash: str = ""

    @field_validator("live_symbols")
    @classmethod
    def normalize_live_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().upper() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("live symbols must be unique and non-blank")
        return normalized

    @field_validator("approved_spot_venues")
    @classmethod
    def normalize_venues(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().lower() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("approved venues must be unique and non-blank")
        return normalized

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        if value and (
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError("content_hash must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def validate_matrix(self) -> TradingScopePolicy:
        if self.live_symbols != ("BTCUSDT", "ETHUSDT"):
            raise ValueError("V1 live scope must remain BTCUSDT and ETHUSDT")
        if self.live_market_type is not MarketType.SPOT:
            raise ValueError("V1 live scope must remain spot")
        if set(self.live_directions) != {PositionDirection.LONG, PositionDirection.FLAT}:
            raise ValueError("V1 live scope must allow only long and flat directions")
        required_disabled = {
            MarketType.MARGIN,
            MarketType.FUTURES,
            MarketType.PERPETUAL,
            MarketType.OPTIONS,
            MarketType.LEVERAGED_TOKENS,
        }
        if not required_disabled.issubset(self.disabled_market_types):
            raise ValueError("V1 disabled market types are incomplete")
        required_forbidden = {
            ScopeAction.WITHDRAW,
            ScopeAction.EXTERNAL_TRANSFER,
            ScopeAction.API_KEY_ADMINISTRATION,
        }
        if not required_forbidden.issubset(self.system_forbidden_actions):
            raise ValueError("V1 system-forbidden action set is incomplete")
        required_gates = {
            ScopeAction.ADD_ASSET,
            ScopeAction.ADD_BROKER_OR_VENUE,
            ScopeAction.ENABLE_LEVERAGE,
            ScopeAction.PROMOTE_MODEL,
            ScopeAction.PROMOTE_STRATEGY,
            ScopeAction.RELAX_RISK_LIMITS,
            ScopeAction.RESUME_AFTER_HARD_KILL,
        }
        if not required_gates.issubset(self.human_gate_actions):
            raise ValueError("V1 human/technical gate set is incomplete")
        if self.execution_authority:
            raise ValueError("scope policy cannot grant execution authority")
        expected_hash = self._computed_content_hash()
        if self.content_hash and self.content_hash != expected_hash:
            raise ValueError("scope policy content_hash does not match policy content")
        object.__setattr__(self, "content_hash", expected_hash)
        return self

    def _canonical_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"content_hash"})

    def _computed_content_hash(self) -> str:
        return canonical_sha256(self._canonical_payload())

    @property
    def policy_hash(self) -> str:
        return self.content_hash


class TradingScopeRequest(BaseModel):
    """A proposed scope action; no field confers execution capability."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    action: ScopeAction
    input_snapshot_hash: str = Field(min_length=64, max_length=64)
    instrument: str | None = None
    market_type: MarketType | None = None
    direction: PositionDirection | None = None
    venue: str | None = None
    venue_approved: bool | None = None
    live_activation_permitted: bool | None = None
    qualification_valid: bool | None = None
    governance_outcome: DecisionOutcome | None = None
    risk_kernel_approved: bool | None = None
    oms_state_unambiguous: bool | None = None
    deterministic_trigger_valid: bool | None = None
    technical_gate_valid: bool | None = None
    human_authorization: HumanAuthorization | None = None
    evaluated_at: datetime

    @field_validator("input_snapshot_hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError("input_snapshot_hash must be a lowercase SHA-256 digest")
        return value

    @field_validator("instrument")
    @classmethod
    def normalize_instrument(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("instrument cannot be blank")
        return normalized

    @field_validator("venue")
    @classmethod
    def normalize_venue(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("venue cannot be blank")
        return normalized

    @field_validator("evaluated_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scope evaluation time must include a timezone")
        return value.astimezone(UTC)


class TradingScopeDecision(BaseModel):
    """Immutable scope result; ``execution_authority`` is permanently false."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    scope_class: ScopeClass
    outcome: ScopeDecisionOutcome
    reason_codes: tuple[ScopeReasonCode, ...]
    policy_id: str
    policy_version: str
    policy_hash: str
    input_snapshot_hash: str
    evaluated_at: datetime
    authorization_valid: bool = False
    human_authorization_id: UUID | None = None
    execution_authority: bool = False
    decision_hash: str = ""

    @field_validator("policy_hash", "input_snapshot_hash")
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("scope decision hashes must be lowercase SHA-256")
        return value

    @field_validator("evaluated_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scope decision time must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def finalize_decision(self) -> TradingScopeDecision:
        if self.execution_authority:
            raise ValueError("scope decisions cannot grant execution authority")
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"decision_hash"}))
        if self.decision_hash and self.decision_hash != expected:
            raise ValueError("scope decision_hash does not match decision content")
        object.__setattr__(self, "decision_hash", expected)
        return self


def _reasons(reasons: list[ScopeReasonCode]) -> tuple[ScopeReasonCode, ...]:
    return tuple(dict.fromkeys(reasons))


def _decision(
    *,
    policy: TradingScopePolicy,
    request: TradingScopeRequest,
    scope_class: ScopeClass,
    outcome: ScopeDecisionOutcome,
    reasons: list[ScopeReasonCode],
    authorization_valid: bool = False,
) -> TradingScopeDecision:
    return TradingScopeDecision(
        scope_class=scope_class,
        outcome=outcome,
        reason_codes=_reasons(reasons),
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_hash=policy.policy_hash,
        input_snapshot_hash=request.input_snapshot_hash,
        evaluated_at=request.evaluated_at,
        authorization_valid=authorization_valid,
        human_authorization_id=(
            request.human_authorization.authorization_id
            if request.human_authorization is not None
            else None
        ),
    )


def _human_gate(
    policy: TradingScopePolicy,
    request: TradingScopeRequest,
    reasons: list[ScopeReasonCode],
    *,
    required_action: ScopeAction | None = None,
) -> TradingScopeDecision:
    authorization_valid = authorization_is_valid(
        request.human_authorization,
        at=request.evaluated_at,
        action_type=(required_action or request.action).value,
        policy_version=policy.human_governance_policy_version,
    )
    if not authorization_valid:
        reasons.append(ScopeReasonCode.HUMAN_AUTHORIZATION_REQUIRED)
    if request.technical_gate_valid is not True:
        reasons.append(ScopeReasonCode.TECHNICAL_GATE_REQUIRED)
    if authorization_valid and request.technical_gate_valid is True:
        reasons.append(ScopeReasonCode.HUMAN_TECHNICAL_GATE_SATISFIED)
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.HUMAN_TECHNICAL_GATE,
            outcome=ScopeDecisionOutcome.ALLOW_HUMAN_GATED,
            reasons=reasons,
            authorization_valid=True,
        )
    return _decision(
        policy=policy,
        request=request,
        scope_class=ScopeClass.HUMAN_TECHNICAL_GATE,
        outcome=ScopeDecisionOutcome.REQUIRE_HUMAN_TECHNICAL_GATE,
        reasons=reasons,
        authorization_valid=authorization_valid,
    )


def _protective_decision(
    policy: TradingScopePolicy, request: TradingScopeRequest
) -> TradingScopeDecision:
    reasons = [ScopeReasonCode.PROTECTIVE_ACTION]
    if request.deterministic_trigger_valid is not True:
        reasons.append(ScopeReasonCode.PROTECTIVE_TRIGGER_REQUIRED)
    if request.risk_kernel_approved is not True:
        reasons.append(
            ScopeReasonCode.RISK_KERNEL_REJECTED
            if request.risk_kernel_approved is False
            else ScopeReasonCode.RISK_KERNEL_REQUIRED
        )
    if request.oms_state_unambiguous is not True:
        reasons.append(
            ScopeReasonCode.OMS_STATE_AMBIGUOUS
            if request.oms_state_unambiguous is False
            else ScopeReasonCode.OMS_STATE_REQUIRED
        )
    if len(reasons) > 1:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.AUTONOMOUS_PROTECTIVE,
            outcome=ScopeDecisionOutcome.HARD_BLOCK,
            reasons=reasons,
        )
    return _decision(
        policy=policy,
        request=request,
        scope_class=ScopeClass.AUTONOMOUS_PROTECTIVE,
        outcome=ScopeDecisionOutcome.ALLOW_AUTONOMOUS,
        reasons=reasons,
    )


def evaluate_trading_scope(
    policy: TradingScopePolicy, request: TradingScopeRequest
) -> TradingScopeDecision:
    """Evaluate scope and authority without creating an execution capability."""

    if request.action in policy.system_forbidden_actions:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.SYSTEM_FORBIDDEN,
            outcome=ScopeDecisionOutcome.SYSTEM_FORBIDDEN,
            reasons=[ScopeReasonCode.SYSTEM_FORBIDDEN_ACTION],
        )

    if request.action in policy.human_gate_actions:
        return _human_gate(policy, request, [])

    if request.action is ScopeAction.RESEARCH:
        if not policy.research_enabled:
            return _decision(
                policy=policy,
                request=request,
                scope_class=ScopeClass.DISABLED,
                outcome=ScopeDecisionOutcome.DISABLED,
                reasons=[ScopeReasonCode.SCOPE_INPUT_UNKNOWN],
            )
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.RESEARCH_ONLY,
            outcome=ScopeDecisionOutcome.RESEARCH_ONLY,
            reasons=[ScopeReasonCode.RESEARCH_SCOPE_ONLY],
        )

    if request.action in {ScopeAction.REDUCE_RISK, ScopeAction.EMERGENCY_PROTECTIVE}:
        return _protective_decision(policy, request)

    if request.action is not ScopeAction.TRADE:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.DISABLED,
            outcome=ScopeDecisionOutcome.DISABLED,
            reasons=[ScopeReasonCode.SCOPE_INPUT_UNKNOWN],
        )

    missing = []
    if request.instrument is None:
        missing.append(ScopeReasonCode.SCOPE_INPUT_UNKNOWN)
    if request.market_type is None:
        missing.append(ScopeReasonCode.SCOPE_INPUT_UNKNOWN)
    if request.direction is None:
        missing.append(ScopeReasonCode.SCOPE_INPUT_UNKNOWN)
    if request.venue is None:
        missing.append(ScopeReasonCode.SCOPE_INPUT_UNKNOWN)
    if missing:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.DISABLED,
            outcome=ScopeDecisionOutcome.HARD_BLOCK,
            reasons=missing,
        )

    assert request.instrument is not None
    assert request.market_type is not None
    assert request.direction is not None
    assert request.venue is not None
    disabled_reasons = {
        MarketType.MARGIN: ScopeReasonCode.MARGIN_DISABLED,
        MarketType.FUTURES: ScopeReasonCode.FUTURES_DISABLED,
        MarketType.PERPETUAL: ScopeReasonCode.PERPETUALS_DISABLED,
        MarketType.OPTIONS: ScopeReasonCode.OPTIONS_DISABLED,
        MarketType.LEVERAGED_TOKENS: ScopeReasonCode.LEVERAGED_TOKENS_DISABLED,
    }
    if request.market_type in policy.disabled_market_types:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.DISABLED,
            outcome=ScopeDecisionOutcome.DISABLED,
            reasons=[
                disabled_reasons.get(request.market_type, ScopeReasonCode.SCOPE_INPUT_UNKNOWN)
            ],
        )
    if request.direction is PositionDirection.SHORT:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.DISABLED,
            outcome=ScopeDecisionOutcome.DISABLED,
            reasons=[ScopeReasonCode.SHORTS_DISABLED],
        )
    if request.market_type is not policy.live_market_type:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.DISABLED,
            outcome=ScopeDecisionOutcome.DISABLED,
            reasons=[ScopeReasonCode.SCOPE_INPUT_UNKNOWN],
        )
    if request.instrument not in policy.live_symbols:
        return _human_gate(
            policy,
            request,
            [ScopeReasonCode.NEW_ASSET],
            required_action=ScopeAction.ADD_ASSET,
        )

    venue_is_listed = (
        not policy.approved_spot_venues or request.venue in policy.approved_spot_venues
    )
    if not venue_is_listed or request.venue_approved is not True:
        return _human_gate(
            policy,
            request,
            [ScopeReasonCode.VENUE_NOT_APPROVED],
            required_action=ScopeAction.ADD_BROKER_OR_VENUE,
        )

    reasons: list[ScopeReasonCode] = [ScopeReasonCode.LIVE_SPOT_LONG_FLAT]
    if (
        policy.live_activation_permitted is not True
        or request.live_activation_permitted is not True
    ):
        reasons.append(ScopeReasonCode.LIVE_CAPITAL_DISABLED)
    if request.qualification_valid is not True:
        reasons.append(ScopeReasonCode.QUALIFICATION_REQUIRED)
    if request.governance_outcome is not DecisionOutcome.ALLOW_AUTONOMOUS:
        reasons.append(ScopeReasonCode.GOVERNANCE_DECISION_REQUIRED)
    if request.risk_kernel_approved is not True:
        reasons.append(
            ScopeReasonCode.RISK_KERNEL_REJECTED
            if request.risk_kernel_approved is False
            else ScopeReasonCode.RISK_KERNEL_REQUIRED
        )
    if request.oms_state_unambiguous is not True:
        reasons.append(
            ScopeReasonCode.OMS_STATE_AMBIGUOUS
            if request.oms_state_unambiguous is False
            else ScopeReasonCode.OMS_STATE_REQUIRED
        )
    if len(reasons) > 1:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.LIVE_ELIGIBLE,
            outcome=ScopeDecisionOutcome.HARD_BLOCK,
            reasons=reasons,
        )
    return _decision(
        policy=policy,
        request=request,
        scope_class=ScopeClass.LIVE_ELIGIBLE,
        outcome=ScopeDecisionOutcome.ALLOW_AUTONOMOUS,
        reasons=reasons,
    )


def load_trading_scope_policy(path: Path) -> TradingScopePolicy:
    """Load one local, pinned YAML scope policy without network access."""

    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"trading scope policy {path} must contain an object")
    return TradingScopePolicy.model_validate(payload)
