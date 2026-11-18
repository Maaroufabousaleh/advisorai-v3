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
    AccountStateSnapshot,
    AmbiguousAcknowledgement,
    DeterministicExecutionPolicy,
    ExecutionPolicyKind,
    KillSwitch,
    MarketEvent,
    NativeVenueAdapter,
    NativeVenueProjectionError,
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
    LedgerEvent,
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


def test_native_open_order_projection_resolves_client_ids_and_rejects_unknown_records(
    btc_usdt, timestamp
):
    order = _order(btc_usdt, timestamp)

    class Transport:
        def __init__(self, records):
            self.records = records

        def submit_order(self, payload):
            return {"accepted": True, "venue_order_id": "native-known"}

        def query_order(self, *, client_order_id):
            return None

        def list_open_orders(self):
            return self.records

    transport = Transport(
        [{"client_order_id": order.idempotency_key, "id": "native-known", "accepted": True}]
    )
    adapter = NativeVenueAdapter(venue="venue", environment="testnet", transport=transport)
    adapter.submit(order)
    assert adapter.open_orders()[0].order_id == order.artifact_id

    unknown = NativeVenueAdapter(
        venue="venue",
        environment="testnet",
        transport=Transport([{"id": "external-only", "accepted": True}]),
    )
    with pytest.raises(NativeVenueProjectionError, match="unknown local order identity"):
        unknown.open_orders()


def test_native_open_order_projection_rejects_invalid_acceptance_and_conflicts(btc_usdt, timestamp):
    order = _order(btc_usdt, timestamp)

    class Transport:
        def __init__(self):
            self.records = []

        def submit_order(self, payload):
            return {"accepted": True, "venue_order_id": "native-one"}

        def query_order(self, *, client_order_id):
            return None

        def list_open_orders(self):
            return self.records

    transport = Transport()
    adapter = NativeVenueAdapter(venue="venue", environment="testnet", transport=transport)
    adapter.submit(order)
    transport.records = [
        {"local_order_id": str(order.artifact_id), "id": "native-one", "accepted": "true"}
    ]
    with pytest.raises(NativeVenueProjectionError, match="non-boolean"):
        adapter.open_orders()
    transport.records = [
        {"local_order_id": str(order.artifact_id), "id": "native-other", "accepted": True}
    ]
    with pytest.raises(NativeVenueProjectionError, match="conflicts"):
        adapter.open_orders()


def test_native_open_order_projection_drops_orders_absent_from_latest_snapshot(btc_usdt, timestamp):
    order = _order(btc_usdt, timestamp)

    class Transport:
        def __init__(self):
            self.records = [{"order_id": str(order.artifact_id), "id": "native-stale"}]

        def submit_order(self, payload):
            return {"accepted": True, "venue_order_id": "native-stale"}

        def query_order(self, *, client_order_id):
            return None

        def list_open_orders(self):
            records, self.records = self.records, []
            return records

    adapter = NativeVenueAdapter(venue="venue", environment="testnet", transport=Transport())
    assert adapter.open_orders()[0].venue_order_id == "native-stale"
    assert adapter.open_orders() == ()


def test_native_open_order_projection_rehydrates_client_id_bindings_after_restart(
    tmp_path, btc_usdt, timestamp
):
    class Transport:
        def __init__(self, records=()):
            self.records = records

        def submit_order(self, payload):
            return {"accepted": True, "venue_order_id": "native-restart"}

        def query_order(self, *, client_order_id):
            return None

        def list_open_orders(self):
            return self.records

    ledgers = SqliteLedgers(tmp_path / "native-restart.sqlite")
    first_adapter = NativeVenueAdapter(venue="venue", environment="testnet", transport=Transport())
    first_oms = OrderManager(ledgers, first_adapter)
    order = _order(btc_usdt, timestamp)
    first_oms.create(order)
    first_oms.transition(order.artifact_id, OrderState.RISK_APPROVED)
    first_oms.route(order.artifact_id)

    restarted_adapter = NativeVenueAdapter(
        venue="venue",
        environment="testnet",
        transport=Transport(
            [
                {
                    "client_order_id": order.idempotency_key,
                    "id": "native-restart",
                    "accepted": True,
                }
            ]
        ),
    )
    OrderManager(ledgers, restarted_adapter)
    assert restarted_adapter.open_orders()[0].order_id == order.artifact_id


def test_native_adapter_refuses_blank_venue_or_negative_cap(btc_usdt, timestamp):
    """P02/P09 prep: the native adapter validates at construction.

    A blank venue name and a negative submission cap are refused
    (the live-environment refusal is pinned by the client-id test).
    Pure unit coverage; no network, no credentials.
    """

    class Transport:
        def submit_order(self, payload):
            raise AssertionError("refused before any transport use")

        def query_order(self, *, client_order_id):
            raise AssertionError("refused before any transport use")

    with pytest.raises(ValueError, match="venue name is required"):
        NativeVenueAdapter(venue="  ", environment="testnet", transport=Transport())
    with pytest.raises(ValueError, match="cap cannot be negative"):
        NativeVenueAdapter(
            venue="venue",
            environment="testnet",
            transport=Transport(),
            max_submissions=-1,
        )


def test_native_adapter_refuses_venue_drift_and_rebound_keys(btc_usdt, timestamp):
    """P02/P09 prep: strict venue and idempotency-key binding hold.

    A strict adapter refuses an order whose instrument venue differs;
    a rebound idempotency key onto a different local order is refused
    before any transport submission. No network, no credentials.
    """

    class Transport:
        def __init__(self):
            self.submissions = 0

        def submit_order(self, payload):
            self.submissions += 1
            return {"accepted": True, "venue_order_id": "native-strict"}

        def query_order(self, *, client_order_id):
            return None

    transport = Transport()
    strict = NativeVenueAdapter(
        venue="venue", environment="testnet", transport=transport, strict_venue=True
    )
    foreign = _order(btc_usdt, timestamp).model_copy(
        update={"instrument": btc_usdt.model_copy(update={"venue": "live"})}
    )
    with pytest.raises(ValueError, match="does not match native venue"):
        strict.submit(foreign)
    assert transport.submissions == 0

    order = _order(btc_usdt, timestamp)
    rebound = order.model_copy(update={"artifact_id": uuid4()})
    assert rebound.idempotency_key == order.idempotency_key
    loose = NativeVenueAdapter(venue="venue", environment="testnet", transport=transport)
    loose.submit(order)
    assert transport.submissions == 1
    with pytest.raises(NativeVenueProjectionError, match="already bound to a different"):
        loose.submit(rebound)
    assert transport.submissions == 1


