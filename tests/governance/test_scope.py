from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from advisorai.governance import (
    ActorType,
    AuthorityClass,
    AuthorizationExpiryMode,
    CredentialCapability,
    CredentialCapabilitySet,
    DecisionOutcome,
    EligibilityTier,
    HumanAuthorization,
    HumanAuthorizationState,
    MarketType,
    ModelLifecycle,
    PositionDirection,
    ScopeAction,
    ScopeAssetClass,
    ScopeClass,
    ScopeDecisionOutcome,
    ScopeReasonCode,
    StrategyLifecycle,
    TradingCapability,
    TradingScopePolicy,
    TradingScopeRequest,
    evaluate_trading_scope,
    is_human_only_action,
    load_trading_scope_policy,
)

ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
REQUIRED_CREDENTIALS = CredentialCapabilitySet(
    enabled=(
        CredentialCapability.MARKET_DATA_READ,
        CredentialCapability.ACCOUNT_READ,
        CredentialCapability.ORDER_READ,
        CredentialCapability.ORDER_CREATE,
        CredentialCapability.ORDER_CANCEL,
    )
)


@pytest.fixture
def policy() -> TradingScopePolicy:
    return load_trading_scope_policy(ROOT / "configs" / "governance" / "trading-scope-v1.yaml")


def _authorization(
    action: str,
    *,
    actor_type: ActorType = ActorType.HUMAN,
    expired: bool = False,
) -> HumanAuthorization:
    created_at = NOW - timedelta(hours=2) if expired else NOW
    expires_at = NOW - timedelta(hours=1) if expired else NOW + timedelta(hours=1)
    return HumanAuthorization(
        created_at=created_at,
        actor_type=actor_type,
        actor_identity="operator-1",
        action_type=action,
        target="scope",
        previous_value="disabled",
        approved_value="enabled",
        scope=("phase-10",),
        expires_at=expires_at,
        expiry_mode=AuthorizationExpiryMode.FIXED_EXPIRATION,
        policy_version="human-governance-v1",
        repository_commit="e" * 40,
        reason="reviewed scope change",
    )


def _request(**overrides) -> TradingScopeRequest:
    values = {
        "action": ScopeAction.TRADE,
        "input_snapshot_hash": "a" * 64,
        "requested_tier": EligibilityTier.LIVE_ELIGIBLE,
        "instrument": "BTCUSDT",
        "asset_class": ScopeAssetClass.CRYPTO_SPOT,
        "market_type": MarketType.SPOT,
        "direction": PositionDirection.LONG,
        "venue": "reviewed-spot-venue",
        "venue_approved": True,
        "live_activation_permitted": True,
        "qualification_valid": True,
        "technical_gate_valid": True,
        "governance_outcome": DecisionOutcome.ALLOW_AUTONOMOUS,
        "risk_kernel_approved": True,
        "oms_state_unambiguous": True,
        "credential_capabilities": REQUIRED_CREDENTIALS,
        "planned_advisorai_capital": Decimal("100000"),
        "active_allocation_stage": 0,
        "authorized_capital_amount": Decimal("5000"),
        "capital_authorization_valid": True,
        "evaluated_at": NOW,
    }
    values.update(overrides)
    return TradingScopeRequest(**values)


def _activated_policy(policy: TradingScopePolicy) -> TradingScopePolicy:
    payload = policy.model_dump(mode="python", exclude={"content_hash"})
    payload["live_activation_permitted"] = True
    return TradingScopePolicy.model_validate(payload)


