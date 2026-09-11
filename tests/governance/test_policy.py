from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from advisorai.governance import (
    ActionDirection,
    ActorType,
    AuthorizationExpiryMode,
    CalibratedConfidenceEvidence,
    CertaintyClass,
    DecisionImpact,
    DecisionOutcome,
    EquitySnapshot,
    GovernanceEvidence,
    GovernancePolicy,
    GovernanceRequest,
    GovernanceRiskSnapshot,
    HumanAuthorization,
    LiveActivationInput,
    PositionSizingInput,
    ReasonCode,
    RiskState,
    TimingClass,
    apply_quarter_kelly,
    authorization_is_valid,
    evaluate_governance,
    evaluate_live_activation,
    load_governance_policy,
)

ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def policy() -> GovernancePolicy:
    return load_governance_policy(ROOT / "configs" / "governance" / "live-risk-policy-v1.yaml")


def _equity(*, managed: str = "100", daily_start: str = "100", high_water: str = "100"):
    return EquitySnapshot(
        managed_equity=Decimal(managed),
        daily_start_equity=Decimal(daily_start),
        high_water_mark=Decimal(high_water),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
    )


def _request(
    *,
    confidence: str = "0.95",
    urgency: TimingClass = TimingClass.URGENT,
    evidence_overrides: dict[str, object] | None = None,
    risk_overrides: dict[str, object] | None = None,
    action_direction: ActionDirection = ActionDirection.RISK_INCREASING,
    action_type: str = "INCREASE_EXPOSURE",
    impact: DecisionImpact = DecisionImpact.OPERATIONAL,
    authorization: HumanAuthorization | None = None,
) -> GovernanceRequest:
    evidence_values: dict[str, object] = {
        "input_snapshot_hash": "a" * 64,
        "calibrated_confidence": CalibratedConfidenceEvidence(
            contract_id="calibration-v1",
            calibrated_confidence=Decimal(confidence),
            evidence_hash="b" * 64,
        ),
        "expected_net_edge": Decimal("0.02"),
        "conservative_all_in_cost": Decimal("0.01"),
        "timing_evidence_valid": False,
        "evidence_fresh": True,
        "source_health_valid": True,
        "pit_provenance_valid": True,
        "model_role_admitted": True,
        "regime_support_valid": True,
        "liquidity_acceptable": True,
        "spread_acceptable": True,
        "portfolio_exposure_valid": True,
        "correlated_exposure_valid": True,
        "reconciliation_healthy": True,
        "oms_state_unambiguous": True,
        "risk_kernel_approval": True,
        "protective_trigger_valid": True,
    }
    if evidence_overrides:
        evidence_values.update(evidence_overrides)
    evidence = GovernanceEvidence(**evidence_values)
    risk_values: dict[str, object] = {
        "equity": _equity(),
        "proposed_gross_leverage": Decimal("1.00"),
        "proposed_asset_exposures": {"BTCUSDT": Decimal("0.10")},
        "proposed_group_exposures": {"CRYPTO_DIRECTIONAL": Decimal("0.10")},
    }
    if risk_overrides:
        risk_values.update(risk_overrides)
    return GovernanceRequest(
        action_type=action_type,
        target="BTCUSDT",
        action_direction=action_direction,
        certainty_class=(
            CertaintyClass.LOW
            if Decimal(confidence) < Decimal("0.70")
            else CertaintyClass.MEDIUM
            if Decimal(confidence) < Decimal("0.90")
            else CertaintyClass.HIGH
        ),
        urgency_class=urgency,
        decision_impact=impact,
        evidence=evidence,
        risk=GovernanceRiskSnapshot(**risk_values),
        human_authorization=authorization,
        evaluated_at=NOW,
    )