def test_native_adapter_refuses_misshapen_acknowledgements(btc_usdt, timestamp):
    """P02/P09 prep: misshapen venue acks fail closed.

    A non-mapping ack, an ack without a venue order id, and an ack
    with a non-boolean acceptance are all refused. No network, no
    credentials.
    """

    class ScriptedTransport:
        def __init__(self, response):
            self.response = response

        def submit_order(self, payload):
            return self.response

        def query_order(self, *, client_order_id):
            return None

    def submit(response):
        adapter = NativeVenueAdapter(
            venue="venue", environment="testnet", transport=ScriptedTransport(response)
        )
        return adapter.submit(_order(btc_usdt, timestamp))

    with pytest.raises(RuntimeError, match="non-mapping acknowledgement"):
        submit(["native-1"])
    with pytest.raises(RuntimeError, match="rejected order"):
        submit({"accepted": True, "venue_order_id": "  "})
    with pytest.raises(RuntimeError, match="rejected order"):
        submit({"accepted": "yes", "venue_order_id": "native-1"})


def test_native_adapter_cancel_requires_an_explicit_typed_endpoint(btc_usdt, timestamp):
    """P02/P09 prep: cancellation without a typed endpoint fails closed.

    A transport without ``cancel_order``, a non-mapping cancellation,
    and a cancellation with a non-boolean result are all refused
    instead of pretending the local transition cancelled the venue
    order. No network, no credentials.
    """
    order = _order(btc_usdt, timestamp)

    class NoCancelTransport:
        def submit_order(self, payload):
            return {"accepted": True, "venue_order_id": "native-cancel"}

        def query_order(self, *, client_order_id):
            return None

    bare = NativeVenueAdapter(
        venue="venue", environment="testnet", transport=NoCancelTransport()
    )
    with pytest.raises(NativeVenueProjectionError, match="does not expose cancellation"):
        bare.cancel(order)

    class ScriptedTransport:
        def __init__(self, response):
            self.response = response

        def submit_order(self, payload):
            return {"accepted": True, "venue_order_id": "native-cancel"}

        def query_order(self, *, client_order_id):
            return None

        def cancel_order(self, *, client_order_id):
            return self.response

    def cancel(response):
        adapter = NativeVenueAdapter(
            venue="venue", environment="testnet", transport=ScriptedTransport(response)
        )
        return adapter.cancel(order)

    with pytest.raises(NativeVenueProjectionError, match="non-mapping cancellation"):
        cancel(["cancelled"])
    with pytest.raises(NativeVenueProjectionError, match="invalid cancellation result"):
        cancel({"cancelled": "eventually"})
    assert cancel({"cancelled": True}) is True


def test_native_open_order_projection_validates_record_shape(btc_usdt, timestamp):
    """P02/P09 prep: misshapen open-order projections fail closed.

    A non-sequence projection, a non-object record, a record without
    a venue identity, an unknown client order, and an invalid local
    identity are all refused. No network, no credentials.
    """
    order = _order(btc_usdt, timestamp)

    class ScriptedTransport:
        def __init__(self, records):
            self.records = records

        def submit_order(self, payload):
            return {"accepted": True, "venue_order_id": "native-known"}

        def query_order(self, *, client_order_id):
            return None

        def list_open_orders(self):
            return self.records

    def project(records):
        adapter = NativeVenueAdapter(
            venue="venue", environment="testnet", transport=ScriptedTransport(records)
        )
        adapter.submit(order)
        return adapter.open_orders()

    with pytest.raises(NativeVenueProjectionError, match="must be a sequence"):
        project({"id": "native-known"})
    with pytest.raises(NativeVenueProjectionError, match="non-object record"):
        project(["native-known"])
    with pytest.raises(NativeVenueProjectionError, match="missing a venue order identity"):
        project([{"client_order_id": order.idempotency_key, "accepted": True}])
    with pytest.raises(NativeVenueProjectionError, match="unknown client order"):
        project([{"client_order_id": "ghost-key", "id": "native-ghost"}])
    with pytest.raises(NativeVenueProjectionError, match="invalid local order identity"):
        project([{"local_order_id": "not-a-uuid", "id": "native-bad"}])


def test_native_account_snapshot_requires_an_explicit_endpoint(btc_usdt, timestamp):
    """P02/P09 prep: account reads without an endpoint fail closed.

    A transport without ``fetch_account_snapshot`` is refused instead
    of returning a fabricated balance. No network, no credentials.
    """

    class NoAccountTransport:
        def submit_order(self, payload):
            return {"accepted": True, "venue_order_id": "native-account"}

        def query_order(self, *, client_order_id):
            return None

    bare = NativeVenueAdapter(
        venue="venue", environment="testnet", transport=NoAccountTransport()
    )
    with pytest.raises(RuntimeError, match="does not expose account snapshots"):
        bare.account_snapshot()


def test_oms_reconnect_queries_completed_order_before_open_order_projection(
    tmp_path, btc_usdt, timestamp
):
    class Transport:
        def submit_order(self, payload):
            return {"accepted": True, "venue_order_id": "native-query"}

        def query_order(self, *, client_order_id):
            return {"id": "native-query", "state": "filled"}

        def list_open_orders(self):
            return ()

    ledgers = SqliteLedgers(tmp_path / "native-query.sqlite")
    adapter = NativeVenueAdapter(venue="venue", environment="testnet", transport=Transport())
    orders = OrderManager(ledgers, adapter)
    order = _order(btc_usdt, timestamp)
    orders.create(order)
    orders.transition(order.artifact_id, OrderState.RISK_APPROVED)
    orders.transition(order.artifact_id, OrderState.ROUTED)

    acknowledgement = orders.reconcile_routed(order.artifact_id)
    assert acknowledgement.venue_order_id == "native-query"
    assert orders.orders[order.artifact_id].state is OrderState.ACKNOWLEDGED


