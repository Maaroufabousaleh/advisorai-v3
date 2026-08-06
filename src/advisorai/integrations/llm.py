"""OpenAI-compatible direct model gateway with typed, non-authoritative output."""

from __future__ import annotations

import json
from collections.abc import Mapping
from math import isfinite
from typing import Any

from advisorai.gateway.adapters import TypedGatewayAdapter
from advisorai.ports import GatewayRequest, GatewayRoute

from .http import HttpTransportError, SafeHttpClient


class GatewayTransportError(RuntimeError):
    """Provider failure that must become recovery/abstention at the caller."""


class OpenAICompatibleGatewayAdapter(TypedGatewayAdapter):
    """A single pinned direct route; no fallback or trading authority is implicit."""

    def __init__(
        self,
        route: GatewayRoute,
        client: SafeHttpClient,
        *,
        api_key: str | None,
        endpoint_path: str = "/chat/completions",
        input_price_per_million: float | None = None,
        output_price_per_million: float | None = None,
        request_price_usd: float | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("direct model gateway requires an API key")
        if (
            not endpoint_path.startswith("/")
            or "?" in endpoint_path
            or "#" in endpoint_path
            or any(token in endpoint_path.lower() for token in ("order", "trade", "deploy"))
        ):
            raise ValueError("model gateway endpoint path is not admitted")
        object.__setattr__(self, "client", client)
        object.__setattr__(self, "endpoint_path", endpoint_path)
        object.__setattr__(self, "_api_key", api_key.strip())
        object.__setattr__(self, "_input_price_per_million", input_price_per_million)
        object.__setattr__(self, "_output_price_per_million", output_price_per_million)
        object.__setattr__(self, "_request_price_usd", request_price_usd)
        super().__init__(name="direct_provider", route=route, transport=self._complete_payload)

    def _complete_payload(self, request: GatewayRequest) -> Mapping[str, object]:
        payload: dict[str, Any] = {
            "model": request.route.model,
            "messages": [item.model_dump(mode="json") for item in request.messages],
            "temperature": 0,
        }
        if request.provider_options:
            payload.update(request.provider_options)
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": f"Typed {tool.output_schema_version} evidence operation",
                        "parameters": dict(tool.input_schema),
                    },
                }
                for tool in request.tools
            ]
        try:
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            }
            if request.route.gateway.lower() == "openrouter":
                # OpenRouter only returns the selected endpoint/provider when
                # this documented opt-in header is enabled.
                headers["X-OpenRouter-Metadata"] = "enabled"
            response = self.client.post_json(
                self._endpoint_url(self.endpoint_path),
                payload,
                headers=headers,
            )
            decoded = json.loads(response.body)
        except (HttpTransportError, json.JSONDecodeError) as exc:
            raise GatewayTransportError("direct model gateway request failed") from exc
        if not isinstance(decoded, Mapping):
            raise GatewayTransportError("direct model gateway returned a non-object response")
        actual_provider, actual_model, actual_endpoint_variant, routing_metadata = (
            self._parse_routing_identity(decoded, request)
        )
        choices = decoded.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise GatewayTransportError("direct model gateway response has no choices")
        message = choices[0].get("message")
        if not isinstance(message, Mapping):
            raise GatewayTransportError("direct model gateway response has no message")
        raw_calls = message.get("tool_calls", ())
        calls: list[Mapping[str, object]] = []
        if raw_calls:
            if not isinstance(raw_calls, list):
                raise GatewayTransportError("direct model gateway tool calls must be a list")
            for item in raw_calls:
                if not isinstance(item, Mapping):
                    raise GatewayTransportError("direct model gateway tool call must be an object")
                function = item.get("function")
                if not isinstance(function, Mapping) or not isinstance(function.get("name"), str):
                    raise GatewayTransportError("direct model gateway tool call has no name")
                calls.append(
                    {"name": function["name"], "arguments": function.get("arguments", "{}")}
                )
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", "")) for part in content if isinstance(part, Mapping)
            )
        if content is None and calls:
            content = ""
        if not isinstance(content, str) or (not content.strip() and not calls):
            raise GatewayTransportError("direct model gateway response content is blank")
        typed_payload: Mapping[str, object] | None = None
        if request.route.schema_mode == "typed_json" and content.strip():
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                raise GatewayTransportError(
                    "direct model gateway returned malformed typed JSON"
                ) from exc
            if not isinstance(parsed, Mapping):
                raise GatewayTransportError("direct model gateway typed output must be an object")
            typed_payload = parsed
        usage = decoded.get("usage")
        usage = usage if isinstance(usage, Mapping) else {}
        billed_cost = self._optional_number(usage.get("cost"), "usage.cost")
        cost_details = usage.get("cost_details")
        cost_metadata: dict[str, object] = {
            "source": "openrouter.usage.cost"
            if request.route.gateway.lower() == "openrouter" and billed_cost is not None
            else "provider.usage",
            "usage": dict(usage),
            "usage_cost_usd": billed_cost,
            "cost_details": dict(cost_details) if isinstance(cost_details, Mapping) else {},
        }
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if key in usage:
                cost_metadata[key] = usage[key]
        estimated_cost = (
            billed_cost
            if billed_cost is not None
            else self._optional_number(decoded.get("estimated_cost_usd", 0) or 0, "estimated_cost_usd")
        )
        return {
            "content": content,
            "typed_payload": typed_payload,
            "tool_calls": tuple(calls),
            "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "output_tokens": int(usage.get("completion_tokens", 0) or 0),
            "estimated_cost_usd": estimated_cost,
            "provider_request_id": str(decoded["id"]) if decoded.get("id") is not None else None,
            "actual_provider": actual_provider,
            "actual_model": actual_model,
            "actual_gateway": (
                str(decoded["actual_gateway"])
                if isinstance(decoded.get("actual_gateway"), str)
                else request.route.gateway
            ),
            "actual_endpoint_variant": actual_endpoint_variant,
            "input_price_per_million": (
                decoded.get("input_price_per_million")
                if decoded.get("input_price_per_million") is not None
                else self._input_price_per_million
            ),
            "output_price_per_million": (
                decoded.get("output_price_per_million")
                if decoded.get("output_price_per_million") is not None
                else self._output_price_per_million
            ),
            "request_price_usd": (
                decoded.get("request_price_usd")
                if decoded.get("request_price_usd") is not None
                else self._request_price_usd
            ),
            "billed_cost_usd": billed_cost,
            "cost_metadata": cost_metadata,
            "routing_metadata": routing_metadata,
        }

    @staticmethod
    def _optional_number(value: object, field: str) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise GatewayTransportError(f"direct model gateway {field} must be numeric")
        number = float(value)
        if not isfinite(number) or number < 0:
            raise GatewayTransportError(f"direct model gateway {field} must be finite and non-negative")
        return number

    @classmethod
    def _parse_routing_identity(
        cls, decoded: Mapping[str, object], request: GatewayRequest
    ) -> tuple[str, str, str, dict[str, object]]:
        """Extract exact provider/model/endpoint identity from provider metadata."""

        if request.route.gateway.lower() == "openrouter":
            raw_metadata = decoded.get("openrouter_metadata")
            if not isinstance(raw_metadata, Mapping):
                raise GatewayTransportError("OpenRouter response omitted openrouter_metadata")
            endpoints = raw_metadata.get("endpoints")
            available = endpoints.get("available") if isinstance(endpoints, Mapping) else None
            if not isinstance(available, list):
                raise GatewayTransportError("OpenRouter metadata omitted endpoint candidates")
            selected = [item for item in available if isinstance(item, Mapping) and item.get("selected") is True]
            if len(selected) != 1:
                raise GatewayTransportError("OpenRouter metadata must identify exactly one selected endpoint")
            endpoint = selected[0]
            provider = endpoint.get("provider")
            model = endpoint.get("model")
            if not isinstance(provider, str) or not provider.strip():
                raise GatewayTransportError("OpenRouter metadata omitted the selected provider tag")
            if not isinstance(model, str) or not model.strip():
                raise GatewayTransportError("OpenRouter metadata omitted the selected model tag")
            return provider.strip(), model.strip(), provider.strip(), dict(raw_metadata)

        identity = {
            "provider": decoded.get("provider"),
            "model": decoded.get("model"),
            "endpoint_variant": decoded.get("provider_variant")
            or decoded.get("actual_endpoint_variant"),
        }
        if any(not isinstance(value, str) or not value.strip() for value in identity.values()):
            raise GatewayTransportError("provider response omitted exact routing identity metadata")
        raw_metadata = decoded.get("routing_metadata")
        return (
            str(identity["provider"]).strip(),
            str(identity["model"]).strip(),
            str(identity["endpoint_variant"]).strip(),
            dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {},
        )

    def _endpoint_url(self, path: str) -> str:
        # SafeHttpClient validates the host and HTTPS scheme.  It accepts an
        # absolute URL, so route clients can be configured with a path-bearing
        # base host by using a request-specific adapter instance.
        if not isinstance(getattr(self.client, "base_url", None), str):
            raise GatewayTransportError("model client must expose a base_url")
        return f"{self.client.base_url.rstrip('/')}/{path.lstrip('/')}"


__all__ = ["GatewayTransportError", "OpenAICompatibleGatewayAdapter"]
