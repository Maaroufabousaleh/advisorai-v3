"""Fill-during-cancel reconciliation: sanitized fixture + regression coverage.

Reproduces the supervised testnet observation where a passive buy filled
while a cancellation was in flight. The fixture uses synthetic balances
only. The failing behavior pre-fix:

1. ``fetch_account_snapshot`` stamped the snapshot with a stale cached
   server time instead of the balance read's own time, so the final
   reconciliation reported ``venue_account_snapshot_predates_local_state``.
2. A Binance spot buy deducts quote free balance; the local account moved
   cash on ``apply_fill`` but never moved the frozen ``margin_available``
   baseline, so the final reconciliation reported
   ``venue_margin_available_mismatch`` while cash and positions matched.

Both are code defects, not gate strictness. The fixes keep the
reconciliation gate exact: cash, positions, margin_used, and open orders
keep exact-equality checks.
"""

from __future__ import annotations

import inspect
import json
import urllib.error
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from advisorai.contracts import Fill, InstrumentIdentity, Order, OrderState
from advisorai.execution import (
    AccountLedger,
    AccountState,
    OrderManager,
    OrderStateError,
    ReconciliationService,
)
from advisorai.execution.reconciliation import VenueAccountSnapshot
from advisorai.ledger import SqliteLedgers
from tests.integrations.test_binance_spot import _account, _symbols, _transport


def _server_time_response(epoch_ms: int):
    return (200, json.dumps({"serverTime": epoch_ms}).encode(), ())


def test_snapshot_timestamp_tracks_the_balance_read_not_run_start() -> None:
    """The snapshot as_of must be current at read time.

    Pre-fix this fails: the snapshot reused the run-start server time, so
    any fill applied after run start made the comparison report
    ``venue_account_snapshot_predates_local_state``.
    """
    responses = [
        _server_time_response(1700000000000),  # run-start server time
        (200, json.dumps({"symbols": _symbols()}).encode(), ()),
        (200, json.dumps(_account()).encode(), ()),  # baseline balances
        _server_time_response(1700000005000),  # fresh time for baseline snapshot
        (200, json.dumps(_account()).encode(), ()),  # final balances
        _server_time_response(1700000070000),  # fresh time for final snapshot
    ]
    transport, calls = _transport(responses)
    transport.server_time()
    transport.verify_symbol_mappings(transport.list_products())
    baseline = transport.fetch_account_snapshot()
    assert baseline.as_of == datetime(2023, 11, 14, 22, 13, 25, tzinfo=UTC)

    # The supervised fill lands a minute after the run-start server time.
    fill_time = datetime(2023, 11, 14, 22, 14, 20, tzinfo=UTC)
    final_snapshot = transport.fetch_account_snapshot()
    assert fill_time < final_snapshot.normalized_as_of
    assert any("/api/v3/time" in call[1] for call in calls[3:])


def test_spot_buy_fill_moves_local_free_balance_projection(tmp_path) -> None:
    """Local margin_available must track spot free balance after a fill.

    Pre-fix the local baseline stayed frozen while the venue free balance
    dropped by notional+fee, producing ``venue_margin_available_mismatch``
    even though cash and positions reconciled exactly.
    """
    ledgers = SqliteLedgers(tmp_path / "margin.sqlite3")
    account = AccountState(
        cash=Decimal("1000"),
        marks={"BTC": Decimal("50000")},
        margin_used=Decimal("0"),
        margin_available=Decimal("1000"),
        as_of=datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC),
    )
    ledger = AccountLedger(ledgers, account)
    instrument = InstrumentIdentity(
        canonical_id="crypto:BTCUSDT:binance_spot_testnet:spot",
        asset_class="crypto",
        venue="paper",
        venue_symbol="BTCUSDT",
    )
    fill = Fill(
        artifact_id=uuid4(),
        order_id=uuid4(),
        venue_fill_id="BTCUSDT:fixture-1",
        quantity=Decimal("0.001"),
        price=Decimal("100.00"),
        fee=Decimal("0.01"),
        fee_currency="USDT",
        occurred_at=datetime(2023, 11, 14, 22, 14, 20, tzinfo=UTC),
    )
    snapshot = ledger.apply_fill(fill, "buy", instrument.canonical_id)
    # Spot buy cost = notional + fee, deducted from both cash and free balance.
    assert snapshot.cash == Decimal("1000") - Decimal("0.10") - Decimal("0.01")
    assert snapshot.margin_available == snapshot.cash


