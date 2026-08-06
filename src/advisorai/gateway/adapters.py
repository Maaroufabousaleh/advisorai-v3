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
    # Test/recovery adapters are local by default.  Concrete remote adapters
    # must opt in so PolicyGateway can require a RouteProfile admission.
    is_remote: bool = False

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
        if content is None:
            content = ""
        if not isinstance(content, str) or (
            not content.strip() and not raw_tool_calls and typed_payload is None
        ):
            raise ValueError(
                f"{self.name} transport response requires text, typed payload, or tool calls"
            )
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
        has_observed_identity = any(
            key in payload
            for key in (
                "observed_provider_name",
                "top_level_response_model",
                "resolved_endpoint_model",
                "endpoint_selected",
            )
        )
        actual_identity: dict[str, str] = {}
        for field in ("actual_provider", "actual_model", "actual_gateway"):
            value = payload.get(field)
            if field == "actual_provider" and value is None and isinstance(
                payload.get("observed_provider_name"), str
            ):
                value = payload["observed_provider_name"]
            if field == "actual_model" and value is None:
                value = payload.get("resolved_endpoint_model") or payload.get("resolved_model")
            if field == "actual_gateway" and value is None:
                value = request.route.gateway
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{self.name} response omitted {field}")
            actual_identity[field] = value.strip()
        def optional_price(field: str) -> float | None:
            value = payload.get(field)
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{self.name} {field} must be numeric")
            if not isfinite(float(value)) or float(value) < 0:
                raise ValueError(f"{self.name} {field} must be finite and non-negative")
            return float(value)

        actual_endpoint_variant = payload.get("actual_endpoint_variant")
        requested_endpoint_selector = payload.get(
            "requested_endpoint_selector", request.route.endpoint_variant
        )
        if actual_endpoint_variant is None and has_observed_identity:
            # OpenRouter does not expose a stable endpoint-variant identity in
            # the response.  Preserve the requested selector as the legacy
            # field; the observed provider/model remain separate fields.
            actual_endpoint_variant = requested_endpoint_selector
        if not isinstance(actual_endpoint_variant, str) or not actual_endpoint_variant.strip():
            raise ValueError(f"{self.name} response omitted actual_endpoint_variant")
        route_updates = {
            "provider": payload.get("route_provider", actual_identity["actual_provider"]),
            "model": payload.get("route_model", actual_identity["actual_model"]),
            "gateway": payload.get("route_gateway", actual_identity["actual_gateway"]),
            "endpoint_variant": payload.get("route_endpoint_variant", request.route.endpoint_variant),
            "fallback_chain": self.route.fallback_chain,
        }
        if any(
            not isinstance(route_updates[field], str) or not route_updates[field].strip()
            for field in ("provider", "model", "gateway")
        ):
            raise ValueError(f"{self.name} returned invalid route identity")
        actual_route = self.route.model_copy(
            update={
                **route_updates,
            }
        )

        requested_provider_selector = payload.get(
            "requested_provider_selector", request.route.provider
        )
        requested_model = payload.get("requested_model", request.route.model)
        requested_gateway = payload.get("requested_gateway", request.route.gateway)
        for field, value in (
            ("requested_provider_selector", requested_provider_selector),
            ("requested_model", requested_model),
            ("requested_gateway", requested_gateway),
            ("requested_endpoint_selector", requested_endpoint_selector),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{self.name} returned invalid {field}")
        if requested_provider_selector != request.route.provider or requested_model != request.route.model:
            raise ValueError(f"{self.name} returned a route selector different from the request")
        if requested_gateway != request.route.gateway or requested_endpoint_selector != request.route.endpoint_variant:
            raise ValueError(f"{self.name} returned a route endpoint different from the request")

        def optional_text(field: str) -> str | None:
            value = payload.get(field)
            if value is None:
                return None
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{self.name} {field} must be non-blank text")
            return value.strip()

        def optional_bool(field: str) -> bool | None:
            value = payload.get(field)
            if value is None:
                return None
            if not isinstance(value, bool):
                raise TypeError(f"{self.name} {field} must be boolean")
            return value

        def optional_int(field: str) -> int | None:
            value = payload.get(field)
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise TypeError(f"{self.name} {field} must be a positive integer")
            return value

        input_tokens = token_values["input_tokens"]
        output_tokens = token_values["output_tokens"]
        if input_tokens > (request.generation_budget.max_input_tokens or input_tokens):
            raise ValueError(f"{self.name} response input tokens exceed generation budget")
        if output_tokens > request.generation_budget.max_output_tokens:
            raise ValueError(f"{self.name} response output tokens exceed generation budget")
        billed_cost = optional_price("billed_cost_usd")
        if billed_cost is not None and billed_cost > request.generation_budget.max_billed_cost_usd:
            raise ValueError(f"{self.name} billed cost exceeds generation budget")
        expected_cost = optional_price("expected_cost_usd")
        if expected_cost is None:
            expected_cost = float(estimated_cost)
        cost_difference = payload.get("cost_difference_usd")
        if cost_difference is None and billed_cost is not None:
            cost_difference = billed_cost - expected_cost
        if cost_difference is not None and (
            isinstance(cost_difference, bool)
            or not isinstance(cost_difference, (int, float))
            or not isfinite(float(cost_difference))
        ):
            raise ValueError(f"{self.name} cost_difference_usd must be finite and numeric")

        return GatewayResponse(
            request_id=request.request_id,
            route=actual_route,
            requested_route=self.route,
            content=content,
            typed_payload=typed_payload if isinstance(typed_payload, Mapping) else None,
            tool_calls=tuple(raw_tool_calls),
            invocation_mode=request.invocation_mode,
            tool_used=bool(raw_tool_calls),
            latency_ms=elapsed,
            input_tokens=token_values["input_tokens"],
            output_tokens=token_values["output_tokens"],
            estimated_cost_usd=float(estimated_cost),
            provider_request_id=provider_request_id.strip()
            if provider_request_id is not None
            else None,
            output_kind=request.output_kind,
            actual_provider=actual_identity["actual_provider"],
            actual_model=actual_identity["actual_model"],
            actual_gateway=actual_route.gateway,
            requested_provider_selector=(
                str(requested_provider_selector) if has_observed_identity else None
            ),
            requested_model=str(requested_model) if has_observed_identity else None,
            requested_gateway=str(requested_gateway) if has_observed_identity else None,
            requested_endpoint_selector=(
                str(requested_endpoint_selector)
                if has_observed_identity and requested_endpoint_selector is not None
                else None
            ),
            observed_provider_name=optional_text("observed_provider_name"),
            top_level_response_model=optional_text("top_level_response_model"),
            resolved_model=optional_text("resolved_model")
            or optional_text("resolved_endpoint_model"),
            resolved_endpoint_model=optional_text("resolved_endpoint_model")
            or optional_text("resolved_model"),
            endpoint_selector_proof=optional_text("endpoint_selector_proof"),
            endpoint_selected=optional_bool("endpoint_selected"),
            routing_strategy=optional_text("routing_strategy"),
            routing_attempt=optional_int("routing_attempt"),
            is_byok=optional_bool("is_byok"),
            endpoint_variant=actual_endpoint_variant.strip(),
            actual_endpoint_variant=actual_endpoint_variant.strip(),
            input_price_per_million=optional_price("input_price_per_million"),
            output_price_per_million=optional_price("output_price_per_million"),
            request_price_usd=optional_price("request_price_usd"),
            expected_cost_usd=expected_cost,
            billed_cost_usd=billed_cost,
            cost_difference_usd=float(cost_difference) if cost_difference is not None else None,
            cost_metadata=(
                dict(payload.get("cost_metadata"))
                if isinstance(payload.get("cost_metadata"), Mapping)
                else {}
            ),
            routing_metadata=(
                dict(payload.get("routing_metadata"))
                if isinstance(payload.get("routing_metadata"), Mapping)
                else {}
            ),
            failure_metadata=(
                dict(payload.get("failure_metadata"))
                if isinstance(payload.get("failure_metadata"), Mapping)
                else {}
            ),
            attempt_metadata=tuple(
                item for item in payload.get("attempt_metadata", ())
                if isinstance(item, Mapping)
            )
            if isinstance(payload.get("attempt_metadata", ()), Sequence)
            and not isinstance(payload.get("attempt_metadata", ()), (str, bytes))
            else (),
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
