"""P02: baseline hydration preserves the verified nonzero BTC position.

Uses the real Binance Spot Testnet adapter with mocked HTTP. Proves the
launcher maps the canonical snapshot position to the local baseline with
the exact quantity, then proves a zero-submission session reaches SESSION
READY, stops at its deadline, reconciles cleanly, and releases leases.
"""

from __future__ import annotations

import base64
import json
from decimal import Decimal

import pytest

from advisorai.config import CredentialResolver
from advisorai.integrations import (
    BINANCE_SPOT_TESTNET_BASE_URL,
    COINBASE_EXCHANGE_SANDBOX_BASE_URL,
    build_binance_spot_testnet_transport,
    build_coinbase_exchange_sandbox_transport,
)
from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.session.application import OwnerApplicationConfig, PaperOwnerApplication
from advisorai.session.testnet_launch import (
    SandboxLaunchRequest,
    build_testnet_owner_application,
    read_testnet_baseline,
)
from tests.integrations.test_binance_spot import _account, _symbol


def _symbols() -> list[dict]:
    return [
        _symbol("BTCUSDT", "BTC", "USDT"),
        _symbol("ETHUSDT", "ETH", "USDT"),
    ]


def _requester(account: dict, calls: list):
    def request(method, url, headers, body, timeout):
        calls.append((method, url))
        if "/api/v3/exchangeInfo" in url:
            return (200, json.dumps({"symbols": _symbols()}).encode(), ())
        if "/api/v3/account" in url:
            return (200, json.dumps(account).encode(), ())
        if "/api/v3/time" in url:
            return (200, json.dumps({"serverTime": 1700000070000}).encode(), ())
        if "/api/v3/openOrders" in url:
            return (200, b"[]", ())
        if "/api/v3/myTrades" in url:
            return (200, b"[]", ())
        if "/api/v3/ticker/bookTicker" in url:
            return (
                200,
                json.dumps(
                    {
                        "symbol": "BTCUSDT",
                        "bidPrice": "50000.00",
                        "bidQty": "1.0",
                        "askPrice": "50000.10",
                        "askQty": "1.0",
                    }
                ).encode(),
                (),
            )
        raise AssertionError(f"unexpected mocked call {method} {url}")

    return request


def _resolver() -> CredentialResolver:
    return CredentialResolver.from_mapping(
        {
            "ADVISORAI_VENUE_NAME": "binance_spot_testnet",
            "ADVISORAI_VENUE_ENVIRONMENT": "paper_testnet",
            "ADVISORAI_VENUE_BASE_URL": BINANCE_SPOT_TESTNET_BASE_URL,
            "ADVISORAI_VENUE_API_KEY": "synthetic-testnet-key",
            "ADVISORAI_VENUE_API_SECRET": "synthetic-testnet-secret",
        }
    )


def _launch(**overrides) -> SandboxLaunchRequest:
    values = {
        "provider": "binance_spot_testnet",
        "provider_instrument_id": "BTCUSDT",
        "quote_currency": "USDT",
        "max_submissions": 0,
    }
    values.update(overrides)
    return SandboxLaunchRequest(**values)


def _coinbase_products() -> list[dict]:
    return [
        {
            "id": "BTC-USD",
            "base_currency": "BTC",
            "quote_currency": "USD",
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
            "base_min_size": "0.00001",
            "base_max_size": "1000",
            "min_market_funds": "1",
            "status": "online",
        },
        {
            "id": "ETH-USD",
            "base_currency": "ETH",
            "quote_currency": "USD",
            "base_increment": "0.000001",
            "quote_increment": "0.01",
            "base_min_size": "0.001",
            "base_max_size": "10000",
            "min_market_funds": "1",
            "status": "online",
        },
    ]


def _coinbase_accounts(btc_balance: str = "0.010") -> list[dict]:
    return [
        {
            "id": "usd-account",
            "currency": "USD",
            "balance": "1000",
            "available": "1000",
            "hold": "0",
        },
        {
            "id": "btc-account",
            "currency": "BTC",
            "balance": btc_balance,
            "available": btc_balance,
            "hold": "0",
        },
    ]


def _coinbase_requester(accounts: list[dict], calls: list):
    def request(method, url, headers, body, timeout):
        calls.append((method, url))
        path = url.split("?", 1)[0].rstrip("/")
        if path.endswith("/products"):
            return (200, json.dumps(_coinbase_products()).encode(), ())
        if path.endswith("/accounts"):
            return (200, json.dumps(accounts).encode(), ())
        if path.endswith("/time"):
            return (200, json.dumps({"epoch": 1700000000.0}).encode(), ())
        if path.endswith("/orders"):
            return (200, b"[]", ())
        if path.endswith("/fills"):
            return (200, b"[]", ())
        raise AssertionError(f"unexpected mocked call {method} {url}")

    return request


