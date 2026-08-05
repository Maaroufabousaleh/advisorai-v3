import importlib.util
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

import pytest

from advisorai.contracts import (
    ExecutionPlan,
    Fill,
    Order,
    OrderState,
    RiskDecision,
    RiskLimit,
    RiskOutcome,
    RiskPolicy,
    Snapshot,
)
from advisorai.execution import (
    AccountLedger,
    AccountState,
    DeterministicExecutionPolicy,
    ExecutionPolicyKind,
    KillSwitch,
    MarketEvent,
    NativeVenueAdapter,
    NautilusRuntimeError,
    NautilusTraderPipeline,
    OrderManager,
    OrderStateError,
    PaperVenueAdapter,
    PortfolioConstraints,
    QuoteState,
    RawEventSpool,
    ReconciliationService,
    ReplayEngine,
    RiskKernel,
    RiskMarketState,
    RiskRequest,
    TargetPortfolioBuilder,
    VenueAccountSnapshot,
    VenueAcknowledgement,
    compute_tca,
)
from advisorai.gates import (
    GateDecision,
    GateEvidence,
    GateEvidenceKind,
    PhaseGateRecord,
    PhaseGateRegistry,
)
from advisorai.ledger import (
    IdempotencyConflict,
    LedgerNamespace,
    SqliteEventOutbox,
    SqliteLedgers,
)


class FailingEventBus:
    def __init__(self, fail_event_type: str) -> None:
        self.fail_event_type = fail_event_type
        self.failed = False

    def publish(self, envelope):
        if envelope.event_type == self.fail_event_type and not self.failed:
            self.failed = True
            raise RuntimeError("notification transport unavailable")

    def replay(self, event_type=None):
        return ()


def _snapshot(timestamp):
    return Snapshot(as_of=timestamp, purpose="paper-core-fixture")


def _order(btc_usdt, timestamp):
    plan = ExecutionPlan(
        created_at=timestamp,
        risk_decision_id=uuid4(),
        target_portfolio_id=uuid4(),
        policy_version="risk-v1",
        instructions=("passive_limit",),
        expires_at=timestamp + timedelta(minutes=5),
    )
    return Order(
        parent_intent_id=uuid4(),
        execution_plan_id=plan.artifact_id,
        instrument=btc_usdt,
        side="buy",
        quantity=Decimal("1"),
        order_type="limit",
        price=Decimal("100"),
        time_in_force="GTC",
        idempotency_key=f"intent-{uuid4()}",
    )


def _authoritative_state_hash(account, market):
    return sha256(f"{account.snapshot().state_hash}:{market.effective_hash}".encode()).hexdigest()


def test_raw_spool_is_idempotent_and_replay_is_deterministic(tmp_path, timestamp):
    spool = RawEventSpool(tmp_path / "raw" / "events.jsonl")
    later = MarketEvent.from_raw(
        event_type="trade",
        instrument_id="BTC",
        occurred_at=timestamp + timedelta(seconds=2),
        sequence=2,
        raw_payload=b"later",
        price=Decimal("101"),
        quantity=Decimal("1"),
    )
    earlier = MarketEvent.from_raw(
        event_type="trade",
        instrument_id="BTC",
        occurred_at=timestamp,
        sequence=1,
        raw_payload=b"earlier",
        price=Decimal("100"),
        quantity=Decimal("1"),
    )
    assert spool.append(later)
    assert not spool.append(later)
    assert spool.append(earlier)
    seen = []
    assert ReplayEngine().replay(spool.read(), seen.append) == 2
    assert [event.sequence for event in seen] == [1, 2]
    pipeline = NautilusTraderPipeline(test_double=True)
    assert pipeline.engine_name == "nautilus_trader"


def test_paper_venue_rejects_reuse_of_idempotency_key_for_changed_order(btc_usdt, timestamp):
    adapter = PaperVenueAdapter()
    order = _order(btc_usdt, timestamp)
    assert adapter.submit(order).accepted
    changed = order.model_copy(update={"quantity": Decimal("2")})
    with pytest.raises(ValueError, match="idempotency key"):
        adapter.submit(changed)


def test_paper_venue_can_enforce_instrument_venue_identity(btc_usdt, timestamp):
    adapter = PaperVenueAdapter(venue="other-paper-venue", strict_venue=True)
    with pytest.raises(ValueError, match="does not match"):
        adapter.submit(_order(btc_usdt, timestamp))


def test_raw_spool_rejects_corrupt_tail_and_conflicting_duplicate(tmp_path, timestamp):
    path = tmp_path / "raw" / "events.jsonl"
    event = MarketEvent.from_raw(
        event_type="trade",
        instrument_id="BTC",
        occurred_at=timestamp,
        sequence=1,
        raw_payload=b"one",
        price=Decimal("100"),
        quantity=Decimal("1"),
    )
    spool = RawEventSpool(path)
    assert spool.append(event)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not-json}\n")
    with pytest.raises(RuntimeError, match="corrupted"):
        RawEventSpool(path)

    clean_path = tmp_path / "raw" / "clean.jsonl"
    clean = RawEventSpool(clean_path)
    assert clean.append(event)
    conflicting = event.model_copy(update={"price": Decimal("101")})
    with pytest.raises(ValueError, match="different content"):
        clean.append(conflicting)


@pytest.mark.skipif(
    importlib.util.find_spec("nautilus_trader") is None,
    reason="the admitted-runtime boundary requires the optional NautilusTrader package",
)
def test_admitted_nautilus_pipeline_requires_and_uses_pinned_runner(timestamp):
    event = MarketEvent.from_raw(
        event_type="trade",
        instrument_id="BTC",
        occurred_at=timestamp,
        sequence=1,
        raw_payload=b"one",
        price=Decimal("100"),
        quantity=Decimal("1"),
    )
    with pytest.raises(NautilusRuntimeError, match="replay runner"):
        NautilusTraderPipeline(phase0_admitted=True)
    gate_registry = PhaseGateRegistry()
    gate_registry.record(
        PhaseGateRecord(
            phase=0,
            name="phase-0",
            decision=GateDecision.PASSED,
            required_evidence=("replay",),
            evidence=(
                GateEvidence(
                    name="replay",
                    kind=GateEvidenceKind.EXTERNAL_TIMED,
                    passed=True,
                    artifact_hash="a" * 64,
                    source="fixture",
                    verified_by="reviewer",
                    observed_at=timestamp,
                ),
            ),
            recorded_by="reviewer",
        )
    )
    seen = []
    pipeline = NautilusTraderPipeline(
        phase0_admitted=True,
        gate_registry=gate_registry,
        replay_runner=lambda events, handler: (handler(next(iter(events))), 1)[1],
    )
    assert pipeline.replay((event,), seen.append) == 1
    assert seen == [event]


