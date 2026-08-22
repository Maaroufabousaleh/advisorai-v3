from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from advisorai.governance import (
    ActorType,
    AuthorizationExpiryMode,
    DecisionOutcome,
    HumanAuthorization,
    MarketType,
    PositionDirection,
    ScopeAction,
    ScopeClass,
    ScopeDecisionOutcome,
    ScopeReasonCode,
    TradingScopePolicy,
    TradingScopeRequest,
    evaluate_trading_scope,
    is_human_only_action,
    load_trading_scope_policy,
)

ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def policy() -> TradingScopePolicy:
    return load_trading_scope_policy(ROOT / "configs" / "governance" / "trading-scope-v1.yaml")


def _authorization(action: str, *, actor_type: ActorType = ActorType.HUMAN) -> HumanAuthorization:
    return HumanAuthorization(
        created_at=NOW,
        actor_type=actor_type,
        actor_identity="operator-1",
        action_type=action,
        target="scope",
        previous_value="disabled",
        approved_value="enabled",
        scope=("phase-10",),
        expires_at=NOW + timedelta(hours=1),
        expiry_mode=AuthorizationExpiryMode.FIXED_EXPIRATION,
        policy_version="human-governance-v1",
        repository_commit="e" * 40,
        reason="reviewed scope change",
    )


def _request(**overrides) -> TradingScopeRequest:
    values = {
        "action": ScopeAction.TRADE,
        "input_snapshot_hash": "a" * 64,
        "instrument": "BTCUSDT",
        "market_type": MarketType.SPOT,
        "direction": PositionDirection.LONG,
        "venue": "reviewed-spot-venue",
        "venue_approved": True,
        "live_activation_permitted": True,
        "qualification_valid": True,
        "governance_outcome": DecisionOutcome.ALLOW_AUTONOMOUS,
        "risk_kernel_approved": True,
        "oms_state_unambiguous": True,
        "evaluated_at": NOW,
    }
    values.update(overrides)
    return TradingScopeRequest(**values)


def _activated_policy(policy: TradingScopePolicy) -> TradingScopePolicy:
    payload = policy.model_dump(mode="python", exclude={"content_hash"})
    payload["live_activation_permitted"] = True
    return TradingScopePolicy.model_validate(payload)


def test_scope_policy_freezes_v1_matrix_and_hash(policy):
    assert policy.live_symbols == ("BTCUSDT", "ETHUSDT")
    assert policy.live_directions == (PositionDirection.LONG, PositionDirection.FLAT)
    assert policy.live_activation_permitted is False
    assert policy.execution_authority is False
    assert set(policy.disabled_market_types) == {
        MarketType.MARGIN,
        MarketType.FUTURES,
        MarketType.PERPETUAL,
        MarketType.OPTIONS,
        MarketType.LEVERAGED_TOKENS,
    }
    assert len(policy.policy_hash) == 64
    with pytest.raises(ValueError, match="execution authority"):
        TradingScopePolicy(
            policy_id="bad",
            policy_version="bad-v1",
            human_governance_policy_version="human-governance-v1",
            live_symbols=("BTCUSDT", "ETHUSDT"),
            live_directions=(PositionDirection.LONG, PositionDirection.FLAT),
            disabled_market_types=tuple(policy.disabled_market_types),
            system_forbidden_actions=tuple(policy.system_forbidden_actions),
            human_gate_actions=tuple(policy.human_gate_actions),
            execution_authority=True,
        )


def test_scope_gate_actions_are_human_only_actions():
    for action in (
        ScopeAction.ADD_ASSET,
        ScopeAction.ADD_BROKER_OR_VENUE,
        ScopeAction.RELAX_RISK_LIMITS,
        ScopeAction.RESUME_AFTER_HARD_KILL,
    ):
        assert is_human_only_action(action.value)