def _authorization(
    *,
    action_type: str = "ENABLE_LIVE_CAPITAL",
    actor_type: ActorType = ActorType.HUMAN,
    created_at: datetime = NOW,
    expires_at: datetime | None = NOW + timedelta(hours=1),
    expiry_mode: AuthorizationExpiryMode = AuthorizationExpiryMode.FIXED_EXPIRATION,
) -> HumanAuthorization:
    return HumanAuthorization(
        created_at=created_at,
        actor_type=actor_type,
        actor_identity="operator-1",
        action_type=action_type,
        target="live_capital",
        previous_value="disabled",
        approved_value="enabled",
        scope=("phase-10", "BTCUSDT", "ETHUSDT"),
        expires_at=expires_at,
        expiry_mode=expiry_mode,
        policy_version="human-governance-v1",
        repository_commit="e" * 40,
        reason="reviewed bounded authorization",
        evidence_refs=("evidence:review-1",),
    )


def test_policy_loads_frozen_defaults_and_hash(policy):
    assert policy.live_capital_authorized is False
    assert policy.initial_live_fraction_of_planned_allocation == Decimal("0.05")
    assert tuple(stage.fraction for stage in policy.allocation_stages) == (
        Decimal("0.05"),
        Decimal("0.10"),
        Decimal("0.20"),
        Decimal("0.35"),
    )
    assert policy.max_single_asset_fraction == Decimal("0.15")
    assert policy.normal_max_gross_leverage == Decimal("1.00")
    assert policy.hard_max_gross_leverage == Decimal("1.25")
    assert policy.future_leverage_enabled is False
    group = policy.group_for_instrument("ethusdt")
    assert group is not None
    assert group.group_id == "crypto_directional"
    assert len(policy.policy_hash) == 64
    with pytest.raises(ValidationError):
        policy.live_capital_authorized = True


def test_quarter_kelly_applies_caps_in_order_and_fails_closed(policy):
    result = apply_quarter_kelly(
        policy,
        PositionSizingInput(
            raw_kelly_fraction=Decimal("4.00"),
            volatility_cap_fraction=Decimal("0.50"),
            liquidity_cap_fraction=Decimal("0.40"),
            correlation_cap_fraction=Decimal("0.30"),
            risk_kernel_cap_fraction=Decimal("0.10"),
        ),
    )
    assert result.allowed is True
    assert result.quarter_kelly_fraction == Decimal("1.00")
    assert result.final_fraction == Decimal("0.10")
    assert result.applied_limits == (
        "volatility",
        "liquidity",
        "correlation",
        "single_asset",
        "risk_kernel",
    )
    missing = apply_quarter_kelly(policy, PositionSizingInput(raw_kelly_fraction=Decimal("0.10")))
    assert missing.allowed is False
    assert missing.reason == "POSITION_SIZING_INPUT_UNKNOWN"


def test_high_confidence_urgent_healthy_action_is_allowed(policy):
    decision = evaluate_governance(policy, _request())
    assert decision.outcome is DecisionOutcome.ALLOW_AUTONOMOUS
    assert decision.reason_codes == (ReasonCode.URGENT_HIGH_CONFIDENCE,)
    assert decision.risk_state is RiskState.NORMAL
    assert len(decision.decision_hash) == 64


def test_high_confidence_slow_action_requires_human(policy):
    decision = evaluate_governance(policy, _request(urgency=TimingClass.SLOW))
    assert decision.outcome is DecisionOutcome.REQUIRE_HUMAN
    assert ReasonCode.HUMAN_APPROVAL_REQUIRED in decision.reason_codes


def test_medium_confidence_boundaries_are_not_autonomous_risk(policy):
    urgent = evaluate_governance(policy, _request(confidence="0.899", urgency=TimingClass.URGENT))
    slow = evaluate_governance(policy, _request(confidence="0.899", urgency=TimingClass.SLOW))
    low = evaluate_governance(policy, _request(confidence="0.699"))
    assert urgent.outcome is DecisionOutcome.ABSTAIN
    assert slow.outcome is DecisionOutcome.REQUIRE_HUMAN
    assert low.outcome is DecisionOutcome.ABSTAIN
    assert ReasonCode.CONFIDENCE_TOO_LOW in urgent.reason_codes