def test_full_reconciliation_is_clean_after_fill_during_cancel(tmp_path) -> None:
    """End-to-end: cancel-pending order fills, then reconciliation is clean."""
    from advisorai.execution import OrderManager
    from advisorai.execution.native import NativeVenueAdapter

    responses = [
        _server_time_response(1700000000000),
        (200, json.dumps({"symbols": _symbols()}).encode(), ()),
        (200, json.dumps(_account()).encode(), ()),
        _server_time_response(1700000005000),
        (200, json.dumps(_account()).encode(), ()),
        _server_time_response(1700000070000),
    ]
    transport, _calls = _transport(responses)
    transport.server_time()
    transport.verify_symbol_mappings(transport.list_products())
    baseline = transport.fetch_account_snapshot()

    ledgers = SqliteLedgers(tmp_path / "oms.sqlite3")
    native = NativeVenueAdapter(
        venue="binance_spot_testnet",
        environment="paper_testnet",
        transport=transport,
        strict_venue=True,
    )
    oms = OrderManager(ledgers, native)
    local = AccountState(
        cash=baseline.cash,
        positions=dict(baseline.positions),
        marks={"crypto:BTCUSDT:binance_spot_testnet:spot": Decimal("100.05")},
        margin_used=baseline.margin_used or Decimal("0"),
        margin_available=baseline.margin_available,
        as_of=baseline.as_of,
    )
    account_ledger = AccountLedger(ledgers, local)
    instrument = InstrumentIdentity(
        canonical_id="crypto:BTCUSDT:binance_spot_testnet:spot",
        asset_class="crypto",
        venue="paper",
        venue_symbol="BTCUSDT",
    )
    fill = Fill(
        artifact_id=uuid4(),
        order_id=uuid4(),
        venue_fill_id="BTCUSDT:fixture-1",
        quantity=Decimal("0.001"),
        price=Decimal("100.00"),
        fee=Decimal("0.01"),
        fee_currency="USDT",
        occurred_at=datetime(2023, 11, 14, 22, 14, 20, tzinfo=UTC),
    )
    account_ledger.apply_fill(fill, "buy", instrument.canonical_id)
    final_snapshot = transport.fetch_account_snapshot()
    venue_view = VenueAccountSnapshot(
        as_of=final_snapshot.as_of,
        cash=local.cash,
        positions=dict(local.positions),
        margin_used=local.margin_used,
        margin_available=local.margin_available,
        venue_open_order_ids=frozenset(),
    )
    result = ReconciliationService(ledgers).run(
        account=local,
        orders=oms,
        account_ledger=account_ledger,
        venue_snapshot=venue_view,
    )
    assert result.reconciled, result.discrepancies


def _post_fill_account(*, usdt_free: str, btc_total: str):
    """Independent venue ledger view after the supervised fill settled."""
    return {
        "accountType": "SPOT",
        "canTrade": True,
        "balances": [
            {"asset": "USDT", "free": usdt_free, "locked": "0"},
            {"asset": "BTC", "free": btc_total, "locked": "0"},
            {"asset": "ETH", "free": "0.0", "locked": "0"},
        ],
    }


