"""Concrete, paper-safe external API adapters.

The package contains transport implementations only.  Decision, risk, order,
ledger, and promotion authority remains in the existing AdvisorAI services.
"""

from .binance_spot import (
    BINANCE_SPOT_TESTNET_ADAPTER_VERSION,
    BINANCE_SPOT_TESTNET_BASE_URL,
    BINANCE_SPOT_TESTNET_HOST,
    BINANCE_SPOT_TESTNET_STREAM_HOST,
    BINANCE_SPOT_TESTNET_STREAM_URL,
    BINANCE_SPOT_TESTNET_WS_API_URL,
    BinanceSpotSigner,
    BinanceSpotSymbolSpec,
    BinanceSpotTestnetTransport,
    build_binance_spot_testnet_transport,
)
from .coinbase_exchange import (
    COINBASE_EXCHANGE_PRODUCTION_HOST,
    COINBASE_EXCHANGE_PRODUCTION_WS_HOST,
    COINBASE_EXCHANGE_SANDBOX_ADAPTER_VERSION,
    COINBASE_EXCHANGE_SANDBOX_BASE_URL,
    COINBASE_EXCHANGE_SANDBOX_HOST,
    COINBASE_EXCHANGE_SANDBOX_WS_HOST,
    COINBASE_EXCHANGE_SANDBOX_WS_URL,
    CoinbaseExchangeSandboxTransport,
    CoinbaseExchangeSigner,
    CoinbaseProductSpec,
    build_coinbase_exchange_sandbox_transport,
)
from .config import ConnectorCard, ConnectorRegistry, ConnectorState
from .factories import build_direct_gateway, build_paper_venue_transport, build_policy_gateway
from .http import (
    HttpClientConfig,
    HttpRequestMetrics,
    HttpTransportError,
    SafeHttpClient,
    SourceHttpTransport,
)
from .llm import GatewayTransportError, OpenAICompatibleGatewayAdapter
from .public_data import (
    BeaDataClient,
    BlsV2Client,
    EiaApiClient,
    ProviderRequestError,
    SmokeTestOutcome,
)
from .research_metadata import CitationSearchResult, CrossrefWorksClient, OpenAlexWorksClient
from .sources import RawHttpSpool, SourceEndpoint, V3CoreCollectors, build_v3_core_collectors
from .venue import HmacVenueSigner, PaperTestnetVenueTransport, VenueTransportError
from .websocket import RawMessageSpool, RawWebSocketFeed, WebSocketTransportError

__all__ = [
    "GatewayTransportError",
    "BINANCE_SPOT_TESTNET_ADAPTER_VERSION",
    "BeaDataClient",
    "BlsV2Client",
    "CitationSearchResult",
    "BINANCE_SPOT_TESTNET_BASE_URL",
    "BINANCE_SPOT_TESTNET_HOST",
    "BINANCE_SPOT_TESTNET_STREAM_HOST",
    "BINANCE_SPOT_TESTNET_STREAM_URL",
    "BINANCE_SPOT_TESTNET_WS_API_URL",
    "BinanceSpotSigner",
    "BinanceSpotSymbolSpec",
    "BinanceSpotTestnetTransport",
    "ConnectorCard",
    "ConnectorRegistry",
    "ConnectorState",
    "COINBASE_EXCHANGE_PRODUCTION_HOST",
    "COINBASE_EXCHANGE_PRODUCTION_WS_HOST",
    "COINBASE_EXCHANGE_SANDBOX_ADAPTER_VERSION",
    "COINBASE_EXCHANGE_SANDBOX_BASE_URL",
    "COINBASE_EXCHANGE_SANDBOX_HOST",
    "COINBASE_EXCHANGE_SANDBOX_WS_HOST",
    "COINBASE_EXCHANGE_SANDBOX_WS_URL",
    "CoinbaseExchangeSandboxTransport",
    "CoinbaseExchangeSigner",
    "CoinbaseProductSpec",
    "CrossrefWorksClient",
    "EiaApiClient",
    "HmacVenueSigner",
    "HttpClientConfig",
    "HttpRequestMetrics",
    "HttpTransportError",
    "OpenAICompatibleGatewayAdapter",
    "PaperTestnetVenueTransport",
    "OpenAlexWorksClient",
    "ProviderRequestError",
    "RawMessageSpool",
    "RawHttpSpool",
    "RawWebSocketFeed",
    "SafeHttpClient",
    "SourceEndpoint",
    "SourceHttpTransport",
    "SmokeTestOutcome",
    "VenueTransportError",
    "V3CoreCollectors",
    "WebSocketTransportError",
    "build_direct_gateway",
    "build_binance_spot_testnet_transport",
    "build_coinbase_exchange_sandbox_transport",
    "build_paper_venue_transport",
    "build_policy_gateway",
    "build_v3_core_collectors",
]