def test_oms_query_can_resolve_a_rejected_ambiguous_order(tmp_path, btc_usdt, timestamp):
    class Transport:
        def submit_order(self, payload):
            raise AmbiguousAcknowledgement("ack timeout")

        def query_order(self, *, client_order_id):
            return {"id": "native-rejected", "state": "rejected"}

        def list_open_orders(self):
            return ()

    ledgers = SqliteLedgers(tmp_path / "native-rejected.sqlite")
    adapter = NativeVenueAdapter(venue="venue", environment="testnet", transport=Transport())
    orders = OrderManager(ledgers, adapter)
    order = _order(btc_usdt, timestamp)
    orders.create(order)
    orders.transition(order.artifact_id, OrderState.RISK_APPROVED)
    assert orders.route(order.artifact_id) is None

    acknowledgement = orders.reconcile_ambiguous(order.artifact_id)
    assert not acknowledgement.accepted
    assert orders.orders[order.artifact_id].state is OrderState.REJECTED


def test_oms_query_rejects_a_different_client_order_identity(tmp_path, btc_usdt, timestamp):
    class Transport:
        def submit_order(self, payload):
            return {"accepted": True, "venue_order_id": "native-query"}

        def query_order(self, *, client_order_id):
            return {
                "client_order_id": f"{client_order_id}-different",
                "id": "native-query",
                "state": "filled",
            }

        def list_open_orders(self):
            return ()

    ledgers = SqliteLedgers(tmp_path / "native-query-identity.sqlite")
    adapter = NativeVenueAdapter(venue="venue", environment="testnet", transport=Transport())
    orders = OrderManager(ledgers, adapter)
    order = _order(btc_usdt, timestamp)
    orders.create(order)
    orders.transition(order.artifact_id, OrderState.RISK_APPROVED)
    orders.transition(order.artifact_id, OrderState.ROUTED)

    with pytest.raises(OrderStateError, match="different client order"):
        orders.reconcile_routed(order.artifact_id)
    assert orders.orders[order.artifact_id].state is OrderState.ROUTED


def test_order_manager_persists_cancel_intent_and_requires_venue_ack(tmp_path, btc_usdt, timestamp):
    ledgers = SqliteLedgers(tmp_path / "cancel-transition.sqlite")
    adapter = PaperVenueAdapter()
    orders = OrderManager(ledgers, adapter)
    order = _order(btc_usdt, timestamp)
    orders.create(order)
    orders.transition(order.artifact_id, OrderState.RISK_APPROVED)
    assert orders.route(order.artifact_id).accepted
    pending = orders.cancel(order.artifact_id)
    assert pending.state is OrderState.CANCEL_PENDING
    assert orders.acknowledge_cancel(order.artifact_id).state is OrderState.CANCELLED
    assert orders.reconcile(order.artifact_id).state is OrderState.RECONCILED


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


def test_reconciliation_accepts_newer_venue_observation_when_balances_match(tmp_path, timestamp):
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    orders = OrderManager(SqliteLedgers(tmp_path / "newer-venue.sqlite"), PaperVenueAdapter())
    result = ReconciliationService().run(
        account=account,
        orders=orders,
        venue_snapshot=VenueAccountSnapshot(
            as_of=timestamp + timedelta(minutes=5),
            cash=Decimal("1000"),
            positions={},
        ),
    )
    assert result.reconciled is True, result.discrepancies


def test_reconciliation_rejects_venue_observation_older_than_local_account_state(
    tmp_path, timestamp
):
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    orders = OrderManager(SqliteLedgers(tmp_path / "older-venue.sqlite"), PaperVenueAdapter())
    result = ReconciliationService().run(
        account=account,
        orders=orders,
        venue_snapshot=VenueAccountSnapshot(
            as_of=timestamp - timedelta(minutes=5),
            cash=Decimal("1000"),
            positions={},
        ),
    )
    assert result.reconciled is False
    assert "venue_account_snapshot_predates_local_state" in result.discrepancies


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


def test_account_state_refuses_misshapen_construction(timestamp):
    """P02/P09 prep: account state validates at construction.

    Non-finite monetary values, blank identifiers, non-finite
    positions/marks, non-positive marks, negative average costs,
    negative fee/funding/margin ledgers, negative cash, and naive
    timestamps are all refused. Pure unit coverage; no network, no
    credentials.
    """
    with pytest.raises(ValueError, match="monetary values must be finite"):
        AccountState(cash=Decimal("NaN"), as_of=timestamp)
    with pytest.raises(ValueError, match="identifiers cannot be blank"):
        AccountState(cash=Decimal("100"), positions={"  ": Decimal("1")}, as_of=timestamp)
    with pytest.raises(ValueError, match="positions and marks must be finite"):
        AccountState(
            cash=Decimal("100"),
            positions={"BTC": Decimal("Infinity")},
            as_of=timestamp,
        )
    with pytest.raises(ValueError, match="marks must be positive"):
        AccountState(cash=Decimal("100"), marks={"BTC": Decimal("0")}, as_of=timestamp)
    with pytest.raises(ValueError, match="average costs cannot be negative"):
        AccountState(
            cash=Decimal("100"), average_cost={"BTC": Decimal("-1")}, as_of=timestamp
        )
    with pytest.raises(ValueError, match="fees, funding, and margin cannot be negative"):
        AccountState(cash=Decimal("100"), fees_paid=Decimal("-1"), as_of=timestamp)
    with pytest.raises(ValueError, match="cash cannot start negative"):
        AccountState(cash=Decimal("-1"), as_of=timestamp)
    with pytest.raises(ValueError, match="peak equity cannot be negative"):
        AccountState(
            cash=Decimal("100"), peak_equity=Decimal("-1"), as_of=timestamp
        )
    with pytest.raises(ValueError, match="timestamp must include a timezone"):
        AccountState(cash=Decimal("100"), as_of=timestamp.replace(tzinfo=None))