def test_llm_confidence_without_typed_calibration_is_hard_blocked(policy):
    decision = evaluate_governance(
        policy,
        _request(
            evidence_overrides={
                "calibrated_confidence": None,
                "llm_reported_confidence": Decimal("0.99"),
            }
        ),
    )
    assert decision.outcome is DecisionOutcome.HARD_BLOCK
    assert ReasonCode.CALIBRATION_MISSING in decision.reason_codes


def test_risk_reduction_and_emergency_protection_do_not_need_alpha_confidence(policy):
    reduction = evaluate_governance(
        policy,
        _request(
            confidence="0.20",
            action_direction=ActionDirection.RISK_REDUCING,
            evidence_overrides={"calibrated_confidence": None},
        ),
    )
    emergency = evaluate_governance(
        policy,
        _request(
            confidence="0.20",
            action_direction=ActionDirection.EMERGENCY_PROTECTIVE,
            evidence_overrides={"calibrated_confidence": None},
        ),
    )
    assert reduction.outcome is DecisionOutcome.DERISK_ONLY
    assert emergency.outcome is DecisionOutcome.DERISK_ONLY


@pytest.mark.parametrize(
    ("equity", "expected_state", "reason"),
    [
        (_equity(managed="99.5"), RiskState.DAILY_DERISK, ReasonCode.DAILY_SOFT_LIMIT),
        (_equity(managed="99"), RiskState.DAILY_HALT, ReasonCode.DAILY_HARD_LIMIT),
        (
            _equity(managed="96", daily_start="96", high_water="100"),
            RiskState.DRAWDOWN_DERISK,
            ReasonCode.DRAWDOWN_SOFT_LIMIT,
        ),
        (
            _equity(managed="94", daily_start="94", high_water="100"),
            RiskState.HARD_DRAWDOWN_KILL,
            ReasonCode.HARD_DRAWDOWN,
        ),
    ],
)
def test_loss_and_drawdown_thresholds_are_inclusive(policy, equity, expected_state, reason):
    decision = evaluate_governance(policy, _request(risk_overrides={"equity": equity}))
    assert decision.risk_state is expected_state
    assert reason in decision.reason_codes
    if expected_state in {RiskState.DAILY_HALT, RiskState.HARD_DRAWDOWN_KILL}:
        assert decision.outcome is DecisionOutcome.HARD_BLOCK


def test_soft_loss_state_only_allows_exceptionally_strong_action_at_derisk_multiplier(policy):
    decision = evaluate_governance(
        policy,
        _request(risk_overrides={"equity": _equity(managed="99.5")}),
    )
    assert decision.outcome is DecisionOutcome.ALLOW_AUTONOMOUS
    assert decision.risk_increasing_multiplier == Decimal("0.50")
    assert ReasonCode.DAILY_SOFT_LIMIT in decision.reason_codes


def test_position_and_leverage_limits_fail_closed(policy):
    position = evaluate_governance(
        policy,
        _request(risk_overrides={"proposed_asset_exposures": {"BTCUSDT": Decimal("0.151")}}),
    )
    leverage = evaluate_governance(
        policy,
        _request(risk_overrides={"proposed_gross_leverage": Decimal("1.01")}),
    )
    absolute = evaluate_governance(
        policy,
        _request(risk_overrides={"proposed_gross_leverage": Decimal("1.25")}),
    )
    assert position.outcome is DecisionOutcome.HARD_BLOCK
    assert ReasonCode.POSITION_LIMIT in position.reason_codes
    assert leverage.outcome is DecisionOutcome.HARD_BLOCK
    assert ReasonCode.LEVERAGE_DISABLED in leverage.reason_codes
    assert absolute.outcome is DecisionOutcome.HARD_BLOCK
    assert ReasonCode.LEVERAGE_DISABLED in absolute.reason_codes


def test_unknown_safety_evidence_and_kernel_rejection_block(policy):
    unknown = evaluate_governance(
        policy, _request(evidence_overrides={"source_health_valid": None})
    )
    rejected = evaluate_governance(
        policy, _request(evidence_overrides={"risk_kernel_approval": False})
    )
    assert unknown.outcome is DecisionOutcome.HARD_BLOCK
    assert ReasonCode.DATA_UNHEALTHY in unknown.reason_codes
    assert rejected.outcome is DecisionOutcome.HARD_BLOCK
    assert ReasonCode.RISK_KERNEL_REJECTED in rejected.reason_codes


