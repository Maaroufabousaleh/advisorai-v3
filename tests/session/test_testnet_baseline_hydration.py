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
            # Saved-evidence clock: the failed canary's baseline snapshot was
            # taken 2026-11-16, after fill 2697483 (2026-11-14T00:51:51.787Z).
            return (200, json.dumps({"serverTime": 1794787500000}).encode(), ())
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


def _historical_fill(symbol: str = "BTCUSDT") -> dict:
    """Sanitized shape of preserved venue fill BTCUSDT:2697483.

    Mirrors the real ``myTrades`` record from lifecycle
    ``20261114T005138.117654Z`` (venue ``occurred_at``
    2026-11-14T00:51:51.787Z, buy 0.00008 BTC, zero fee): economics
    preserved exactly because the accounting proof depends on them.
    """
    return {
        "symbol": symbol,
        "id": 2697483,
        "orderId": 424242,
        "price": "68384.74",
        "qty": "0.00008",
        "quoteQty": "5.47078",
        "commission": "0",
        "commissionAsset": "USDT",
        "time": 1794617511787,
        "isBuyer": True,
        "isMaker": False,
        "isBestMatch": True,
    }


def _requester_with_fills(account: dict, calls: list, fills: list[dict]):
    """Seeded requester that also serves venue trade history per symbol."""
    base = _requester(account, calls)

    def request(method, url, headers, body, timeout):
        if "/api/v3/myTrades" in url:
            calls.append((method, url))
            symbol = "BTCUSDT" if "BTCUSDT" in url else "ETHUSDT"
            matching = [fill for fill in fills if fill["symbol"] == symbol]
            return (200, json.dumps(matching).encode(), ())
        return base(method, url, headers, body, timeout)

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
    transport, baseline, balance_cutoff = read_testnet_baseline(
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
    transport, baseline, balance_cutoff = read_testnet_baseline(
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


def test_baseline_refuses_locked_in_profile_leg_before_seeding(monkeypatch) -> None:
    """P02: a locked BTC leg refuses the baseline before any seeding.

    Position quantities are free+locked totals, so a stuck open order or
    partially settled fill would otherwise seed untradeable funds as
    trading baseline. The guard names the locked leg, refuses seeding,
    and performs GET reads only.
    """
    calls: list = []
    account = _account()
    account["balances"] = [
        {"asset": "USDT", "free": "1000", "locked": "0"},
        {"asset": "BTC", "free": "0.005", "locked": "0.005"},
    ]
    real_builder = build_binance_spot_testnet_transport

    def seeded_builder(resolver, **kwargs):
        return real_builder(
            resolver, requester=_requester(account, calls), **kwargs
        )

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )
    with pytest.raises(ValueError, match="locked BTC/USDT balance"):
        read_testnet_baseline(resolver=_resolver(), launch=_launch())
    assert calls and all(method == "GET" for method, _ in calls)


def test_baseline_refuses_unmapped_canonical_position_but_pins_faucet_assets(
    monkeypatch,
) -> None:
    """P02: faucet assets pin into the baseline; foreign canonical keys refuse.

    The real Binance Spot Testnet account carries hundreds of funded
    faucet assets (515 observed). Refusing them all makes the account
    unlaunchable, so ``asset:`` balances pin into the durable baseline as
    frozen, unmarked positions: excluded from equity and buying power,
    exactly reconciled, any drift fails closed. A nonzero canonical
    position key outside the verified instrument still refuses.
    """
    calls: list = []
    account = _account()
    account["balances"] = [
        {"asset": "USDT", "free": "1000", "locked": "0"},
        {"asset": "BTC", "free": "0.010", "locked": "0"},
        {"asset": "ETH", "free": "1.5", "locked": "0"},
        {"asset": "BNB", "free": "2", "locked": "0.25"},
    ]
    real_builder = build_binance_spot_testnet_transport

    def seeded_builder(resolver, **kwargs):
        return real_builder(
            resolver, requester=_requester(account, calls), **kwargs
        )

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )
    transport, baseline, balance_cutoff = read_testnet_baseline(
        resolver=_resolver(), launch=_launch()
    )
    assert transport.verified_symbol_ids == ("BTCUSDT",)
    assert baseline.cash == Decimal("1000")
    assert baseline.positions == {
        "BTC": Decimal("0.010"),
        "asset:ETH": Decimal("1.5"),
        "asset:BNB": Decimal("2.25"),
    }
    assert baseline.marks == {"BTC": Decimal("50000")}
    assert baseline.equity() == Decimal("1000") + Decimal("0.010") * Decimal("50000")


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
    transport, baseline, balance_cutoff = read_testnet_baseline(resolver=_resolver(), launch=launch)
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
        baseline_balance_cutoff=balance_cutoff,
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
    transport, baseline, balance_cutoff = read_testnet_baseline(
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
    transport, baseline, balance_cutoff = read_testnet_baseline(resolver=_resolver(), launch=launch)
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
            baseline_balance_cutoff=balance_cutoff,
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
    transport, baseline, balance_cutoff = read_testnet_baseline(
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
        baseline_balance_cutoff=balance_cutoff,
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

def _multi_asset_account() -> dict:
    """Sanitized representative Testnet shape: BTC/USDT plus faucet dust."""
    return {
        "accountType": "SPOT",
        "canTrade": True,
        "balances": [
            {"asset": "USDT", "free": "1000", "locked": "0"},
            {"asset": "BTC", "free": "0.010", "locked": "0"},
            {"asset": "ETH", "free": "1.5", "locked": "0"},
            {"asset": "BNB", "free": "2", "locked": "0.25"},
            {"asset": "SOL", "free": "0", "locked": "0"},
        ],
    }


def _seeded_binance(monkeypatch, account: dict, calls: list) -> None:
    real_builder = build_binance_spot_testnet_transport

    def seeded_builder(resolver, **kwargs):
        return real_builder(
            resolver, requester=_requester(account, calls), **kwargs
        )

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )


def test_multi_asset_zero_submission_session_reconciles_clean(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02: real 515-asset account shape reaches READY and reconciles clean.

    End-to-end through the real launcher path and owner adapter with
    mocked HTTP: faucet holdings pin into the baseline, contribute
    nothing to equity, and reconcile exactly across recovery and the
    deadline stop. Zero venue writes.
    """
    monkeypatch.setattr(
        PaperOwnerApplication, "_apply_resource_envelope", staticmethod(lambda: None)
    )
    calls: list = []
    _seeded_binance(monkeypatch, _multi_asset_account(), calls)
    ledgers = SqliteLedgers(tmp_path / "p02-multi.sqlite3")
    launch = _launch()
    transport, baseline, balance_cutoff = read_testnet_baseline(resolver=_resolver(), launch=launch)
    assert baseline.positions == {
        "BTC": Decimal("0.010"),
        "asset:ETH": Decimal("1.5"),
        "asset:BNB": Decimal("2.25"),
    }
    assert "asset:SOL" not in baseline.positions
    app = build_testnet_owner_application(
        ledgers=ledgers,
        state_dir=tmp_path / "p02-multi-state",
        lock_identity="p02-multi-asset-zero-submission",
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
        baseline_balance_cutoff=balance_cutoff,
    )
    assert app.adapters.paper_venue.native.max_submissions == 0
    exit_code = app.run(reason="p02 multi-asset zero-submission test")
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "SESSION READY" in output
    assert "stop_reason=runtime_deadline_reached" in output
    assert "outcome=clean" in output
    assert "reconciliation=reconciled" in output
    assert "leases_released=True" in output
    assert all("/api/v3/order" not in url for _, url in calls)
    assert app.supervisor.active_session_id() is None


def test_multi_asset_session_fails_closed_on_faucet_drift(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02 negative: an unaccounted faucet change fails, never reconciles.

    Same session, but the venue's ETH balance moves mid-run (Testnet
    faucet behavior, external interference, or a mis-scoped fee).
    Recovery or the stop reconciliation must refuse clean: exit 2,
    outcome unclean, no reconciled claim.
    """
    monkeypatch.setattr(
        PaperOwnerApplication, "_apply_resource_envelope", staticmethod(lambda: None)
    )
    calls: list = []
    live = {"balances": _multi_asset_account()}

    def _live_requester(method, url, headers, body, timeout):
        return _requester(live["balances"], calls)(method, url, headers, body, timeout)

    real_builder = build_binance_spot_testnet_transport

    def seeded_builder(resolver, **kwargs):
        return real_builder(resolver, requester=_live_requester, **kwargs)

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )
    ledgers = SqliteLedgers(tmp_path / "p02-drift.sqlite3")
    launch = _launch()
    transport, baseline, balance_cutoff = read_testnet_baseline(resolver=_resolver(), launch=launch)
    app = build_testnet_owner_application(
        ledgers=ledgers,
        state_dir=tmp_path / "p02-drift-state",
        lock_identity="p02-multi-asset-drift",
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
        baseline_balance_cutoff=balance_cutoff,
    )
    live["balances"] = {
        "accountType": "SPOT",
        "canTrade": True,
        "balances": [
            {"asset": "USDT", "free": "1000", "locked": "0"},
            {"asset": "BTC", "free": "0.010", "locked": "0"},
            {"asset": "ETH", "free": "1.6", "locked": "0"},
            {"asset": "BNB", "free": "2", "locked": "0.25"},
            {"asset": "SOL", "free": "0", "locked": "0"},
        ],
    }
    exit_code = app.run(reason="p02 multi-asset drift test")
    output = capsys.readouterr().out
    assert exit_code == 2
    assert "outcome=clean" not in output
    assert "reconciliation=reconciled" not in output
    assert all("/api/v3/order" not in url for _, url in calls)

def test_first_start_with_historical_venue_fill_in_opening_balances(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02 canary repro: session c069585e blocked on fill BTCUSDT:2697483.

    First start with an empty local OMS against a funded multi-asset
    account whose opening BTC balance already reflects the historical
    lifecycle fill (buy 0.00008 BTC at 68384.74, zero fee, venue time
    2026-11-14T00:51:51.787Z). No open orders, nothing locked.
    Pre-fix this abstains on ``account_order_reconciliation`` before
    SESSION READY; post-fix the baseline-absorbed fill must not block
    recovery and must never be applied twice.
    """
    monkeypatch.setattr(
        PaperOwnerApplication, "_apply_resource_envelope", staticmethod(lambda: None)
    )
    calls: list = []
    account = _multi_asset_account()
    fills = [_historical_fill()]
    real_builder = build_binance_spot_testnet_transport

    def seeded_builder(resolver, **kwargs):
        return real_builder(
            resolver, requester=_requester_with_fills(account, calls, fills), **kwargs
        )

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )
    ledgers = SqliteLedgers(tmp_path / "p02-historical.sqlite3")
    launch = _launch()
    transport, baseline, balance_cutoff = read_testnet_baseline(resolver=_resolver(), launch=launch)
    app = build_testnet_owner_application(
        ledgers=ledgers,
        state_dir=tmp_path / "p02-historical-state",
        lock_identity="p02-historical-fill",
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
        baseline_balance_cutoff=balance_cutoff,
    )
    exit_code = app.run(reason="p02 historical-fill repro")
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "SESSION READY" in output
    assert "stop_reason=runtime_deadline_reached" in output
    assert "outcome=clean" in output
    assert "reconciliation=reconciled" in output
    assert "leases_released=True" in output
    assert all("/api/v3/order" not in url for _, url in calls)
    assert app.supervisor.active_session_id() is None
    # The historical fill was never applied to the local account: the
    # session ends holding exactly the opening baseline quantities.
    assert app.account.positions.get("BTC") == baseline.positions["BTC"]
    assert app.account.cash == baseline.cash
    order_events = ledgers.events(LedgerNamespace.ORDER)
    assert all(
        event.event_type != "fill_recorded" for event in order_events
    )
    account_events = ledgers.events(LedgerNamespace.ACCOUNT)
    assert all(
        event.event_type != "fill_applied" for event in account_events
    )

def test_new_fitting_fill_is_not_absorbed_as_baseline_settled(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02: a NEW unmatched fill that fits the baseline still blocks recovery.

    Regression against the 8085b27 balance-only proof: a fill nobody
    ordered arrives AFTER the baseline snapshot, but its economics are
    small enough to fit inside the opening balances. Arithmetic alone
    would absorb it; recovery must instead block, because no verified
    evidence ties this fill to the exact opening baseline.
    """
    account = _multi_asset_account()
    new_fill = _historical_fill()
    new_fill["id"] = 9990002
    new_fill["orderId"] = 9990002
    # After the mocked baseline snapshot (serverTime 1794787500000): a fill
    # nobody ordered, small enough to fit the balances, but not settled.
    new_fill["time"] = 1794787600000
    app, calls = _run_zero_submission_session(tmp_path, monkeypatch, account, [new_fill])
    exit_code = app.run(reason="p02 new-fill over-acceptance")
    output = capsys.readouterr().out
    assert exit_code == 2
    assert "SESSION READY" not in output
    assert "RECOVERY INCOMPLETE" in output
    assert "outcome=clean" not in output
    assert "reconciliation=reconciled" not in output
    assert all("/api/v3/order" not in url for _, url in calls)

def _run_zero_submission_session(tmp_path, monkeypatch, account, fills, open_orders=()):
    """Drive the real launcher path with seeded venue history; return app + output."""

    monkeypatch.setattr(
        PaperOwnerApplication, "_apply_resource_envelope", staticmethod(lambda: None)
    )
    calls: list = []
    real_builder = build_binance_spot_testnet_transport
    base_requester = _requester_with_fills(account, calls, fills)

    def request(method, url, headers, body, timeout):
        if "/api/v3/openOrders" in url:
            calls.append((method, url))
            return (200, json.dumps(list(open_orders)).encode(), ())
        return base_requester(method, url, headers, body, timeout)

    def seeded_builder(resolver, **kwargs):
        return real_builder(resolver, requester=request, **kwargs)

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )
    ledgers = SqliteLedgers(tmp_path / "p02-failclosed.sqlite3")
    launch = _launch()
    transport, baseline, balance_cutoff = read_testnet_baseline(resolver=_resolver(), launch=launch)
    app = build_testnet_owner_application(
        ledgers=ledgers,
        state_dir=tmp_path / "p02-failclosed-state",
        lock_identity="p02-failclosed",
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
        baseline_balance_cutoff=balance_cutoff,
    )
    return app, calls


def test_unknown_fill_exceeding_baseline_still_blocks_recovery(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02: a fill the baseline cannot contain still blocks recovery.

    Same first start, but the venue reports a NEW unknown fill buying
    more BTC than the opening balance holds. Reversal underflows, so
    absorption refuses and the session abstains before READY — the
    historical-fill fix must not become a blanket timestamp filter.
    """
    account = _multi_asset_account()
    oversized = _historical_fill()
    oversized["id"] = 9990001
    oversized["qty"] = "5.0"
    app, calls = _run_zero_submission_session(
        tmp_path, monkeypatch, account, [oversized]
    )
    exit_code = app.run(reason="p02 unknown-fill negative")
    output = capsys.readouterr().out
    assert exit_code == 2
    assert "SESSION READY" not in output
    assert "RECOVERY INCOMPLETE" in output
    assert "outcome=clean" not in output
    assert "reconciliation=reconciled" not in output
    assert all("/api/v3/order" not in url for _, url in calls)


def test_unexplained_open_order_still_blocks_recovery_with_absorbed_fill(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02: an open order nobody owns still blocks, even with history absorbed.

    The historical fill absorbs, but the venue also reports an open
    BTCUSDT order with a foreign client identity. The open-order sweep
    must still fail closed before READY.
    """
    account = _multi_asset_account()
    foreign_order = {
        "symbol": "BTCUSDT",
        "orderId": 888001,
        "clientOrderId": "foreign-operator-order",
        "price": "60000.00",
        "origQty": "0.001",
        "executedQty": "0",
        "status": "NEW",
        "type": "LIMIT",
        "side": "BUY",
        "time": 1794617600000,
    }
    app, calls = _run_zero_submission_session(
        tmp_path, monkeypatch, account, [_historical_fill()], [foreign_order]
    )
    exit_code = app.run(reason="p02 open-order negative")
    output = capsys.readouterr().out
    assert exit_code == 2
    assert "SESSION READY" not in output
    assert "outcome=clean" not in output
    assert all("/api/v3/order" not in url for _, url in calls)


def test_absorbed_fill_survives_restart_without_reapply_or_block(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02: restart reuses the durable absorption record, still reconciles.

    After a clean absorbed-fill session, a second app instance over the
    same ledgers (restart) must recognize the recorded fill without
    re-proving it, must not apply it, and must still reach READY and
    reconcile clean. Asserts exactly one absorption record exists.
    """
    from advisorai.ledger import LedgerNamespace

    monkeypatch.setattr(
        PaperOwnerApplication, "_apply_resource_envelope", staticmethod(lambda: None)
    )
    calls: list = []
    account = _multi_asset_account()
    fills = [_historical_fill()]
    real_builder = build_binance_spot_testnet_transport

    def seeded_builder(resolver, **kwargs):
        return real_builder(
            resolver, requester=_requester_with_fills(account, calls, fills), **kwargs
        )

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )
    ledgers = SqliteLedgers(tmp_path / "p02-restart.sqlite3")
    launch = _launch()
    transport, baseline, balance_cutoff = read_testnet_baseline(resolver=_resolver(), launch=launch)

    def build(tag):
        return build_testnet_owner_application(
            ledgers=ledgers,
            state_dir=tmp_path / "p02-restart-state",
            lock_identity=f"p02-restart-{tag}",
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
            baseline_balance_cutoff=balance_cutoff,
        )

    first = build("first")
    assert first.run(reason="p02 restart first") == 0
    capsys.readouterr()
    second = build("second")
    exit_code = second.run(reason="p02 restart second")
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "SESSION READY" in output
    assert "outcome=clean" in output
    assert "reconciliation=reconciled" in output
    absorptions = [
        event
        for event in ledgers.events(LedgerNamespace.VENUE)
        if event.event_type == "baseline_settled_fill_absorbed"
    ]
    assert len(absorptions) == 1
    assert absorptions[0].payload["venue_fill_id"] == "BTCUSDT:2697483"
    assert second.account.positions.get("BTC") == baseline.positions["BTC"]
    assert second.account.cash == baseline.cash

def test_changed_absorbed_fill_blocks_recovery_after_restart(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02: venue history drifting under an absorption record blocks restart.

    First run absorbs the historical fill and passes. On restart the
    venue replays the same fill ID with different economics (price
    drift). The durable record no longer matches, so recovery fails
    closed instead of trusting stale evidence.
    """
    from advisorai.ledger import LedgerNamespace

    monkeypatch.setattr(
        PaperOwnerApplication, "_apply_resource_envelope", staticmethod(lambda: None)
    )
    calls: list = []
    account = _multi_asset_account()
    fills = [_historical_fill()]
    real_builder = build_binance_spot_testnet_transport

    def seeded_builder(resolver, **kwargs):
        return real_builder(
            resolver, requester=_requester_with_fills(account, calls, fills), **kwargs
        )

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )
    ledgers = SqliteLedgers(tmp_path / "p02-drift.sqlite3")
    launch = _launch()
    transport, baseline, balance_cutoff = read_testnet_baseline(resolver=_resolver(), launch=launch)

    def build(tag):
        return build_testnet_owner_application(
            ledgers=ledgers,
            state_dir=tmp_path / "p02-drift-state",
            lock_identity=f"p02-drift-{tag}",
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
            baseline_balance_cutoff=balance_cutoff,
        )

    assert build("first").run(reason="p02 drift first") == 0
    capsys.readouterr()
    drifted = _historical_fill()
    drifted["price"] = "70000.00"
    fills.clear()
    fills.append(drifted)
    exit_code = build("second").run(reason="p02 drift second")
    output = capsys.readouterr().out
    assert exit_code == 2
    assert "SESSION READY" not in output
    assert "RECOVERY INCOMPLETE" in output
    absorptions = [
        event
        for event in ledgers.events(LedgerNamespace.VENUE)
        if event.event_type == "baseline_settled_fill_absorbed"
    ]
    assert len(absorptions) == 1
    assert all("/api/v3/order" not in url for _, url in calls)


def test_new_fill_after_absorbed_restart_still_blocks_recovery(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02: a new post-snapshot fill blocks even with history absorbed.

    First run absorbs the historical fill and passes. On restart the
    venue additionally reports a NEW unmatched fill that landed after
    the baseline snapshot but fits the balances. The proof (predates
    the snapshot) fails for the new fill, so recovery fails closed —
    the old absorption must not whitelist new venue activity.
    """
    monkeypatch.setattr(
        PaperOwnerApplication, "_apply_resource_envelope", staticmethod(lambda: None)
    )
    calls: list = []
    account = _multi_asset_account()
    fills = [_historical_fill()]
    real_builder = build_binance_spot_testnet_transport

    def seeded_builder(resolver, **kwargs):
        return real_builder(
            resolver, requester=_requester_with_fills(account, calls, fills), **kwargs
        )

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )
    ledgers = SqliteLedgers(tmp_path / "p02-newafter.sqlite3")
    launch = _launch()
    transport, baseline, balance_cutoff = read_testnet_baseline(resolver=_resolver(), launch=launch)

    def build(tag):
        return build_testnet_owner_application(
            ledgers=ledgers,
            state_dir=tmp_path / "p02-newafter-state",
            lock_identity=f"p02-newafter-{tag}",
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
            baseline_balance_cutoff=balance_cutoff,
        )

    assert build("first").run(reason="p02 new-after first") == 0
    capsys.readouterr()
    new_fill = _historical_fill()
    new_fill["id"] = 9990003
    new_fill["orderId"] = 9990003
    new_fill["time"] = 1794787600000  # after the mocked snapshot
    fills.append(new_fill)
    exit_code = build("second").run(reason="p02 new-after second")
    output = capsys.readouterr().out
    assert exit_code == 2
    assert "SESSION READY" not in output
    assert "RECOVERY INCOMPLETE" in output
    assert "outcome=clean" not in output
    assert all("/api/v3/order" not in url for _, url in calls)


def test_balance_gap_fill_is_not_absorbed_as_baseline_settled(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02: a fill in the balance-to-time gap still blocks recovery.

    The Binance account payload carries no timestamp; the adapter stamps
    the snapshot with a server time fetched AFTER the balances. A fill
    that lands between the balance read and that time read occurred
    after the snapshot's balance truth, however much its timestamp
    predates the cutoff. dbb1662 compares against the post-read cutoff
    and absorbs it; it must instead refuse, because the later
    reconciliation read moves the balances but does not retract the
    wrongly recorded absorption.
    """
    gap_fill = _historical_fill()
    gap_fill["id"] = 9990004
    gap_fill["orderId"] = 9990004
    # Balance read reflects T0; the mocked serverTime cutoff is T1 > T0.
    # The gap fill lands strictly inside (T0, T1): after the balances,
    # before the cutoff, small enough to fit the opening balances.
    gap_fill["time"] = 1794787400000
    balance_before = _multi_asset_account()
    # The gap fill (buy 0.00008 BTC @ 68384.74) settles into the venue
    # balances only on later reads: opening USDT 1000 -> 994.52922,
    # opening BTC 0.010 -> 0.01008. The mocked venue serves the
    # pre-fill balances until ``armed`` flips, then the settled ones.
    balance_after = {
        "accountType": "SPOT",
        "canTrade": True,
        "balances": [
            {"asset": "USDT", "free": "994.52922", "locked": "0"},
            {"asset": "BTC", "free": "0.01008", "locked": "0"},
            {"asset": "ETH", "free": "1.5", "locked": "0"},
            {"asset": "BNB", "free": "2", "locked": "0.25"},
            {"asset": "SOL", "free": "0", "locked": "0"},
        ],
    }
    live = {"armed": False, "time_calls": 0}
    calls: list = []

    monkeypatch.setattr(
        PaperOwnerApplication, "_apply_resource_envelope", staticmethod(lambda: None)
    )
    real_builder = build_binance_spot_testnet_transport

    def request(method, url, headers, body, timeout):
        if "/api/v3/time" in url:
            calls.append((method, url))
            live["time_calls"] += 1
            # Bracket the baseline balance read: the pre-read time T0
            # predates the gap fill, the post-read time T1 postdates it.
            # dbb1662 proves against T1 and absorbs; the fix proves
            # against the pre-read T0 and refuses.
            moment = 1794787300000 if live["time_calls"] == 1 else 1794787500000
            return (200, json.dumps({"serverTime": moment}).encode(), ())
        if "/api/v3/account" in url and live["armed"]:
            calls.append((method, url))
            return (200, json.dumps(balance_after).encode(), ())
        return _requester_with_fills(balance_before, calls, [gap_fill])(
            method, url, headers, body, timeout
        )

    def seeded_builder(resolver, **kwargs):
        return real_builder(resolver, requester=request, **kwargs)

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )
    ledgers = SqliteLedgers(tmp_path / "p02-gap.sqlite3")
    launch = _launch()
    transport, baseline, balance_cutoff = read_testnet_baseline(resolver=_resolver(), launch=launch)
    # The gap fill settles after the baseline read but before recovery's
    # venue reads: exactly the real timing that makes the post-read
    # cutoff unusable as inclusion proof.
    live["armed"] = True
    app = build_testnet_owner_application(
        ledgers=ledgers,
        state_dir=tmp_path / "p02-gap-state",
        lock_identity="p02-gap",
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
        baseline_balance_cutoff=balance_cutoff,
    )
    exit_code = app.run(reason="p02 balance-gap negative")
    output = capsys.readouterr().out
    assert exit_code == 2
    assert "SESSION READY" not in output
    assert "RECOVERY INCOMPLETE" in output
    assert "outcome=clean" not in output
    assert "reconciliation=reconciled" not in output
    assert all("/api/v3/order" not in url for _, url in calls)
    absorptions = [
        event
        for event in ledgers.events(LedgerNamespace.VENUE)
        if event.event_type == "baseline_settled_fill_absorbed"
    ]
    assert absorptions == []


def test_restart_with_moved_cutoff_blocks_despite_matching_fill(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02: a fresh baseline read cannot silently move the absorption cutoff.

    First run absorbs the known lifecycle fill and passes. On restart
    the operator re-reads the baseline from a live venue whose clock
    has advanced: same balances, same fill economics, but a later
    pre-read cutoff and as_of. The durable record pins the exact
    opening baseline it was proven against, so recovery fails closed
    instead of silently re-proving history against a moved cutoff.
    """
    from advisorai.ledger import LedgerNamespace

    monkeypatch.setattr(
        PaperOwnerApplication, "_apply_resource_envelope", staticmethod(lambda: None)
    )
    calls: list = []
    account = _multi_asset_account()
    fills = [_historical_fill()]
    clock = {"server_time": 1794787500000}
    real_builder = build_binance_spot_testnet_transport

    def request(method, url, headers, body, timeout):
        if "/api/v3/time" in url:
            calls.append((method, url))
            return (200, json.dumps({"serverTime": clock["server_time"]}).encode(), ())
        return _requester_with_fills(account, calls, fills)(
            method, url, headers, body, timeout
        )

    def seeded_builder(resolver, **kwargs):
        return real_builder(resolver, requester=request, **kwargs)

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )
    ledgers = SqliteLedgers(tmp_path / "p02-cutoffmove.sqlite3")
    launch = _launch()

    def build(tag, baseline, balance_cutoff, venue_transport):
        return build_testnet_owner_application(
            ledgers=ledgers,
            state_dir=tmp_path / "p02-cutoffmove-state",
            lock_identity=f"p02-cutoffmove-{tag}",
            code_hash="c" * 64,
            config_hash="d" * 64,
            config=OwnerApplicationConfig(
                scenario="quiet",
                interval_seconds=0.5,
                max_runtime_seconds=2,
            ),
            resolver=_resolver(),
            launch=launch,
            transport=venue_transport,
            baseline=baseline,
            baseline_balance_cutoff=balance_cutoff,
        )

    transport, baseline, balance_cutoff = read_testnet_baseline(
        resolver=_resolver(), launch=launch
    )
    assert build("first", baseline, balance_cutoff, transport).run(
        reason="p02 cutoff first"
    ) == 0
    capsys.readouterr()
    # Fresh venue read on restart: balances and fill identical, clock moved.
    clock["server_time"] = 1794787600000
    transport2, baseline2, balance_cutoff2 = read_testnet_baseline(
        resolver=_resolver(), launch=launch
    )
    assert balance_cutoff2 > balance_cutoff
    assert baseline2.cash == baseline.cash
    assert baseline2.positions == baseline.positions
    exit_code = build("second", baseline2, balance_cutoff2, transport2).run(
        reason="p02 cutoff second"
    )
    output = capsys.readouterr().out
    assert exit_code == 2
    assert "SESSION READY" not in output
    assert "RECOVERY INCOMPLETE" in output
    assert "outcome=clean" not in output
    assert "reconciliation=reconciled" not in output
    absorptions = [
        event
        for event in ledgers.events(LedgerNamespace.VENUE)
        if event.event_type == "baseline_settled_fill_absorbed"
    ]
    assert len(absorptions) == 1
    assert absorptions[0].payload["venue_fill_id"] == "BTCUSDT:2697483"
    assert all("/api/v3/order" not in url for _, url in calls)
