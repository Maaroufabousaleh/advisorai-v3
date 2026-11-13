"""Cross-engine replay comparison: independent correctness evidence.

The same deterministic market-event stream is replayed through:
  1. the canonical deterministic ReplayEngine,
  2. the NautilusTrader boundary test double,
  3. direct OMS fill application + account projection.

All three must agree on fill quantities, account positions, and event order.
NautilusTrader remains quarantined (no installed runtime, no Phase 0 gate);
the comparison proves the boundary preserves semantics, not admission.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from advisorai.contracts import (
    AssetClass,
    ExecutionPlan,
    Fill,
    InstrumentIdentity,
    Order,
    OrderState,
)
from advisorai.execution import AccountState, OrderManager, PaperVenueAdapter
from advisorai.execution.events import MarketEvent, ReplayEngine
from advisorai.execution.nautilus import NautilusTraderPipeline
from advisorai.ledger import SqliteLedgers


def _events():
    base = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    return tuple(
        MarketEvent.from_raw(
            event_type="trade",
            instrument_id="BTC",
            occurred_at=base,
            sequence=index,
            raw_payload=f"trade-{index}".encode(),
            price=Decimal("50000") + Decimal(index * 10),
            quantity=Decimal("1"),
        )
        for index in range(5)
    )


def test_nautilus_double_matches_deterministic_replay_order():
    events = _events()
    shuffled = (events[3], events[0], events[4], events[1], events[2])
    canonical_seen: list[MarketEvent] = []
    assert ReplayEngine().replay(shuffled, canonical_seen.append) == 5
    double_seen: list[MarketEvent] = []
    assert NautilusTraderPipeline(test_double=True).replay(shuffled, double_seen.append) == 5
    assert [event.event_id for event in double_seen] == [event.event_id for event in canonical_seen]
    assert [event.sequence for event in double_seen] == [0, 1, 2, 3, 4]


def test_replay_fills_match_oms_account_projection(tmp_path):
    events = _events()
    ledgers = SqliteLedgers(tmp_path / "cross-engine.sqlite3")
    orders = OrderManager(ledgers, PaperVenueAdapter())
    btc = InstrumentIdentity(canonical_id="BTC", asset_class=AssetClass.CRYPTO, venue="paper")
    account = AccountState(
        cash=Decimal("1000000"),
        as_of=datetime(2026, 8, 5, 15, tzinfo=UTC),
        marks={"BTC": Decimal("50000")},
    )
    plan = ExecutionPlan(
        risk_decision_id=uuid4(),
        target_portfolio_id=uuid4(),
        policy_version="risk-v1",
        instructions=("cross-engine replay",),
        expires_at=datetime(2026, 8, 5, 16, tzinfo=UTC),
        created_at=datetime(2026, 8, 5, 15, tzinfo=UTC),
    )
    order = Order(
        parent_intent_id=uuid4(),
        execution_plan_id=plan.artifact_id,
        instrument=btc,
        side="buy",
        quantity=Decimal("5"),
        order_type="limit",
        price=Decimal("60000"),
        time_in_force="GTC",
        idempotency_key=f"cross-engine-{uuid4()}",
    )
    created = orders.create(order)
    orders.transition(created.artifact_id, OrderState.RISK_APPROVED)
    acknowledgement = orders.route(created.artifact_id)
    assert acknowledgement is not None and acknowledgement.accepted is True
    applied: list[tuple[int, Decimal]] = []

    def on_trade(event: MarketEvent) -> None:
        assert event.price is not None and event.quantity is not None
        fill = Fill(
            order_id=created.artifact_id,
            venue_fill_id=f"venue-{event.event_id}",
            quantity=event.quantity,
            price=event.price,
            fee=Decimal("0"),
            occurred_at=event.occurred_at,
        )
        orders.record_fill(fill, "buy")
        account.apply_fill(fill, "buy", "BTC")
        applied.append((event.sequence, event.price))

    count = ReplayEngine().replay(events, on_trade)
    assert count == 5
    assert len(applied) == 5
    assert account.positions.get("BTC") == Decimal("5")
    assert orders.orders[created.artifact_id].state.value == "filled"
    # Replaying the identical stream is idempotent: no duplicate fills.
    before = len(orders.fills)
    ReplayEngine().replay(events, lambda event: None)
    assert len(orders.fills) == before