def test_scope_policy_freezes_v1_matrix_and_hash(policy):
    assert policy.live_asset_class is ScopeAssetClass.CRYPTO_SPOT
    assert policy.live_symbols == ("BTCUSDT", "ETHUSDT")
    assert policy.live_directions == (PositionDirection.LONG, PositionDirection.FLAT)
    assert policy.live_activation_permitted is False
    assert policy.execution_authority is False
    assert set(policy.disabled_capabilities) == set(TradingCapability)
    assert set(policy.required_live_credential_capabilities) == {
        CredentialCapability.MARKET_DATA_READ,
        CredentialCapability.ACCOUNT_READ,
        CredentialCapability.ORDER_READ,
        CredentialCapability.ORDER_CREATE,
        CredentialCapability.ORDER_CANCEL,
    }
    assert len(policy.policy_hash) == 64
    assert policy.authority_for(ScopeAction.PROMOTE_MODEL).llm_may_execute is False
    assert (
        policy.authority_for(ScopeAction.CHANGE_CAPITAL_ALLOCATION).authority_class
        is AuthorityClass.HUMAN_ONLY
    )
    with pytest.raises(ValueError, match="execution authority"):
        TradingScopePolicy(
            policy_id="bad",
            policy_version="bad-v1",
            human_governance_policy_version="human-governance-v1",
            live_symbols=("BTCUSDT", "ETHUSDT"),
            live_directions=(PositionDirection.LONG, PositionDirection.FLAT),
            disabled_market_types=tuple(policy.disabled_market_types),
            disabled_capabilities=tuple(policy.disabled_capabilities),
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
        ScopeAction.CHANGE_CAPITAL_ALLOCATION,
    ):
        assert is_human_only_action(action.value)


@pytest.mark.parametrize(
    ("instrument", "direction"),
    [
        ("BTCUSDT", PositionDirection.LONG),
        ("BTCUSDT", PositionDirection.FLAT),
        ("ETHUSDT", PositionDirection.LONG),
        ("ETHUSDT", PositionDirection.FLAT),
    ],
)
def test_live_btc_and_eth_spot_long_flat_pass_only_when_activation_is_enabled(
    policy, instrument, direction
):
    decision = evaluate_trading_scope(
        _activated_policy(policy), _request(instrument=instrument, direction=direction)
    )
    assert decision.scope_class is ScopeClass.LIVE_ELIGIBLE
    assert decision.outcome is ScopeDecisionOutcome.ALLOW_WITHIN_GOVERNANCE
    assert decision.authority_class is AuthorityClass.AUTONOMOUS_WITHIN_LIMITS
    assert decision.execution_authority is False
    assert len(decision.decision_hash) == 64


def test_live_scope_default_is_fail_closed(policy):
    decision = evaluate_trading_scope(policy, _request())
    assert decision.outcome is ScopeDecisionOutcome.HARD_BLOCK
    assert ScopeReasonCode.LIVE_DISABLED in decision.reason_codes
    assert ScopeReasonCode.LIVE_CAPITAL_DISABLED in decision.reason_codes


@pytest.mark.parametrize(
    ("market_type", "reason"),
    [
        (MarketType.MARGIN, ScopeReasonCode.MARGIN_DISABLED),
        (MarketType.FUTURES, ScopeReasonCode.FUTURES_DISABLED),
        (MarketType.PERPETUAL, ScopeReasonCode.PERPETUALS_DISABLED),
        (MarketType.OPTIONS, ScopeReasonCode.OPTIONS_DISABLED),
        (MarketType.CFDS, ScopeReasonCode.CFDS_DISABLED),
        (MarketType.LEVERAGED_TOKENS, ScopeReasonCode.LEVERAGED_TOKENS_DISABLED),
        (MarketType.SYNTHETIC_LEVERAGED_EXPOSURE, ScopeReasonCode.SYNTHETIC_LEVERAGE_DISABLED),
    ],
)
def test_derivatives_margin_and_synthetic_leverage_are_disabled(policy, market_type, reason):
    decision = evaluate_trading_scope(policy, _request(market_type=market_type))
    assert decision.scope_class is ScopeClass.DISABLED
    assert decision.outcome is ScopeDecisionOutcome.DISABLED
    assert reason in decision.reason_codes


def test_short_is_disabled_even_for_high_quality_inputs(policy):
    decision = evaluate_trading_scope(policy, _request(direction=PositionDirection.SHORT))
    assert decision.outcome is ScopeDecisionOutcome.DISABLED
    assert ScopeReasonCode.SHORTS_DISABLED in decision.reason_codes