def test_human_only_action_and_agent_approval_cannot_self_authorize(policy):
    agent = _authorization(action_type="ENABLE_LEVERAGE", actor_type=ActorType.AGENT)
    decision = evaluate_governance(
        policy,
        _request(
            action_type="ENABLE_LEVERAGE",
            risk_overrides={"proposed_gross_leverage": Decimal("1.01")},
            authorization=agent,
        ),
    )
    assert decision.outcome is DecisionOutcome.REQUIRE_HUMAN
    assert decision.authorization_valid is False
    assert ReasonCode.STRATEGIC_HUMAN_ONLY in decision.reason_codes
    expired = _authorization(
        action_type="ENABLE_LEVERAGE",
        created_at=NOW - timedelta(hours=2),
        expires_at=NOW - timedelta(minutes=1),
    )
    expired_decision = evaluate_governance(
        policy,
        _request(
            action_type="ENABLE_LEVERAGE",
            risk_overrides={"proposed_gross_leverage": Decimal("1.01")},
            authorization=expired,
        ),
    )
    assert ReasonCode.AUTHORIZATION_EXPIRED in expired_decision.reason_codes


def test_human_authorization_is_hashed_scoped_and_expires():
    authorization = _authorization()
    assert len(authorization.authorization_hash) == 64
    assert authorization_is_valid(
        authorization, at=NOW + timedelta(minutes=30), action_type="ENABLE_LIVE_CAPITAL"
    )
    assert not authorization_is_valid(
        authorization, at=NOW + timedelta(hours=1), action_type="ENABLE_LIVE_CAPITAL"
    )
    assert not authorization_is_valid(authorization, at=NOW, action_type="ENABLE_LEVERAGE")
    persistent = _authorization(
        expiry_mode=AuthorizationExpiryMode.PERSISTENT_UNTIL_REVOKED,
        expires_at=None,
    )
    assert authorization_is_valid(
        persistent, at=NOW + timedelta(days=365), action_type="ENABLE_LIVE_CAPITAL"
    )
    assert not authorization_is_valid(
        persistent,
        at=NOW + timedelta(days=365),
        action_type="ENABLE_LIVE_CAPITAL",
        revoked_ids=(persistent.authorization_id,),
    )
    with pytest.raises(ValueError, match="authorization_hash"):
        HumanAuthorization.model_validate(
            authorization.model_dump(mode="json") | {"authorization_hash": "c" * 64}
        )


def test_live_activation_is_disabled_without_phase10_human_authority(policy):
    activation = LiveActivationInput(
        planned_advisorai_capital=None,
        phase10_gate_passed=False,
        evaluated_at=NOW,
        input_snapshot_hash="d" * 64,
    )
    decision = evaluate_live_activation(policy, activation)
    assert decision.outcome is DecisionOutcome.HARD_BLOCK
    assert ReasonCode.LIVE_CAPITAL_DISABLED in decision.reason_codes
    assert ReasonCode.PLANNED_CAPITAL_MISSING in decision.reason_codes

    agent_activation = activation.model_copy(
        update={
            "planned_advisorai_capital": Decimal("1000"),
            "phase10_gate_passed": True,
            "authorization": _authorization(actor_type=ActorType.AGENT),
        }
    )
    agent_decision = evaluate_live_activation(policy, agent_activation)
    assert agent_decision.outcome is DecisionOutcome.HARD_BLOCK
    assert ReasonCode.AUTHORIZATION_INVALID in agent_decision.reason_codes


def test_policy_rejects_attempts_to_enable_llm_authority():
    with pytest.raises(ValueError, match="LLM execution authority"):
        GovernancePolicy(
            policy_id="bad",
            policy_version="bad-v1",
            allocation_stages=({"stage": 0, "fraction": Decimal("0.05")},),
            llm_execution_authority=True,
        )