def _coinbase_resolver() -> CredentialResolver:
    return CredentialResolver.from_mapping(
        {
            "ADVISORAI_VENUE_NAME": "coinbase_exchange_sandbox",
            "ADVISORAI_VENUE_ENVIRONMENT": "paper_testnet",
            "ADVISORAI_VENUE_BASE_URL": COINBASE_EXCHANGE_SANDBOX_BASE_URL,
            "ADVISORAI_VENUE_API_KEY": "synthetic-testnet-key",
            # Computed at runtime like tests/integrations/test_coinbase_exchange.py:
            # a base64 literal trips the high-entropy secret detector.
            "ADVISORAI_VENUE_API_SECRET": base64.b64encode(b"synthetic-testnet-secret").decode(
                "ascii"
            ),
            "ADVISORAI_VENUE_PASSPHRASE": "synthetic-testnet-passphrase",
        }
    )


def _coinbase_launch(**overrides) -> SandboxLaunchRequest:
    values = {
        "provider": "coinbase_exchange_sandbox",
        "provider_instrument_id": "BTC-USD",
        "quote_currency": "USD",
        "max_submissions": 0,
    }
    values.update(overrides)
    return SandboxLaunchRequest(**values)


def test_baseline_preserves_exact_verified_btc_quantity(monkeypatch) -> None:
    """Nonzero BTC in the verified snapshot hydrates with exact quantity."""
    calls: list = []
    account = _account()
    real_builder = build_binance_spot_testnet_transport

    def seeded_builder(resolver, **kwargs):
        return real_builder(
            resolver, requester=_requester(account, calls), **kwargs
        )

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )
    transport, baseline = read_testnet_baseline(
        resolver=_resolver(), launch=_launch()
    )
    expected_key = transport.canonical_instrument_id("BTCUSDT")
    assert transport.verified_symbol_ids == ("BTCUSDT",)
    assert baseline.cash == Decimal("1000")
    assert baseline.positions == {"BTC": Decimal("0.010")}
    assert baseline.margin_used == Decimal("0")
    assert baseline.margin_available == Decimal("1000")
    assert expected_key == "crypto:BTCUSDT:binance_spot_testnet:spot"

def test_baseline_verifies_instrument_on_same_transport_before_account_read(
    monkeypatch,
) -> None:
    """P02: verification (exchangeInfo) precedes the account read, same transport."""
    calls: list = []
    account = _account()
    real_builder = build_binance_spot_testnet_transport
    seen: dict = {}

    def seeded_builder(resolver, **kwargs):
        transport = real_builder(
            resolver, requester=_requester(account, calls), **kwargs
        )
        seen["transport"] = transport
        return transport

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )
    transport, baseline = read_testnet_baseline(
        resolver=_resolver(), launch=_launch()
    )
    assert seen["transport"] is transport
    assert transport.verified_symbol_ids == ("BTCUSDT",)
    assert baseline.positions == {"BTC": Decimal("0.010")}
    kinds = [
        "exchangeInfo" if "/api/v3/exchangeInfo" in url else "account"
        for _, url in calls
        if "/api/v3/exchangeInfo" in url or "/api/v3/account" in url
    ]
    assert kinds[:2] == ["exchangeInfo", "account"]


def test_baseline_refuses_out_of_profile_balances(monkeypatch) -> None:
    """P02: nonzero balances outside BTCUSDT fail closed before hydration."""
    calls: list = []
    account = _account()
    account["balances"] = [
        balance if balance["asset"] != "ETH" else {"asset": "ETH", "free": "1.5", "locked": "0"}
        for balance in account["balances"]
    ]
    real_builder = build_binance_spot_testnet_transport

    def seeded_builder(resolver, **kwargs):
        return real_builder(
            resolver, requester=_requester(account, calls), **kwargs
        )

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )
    with pytest.raises(ValueError, match="clear it first|clear them first"):
        read_testnet_baseline(resolver=_resolver(), launch=_launch())


