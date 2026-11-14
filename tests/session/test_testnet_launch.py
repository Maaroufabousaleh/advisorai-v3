"""Offline contracts for the supervised testnet launch path.

All credentials are synthetic fixtures for injected transports. No
network call and no real ``secrets.env`` value is used here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

import pytest

from advisorai.config import CredentialResolver
from advisorai.integrations import (
    BINANCE_SPOT_TESTNET_BASE_URL,
    BINANCE_SPOT_TESTNET_HOST,
)
from advisorai.integrations.http import HttpClientConfig, SafeHttpClient
from advisorai.ledger import SqliteLedgers
from advisorai.session.application import OwnerApplicationConfig
from advisorai.session.testnet_launch import (
    SandboxFixtureDataAdapter,
    SandboxLaunchRequest,
    build_testnet_owner_application,
)


class SyntheticBinanceTransport:
    """Minimal synthetic double behind the reviewed Binance testnet boundary."""

    venue_name = "binance_spot_testnet"
    environment = "paper_testnet"

    def __init__(self) -> None:
        self.client = SafeHttpClient(
            HttpClientConfig(allowed_hosts=(BINANCE_SPOT_TESTNET_HOST,)),
            base_url=BINANCE_SPOT_TESTNET_BASE_URL,
            requester=lambda *_args: (200, b"{}", ()),
            sleeper=lambda _seconds: None,
        )
        self.verified_symbol_ids = ("BTCUSDT",)
        self.orders: dict[str, dict[str, object]] = {}

    def list_products(self):  # noqa: ANN001, ANN202
        return ({"symbol": "BTCUSDT"},)

    def verify_symbol_mappings(self, _products, *, required):  # noqa: ANN001
        assert tuple(required) == ("BTCUSDT",)

    def verified_symbol_spec(self, symbol: str):  # noqa: ANN001, ANN202
        from types import SimpleNamespace

        assert symbol == "BTCUSDT"
        return SimpleNamespace(base_asset="BTC", quote_asset="USDT")

    def canonical_instrument_id(self, symbol: str) -> str:
        return f"crypto:{symbol}:binance_spot_testnet:spot"

    def submit_order(self, payload):  # noqa: ANN001, ANN202
        client_id = str(payload["client_order_id"])
        self.orders[client_id] = {"id": f"provider-{client_id}"}
        return {"venue_order_id": f"provider-{client_id}", "accepted": True}

    def query_order(self, *, client_order_id: str):  # noqa: ANN202
        return self.orders.get(client_order_id)

    def list_open_orders(self):  # noqa: ANN202
        return ()

    def cancel_order(self, *, client_order_id: str):  # noqa: ANN202
        self.orders.pop(client_order_id, None)
        return {"cancelled": True}

    def fetch_account_snapshot(self):  # noqa: ANN202
        from advisorai.execution.reconciliation import VenueAccountSnapshot

        return VenueAccountSnapshot(
            as_of=datetime.now(UTC), cash=Decimal("100000"), positions={}
        )

    def list_fills(self):  # noqa: ANN202
        return ()

    def fill_contract_values(self, _record):  # noqa: ANN001, ANN202
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


def test_testnet_launch_composes_reviewed_profile_without_real_secrets(tmp_path) -> None:
    ledgers = SqliteLedgers(tmp_path / "testnet-owner.sqlite3")
    app = build_testnet_owner_application(
        ledgers=ledgers,
        state_dir=tmp_path / "state",
        lock_identity="testnet-launch-test",
        code_hash="c" * 64,
        config_hash="d" * 64,
        config=OwnerApplicationConfig(scenario="quiet", interval_seconds=3600),
        resolver=_resolver(),
        launch=SandboxLaunchRequest(
            provider="binance_spot_testnet",
            provider_instrument_id="BTCUSDT",
            quote_currency="USDT",
        ),
        transport=SyntheticBinanceTransport(),
    )
    assert app.venue_profile == "binance_spot_testnet"
    assert app.adapters.instrument.venue_symbol == "BTCUSDT"
    assert app.adapters.instrument.quote_asset == "USDT"
    assert app.adapters.paper_venue.venue == "paper"
    assert "synthetic-testnet-key" not in repr(app.adapters.paper_venue)


def test_testnet_launch_refuses_wrong_instrument_or_live_identity(tmp_path) -> None:
    ledgers = SqliteLedgers(tmp_path / "testnet-refuse.sqlite3")
    kwargs = {
        "ledgers": ledgers,
        "state_dir": tmp_path / "state",
        "lock_identity": "testnet-launch-refuse",
        "code_hash": "c" * 64,
        "config_hash": "d" * 64,
        "config": OwnerApplicationConfig(scenario="quiet"),
        "resolver": _resolver(),
        "transport": SyntheticBinanceTransport(),
    }
    with pytest.raises(ValueError, match="exactly BTCUSDT"):
        build_testnet_owner_application(
            **kwargs,  # type: ignore[arg-type]
            launch=SandboxLaunchRequest(
                provider="binance_spot_testnet",
                provider_instrument_id="ETHUSDT",
                quote_currency="USDT",
            ),
        )
    live = CredentialResolver.from_mapping(
        {
            "ADVISORAI_VENUE_API_KEY": "synthetic-testnet-key",
            "ADVISORAI_VENUE_API_SECRET": "synthetic-testnet-secret",
            "ADVISORAI_VENUE_NAME": "binance_spot_testnet",
            "ADVISORAI_VENUE_ENVIRONMENT": "live",
            "ADVISORAI_VENUE_BASE_URL": "https://api.binance.com",
        }
    )
    with pytest.raises(ValueError, match="paper/testnet"):
        build_testnet_owner_application(
            ledgers=ledgers,
            state_dir=tmp_path / "state",
            lock_identity="testnet-launch-refuse",
            code_hash="c" * 64,
            config_hash="d" * 64,
            config=OwnerApplicationConfig(scenario="quiet"),
            transport=SyntheticBinanceTransport(),
            launch=SandboxLaunchRequest(
                provider="binance_spot_testnet",
                provider_instrument_id="BTCUSDT",
                quote_currency="USDT",
            ),
            resolver=live,
        )


def test_testnet_fixture_adapter_marks_explicit_fixture_identity(tmp_path) -> None:  # noqa: ARG001
    adapter = SandboxFixtureDataAdapter("USDT")
    assert adapter.watermark_key == "testnet-fixture/BTCUSDT-1h"
    assert adapter.quote_currency == "USDT"
    cutoff = datetime(2026, 1, 2, tzinfo=UTC)
    snapshot = adapter.snapshot_at(cutoff)
    assert snapshot.artifact_id == uuid5(
        NAMESPACE_URL, f"advisorai-v3/testnet-fixture-snapshot/{cutoff.isoformat()}"
    )
    market = adapter.market_at(snapshot)
    assert market.marks["BTC"] > 0