def test_fill_during_cancel_reproduces_both_discrepancies_then_reconciles_exactly(
    tmp_path,
) -> None:
    """Joint end-to-end: the saved run's two discrepancies, then an exact pass.

    Phase 1 replays the pre-fix projection defects against independent venue
    balances: a stale run-start timestamp plus a frozen free-balance baseline
    produce exactly the two discrepancies from the saved run, and nothing
    else. Phase 2 runs the same scenario through the fixed adapter and
    account so reconciliation reconciles exactly on every comparison.
    """
    from advisorai.execution import OrderManager
    from advisorai.execution.native import NativeVenueAdapter

    instrument_id = "crypto:BTCUSDT:binance_spot_testnet:spot"
    bought = Decimal("0.001")
    price = Decimal("100.00")
    fee = Decimal("0.01")
    cost = bought * price + fee
    base_btc = Decimal("0.010")
    run_start = datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
    fill_time = datetime(2023, 11, 14, 22, 14, 20, tzinfo=UTC)
    final_time = datetime(2023, 11, 14, 22, 15, 10, tzinfo=UTC)

    def _order_manager(ledgers: SqliteLedgers, transport) -> OrderManager:
        native = NativeVenueAdapter(
            venue="binance_spot_testnet",
            environment="paper_testnet",
            transport=transport,
            strict_venue=True,
        )
        return OrderManager(ledgers, native)

    def _apply_supervised_fill(local: AccountState, ledgers: SqliteLedgers) -> AccountLedger:
        ledger = AccountLedger(ledgers, local)
        ledger.apply_fill(
            Fill(
                artifact_id=uuid4(),
                order_id=uuid4(),
                venue_fill_id="BTCUSDT:fixture-joint",
                quantity=bought,
                price=price,
                fee=fee,
                fee_currency="USDT",
                occurred_at=fill_time,
            ),
            "buy",
            instrument_id,
        )
        return ledger

    # Phase 1: pre-fix projections against the venue's post-fill balances.
    pre_transport, _pre_calls = _transport([])
    pre_transport.server_time = lambda: {  # noqa: E731
        "serverTime": int(run_start.timestamp() * 1000)
    }
    pre_local = AccountState(
        cash=Decimal("1000"),
        positions={instrument_id: base_btc},
        marks={instrument_id: Decimal("100.05")},
        margin_used=Decimal("0"),
        margin_available=Decimal("1000"),
        as_of=run_start,
    )
    pre_ledgers = SqliteLedgers(tmp_path / "pre-fix.sqlite3")
    pre_orders = _order_manager(pre_ledgers, pre_transport)
    pre_account_ledger = _apply_supervised_fill(pre_local, pre_ledgers)
    # Pre-fix defect 1: snapshot reuses the cached run-start server time even
    # though the balances were read after the fill.
    stale_snapshot = VenueAccountSnapshot(
        as_of=run_start,
        cash=Decimal("1000") - cost,
        positions={instrument_id: base_btc + bought},
        margin_used=Decimal("0"),
        margin_available=Decimal("1000") - cost,
        venue_open_order_ids=frozenset(),
    )
    # Pre-fix defect 2: local free balance stays frozen at the baseline.
    pre_local.margin_available = Decimal("1000")
    pre_result = ReconciliationService(pre_ledgers).run(
        account=pre_local,
        orders=pre_orders,
        account_ledger=pre_account_ledger,
        venue_snapshot=stale_snapshot,
    )
    assert pre_result.reconciled is False
    assert [item.split(":")[0] for item in pre_result.discrepancies] == [
        "venue_account_snapshot_predates_local_state",
        "venue_margin_available_mismatch",
    ]

    # Phase 2: the fixed path with independent venue balances end to end.
    # Each snapshot uses its own transport so the fixture queue never depends
    # on how many time reads a snapshot performs internally.
    baseline_transport, _ = _transport(
        [
            (200, json.dumps(_account()).encode(), ()),
            _server_time_response(int(run_start.timestamp() * 1000) + 5000),
        ]
    )
    baseline_transport.verify_symbol_mappings(_symbols())
    baseline = baseline_transport.fetch_account_snapshot()
    ledgers = SqliteLedgers(tmp_path / "oms.sqlite3")
    final_transport, _ = _transport(
        [
            (
                200,
                json.dumps(
                    _post_fill_account(
                        usdt_free=str(Decimal("1000") - cost),
                        btc_total=str(base_btc + bought),
                    )
                ).encode(),
                (),
            ),
            _server_time_response(int(final_time.timestamp() * 1000)),
        ]
    )
    final_transport.verify_symbol_mappings(_symbols())
    orders = _order_manager(ledgers, final_transport)
    local = AccountState(
        cash=baseline.cash,
        positions=dict(baseline.positions),
        marks={instrument_id: Decimal("100.05")},
        margin_used=baseline.margin_used or Decimal("0"),
        margin_available=baseline.margin_available,
        as_of=baseline.as_of,
    )
    account_ledger = _apply_supervised_fill(local, ledgers)
    venue_snapshot = final_transport.fetch_account_snapshot()
    assert venue_snapshot.cash == local.cash
    assert venue_snapshot.positions == dict(local.positions)
    assert venue_snapshot.margin_available == local.margin_available
    result = ReconciliationService(ledgers).run(
        account=local,
        orders=orders,
        account_ledger=account_ledger,
        venue_snapshot=venue_snapshot,
    )
    assert result.reconciled, result.discrepancies
    assert result.discrepancies == ()


