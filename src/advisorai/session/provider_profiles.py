"""Verified testnet venue profiles for the supervised paper owner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from advisorai.agents import EvidenceCouncil
from advisorai.config import CredentialResolver, CredentialScope
from advisorai.contracts import AssetClass, InstrumentIdentity, Snapshot
from advisorai.decision import DecisionModelPort
from advisorai.execution import AccountState
from advisorai.execution.native import NativeVenueAdapter
from advisorai.integrations.binance_spot import (
    BINANCE_SPOT_TESTNET_ADAPTER_VERSION,
    BINANCE_SPOT_TESTNET_BASE_URL,
    BINANCE_SPOT_TESTNET_HOST,
    BINANCE_SPOT_TESTNET_STREAM_URL,
    BINANCE_SPOT_TESTNET_WS_API_URL,
    BinanceSpotTestnetTransport,
    build_binance_spot_testnet_transport,
)
from advisorai.integrations.coinbase_exchange import (
    COINBASE_EXCHANGE_SANDBOX_ADAPTER_VERSION,
    COINBASE_EXCHANGE_SANDBOX_BASE_URL,
    COINBASE_EXCHANGE_SANDBOX_HOST,
    COINBASE_EXCHANGE_SANDBOX_WS_URL,
    CoinbaseExchangeSandboxTransport,
    build_coinbase_exchange_sandbox_transport,
)
from advisorai.ledger import SqliteLedgers
from advisorai.session.application import (
    OwnerApplicationAdapters,
    OwnerDataAdapter,
)
from advisorai.session.outcomes import FxRateProvider, QuoteAwarePaperOutcomeAdapter, QuoteToUsdRate
from advisorai.session.provider_quality import ProviderOutcomeQualityProvider
from advisorai.session.providers import (
    BinanceSpotTestnetOwnerAdapter,
    CoinbaseExchangeSandboxOwnerAdapter,
)

PROVIDER_PROFILE_VERSIONS = {
    "binance_spot_testnet": BINANCE_SPOT_TESTNET_ADAPTER_VERSION,
    "coinbase_exchange_sandbox": COINBASE_EXCHANGE_SANDBOX_ADAPTER_VERSION,
}


@dataclass(frozen=True, slots=True)
class ProviderProfileRequest:
    provider: str
    provider_instrument_id: str
    quote_currency: str
    max_submissions: int | None = None

    def __post_init__(self) -> None:
        provider = self.provider.strip().lower()
        provider_instrument_id = self.provider_instrument_id.strip().upper()
        quote_currency = self.quote_currency.strip().upper()
        if not provider or not provider_instrument_id or not quote_currency:
            raise ValueError("provider profile needs provider, instrument, and quote identities")
        if self.max_submissions is not None and self.max_submissions < 0:
            raise ValueError("provider submission cap cannot be negative")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "provider_instrument_id", provider_instrument_id)
        object.__setattr__(self, "quote_currency", quote_currency)


@dataclass(frozen=True, slots=True)
class ProviderProfileContext:
    """Owner-owned data, model, account, and outcome boundaries for one profile."""

    data: OwnerDataAdapter
    evidence_council: EvidenceCouncil
    decision_model_factory: Callable[[Snapshot], DecisionModelPort]
    initial_account: AccountState
    ledgers: SqliteLedgers
    instrument: InstrumentIdentity
    account_quote_currency: str
    fx_rate_provider: FxRateProvider | None = None

    def __post_init__(self) -> None:
        quote = self.account_quote_currency.strip().upper()
        if not quote:
            raise ValueError("provider profile baseline requires a quote currency")
        object.__setattr__(self, "account_quote_currency", quote)
        data_quote = str(getattr(self.data, "quote_currency", "")).strip().upper()
        if not data_quote:
            raise ValueError("provider profiles require an explicitly quoted data adapter")
        if data_quote != quote:
            raise ValueError("provider data and initial account quote currencies differ")
        if not callable(self.decision_model_factory):
            raise ValueError("provider profile requires a decision model factory")
        if self.fx_rate_provider is not None and not callable(self.fx_rate_provider):
            raise ValueError("provider FX rate provider must be callable")


@dataclass(frozen=True, slots=True)
class ProviderProfileComponents:
    context: ProviderProfileContext
    request: ProviderProfileRequest


def _validate_context(components: ProviderProfileComponents, provider: str) -> None:
    request = components.request
    context = components.context
    if request.provider != provider:
        raise ValueError(f"provider profile request must select {provider}")
    if context.instrument.venue != "paper":
        raise ValueError("provider instrument must use the supervised paper venue alias")
    if context.instrument.asset_class is not AssetClass.CRYPTO:
        raise ValueError("provider profiles currently support crypto spot instruments only")
    if not context.instrument.base_asset or not context.instrument.quote_asset:
        raise ValueError("provider profile instrument requires explicit base and quote assets")
    if context.instrument.canonical_id not in context.initial_account.marks:
        raise ValueError("provider account baseline must mark the configured instrument")
    if context.account_quote_currency != request.quote_currency:
        raise ValueError("provider quote differs from the durable account baseline currency")
    if context.instrument.quote_asset.strip().upper() != request.quote_currency:
        raise ValueError("provider quote differs from the owner instrument identity")
    data_quote = str(getattr(context.data, "quote_currency", "")).strip().upper()
    if data_quote != request.quote_currency:
        raise ValueError("provider quote differs from the data adapter currency")
    if request.quote_currency != "USD" and context.fx_rate_provider is None:
        raise ValueError("non-USD provider profiles require a snapshot-bound FX rate provider")


def _validate_credentials(resolver: CredentialResolver, provider: str) -> None:
    scoped = resolver.resolve(CredentialScope.PAPER_VENUE)
    expected_url = (
        BINANCE_SPOT_TESTNET_BASE_URL
        if provider == "binance_spot_testnet"
        else COINBASE_EXCHANGE_SANDBOX_BASE_URL
    )
    required = ["ADVISORAI_VENUE_API_KEY", "ADVISORAI_VENUE_API_SECRET"]
    if provider == "coinbase_exchange_sandbox":
        required.append("ADVISORAI_VENUE_PASSPHRASE")
    if any(not scoped.get(name, "").strip() for name in required):
        raise ValueError(f"{provider} profile requires its scoped PAPER_VENUE credentials")
    if scoped.get("ADVISORAI_VENUE_NAME", "").strip() != provider:
        raise ValueError("PAPER_VENUE credential identity does not match the selected profile")
    if scoped.get("ADVISORAI_VENUE_ENVIRONMENT", "").strip().lower() not in {
        "testnet",
        "paper",
        "paper_testnet",
    }:
        raise ValueError("provider credentials must target a paper/testnet environment")
    if scoped.get("ADVISORAI_VENUE_BASE_URL", "").strip().rstrip("/") != expected_url:
        raise ValueError("PAPER_VENUE credentials contain an unadmitted provider endpoint")
    ws_url = scoped.get("ADVISORAI_VENUE_WS_URL", "").strip()
    admitted_ws_urls = (
        {BINANCE_SPOT_TESTNET_WS_API_URL, BINANCE_SPOT_TESTNET_STREAM_URL}
        if provider == "binance_spot_testnet"
        else {COINBASE_EXCHANGE_SANDBOX_WS_URL}
    )
    if ws_url and ws_url not in admitted_ws_urls:
        raise ValueError("PAPER_VENUE credentials contain an unadmitted WebSocket endpoint")


def _verify_selected_instrument(transport: object, components: ProviderProfileComponents) -> None:
    request = components.request
    if getattr(transport, "venue_name", None) != request.provider:
        raise ValueError("provider transport identity differs from the selected profile")
    if getattr(transport, "environment", None) != "paper_testnet":
        raise ValueError("provider profile accepts sandbox transports only")
    client = getattr(transport, "client", None)
    client_config = getattr(client, "config", None)
    expected_url, expected_host = (
        (BINANCE_SPOT_TESTNET_BASE_URL, BINANCE_SPOT_TESTNET_HOST)
        if request.provider == "binance_spot_testnet"
        else (COINBASE_EXCHANGE_SANDBOX_BASE_URL, COINBASE_EXCHANGE_SANDBOX_HOST)
    )
    if getattr(client, "base_url", None) != expected_url or tuple(
        getattr(client_config, "allowed_hosts", ())
    ) != (expected_host,):
        raise ValueError("provider profile requires the exact sandbox host boundary")
    products = transport.list_products()  # type: ignore[attr-defined]
    if request.provider == "binance_spot_testnet":
        transport.verify_symbol_mappings(  # type: ignore[attr-defined]
            products, required=(request.provider_instrument_id,)
        )
        admitted = tuple(transport.verified_symbol_ids)  # type: ignore[attr-defined]
        spec = transport.verified_symbol_spec(request.provider_instrument_id)  # type: ignore[attr-defined]
        base = str(spec.base_asset).strip().upper()
        quote = str(spec.quote_asset).strip().upper()
    else:
        transport.verify_product_mappings(  # type: ignore[attr-defined]
            products, required=(request.provider_instrument_id,)
        )
        admitted = tuple(transport.verified_product_ids)  # type: ignore[attr-defined]
        spec = transport.verified_product_spec(request.provider_instrument_id)  # type: ignore[attr-defined]
        base = str(spec.base_currency).strip().upper()
        quote = str(spec.quote_currency).strip().upper()
    if admitted != (request.provider_instrument_id,):
        raise ValueError("provider profile must admit exactly the requested instrument")
    if quote != request.quote_currency:
        raise ValueError("provider quote differs from exchange-verified product metadata")
    if base != components.context.instrument.base_asset.strip().upper():
        raise ValueError("provider base asset differs from the canonical owner instrument")


def _compose(
    components: ProviderProfileComponents,
    *,
    transport: BinanceSpotTestnetTransport | CoinbaseExchangeSandboxTransport,
) -> OwnerApplicationAdapters:
    _verify_selected_instrument(transport, components)
    context = components.context
    request = components.request
    instrument = context.instrument.model_copy(
        update={
            "venue": "paper",
            "venue_symbol": request.provider_instrument_id,
            "quote_asset": request.quote_currency,
        }
    )
    native = NativeVenueAdapter(
        venue=request.provider,
        environment="paper_testnet",
        transport=transport,
        max_submissions=request.max_submissions,
    )
    if request.provider == "binance_spot_testnet":
        venue = BinanceSpotTestnetOwnerAdapter(
            native,
            context.ledgers,
            canonical_instrument_id=instrument.canonical_id,
            symbol=request.provider_instrument_id,
            quote_asset=request.quote_currency,
        )
    else:
        venue = CoinbaseExchangeSandboxOwnerAdapter(
            native,
            context.ledgers,
            canonical_instrument_id=instrument.canonical_id,
            product_id=request.provider_instrument_id,
            quote_currency=request.quote_currency,
        )
    fx_rate_provider = context.fx_rate_provider or _identity_usd_rate
    outcome_adapter = QuoteAwarePaperOutcomeAdapter(
        quote_currency=request.quote_currency,
        fx_rate_provider=fx_rate_provider,
        quality_provider=ProviderOutcomeQualityProvider(
            provider_identity=request.provider,
            client=transport.client,
        ),
    )
    return OwnerApplicationAdapters(
        data=context.data,
        evidence_council=context.evidence_council,
        decision_model_factory=context.decision_model_factory,
        paper_venue=venue,
        outcome_adapter=outcome_adapter,
        initial_account=context.initial_account,
        instrument=instrument,
    )


def _identity_usd_rate(snapshot: Snapshot) -> QuoteToUsdRate:
    return QuoteToUsdRate(
        quote_currency="USD",
        usd_per_quote=Decimal("1"),
        as_of=snapshot.as_of,
        snapshot_id=snapshot.artifact_id,
        evidence_id=uuid5(NAMESPACE_URL, f"advisorai-v3/usd-identity-fx/{snapshot.artifact_id}"),
        source="currency-identity/USD",
    )


def build_binance_spot_testnet_profile(
    components: ProviderProfileComponents,
    resolver: CredentialResolver,
    *,
    transport: BinanceSpotTestnetTransport | None = None,
) -> OwnerApplicationAdapters:
    """Verify and compose exactly one Binance Spot Testnet symbol."""
    _validate_context(components, "binance_spot_testnet")
    _validate_credentials(resolver, "binance_spot_testnet")
    venue_transport = transport or build_binance_spot_testnet_transport(resolver)
    return _compose(components, transport=venue_transport)


def build_coinbase_exchange_sandbox_profile(
    components: ProviderProfileComponents,
    resolver: CredentialResolver,
    *,
    transport: CoinbaseExchangeSandboxTransport | None = None,
) -> OwnerApplicationAdapters:
    """Verify and compose exactly one Coinbase Exchange Sandbox product."""
    _validate_context(components, "coinbase_exchange_sandbox")
    _validate_credentials(resolver, "coinbase_exchange_sandbox")
    if components.request.quote_currency != "USD":
        raise ValueError("Coinbase owner profile currently admits USD quote products only")
    venue_transport = transport or build_coinbase_exchange_sandbox_transport(resolver)
    return _compose(components, transport=venue_transport)


def build_provider_profile(
    components: ProviderProfileComponents,
    resolver: CredentialResolver,
    **kwargs: object,
) -> OwnerApplicationAdapters:
    """Dispatch only reviewed testnet profiles using the scoped venue credential."""
    provider = components.request.provider
    if provider not in PROVIDER_PROFILE_VERSIONS:
        raise ValueError(f"unknown provider profile {provider!r}")
    if provider == "binance_spot_testnet":
        return build_binance_spot_testnet_profile(components, resolver, **kwargs)  # type: ignore[arg-type]
    return build_coinbase_exchange_sandbox_profile(components, resolver, **kwargs)  # type: ignore[arg-type]


__all__ = [
    "PROVIDER_PROFILE_VERSIONS",
    "ProviderProfileComponents",
    "ProviderProfileContext",
    "ProviderProfileRequest",
    "build_binance_spot_testnet_profile",
    "build_coinbase_exchange_sandbox_profile",
    "build_provider_profile",
]
