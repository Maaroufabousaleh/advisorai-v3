"""Replaceable model gateway adapters with explicit route/fallback records."""

from .adapters import (
    DirectProviderGatewayAdapter,
    LiteLLMGatewayAdapter,
    OmniRouteGatewayAdapter,
    TypedGatewayAdapter,
)
from .core import (
    GatewayAttempt,
    GatewayCallRecord,
    GatewayChain,
    GatewayFailure,
    GatewayRecorder,
    LocalDeterministicGateway,
)

__all__ = [
    "DirectProviderGatewayAdapter",
    "GatewayAttempt",
    "GatewayCallRecord",
    "GatewayChain",
    "GatewayFailure",
    "GatewayRecorder",
    "LiteLLMGatewayAdapter",
    "OmniRouteGatewayAdapter",
    "LocalDeterministicGateway",
    "TypedGatewayAdapter",
]