def test_live_btc_and_eth_spot_long_flat_are_scope_eligible(policy):
    active_policy = _activated_policy(policy)
    btc = evaluate_trading_scope(active_policy, _request())
    eth = evaluate_trading_scope(
        active_policy,
        _request(instrument="ETHUSDT", direction=PositionDirection.FLAT),
    )
    assert btc.scope_class is ScopeClass.LIVE_ELIGIBLE
    assert btc.outcome is ScopeDecisionOutcome.ALLOW_AUTONOMOUS
    assert eth.outcome is ScopeDecisionOutcome.ALLOW_AUTONOMOUS
    assert btc.execution_authority is False
    assert len(btc.decision_hash) == 64


def test_default_scope_requires_live_activation_and_does_not_grant_it(policy):
    decision = evaluate_trading_scope(policy, _request(live_activation_permitted=False))
    assert decision.outcome is ScopeDecisionOutcome.HARD_BLOCK
    assert ScopeReasonCode.LIVE_CAPITAL_DISABLED in decision.reason_codes


@pytest.mark.parametrize(
    ("market_type", "reason"),
    [
        (MarketType.MARGIN, ScopeReasonCode.MARGIN_DISABLED),
        (MarketType.FUTURES, ScopeReasonCode.FUTURES_DISABLED),
        (MarketType.PERPETUAL, ScopeReasonCode.PERPETUALS_DISABLED),
        (MarketType.OPTIONS, ScopeReasonCode.OPTIONS_DISABLED),
        (MarketType.LEVERAGED_TOKENS, ScopeReasonCode.LEVERAGED_TOKENS_DISABLED),
    ],
)
def test_leverage_and_derivative_market_types_are_disabled(policy, market_type, reason):
    decision = evaluate_trading_scope(policy, _request(market_type=market_type))
    assert decision.scope_class is ScopeClass.DISABLED
    assert decision.outcome is ScopeDecisionOutcome.DISABLED
    assert reason in decision.reason_codes


def test_shorts_are_disabled(policy):
    decision = evaluate_trading_scope(policy, _request(direction=PositionDirection.SHORT))
    assert decision.scope_class is ScopeClass.DISABLED
    assert decision.outcome is ScopeDecisionOutcome.DISABLED
    assert ScopeReasonCode.SHORTS_DISABLED in decision.reason_codes


@pytest.mark.parametrize(
    "action",
    [ScopeAction.WITHDRAW, ScopeAction.EXTERNAL_TRANSFER, ScopeAction.API_KEY_ADMINISTRATION],
)
def test_system_forbidden_actions_are_always_forbidden(policy, action):
    decision = evaluate_trading_scope(policy, _request(action=action))
    assert decision.scope_class is ScopeClass.SYSTEM_FORBIDDEN
    assert decision.outcome is ScopeDecisionOutcome.SYSTEM_FORBIDDEN
    assert decision.execution_authority is False


@pytest.mark.parametrize(
    "action",
    [
        ScopeAction.ADD_ASSET,
        ScopeAction.ADD_BROKER_OR_VENUE,
        ScopeAction.ENABLE_LEVERAGE,
        ScopeAction.PROMOTE_MODEL,
        ScopeAction.PROMOTE_STRATEGY,
        ScopeAction.RELAX_RISK_LIMITS,
        ScopeAction.RESUME_AFTER_HARD_KILL,
    ],
)
def test_human_technical_gate_actions_require_both_authorities(policy, action):
    missing = evaluate_trading_scope(policy, _request(action=action))
    valid = evaluate_trading_scope(
        policy,
        _request(
            action=action,
            human_authorization=_authorization(action.value),
            technical_gate_valid=True,
        ),
    )
    assert missing.outcome is ScopeDecisionOutcome.REQUIRE_HUMAN_TECHNICAL_GATE
    assert ScopeReasonCode.HUMAN_AUTHORIZATION_REQUIRED in missing.reason_codes
    assert valid.outcome is ScopeDecisionOutcome.ALLOW_HUMAN_GATED
    assert valid.authorization_valid is True


