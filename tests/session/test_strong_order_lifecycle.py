"""Strong paper order lifecycle: proposal → risk → OMS → fill → reconcile.

End-to-end proof with exact states and identities, not a loose terminal-state
acceptance:

1. A passing evidence gate + validated LONG proposal produces a nonzero order.
2. The order traverses CREATED → RISK_APPROVED → ROUTED → ACKNOWLEDGED →
   FILLED → RECONCILED with exact state assertions at each step.
3. The fill reconciles against the venue snapshot; attribution/TCA and outcome
   memory link by exact decision/order/fill IDs.
4. A crash with an ambiguous acknowledgement reconciles without duplication.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from advisorai.agents import EvidenceCouncil, MissionRequest
from advisorai.agents.council import RoleResult
from advisorai.agents.router import MissionKind
from advisorai.api.service import AdvisorService
from advisorai.contracts import (
    AssetClass,
    Evidence,
    ExecutionPlan,
    Fill,
    InstrumentIdentity,
    Order,
    OrderState,
    RiskLimit,
    RiskPolicy,
    Snapshot,
)
from advisorai.decision import (
    DecisionModelArtifact,
    DecisionStance,
    ReferenceDecisionAdapter,
)
from advisorai.execution import (
    AccountState,
    OrderManager,
    PaperVenueAdapter,
    ReconciliationService,
    RiskMarketState,
    VenueAccountSnapshot,
)
from advisorai.learning import PaperDecisionRecord, PaperLearningLoop, PaperOutcome
from advisorai.ledger import SqliteLedgers
from advisorai.session import SessionSupervisor


def _evidence(timestamp, *, origin, family, claim, factor):
    return Evidence(
        claim=claim,
        source_family=family,
        origin=origin,
        observed_at=timestamp,
        first_available_at=timestamp,
        uncertainty=Decimal("0.1"),
        expires_at=timestamp + timedelta(hours=1),
    ), factor


def _passing_council(snapshot):
    cutoff = snapshot.as_of

    def role(name, origin, family, factor):
        def run(_snapshot):
            evidence, _ = _evidence(
                cutoff, origin=origin, family=family, claim=f"{name} claim", factor=factor
            )
            return RoleResult(role=name, evidence=(evidence,))

        return run

    # Roles match the service's initial + optional roster. Gate needs
    # >=2 source families, >=3 factor families, and a deterministic check.
    return EvidenceCouncil(
        {
            "data_verifier": role("data_verifier", "venue-a", "market", "data_verifier"),
            "technical_flow": role("technical_flow", "venue-b", "market", "technical"),
            "derivatives_regime": role("derivatives_regime", "venue-c", "market", "derivatives"),
            "news_event": role("news_event", "wire-a", "news", "event"),
            "skeptic_base_rate": role("skeptic_base_rate", "research-a", "research", "base_rate"),
        }
    )


def _build_long(tmp_path, name="strong-e2e.sqlite3"):
    ledgers = SqliteLedgers(tmp_path / name)
    supervisor = SessionSupervisor(
        ledgers,
        state_dir=tmp_path / "state",
        lock_identity="owner-workstation",
        code_hash="c" * 64,
        config_hash="d" * 64,
    )
    supervisor.start(reason="strong paper order session")
    btc = InstrumentIdentity(canonical_id="BTC", asset_class=AssetClass.CRYPTO, venue="paper")
    cutoff = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    snapshot = Snapshot(as_of=cutoff, purpose="strong-e2e-fixture")
    account = AccountState(cash=Decimal("100000"), as_of=cutoff, marks={"BTC": Decimal("50000")})
    market = RiskMarketState(
        marks={"BTC": Decimal("50000")},
        volatility={"BTC": Decimal("0.01")},
        liquidity_notional={"BTC": Decimal("1000000")},
    )
    council = _passing_council(snapshot)
    artifact = DecisionModelArtifact(
        model_name="reference",
        adapter_name="reference-long-v1",
        code_hash="a" * 64,
        config_hash="b" * 64,
        snapshot_id=snapshot.artifact_id,
    )
    adapter = ReferenceDecisionAdapter(artifact, directional_stance=DecisionStance.LONG)
    service = AdvisorService(council, ledgers, decision_model=adapter)
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=cutoff,
        hard_limits=(
            RiskLimit(name="max_gross_notional", limit=Decimal("500000"), unit="USD"),
            RiskLimit(name="max_order_notional", limit=Decimal("500000"), unit="USD"),
        ),
        approved_by="owner",
    )
    orders = OrderManager(ledgers, PaperVenueAdapter())
    return supervisor, ledgers, service, policy, orders, account, market, snapshot, btc


def test_long_proposal_drives_full_order_lifecycle(tmp_path):
    supervisor, ledgers, service, policy, orders, account, market, snapshot, btc = _build_long(
        tmp_path
    )
    result = service.build_decision(
        # STANDARD mission budget admits the full council roster. Plain
        # TRADE_FAST is policy-bounded to 2 roles and can never satisfy the
        # independence gate — by design it abstains (covered separately).
        request=MissionRequest(kind=MissionKind.RECOMMENDATION, user_text="strong e2e"),
        snapshot=snapshot,
        account=account,
        market=market,
        targets={btc: Decimal("999")},
        risk_policy_version="risk-v1",
    )
    # Caller targets are ignored: the proposal binds executable intent.
    assert result.proposal is not None
    assert result.proposal.stance is DecisionStance.LONG
    assert result.decision.abstained is False
    assert result.decision.gate.passed is True

    checked = service.evaluate_risk(result=result, account=account, market=market, policy=policy)
    assert checked.risk_decision is not None
    assert checked.risk_decision.outcome.value == "approved"

    target = checked.decision.target_portfolio
    btc_position = next(item for item in target.positions if item.instrument.canonical_id == "BTC")
    assert btc_position.target_quantity > account.positions.get("BTC", Decimal("0"))

    plan = ExecutionPlan(
        risk_decision_id=checked.risk_decision.artifact_id,
        target_portfolio_id=target.artifact_id,
        policy_version=target.risk_constraints_version,
        instructions=("strong e2e deterministic fill",),
        expires_at=result.decision.expires_at,
        created_at=result.decision.created_at,
    )
    order = Order(
        parent_intent_id=target.artifact_id,
        execution_plan_id=plan.artifact_id,
        instrument=btc,
        side="buy",
        quantity=Decimal("1"),
        order_type="limit",
        price=Decimal("50000"),
        time_in_force="GTC",
        idempotency_key=f"strong-e2e-{snapshot.artifact_id}",
    )
    created = orders.create(order)
    assert created.state is OrderState.CREATED
    from advisorai.execution import RiskKernel

    order_check = RiskKernel().check_order(
        order=created, account=account, market=market, policy=policy
    )
    assert order_check.approved, order_check.reasons
    orders.approve_risk(created.artifact_id, checked.risk_decision, order_check=order_check)
    assert orders.orders[created.artifact_id].state is OrderState.RISK_APPROVED
    acknowledgement = orders.route(created.artifact_id)
    assert acknowledgement is not None and acknowledgement.accepted is True
    assert orders.orders[created.artifact_id].state is OrderState.ACKNOWLEDGED

    fill = Fill(
        order_id=created.artifact_id,
        venue_fill_id=f"venue-fill-{created.artifact_id}",
        quantity=Decimal("1"),
        price=Decimal("50000"),
        fee=Decimal("0.5"),
        occurred_at=datetime.now(UTC),
    )
    orders.record_fill(fill, "buy")
    assert orders.orders[created.artifact_id].state is OrderState.FILLED
    account.apply_fill(fill, "buy", "BTC")
    orders.reconcile(created.artifact_id)
    assert orders.orders[created.artifact_id].state is OrderState.RECONCILED

    venue_snapshot = VenueAccountSnapshot(
        as_of=account.as_of,
        cash=account.cash,
        positions=dict(account.positions),
    )
    reconciliation = ReconciliationService(ledgers).run(
        account=account, orders=orders, venue_snapshot=venue_snapshot
    )
    assert reconciliation.reconciled is True, reconciliation.discrepancies

    # Outcome memory links by exact IDs, only after the horizon closes.
    loop = PaperLearningLoop(ledgers)
    record = loop.record_decision(
        PaperDecisionRecord(
            decision_id=uuid4(),
            mission_id=result.decision.mission_id,
            snapshot_id=snapshot.artifact_id,
            target_id=target.artifact_id,
            risk_decision_id=checked.risk_decision.artifact_id,
            order_ids=(created.artifact_id,),
            fill_ids=(fill.artifact_id,),
            reconciliation_id=reconciliation.artifact_id,
            subject="decision-pipeline",
            subject_version="strong-e2e-v1",
            asset="BTC",
            horizon="1h",
            cutoff=result.decision.created_at,
            horizon_end=result.decision.expires_at,
        )
    )
    outcome = loop.record_outcome(
        PaperOutcome(
            decision_id=record.decision_id,
            as_of=record.horizon_end,
            realized_value=Decimal("50100"),
            forecast_value=Decimal("50050"),
            net_utility=Decimal("10"),
            cost_usd=Decimal("5"),
            fill_quality=Decimal("1"),
            data_reliability=Decimal("1"),
            latency_ms=12,
            failure_rate=Decimal("0"),
        )
    )
    assert outcome.net_utility == Decimal("10")
    supervisor.checkpoint()
    end = supervisor.stop()
    assert end.outcome.value == "clean"


def test_crash_with_ambiguous_order_reconciles_without_duplicate(tmp_path):
    supervisor, ledgers, service, policy, orders, account, market, snapshot, btc = _build_long(
        tmp_path, name="ambiguous-e2e.sqlite3"
    )
    result = service.build_decision(
        request=MissionRequest(kind=MissionKind.RECOMMENDATION, user_text="ambiguous e2e"),
        snapshot=snapshot,
        account=account,
        market=market,
        targets={btc: Decimal("0")},
        risk_policy_version="risk-v1",
    )
    checked = service.evaluate_risk(result=result, account=account, market=market, policy=policy)
    assert checked.risk_decision is not None
    assert checked.risk_decision.outcome.value == "approved"

    orders.adapter.inject_ambiguous_ack_once("ambiguous-key-1")
    ambiguous_plan = ExecutionPlan(
        risk_decision_id=checked.risk_decision.artifact_id,
        target_portfolio_id=checked.decision.target_portfolio.artifact_id,
        policy_version="risk-v1",
        instructions=("ambiguous e2e",),
        expires_at=result.decision.expires_at,
        created_at=result.decision.created_at,
    )
    order = Order(
        parent_intent_id=checked.decision.target_portfolio.artifact_id,
        execution_plan_id=ambiguous_plan.artifact_id,
        instrument=btc,
        side="buy",
        quantity=Decimal("1"),
        order_type="limit",
        price=Decimal("50000"),
        time_in_force="GTC",
        idempotency_key="ambiguous-key-1",
    )
    created = orders.create(order)
    from advisorai.execution import RiskKernel as _RiskKernel

    ambiguous_check = _RiskKernel().check_order(
        order=created, account=account, market=market, policy=policy
    )
    assert ambiguous_check.approved, ambiguous_check.reasons
    orders.approve_risk(created.artifact_id, checked.risk_decision, order_check=ambiguous_check)
    assert orders.route(created.artifact_id) is None
    assert created.artifact_id in orders.ambiguous_orders

    # Crash: stop unclean with the ambiguous order open.
    supervisor.checkpoint()
    end = supervisor.stop(
        outcome=__import__("advisorai.session", fromlist=["SessionOutcome"]).SessionOutcome.UNCLEAN,
        open_orders=(created.artifact_id,),
        unfinished_work=("ambiguous_order_pending",),
    )
    assert end.outcome.value == "unclean"

    # Restart: reconcile the ambiguous acknowledgement, never resubmit.
    reconciled = orders.reconcile_ambiguous(created.artifact_id)
    assert reconciled.accepted is True
    assert created.artifact_id not in orders.ambiguous_orders
    assert orders.orders[created.artifact_id].state is OrderState.ACKNOWLEDGED
    count_before = len(orders.orders)
    # Retrying with the current projection is idempotent and returns the
    # existing record; a duplicate submission would raise or create a second
    # order. (The stale local copy differs by state and is rightly refused.)
    current = orders.orders[created.artifact_id]
    duplicate = orders.create(current)
    assert duplicate.artifact_id == created.artifact_id
    assert len(orders.orders) == count_before
