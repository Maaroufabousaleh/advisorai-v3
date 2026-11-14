"""P02: baseline hydration preserves the verified nonzero BTC position.

Uses the real Binance Spot Testnet adapter with mocked HTTP. Proves the
launcher maps the canonical snapshot position to the local baseline with
the exact quantity, then proves a zero-submission session reaches SESSION
READY, stops at its deadline, reconciles cleanly, and releases leases.
"""

from __future__ import annotations

import json
from decimal import Decimal

from advisorai.config import CredentialResolver
from advisorai.integrations import (
    BINANCE_SPOT_TESTNET_BASE_URL,
    build_binance_spot_testnet_transport,
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