def test_account_fill_mark_and_snapshot_are_authoritative(btc_usdt, timestamp):
    account = AccountState(cash=Decimal("1000"))
    fill = Fill(
        order_id=uuid4(),
        venue_fill_id="fill-1",
        quantity=Decimal("2"),
        price=Decimal("100"),
        fee=Decimal("0.20"),
        occurred_at=timestamp,
    )
    account.apply_fill(fill, "buy", btc_usdt.canonical_id)
    account.mark(btc_usdt.canonical_id, Decimal("110"), timestamp + timedelta(minutes=1))
    snapshot = account.snapshot()
    assert account.cash == Decimal("799.80")
    assert snapshot.unrealized_pnl == Decimal("20")
    assert len(snapshot.state_hash) == 64


def test_account_crossing_fill_resets_average_cost_for_new_side(btc_usdt, timestamp):
    account = AccountState(cash=Decimal("1000"))
    opening = Fill(
        order_id=uuid4(),
        venue_fill_id="long-open",
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee=Decimal("0"),
        occurred_at=timestamp,
    )
    crossing = opening.model_copy(
        update={"venue_fill_id": "crossing", "quantity": Decimal("2"), "price": Decimal("110")}
    )
    account.apply_fill(opening, "buy", btc_usdt.canonical_id)
    account.apply_fill(crossing, "sell", btc_usdt.canonical_id)
    assert account.positions[btc_usdt.canonical_id] == Decimal("-1")
    assert account.average_cost[btc_usdt.canonical_id] == Decimal("110")
    assert account.realized_pnl == Decimal("10")


def test_account_ledger_deduplicates_fill_and_funding_retries(btc_usdt, timestamp, tmp_path):
    account = AccountState(cash=Decimal("1000"))
    ledgers = SqliteLedgers(tmp_path / "ledgers.sqlite")
    ledger = AccountLedger(ledgers, account)
    fill = Fill(
        order_id=uuid4(),
        venue_fill_id="fill-idempotent",
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee=Decimal("0.10"),
        occurred_at=timestamp,
    )
    first = ledger.apply_fill(fill, "buy", btc_usdt.canonical_id)
    retry = ledger.apply_fill(fill, "buy", btc_usdt.canonical_id)
    assert retry.state_hash == first.state_hash
    assert account.cash == Decimal("899.90")
    assert len(ledgers.events(LedgerNamespace.ACCOUNT)) == 1
    with pytest.raises(IdempotencyConflict, match="different fill"):
        ledger.apply_fill(
            fill.model_copy(update={"artifact_id": uuid4()}), "buy", btc_usdt.canonical_id
        )

    ledger.apply_funding(Decimal("1.25"), timestamp, "funding-idempotent")
    funding_retry = ledger.apply_funding(Decimal("1.25"), timestamp, "funding-idempotent")
    assert funding_retry.funding_paid == Decimal("1.25")
    assert account.cash == Decimal("898.65")


def test_account_ledger_publishes_durable_events_after_commit(btc_usdt, timestamp, tmp_path):
    ledgers = SqliteLedgers(tmp_path / "ledgers.sqlite")
    outbox = SqliteEventOutbox(tmp_path / "outbox.sqlite")
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    ledger = AccountLedger(ledgers, account, event_bus=outbox)
    fill = Fill(
        order_id=uuid4(),
        venue_fill_id="outbox-fill",
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee=Decimal("0.10"),
        occurred_at=timestamp,
    )

    ledger.apply_fill(fill, "buy", btc_usdt.canonical_id)

    pending = outbox.pending()
    assert len(pending) == 1
    assert pending[0].event_type == "account_fill_applied"
    assert pending[0].artifact_ids == (fill.artifact_id,)
    assert pending[0].event_id == ledgers.events(LedgerNamespace.ACCOUNT)[0].event_id
    assert pending[0].occurred_at == ledgers.events(LedgerNamespace.ACCOUNT)[0].occurred_at


def test_account_ledger_notification_failure_does_not_double_apply_fill(
    btc_usdt, timestamp, tmp_path
):
    ledgers = SqliteLedgers(tmp_path / "account-notification-failure.sqlite")
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    ledger = AccountLedger(ledgers, account, event_bus=FailingEventBus("account_fill_applied"))
    fill = Fill(
        order_id=uuid4(),
        venue_fill_id="notification-failure-fill",
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee=Decimal("0.10"),
        occurred_at=timestamp,
    )
    with pytest.raises(RuntimeError, match="notification transport"):
        ledger.apply_fill(fill, "buy", btc_usdt.canonical_id)
    retry = ledger.apply_fill(fill, "buy", btc_usdt.canonical_id)
    assert retry.cash == Decimal("899.90")
    assert len(ledgers.events(LedgerNamespace.ACCOUNT)) == 1


def test_oms_publishes_durable_order_events(tmp_path, btc_usdt, timestamp):
    ledgers = SqliteLedgers(tmp_path / "oms-outbox.sqlite")
    outbox = SqliteEventOutbox(tmp_path / "oms-events.sqlite")
    oms = OrderManager(ledgers, PaperVenueAdapter(), event_bus=outbox)
    order = _order(btc_usdt, timestamp)

    oms.create(order)

    pending = outbox.pending()
    assert len(pending) == 1
    assert pending[0].event_type == "order_created"
    assert pending[0].artifact_ids == (order.artifact_id,)
    assert pending[0].event_id == ledgers.events(LedgerNamespace.ORDER)[0].event_id


def test_oms_order_creation_notification_failure_is_retryable(tmp_path, btc_usdt, timestamp):
    ledgers = SqliteLedgers(tmp_path / "oms-create-notification-failure.sqlite")
    oms = OrderManager(
        ledgers,
        PaperVenueAdapter(),
        event_bus=FailingEventBus("order_created"),
    )
    order = _order(btc_usdt, timestamp)
    with pytest.raises(RuntimeError, match="notification transport"):
        oms.create(order)
    assert oms.orders[order.artifact_id] == order
    assert oms.create(order) == order
    assert len(ledgers.events(LedgerNamespace.ORDER)) == 1


def test_oms_rejects_reuse_of_order_artifact_id_with_changed_payload(tmp_path, btc_usdt, timestamp):
    oms = OrderManager(
        SqliteLedgers(tmp_path / "oms-artifact-immutability.sqlite"), PaperVenueAdapter()
    )
    order = _order(btc_usdt, timestamp)
    oms.create(order)
    with pytest.raises(OrderStateError, match="artifact ID is immutable"):
        oms.create(order.model_copy(update={"quantity": Decimal("2")}))