@pytest.mark.parametrize(
    "action",
    [
        ScopeAction.SHORT_BTC,
        ScopeAction.TRADE_SOL,
        ScopeAction.TRADE_EQUITY,
        ScopeAction.TRADE_OPTION,
        ScopeAction.TRADE_FUTURE,
        ScopeAction.USE_MARGIN,
    ],
)
def test_direct_unsupported_trade_actions_are_hard_blocked(policy, action):
    decision = evaluate_trading_scope(policy, _request(action=action))
    assert decision.outcome is ScopeDecisionOutcome.HARD_BLOCK
    assert decision.scope_class is ScopeClass.DISABLED


@pytest.mark.parametrize(
    "action",
    [
        ScopeAction.WITHDRAW,
        ScopeAction.WITHDRAW_CRYPTO,
        ScopeAction.WITHDRAW_FIAT,
        ScopeAction.EXTERNAL_TRANSFER,
        ScopeAction.TRANSFER_FUNDS,
        ScopeAction.INTERNAL_ACCOUNT_TRANSFER,
        ScopeAction.CHANGE_WITHDRAWAL_ADDRESS,
        ScopeAction.WHITELIST_WITHDRAWAL_ADDRESS,
        ScopeAction.API_KEY_ADMINISTRATION,
    ],
)
def test_cash_transfer_and_security_actions_are_system_forbidden(policy, action):
    decision = evaluate_trading_scope(policy, _request(action=action))
    assert decision.scope_class is ScopeClass.SYSTEM_FORBIDDEN
    assert decision.outcome is ScopeDecisionOutcome.SYSTEM_FORBIDDEN
    assert decision.execution_authority is False


def test_credential_contract_requires_minimum_read_and_order_capabilities(policy):
    missing = evaluate_trading_scope(
        _activated_policy(policy), _request(credential_capabilities=CredentialCapabilitySet())
    )
    excessive = evaluate_trading_scope(
        _activated_policy(policy),
        _request(
            credential_capabilities=CredentialCapabilitySet(
                enabled=REQUIRED_CREDENTIALS.enabled + (CredentialCapability.WITHDRAW_CRYPTO,)
            )
        ),
    )
    assert ScopeReasonCode.CREDENTIAL_CAPABILITY_MISSING in missing.reason_codes
    assert ScopeReasonCode.CREDENTIAL_CAPABILITY_FORBIDDEN in excessive.reason_codes


def test_research_and_paper_are_separate_from_live_scope(policy):
    research = evaluate_trading_scope(
        policy,
        _request(
            action=ScopeAction.RESEARCH,
            requested_tier=EligibilityTier.RESEARCH_ELIGIBLE,
            instrument="SOLUSDT",
            asset_class=ScopeAssetClass.EQUITY,
            market_type=MarketType.FUTURES,
            direction=PositionDirection.SHORT,
        ),
    )
    paper = evaluate_trading_scope(
        policy,
        _request(
            action=ScopeAction.RUN_PAPER_STRATEGY,
            requested_tier=EligibilityTier.PAPER_ELIGIBLE,
            instrument="SOLUSDT",
            paper_qualified=True,
        ),
    )
    assert research.scope_class is ScopeClass.RESEARCH_ONLY
    assert research.outcome is ScopeDecisionOutcome.RESEARCH_ONLY
    assert paper.scope_class is ScopeClass.PAPER_ELIGIBLE
    assert paper.outcome is ScopeDecisionOutcome.PAPER_ELIGIBLE
    assert paper.execution_authority is False