def test_account_mutations_refuse_misshapen_adjustments(timestamp):
    """P02/P09 prep: account mutations validate their adjustments.

    Negative funding, zero cash transfers, negative borrows, zero FX
    and corporate-action adjustments, blank split/dividend
    instruments, non-positive split ratios, negative dividend rates,
    negative margin values, blank mark instruments, and non-positive
    mark prices are all refused without mutating state. Pure unit
    coverage; no network, no credentials.
    """
    account = AccountState(cash=Decimal("100"), as_of=timestamp)
    before = account.snapshot()
    with pytest.raises(ValueError, match="funding amount must be finite"):
        account.apply_funding(Decimal("-1"), timestamp)
    with pytest.raises(ValueError, match="transfer amount cannot be zero"):
        account.apply_cash_transfer(Decimal("0"), timestamp)
    with pytest.raises(ValueError, match="borrow amount must be finite"):
        account.apply_borrow(Decimal("-1"), timestamp)
    with pytest.raises(ValueError, match="FX adjustment must be finite"):
        account.apply_fx_adjustment(Decimal("0"), timestamp)
    with pytest.raises(ValueError, match="corporate action adjustment must be finite"):
        account.apply_corporate_action(Decimal("0"), timestamp)
    with pytest.raises(ValueError, match="split instrument identifier cannot be blank"):
        account.apply_split("  ", Decimal("2"), timestamp)
    with pytest.raises(ValueError, match="split ratio must be finite and positive"):
        account.apply_split("BTC", Decimal("0"), timestamp)
    with pytest.raises(ValueError, match="dividend instrument identifier cannot be blank"):
        account.apply_dividend("  ", Decimal("1"), timestamp)
    with pytest.raises(ValueError, match="dividend cash per unit must be finite"):
        account.apply_dividend("BTC", Decimal("-1"), timestamp)
    with pytest.raises(ValueError, match="margin values cannot be negative"):
        account.update_margin(
            margin_used=Decimal("-1"), margin_available=Decimal("10"), as_of=timestamp
        )
    with pytest.raises(ValueError, match="mark instrument identifier cannot be blank"):
        account.mark("  ", Decimal("100"), timestamp)
    with pytest.raises(ValueError, match="mark price must be positive"):
        account.mark("BTC", Decimal("0"), timestamp)
    assert account.snapshot() == before


def test_account_fill_refuses_blank_instrument_or_side(timestamp):
    """P02/P09 prep: fill ingestion validates instrument and side.

    A blank instrument identifier and an unknown side are refused
    before any position moves. Pure unit coverage; no network, no
    credentials.
    """
    account = AccountState(cash=Decimal("100"), as_of=timestamp)
    fill = Fill(
        order_id=uuid4(),
        venue_fill_id="venue-fill-1",
        quantity=Decimal("0.01"),
        price=Decimal("50000"),
        fee=Decimal("0.1"),
        fee_currency="USDT",
        occurred_at=timestamp,
    )
    with pytest.raises(ValueError, match="fill instrument identifier cannot be blank"):
        account.apply_fill(fill, "buy", "  ")
    with pytest.raises(ValueError, match="side must be buy or sell"):
        account.apply_fill(fill, "hold", "BTC")
    assert account.positions == {}


def test_account_state_snapshot_refuses_misshapen_projection(timestamp):
    """P02/P09 prep: the frozen snapshot validates at construction.

    Non-finite values, blank position/mark/average-cost identifiers,
    negative average costs, duplicate identifiers, naive timestamps,
    and non-digest state hashes are all refused. Pure unit coverage;
    no network, no credentials.
    """
    digest = "ab" * 32
    base = {
        "cash": Decimal("100"),
        "positions": (("BTC", Decimal("0.01")),),
        "marks": (("BTC", Decimal("50000")),),
        "fees_paid": Decimal("0"),
        "funding_paid": Decimal("0"),
        "realized_pnl": Decimal("0"),
        "unrealized_pnl": Decimal("0"),
        "margin_used": Decimal("0"),
        "margin_available": None,
        "as_of": timestamp,
        "state_hash": digest,
    }
    with pytest.raises(ValueError, match="snapshot values must be finite"):
        AccountStateSnapshot(**{**base, "cash": Decimal("NaN")})
    with pytest.raises(ValueError, match="position identifiers cannot be blank"):
        AccountStateSnapshot(
            **{**base, "positions": (("  ", Decimal("0.01")),)}
        )
    with pytest.raises(ValueError, match="mark identifiers cannot be blank"):
        AccountStateSnapshot(**{**base, "marks": (("  ", Decimal("50000")),)})
    with pytest.raises(ValueError, match="average-cost identifiers cannot be blank"):
        AccountStateSnapshot(
            **{**base, "average_cost": (("  ", Decimal("1")),)}
        )
    with pytest.raises(ValueError, match="average costs must be finite"):
        AccountStateSnapshot(
            **{**base, "average_cost": (("BTC", Decimal("-1")),)}
        )
    with pytest.raises(ValueError, match="positions must be unique"):
        AccountStateSnapshot(
            **{
                **base,
                "positions": (("BTC", Decimal("1")), ("BTC", Decimal("2"))),
            }
        )
    with pytest.raises(ValueError, match="marks must be unique"):
        AccountStateSnapshot(
            **{**base, "marks": (("BTC", Decimal("1")), ("BTC", Decimal("2")))}
        )
    with pytest.raises(ValueError, match="average costs must be unique"):
        AccountStateSnapshot(
            **{
                **base,
                "average_cost": (("BTC", Decimal("1")), ("BTC", Decimal("2"))),
            }
        )
    with pytest.raises(ValueError, match="snapshot timestamp must include a timezone"):
        AccountStateSnapshot(**{**base, "as_of": timestamp.replace(tzinfo=None)})
    with pytest.raises(ValueError, match="state_hash must be a lowercase"):
        AccountStateSnapshot(**{**base, "state_hash": "NOT-A-DIGEST"})


def test_venue_account_snapshot_refuses_misshapen_projection(timestamp):
    """P02/P09 prep: the venue projection validates at construction.

    Naive timestamps, non-Decimal values, non-string or blank
    position identifiers, non-finite values, misshapen margins, and
    non-UUID open-order identifiers are all refused before any
    reconciliation comparison. Pure unit coverage; no network, no
    credentials.
    """
    with pytest.raises(ValueError, match="snapshot timestamp must include a timezone"):
        VenueAccountSnapshot(
            as_of=timestamp.replace(tzinfo=None),
            cash=Decimal("100"),
            positions={},
        )
    with pytest.raises(ValueError, match="account values must be Decimal instances"):
        VenueAccountSnapshot(as_of=timestamp, cash=100.0, positions={})
    with pytest.raises(ValueError, match="position identifiers must be strings"):
        VenueAccountSnapshot(
            as_of=timestamp, cash=Decimal("100"), positions={123: Decimal("1")}
        )
    with pytest.raises(ValueError, match="account values must be finite"):
        VenueAccountSnapshot(
            as_of=timestamp,
            cash=Decimal("100"),
            positions={"BTC": Decimal("NaN")},
        )
    with pytest.raises(ValueError, match="position identifiers cannot be blank"):
        VenueAccountSnapshot(
            as_of=timestamp,
            cash=Decimal("100"),
            positions={"  ": Decimal("1")},
        )
    with pytest.raises(ValueError, match="margin used must be"):
        VenueAccountSnapshot(
            as_of=timestamp,
            cash=Decimal("100"),
            positions={},
            margin_used=Decimal("-1"),
        )
    with pytest.raises(ValueError, match="margin available must be"):
        VenueAccountSnapshot(
            as_of=timestamp,
            cash=Decimal("100"),
            positions={},
            margin_available=Decimal("-1"),
        )
    with pytest.raises(ValueError, match="open order identifiers must be UUIDs"):
        VenueAccountSnapshot(
            as_of=timestamp,
            cash=Decimal("100"),
            positions={},
            venue_open_order_ids=frozenset({"not-a-uuid"}),
        )