def test_oms_moves_an_explicit_venue_rejection_to_rejected_state(tmp_path, btc_usdt, timestamp):
    class RejectingAdapter(PaperVenueAdapter):
        def submit(self, order):
            return VenueAcknowledgement(
                order_id=order.artifact_id,
                venue_order_id="paper-rejected",
                accepted=False,
            )

    oms = OrderManager(SqliteLedgers(tmp_path / "oms-rejection.sqlite"), RejectingAdapter())
    order = _order(btc_usdt, timestamp)
    oms.create(order)
    oms.transition(order.artifact_id, OrderState.RISK_APPROVED)
    assert oms.route(order.artifact_id).accepted is False
    assert oms.orders[order.artifact_id].state is OrderState.REJECTED


def test_oms_transition_notification_failure_is_retryable(tmp_path, btc_usdt, timestamp):
    ledgers = SqliteLedgers(tmp_path / "oms-transition-notification-failure.sqlite")
    oms = OrderManager(ledgers, PaperVenueAdapter())
    order = _order(btc_usdt, timestamp)
    oms.create(order)
    oms.event_bus = FailingEventBus("order_risk_approved")
    with pytest.raises(RuntimeError, match="notification transport"):
        oms.transition(order.artifact_id, OrderState.RISK_APPROVED)
    assert oms.orders[order.artifact_id].state is OrderState.RISK_APPROVED
    assert (
        oms.transition(order.artifact_id, OrderState.RISK_APPROVED).state
        is OrderState.RISK_APPROVED
    )
    assert [
        event.event_type
        for event in ledgers.events(LedgerNamespace.ORDER)
        if event.event_type == "order_risk_approved"
    ] == ["order_risk_approved"]


def test_oms_fill_notification_failure_is_retryable_without_duplicate_fill(
    tmp_path, btc_usdt, timestamp
):
    ledgers = SqliteLedgers(tmp_path / "oms-notification-failure.sqlite")
    adapter = PaperVenueAdapter()
    oms = OrderManager(
        ledgers,
        adapter,
        event_bus=FailingEventBus("fill_recorded"),
    )
    order = _order(btc_usdt, timestamp)
    oms.create(order)
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=timestamp,
        hard_limits=(RiskLimit(name="max_order_notional", limit=Decimal("250"), unit="USD"),),
        approved_by="human-reviewer",
    )
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    market = RiskMarketState(marks={btc_usdt.canonical_id: Decimal("100")})
    check = RiskKernel().check_order(order=order, account=account, market=market, policy=policy)
    oms.approve_risk(
        order.artifact_id,
        RiskDecision(
            target_portfolio_id=uuid4(),
            risk_policy_id=policy.artifact_id,
            outcome=RiskOutcome.APPROVED,
            authoritative_state_hash=check.authoritative_state_hash or "",
        ),
        order_check=check,
    )
    oms.route(order.artifact_id)
    fill = Fill(
        order_id=order.artifact_id,
        venue_fill_id="notification-failure-fill",
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee=Decimal("0"),
        occurred_at=timestamp,
    )
    with pytest.raises(RuntimeError, match="notification transport"):
        oms.record_fill(fill, "buy")
    assert oms.record_fill(fill, "buy").state is OrderState.FILLED
    assert len(oms.fills) == 1
    assert (
        sum(event.event_type == "fill_recorded" for event in ledgers.events(LedgerNamespace.ORDER))
        == 1
    )


def test_oms_retrying_a_partial_fill_does_not_count_it_twice(tmp_path, btc_usdt, timestamp):
    ledgers = SqliteLedgers(tmp_path / "oms-partial-retry.sqlite")
    adapter = PaperVenueAdapter()
    oms = OrderManager(ledgers, adapter)
    order = _order(btc_usdt, timestamp)
    oms.create(order)
    oms.transition(order.artifact_id, OrderState.RISK_APPROVED)
    oms.route(order.artifact_id)
    partial = Fill(
        order_id=order.artifact_id,
        venue_fill_id="partial-retry-fill",
        quantity=Decimal("0.5"),
        price=Decimal("100"),
        fee=Decimal("0"),
        occurred_at=timestamp,
    )
    assert oms.record_fill(partial, "buy").state is OrderState.PARTIALLY_FILLED
    assert oms.record_fill(partial, "buy").state is OrderState.PARTIALLY_FILLED


def test_oms_rejects_reuse_of_fill_artifact_id_with_changed_payload(tmp_path, btc_usdt, timestamp):
    ledgers = SqliteLedgers(tmp_path / "oms-fill-artifact-immutability.sqlite")
    adapter = PaperVenueAdapter()
    oms = OrderManager(ledgers, adapter)
    order = _order(btc_usdt, timestamp)
    oms.create(order)
    oms.transition(order.artifact_id, OrderState.RISK_APPROVED)
    oms.route(order.artifact_id)
    fill = Fill(
        order_id=order.artifact_id,
        venue_fill_id="immutable-fill",
        quantity=Decimal("0.25"),
        price=Decimal("100"),
        fee=Decimal("0"),
        occurred_at=timestamp,
    )
    oms.record_fill(fill, "buy")
    with pytest.raises(OrderStateError, match="artifact ID is immutable"):
        oms.record_fill(fill.model_copy(update={"quantity": Decimal("0.5")}), "buy")
    assert len(oms.fills) == 1


def test_account_ledger_rebuilds_cash_positions_marks_and_transfers_after_restart(
    btc_usdt, timestamp, tmp_path
):
    ledgers = SqliteLedgers(tmp_path / "restart.sqlite")
    original = AccountState(cash=Decimal("1000"), as_of=timestamp)
    ledger = AccountLedger(ledgers, original)
    fill = Fill(
        order_id=uuid4(),
        venue_fill_id="restart-fill",
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee=Decimal("0.10"),
        occurred_at=timestamp,
    )
    ledger.apply_fill(fill, "buy", btc_usdt.canonical_id)
    ledger.mark(btc_usdt.canonical_id, Decimal("110"), timestamp, "restart-mark")
    ledger.apply_cash_transfer(Decimal("25"), timestamp, "restart-deposit")

    rebuilt_account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    rebuilt = AccountLedger(ledgers, rebuilt_account)
    assert rebuilt_account.snapshot().state_hash == original.snapshot().state_hash
    assert (
        rebuilt.mark(btc_usdt.canonical_id, Decimal("110"), timestamp, "restart-mark").state_hash
        == original.snapshot().state_hash
    )


