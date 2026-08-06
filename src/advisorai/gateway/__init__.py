"""Replaceable model gateway adapters with explicit route/fallback records."""

from advisorai.ports import (
    DataClassification,
    DecisionImpact,
    GatewayDataClass,
    GatewayOutputKind,
    GatewayTier,
    RouteTier,
)

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
from .policy import (
    GatewayDecision,
    GatewayPolicyConfig,
    GatewayPolicyError,
    ModelGateway,
    PolicyGateway,
    ProviderRoutePolicy,
    ProviderTerms,
    RouteProfile,
    ThreeTierModelGateway,
    classify_payload,
    contains_secret_material,
    redact_request,
    redact_text,
)

__all__ = [
    "DirectProviderGatewayAdapter",
    "GatewayAttempt",
    "GatewayCallRecord",
    "GatewayChain",
    "GatewayFailure",
    "GatewayRecorder",
    "GatewayDecision",
    "GatewayDataClass",
    "GatewayOutputKind",
    "GatewayPolicyConfig",
    "GatewayPolicyError",
    "GatewayTier",
    "DataClassification",
    "DecisionImpact",
    "RouteTier",
    "LiteLLMGatewayAdapter",
    "OmniRouteGatewayAdapter",
    "LocalDeterministicGateway",
    "ModelGateway",
    "PolicyGateway",
    "ProviderRoutePolicy",
    "ProviderTerms",
    "RouteProfile",
    "ThreeTierModelGateway",
    "TypedGatewayAdapter",
    "classify_payload",
    "contains_secret_material",
    "redact_request",
    "redact_text",
]