def test_account_ledger_requires_event_identifiers(timestamp, tmp_path):
    """P02/P09 prep: ledger-coupled mutations require identifiers.

    Blank funding, cash-transfer, borrow, mark, and margin
    identifiers, blank split/dividend instrument/action pairs, and
    split/dividend corporate-action records missing their amounts
    are all refused before any ledger write. Pure unit coverage over
    a throwaway SQLite ledger; no network, no credentials.
    """
    from types import SimpleNamespace

    ledger = AccountLedger(
        SqliteLedgers(tmp_path / "ledgers.sqlite"),
        AccountState(cash=Decimal("1000"), as_of=timestamp),
    )
    with pytest.raises(ValueError, match="funding ID is required"):
        ledger.apply_funding(Decimal("1"), timestamp, "  ")
    with pytest.raises(ValueError, match="cash transfer ID is required"):
        ledger.apply_cash_transfer(Decimal("1"), timestamp, "  ")
    with pytest.raises(ValueError, match="account adjustment ID is required"):
        ledger.apply_borrow(Decimal("1"), timestamp, "  ")
    with pytest.raises(ValueError, match="mark and instrument IDs are required"):
        ledger.mark("BTC", Decimal("100"), timestamp, "  ")
    with pytest.raises(ValueError, match="mark and instrument IDs are required"):
        ledger.mark("  ", Decimal("100"), timestamp, "mark-1")
    with pytest.raises(ValueError, match="margin ID is required"):
        ledger.update_margin(
            margin_used=Decimal("0"),
            margin_available=Decimal("100"),
            as_of=timestamp,
            margin_id="  ",
        )
    with pytest.raises(
        ValueError, match="corporate-action instrument and action IDs are required"
    ):
        ledger.apply_split("  ", Decimal("2"), timestamp, "split-1")
    with pytest.raises(
        ValueError, match="corporate-action instrument and action IDs are required"
    ):
        ledger.apply_dividend("BTC", Decimal("1"), timestamp, "  ")
    split_action = SimpleNamespace(
        action_type=SimpleNamespace(value="split"),
        instrument=SimpleNamespace(canonical_id="BTC"),
        action_id="split-record-1",
        effective_at=timestamp,
        ratio=None,
    )
    with pytest.raises(ValueError, match="split corporate actions require a ratio"):
        ledger.apply_corporate_action_record(split_action)
    dividend_action = SimpleNamespace(
        action_type=SimpleNamespace(value="dividend"),
        instrument=SimpleNamespace(canonical_id="BTC"),
        action_id="dividend-record-1",
        effective_at=timestamp,
        cash_amount=None,
    )
    with pytest.raises(
        ValueError, match="dividend corporate actions require a cash amount"
    ):
        ledger.apply_corporate_action_record(dividend_action)
    merger_action = SimpleNamespace(
        action_type=SimpleNamespace(value="merger"),
        instrument=SimpleNamespace(canonical_id="BTC"),
        action_id="merger-record-1",
        effective_at=timestamp,
        cash_amount=Decimal("0"),
    )
    with pytest.raises(ValueError, match="merger/spinoff accounting requires"):
        ledger.apply_corporate_action_record(merger_action)


def test_account_ledger_refuses_reused_keys_for_different_events(timestamp, tmp_path):
    """P02/P09 prep: idempotency keys bind to their first event.

    Reusing a funding, cash-transfer, borrow, mark, margin, split,
    or dividend key for changed content raises IdempotencyConflict
    naming the event kind; identical retries still dedupe. Pure unit
    coverage over a throwaway SQLite ledger; no network, no
    credentials.
    """
    ledger = AccountLedger(
        SqliteLedgers(tmp_path / "ledgers.sqlite"),
        AccountState(cash=Decimal("1000"), as_of=timestamp),
    )
    ledger.apply_funding(Decimal("1"), timestamp, "funding-1")
    ledger.apply_funding(Decimal("1"), timestamp, "funding-1")
    with pytest.raises(IdempotencyConflict, match="different funding"):
        ledger.apply_funding(Decimal("2"), timestamp, "funding-1")
    ledger.apply_cash_transfer(Decimal("5"), timestamp, "transfer-1")
    ledger.apply_cash_transfer(Decimal("5"), timestamp, "transfer-1")
    with pytest.raises(IdempotencyConflict, match="different cash transfer"):
        ledger.apply_cash_transfer(Decimal("6"), timestamp, "transfer-1")
    ledger.apply_borrow(Decimal("1"), timestamp, "borrow-1")
    ledger.apply_borrow(Decimal("1"), timestamp, "borrow-1")
    with pytest.raises(IdempotencyConflict, match="different account adjustment"):
        ledger.apply_borrow(Decimal("2"), timestamp, "borrow-1")
    ledger.mark("BTC", Decimal("100"), timestamp, "mark-1")
    ledger.mark("BTC", Decimal("100"), timestamp, "mark-1")
    with pytest.raises(IdempotencyConflict, match="different mark"):
        ledger.mark("BTC", Decimal("101"), timestamp, "mark-1")
    margin = {"margin_used": Decimal("0"), "margin_available": Decimal("100")}
    ledger.update_margin(**margin, as_of=timestamp, margin_id="margin-1")
    ledger.update_margin(**margin, as_of=timestamp, margin_id="margin-1")
    with pytest.raises(IdempotencyConflict, match="different margin"):
        ledger.update_margin(
            margin_used=Decimal("1"),
            margin_available=Decimal("100"),
            as_of=timestamp,
            margin_id="margin-1",
        )
    ledger.apply_split("BTC", Decimal("2"), timestamp, "split-1")
    ledger.apply_split("BTC", Decimal("2"), timestamp, "split-1")
    with pytest.raises(IdempotencyConflict, match="different corporate action"):
        ledger.apply_split("BTC", Decimal("3"), timestamp, "split-1")
    ledger.apply_dividend("BTC", Decimal("1"), timestamp, "dividend-1")
    ledger.apply_dividend("BTC", Decimal("1"), timestamp, "dividend-1")
    with pytest.raises(IdempotencyConflict, match="different corporate action"):
        ledger.apply_dividend("BTC", Decimal("2"), timestamp, "dividend-1")