def test_account_ledger_rebuilds_margin_state_idempotently(tmp_path, timestamp):
    ledgers = SqliteLedgers(tmp_path / "margin.sqlite")
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    ledger = AccountLedger(ledgers, account)

    first = ledger.update_margin(
        margin_used=Decimal("100"),
        margin_available=Decimal("900"),
        as_of=timestamp,
        margin_id="margin-1",
    )
    retry = ledger.update_margin(
        margin_used=Decimal("100"),
        margin_available=Decimal("900"),
        as_of=timestamp,
        margin_id="margin-1",
    )
    rebuilt_account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    AccountLedger(ledgers, rebuilt_account)

    assert first.state_hash == retry.state_hash == rebuilt_account.snapshot().state_hash
    assert rebuilt_account.margin_used == Decimal("100")
    assert rebuilt_account.margin_available == Decimal("900")


def test_account_ledger_rebuilds_borrow_fx_and_corporate_action_events(tmp_path, timestamp):
    ledgers = SqliteLedgers(tmp_path / "accounting-events.sqlite")
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    ledger = AccountLedger(ledgers, account)
    ledger.apply_borrow(Decimal("2"), timestamp, "borrow-1")
    ledger.apply_fx_adjustment(Decimal("5"), timestamp, "fx-1")
    ledger.apply_corporate_action(Decimal("3"), timestamp, "corp-1")
    rebuilt = AccountState(cash=Decimal("1000"), as_of=timestamp)
    AccountLedger(ledgers, rebuilt)
    assert rebuilt.snapshot().state_hash == account.snapshot().state_hash
    assert rebuilt.borrow_paid == Decimal("2")
    assert rebuilt.fx_adjustments == Decimal("5")
    assert rebuilt.corporate_action_adjustments == Decimal("3")


def test_account_ledger_applies_split_and_dividend_without_losing_replay_truth(
    tmp_path, btc_usdt, timestamp
):
    # Reuse the account projection for an equity-shaped instrument to exercise
    # the Phase 9 accounting boundary without opening an equity execution path.
    ledgers = SqliteLedgers(tmp_path / "split-dividend.sqlite")
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    ledger = AccountLedger(ledgers, account)
    fill = Fill(
        order_id=uuid4(),
        venue_fill_id="split-fill",
        quantity=Decimal("2"),
        price=Decimal("100"),
        fee=Decimal("0"),
        occurred_at=timestamp,
    )
    ledger.apply_fill(fill, "buy", btc_usdt.canonical_id)
    ledger.mark(btc_usdt.canonical_id, Decimal("100"), timestamp, "split-mark")
    before = account.equity()
    ledger.apply_split(btc_usdt.canonical_id, Decimal("2"), timestamp, "split-1")
    assert account.positions[btc_usdt.canonical_id] == Decimal("4")
    assert account.average_cost[btc_usdt.canonical_id] == Decimal("50")
    assert account.marks[btc_usdt.canonical_id] == Decimal("50")
    assert account.equity() == before
    ledger.apply_dividend(btc_usdt.canonical_id, Decimal("2"), timestamp, "dividend-1")
    assert account.cash == Decimal("808")
    assert account.corporate_action_adjustments == Decimal("8")

    rebuilt = AccountState(cash=Decimal("1000"), as_of=timestamp)
    AccountLedger(ledgers, rebuilt)
    assert rebuilt.snapshot().state_hash == account.snapshot().state_hash


def test_account_snapshot_exposes_equity_and_drawdown(btc_usdt, timestamp):
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    fill = Fill(
        order_id=uuid4(),
        venue_fill_id="equity-fill",
        quantity=Decimal("2"),
        price=Decimal("100"),
        fee=Decimal("0"),
        occurred_at=timestamp,
    )
    account.apply_fill(fill, "buy", btc_usdt.canonical_id)
    account.mark(btc_usdt.canonical_id, Decimal("90"), timestamp)
    snapshot = account.snapshot()
    assert snapshot.equity == Decimal("980")
    assert snapshot.gross_notional == Decimal("180")
    assert snapshot.drawdown == Decimal("20")


def test_target_builder_preserves_no_trade_band(btc_usdt, timestamp):
    account = AccountState(cash=Decimal("1000"), positions={btc_usdt.canonical_id: Decimal("1")})
    snapshot = _snapshot(timestamp)
    target = TargetPortfolioBuilder(no_trade_band=Decimal("10")).build(
        snapshot=snapshot,
        account=account,
        targets={btc_usdt: Decimal("1.05")},
        marks={btc_usdt.canonical_id: Decimal("100")},
    )
    assert target.positions[0].target_quantity == Decimal("1")
    assert target.expected_cost == Decimal("0")


def test_target_builder_uses_explicit_cost_model_and_expected_return(btc_usdt, timestamp):
    target = TargetPortfolioBuilder(
        fee_bps=Decimal("10"), spread_bps=Decimal("5"), impact_bps=Decimal("5")
    ).build(
        snapshot=_snapshot(timestamp),
        account=AccountState(cash=Decimal("1000"), as_of=timestamp),
        targets={btc_usdt: Decimal("1")},
        marks={btc_usdt.canonical_id: Decimal("100")},
        expected_returns={btc_usdt.canonical_id: Decimal("0.08")},
    )
    assert target.expected_cost == Decimal("0.20")
    assert target.positions[0].expected_return_after_costs == Decimal("0.08")


def test_execution_policy_selects_immediate_or_passive_without_submission(btc_usdt, timestamp):
    quote = QuoteState(mark=Decimal("100"), bid=Decimal("99"), ask=Decimal("101"))
    policy = DeterministicExecutionPolicy()
    immediate = policy.choose(
        signed_delta=Decimal("2"), quote=quote, policy=ExecutionPolicyKind.IMMEDIATE
    )
    passive = policy.choose(
        signed_delta=Decimal("-2"), quote=quote, policy=ExecutionPolicyKind.PASSIVE_LIMIT
    )
    assert (
        immediate is not None
        and immediate.order_type == "market"
        and immediate.price == Decimal("101")
    )
    assert (
        passive is not None
        and passive.order_type == "passive_limit"
        and passive.price == Decimal("101")
    )
    assert (
        policy.choose(signed_delta=Decimal("0"), quote=quote, policy=ExecutionPolicyKind.IMMEDIATE)
        is None
    )


