"""Concrete, paper-safe external API adapters.

The package contains transport implementations only.  Decision, risk, order,
ledger, and promotion authority remains in the existing AdvisorAI services.
"""

from .coinbase_exchange import (
    COINBASE_EXCHANGE_PRODUCTION_HOST,
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
from .http import HttpClientConfig, HttpTransportError, SafeHttpClient, SourceHttpTransport
from .llm import GatewayTransportError, OpenAICompatibleGatewayAdapter
from .sources import RawHttpSpool, SourceEndpoint, V3CoreCollectors, build_v3_core_collectors
from .venue import HmacVenueSigner, PaperTestnetVenueTransport, VenueTransportError
from .websocket import RawMessageSpool, RawWebSocketFeed, WebSocketTransportError

__all__ = [
    "GatewayTransportError",
    "ConnectorCard",
    "ConnectorRegistry",
    "ConnectorState",
    "COINBASE_EXCHANGE_PRODUCTION_HOST",
    "COINBASE_EXCHANGE_SANDBOX_ADAPTER_VERSION",
    "COINBASE_EXCHANGE_SANDBOX_BASE_URL",
    "COINBASE_EXCHANGE_SANDBOX_HOST",
    "COINBASE_EXCHANGE_SANDBOX_WS_HOST",
    "COINBASE_EXCHANGE_SANDBOX_WS_URL",
    "CoinbaseExchangeSandboxTransport",
    "CoinbaseExchangeSigner",
    "CoinbaseProductSpec",
    "HmacVenueSigner",
    "HttpClientConfig",
    "HttpTransportError",
    "OpenAICompatibleGatewayAdapter",
    "PaperTestnetVenueTransport",
    "RawMessageSpool",
    "RawHttpSpool",
    "RawWebSocketFeed",
    "SafeHttpClient",
    "SourceEndpoint",
    "SourceHttpTransport",
    "VenueTransportError",
    "V3CoreCollectors",
    "WebSocketTransportError",
    "build_direct_gateway",
    "build_coinbase_exchange_sandbox_transport",
    "build_paper_venue_transport",
    "build_policy_gateway",
    "build_v3_core_collectors",
]