def test_protective_reduction_of_existing_long_is_allowed_without_new_risk(policy):
    allowed = evaluate_trading_scope(
        policy,
        _request(
            action=ScopeAction.REDUCE_RISK,
            direction=PositionDirection.FLAT,
            existing_long_position=True,
            deterministic_trigger_valid=True,
        ),
    )
    short_loophole = evaluate_trading_scope(
        policy,
        _request(
            action=ScopeAction.REDUCE_RISK,
            direction=PositionDirection.SHORT,
            existing_long_position=True,
            creates_net_negative_exposure=True,
            deterministic_trigger_valid=True,
        ),
    )
    assert allowed.outcome is ScopeDecisionOutcome.ALLOW_WITHIN_GOVERNANCE
    assert allowed.authority_class is AuthorityClass.AUTONOMOUS_RISK_REDUCTION
    assert short_loophole.outcome is ScopeDecisionOutcome.HARD_BLOCK
    assert ScopeReasonCode.NEGATIVE_EXPOSURE_FORBIDDEN in short_loophole.reason_codes


def test_unsafe_order_cancellation_is_deterministic_protection(policy):
    decision = evaluate_trading_scope(
        policy,
        _request(
            action=ScopeAction.CANCEL_UNSAFE_ORDER,
            deterministic_trigger_valid=True,
            oms_state_unambiguous=True,
            risk_kernel_approved=True,
        ),
    )
    assert decision.outcome is ScopeDecisionOutcome.ALLOW_WITHIN_GOVERNANCE


def test_new_asset_venue_model_and_strategy_require_human_and_technical_gate(policy):
    cases = [
        (ScopeAction.ADD_ASSET, ScopeAction.ADD_ASSET, {}),
        (ScopeAction.ADD_BROKER_OR_VENUE, ScopeAction.ADD_BROKER_OR_VENUE, {}),
        (
            ScopeAction.PROMOTE_MODEL,
            ScopeAction.PROMOTE_MODEL,
            {"formal_evidence_gate_valid": True},
        ),
        (
            ScopeAction.PROMOTE_STRATEGY,
            ScopeAction.PROMOTE_STRATEGY,
            {"formal_evidence_gate_valid": True},
        ),
    ]
    for action, authorization_action, extra in cases:
        missing = evaluate_trading_scope(
            policy, _request(action=action, technical_gate_valid=False, **extra)
        )
        valid = evaluate_trading_scope(
            policy,
            _request(
                action=action,
                human_authorization=_authorization(authorization_action.value),
                technical_gate_valid=True,
                qualification_valid=True,
                **extra,
            ),
        )
        assert missing.outcome is ScopeDecisionOutcome.REQUIRE_HUMAN_AND_TECHNICAL_GATE
        assert valid.outcome is ScopeDecisionOutcome.ALLOW_HUMAN_GATED
        assert valid.human_authorization_state is HumanAuthorizationState.VALID


def test_capital_threshold_changes_are_human_only_not_agent_approved(policy):
    missing = evaluate_trading_scope(
        policy,
        _request(action=ScopeAction.CHANGE_CAPITAL_ALLOCATION),
    )
    valid = evaluate_trading_scope(
        policy,
        _request(
            action=ScopeAction.CHANGE_CAPITAL_ALLOCATION,
            human_authorization=_authorization(ScopeAction.CHANGE_CAPITAL_ALLOCATION.value),
            technical_gate_valid=False,
        ),
    )
    assert missing.outcome is ScopeDecisionOutcome.REQUIRE_HUMAN
    assert valid.outcome is ScopeDecisionOutcome.ALLOW_HUMAN_GATED


def test_hard_kill_resume_requires_human_and_technical_recovery(policy):
    decision = evaluate_trading_scope(
        policy,
        _request(
            action=ScopeAction.RESUME_AFTER_HARD_KILL,
            technical_gate_valid=False,
        ),
    )
    assert decision.outcome is ScopeDecisionOutcome.REQUIRE_HUMAN_AND_TECHNICAL_GATE
    assert ScopeReasonCode.HARD_KILL_ACTIVE in decision.reason_codes
    assert ScopeReasonCode.RESUME_AUTHORIZATION_REQUIRED in decision.reason_codes