def test_risk_kernel_rejects_stale_data_and_kill_switch(btc_usdt, timestamp):
    account = AccountState(cash=Decimal("1000"))
    target = TargetPortfolioBuilder().build(
        snapshot=_snapshot(timestamp),
        account=account,
        targets={btc_usdt: Decimal("1")},
        marks={btc_usdt.canonical_id: Decimal("100")},
    )
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=timestamp,
        hard_limits=(RiskLimit(name="max_gross_notional", limit=Decimal("200"), unit="USD"),),
        approved_by="human-reviewer",
    )
    decision = RiskKernel().evaluate(
        RiskRequest(
            target=target,
            account=account,
            market=RiskMarketState(
                marks={btc_usdt.canonical_id: Decimal("100")},
                stale_instruments=frozenset({btc_usdt.canonical_id}),
            ),
            policy=policy,
        )
    )
    assert decision.outcome.value == "rejected"
    switch = KillSwitch()
    switch.trip("operator test")
    assert (
        RiskKernel(switch)
        .evaluate(
            RiskRequest(
                target=target,
                account=account,
                market=RiskMarketState(marks={btc_usdt.canonical_id: Decimal("100")}),
                policy=policy,
            )
        )
        .outcome.value
        == "rejected"
    )


def test_risk_hash_binds_canonical_market_state_and_rejects_invalid_supplied_hash(
    btc_usdt, timestamp
):
    account = AccountState(cash=Decimal("1000"))
    target = TargetPortfolioBuilder().build(
        snapshot=_snapshot(timestamp),
        account=account,
        targets={btc_usdt: Decimal("1")},
        marks={btc_usdt.canonical_id: Decimal("100")},
    )
    market = RiskMarketState(marks={btc_usdt.canonical_id: Decimal("100")})
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=timestamp,
        hard_limits=(),
        approved_by="human-reviewer",
    )
    decision = RiskKernel().evaluate(
        RiskRequest(target=target, account=account, market=market, policy=policy)
    )
    assert (
        decision.authoritative_state_hash
        == __import__("hashlib")
        .sha256(f"{account.snapshot().state_hash}:{market.canonical_hash()}".encode())
        .hexdigest()
    )
    with pytest.raises(ValueError, match="canonical market state"):
        RiskMarketState(
            marks={btc_usdt.canonical_id: Decimal("100")},
            market_state_hash="a" * 64,
        )


def test_risk_kernel_fails_closed_when_configured_market_inputs_are_missing(btc_usdt, timestamp):
    account = AccountState(cash=Decimal("1000"))
    target = TargetPortfolioBuilder().build(
        snapshot=_snapshot(timestamp),
        account=account,
        targets={btc_usdt: Decimal("1")},
        marks={btc_usdt.canonical_id: Decimal("100")},
    )
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=timestamp,
        hard_limits=(
            RiskLimit(name="max_liquidity_participation", limit=Decimal("0.5"), unit="ratio"),
            RiskLimit(name="max_venue_health", limit=Decimal("0"), unit="flag"),
            RiskLimit(name="max_collateral_deficit", limit=Decimal("0"), unit="USD"),
        ),
        approved_by="human-reviewer",
    )
    decision = RiskKernel().evaluate(
        RiskRequest(
            target=target,
            account=account,
            market=RiskMarketState(marks={btc_usdt.canonical_id: Decimal("100")}),
            policy=policy,
        )
    )
    assert decision.outcome is RiskOutcome.REJECTED
    assert {reason.split(":", 1)[0] for reason in decision.reasons} >= {
        "max_liquidity_participation",
        "max_venue_health",
        "max_collateral_deficit",
    }


def test_risk_kernel_checks_proposed_target_cash_deficit(btc_usdt, timestamp):
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    target = (
        TargetPortfolioBuilder()
        .build(
            snapshot=_snapshot(timestamp),
            account=account,
            targets={btc_usdt: Decimal("1")},
            marks={btc_usdt.canonical_id: Decimal("100")},
            risk_constraints_version="risk-v1",
        )
        .model_copy(update={"cash_target": Decimal("-1")})
    )
    decision = RiskKernel().evaluate(
        RiskRequest(
            target=target,
            account=account,
            market=RiskMarketState(marks={btc_usdt.canonical_id: Decimal("100")}),
            policy=RiskPolicy(
                policy_version="risk-v1",
                effective_at=timestamp,
                hard_limits=(RiskLimit(name="max_cash_deficit", limit=Decimal("0"), unit="USD"),),
                approved_by="human-reviewer",
            ),
        )
    )
    assert decision.outcome is RiskOutcome.REJECTED
    assert any(reason.startswith("max_cash_deficit:") for reason in decision.reasons)


def test_risk_kernel_can_reduce_only_when_explicitly_requested(btc_usdt, timestamp):
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    target = TargetPortfolioBuilder().build(
        snapshot=_snapshot(timestamp),
        account=account,
        targets={btc_usdt: Decimal("5")},
        marks={btc_usdt.canonical_id: Decimal("100")},
        risk_constraints_version="risk-v1",
    )
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=timestamp,
        hard_limits=(RiskLimit(name="max_order_notional", limit=Decimal("250"), unit="USD"),),
        approved_by="human-reviewer",
    )
    request = RiskRequest(
        target=target,
        account=account,
        market=RiskMarketState(marks={btc_usdt.canonical_id: Decimal("100")}),
        policy=policy,
    )
    assert RiskKernel().evaluate(request).outcome is RiskOutcome.REJECTED
    reduced = RiskKernel().evaluate(request, allow_reduction=True)
    assert reduced.outcome is RiskOutcome.REDUCED
    assert reduced.reduced_positions[0].target_quantity <= Decimal("2.5")
    assert "target_reduced_to_policy_limits" in reduced.reasons


def test_risk_market_state_rejects_non_decimal_scalar_values():
    with pytest.raises(ValueError, match="Decimal instances"):
        RiskMarketState(marks={"BTC": Decimal("100")}, funding_cost_bps=1)  # type: ignore[arg-type]


def test_risk_kernel_enforces_borrow_cost_limit(btc_usdt, timestamp):
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    target = TargetPortfolioBuilder().build(
        snapshot=_snapshot(timestamp),
        account=account,
        targets={btc_usdt: Decimal("1")},
        marks={btc_usdt.canonical_id: Decimal("100")},
        risk_constraints_version="risk-v1",
    )
    decision = RiskKernel().evaluate(
        RiskRequest(
            target=target,
            account=account,
            market=RiskMarketState(
                marks={btc_usdt.canonical_id: Decimal("100")},
                borrow_cost_bps=Decimal("5"),
            ),
            policy=RiskPolicy(
                policy_version="risk-v1",
                effective_at=timestamp,
                hard_limits=(
                    RiskLimit(name="max_borrow_cost_bps", limit=Decimal("1"), unit="bps"),
                ),
                approved_by="human-reviewer",
            ),
        )
    )
    assert decision.outcome is RiskOutcome.REJECTED
    assert any(reason.startswith("max_borrow_cost_bps:") for reason in decision.reasons)