def test_zero_submission_session_reaches_ready_and_reconciles_clean(
    tmp_path, monkeypatch, capsys
) -> None:
    """Zero-submission run: READY, deadline stop, clean reconciliation, leases."""
    monkeypatch.setattr(
        PaperOwnerApplication, "_apply_resource_envelope", staticmethod(lambda: None)
    )
    calls: list = []
    account = _account()
    real_builder = build_binance_spot_testnet_transport

    def seeded_builder(resolver, **kwargs):
        return real_builder(
            resolver, requester=_requester(account, calls), **kwargs
        )

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )
    ledgers = SqliteLedgers(tmp_path / "p02.sqlite3")
    launch = _launch()
    transport, baseline = read_testnet_baseline(resolver=_resolver(), launch=launch)
    assert baseline.positions == {"BTC": Decimal("0.010")}
    app = build_testnet_owner_application(
        ledgers=ledgers,
        state_dir=tmp_path / "p02-state",
        lock_identity="p02-zero-submission",
        code_hash="c" * 64,
        config_hash="d" * 64,
        config=OwnerApplicationConfig(
            scenario="quiet",
            interval_seconds=0.5,
            max_runtime_seconds=2,
        ),
        resolver=_resolver(),
        launch=launch,
        transport=transport,
        baseline=baseline,
    )
    assert app.adapters.paper_venue.native.max_submissions == 0
    exit_code = app.run(reason="p02 zero-submission test")
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "SESSION READY" in output
    assert "stop_reason=runtime_deadline_reached" in output
    assert "outcome=clean" in output
    assert "reconciliation=reconciled" in output
    assert "leases_released=True" in output
    assert all("/api/v3/order" not in url for _, url in calls)
    assert app.supervisor.active_session_id() is None
    mission = ledgers.events(LedgerNamespace.MISSION)
    assert any(
        event.event_type == "session_ended"
        and event.payload.get("end", {}).get("outcome") == "clean"
        for event in mission
    )


def test_baseline_preserves_exact_verified_btc_quantity_on_coinbase(monkeypatch) -> None:
    """P02 parity: Coinbase BTC-USD hydrates with the exact nonzero quantity."""
    calls: list = []
    accounts = _coinbase_accounts("0.010")
    real_builder = build_coinbase_exchange_sandbox_transport

    def seeded_builder(resolver, **kwargs):
        return real_builder(resolver, requester=_coinbase_requester(accounts, calls), **kwargs)

    monkeypatch.setattr(
        "advisorai.integrations.build_coinbase_exchange_sandbox_transport",
        seeded_builder,
    )
    transport, baseline = read_testnet_baseline(
        resolver=_coinbase_resolver(), launch=_coinbase_launch()
    )
    expected_key = transport.canonical_instrument_id("BTC-USD")
    assert transport.verified_product_ids == ("BTC-USD",)
    assert baseline.cash == Decimal("1000")
    assert baseline.positions == {"BTC": Decimal("0.010")}
    assert baseline.margin_used == Decimal("0")
    assert baseline.margin_available == Decimal("1000")
    assert expected_key == "crypto:BTC-USD:coinbase_exchange_sandbox:spot"
    kinds = [
        "products" if url.rstrip("/").endswith("/products") else "accounts"
        for _, url in calls
        if url.rstrip("/").endswith("/products") or url.rstrip("/").endswith("/accounts")
    ]
    assert kinds[:2] == ["products", "accounts"]


def test_baseline_refuses_unmapped_coinbase_balances(monkeypatch) -> None:
    """P02 parity: nonzero unmapped Coinbase assets fail closed, never hydrate.

    Only BTC-USD is admitted for the launch, so the real adapter raises on
    the unmapped ETH balance during the snapshot read (layer below the
    launcher's own off-instrument guard); either way nothing hydrates.
    """
    calls: list = []
    accounts = _coinbase_accounts("0.010") + [
        {
            "id": "eth-account",
            "currency": "ETH",
            "balance": "1.5",
            "available": "1.5",
            "hold": "0",
        },
    ]
    real_builder = build_coinbase_exchange_sandbox_transport

    def seeded_builder(resolver, **kwargs):
        return real_builder(resolver, requester=_coinbase_requester(accounts, calls), **kwargs)

    monkeypatch.setattr(
        "advisorai.integrations.build_coinbase_exchange_sandbox_transport",
        seeded_builder,
    )
    from advisorai.integrations import VenueTransportError

    with pytest.raises(VenueTransportError, match="non-zero unmapped asset ETH"):
        read_testnet_baseline(resolver=_coinbase_resolver(), launch=_coinbase_launch())


