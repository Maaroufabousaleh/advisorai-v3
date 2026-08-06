from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from advisorai.agents.fusion import DecisionBundle, EvidenceGateResult
from advisorai.api.service import DecisionPipelineResult
from advisorai.contracts import (
    AssetClass,
    InstrumentIdentity,
    RiskLimit,
    RiskPolicy,
    Snapshot,
    TargetPortfolio,
    TargetPosition,
)
from advisorai.execution import AccountState, OrderManager, PaperVenueAdapter, RiskMarketState
from advisorai.learning import PaperLearningLoop
from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.runtime import (
    CadenceGate,
    PaperRuntime,
    PaperRuntimeConfig,
    RuntimeStage,
    build_default_orders,
)


def _runtime(
    tmp_path,
    *,
    admitted: bool,
    with_learning: bool = False,
    cadence=None,
    observation_cutoff_provider=None,
    order_factory=None,
    database_name="runtime.sqlite3",
):
    cutoff = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    btc = InstrumentIdentity(canonical_id="BTC", asset_class=AssetClass.CRYPTO, venue="paper")
    snapshot = Snapshot(as_of=cutoff, purpose="runtime-fixture")
    target = TargetPortfolio(
        snapshot_id=snapshot.artifact_id,
        positions=(TargetPosition(instrument=btc, target_quantity=Decimal("1")),),
        cash_target=Decimal("900"),
        expected_cost=Decimal("0"),
        construction_method="fixture",
        risk_constraints_version="risk-v1",
        no_trade_comparison="current",
    )
    gate = EvidenceGateResult(
        passed=admitted,
        independent_origins=("fixture",) if admitted else (),
        independent_source_families=("market",) if admitted else (),
        independent_factor_families=("data_quality",) if admitted else (),
        discounted_evidence_ids=(),
        reasons=() if admitted else ("fixture_abstention",),
    )
    decision = DecisionBundle(
        mission_id=uuid4(),
        snapshot_id=snapshot.artifact_id,
        target_portfolio=target,
        evidence_ids=(uuid4(),) if admitted else (),
        consensus="fixture",
        confidence=Decimal("0.8") if admitted else Decimal("0"),
        abstained=not admitted,
        expires_at=cutoff + timedelta(hours=1),
        created_at=cutoff,
        gate=gate,
    )
    account = AccountState(cash=Decimal("1000"), as_of=cutoff)
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=cutoff - timedelta(hours=1),
        hard_limits=(RiskLimit(name="max_order_notional", limit=Decimal("200"), unit="USD"),),
        approved_by="reviewer",
    )
    ledgers = SqliteLedgers(tmp_path / database_name)
    learning = PaperLearningLoop(ledgers) if with_learning else None
    orders = OrderManager(ledgers, PaperVenueAdapter(venue="paper"))
    runtime = PaperRuntime(
        config=PaperRuntimeConfig(),
        snapshot_provider=lambda requested: snapshot,
        market_provider=lambda _: RiskMarketState(
            marks={"BTC": Decimal("100")}, venue_healthy={"BTC": True}
        ),
        decision_builder=lambda *_: DecisionPipelineResult(decision=decision, risk_decision=None),
        account=account,
        risk_policy=policy,
        orders=orders,
        ledgers=ledgers,
        order_factory=order_factory or build_default_orders,
        learning=learning,
        cadence=cadence,
        observation_cutoff_provider=observation_cutoff_provider,
    )
    return runtime, cutoff, ledgers, learning


def test_runtime_abstains_before_risk_when_evidence_gate_fails(tmp_path):
    runtime, cutoff, ledgers, _ = _runtime(tmp_path, admitted=False)
    result = runtime.run_once(cutoff)
    assert result.stage is RuntimeStage.ABSTAINED
    assert "fixture_abstention" in result.reasons
    assert any(
        event.event_type == "paper_runtime_cycle"
        for event in ledgers.events(LedgerNamespace.MISSION)
    )


def test_runtime_routes_only_after_risk_and_records_cycle(tmp_path):
    runtime, cutoff, ledgers, _ = _runtime(tmp_path, admitted=True)
    result = runtime.run_once(cutoff)
    assert result.stage is RuntimeStage.ORDERS_ROUTED
    assert len(result.order_ids) == 1
    assert any(
        event.event_type == "paper_runtime_cycle"
        for event in ledgers.events(LedgerNamespace.MISSION)
    )


def test_runtime_requires_closed_hourly_cutoff(tmp_path):
    runtime, cutoff, _, _ = _runtime(tmp_path, admitted=False)
    try:
        runtime.run_once(cutoff + timedelta(minutes=5))
    except ValueError as exc:
        assert "closed one-hour" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("runtime accepted an open hourly cutoff")


def test_runtime_abstains_when_closed_five_minute_data_is_missing(tmp_path):
    gate = CadenceGate()
    cutoff = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    runtime, _, _, _ = _runtime(
        tmp_path,
        admitted=True,
        cadence=gate,
        observation_cutoff_provider=lambda _: gate.expected_observation_cutoffs(cutoff)[:-1],
    )
    result = runtime.run_once(cutoff)
    assert result.stage is RuntimeStage.ABSTAINED
    assert result.reasons == ("missing_closed_observation_data",)


def test_runtime_can_record_learning_chain_and_halt_resume_safely(tmp_path):
    runtime, cutoff, _, learning = _runtime(tmp_path, admitted=True, with_learning=True)
    result = runtime.run_once(cutoff)
    assert result.decision_id is not None
    assert learning is not None
    assert len(learning.problems()) == 0
    assert learning.decision(result.decision_id) is not None

    runtime.halt("operator test")
    assert runtime.risk_kernel.kill_switch.tripped
    assert runtime.run_forever(clock=lambda: cutoff, sleep=lambda _: None, max_cycles=1) == ()
    runtime.resume(approved_by="operator")
    assert not runtime.risk_kernel.kill_switch.tripped


def test_runtime_restart_hydrates_durable_kill_switch_before_routing(tmp_path):
    runtime, cutoff, _, _ = _runtime(tmp_path, admitted=True)
    runtime.halt("persisted operator halt")

    restarted, _, ledgers, _ = _runtime(tmp_path, admitted=True)
    result = restarted.run_once(cutoff)

    assert restarted.risk_kernel.kill_switch.tripped
    assert result.stage is RuntimeStage.ABSTAINED
    assert any(
        event.event_type == "kill_switch_tripped"
        for event in ledgers.events(LedgerNamespace.INCIDENT)
    )
    assert not restarted.orders.orders


def test_runtime_marks_created_order_rejected_when_order_level_risk_fails(tmp_path):
    def order_factory(result, account, market):
        orders = build_default_orders(result, account, market)
        return tuple(order.model_copy(update={"price": Decimal("300")}) for order in orders)

    runtime, cutoff, _, _ = _runtime(
        tmp_path,
        admitted=True,
        order_factory=order_factory,
    )
    result = runtime.run_once(cutoff)

    assert result.stage is RuntimeStage.RISK_REJECTED
    assert len(runtime.orders.orders) == 1
    assert next(iter(runtime.orders.orders.values())).state.value == "rejected"