def test_agent_or_expired_authorization_cannot_satisfy_gate(policy):
    agent = evaluate_trading_scope(
        policy,
        _request(
            action=ScopeAction.PROMOTE_MODEL,
            formal_evidence_gate_valid=True,
            human_authorization=_authorization(
                ScopeAction.PROMOTE_MODEL.value, actor_type=ActorType.AGENT
            ),
            technical_gate_valid=True,
        ),
    )
    expired = evaluate_trading_scope(
        policy,
        _request(
            action=ScopeAction.PROMOTE_MODEL,
            formal_evidence_gate_valid=True,
            human_authorization=_authorization(ScopeAction.PROMOTE_MODEL.value, expired=True),
            technical_gate_valid=True,
        ),
    )
    assert agent.outcome is ScopeDecisionOutcome.HARD_BLOCK
    assert ScopeReasonCode.HUMAN_ACTOR_REQUIRED in agent.reason_codes
    assert expired.outcome is ScopeDecisionOutcome.REQUIRE_HUMAN
    assert expired.human_authorization_state is HumanAuthorizationState.EXPIRED
    assert ScopeReasonCode.HUMAN_AUTHORIZATION_EXPIRED in expired.reason_codes


def test_high_confidence_cannot_override_unauthorized_scope(policy):
    sol = evaluate_trading_scope(
        policy,
        _request(action=ScopeAction.TRADE_SOL, input_snapshot_hash="b" * 64),
    )
    perpetual = evaluate_trading_scope(
        policy,
        _request(market_type=MarketType.PERPETUAL, input_snapshot_hash="c" * 64),
    )
    assert sol.outcome is ScopeDecisionOutcome.HARD_BLOCK
    assert perpetual.outcome is ScopeDecisionOutcome.DISABLED


def test_risk_limit_tightening_is_allowed_but_loosening_is_gated(policy):
    tightened = evaluate_trading_scope(
        policy,
        _request(
            action=ScopeAction.CHANGE_POSITION_LIMIT,
            current_risk_limit=Decimal("0.15"),
            proposed_risk_limit=Decimal("0.10"),
            risk_kernel_approved=True,
            oms_state_unambiguous=True,
        ),
    )
    loosened = evaluate_trading_scope(
        policy,
        _request(
            action=ScopeAction.CHANGE_POSITION_LIMIT,
            current_risk_limit=Decimal("0.10"),
            proposed_risk_limit=Decimal("0.15"),
            human_authorization=_authorization(ScopeAction.RELAX_RISK_LIMITS.value),
            technical_gate_valid=True,
            qualification_valid=True,
        ),
    )
    over_ceiling = evaluate_trading_scope(
        policy,
        _request(
            action=ScopeAction.CHANGE_POSITION_LIMIT,
            current_risk_limit=Decimal("0.15"),
            proposed_risk_limit=Decimal("0.20"),
        ),
    )
    assert tightened.outcome is ScopeDecisionOutcome.ALLOW_WITHIN_GOVERNANCE
    assert loosened.outcome is ScopeDecisionOutcome.ALLOW_HUMAN_GATED
    assert over_ceiling.outcome is ScopeDecisionOutcome.HARD_BLOCK


def test_live_model_and_strategy_lifecycle_must_be_admitted_or_promoted(policy):
    not_admitted = evaluate_trading_scope(
        _activated_policy(policy),
        _request(
            model_lifecycle=ModelLifecycle.CHALLENGER,
            strategy_lifecycle=StrategyLifecycle.PAPER,
        ),
    )
    admitted = evaluate_trading_scope(
        _activated_policy(policy),
        _request(
            model_lifecycle=ModelLifecycle.ADMITTED,
            strategy_lifecycle=StrategyLifecycle.PROMOTED,
        ),
    )
    assert not_admitted.outcome is ScopeDecisionOutcome.HARD_BLOCK
    assert ScopeReasonCode.MODEL_NOT_ADMITTED in not_admitted.reason_codes
    assert ScopeReasonCode.STRATEGY_NOT_PROMOTED in not_admitted.reason_codes
    assert admitted.outcome is ScopeDecisionOutcome.ALLOW_WITHIN_GOVERNANCE