def test_account_ledger_hydrate_refuses_corrupt_or_diverged_history(timestamp, tmp_path):
    """P02/P09 prep: restart hydration validates durable history.

    A durable fill event with an invalid payload and a durable
    event whose recorded state hash no longer matches the replayed
    account both raise IdempotencyConflict instead of hydrating a
    corrupt balance. Pure unit coverage over throwaway SQLite
    ledgers; no network, no credentials.
    """
    bad_ledgers = SqliteLedgers(tmp_path / "bad.sqlite")
    bad_ledgers.append(
        LedgerEvent(
            namespace=LedgerNamespace.ACCOUNT,
            event_type="fill_applied",
            idempotency_key="account-fill:bad-fill",
            payload={"venue_fill_id": "bad-fill"},
        )
    )
    with pytest.raises(IdempotencyConflict, match="invalid fill payload"):
        AccountLedger(
            bad_ledgers,
            AccountState(cash=Decimal("1000"), as_of=timestamp),
        )

    diverged_ledgers = SqliteLedgers(tmp_path / "diverged.sqlite")
    diverged_ledgers.append(
        LedgerEvent(
            namespace=LedgerNamespace.ACCOUNT,
            event_type="funding_applied",
            idempotency_key="funding:diverged-1",
            payload={
                "amount": "1",
                "occurred_at": timestamp.isoformat(),
                "funding_id": "diverged-1",
                "state_hash": "00" * 32,
            },
        )
    )
    with pytest.raises(IdempotencyConflict, match="state hash mismatch"):
        AccountLedger(
            diverged_ledgers,
            AccountState(cash=Decimal("1000"), as_of=timestamp),
        )


def test_oms_cancel_fails_closed_on_venue_refusals(tmp_path, btc_usdt, timestamp):
    """P02/P09 prep: cancellation validates the venue acknowledgement.

    A venue adapter without cancellation, a transport fault, a
    non-boolean acknowledgement, and a rejected cancellation are
    all refused with the order left in durable CANCEL_PENDING for
    recovery through reconciliation. Pure unit coverage over
    throwaway SQLite ledgers; no network, no credentials.
    """

    class StubAdapter:
        def __init__(self, cancel_behavior):
            self.cancel_behavior = cancel_behavior

        def submit(self, order):
            return VenueAcknowledgement(
                order_id=order.artifact_id,
                venue_order_id=f"stub-{order.artifact_id}",
                accepted=True,
            )

        def open_orders(self):
            return ()

        def cancel(self, order):
            if isinstance(self.cancel_behavior, Exception):
                raise self.cancel_behavior
            return self.cancel_behavior

    class NoCancelAdapter(StubAdapter):
        cancel = None

    def _routed(tmp_name, adapter):
        ledgers = SqliteLedgers(tmp_path / tmp_name)
        orders = OrderManager(ledgers, adapter)
        order = _order(btc_usdt, timestamp)
        orders.create(order)
        orders.transition(order.artifact_id, OrderState.RISK_APPROVED)
        assert orders.route(order.artifact_id).accepted
        return orders, order

    orders, order = _routed("cancel-no-adapter.sqlite", NoCancelAdapter(True))
    with pytest.raises(OrderStateError, match="does not expose cancellation"):
        orders.cancel(order.artifact_id)
    assert orders.orders[order.artifact_id].state is OrderState.CANCEL_PENDING

    orders, order = _routed(
        "cancel-ambiguous.sqlite", StubAdapter(RuntimeError("transport lost"))
    )
    with pytest.raises(OrderStateError, match="acknowledgement is ambiguous"):
        orders.cancel(order.artifact_id)
    assert orders.orders[order.artifact_id].state is OrderState.CANCEL_PENDING

    orders, order = _routed("cancel-nonbool.sqlite", StubAdapter("yes"))
    with pytest.raises(OrderStateError, match="must be boolean"):
        orders.cancel(order.artifact_id)
    assert orders.orders[order.artifact_id].state is OrderState.CANCEL_PENDING

    orders, order = _routed("cancel-rejected.sqlite", StubAdapter(False))
    with pytest.raises(OrderStateError, match="venue rejected cancellation"):
        orders.cancel(order.artifact_id)
    assert orders.orders[order.artifact_id].state is OrderState.CANCEL_PENDING


def test_oms_record_fill_validates_side_quantity_and_state(tmp_path, btc_usdt, timestamp):
    """P02/P09 prep: fill recording validates side, quantity, and state.

    An unknown side, a side mismatch against the canonical order,
    an overfill beyond order quantity, a fill on a non-fillable
    order, and a venue fill ID reused for different content are
    all refused. Pure unit coverage over throwaway SQLite ledgers;
    no network, no credentials.
    """
    ledgers = SqliteLedgers(tmp_path / "fill-guards.sqlite")
    orders = OrderManager(ledgers, PaperVenueAdapter())
    order = _order(btc_usdt, timestamp)
    orders.create(order)
    orders.transition(order.artifact_id, OrderState.RISK_APPROVED)
    assert orders.route(order.artifact_id).accepted

    def _fill(venue_fill_id, quantity):
        return Fill(
            order_id=order.artifact_id,
            venue_fill_id=venue_fill_id,
            quantity=quantity,
            price=Decimal("100"),
            fee=Decimal("0.10"),
            occurred_at=timestamp,
        )

    with pytest.raises(OrderStateError, match="fill side must be buy or sell"):
        orders.record_fill(_fill("fill-bad-side", Decimal("1")), "hold")
    with pytest.raises(OrderStateError, match="does not match the canonical order"):
        orders.record_fill(_fill("fill-wrong-side", Decimal("1")), "sell")
    with pytest.raises(OrderStateError, match="fills exceed order quantity"):
        orders.record_fill(_fill("fill-overfill", Decimal("2")), "buy")
    orders.record_fill(_fill("fill-ok", Decimal("1")), "buy")
    with pytest.raises(OrderStateError, match="reused for different fill content"):
        orders.record_fill(_fill("fill-ok", Decimal("0.5")), "buy")

    created = _order(btc_usdt, timestamp)
    orders.create(created)
    early = Fill(
        order_id=created.artifact_id,
        venue_fill_id="fill-early",
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee=Decimal("0.10"),
        occurred_at=timestamp,
    )
    with pytest.raises(OrderStateError, match="cannot fill order in state"):
        orders.record_fill(early, "buy")