def test_stale_venue_balances_block_on_values_not_on_timestamp(tmp_path) -> None:
    """A fresh completion stamp must not launder stale balance data.

    The post-fix snapshot timestamp bounds when the read completed, not
    when the balance data was produced. If the venue serves pre-fill
    balances under a fresh timestamp, reconciliation must still block —
    through the exact cash/position/margin value checks, without relying
    on the staleness timestamp check.
    """
    from advisorai.execution import OrderManager
    from advisorai.execution.native import NativeVenueAdapter

    instrument_id = "crypto:BTCUSDT:binance_spot_testnet:spot"
    fill_time = datetime(2023, 11, 14, 22, 14, 20, tzinfo=UTC)
    fresh_completion = datetime(2023, 11, 14, 22, 15, 10, tzinfo=UTC)
    assert fill_time < fresh_completion

    transport, _calls = _transport([])
    transport.verify_symbol_mappings(_symbols())
    ledgers = SqliteLedgers(tmp_path / "stale.sqlite3")
    native = NativeVenueAdapter(
        venue="binance_spot_testnet",
        environment="paper_testnet",
        transport=transport,
        strict_venue=True,
    )
    orders = OrderManager(ledgers, native)
    local = AccountState(
        cash=Decimal("1000"),
        positions={instrument_id: Decimal("0.010")},
        marks={instrument_id: Decimal("100.05")},
        margin_used=Decimal("0"),
        margin_available=Decimal("1000"),
        as_of=datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC),
    )
    account_ledger = AccountLedger(ledgers, local)
    account_ledger.apply_fill(
        Fill(
            artifact_id=uuid4(),
            order_id=uuid4(),
            venue_fill_id="BTCUSDT:fixture-stale",
            quantity=Decimal("0.001"),
            price=Decimal("100.00"),
            fee=Decimal("0.01"),
            fee_currency="USDT",
            occurred_at=fill_time,
        ),
        "buy",
        instrument_id,
    )
    # Venue serves the pre-fill balances stamped with a fresh completion
    # time: the local ledger has moved on but the venue data has not.
    stale_venue_view = VenueAccountSnapshot(
        as_of=fresh_completion,
        cash=Decimal("1000"),
        positions={instrument_id: Decimal("0.010")},
        margin_used=Decimal("0"),
        margin_available=Decimal("1000"),
        venue_open_order_ids=frozenset(),
    )
    result = ReconciliationService(ledgers).run(
        account=local,
        orders=orders,
        account_ledger=account_ledger,
        venue_snapshot=stale_venue_view,
    )
    assert result.reconciled is False
    categories = [item.split(":")[0] for item in result.discrepancies]
    assert "venue_account_snapshot_predates_local_state" not in categories
    assert "venue_cash_mismatch" in categories
    assert "venue_margin_available_mismatch" in categories
    assert any(item.startswith("venue_position_mismatch") for item in categories)


