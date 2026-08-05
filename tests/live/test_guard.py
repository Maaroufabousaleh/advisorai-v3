from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

from advisorai.contracts import (
    Order,
    RiskDecision,
    RiskLimit,
    RiskOutcome,
    RiskPolicy,
)
from advisorai.execution import AccountState, RiskMarketState
from advisorai.live import (
    ControlledLiveOrderGuard,
    LiveAuthorization,
    LiveControlPlane,
    LiveOperatingState,
    LiveReadinessGate,
    OfflineSafetyCheck,
)
from advisorai.soak import SoakGate


def _offline():
    return OfflineSafetyCheck(
        api_llm_stopped=True,
        hermes_stopped=True,
        browser_stopped=True,
        research_workers_stopped=True,
        cancel_exit_reconcile_risk_operational=True,
    )


def _soak():
    return SoakGate(
        calendar_days=60,
        decision_count=100,
        trade_count=20,
        adverse_scenarios_seen=("venue_outage",),
        resources_stable=True,
        unresolved_reconciliation=False,
        unresolved_safety_incident=False,
        net_utility_after_costs=Decimal("1"),
        passed=True,
    )


def test_live_gate_requires_approval_and_clean_reconciliation(timestamp):
    gate = LiveReadinessGate().evaluate(
        soak=_soak(),
        authorization=None,
        current_reconciliation_clean=False,
        offline_safety=_offline(),
    )
    assert not gate.passed
    assert "explicit_human_authorization_missing" in gate.reasons
    authorization = LiveAuthorization(
        human_approver="human",
        approved_at=timestamp,
        allowed_instruments=("crypto:BTC-USDT:approved-venue:spot",),
        fixed_loss_budget=Decimal("100"),
        max_order_notional=Decimal("100"),
        risk_policy_hash="a" * 64,
        rollback_condition="any unexplained reconciliation discrepancy",
        no_simultaneous_expansion=True,
        ai_services_can_be_stopped=True,
    )
    assert (
        LiveReadinessGate()
        .evaluate(
            soak=_soak(),
            authorization=authorization,
            current_reconciliation_clean=True,
            offline_safety=_offline(),
        )
        .passed
    )


def test_live_readiness_accepts_an_explicit_evaluation_cutoff(timestamp):
    authorization = LiveAuthorization(
        human_approver="human",
        approved_at=timestamp,
        expires_at=timestamp + timedelta(hours=1),
        allowed_instruments=("crypto:BTC-USDT:approved-venue:spot",),
        fixed_loss_budget=Decimal("100"),
        max_order_notional=Decimal("100"),
        risk_policy_hash="a" * 64,
        rollback_condition="reconciliation discrepancy",
        no_simultaneous_expansion=True,
        ai_services_can_be_stopped=True,
    )
    result = LiveReadinessGate().evaluate(
        soak=_soak(),
        authorization=authorization,
        current_reconciliation_clean=True,
        offline_safety=_offline(),
        evaluation_at=timestamp + timedelta(hours=2),
    )
    assert not result.passed
    assert "live_authorization_expired_or_not_yet_active" in result.reasons


def test_controlled_live_order_guard_binds_policy_and_authoritative_state(btc_usdt, timestamp):
    account = AccountState(cash=Decimal("1000"))
    policy = RiskPolicy(
        policy_version="risk-live-v1",
        effective_at=timestamp,
        hard_limits=(RiskLimit(name="max_order_notional", limit=Decimal("100"), unit="USD"),),
        approved_by="human",
    )
    authorization = LiveAuthorization(
        human_approver="human",
        approved_at=timestamp,
        allowed_instruments=(btc_usdt.canonical_id,),
        fixed_loss_budget=Decimal("100"),
        max_order_notional=Decimal("100"),
        risk_policy_hash=policy.canonical_hash(),
        rollback_condition="reconciliation discrepancy",
        no_simultaneous_expansion=True,
        ai_services_can_be_stopped=True,
    )
    order = Order(
        parent_intent_id=uuid4(),
        execution_plan_id=uuid4(),
        instrument=btc_usdt,
        side="buy",
        quantity=Decimal("1"),
        order_type="limit",
        price=Decimal("100"),
        time_in_force="GTC",
        idempotency_key="live-test-order",
    )
    decision = RiskDecision(
        target_portfolio_id=uuid4(),
        risk_policy_id=policy.artifact_id,
        outcome=RiskOutcome.APPROVED,
        authoritative_state_hash=sha256(
            f"{account.snapshot().state_hash}:{RiskMarketState(marks={btc_usdt.canonical_id: Decimal('100')}).effective_hash}".encode()
        ).hexdigest(),
    )
    result = ControlledLiveOrderGuard(authorization).approve(
        order=order,
        account=account,
        market=RiskMarketState(marks={btc_usdt.canonical_id: Decimal("100")}),
        policy=policy,
        risk_decision=decision,
    )
    assert result.passed


def test_live_control_plane_defaults_to_paper_and_durably_rolls_back(timestamp, tmp_path):
    from advisorai.ledger import SqliteLedgers

    authorization = LiveAuthorization(
        human_approver="human",
        owner="operator",
        venue="approved-venue",
        approved_at=timestamp,
        allowed_instruments=("crypto:BTC-USDT:approved-venue:spot",),
        fixed_loss_budget=Decimal("100"),
        max_order_notional=Decimal("100"),
        risk_policy_hash="a" * 64,
        rollback_condition="any reconciliation discrepancy",
        no_simultaneous_expansion=True,
        ai_services_can_be_stopped=True,
    )
    ledgers = SqliteLedgers(tmp_path / "live.sqlite")
    plane = LiveControlPlane(ledgers=ledgers)
    assert plane.status().state is LiveOperatingState.PAPER
    plane.record_authorization(authorization)
    assert plane.evaluate_readiness(
        soak=_soak(),
        current_reconciliation_clean=True,
        offline_safety=_offline(),
    ).passed
    assert plane.start().state is LiveOperatingState.ACTIVE
    assert plane.rollback("test incident").state is LiveOperatingState.ROLLED_BACK
    restarted = LiveControlPlane(ledgers=ledgers)
    assert restarted.authorization == authorization
    assert restarted.status().rollback_reason == "test incident"


def test_live_control_plane_rejects_corrupt_active_ledger_state(tmp_path):
    from datetime import UTC, datetime

    from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers

    ledgers = SqliteLedgers(tmp_path / "corrupt-live.sqlite")
    ledgers.append(
        LedgerEvent(
            namespace=LedgerNamespace.INCIDENT,
            event_type="live_state_changed",
            idempotency_key="corrupt-live-state",
            payload={
                "status": {
                    "state": "active",
                    "authorization_id": str(uuid4()),
                    "changed_at": datetime.now(UTC).isoformat(),
                }
            },
        )
    )
    import pytest

    with pytest.raises(ValueError, match="active state without readiness"):
        LiveControlPlane(ledgers=ledgers)
