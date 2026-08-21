"""Deterministic governance decisions with no execution authority."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .authorization import (
    HumanAuthorization,
    authorization_is_valid,
    is_human_only_action,
    normalize_action_type,
)
from .hashing import canonical_sha256
from .policy import GovernancePolicy, LiveActivationInput


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class CertaintyClass(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class TimingClass(StrEnum):
    SLOW = "SLOW"
    NORMAL = "NORMAL"
    URGENT = "URGENT"


class ActionDirection(StrEnum):
    RISK_INCREASING = "RISK_INCREASING"
    RISK_NEUTRAL = "RISK_NEUTRAL"
    RISK_REDUCING = "RISK_REDUCING"
    EMERGENCY_PROTECTIVE = "EMERGENCY_PROTECTIVE"


class DecisionImpact(StrEnum):
    OPERATIONAL = "OPERATIONAL"
    EXECUTION = "EXECUTION"
    STRATEGIC = "STRATEGIC"
    POLICY_CHANGE = "POLICY_CHANGE"


class DecisionOutcome(StrEnum):
    ALLOW_AUTONOMOUS = "ALLOW_AUTONOMOUS"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"
    ABSTAIN = "ABSTAIN"
    DERISK_ONLY = "DERISK_ONLY"
    HARD_BLOCK = "HARD_BLOCK"


class RiskState(StrEnum):
    NORMAL = "NORMAL"
    DAILY_DERISK = "DAILY_DERISK"
    DAILY_HALT = "DAILY_HALT"
    DRAWDOWN_DERISK = "DRAWDOWN_DERISK"
    HARD_DRAWDOWN_KILL = "HARD_DRAWDOWN_KILL"


class ReasonCode(StrEnum):
    CONFIDENCE_TOO_LOW = "CONFIDENCE_TOO_LOW"
    CALIBRATION_MISSING = "CALIBRATION_MISSING"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    URGENT_HIGH_CONFIDENCE = "URGENT_HIGH_CONFIDENCE"
    DAILY_SOFT_LIMIT = "DAILY_SOFT_LIMIT"
    DAILY_HARD_LIMIT = "DAILY_HARD_LIMIT"
    DRAWDOWN_SOFT_LIMIT = "DRAWDOWN_SOFT_LIMIT"
    HARD_DRAWDOWN = "HARD_DRAWDOWN"
    LEVERAGE_DISABLED = "LEVERAGE_DISABLED"
    LEVERAGE_LIMIT = "LEVERAGE_LIMIT"
    POSITION_LIMIT = "POSITION_LIMIT"
    CORRELATED_EXPOSURE_LIMIT = "CORRELATED_EXPOSURE_LIMIT"
    CORRELATED_EXPOSURE_UNKNOWN = "CORRELATED_EXPOSURE_UNKNOWN"
    RECONCILIATION_UNHEALTHY = "RECONCILIATION_UNHEALTHY"
    DATA_UNHEALTHY = "DATA_UNHEALTHY"
    PIT_PROVENANCE_INVALID = "PIT_PROVENANCE_INVALID"
    MODEL_ROLE_NOT_ADMITTED = "MODEL_ROLE_NOT_ADMITTED"
    REGIME_UNSUPPORTED = "REGIME_UNSUPPORTED"
    LIQUIDITY_UNACCEPTABLE = "LIQUIDITY_UNACCEPTABLE"
    SPREAD_UNACCEPTABLE = "SPREAD_UNACCEPTABLE"
    OMS_STATE_AMBIGUOUS = "OMS_STATE_AMBIGUOUS"
    RISK_KERNEL_REJECTED = "RISK_KERNEL_REJECTED"
    RISK_KERNEL_UNKNOWN = "RISK_KERNEL_UNKNOWN"
    AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
    AUTHORIZATION_INVALID = "AUTHORIZATION_INVALID"
    LIVE_CAPITAL_DISABLED = "LIVE_CAPITAL_DISABLED"
    PLANNED_CAPITAL_MISSING = "PLANNED_CAPITAL_MISSING"
    EDGE_BELOW_COST = "EDGE_BELOW_COST"
    EDGE_EVIDENCE_UNKNOWN = "EDGE_EVIDENCE_UNKNOWN"
    POSITION_SIZING_INPUT_UNKNOWN = "POSITION_SIZING_INPUT_UNKNOWN"
    RISK_REDUCING_ACTION = "RISK_REDUCING_ACTION"
    EMERGENCY_PROTECTIVE = "EMERGENCY_PROTECTIVE"
    RISK_NEUTRAL_ACTION = "RISK_NEUTRAL_ACTION"
    STRATEGIC_HUMAN_ONLY = "STRATEGIC_HUMAN_ONLY"
    UNKNOWN_REQUIRED_INPUT = "UNKNOWN_REQUIRED_INPUT"


class CalibratedConfidenceEvidence(_StrictModel):
    """Quantitative calibration evidence; LLM prose cannot populate it."""

    contract_id: str = Field(min_length=1)
    calibrated_confidence: Decimal
    evidence_hash: str = Field(min_length=64, max_length=64)

    @field_validator("calibrated_confidence")
    @classmethod
    def validate_confidence(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or not 0 <= value <= 1:
            raise ValueError("calibrated confidence must be finite and within [0, 1]")
        return value

    @field_validator("evidence_hash")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError("confidence evidence hash must be lowercase SHA-256")
        return value


class GovernanceEvidence(_StrictModel):
    """Typed opportunity and safety evidence consumed by the policy."""

    input_snapshot_hash: str = Field(min_length=64, max_length=64)
    calibrated_confidence: CalibratedConfidenceEvidence | None = None
    llm_reported_confidence: Decimal | None = None
    expected_net_edge: Decimal | None = None
    conservative_all_in_cost: Decimal | None = None
    timing_evidence_valid: bool | None = None
    evidence_fresh: bool | None = None
    source_health_valid: bool | None = None
    pit_provenance_valid: bool | None = None
    model_role_admitted: bool | None = None
    regime_support_valid: bool | None = None
    liquidity_acceptable: bool | None = None
    spread_acceptable: bool | None = None
    portfolio_exposure_valid: bool | None = None
    correlated_exposure_valid: bool | None = None
    reconciliation_healthy: bool | None = None
    oms_state_unambiguous: bool | None = None
    risk_kernel_approval: bool | None = None
    protective_trigger_valid: bool | None = None

    @field_validator("input_snapshot_hash")
    @classmethod
    def validate_snapshot_hash(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError("input_snapshot_hash must be lowercase SHA-256")
        return value

    @field_validator("llm_reported_confidence")
    @classmethod
    def validate_optional_llm_confidence(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (not value.is_finite() or not 0 <= value <= 1):
            raise ValueError("LLM-reported confidence must be within [0, 1] when recorded")
        return value

    @field_validator("expected_net_edge", "conservative_all_in_cost")
    @classmethod
    def validate_cost_values(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("edge and cost values must be finite")
        return value


class EquitySnapshot(_StrictModel):
    """Managed equity basis including realized and unrealized P&L."""

    managed_equity: Decimal
    daily_start_equity: Decimal
    high_water_mark: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal

    @field_validator(
        "managed_equity", "daily_start_equity", "high_water_mark", "realized_pnl", "unrealized_pnl"
    )
    @classmethod
    def validate_equity_values(cls, value: Decimal, info) -> Decimal:
        if not value.is_finite():
            raise ValueError(f"{info.field_name} must be finite")
        return value

    @model_validator(mode="after")
    def validate_positive_bases(self) -> EquitySnapshot:
        if self.managed_equity <= 0 or self.daily_start_equity <= 0 or self.high_water_mark <= 0:
            raise ValueError("equity bases must be positive")
        return self

    @property
    def daily_loss_fraction(self) -> Decimal:
        return (self.managed_equity - self.daily_start_equity) / self.daily_start_equity

    @property
    def drawdown_fraction(self) -> Decimal:
        return (self.managed_equity - self.high_water_mark) / self.high_water_mark


class GovernanceRiskSnapshot(_StrictModel):
    """Current and proposed risk state; it does not mutate RiskKernel state."""

    equity: EquitySnapshot
    proposed_gross_leverage: Decimal | None = None
    proposed_asset_exposures: dict[str, Decimal] = Field(default_factory=dict)
    proposed_group_exposures: dict[str, Decimal] = Field(default_factory=dict)

    @field_validator("proposed_gross_leverage")
    @classmethod
    def validate_leverage(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (not value.is_finite() or value < 0):
            raise ValueError("proposed gross leverage must be finite and non-negative")
        return value

    @field_validator("proposed_asset_exposures", "proposed_group_exposures")
    @classmethod
    def validate_exposures(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        normalized: dict[str, Decimal] = {}
        for key, exposure in value.items():
            instrument = key.strip().upper()
            if not instrument or not exposure.is_finite() or exposure < 0:
                raise ValueError("proposed exposures must use finite non-negative values")
            normalized[instrument] = exposure
        return normalized


class GovernanceRequest(_StrictModel):
    """Pure input to :func:`evaluate_governance`."""

    action_type: str = "INCREASE_EXPOSURE"
    target: str = Field(min_length=1)
    action_direction: ActionDirection
    certainty_class: CertaintyClass
    urgency_class: TimingClass
    decision_impact: DecisionImpact = DecisionImpact.OPERATIONAL
    evidence: GovernanceEvidence
    risk: GovernanceRiskSnapshot
    human_authorization: HumanAuthorization | None = None
    evaluated_at: datetime

    @field_validator("action_type")
    @classmethod
    def normalize_action(cls, value: str) -> str:
        normalized = normalize_action_type(value)
        if not normalized:
            raise ValueError("governance action_type cannot be blank")
        return normalized

    @field_validator("evaluated_at")
    @classmethod
    def normalize_evaluated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("governance evaluation time must include a timezone")
        return value.astimezone(UTC)


class GovernanceDecision(_StrictModel):
    """Immutable, reproducible policy output; it is not an order instruction."""

    outcome: DecisionOutcome
    reason_codes: tuple[ReasonCode, ...]
    policy_id: str
    policy_version: str
    policy_hash: str
    input_snapshot_hash: str
    evaluated_at: datetime
    risk_state: RiskState
    risk_increasing_multiplier: Decimal
    human_authorization_id: UUID | None = None
    authorization_valid: bool = False
    decision_hash: str = ""

    @field_validator("evaluated_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision time must include a timezone")
        return value.astimezone(UTC)

    @field_validator("policy_hash", "input_snapshot_hash")
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("governance decision hashes must be lowercase SHA-256")
        return value

    @field_validator("risk_increasing_multiplier")
    @classmethod
    def validate_multiplier(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or not 0 <= value <= 1:
            raise ValueError("risk-increasing multiplier must be within [0, 1]")
        return value

    @model_validator(mode="after")
    def finalize_hash(self) -> GovernanceDecision:
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"decision_hash"}))
        if self.decision_hash and self.decision_hash != expected:
            raise ValueError("decision_hash does not match decision content")
        object.__setattr__(self, "decision_hash", expected)
        return self


def _unique_reasons(reasons: list[ReasonCode]) -> tuple[ReasonCode, ...]:
    return tuple(dict.fromkeys(reasons))


def _authorization_failure_reason(
    authorization: HumanAuthorization | None, at: datetime
) -> ReasonCode:
    if (
        authorization is not None
        and authorization.expires_at is not None
        and at >= authorization.expires_at
    ):
        return ReasonCode.AUTHORIZATION_EXPIRED
    return ReasonCode.AUTHORIZATION_INVALID


def _risk_state(
    policy: GovernancePolicy, risk: GovernanceRiskSnapshot
) -> tuple[RiskState, Decimal, list[ReasonCode]]:
    daily = risk.equity.daily_loss_fraction
    drawdown = risk.equity.drawdown_fraction
    reasons: list[ReasonCode] = []
    if drawdown <= policy.hard_drawdown_fraction:
        reasons.append(ReasonCode.HARD_DRAWDOWN)
        return RiskState.HARD_DRAWDOWN_KILL, Decimal("0"), reasons
    if daily <= policy.hard_daily_loss_fraction:
        reasons.append(ReasonCode.DAILY_HARD_LIMIT)
        return RiskState.DAILY_HALT, Decimal("0"), reasons
    if drawdown <= policy.soft_drawdown_fraction:
        reasons.append(ReasonCode.DRAWDOWN_SOFT_LIMIT)
        return RiskState.DRAWDOWN_DERISK, policy.derisk_risk_multiplier, reasons
    if daily <= policy.soft_daily_loss_fraction:
        reasons.append(ReasonCode.DAILY_SOFT_LIMIT)
        return RiskState.DAILY_DERISK, policy.derisk_risk_multiplier, reasons
    return RiskState.NORMAL, Decimal("1"), reasons


def _decision(
    *,
    policy: GovernancePolicy,
    request: GovernanceRequest,
    outcome: DecisionOutcome,
    reasons: list[ReasonCode],
    risk_state: RiskState,
    risk_multiplier: Decimal,
    authorization_valid: bool = False,
) -> GovernanceDecision:
    return GovernanceDecision(
        outcome=outcome,
        reason_codes=_unique_reasons(reasons),
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_hash=policy.policy_hash,
        input_snapshot_hash=request.evidence.input_snapshot_hash,
        evaluated_at=request.evaluated_at,
        risk_state=risk_state,
        risk_increasing_multiplier=risk_multiplier,
        human_authorization_id=(
            request.human_authorization.authorization_id
            if request.human_authorization is not None
            else None
        ),
        authorization_valid=authorization_valid,
    )


def _architecture_reasons(evidence: GovernanceEvidence) -> list[ReasonCode]:
    checks = (
        (evidence.evidence_fresh, ReasonCode.DATA_UNHEALTHY),
        (evidence.source_health_valid, ReasonCode.DATA_UNHEALTHY),
        (evidence.pit_provenance_valid, ReasonCode.PIT_PROVENANCE_INVALID),
        (evidence.model_role_admitted, ReasonCode.MODEL_ROLE_NOT_ADMITTED),
        (evidence.regime_support_valid, ReasonCode.REGIME_UNSUPPORTED),
        (evidence.liquidity_acceptable, ReasonCode.LIQUIDITY_UNACCEPTABLE),
        (evidence.spread_acceptable, ReasonCode.SPREAD_UNACCEPTABLE),
        (evidence.portfolio_exposure_valid, ReasonCode.POSITION_LIMIT),
        (evidence.correlated_exposure_valid, ReasonCode.CORRELATED_EXPOSURE_UNKNOWN),
        (evidence.reconciliation_healthy, ReasonCode.RECONCILIATION_UNHEALTHY),
        (evidence.oms_state_unambiguous, ReasonCode.OMS_STATE_AMBIGUOUS),
    )
    return [reason for value, reason in checks if value is not True]


def _exposure_reasons(policy: GovernancePolicy, request: GovernanceRequest) -> list[ReasonCode]:
    reasons: list[ReasonCode] = []
    for exposure in request.risk.proposed_asset_exposures.values():
        if exposure > policy.max_single_asset_fraction:
            reasons.append(ReasonCode.POSITION_LIMIT)
    group_limits = {
        limit.group_id: limit.max_fraction for limit in policy.aggregate_group_exposure_limits
    }
    for group in policy.correlated_exposure_groups:
        if group.group_id not in request.risk.proposed_group_exposures:
            continue
        proposed = request.risk.proposed_group_exposures[group.group_id]
        limit = group_limits.get(group.group_id)
        if limit is not None and proposed > limit:
            reasons.append(ReasonCode.CORRELATED_EXPOSURE_LIMIT)
        elif limit is None and request.evidence.correlated_exposure_valid is not True:
            reasons.append(ReasonCode.CORRELATED_EXPOSURE_UNKNOWN)
    return reasons


def evaluate_governance(policy: GovernancePolicy, request: GovernanceRequest) -> GovernanceDecision:
    """Evaluate a proposal without executing or changing any authoritative state."""

    risk_state, risk_multiplier, threshold_reasons = _risk_state(policy, request.risk)
    reasons = list(threshold_reasons)
    authorization_valid = authorization_is_valid(
        request.human_authorization,
        at=request.evaluated_at,
        action_type=request.action_type,
        policy_version=policy.policy_version,
    )

    if request.action_direction is ActionDirection.EMERGENCY_PROTECTIVE:
        reasons.append(ReasonCode.EMERGENCY_PROTECTIVE)
        if request.evidence.protective_trigger_valid is not True:
            reasons.append(ReasonCode.UNKNOWN_REQUIRED_INPUT)
            return _decision(
                policy=policy,
                request=request,
                outcome=DecisionOutcome.HARD_BLOCK,
                reasons=reasons,
                risk_state=risk_state,
                risk_multiplier=Decimal("0"),
            )
        return _decision(
            policy=policy,
            request=request,
            outcome=DecisionOutcome.DERISK_ONLY,
            reasons=reasons,
            risk_state=risk_state,
            risk_multiplier=Decimal("0"),
        )

    if request.action_direction is ActionDirection.RISK_REDUCING:
        reasons.append(ReasonCode.RISK_REDUCING_ACTION)
        return _decision(
            policy=policy,
            request=request,
            outcome=DecisionOutcome.DERISK_ONLY,
            reasons=reasons,
            risk_state=risk_state,
            risk_multiplier=Decimal("0"),
            authorization_valid=authorization_valid,
        )

    if request.action_direction is ActionDirection.RISK_NEUTRAL:
        reasons.append(ReasonCode.RISK_NEUTRAL_ACTION)
        if request.evidence.oms_state_unambiguous is not True:
            reasons.append(ReasonCode.OMS_STATE_AMBIGUOUS)
            return _decision(
                policy=policy,
                request=request,
                outcome=DecisionOutcome.HARD_BLOCK,
                reasons=reasons,
                risk_state=risk_state,
                risk_multiplier=risk_multiplier,
                authorization_valid=authorization_valid,
            )
        return _decision(
            policy=policy,
            request=request,
            outcome=DecisionOutcome.ALLOW_AUTONOMOUS,
            reasons=reasons,
            risk_state=risk_state,
            risk_multiplier=risk_multiplier,
            authorization_valid=authorization_valid,
        )

    if risk_state is RiskState.HARD_DRAWDOWN_KILL:
        return _decision(
            policy=policy,
            request=request,
            outcome=DecisionOutcome.HARD_BLOCK,
            reasons=reasons,
            risk_state=risk_state,
            risk_multiplier=Decimal("0"),
            authorization_valid=authorization_valid,
        )
    if risk_state is RiskState.DAILY_HALT:
        return _decision(
            policy=policy,
            request=request,
            outcome=DecisionOutcome.HARD_BLOCK,
            reasons=reasons,
            risk_state=risk_state,
            risk_multiplier=Decimal("0"),
            authorization_valid=authorization_valid,
        )

    if request.decision_impact in {
        DecisionImpact.STRATEGIC,
        DecisionImpact.POLICY_CHANGE,
    } or is_human_only_action(request.action_type):
        reasons.append(ReasonCode.STRATEGIC_HUMAN_ONLY)
        reasons.append(ReasonCode.HUMAN_APPROVAL_REQUIRED)
        if request.human_authorization is not None and not authorization_valid:
            reasons.append(
                _authorization_failure_reason(request.human_authorization, request.evaluated_at)
            )
        return _decision(
            policy=policy,
            request=request,
            outcome=DecisionOutcome.REQUIRE_HUMAN,
            reasons=reasons,
            risk_state=risk_state,
            risk_multiplier=Decimal("0"),
            authorization_valid=authorization_valid,
        )

    confidence = (
        request.evidence.calibrated_confidence.calibrated_confidence
        if request.evidence.calibrated_confidence is not None
        else None
    )
    if confidence is None:
        reasons.append(ReasonCode.CALIBRATION_MISSING)
        return _decision(
            policy=policy,
            request=request,
            outcome=DecisionOutcome.HARD_BLOCK,
            reasons=reasons,
            risk_state=risk_state,
            risk_multiplier=Decimal("0"),
            authorization_valid=authorization_valid,
        )
    if confidence < policy.medium_confidence_threshold:
        reasons.append(ReasonCode.CONFIDENCE_TOO_LOW)
        return _decision(
            policy=policy,
            request=request,
            outcome=DecisionOutcome.ABSTAIN,
            reasons=reasons,
            risk_state=risk_state,
            risk_multiplier=Decimal("0"),
            authorization_valid=authorization_valid,
        )
    if confidence < policy.high_confidence_threshold:
        reasons.append(ReasonCode.CONFIDENCE_TOO_LOW)
        if request.urgency_class is not TimingClass.URGENT:
            reasons.append(ReasonCode.HUMAN_APPROVAL_REQUIRED)
            outcome = DecisionOutcome.REQUIRE_HUMAN
        else:
            outcome = DecisionOutcome.ABSTAIN
        return _decision(
            policy=policy,
            request=request,
            outcome=outcome,
            reasons=reasons,
            risk_state=risk_state,
            risk_multiplier=Decimal("0"),
            authorization_valid=authorization_valid,
        )

    if (
        request.urgency_class is not TimingClass.URGENT
        and request.evidence.timing_evidence_valid is not True
    ):
        reasons.extend((ReasonCode.HUMAN_APPROVAL_REQUIRED,))
        return _decision(
            policy=policy,
            request=request,
            outcome=DecisionOutcome.REQUIRE_HUMAN,
            reasons=reasons,
            risk_state=risk_state,
            risk_multiplier=Decimal("0"),
            authorization_valid=authorization_valid,
        )

    if (
        request.evidence.expected_net_edge is None
        or request.evidence.conservative_all_in_cost is None
    ):
        reasons.append(ReasonCode.EDGE_EVIDENCE_UNKNOWN)
        return _decision(
            policy=policy,
            request=request,
            outcome=DecisionOutcome.HARD_BLOCK,
            reasons=reasons,
            risk_state=risk_state,
            risk_multiplier=Decimal("0"),
            authorization_valid=authorization_valid,
        )
    if request.evidence.expected_net_edge < (
        policy.conservative_cost_safety_factor * request.evidence.conservative_all_in_cost
    ):
        reasons.append(ReasonCode.EDGE_BELOW_COST)
        return _decision(
            policy=policy,
            request=request,
            outcome=DecisionOutcome.ABSTAIN,
            reasons=reasons,
            risk_state=risk_state,
            risk_multiplier=Decimal("0"),
            authorization_valid=authorization_valid,
        )

    reasons.extend(_architecture_reasons(request.evidence))
    reasons.extend(_exposure_reasons(policy, request))
    proposed_leverage = request.risk.proposed_gross_leverage
    if proposed_leverage is None:
        reasons.append(ReasonCode.UNKNOWN_REQUIRED_INPUT)
    elif proposed_leverage > policy.hard_max_gross_leverage:
        reasons.append(ReasonCode.LEVERAGE_LIMIT)
    elif proposed_leverage > policy.normal_max_gross_leverage:
        leverage_authorized = authorization_is_valid(
            request.human_authorization,
            at=request.evaluated_at,
            action_type="ENABLE_LEVERAGE",
            policy_version=policy.policy_version,
        )
        if not policy.future_leverage_enabled or not leverage_authorized:
            reasons.append(ReasonCode.LEVERAGE_DISABLED)
            if request.human_authorization is not None and not leverage_authorized:
                reasons.append(
                    _authorization_failure_reason(request.human_authorization, request.evaluated_at)
                )
            authorization_valid = authorization_valid and leverage_authorized
    if request.evidence.risk_kernel_approval is not True:
        reasons.append(
            ReasonCode.RISK_KERNEL_REJECTED
            if request.evidence.risk_kernel_approval is False
            else ReasonCode.RISK_KERNEL_UNKNOWN
        )

    if reasons and any(
        reason
        in {
            ReasonCode.DATA_UNHEALTHY,
            ReasonCode.PIT_PROVENANCE_INVALID,
            ReasonCode.MODEL_ROLE_NOT_ADMITTED,
            ReasonCode.REGIME_UNSUPPORTED,
            ReasonCode.LIQUIDITY_UNACCEPTABLE,
            ReasonCode.SPREAD_UNACCEPTABLE,
            ReasonCode.POSITION_LIMIT,
            ReasonCode.CORRELATED_EXPOSURE_LIMIT,
            ReasonCode.CORRELATED_EXPOSURE_UNKNOWN,
            ReasonCode.RECONCILIATION_UNHEALTHY,
            ReasonCode.OMS_STATE_AMBIGUOUS,
            ReasonCode.LEVERAGE_DISABLED,
            ReasonCode.LEVERAGE_LIMIT,
            ReasonCode.RISK_KERNEL_REJECTED,
            ReasonCode.RISK_KERNEL_UNKNOWN,
            ReasonCode.UNKNOWN_REQUIRED_INPUT,
        }
        for reason in reasons
    ):
        return _decision(
            policy=policy,
            request=request,
            outcome=DecisionOutcome.HARD_BLOCK,
            reasons=reasons,
            risk_state=risk_state,
            risk_multiplier=Decimal("0"),
            authorization_valid=authorization_valid,
        )

    reasons.append(ReasonCode.URGENT_HIGH_CONFIDENCE)
    return _decision(
        policy=policy,
        request=request,
        outcome=DecisionOutcome.ALLOW_AUTONOMOUS,
        reasons=reasons,
        risk_state=risk_state,
        risk_multiplier=risk_multiplier,
        authorization_valid=authorization_valid,
    )


def evaluate_live_activation(
    policy: GovernancePolicy, activation: LiveActivationInput
) -> GovernanceDecision:
    """Return a non-mutating Phase-10 activation decision.

    This function never changes a runtime mode, obtains credentials, or calls
    an OMS.  The policy default and missing capital therefore remain a hard
    block even when a caller supplies an approval-shaped object.
    """

    reasons: list[ReasonCode] = []
    authorization_valid = authorization_is_valid(
        activation.authorization,
        at=activation.evaluated_at,
        action_type="ENABLE_LIVE_CAPITAL",
        policy_version=policy.policy_version,
    )
    if not policy.live_capital_authorized:
        reasons.append(ReasonCode.LIVE_CAPITAL_DISABLED)
    if activation.planned_advisorai_capital is None:
        reasons.append(ReasonCode.PLANNED_CAPITAL_MISSING)
    if not activation.phase10_gate_passed:
        reasons.append(ReasonCode.HUMAN_APPROVAL_REQUIRED)
    if not authorization_valid:
        reasons.append(
            _authorization_failure_reason(activation.authorization, activation.evaluated_at)
        )
    request = GovernanceRequest(
        action_type="ENABLE_LIVE_CAPITAL",
        target="live_capital",
        action_direction=ActionDirection.RISK_INCREASING,
        certainty_class=CertaintyClass.HIGH,
        urgency_class=TimingClass.SLOW,
        decision_impact=DecisionImpact.POLICY_CHANGE,
        evidence=GovernanceEvidence(input_snapshot_hash=activation.input_snapshot_hash),
        risk=GovernanceRiskSnapshot(
            equity=EquitySnapshot(
                managed_equity=Decimal("1"),
                daily_start_equity=Decimal("1"),
                high_water_mark=Decimal("1"),
                realized_pnl=Decimal("0"),
                unrealized_pnl=Decimal("0"),
            )
        ),
        evaluated_at=activation.evaluated_at,
    )
    return _decision(
        policy=policy,
        request=request,
        outcome=DecisionOutcome.HARD_BLOCK if reasons else DecisionOutcome.REQUIRE_HUMAN,
        reasons=reasons or [ReasonCode.HUMAN_APPROVAL_REQUIRED],
        risk_state=RiskState.NORMAL,
        risk_multiplier=Decimal("0"),
        authorization_valid=authorization_valid,
    )