def test_kill_switch_state_rebuilds_from_incident_ledger(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "kill-switch.sqlite")
    first = KillSwitch(ledgers)
    first.trip("venue outage")

    restarted = KillSwitch(ledgers)
    assert restarted.tripped
    assert restarted.reason == "venue outage"
    with pytest.raises(ValueError, match="reason"):
        restarted.trip(" ")

    restarted.reset(approved_by="human-operator")
    assert not KillSwitch(ledgers).tripped


def test_risk_kernel_fails_closed_on_disagreement_and_unknown_hard_limit(btc_usdt, timestamp):
    account = AccountState(cash=Decimal("1000"))
    target = TargetPortfolioBuilder().build(
        snapshot=_snapshot(timestamp),
        account=account,
        targets={btc_usdt: Decimal("1")},
        marks={btc_usdt.canonical_id: Decimal("100")},
    )
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=timestamp,
        hard_limits=(RiskLimit(name="future_limit", limit=Decimal("1"), unit="USD"),),
        approved_by="human-reviewer",
    )
    decision = RiskKernel().evaluate(
        RiskRequest(
            target=target,
            account=account,
            market=RiskMarketState(
                marks={btc_usdt.canonical_id: Decimal("100")},
                disagreed_instruments=frozenset({btc_usdt.canonical_id}),
            ),
            policy=policy,
        )
    )
    assert decision.outcome.value == "rejected"
    assert "unsupported_hard_limit:future_limit" in decision.reasons
    assert "disagreed_market_data" in decision.reasons


def test_risk_kernel_binds_target_to_effective_policy_version(btc_usdt, timestamp):
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    target = TargetPortfolioBuilder().build(
        snapshot=_snapshot(timestamp),
        account=account,
        targets={btc_usdt: Decimal("1")},
        marks={btc_usdt.canonical_id: Decimal("100")},
        risk_constraints_version="risk-v1",
    )
    policy = RiskPolicy(
        policy_version="risk-v2",
        effective_at=timestamp,
        hard_limits=(),
        approved_by="human-reviewer",
    )
    decision = RiskKernel().evaluate(
        RiskRequest(
            target=target,
            account=account,
            market=RiskMarketState(marks={btc_usdt.canonical_id: Decimal("100")}),
            policy=policy,
        )
    )
    assert decision.outcome is RiskOutcome.REJECTED
    assert "risk_policy_version_mismatch" in decision.reasons


def test_order_risk_check_enforces_price_collar(btc_usdt, timestamp):
    account = AccountState(cash=Decimal("1000"))
    order = _order(btc_usdt, timestamp)
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=timestamp,
        hard_limits=(RiskLimit(name="price_collar_bps", limit=Decimal("50"), unit="bps"),),
        approved_by="human-reviewer",
    )
    check = RiskKernel().check_order(
        order=order,
        account=account,
        market=RiskMarketState(marks={btc_usdt.canonical_id: Decimal("101")}),
        policy=policy,
    )
    assert not check.approved
    assert any("price_collar_bps" in reason for reason in check.reasons)


def test_order_risk_check_enforces_liquidity_model_and_operational_limits(btc_usdt, timestamp):
    account = AccountState(cash=Decimal("1000"))
    order = _order(btc_usdt, timestamp)
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=timestamp,
        hard_limits=(
            RiskLimit(name="max_liquidity_participation", limit=Decimal("0.1"), unit="ratio"),
            RiskLimit(name="max_spread_bps", limit=Decimal("5"), unit="bps"),
            RiskLimit(name="max_model_drift", limit=Decimal("0"), unit="flag"),
        ),
        approved_by="human-reviewer",
    )
    check = RiskKernel().check_order(
        order=order,
        account=account,
        market=RiskMarketState(
            marks={btc_usdt.canonical_id: Decimal("101")},
            liquidity_notional={btc_usdt.canonical_id: Decimal("500")},
            spread_bps={btc_usdt.canonical_id: Decimal("10")},
            model_drift=True,
        ),
        policy=policy,
    )
    assert not check.approved
    assert {reason.split(":", 1)[0] for reason in check.reasons} >= {
        "max_liquidity_participation",
        "max_spread_bps",
        "model_drift",
    }


def test_native_adapter_is_testnet_only_and_uses_client_id(btc_usdt, timestamp):
    class Transport:
        def __init__(self):
            self.payload = None

        def submit_order(self, payload):
            self.payload = payload
            return {"accepted": True, "venue_order_id": "native-1"}

        def query_order(self, *, client_order_id):
            return {"client_order_id": client_order_id, "state": "acknowledged"}

    with pytest.raises(ValueError, match="paper/testnet"):
        NativeVenueAdapter(venue="venue", environment="live", transport=Transport())
    transport = Transport()
    adapter = NativeVenueAdapter(venue="venue", environment="testnet", transport=transport)
    order = _order(btc_usdt, timestamp)
    acknowledgement = adapter.submit(order)
    assert acknowledgement.venue_order_id == "native-1"
    assert transport.payload["client_order_id"] == order.idempotency_key
    assert adapter.reconcile(order)["state"] == "acknowledged"


def test_native_adapter_preserves_explicit_venue_rejection(btc_usdt, timestamp):
    class RejectingTransport:
        def submit_order(self, payload):
            return {"accepted": False, "venue_order_id": "native-rejected"}

        def query_order(self, *, client_order_id):
            return None

    acknowledgement = NativeVenueAdapter(
        venue="venue", environment="paper", transport=RejectingTransport()
    ).submit(_order(btc_usdt, timestamp))
    assert not acknowledgement.accepted