def test_new_asset_and_unapproved_venue_do_not_silently_become_live(policy):
    new_asset = evaluate_trading_scope(policy, _request(instrument="SOLUSDT"))
    approved_new_asset = evaluate_trading_scope(
        policy,
        _request(
            instrument="SOLUSDT",
            human_authorization=_authorization(ScopeAction.ADD_ASSET.value),
            technical_gate_valid=True,
        ),
    )
    new_venue = evaluate_trading_scope(
        policy,
        _request(venue_approved=False),
    )
    approved_new_venue = evaluate_trading_scope(
        policy,
        _request(
            human_authorization=_authorization(ScopeAction.ADD_BROKER_OR_VENUE.value),
            technical_gate_valid=True,
            venue_approved=False,
        ),
    )
    assert new_asset.scope_class is ScopeClass.HUMAN_TECHNICAL_GATE
    assert new_asset.outcome is ScopeDecisionOutcome.REQUIRE_HUMAN_TECHNICAL_GATE
    assert ScopeReasonCode.NEW_ASSET in new_asset.reason_codes
    assert approved_new_asset.outcome is ScopeDecisionOutcome.ALLOW_HUMAN_GATED
    assert new_venue.outcome is ScopeDecisionOutcome.REQUIRE_HUMAN_TECHNICAL_GATE
    assert approved_new_venue.outcome is ScopeDecisionOutcome.ALLOW_HUMAN_GATED


def test_wrong_human_gate_authorization_cannot_approve_new_asset(policy):
    decision = evaluate_trading_scope(
        policy,
        _request(
            instrument="SOLUSDT",
            human_authorization=_authorization(ScopeAction.ADD_BROKER_OR_VENUE.value),
            technical_gate_valid=True,
        ),
    )
    assert decision.outcome is ScopeDecisionOutcome.REQUIRE_HUMAN_TECHNICAL_GATE
    assert decision.authorization_valid is False


def test_agent_authorization_cannot_satisfy_human_gate(policy):
    decision = evaluate_trading_scope(
        policy,
        _request(
            action=ScopeAction.PROMOTE_MODEL,
            human_authorization=_authorization(
                ScopeAction.PROMOTE_MODEL.value,
                actor_type=ActorType.AGENT,
            ),
            technical_gate_valid=True,
        ),
    )
    assert decision.outcome is ScopeDecisionOutcome.REQUIRE_HUMAN_TECHNICAL_GATE
    assert decision.authorization_valid is False


def test_research_outside_live_scope_is_explicitly_research_only(policy):
    decision = evaluate_trading_scope(
        policy,
        _request(
            action=ScopeAction.RESEARCH,
            instrument="SOLUSDT",
            market_type=MarketType.FUTURES,
            direction=PositionDirection.SHORT,
        ),
    )
    assert decision.scope_class is ScopeClass.RESEARCH_ONLY
    assert decision.outcome is ScopeDecisionOutcome.RESEARCH_ONLY
    assert decision.execution_authority is False


def test_protective_actions_are_autonomous_only_inside_deterministic_controls(policy):
    allowed = evaluate_trading_scope(
        policy,
        _request(
            action=ScopeAction.EMERGENCY_PROTECTIVE,
            instrument="SOLUSDT",
            deterministic_trigger_valid=True,
            risk_kernel_approved=True,
            oms_state_unambiguous=True,
        ),
    )
    blocked = evaluate_trading_scope(
        policy,
        _request(
            action=ScopeAction.REDUCE_RISK,
            deterministic_trigger_valid=True,
            risk_kernel_approved=False,
            oms_state_unambiguous=True,
        ),
    )
    assert allowed.scope_class is ScopeClass.AUTONOMOUS_PROTECTIVE
    assert allowed.outcome is ScopeDecisionOutcome.ALLOW_AUTONOMOUS
    assert blocked.outcome is ScopeDecisionOutcome.HARD_BLOCK
    assert ScopeReasonCode.RISK_KERNEL_REJECTED in blocked.reason_codes


def test_live_trade_requires_upstream_governance_and_risk_kernel(policy):
    decision = evaluate_trading_scope(
        policy,
        _request(
            governance_outcome=DecisionOutcome.HARD_BLOCK,
            risk_kernel_approved=False,
        ),
    )
    assert decision.outcome is ScopeDecisionOutcome.HARD_BLOCK
    assert ScopeReasonCode.GOVERNANCE_DECISION_REQUIRED in decision.reason_codes
    assert ScopeReasonCode.RISK_KERNEL_REJECTED in decision.reason_codes