def test_oms_reconcile_routed_rejects_misshapen_query_responses(
    tmp_path, btc_usdt, timestamp
):
    """P02/P09 prep: routed reconciliation validates venue queries.

    Reconciling a non-routed order, a non-object query response, a
    query missing the venue order identity, a query with no
    admitted order state, and a routed order with no matching
    venue record are all refused without resubmission. Pure unit
    coverage over throwaway SQLite ledgers; no network, no
    credentials.
    """

    class QueryAdapter:
        def __init__(self, response):
            self.response = response
            self.submits = 0

        def submit(self, order):
            self.submits += 1
            return VenueAcknowledgement(
                order_id=order.artifact_id,
                venue_order_id=f"query-{order.artifact_id}",
                accepted=True,
            )

        def open_orders(self):
            return ()

        def reconcile(self, order):
            return self.response

    def _routed(tmp_name, response):
        adapter = QueryAdapter(response)
        ledgers = SqliteLedgers(tmp_path / tmp_name)
        orders = OrderManager(ledgers, adapter)
        order = _order(btc_usdt, timestamp)
        orders.create(order)
        orders.transition(order.artifact_id, OrderState.RISK_APPROVED)
        orders.transition(order.artifact_id, OrderState.ROUTED)
        return orders, order, adapter

    orders, order, adapter = _routed("query-nonobject.sqlite", ["not-a-mapping"])
    with pytest.raises(OrderStateError, match="non-object response"):
        orders.reconcile_routed(order.artifact_id)
    assert adapter.submits == 0
    assert orders.orders[order.artifact_id].state is OrderState.ROUTED

    orders, order, adapter = _routed("query-no-identity.sqlite", {"state": "open"})
    with pytest.raises(OrderStateError, match="missing a venue order identity"):
        orders.reconcile_routed(order.artifact_id)
    assert adapter.submits == 0

    orders, order, adapter = _routed(
        "query-no-state.sqlite", {"venue_order_id": "venue-1", "state": "mysterious"}
    )
    with pytest.raises(OrderStateError, match="no admitted order state"):
        orders.reconcile_routed(order.artifact_id)
    assert adapter.submits == 0

    ledgers = SqliteLedgers(tmp_path / "query-no-match.sqlite")
    orphan_orders = OrderManager(ledgers, PaperVenueAdapter())
    orphan = _order(btc_usdt, timestamp)
    orphan_orders.create(orphan)
    orphan_orders.transition(orphan.artifact_id, OrderState.RISK_APPROVED)
    orphan_orders.transition(orphan.artifact_id, OrderState.ROUTED)
    with pytest.raises(OrderStateError, match="no matching routed order"):
        orphan_orders.reconcile_routed(orphan.artifact_id)

    ledgers = SqliteLedgers(tmp_path / "query-wrong-state.sqlite")
    settled_orders = OrderManager(ledgers, PaperVenueAdapter())
    settled = _order(btc_usdt, timestamp)
    settled_orders.create(settled)
    with pytest.raises(OrderStateError, match="only routed orders require"):
        settled_orders.reconcile_routed(settled.artifact_id)


def test_oms_create_and_transition_validate_lifecycle(tmp_path, btc_usdt, timestamp):
    """P02/P09 prep: order creation and transitions validate lifecycle.

    An idempotency key reused for a different order, a direct
    invalid transition, a transition on an unknown order, and a
    reconciliation request on a non-terminal order are all
    refused. Pure unit coverage over a throwaway SQLite ledger; no
    network, no credentials.
    """
    ledgers = SqliteLedgers(tmp_path / "lifecycle.sqlite")
    orders = OrderManager(ledgers, PaperVenueAdapter())
    order = _order(btc_usdt, timestamp)
    orders.create(order)
    divergent = _order(btc_usdt, timestamp).model_copy(
        update={"idempotency_key": order.idempotency_key}
    )
    with pytest.raises(OrderStateError, match="reused for different order"):
        orders.create(divergent)
    with pytest.raises(OrderStateError, match="invalid order transition"):
        orders.transition(order.artifact_id, OrderState.FILLED)
    assert orders.orders[order.artifact_id].state is OrderState.CREATED
    with pytest.raises(OrderStateError, match="unknown order"):
        orders.transition(uuid4(), OrderState.CANCELLED)
    with pytest.raises(OrderStateError, match="must be terminal before reconciliation"):
        orders.reconcile(order.artifact_id)


def test_oms_record_rejects_non_artifacts(tmp_path, btc_usdt, timestamp):
    """P02/P09 prep: the order ledger only records Order or Fill artifacts.

    Recording any other object type raises TypeError before any
    ledger write. Pure unit coverage over a throwaway SQLite
    ledger; no network, no credentials.
    """
    ledgers = SqliteLedgers(tmp_path / "record-type.sqlite")
    orders = OrderManager(ledgers, PaperVenueAdapter())
    with pytest.raises(TypeError, match="can only ledger Order or Fill"):
        orders._record({"not": "an-artifact"}, "order_created")
    assert ledgers.events(LedgerNamespace.ORDER) == ()