def test_spot_free_balance_semantics_are_pinned_to_spot_profiles() -> None:
    """Only spot free-balance seeders may populate margin_available.

    ``apply_fill`` treats a populated ``margin_available`` as a spot
    free-balance projection (settled in the quote asset). This pins the
    supported seeders to the two spot sandbox adapters plus the supervised
    lifecycle's and the testnet baseline's local seeding from the same
    venue field, so a future margin-profile seeder cannot silently
    inherit spot settlement math.
    """
    from advisorai.integrations import binance_spot, coinbase_exchange

    binance_source = inspect.getsource(
        binance_spot.BinanceSpotTestnetTransport.fetch_account_snapshot
    )
    assert 'quote_record.get("free"' in binance_source
    coinbase_source = inspect.getsource(
        coinbase_exchange.CoinbaseExchangeSandboxTransport.fetch_account_snapshot
    )
    assert 'cash_account.get("available")' in coinbase_source

    lifecycle_source = Path("scripts/qualify_binance_spot_testnet_lifecycle.py").read_text()
    assert "margin_available=account_snapshot.margin_available" in lifecycle_source

    # The testnet baseline seeds from the verified venue snapshot's
    # free-balance field, mirroring the supervised lifecycle.
    baseline_source = Path("src/advisorai/session/testnet_launch.py").read_text()
    assert "margin_available=snapshot.margin_available" in baseline_source

    # Unseeded profiles leave the field None, so apply_fill's projection is
    # a no-op there: the simulator never opts in.
    simulator_source = Path("src/advisorai/session/application.py").read_text()
    assert "margin_available" not in simulator_source


def test_lost_binance_ack_resolves_by_query_without_resubmission(tmp_path) -> None:
    """Ambiguous submit on the real adapter resolves by query, never retry.

    A signed POST whose response is lost raises through the transport while
    the order may have reached the venue. OMS must leave the order ROUTED,
    refuse a blind retry, then settle via ``query_order`` on reconnect —
    exactly one POST is ever sent.
    """
    from advisorai.execution.native import NativeVenueAdapter
    from advisorai.integrations import VenueTransportError

    local_key = "ambiguous-fixture-1"
    ack = {
        "symbol": "BTCUSDT",
        "orderId": 4242,
        "clientOrderId": local_key,
        "status": "NEW",
    }
    responses = [
        # verify_symbol_mappings takes symbols directly; first HTTP call is the POST.
        urllib.error.URLError("fixture: response lost after venue received POST"),
        (200, json.dumps(ack).encode(), ()),
    ]

    def _raising_requester(method, url, headers, body, timeout):
        item = responses.pop(0)
        calls.append((method, url, dict(headers), body))
        if isinstance(item, Exception):
            raise item
        return item

    transport, calls = _transport([])
    transport.client._requester = _raising_requester  # fixture: fail the POST once
    transport.verify_symbol_mappings(_symbols())

    ledgers = SqliteLedgers(tmp_path / "ambiguous.sqlite3")
    native = NativeVenueAdapter(
        venue="binance_spot_testnet",
        environment="paper_testnet",
        transport=transport,
        strict_venue=True,
    )
    oms = OrderManager(ledgers, native)
    order = Order(
        parent_intent_id=uuid4(),
        execution_plan_id=uuid4(),
        instrument=InstrumentIdentity(
            canonical_id="crypto:BTCUSDT:binance_spot_testnet:spot",
            asset_class="crypto",
            venue="binance_spot_testnet",
            venue_symbol="BTCUSDT",
        ),
        side="buy",
        quantity=Decimal("0.001"),
        order_type="passive_limit",
        price=Decimal("10000.00"),
        time_in_force="GTC",
        idempotency_key=local_key,
    )
    oms.create(order)
    oms.transition(order.artifact_id, OrderState.RISK_APPROVED)
    with pytest.raises(VenueTransportError, match="request failed"):
        oms.route(order.artifact_id)

    # Lost ack: the order stays ROUTED and refuses a blind retry.
    assert oms.orders[order.artifact_id].state.value == "routed"
    with pytest.raises(OrderStateError, match="reconciliation before retry"):
        oms.route(order.artifact_id)

    # Reconnect resolves via the venue query; the POST is never re-sent.
    resolved = oms.reconcile_routed(order.artifact_id)
    assert resolved.accepted is True
    assert resolved.venue_order_id == "4242"
    assert oms.orders[order.artifact_id].state.value == "acknowledged"
    posts = [call for call in calls if call[0] == "POST"]
    assert len(posts) == 1
    assert "origClientOrderId" in calls[-1][1]


