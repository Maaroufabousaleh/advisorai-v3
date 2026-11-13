"""Provider profiles bind verified sandbox connectors under the paper owner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from advisorai.config import CredentialResolver
from advisorai.contracts import InstrumentIdentity
from advisorai.execution.reconciliation import VenueAccountSnapshot
from advisorai.integrations import (
    BINANCE_SPOT_TESTNET_BASE_URL,
    BINANCE_SPOT_TESTNET_HOST,
    COINBASE_EXCHANGE_SANDBOX_BASE_URL,
    COINBASE_EXCHANGE_SANDBOX_HOST,
)
from advisorai.session.application import (
    LocalPaperOutcomeAdapter,
    OwnerApplicationAdapters,
)
from advisorai.session.provider_profiles import (
    ProviderProfileComponents,
    ProviderProfileContext,
    ProviderProfileRequest,
    build_binance_spot_testnet_profile,
    build_coinbase_exchange_sandbox_profile,
    build_provider_profile,
)


class ProfileTransport:
    environment = "paper_testnet"

    def __init__(self, provider: str, instrument: str, quote: str) -> None:
        self.venue_name = provider
        self.instrument = instrument
        self.quote = quote
        if provider == "binance_spot_testnet":
            self.base_url = BINANCE_SPOT_TESTNET_BASE_URL
            self.host = BINANCE_SPOT_TESTNET_HOST
        else:
            self.base_url = COINBASE_EXCHANGE_SANDBOX_BASE_URL
            self.host = COINBASE_EXCHANGE_SANDBOX_HOST
        self.client = SimpleNamespace(
            base_url=self.base_url,
            config=SimpleNamespace(allowed_hosts=(self.host,)),
        )
        self.verified_symbol_ids: tuple[str, ...] = ()
        self.verified_product_ids: tuple[str, ...] = ()
        self.verification_calls: list[tuple[str, ...]] = []
        self.orders: dict[str, dict[str, object]] = {}
        self.cash = Decimal("100000")
        self.product_reads = 0

    def list_products(self) -> tuple[Mapping[str, object], ...]:
        self.product_reads += 1
        return ({"id": self.instrument, "quote": self.quote},)

    def verify_symbol_mappings(self, _products, *, required):  # noqa: ANN001
        self.verification_calls.append(tuple(required))
        if tuple(required) != (self.instrument,):
            raise ValueError("unavailable symbol")
        self.verified_symbol_ids = (self.instrument,)

    def verify_product_mappings(self, _products, *, required):  # noqa: ANN001
        self.verification_calls.append(tuple(required))
        if tuple(required) != (self.instrument,):
            raise ValueError("unavailable product")
        self.verified_product_ids = (self.instrument,)

    def verified_symbol_spec(self, symbol: str):
        assert symbol == self.instrument
        return SimpleNamespace(base_asset="BTC", quote_asset=self.quote)

    def verified_product_spec(self, product_id: str):
        assert product_id == self.instrument
        return SimpleNamespace(base_currency="BTC", quote_currency=self.quote)

    def canonical_instrument_id(self, provider_instrument: str) -> str:
        return f"crypto:{provider_instrument}:{self.venue_name}:spot"

    def submit_order(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        client_id = str(payload["client_order_id"])
        record = {
            "id": f"provider-{client_id}",
            "client_order_id": client_id,
            "client_oid": client_id,
            "accepted": True,
        }
        self.orders[client_id] = record
        return {"venue_order_id": record["id"], "accepted": True}

    def query_order(self, *, client_order_id: str):
        return self.orders.get(client_order_id)

    def list_open_orders(self) -> Sequence[Mapping[str, object]]:
        return tuple(self.orders.values())

    def cancel_order(self, *, client_order_id: str):
        self.orders.pop(client_order_id, None)
        return {"cancelled": True}

    def fetch_account_snapshot(self) -> VenueAccountSnapshot:
        return VenueAccountSnapshot(as_of=datetime.now(UTC), cash=self.cash, positions={})

    def list_fills(self, **_kwargs: object):
        return ()

    def fill_contract_values(self, _record: Mapping[str, object]) -> dict[str, Any]:
        raise AssertionError("profile fixture does not exercise fill ingestion")


def _context(tmp_path) -> tuple[ProviderProfileContext, object]:  # noqa: ANN001
    from advisorai.ledger import SqliteLedgers
    from advisorai.session.application import OwnerApplicationConfig, PaperOwnerApplication

    ledgers = SqliteLedgers(tmp_path / "owner.sqlite3")
    app = PaperOwnerApplication(
        ledgers=ledgers,
        state_dir=tmp_path / "state",
        lock_identity="provider-profile-test",
        code_hash="c" * 64,
        config_hash="d" * 64,
        config=OwnerApplicationConfig(scenario="quiet"),
    )
    context = ProviderProfileContext(
        data=app.adapters.data,
        evidence_council=app.adapters.evidence_council,
        decision_model_factory=app._reference_decision_model,
        initial_account=app.adapters.initial_account,
        ledgers=ledgers,
        instrument=InstrumentIdentity(
            canonical_id="BTC",
            asset_class="crypto",
            venue="paper",
            venue_symbol="BTC-USD",
            base_asset="BTC",
            quote_asset="USD",
        ),
        account_quote_currency="USD",
        outcome_adapter=LocalPaperOutcomeAdapter(),
    )
    return context, app


def _components(context, provider: str, instrument: str, quote: str) -> ProviderProfileComponents:  # noqa: ANN001
    return ProviderProfileComponents(
        context=context,
        request=ProviderProfileRequest(
            provider=provider,
            provider_instrument_id=instrument,
            quote_currency=quote,
        ),
    )


def _resolver(provider: str = "binance_spot_testnet") -> CredentialResolver:
    values = {
        "ADVISORAI_VENUE_API_KEY": "profile-test-key",
        "ADVISORAI_VENUE_API_SECRET": "profile-test-secret",
        "ADVISORAI_VENUE_NAME": provider,
        "ADVISORAI_VENUE_ENVIRONMENT": "testnet",
        "ADVISORAI_VENUE_BASE_URL": (
            BINANCE_SPOT_TESTNET_BASE_URL
            if provider == "binance_spot_testnet"
            else COINBASE_EXCHANGE_SANDBOX_BASE_URL
        ),
    }
    if provider == "coinbase_exchange_sandbox":
        values["ADVISORAI_VENUE_PASSPHRASE"] = "profile-test-passphrase"
    return CredentialResolver.from_mapping(values)


@pytest.mark.parametrize(
    ("provider", "instrument", "builder"),
    [
        ("binance_spot_testnet", "BTCUSD", build_binance_spot_testnet_profile),
        (
            "coinbase_exchange_sandbox",
            "BTC-USD",
            build_coinbase_exchange_sandbox_profile,
        ),
    ],
)
def test_provider_profile_verifies_and_composes_exactly_one_sandbox_instrument(
    tmp_path, provider, instrument, builder
):
    context, _app = _context(tmp_path)
    transport = ProfileTransport(provider, instrument, "USD")
    profile = builder(
        _components(context, provider, instrument, "USD"),
        _resolver(provider),
        transport=transport,
    )
    assert isinstance(profile, OwnerApplicationAdapters)
    assert profile.paper_venue.venue == "paper"
    assert profile.instrument.venue_symbol == instrument
    assert profile.instrument.quote_asset == "USD"
    assert transport.verification_calls == [(instrument,)]


def test_provider_profile_current_account_verifier_rejects_baseline_mismatch(tmp_path):
    from advisorai.ledger import SqliteLedgers
    from advisorai.session.application import OwnerApplicationConfig, PaperOwnerApplication

    context, _local_app = _context(tmp_path)
    transport = ProfileTransport("coinbase_exchange_sandbox", "BTC-USD", "USD")
    profile = build_coinbase_exchange_sandbox_profile(
        _components(context, "coinbase_exchange_sandbox", "BTC-USD", "USD"),
        _resolver("coinbase_exchange_sandbox"),
        transport=transport,
    )
    owner = PaperOwnerApplication(
        ledgers=SqliteLedgers(tmp_path / "owner.sqlite3"),
        state_dir=tmp_path / "provider-state",
        lock_identity="provider-profile-owner",
        code_hash="c" * 64,
        config_hash="d" * 64,
        config=OwnerApplicationConfig(scenario="quiet"),
        adapters=profile,
        venue_profile="coinbase_exchange_sandbox",
    )
    assert owner._verify_current_order_account() is True
    transport.cash = Decimal("99999")
    assert owner._verify_current_order_account() is False


def test_profile_dispatch_refuses_unknown_provider_and_credentialless_request(tmp_path):
    context, _app = _context(tmp_path)
    unknown = _components(context, "kraken_live", "BTC-USD", "USD")
    with pytest.raises(ValueError, match="unknown provider"):
        build_provider_profile(unknown, _resolver())

    missing = CredentialResolver.from_mapping({})
    valid = _components(context, "coinbase_exchange_sandbox", "BTC-USD", "USD")
    with pytest.raises(ValueError, match="PAPER_VENUE credentials"):
        build_provider_profile(valid, missing)


def test_profile_refuses_mismatched_quote_or_live_host(tmp_path):
    context, _app = _context(tmp_path)
    wrong_quote = ProfileTransport("binance_spot_testnet", "BTCUSD", "USDT")
    with pytest.raises(ValueError, match="exchange-verified product metadata"):
        build_binance_spot_testnet_profile(
            _components(context, "binance_spot_testnet", "BTCUSD", "USD"),
            _resolver("binance_spot_testnet"),
            transport=wrong_quote,
        )

    live_endpoint = ProfileTransport("coinbase_exchange_sandbox", "BTC-USD", "USD")
    live_endpoint.client.base_url = "https://api.exchange.coinbase.com"
    with pytest.raises(ValueError, match="exact sandbox host boundary"):
        build_coinbase_exchange_sandbox_profile(
            _components(context, "coinbase_exchange_sandbox", "BTC-USD", "USD"),
            _resolver("coinbase_exchange_sandbox"),
            transport=live_endpoint,
        )
    assert live_endpoint.product_reads == 0
