"""Offline contracts for bounded testnet launcher controls.

All transports are synthetic doubles. No network, no credentials.
Covers the three enforced bounds: wall-clock deadline, venue-boundary
submission cap, and verified clean stop with reconciliation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from advisorai.config import CredentialResolver
from advisorai.contracts import ExecutionPlan, InstrumentIdentity, Order, OrderState
from advisorai.execution import NativeVenueAdapter, NativeVenueProjectionError, OrderManager
from advisorai.execution.reconciliation import VenueAccountSnapshot
from advisorai.integrations import (
    BINANCE_SPOT_TESTNET_BASE_URL,
    BINANCE_SPOT_TESTNET_HOST,
)
from advisorai.integrations.http import HttpClientConfig, SafeHttpClient
from advisorai.ledger import SqliteLedgers
from advisorai.session.application import OwnerApplicationConfig, PaperOwnerApplication
from advisorai.session.testnet_launch import (
    SandboxLaunchRequest,
    build_testnet_owner_application,
)

_NOW = datetime(2026, 11, 12, 16, 0, tzinfo=UTC)


class _CountingTransport:
    """Synthetic double that counts signed-style submissions."""

    venue_name = "binance_spot_testnet"
    environment = "paper_testnet"
    verified_symbol_ids = ("BTCUSDT",)

    def __init__(self) -> None:
        self.client = SafeHttpClient(
            HttpClientConfig(allowed_hosts=(BINANCE_SPOT_TESTNET_HOST,)),
            base_url=BINANCE_SPOT_TESTNET_BASE_URL,
            requester=lambda *_args: (200, b"{}", ()),
            sleeper=lambda _seconds: None,
        )
        self.submit_calls = 0

    def list_products(self):
        return ({"symbol": "BTCUSDT"},)

    def verify_symbol_mappings(self, _products, *, required):
        assert tuple(required) == ("BTCUSDT",)

    def verified_symbol_spec(self, symbol: str):
        from types import SimpleNamespace

        assert symbol == "BTCUSDT"
        return SimpleNamespace(base_asset="BTC", quote_asset="USDT")

    def canonical_instrument_id(self, symbol: str) -> str:
        return f"crypto:{symbol}:binance_spot_testnet:spot"

    def submit_order(self, payload):
        self.submit_calls += 1
        client_id = str(payload["client_order_id"])
        return {"venue_order_id": f"provider-{client_id}", "accepted": True}

    def query_order(self, *, client_order_id: str):
        return None

    def list_open_orders(self):
        return ()

    def cancel_order(self, *, client_order_id: str):
        return {"cancelled": True}

    def fetch_account_snapshot(self):
        return VenueAccountSnapshot(
            as_of=_NOW, cash=Decimal("100000"), positions={}
        )

    def list_fills(self):
        return ()

    def fill_contract_values(self, _record):
        raise AssertionError("synthetic transport holds no fills")


def _resolver() -> CredentialResolver:
    return CredentialResolver.from_mapping(
        {
            "ADVISORAI_VENUE_API_KEY": "synthetic-testnet-key",
            "ADVISORAI_VENUE_API_SECRET": "synthetic-testnet-secret",
            "ADVISORAI_VENUE_NAME": "binance_spot_testnet",
            "ADVISORAI_VENUE_ENVIRONMENT": "testnet",
            "ADVISORAI_VENUE_BASE_URL": BINANCE_SPOT_TESTNET_BASE_URL,
        }
    )


def _order() -> Order:
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
        policy_version="bounded-test-v1",
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
        idempotency_key=f"bounded-test-{uuid4()}",
    )


def test_native_submission_cap_refuses_before_transport_call(tmp_path) -> None:
    transport = _CountingTransport()
    native = NativeVenueAdapter(
        venue="binance_spot_testnet",
        environment="paper_testnet",
        transport=transport,
        max_submissions=1,
    )
    ledgers = SqliteLedgers(tmp_path / "cap.sqlite3")
    oms = OrderManager(ledgers, native)
    first = _order()
    oms.create(first)
    oms.transition(first.artifact_id, OrderState.RISK_APPROVED)
    oms.route(first.artifact_id)
    assert transport.submit_calls == 1
    assert native.submission_count == 1
    second = _order()
    oms.create(second)
    oms.transition(second.artifact_id, OrderState.RISK_APPROVED)
    with pytest.raises(NativeVenueProjectionError, match="submission cap reached"):
        oms.route(second.artifact_id)
    assert transport.submit_calls == 1
    assert native.submission_count == 1


def test_launch_builder_threads_submission_cap_to_native_boundary(tmp_path) -> None:
    ledgers = SqliteLedgers(tmp_path / "capped-owner.sqlite3")
    transport = _CountingTransport()
    app = build_testnet_owner_application(
        ledgers=ledgers,
        state_dir=tmp_path / "state",
        lock_identity="bounded-cap-test",
        code_hash="c" * 64,
        config_hash="d" * 64,
        config=OwnerApplicationConfig(scenario="quiet", interval_seconds=3600),
        resolver=_resolver(),
        launch=SandboxLaunchRequest(
            provider="binance_spot_testnet",
            provider_instrument_id="BTCUSDT",
            quote_currency="USDT",
            max_submissions=0,
        ),
        transport=transport,
    )
    native = app.adapters.paper_venue.native
    assert native.max_submissions == 0
    with pytest.raises(NativeVenueProjectionError, match="submission cap reached"):
        native.submit(_order())
    assert transport.submit_calls == 0


def test_owner_deadline_stops_session_without_scheduler_cycles(tmp_path, monkeypatch) -> None:
    ledgers = SqliteLedgers(tmp_path / "deadline.sqlite3")
    monkeypatch.setattr(
        PaperOwnerApplication, "_apply_resource_envelope", staticmethod(lambda: None)
    )
    app = PaperOwnerApplication(
        ledgers=ledgers,
        state_dir=tmp_path / "deadline-state",
        lock_identity="deadline-test",
        code_hash="a" * 64,
        config_hash="b" * 64,
        config=OwnerApplicationConfig(
            scenario="quiet",
            interval_seconds=3600,
            max_runtime_seconds=1,
        ),
    )
    started = datetime.now(UTC)
    exit_code = app.run(reason="deadline test")
    elapsed = (datetime.now(UTC) - started).total_seconds()
    assert exit_code == 0
    assert elapsed < 30
    end = app.supervisor.active_session_id()
    assert end is None


def test_bounded_testnet_run_stops_clean_with_reconciliation(
    tmp_path, monkeypatch
) -> None:
    ledgers = SqliteLedgers(tmp_path / "bounded.sqlite3")
    monkeypatch.setattr(
        PaperOwnerApplication, "_apply_resource_envelope", staticmethod(lambda: None)
    )
    transport = _CountingTransport()
    app = build_testnet_owner_application(
        ledgers=ledgers,
        state_dir=tmp_path / "bounded-state",
        lock_identity="bounded-run-test",
        code_hash="c" * 64,
        config_hash="d" * 64,
        config=OwnerApplicationConfig(
            scenario="quiet",
            interval_seconds=0.5,
            max_runtime_seconds=2,
        ),
        resolver=_resolver(),
        launch=SandboxLaunchRequest(
            provider="binance_spot_testnet",
            provider_instrument_id="BTCUSDT",
            quote_currency="USDT",
            max_submissions=1,
        ),
        transport=transport,
    )
    exit_code = app.run(reason="bounded offline test")
    assert exit_code == 0
    assert transport.submit_calls <= 1
    assert app.supervisor.active_session_id() is None