def test_oms_handles_ambiguous_ack_partial_fill_and_reconciliation(tmp_path, btc_usdt, timestamp):
    ledgers = SqliteLedgers(tmp_path / "state.sqlite")
    adapter = PaperVenueAdapter()
    oms = OrderManager(ledgers, adapter)
    order = _order(btc_usdt, timestamp)
    oms.create(order)
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=timestamp,
        hard_limits=(RiskLimit(name="max_order_notional", limit=Decimal("250"), unit="USD"),),
        approved_by="human-reviewer",
    )
    risk_account = AccountState(cash=Decimal("1000"))
    risk_market = RiskMarketState(marks={btc_usdt.canonical_id: Decimal("100")})
    order_check = RiskKernel().check_order(
        order=order,
        account=risk_account,
        market=risk_market,
        policy=policy,
    )
    oms.approve_risk(
        order.artifact_id,
        RiskDecision(
            target_portfolio_id=uuid4(),
            risk_policy_id=policy.artifact_id,
            outcome=RiskOutcome.APPROVED,
            authoritative_state_hash=_authoritative_state_hash(risk_account, risk_market),
        ),
        order_check=order_check,
    )
    adapter.inject_ambiguous_ack_once(order.idempotency_key)
    assert oms.route(order.artifact_id) is None
    assert order.artifact_id in oms.ambiguous_orders
    with pytest.raises(OrderStateError, match="invalid order transition"):
        oms.route(order.artifact_id)
    acknowledgement = oms.reconcile_ambiguous(order.artifact_id)
    assert acknowledgement.accepted
    partial = Fill(
        order_id=order.artifact_id,
        venue_fill_id="fill-partial",
        quantity=Decimal("0.4"),
        price=Decimal("100"),
        fee=Decimal("0.05"),
        occurred_at=timestamp,
    )
    assert oms.record_fill(partial, "buy").state is OrderState.PARTIALLY_FILLED
    assert oms.record_fill(partial, "buy").state is OrderState.PARTIALLY_FILLED
    assert len(oms.fills) == 1
    full = partial.model_copy(
        update={
            "artifact_id": uuid4(),
            "venue_fill_id": "fill-final",
            "quantity": Decimal("0.6"),
        }
    )
    assert oms.record_fill(full, "buy").state is OrderState.FILLED
    assert (
        not ReconciliationService()
        .run(account=AccountState(cash=Decimal("1000")), orders=oms)
        .reconciled
    )
    oms.reconcile(order.artifact_id)
    assert (
        ReconciliationService()
        .run(account=AccountState(cash=Decimal("1000")), orders=oms)
        .reconciled
    )
    assert len(adapter.open_orders()) == 1
    restarted = OrderManager(ledgers, adapter)
    assert restarted.orders[order.artifact_id].state is OrderState.RECONCILED
    assert set(restarted.fills) == {"fill-partial", "fill-final"}


def test_reconciliation_can_require_account_ledger_projection(tmp_path, btc_usdt, timestamp):
    ledgers = SqliteLedgers(tmp_path / "recon-account.sqlite")
    adapter = PaperVenueAdapter()
    oms = OrderManager(ledgers, adapter)
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    account_ledger = AccountLedger(ledgers, account)
    order = _order(btc_usdt, timestamp)
    oms.create(order)
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=timestamp,
        hard_limits=(RiskLimit(name="max_order_notional", limit=Decimal("250"), unit="USD"),),
        approved_by="human-reviewer",
    )
    risk_market = RiskMarketState(marks={btc_usdt.canonical_id: Decimal("100")})
    check = RiskKernel().check_order(
        order=order,
        account=account,
        market=risk_market,
        policy=policy,
    )
    oms.approve_risk(
        order.artifact_id,
        RiskDecision(
            target_portfolio_id=uuid4(),
            risk_policy_id=policy.artifact_id,
            outcome=RiskOutcome.APPROVED,
            authoritative_state_hash=_authoritative_state_hash(account, risk_market),
        ),
        order_check=check,
    )
    oms.route(order.artifact_id)
    fill = Fill(
        order_id=order.artifact_id,
        venue_fill_id="account-recon-fill",
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee=Decimal("0"),
        occurred_at=timestamp,
    )
    oms.record_fill(fill, "buy")
    assert (
        not ReconciliationService()
        .run(account=account, orders=oms, account_ledger=account_ledger)
        .reconciled
    )
    account_ledger.apply_fill(fill, "buy", btc_usdt.canonical_id)
    assert "account-recon-fill" in account_ledger.applied_fill_ids
    oms.reconcile(order.artifact_id)
    assert (
        ReconciliationService()
        .run(account=account, orders=oms, account_ledger=account_ledger)
        .reconciled
    )


def test_reconciliation_compares_independent_venue_projection_and_persists_record(
    tmp_path, btc_usdt, timestamp
):
    ledgers = SqliteLedgers(tmp_path / "recon-venue.sqlite")
    oms = OrderManager(ledgers, PaperVenueAdapter())
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    order = _order(btc_usdt, timestamp)
    oms.create(order)
    oms.transition(order.artifact_id, OrderState.REJECTED)
    oms.reconcile(order.artifact_id)
    service = ReconciliationService(ledgers)
    result = service.run(
        account=account,
        orders=oms,
        venue_snapshot=VenueAccountSnapshot(
            as_of=timestamp,
            cash=Decimal("999"),
            positions={btc_usdt.canonical_id: Decimal("1")},
        ),
    )
    assert not result.reconciled
    assert any(reason.startswith("venue_cash_mismatch") for reason in result.discrepancies)
    assert any(reason.startswith("venue_position_mismatch") for reason in result.discrepancies)
    assert any(
        event.event_type == "reconciliation_recorded"
        for event in ledgers.events(LedgerNamespace.ORDER)
    )


def test_reconciliation_compares_venue_open_order_projection(tmp_path, btc_usdt, timestamp):
    ledgers = SqliteLedgers(tmp_path / "recon-open-orders.sqlite")
    oms = OrderManager(ledgers, PaperVenueAdapter())
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    order = _order(btc_usdt, timestamp)
    oms.create(order)
    oms.transition(order.artifact_id, OrderState.RISK_APPROVED)
    oms.route(order.artifact_id)
    result = ReconciliationService().run(
        account=account,
        orders=oms,
        venue_snapshot=VenueAccountSnapshot(
            as_of=timestamp,
            cash=Decimal("1000"),
            positions={},
            venue_open_order_ids=frozenset(),
        ),
    )
    assert not result.reconciled
    assert any(
        reason.startswith("local_open_orders_missing_at_venue") for reason in result.discrepancies
    )


def test_reconciliation_projection_is_idempotent_for_unchanged_state(tmp_path, btc_usdt, timestamp):
    ledgers = SqliteLedgers(tmp_path / "recon-idempotent.sqlite")
    orders = OrderManager(ledgers, PaperVenueAdapter())
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    service = ReconciliationService(ledgers)
    first = service.run(account=account, orders=orders)
    second = service.run(account=account, orders=orders)
    assert first.artifact_id == second.artifact_id
    assert (
        len(
            [
                event
                for event in ledgers.events(LedgerNamespace.ORDER)
                if event.event_type == "reconciliation_recorded"
            ]
        )
        == 1
    )


