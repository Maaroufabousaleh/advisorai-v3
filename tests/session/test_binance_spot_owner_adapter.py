"""Owner-scoped projection tests for Binance Spot Testnet."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from advisorai.contracts import ExecutionPlan, InstrumentIdentity, Order, OrderState
from advisorai.execution import NativeVenueAdapter, NativeVenueProjectionError, OrderManager
from advisorai.execution.reconciliation import VenueAccountSnapshot
from advisorai.integrations import BINANCE_SPOT_TESTNET_BASE_URL, BINANCE_SPOT_TESTNET_HOST
from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.session.providers import BinanceSpotTestnetOwnerAdapter

_NOW = datetime(2026, 11, 12, 16, 0, tzinfo=UTC)


class _Transport:
    venue_name = "binance_spot_testnet"
    environment = "paper_testnet"
    verified_symbol_ids = ("BTCUSDT",)

    def __init__(self) -> None:
        self.client = SimpleNamespace(
            base_url=BINANCE_SPOT_TESTNET_BASE_URL,
            config=SimpleNamespace(allowed_hosts=(BINANCE_SPOT_TESTNET_HOST,)),
        )
        self.positions = {
            "crypto:BTCUSDT:binance_spot_testnet:spot": Decimal("0")
        }
        self.cash = Decimal("1000")
        self.open_order_records: list[dict[str, object]] = []
        self.orders: dict[str, dict[str, object]] = {}
        self.trade_records: list[dict[str, object]] = []

    @staticmethod
    def canonical_instrument_id(symbol: str) -> str:
        return f"crypto:{symbol.upper()}:binance_spot_testnet:spot"

    @staticmethod
    def verified_symbol_spec(symbol: str):
        if symbol != "BTCUSDT":
            raise ValueError("unknown symbol")
        return SimpleNamespace(base_asset="BTC", quote_asset="USDT")

    def submit_order(self, payload):
        client_id = str(payload["client_order_id"])
        record = {
            "client_order_id": client_id,
            "venue_order_id": "provider-order-1",
            "orderId": "provider-order-1",
            "symbol": "BTCUSDT",
            "status": "NEW",
            "accepted": True,
        }
        self.orders[client_id] = record
        self.open_order_records = [record]
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
            positions=dict(self.positions),
            margin_used=Decimal("0"),
            margin_available=self.cash,
        )

    def list_fills(self):
        return tuple(self.trade_records)

    @staticmethod
    def fill_contract_values(record):
        return {
            "venue_fill_id": f"{record['symbol']}:{record['id']}",
            "order_id": str(record["orderId"]),
            "symbol": str(record["symbol"]),
            "side": "buy" if record["isBuyer"] else "sell",
            "quantity": Decimal(str(record["qty"])),
            "price": Decimal(str(record["price"])),
            "fee": Decimal(str(record["commission"])),
            "occurred_at": _NOW,
        }


def _owner_adapter(ledgers, transport=None, **kwargs):
    provider = transport or _Transport()
    native = NativeVenueAdapter(
        venue="binance_spot_testnet",
        environment="paper_testnet",
        transport=provider,
    )
    profile = {
        "canonical_instrument_id": "BTC",
        "symbol": "BTCUSDT",
        "quote_asset": "USDT",
        **kwargs,
    }
    return BinanceSpotTestnetOwnerAdapter(
        native,
        ledgers,
        **profile,
    )


def _order():
    instrument = InstrumentIdentity(
        canonical_id="BTC",
        asset_class="crypto",
        venue="paper",
        venue_symbol="BTCUSDT",
    )
    plan = ExecutionPlan(
        created_at=_NOW,
        risk_decision_id=uuid4(),
        target_portfolio_id=uuid4(),
        policy_version="testnet-owner-v1",
        instructions=("passive_limit",),
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
            "id": "trade-1",
            "orderId": "provider-order-1",
            "symbol": "BTCUSDT",
            "isBuyer": True,
            "qty": "0.01",
            "price": "50000",
            "commission": "0.05",
            "commissionAsset": "USDT",
        }
    ]
    fill, = adapter.fills
    manager.record_fill(fill, order.side)
    assert manager.orders[order.artifact_id].state is OrderState.FILLED
    assert fill.order_id == order.artifact_id
    assert fill.fee == Decimal("0.05")
    assert len(
        [
            event
            for event in ledgers.events(LedgerNamespace.VENUE)
            if event.event_type == "owner_venue_order_identity_recorded"
        ]
    ) == 1

    restarted = _owner_adapter(ledgers, transport)
    restarted_manager = OrderManager(ledgers, restarted)
    reloaded_fill, = restarted.fills
    assert reloaded_fill.artifact_id == fill.artifact_id
    assert reloaded_fill.order_id == order.artifact_id
    assert reloaded_fill.fee_currency == "USDT"
    assert restarted_manager.orders[order.artifact_id].state is OrderState.FILLED


def test_owner_adapter_requires_exact_testnet_and_single_symbol_profile(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "profile.sqlite3")
    transport = _Transport()
    transport.client.base_url = "https://api.binance.com"
    with pytest.raises(ValueError, match="exact Spot Testnet boundary"):
        _owner_adapter(ledgers, transport)

    transport = _Transport()
    transport.verified_symbol_ids = ("BTCUSDT", "ETHUSDT")
    with pytest.raises(ValueError, match="exactly one verified instrument"):
        _owner_adapter(ledgers, transport)

    with pytest.raises(ValueError, match="quote asset differs"):
        _owner_adapter(ledgers, _Transport(), quote_asset="USDC")

    transport = _Transport()
    native = NativeVenueAdapter(
        venue="binance_spot_testnet",
        environment="paper_testnet",
        transport=transport,
        strict_venue=True,
    )
    with pytest.raises(ValueError, match="exact Spot Testnet boundary"):
        BinanceSpotTestnetOwnerAdapter(
            native,
            ledgers,
            canonical_instrument_id="BTC",
            symbol="BTCUSDT",
            quote_asset="USDT",
        )


def test_owner_adapter_rejects_unmapped_positions_unknown_fills_and_base_fees(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "fail-closed.sqlite3")
    transport = _Transport()
    transport.positions["asset:BNB"] = Decimal("0.1")
    adapter = _owner_adapter(ledgers, transport)
    with pytest.raises(NativeVenueProjectionError, match="unmapped spot position"):
        adapter.account_snapshot()

    transport.positions.pop("asset:BNB")
    transport.trade_records = [
        {
            "id": "unmapped",
            "orderId": "external-order",
            "symbol": "BTCUSDT",
            "isBuyer": True,
            "qty": "0.01",
            "price": "50000",
            "commission": "0",
            "commissionAsset": "BTC",
        }
    ]
    with pytest.raises(NativeVenueProjectionError, match="unknown local OMS order"):
        _ = adapter.fills

    order = _order()
    adapter.submit(order)
    transport.trade_records[0]["orderId"] = "provider-order-1"
    transport.trade_records[0]["commission"] = "0.001"
    with pytest.raises(NativeVenueProjectionError, match="not denominated"):
        _ = adapter.fills


def test_owner_adapter_rejects_changed_local_order_identity_after_durable_binding(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "identity.sqlite3")
    adapter = _owner_adapter(ledgers)
    order = _order()
    adapter.submit(order)
    changed = order.model_copy(update={"idempotency_key": "changed-client-id"})
    with pytest.raises(NativeVenueProjectionError, match="local order identity changed"):
        adapter.bind_order(changed)
