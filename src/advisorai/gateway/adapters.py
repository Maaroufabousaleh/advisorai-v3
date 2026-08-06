"""Provider-shaped gateway adapters with injected transports for safe testing."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from time import perf_counter_ns

from advisorai.ports import GatewayRequest, GatewayResponse, GatewayRoute


@dataclass(frozen=True, slots=True)
class TypedGatewayAdapter:
    name: str
    route: GatewayRoute
    transport: Callable[[GatewayRequest], Mapping[str, object]]
    privacy_class: str = "non_secret"

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        if request.route != self.route:
            raise ValueError(
                f"{self.name} is pinned to {self.route.provider}/{self.route.model}/{self.route.gateway}"
            )
        if request.privacy_class.lower() in {"secret", "credential"}:
            raise PermissionError(f"{self.name} refuses secret or credential-class requests")
        if request.privacy_class != self.privacy_class:
            raise PermissionError(f"{self.name} refuses privacy class {request.privacy_class}")
        started = perf_counter_ns()
        payload = self.transport(request)
        if not isinstance(payload, Mapping):
            raise TypeError(f"{self.name} transport must return a mapping payload")
        elapsed = max(0, (perf_counter_ns() - started) // 1_000_000)
        typed_payload = payload.get("typed_payload")
        if typed_payload is not None and not isinstance(typed_payload, Mapping):
            raise TypeError(f"{self.name} typed_payload must be a mapping")
        raw_tool_calls = payload.get("tool_calls", ())
        if raw_tool_calls is None:
            raw_tool_calls = ()
        if isinstance(raw_tool_calls, (str, bytes)) or not isinstance(raw_tool_calls, Sequence):
            raise TypeError(f"{self.name} tool_calls must be a sequence of mappings")
        content = payload.get("content")
        if not isinstance(content, str) or (not content.strip() and not raw_tool_calls):
            raise ValueError(f"{self.name} transport response content must be non-blank text")
        token_values: dict[str, int] = {}
        for field in ("input_tokens", "output_tokens"):
            value = payload.get(field, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TypeError(f"{self.name} {field} must be a non-negative integer")
            token_values[field] = value
        estimated_cost = payload.get("estimated_cost_usd", 0)
        if isinstance(estimated_cost, bool) or not isinstance(estimated_cost, (int, float)):
            raise TypeError(f"{self.name} estimated_cost_usd must be numeric")
        if not isfinite(float(estimated_cost)) or float(estimated_cost) < 0:
            raise ValueError(f"{self.name} estimated_cost_usd must be finite and non-negative")
        provider_request_id = payload.get("provider_request_id")
        if provider_request_id is not None and not isinstance(provider_request_id, str):
            raise TypeError(f"{self.name} provider_request_id must be text")
        return GatewayResponse(
            request_id=request.request_id,
            route=self.route,
            content=content,
            typed_payload=typed_payload if isinstance(typed_payload, Mapping) else None,
            tool_calls=tuple(raw_tool_calls),
            latency_ms=elapsed,
            input_tokens=token_values["input_tokens"],
            output_tokens=token_values["output_tokens"],
            estimated_cost_usd=float(estimated_cost),
            provider_request_id=provider_request_id.strip()
            if provider_request_id is not None
            else None,
        )


class LiteLLMGatewayAdapter(TypedGatewayAdapter):
    """LiteLLM route is still injected in tests; automatic routing is not used."""

    def __init__(
        self, route: GatewayRoute, transport: Callable[[GatewayRequest], Mapping[str, object]]
    ) -> None:
        super().__init__(name="litellm", route=route, transport=transport)


class OmniRouteGatewayAdapter(TypedGatewayAdapter):
    """OmniRoute challenger with the same typed contract and explicit route."""

    def __init__(
        self, route: GatewayRoute, transport: Callable[[GatewayRequest], Mapping[str, object]]
    ) -> None:
        super().__init__(name="omniroute", route=route, transport=transport)


class DirectProviderGatewayAdapter(TypedGatewayAdapter):
    def __init__(
        self, route: GatewayRoute, transport: Callable[[GatewayRequest], Mapping[str, object]]
    ) -> None:
        super().__init__(name="direct_provider", route=route, transport=transport)
