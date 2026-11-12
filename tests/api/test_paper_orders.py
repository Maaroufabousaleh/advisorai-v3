"""Ledger-backed paper orders and fills projection."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from advisorai.api.dashboard import DashboardProjection
from advisorai.contracts import (
    AssetClass,
    ExecutionPlan,
    Fill,
    InstrumentIdentity,
    Order,
    OrderState,
)
from advisorai.execution import OrderManager, PaperVenueAdapter
from advisorai.ledger import SqliteLedgers


def _setup(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "orders.sqlite3")
    orders = OrderManager(ledgers, PaperVenueAdapter())
    btc = InstrumentIdentity(canonical_id="BTC", asset_class=AssetClass.CRYPTO, venue="paper")
    cutoff = datetime(2026, 8, 5, 15, tzinfo=UTC)
    plan = ExecutionPlan(
        risk_decision_id=uuid4(),
        target_portfolio_id=uuid4(),
        policy_version="risk-v1",
        instructions=("inspection",),
        expires_at=datetime(2026, 8, 5, 16, tzinfo=UTC),
        created_at=cutoff,
    )
    order = Order(
        parent_intent_id=uuid4(),
        execution_plan_id=plan.artifact_id,
        instrument=btc,
        side="buy",
        quantity=Decimal("1"),
        order_type="limit",
        price=Decimal("50000"),
        time_in_force="GTC",
        idempotency_key=f"inspection-{uuid4()}",
    )
    created = orders.create(order)
    orders.transition(created.artifact_id, OrderState.RISK_APPROVED)
    assert orders.route(created.artifact_id) is not None
    fill = Fill(
        order_id=created.artifact_id,
        venue_fill_id=f"venue-{created.artifact_id}",
        quantity=Decimal("1"),
        price=Decimal("50000"),
        fee=Decimal("0.5"),
        occurred_at=datetime(2026, 8, 5, 15, 30, tzinfo=UTC),
    )
    orders.record_fill(fill, "buy")
    orders.reconcile(created.artifact_id)
    return ledgers, created, fill


def test_paper_orders_project_lifecycle_states(tmp_path):
    ledgers, created, _ = _setup(tmp_path)
    store = DashboardProjection(
        ledgers=ledgers, research_registry_path=tmp_path / "missing.sqlite3"
    )
    views = store.paper_orders()
    assert len(views) == 1
    view = views[0]
    assert view.order_id == str(created.artifact_id)
    assert view.state == "reconciled"
    assert view.instrument == "BTC"
    assert view.side == "buy"


def test_paper_fills_link_to_orders_by_id(tmp_path):
    ledgers, created, fill = _setup(tmp_path)
    store = DashboardProjection(ledgers=ledgers)
    fills = store.paper_fills()
    assert len(fills) == 1
    assert fills[0].order_id == str(created.artifact_id)
    assert fills[0].fill_id == str(fill.artifact_id)
    assert fills[0].price == "50000"


def test_no_ledgers_returns_empty():
    store = DashboardProjection()
    assert store.paper_orders() == ()
    assert store.paper_fills() == ()