def test_restart_reconciles_routed_order_without_resubmission(tmp_path) -> None:
    """Crash between POST and ack reconciles on restart via query, no retry.

    The signed POST reaches the venue but the process dies before the ack
    is recorded, leaving the durable order ROUTED. A restarted OMS over the
    same ledger must settle through the real ``query_order`` contract and
    then record the venue fill exactly once — the POST is never re-sent.
    """
    from uuid import NAMESPACE_URL, uuid5

    from advisorai.execution.native import NativeVenueAdapter
    from advisorai.integrations import VenueTransportError

    local_key = "restart-fixture-1"
    queried = {
        "symbol": "BTCUSDT",
        "orderId": 5150,
        "clientOrderId": local_key,
        "status": "NEW",
    }
    trade = {
        "symbol": "BTCUSDT",
        "id": 777001,
        "orderId": 5150,
        "qty": "0.001",
        "price": "10000.00",
        "commission": "0.01",
        "commissionAsset": "USDT",
        "time": 1700000060000,
        "isBuyer": True,
    }
    responses = [
        urllib.error.URLError("fixture: process died after the POST reached the venue"),
        (200, json.dumps(queried).encode(), ()),
        # list_fills fans out per verified symbol: BTCUSDT carries the trade, ETHUSDT is empty.
        (200, json.dumps([trade]).encode(), ()),
        (200, json.dumps([]).encode(), ()),
    ]

    def _raising_requester(method, url, headers, body, timeout):
        item = responses.pop(0)
        calls.append((method, url, dict(headers), body))
        if isinstance(item, Exception):
            raise item
        return item

    transport, calls = _transport([])
    transport.client._requester = _raising_requester  # fixture: POST succeeds remotely, ack lost
    transport.verify_symbol_mappings(_symbols())

    ledgers = SqliteLedgers(tmp_path / "restart.sqlite3")

    def _oms() -> OrderManager:
        return OrderManager(
            ledgers,
            NativeVenueAdapter(
                venue="binance_spot_testnet",
                environment="paper_testnet",
                transport=transport,
                strict_venue=True,
            ),
        )

    oms = _oms()
    order = Order(
        parent_intent_id=uuid4(),
        execution_plan_id=uuid4(),
        instrument=InstrumentIdentity(
            canonical_id="crypto:BTCUSDT:binance_spot_testnet:spot",
            asset_class="crypto",
            venue="binance_spot_testnet",
            venue_symbol="BTCUSDT",
        ),
        side="buy",
        quantity=Decimal("0.001"),
        order_type="passive_limit",
        price=Decimal("10000.00"),
        time_in_force="GTC",
        idempotency_key=local_key,
    )
    oms.create(order)
    oms.transition(order.artifact_id, OrderState.RISK_APPROVED)
    with pytest.raises(VenueTransportError, match="request failed"):
        oms.route(order.artifact_id)

    # Restart: a fresh OMS over the same ledger hydrates the ROUTED order.
    del oms
    restarted = _oms()
    assert restarted.orders[order.artifact_id].state is OrderState.ROUTED

    # Settle via the authoritative venue query; the POST is never re-sent.
    resolved = restarted.reconcile_routed(order.artifact_id)
    assert resolved.accepted is True
    assert resolved.venue_order_id == "5150"
    assert restarted.orders[order.artifact_id].state is OrderState.ACKNOWLEDGED

    # Ingest the venue fill through the real contract, exactly once.
    fills = transport.list_fills()
    assert len(fills) == 1
    values = transport.fill_contract_values(fills[0])
    assert values["venue_fill_id"] == "BTCUSDT:777001"
    assert values["order_id"] == "5150"
    fill = Fill(
        artifact_id=uuid5(NAMESPACE_URL, f"advisorai-v3/binance-fill/{values['venue_fill_id']}"),
        order_id=order.artifact_id,
        venue_fill_id=str(values["venue_fill_id"]),
        quantity=values["quantity"],
        price=values["price"],
        fee=values["fee"],
        fee_currency="USDT",
        occurred_at=values["occurred_at"],
    )
    restarted.record_fill(fill, "buy")
    assert restarted.orders[order.artifact_id].state is OrderState.FILLED

    # Re-ingesting the same venue trade is a no-op, not a duplicate.
    restarted.record_fill(fill, "buy")
    assert len(restarted.fills) == 1
    posts = [call for call in calls if call[0] == "POST"]
    assert len(posts) == 1
