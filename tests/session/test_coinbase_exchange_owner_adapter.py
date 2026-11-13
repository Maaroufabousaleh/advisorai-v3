"""Owner-scoped projection tests for Coinbase Exchange Sandbox."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from advisorai.contracts import ExecutionPlan, InstrumentIdentity, Order, OrderState
from advisorai.execution import NativeVenueAdapter, NativeVenueProjectionError, OrderManager
from advisorai.execution.reconciliation import VenueAccountSnapshot
from advisorai.integrations import (
    COINBASE_EXCHANGE_SANDBOX_BASE_URL,
    COINBASE_EXCHANGE_SANDBOX_HOST,
)
from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.session.providers import CoinbaseExchangeSandboxOwnerAdapter

_NOW = datetime(2026, 11, 12, 16, 0, tzinfo=UTC)


class _Transport:
    venue_name = "coinbase_exchange_sandbox"
    environment = "paper_testnet"
    verified_product_ids = ("BTC-USD",)

    def __init__(self) -> None:
        self.client = SimpleNamespace(
            base_url=COINBASE_EXCHANGE_SANDBOX_BASE_URL,
            config=SimpleNamespace(allowed_hosts=(COINBASE_EXCHANGE_SANDBOX_HOST,)),
        )
        self.cash = Decimal("1000")
        self.open_order_records: list[dict[str, object]] = []
        self.orders: dict[str, dict[str, object]] = {}
        self.trade_records: list[dict[str, object]] = []

    @staticmethod
    def verified_product_spec(product_id: str):
        if product_id != "BTC-USD":
            raise ValueError("unknown product")
        return SimpleNamespace(base_currency="BTC", quote_currency="USD")

    @staticmethod
    def canonical_instrument_id(product_id: str) -> str:
        return f"crypto:{product_id.upper()}:coinbase_exchange_sandbox:spot"

    def submit_order(self, payload):
        client_id = str(payload["client_order_id"])
        record = {
            "id": "provider-order-1",
            "client_oid": client_id,
            "client_order_id": client_id,
            "product_id": "BTC-USD",
            "status": "open",
            "accepted": True,
        }
        self.orders[client_id] = record
        self.open_order_records = [dict(record)]
        return {"accepted": True, "venue_order_id": "provider-order-1"}

    def query_order(self, *, client_order_id: str):
        return self.orders.get(client_order_id)

    def list_open_orders(self):
        return tuple(self.open_order_records)

    def cancel_order(self, *, client_order_id: str):
        self.open_order_records = []
        return {"cancelled": True}

    def fetch_account_snapshot(self):
        return VenueAccountSnapshot(
            as_of=_NOW,
            cash=self.cash,
            positions={},
            margin_used=Decimal("0"),
            margin_available=self.cash,
        )

    def list_fills(self, *, product_id=None):
        assert product_id in (None, "BTC-USD")
        return tuple(self.trade_records)

    @staticmethod
    def fill_contract_values(record):
        return {
            "venue_fill_id": str(record["trade_id"]),
            "order_id": str(record["order_id"]),
            "product_id": str(record["product_id"]),
            "side": str(record["side"]),
            "quantity": Decimal(str(record["size"])),
            "price": Decimal(str(record["price"])),
            "fee": Decimal(str(record["fee"])),
            "fee_currency": str(record.get("fee_currency", "USD")),
            "occurred_at": _NOW,
        }


def _owner_adapter(ledgers, transport=None, **kwargs):
    provider = transport or _Transport()
    native = NativeVenueAdapter(
        venue="coinbase_exchange_sandbox",
        environment="paper_testnet",
        transport=provider,
    )
    profile = {
        "canonical_instrument_id": "BTC",
        "product_id": "BTC-USD",
        "quote_currency": "USD",
        **kwargs,
    }
    return CoinbaseExchangeSandboxOwnerAdapter(
        native,
        ledgers,
        **profile,
    )


def _order():
    instrument = InstrumentIdentity(
        canonical_id="BTC",
        asset_class="crypto",
        venue="paper",
        venue_symbol="BTC-USD",
    )
    plan = ExecutionPlan(
        created_at=_NOW,
        risk_decision_id=uuid4(),
        target_portfolio_id=uuid4(),
        policy_version="testnet-owner-v1",
        instructions=("limit",),
        expires_at=_NOW + timedelta(minutes=5),
    )
    return Order(
        parent_intent_id=uuid4(),
        execution_plan_id=plan.artifact_id,
        instrument=instrument,
        side="buy",
        quantity=Decimal("0.01"),
        order_type="limit",
        price=Decimal("50000"),
        time_in_force="GTC",
        idempotency_key=f"owner-test-{uuid4()}",
    )


def test_owner_adapter_projects_order_fill_and_identity_across_restart(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "owner.sqlite3")
    transport = _Transport()
    adapter = _owner_adapter(ledgers, transport)
    manager = OrderManager(ledgers, adapter)
    order = _order()
    manager.create(order)
    manager.transition(order.artifact_id, OrderState.RISK_APPROVED)
    acknowledgement = manager.route(order.artifact_id)

    assert acknowledgement is not None and acknowledgement.accepted
    assert adapter.open_orders()[0].order_id == order.artifact_id
    transport.trade_records = [
        {
            "trade_id": "trade-1",
            "order_id": "provider-order-1",
            "product_id": "BTC-USD",
            "side": "buy",
            "size": "0.01",
            "price": "50000",
            "fee": "0.05",
            "fee_currency": "USD",
        }
    ]
    (fill,) = adapter.fills
    manager.record_fill(fill, order.side)
    assert manager.orders[order.artifact_id].state is OrderState.FILLED
    assert fill.order_id == order.artifact_id
    assert fill.fee == Decimal("0.05")
    assert (
        len(
            [
                event
                for event in ledgers.events(LedgerNamespace.VENUE)
                if event.event_type == "owner_venue_order_identity_recorded"
            ]
        )
        == 1
    )

    restarted = _owner_adapter(ledgers, transport)
    restarted_manager = OrderManager(ledgers, restarted)
    (reloaded_fill,) = restarted.fills
    assert reloaded_fill == fill
    assert reloaded_fill.artifact_id == fill.artifact_id
    assert reloaded_fill.order_id == order.artifact_id
    assert restarted_manager.orders[order.artifact_id].state is OrderState.FILLED


def test_owner_adapter_requires_exact_sandbox_and_single_product_profile(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "profile.sqlite3")
    transport = _Transport()
    transport.client.base_url = "https://api.exchange.coinbase.com"
    with pytest.raises(ValueError, match="exact Sandbox boundary"):
        _owner_adapter(ledgers, transport)

    transport = _Transport()
    transport.verified_product_ids = ("BTC-USD", "ETH-USD")
    with pytest.raises(ValueError, match="exactly one verified product"):
        _owner_adapter(ledgers, transport)

    with pytest.raises(ValueError, match="USD quote product"):
        _owner_adapter(ledgers, _Transport(), quote_currency="USDC")


def test_owner_adapter_rejects_unknown_fills_and_non_quote_fees(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "fail-closed.sqlite3")
    transport = _Transport()
    adapter = _owner_adapter(ledgers, transport)
    transport.trade_records = [
        {
            "trade_id": "unmapped",
            "order_id": "external-order",
            "product_id": "BTC-USD",
            "side": "buy",
            "size": "0.01",
            "price": "50000",
            "fee": "0",
            "fee_currency": "USD",
        }
    ]
    with pytest.raises(NativeVenueProjectionError, match="unknown local OMS order"):
        _ = adapter.fills

    order = _order()
    adapter.submit(order)
    transport.trade_records[0]["order_id"] = "provider-order-1"
    transport.trade_records[0]["fee"] = "0.001"
    transport.trade_records[0]["fee_currency"] = "BTC"
    with pytest.raises(NativeVenueProjectionError, match="not denominated"):
        _ = adapter.fills