def test_builder_reuses_owner_supervisor_and_stop_event(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02 owner-flow: the app runs on the caller's lock and signal event.

    Mirrors the owner launcher sequence exactly: ``read_testnet_baseline``
    first, then a ``SessionSupervisor`` that acquires the owner lock,
    then ``build_testnet_owner_application`` with that supervisor and
    the caller-owned stop event. The zero-submission run must reach
    SESSION READY and stop clean — a builder that constructed its own
    second supervisor would refuse with SESSION START REFUSED
    (same-process flock) before READY, and a builder that dropped the
    caller's stop event would leave SIGINT/SIGTERM unable to reach
    the app loop.
    """
    import threading

    from advisorai.session.supervisor import SessionSupervisor

    monkeypatch.setattr(
        PaperOwnerApplication, "_apply_resource_envelope", staticmethod(lambda: None)
    )
    calls: list = []
    real_builder = build_binance_spot_testnet_transport

    def seeded_builder(resolver, **kwargs):
        return real_builder(resolver, requester=_requester(_account(), calls), **kwargs)

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )
    state_dir = tmp_path / "p02-lock-handoff"
    ledgers = SqliteLedgers(tmp_path / "p02-lock-handoff.sqlite3")
    launch = _launch()
    transport, baseline = read_testnet_baseline(resolver=_resolver(), launch=launch)
    supervisor = SessionSupervisor(
        ledgers,
        state_dir=state_dir,
        lock_identity="p02-lock-handoff",
        code_hash="c" * 64,
        config_hash="d" * 64,
    )
    supervisor._acquire_lock()
    stop_event = threading.Event()
    try:
        app = build_testnet_owner_application(
            ledgers=ledgers,
            state_dir=state_dir,
            lock_identity="p02-lock-handoff",
            code_hash="c" * 64,
            config_hash="d" * 64,
            config=OwnerApplicationConfig(
                scenario="quiet",
                interval_seconds=0.5,
                max_runtime_seconds=2,
            ),
            resolver=_resolver(),
            launch=launch,
            transport=transport,
            baseline=baseline,
            supervisor=supervisor,
            stop_requested=stop_event,
        )
        assert app.supervisor is supervisor
        assert app.stop_requested is stop_event
        exit_code = app.run(reason="p02 lock-handoff test")
    finally:
        supervisor._release_lock()
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "SESSION READY" in output
    assert "SESSION START REFUSED" not in output
    assert "stop_reason=runtime_deadline_reached" in output
    assert "outcome=clean" in output
    assert "reconciliation=reconciled" in output
    assert "leases_released=True" in output
    assert all("/api/v3/order" not in url for _, url in calls)


def test_coinbase_zero_submission_session_reaches_ready_and_reconciles_clean(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02 parity: coinbase sandbox runs READY, deadline stop, clean reconcile.

    End-to-end through the real Coinbase sandbox adapter with mocked
    HTTP: verified baseline hydration, SESSION READY, deadline stop,
    clean reconciliation, released leases, and zero venue writes
    (no POST to /orders). The binance path has this coverage; the
    coinbase builder path previously had hydration-only tests.
    """
    monkeypatch.setattr(
        PaperOwnerApplication, "_apply_resource_envelope", staticmethod(lambda: None)
    )
    calls: list = []
    accounts = _coinbase_accounts("0.010")
    real_builder = build_coinbase_exchange_sandbox_transport

    def seeded_builder(resolver, **kwargs):
        return real_builder(
            resolver, requester=_coinbase_requester(accounts, calls), **kwargs
        )

    monkeypatch.setattr(
        "advisorai.integrations.build_coinbase_exchange_sandbox_transport",
        seeded_builder,
    )
    ledgers = SqliteLedgers(tmp_path / "p02-coinbase.sqlite3")
    launch = _coinbase_launch()
    transport, baseline = read_testnet_baseline(
        resolver=_coinbase_resolver(), launch=launch
    )
    assert baseline.positions == {"BTC": Decimal("0.010")}
    app = build_testnet_owner_application(
        ledgers=ledgers,
        state_dir=tmp_path / "p02-coinbase-state",
        lock_identity="p02-coinbase-zero-submission",
        code_hash="c" * 64,
        config_hash="d" * 64,
        config=OwnerApplicationConfig(
            scenario="quiet",
            interval_seconds=0.5,
            max_runtime_seconds=2,
        ),
        resolver=_coinbase_resolver(),
        launch=launch,
        transport=transport,
        baseline=baseline,
    )
    assert app.adapters.paper_venue.native.max_submissions == 0
    exit_code = app.run(reason="p02 coinbase zero-submission test")
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "SESSION READY" in output
    assert "stop_reason=runtime_deadline_reached" in output
    assert "outcome=clean" in output
    assert "reconciliation=reconciled" in output
    assert "leases_released=True" in output
    assert all(not (method == "POST" and url.rstrip("/").endswith("/orders")) for method, url in calls)
    assert app.supervisor.active_session_id() is None
    mission = ledgers.events(LedgerNamespace.MISSION)
    assert any(
        event.event_type == "session_ended"
        and event.payload.get("end", {}).get("outcome") == "clean"
        for event in mission
    )