def test_oms_hydrate_refuses_corrupt_order_history(tmp_path, btc_usdt, timestamp):
    """P02/P09 prep: restart hydration validates the order ledger.

    A transition recorded before order creation, a creation entry
    with a non-created state, an invalid recorded transition, and
    a fill recorded before its order all refuse hydration with
    the underlying fault chained. Pure unit coverage over
    throwaway SQLite ledgers; no network, no credentials.
    """

    def _append(ledgers, event_type, artifact, key):
        ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.ORDER,
                event_type=event_type,
                idempotency_key=key,
                payload={
                    "artifact_id": str(artifact.artifact_id),
                    "artifact": artifact.model_dump(mode="json", round_trip=True),
                },
            )
        )

    order = _order(btc_usdt, timestamp)
    early_ledgers = SqliteLedgers(tmp_path / "early-transition.sqlite")
    _append(
        early_ledgers,
        "order_routed",
        order.model_copy(update={"state": OrderState.ROUTED}),
        "order:early:order_routed",
    )
    with pytest.raises(OrderStateError, match="invalid artifact") as excinfo:
        OrderManager(early_ledgers, PaperVenueAdapter())
    assert "transition before order creation" in str(excinfo.value.__cause__)

    state_ledgers = SqliteLedgers(tmp_path / "bad-creation-state.sqlite")
    _append(
        state_ledgers,
        "order_created",
        order.model_copy(update={"state": OrderState.ROUTED}),
        "order:badstate:order_created",
    )
    with pytest.raises(OrderStateError, match="invalid artifact") as excinfo:
        OrderManager(state_ledgers, PaperVenueAdapter())
    assert "non-created state" in str(excinfo.value.__cause__)

    skip_ledgers = SqliteLedgers(tmp_path / "skipped-transition.sqlite")
    _append(skip_ledgers, "order_created", order, "order:skip:order_created")
    _append(
        skip_ledgers,
        "order_filled",
        order.model_copy(update={"state": OrderState.FILLED}),
        "order:skip:order_filled",
    )
    with pytest.raises(OrderStateError, match="invalid artifact") as excinfo:
        OrderManager(skip_ledgers, PaperVenueAdapter())
    assert "invalid transition" in str(excinfo.value.__cause__)

    orphan_ledgers = SqliteLedgers(tmp_path / "orphan-fill.sqlite")
    orphan_fill = Fill(
        order_id=uuid4(),
        venue_fill_id="orphan-fill-1",
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee=Decimal("0.10"),
        occurred_at=timestamp,
    )
    _append(orphan_ledgers, "fill_recorded", orphan_fill, "fill:orphan-fill-1")
    with pytest.raises(OrderStateError, match="invalid artifact") as excinfo:
        OrderManager(orphan_ledgers, PaperVenueAdapter())
    assert "precedes its order" in str(excinfo.value.__cause__)


def test_risk_market_state_refuses_misshapen_construction(btc_usdt):
    """P02/P09 prep: the risk market snapshot validates at construction.

    Non-integer clock drift and order counts, non-Decimal
    scalars, non-digest and mismatched state hashes, blank
    identifiers, non-Decimal/non-finite numerics, non-boolean
    venue health, non-positive marks and liquidity, negative
    staleness/volatility/costs/drift/counters, and non-finite
    collateral are all refused. Pure unit coverage; no network, no
    credentials.
    """
    marks = {btc_usdt.canonical_id: Decimal("100")}
    with pytest.raises(ValueError, match="clock drift must be an integer"):
        RiskMarketState(marks=marks, clock_drift_seconds=1.5)
    with pytest.raises(ValueError, match="order count must be an integer"):
        RiskMarketState(marks=marks, order_count=True)
    with pytest.raises(ValueError, match="scalar values must be Decimal"):
        RiskMarketState(marks=marks, funding_cost_bps=1.5)
    with pytest.raises(ValueError, match="must be a lowercase SHA-256 digest"):
        RiskMarketState(marks=marks, market_state_hash="NOT-A-DIGEST")
    with pytest.raises(ValueError, match="identifiers cannot be blank"):
        RiskMarketState(marks={"  ": Decimal("100")})
    with pytest.raises(ValueError, match="numeric values must be Decimal instances"):
        RiskMarketState(marks=marks, volatility={btc_usdt.canonical_id: 0.5})
    with pytest.raises(ValueError, match="health values must be booleans"):
        RiskMarketState(marks=marks, venue_healthy={"venue": "yes"})
    with pytest.raises(ValueError, match="numeric values must be finite"):
        RiskMarketState(marks={btc_usdt.canonical_id: Decimal("NaN")})
    with pytest.raises(ValueError, match="counters must be finite"):
        RiskMarketState(marks=marks, funding_cost_bps=Decimal("Infinity"))
    with pytest.raises(ValueError, match="marks must be positive"):
        RiskMarketState(marks={btc_usdt.canonical_id: Decimal("0")})
    with pytest.raises(ValueError, match="stale durations cannot be negative"):
        RiskMarketState(marks=marks, stale_seconds={btc_usdt.canonical_id: -1})
    with pytest.raises(ValueError, match="volatility values cannot be negative"):
        RiskMarketState(
            marks=marks, volatility={btc_usdt.canonical_id: Decimal("-1")}
        )
    with pytest.raises(ValueError, match="liquidity notionals must be positive"):
        RiskMarketState(
            marks=marks, liquidity_notional={btc_usdt.canonical_id: Decimal("0")}
        )
    with pytest.raises(ValueError, match="cost estimates cannot be negative"):
        RiskMarketState(
            marks=marks, spread_bps={btc_usdt.canonical_id: Decimal("-1")}
        )
    with pytest.raises(ValueError, match="clock drift cannot be negative"):
        RiskMarketState(marks=marks, clock_drift_seconds=-1)
    with pytest.raises(ValueError, match="counters/costs cannot be negative"):
        RiskMarketState(marks=marks, order_count=-1)
    with pytest.raises(ValueError, match="collateral availability must be finite"):
        RiskMarketState(marks=marks, collateral_available=Decimal("NaN"))
    with pytest.raises(ValueError, match="does not match canonical market state"):
        RiskMarketState(marks=marks, market_state_hash="00" * 32)


def test_kill_switch_reset_requires_human_approver(tmp_path):
    """P02/P09 prep: kill-switch reset requires an explicit approver.

    A blank approver is refused and the switch stays tripped.
    Pure unit coverage over a throwaway SQLite ledger; no network,
    no credentials.
    """
    ledgers = SqliteLedgers(tmp_path / "kill-switch-reset.sqlite")
    switch = KillSwitch(ledgers)
    switch.trip("venue outage")
    with pytest.raises(ValueError, match="explicit human approver"):
        switch.reset(approved_by="  ")
    assert switch.tripped
    switch.reset(approved_by="human-operator")
    assert not switch.tripped
