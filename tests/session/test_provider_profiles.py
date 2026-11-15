"""Provider profiles bind verified sandbox connectors under the paper owner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import NAMESPACE_URL, uuid5

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
from advisorai.integrations.http import HttpClientConfig, SafeHttpClient
from advisorai.session.application import (
    OwnerApplicationAdapters,
)
from advisorai.session.outcomes import QuoteAwarePaperOutcomeAdapter, QuoteToUsdRate
from advisorai.session.provider_profiles import (
    ProviderProfileComponents,
    ProviderProfileContext,
    ProviderProfileRequest,
    build_binance_spot_testnet_profile,
    build_coinbase_exchange_sandbox_profile,
    build_provider_profile,
)
from advisorai.session.provider_quality import ProviderOutcomeQualityProvider


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
        self.client = SafeHttpClient(
            HttpClientConfig(allowed_hosts=(self.host,), requests_per_second=100),
            base_url=self.base_url,
            requester=lambda *_args: (200, b"{}", ()),
            sleeper=lambda _seconds: None,
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
        fx_rate_provider=_identity_usd_rate,
    )
    return context, app


def _identity_usd_rate(snapshot):  # noqa: ANN001
    return QuoteToUsdRate(
        quote_currency="USD",
        usd_per_quote=Decimal("1"),
        as_of=snapshot.as_of,
        snapshot_id=snapshot.artifact_id,
        evidence_id=uuid5(NAMESPACE_URL, f"profile-usd-fx/{snapshot.artifact_id}"),
        source="currency-identity/USD",
    )


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
    assert isinstance(profile.outcome_adapter, QuoteAwarePaperOutcomeAdapter)
    assert isinstance(profile.outcome_adapter.quality_provider, ProviderOutcomeQualityProvider)
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


def test_non_usd_profile_requires_explicit_snapshot_bound_fx(tmp_path):
    context, _app = _context(tmp_path)
    context.data.quote_currency = "USDT"
    instrument = context.instrument.model_copy(update={"quote_asset": "USDT"})
    request = _components(context, "binance_spot_testnet", "BTCUSDT", "USDT").request
    transport = ProfileTransport("binance_spot_testnet", "BTCUSDT", "USDT")
    missing_fx_context = ProviderProfileContext(
        data=context.data,
        evidence_council=context.evidence_council,
        decision_model_factory=context.decision_model_factory,
        initial_account=context.initial_account,
        ledgers=context.ledgers,
        instrument=instrument,
        account_quote_currency="USDT",
    )
    with pytest.raises(ValueError, match="snapshot-bound FX"):
        build_binance_spot_testnet_profile(
            ProviderProfileComponents(context=missing_fx_context, request=request),
            _resolver("binance_spot_testnet"),
            transport=transport,
        )

    def explicit_fx(snapshot):  # noqa: ANN001
        return QuoteToUsdRate(
            quote_currency="USDT",
            usd_per_quote=Decimal("0.999"),
            as_of=snapshot.as_of,
            snapshot_id=snapshot.artifact_id,
            evidence_id=uuid5(NAMESPACE_URL, f"test-fx/{snapshot.artifact_id}"),
            source="verified-usdt-fx-fixture",
        )

    fx_context = ProviderProfileContext(
        data=context.data,
        evidence_council=context.evidence_council,
        decision_model_factory=context.decision_model_factory,
        initial_account=context.initial_account,
        ledgers=context.ledgers,
        instrument=instrument,
        account_quote_currency="USDT",
        fx_rate_provider=explicit_fx,
    )
    profile = build_binance_spot_testnet_profile(
        ProviderProfileComponents(context=fx_context, request=request),
        _resolver("binance_spot_testnet"),
        transport=transport,
    )
    assert isinstance(profile.outcome_adapter, QuoteAwarePaperOutcomeAdapter)
    assert profile.outcome_adapter.quote_currency == "USDT"


def test_profile_request_refuses_blank_identities_and_negative_cap() -> None:
    """P02/P09 prep: profile requests validate at construction.

    Blank provider/instrument/quote identities and negative
    submission caps are refused before any context, credential, or
    transport work. Pure unit coverage; no network, no credentials.
    """
    with pytest.raises(ValueError, match="needs provider, instrument, and quote"):
        ProviderProfileRequest(
            provider="  ", provider_instrument_id="BTCUSDT", quote_currency="USD"
        )
    with pytest.raises(ValueError, match="needs provider, instrument, and quote"):
        ProviderProfileRequest(
            provider="binance_spot_testnet",
            provider_instrument_id="",
            quote_currency="USD",
        )
    with pytest.raises(ValueError, match="submission cap cannot be negative"):
        ProviderProfileRequest(
            provider="binance_spot_testnet",
            provider_instrument_id="BTCUSDT",
            quote_currency="USD",
            max_submissions=-1,
        )


def test_profile_context_refuses_misshapen_owner_boundaries(tmp_path) -> None:
    """P02/P09 prep: profile contexts validate owner boundaries.

    A blank baseline quote, an unquoted data adapter, a data/baseline
    quote split, an uncallable decision factory, and an uncallable FX
    provider are all refused at construction. Uses the quiet owner
    harness; no network, no credentials.
    """
    context, _app = _context(tmp_path)

    def rebuild(**overrides):
        kwargs = {
            "data": context.data,
            "evidence_council": context.evidence_council,
            "decision_model_factory": context.decision_model_factory,
            "initial_account": context.initial_account,
            "ledgers": context.ledgers,
            "instrument": context.instrument,
            "account_quote_currency": context.account_quote_currency,
            "fx_rate_provider": context.fx_rate_provider,
        }
        kwargs.update(overrides)
        return ProviderProfileContext(**kwargs)

    with pytest.raises(ValueError, match="baseline requires a quote currency"):
        rebuild(account_quote_currency="  ")
    unquoted_data = SimpleNamespace(quote_currency="  ")
    with pytest.raises(ValueError, match="explicitly quoted data adapter"):
        rebuild(data=unquoted_data)
    split_data = SimpleNamespace(quote_currency="EUR")
    with pytest.raises(ValueError, match="quote currencies differ"):
        rebuild(data=split_data)
    with pytest.raises(ValueError, match="requires a decision model factory"):
        rebuild(decision_model_factory=None)
    with pytest.raises(ValueError, match="FX rate provider must be callable"):
        rebuild(fx_rate_provider="not-callable")


def test_profile_builder_refuses_mismatched_context(tmp_path) -> None:
    """P02/P09 prep: builders refuse contexts that drift from the request.

    A request selecting another provider, a non-paper instrument, a
    non-crypto asset class, an unmarked baseline, and quotes that
    disagree with the baseline/instrument/data are all refused before
    any credential or transport use.
    """
    context, _app = _context(tmp_path)

    def build(request_context, request=None, **request_overrides):
        req = request or _components(
            request_context, "binance_spot_testnet", "BTCUSD", "USD"
        ).request
        if request_overrides:
            req = ProviderProfileRequest(
                provider=req.provider,
                provider_instrument_id=req.provider_instrument_id,
                quote_currency=req.quote_currency,
                max_submissions=req.max_submissions,
                **request_overrides,
            )
        return build_binance_spot_testnet_profile(
            ProviderProfileComponents(context=request_context, request=req),
            _resolver("binance_spot_testnet"),
            transport=ProfileTransport("binance_spot_testnet", "BTCUSD", "USD"),
        )

    foreign_request = _components(
        context, "coinbase_exchange_sandbox", "BTC-USD", "USD"
    ).request
    with pytest.raises(ValueError, match="must select binance_spot_testnet"):
        build(context, request=foreign_request)

    live_instrument = context.instrument.model_copy(update={"venue": "live"})
    live_context = ProviderProfileContext(
        data=context.data,
        evidence_council=context.evidence_council,
        decision_model_factory=context.decision_model_factory,
        initial_account=context.initial_account,
        ledgers=context.ledgers,
        instrument=live_instrument,
        account_quote_currency=context.account_quote_currency,
        fx_rate_provider=context.fx_rate_provider,
    )
    with pytest.raises(ValueError, match="supervised paper venue alias"):
        build(live_context)

    equity_instrument = context.instrument.model_copy(
        update={"asset_class": "equity"}
    )
    equity_context = ProviderProfileContext(
        data=context.data,
        evidence_council=context.evidence_council,
        decision_model_factory=context.decision_model_factory,
        initial_account=context.initial_account,
        ledgers=context.ledgers,
        instrument=equity_instrument,
        account_quote_currency=context.account_quote_currency,
        fx_rate_provider=context.fx_rate_provider,
    )
    with pytest.raises(ValueError, match="crypto spot instruments only"):
        build(equity_context)

    unmarked_context = ProviderProfileContext(
        data=context.data,
        evidence_council=context.evidence_council,
        decision_model_factory=context.decision_model_factory,
        initial_account=context.initial_account,
        ledgers=context.ledgers,
        instrument=context.instrument.model_copy(update={"canonical_id": "ETH"}),
        account_quote_currency=context.account_quote_currency,
        fx_rate_provider=context.fx_rate_provider,
    )
    with pytest.raises(ValueError, match="must mark the configured instrument"):
        build(unmarked_context)

    eur_context = ProviderProfileContext(
        data=SimpleNamespace(quote_currency="EUR"),
        evidence_council=context.evidence_council,
        decision_model_factory=context.decision_model_factory,
        initial_account=context.initial_account,
        ledgers=context.ledgers,
        instrument=context.instrument,
        account_quote_currency="EUR",
        fx_rate_provider=context.fx_rate_provider,
    )
    with pytest.raises(
        ValueError, match="differs from the durable account baseline"
    ):
        build(eur_context)

    jpy_instrument = context.instrument.model_copy(update={"quote_asset": "JPY"})
    jpy_context = ProviderProfileContext(
        data=context.data,
        evidence_council=context.evidence_council,
        decision_model_factory=context.decision_model_factory,
        initial_account=context.initial_account,
        ledgers=context.ledgers,
        instrument=jpy_instrument,
        account_quote_currency=context.account_quote_currency,
        fx_rate_provider=context.fx_rate_provider,
    )
    with pytest.raises(ValueError, match="differs from the owner instrument"):
        build(jpy_context)


def test_profile_builder_refuses_misscoped_credentials(tmp_path) -> None:
    """P02/P09 prep: builders refuse credentials outside the profile scope.

    Missing venue secrets, a credential identity naming another
    profile, a live environment, an unadmitted base URL, and an
    unadmitted WebSocket URL are all refused before any transport
    or product read.
    """
    context, _app = _context(tmp_path)

    def resolver_with(**overrides):
        values = {
            "ADVISORAI_VENUE_API_KEY": "profile-test-key",
            "ADVISORAI_VENUE_API_SECRET": "profile-test-secret",
            "ADVISORAI_VENUE_NAME": "binance_spot_testnet",
            "ADVISORAI_VENUE_ENVIRONMENT": "testnet",
            "ADVISORAI_VENUE_BASE_URL": BINANCE_SPOT_TESTNET_BASE_URL,
        }
        values.update(overrides)
        pruned = {key: value for key, value in values.items() if value is not None}
        return CredentialResolver.from_mapping(pruned)

    def build(resolver):
        transport = ProfileTransport("binance_spot_testnet", "BTCUSD", "USD")
        profile = None
        try:
            profile = build_binance_spot_testnet_profile(
                _components(context, "binance_spot_testnet", "BTCUSD", "USD"),
                resolver,
                transport=transport,
            )
        finally:
            assert transport.product_reads == 0
        return profile

    with pytest.raises(ValueError, match="scoped PAPER_VENUE credentials"):
        build(resolver_with(ADVISORAI_VENUE_API_SECRET=None))
    with pytest.raises(ValueError, match="identity does not match"):
        build(resolver_with(ADVISORAI_VENUE_NAME="coinbase_exchange_sandbox"))
    with pytest.raises(ValueError, match="paper/testnet environment"):
        build(resolver_with(ADVISORAI_VENUE_ENVIRONMENT="live"))
    with pytest.raises(ValueError, match="unadmitted provider endpoint"):
        build(resolver_with(ADVISORAI_VENUE_BASE_URL="https://api.binance.com"))
    with pytest.raises(ValueError, match="unadmitted WebSocket endpoint"):
        build(resolver_with(ADVISORAI_VENUE_WS_URL="wss://stream.binance.com:9443"))


def test_verifier_refuses_transport_identity_or_environment_drift(tmp_path) -> None:
    """P02/P09 prep: the verifier refuses transports outside the profile.

    A transport naming another venue or running outside paper/testnet
    is refused before any product read. Uses the profile double with
    mutated identity fields; no network, no credentials.
    """
    context, _app = _context(tmp_path)

    foreign = ProfileTransport("binance_spot_testnet", "BTCUSD", "USD")
    foreign.venue_name = "coinbase_exchange_sandbox"
    with pytest.raises(ValueError, match="transport identity differs"):
        build_binance_spot_testnet_profile(
            _components(context, "binance_spot_testnet", "BTCUSD", "USD"),
            _resolver("binance_spot_testnet"),
            transport=foreign,
        )
    assert foreign.product_reads == 0

    live = ProfileTransport("binance_spot_testnet", "BTCUSD", "USD")
    live.environment = "live"
    with pytest.raises(ValueError, match="accepts sandbox transports only"):
        build_binance_spot_testnet_profile(
            _components(context, "binance_spot_testnet", "BTCUSD", "USD"),
            _resolver("binance_spot_testnet"),
            transport=live,
        )
    assert live.product_reads == 0


def test_verifier_refuses_admission_and_metadata_drift(tmp_path) -> None:
    """P02/P09 prep: the verifier pins admission and metadata exactly.

    A transport admitting extra symbols, exchange metadata quoting a
    different currency (already pinned for Binance; here the Coinbase
    leg), and base-asset metadata disagreeing with the canonical
    owner instrument are all refused. No network, no credentials.
    """
    context, _app = _context(tmp_path)

    class _GreedyTransport(ProfileTransport):
        def verify_symbol_mappings(self, _products, *, required):
            super().verify_symbol_mappings(_products, required=required)
            self.verified_symbol_ids = ("BTCUSD", "ETHUSD")

    greedy = _GreedyTransport("binance_spot_testnet", "BTCUSD", "USD")
    with pytest.raises(ValueError, match="admit exactly the requested"):
        build_binance_spot_testnet_profile(
            _components(context, "binance_spot_testnet", "BTCUSD", "USD"),
            _resolver("binance_spot_testnet"),
            transport=greedy,
        )

    eth_basis = ProfileTransport("coinbase_exchange_sandbox", "BTC-USD", "USD")
    eth_basis.verified_product_spec = lambda product_id: SimpleNamespace(  # type: ignore[method-assign]
        base_currency="ETH", quote_currency="USD"
    )
    with pytest.raises(ValueError, match="differs from the canonical owner"):
        build_coinbase_exchange_sandbox_profile(
            _components(context, "coinbase_exchange_sandbox", "BTC-USD", "USD"),
            _resolver("coinbase_exchange_sandbox"),
            transport=eth_basis,
        )


def test_builder_refuses_coinbase_non_usd_and_bare_instruments(tmp_path) -> None:
    """P02/P09 prep: Coinbase USD-only and instrument-identity gates.

    A USDT Coinbase request is refused even with a consistent USDT
    context; instruments without base/quote assets and data adapters
    whose quote drifts after construction are refused before any
    credential or transport use.
    """
    context, _app = _context(tmp_path)

    context.data.quote_currency = "USDT"
    usdt_instrument = context.instrument.model_copy(update={"quote_asset": "USDT"})
    usdt_context = ProviderProfileContext(
        data=context.data,
        evidence_council=context.evidence_council,
        decision_model_factory=context.decision_model_factory,
        initial_account=context.initial_account,
        ledgers=context.ledgers,
        instrument=usdt_instrument,
        account_quote_currency="USDT",
        fx_rate_provider=context.fx_rate_provider,
    )
    with pytest.raises(ValueError, match="admits USD quote products only"):
        build_coinbase_exchange_sandbox_profile(
            ProviderProfileComponents(
                context=usdt_context,
                request=ProviderProfileRequest(
                    provider="coinbase_exchange_sandbox",
                    provider_instrument_id="BTC-USD",
                    quote_currency="USDT",
                ),
            ),
            _resolver("coinbase_exchange_sandbox"),
            transport=ProfileTransport("coinbase_exchange_sandbox", "BTC-USD", "USDT"),
        )
    context.data.quote_currency = "USD"

    bare_instrument = context.instrument.model_copy(update={"base_asset": ""})
    bare_context = ProviderProfileContext(
        data=context.data,
        evidence_council=context.evidence_council,
        decision_model_factory=context.decision_model_factory,
        initial_account=context.initial_account,
        ledgers=context.ledgers,
        instrument=bare_instrument,
        account_quote_currency=context.account_quote_currency,
        fx_rate_provider=context.fx_rate_provider,
    )
    with pytest.raises(
        ValueError, match="requires explicit base and quote assets"
    ):
        build_binance_spot_testnet_profile(
            _components(bare_context, "binance_spot_testnet", "BTCUSD", "USD"),
            _resolver("binance_spot_testnet"),
            transport=ProfileTransport("binance_spot_testnet", "BTCUSD", "USD"),
        )

    context.data.quote_currency = "EUR"
    try:
        with pytest.raises(ValueError, match="differs from the data adapter"):
            build_binance_spot_testnet_profile(
                _components(context, "binance_spot_testnet", "BTCUSD", "USD"),
                _resolver("binance_spot_testnet"),
                transport=ProfileTransport("binance_spot_testnet", "BTCUSD", "USD"),
            )
    finally:
        context.data.quote_currency = "USD"