def test_target_builder_applies_portfolio_constraints(btc_usdt, timestamp):
    account = AccountState(cash=Decimal("1000"))
    constraints = PortfolioConstraints(
        max_gross_notional=Decimal("50"),
        min_cash_reserve=Decimal("950"),
        max_liquidity_participation=Decimal("0.2"),
        liquidity_notional={btc_usdt.canonical_id: Decimal("1000")},
    )
    with pytest.raises(ValueError, match="gross notional"):
        TargetPortfolioBuilder().build(
            snapshot=_snapshot(timestamp),
            account=account,
            targets={btc_usdt: Decimal("1")},
            marks={btc_usdt.canonical_id: Decimal("100")},
            constraints=constraints,
        )


def test_oms_requires_order_level_risk_evidence(tmp_path, btc_usdt, timestamp):
    oms = OrderManager(SqliteLedgers(tmp_path / "state.sqlite"), PaperVenueAdapter())
    order = _order(btc_usdt, timestamp)
    oms.create(order)
    with pytest.raises(OrderStateError, match="order-level risk check"):
        oms.approve_risk(
            order.artifact_id,
            RiskDecision(
                target_portfolio_id=uuid4(),
                risk_policy_id=uuid4(),
                outcome=RiskOutcome.APPROVED,
                authoritative_state_hash="a" * 64,
            ),
        )


def test_oms_rejects_state_hash_mismatch_from_order_level_check(tmp_path, btc_usdt, timestamp):
    oms = OrderManager(SqliteLedgers(tmp_path / "hash-state.sqlite"), PaperVenueAdapter())
    order = _order(btc_usdt, timestamp)
    oms.create(order)
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=timestamp,
        hard_limits=(),
        approved_by="human-reviewer",
    )
    account = AccountState(cash=Decimal("1000"))
    market = RiskMarketState(marks={btc_usdt.canonical_id: Decimal("100")})
    check = RiskKernel().check_order(order=order, account=account, market=market, policy=policy)
    with pytest.raises(OrderStateError, match="state hash"):
        oms.approve_risk(
            order.artifact_id,
            RiskDecision(
                target_portfolio_id=uuid4(),
                risk_policy_id=policy.artifact_id,
                outcome=RiskOutcome.APPROVED,
                authoritative_state_hash="a" * 64,
            ),
            order_check=check,
        )


def test_oms_rejects_policy_mismatch_from_order_level_check(tmp_path, btc_usdt, timestamp):
    oms = OrderManager(SqliteLedgers(tmp_path / "policy-state.sqlite"), PaperVenueAdapter())
    order = _order(btc_usdt, timestamp)
    oms.create(order)
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=timestamp,
        hard_limits=(),
        approved_by="human-reviewer",
    )
    account = AccountState(cash=Decimal("1000"))
    market = RiskMarketState(marks={btc_usdt.canonical_id: Decimal("100")})
    check = RiskKernel().check_order(order=order, account=account, market=market, policy=policy)
    with pytest.raises(OrderStateError, match="policy"):
        oms.approve_risk(
            order.artifact_id,
            RiskDecision(
                target_portfolio_id=uuid4(),
                risk_policy_id=uuid4(),
                outcome=RiskOutcome.APPROVED,
                authoritative_state_hash=check.authoritative_state_hash or "",
            ),
            order_check=check,
        )


def test_oms_keeps_routed_order_on_venue_outage_and_rejects_duplicate_fill(
    tmp_path, btc_usdt, timestamp
):
    ledgers = SqliteLedgers(tmp_path / "state.sqlite")
    adapter = PaperVenueAdapter()
    oms = OrderManager(ledgers, adapter)
    order = _order(btc_usdt, timestamp)
    oms.create(order)
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=timestamp,
        hard_limits=(RiskLimit(name="max_order_notional", limit=Decimal("250"), unit="USD"),),
        approved_by="human-reviewer",
    )
    risk_account = AccountState(cash=Decimal("1000"))
    risk_market = RiskMarketState(marks={btc_usdt.canonical_id: Decimal("100")})
    order_check = RiskKernel().check_order(
        order=order,
        account=risk_account,
        market=risk_market,
        policy=policy,
    )
    oms.approve_risk(
        order.artifact_id,
        RiskDecision(
            target_portfolio_id=uuid4(),
            risk_policy_id=policy.artifact_id,
            outcome=RiskOutcome.APPROVED,
            authoritative_state_hash=_authoritative_state_hash(risk_account, risk_market),
        ),
        order_check=order_check,
    )
    adapter.inject_outage_once()
    with pytest.raises(RuntimeError, match="outage"):
        oms.route(order.artifact_id)
    assert oms.orders[order.artifact_id].state is OrderState.ROUTED
    with pytest.raises(OrderStateError, match="invalid order transition"):
        oms.route(order.artifact_id)
    assert adapter.open_orders() == ()
    assert (
        not ReconciliationService()
        .run(account=AccountState(cash=Decimal("1000")), orders=oms)
        .reconciled
    )
    oms.expire_unacknowledged(order.artifact_id)
    oms.reconcile(order.artifact_id)
    assert (
        ReconciliationService()
        .run(account=AccountState(cash=Decimal("1000")), orders=oms)
        .reconciled
    )


def test_tca_records_fees_spread_and_fill_ratio(btc_usdt, timestamp):
    order_id = uuid4()
    fills = (
        Fill(
            order_id=order_id,
            venue_fill_id="tca-1",
            quantity=Decimal("0.5"),
            price=Decimal("101"),
            fee=Decimal("0.1"),
            occurred_at=timestamp,
        ),
    )
    report = compute_tca(
        order_id=order_id,
        order_quantity=Decimal("1"),
        side="buy",
        arrival_price=Decimal("100"),
        fills=fills,
        best_bid=Decimal("99"),
        best_ask=Decimal("101"),
    )
    assert report.fill_ratio == Decimal("0.5")
    assert report.fees == Decimal("0.1")
    assert report.spread_cost == Decimal("0.5")
    assert report.opportunity_cost == Decimal("0")


def test_tca_records_market_impact_and_venue_performance(btc_usdt, timestamp):
    order_id = uuid4()
    fill = Fill(
        order_id=order_id,
        venue_fill_id="tca-impact",
        quantity=Decimal("1"),
        price=Decimal("101"),
        fee=Decimal("0.1"),
        occurred_at=timestamp,
    )
    report = compute_tca(
        order_id=order_id,
        order_quantity=Decimal("1"),
        side="buy",
        arrival_price=Decimal("100"),
        fills=(fill,),
        market_impact=Decimal("0.25"),
        venue="approved-paper-venue",
        venue_performance=Decimal("0.98"),
    )
    assert report.market_impact == Decimal("0.25")
    assert report.venue == "approved-paper-venue"
    assert report.venue_performance == Decimal("0.98")
    assert report.implementation_shortfall >= Decimal("1.35")
