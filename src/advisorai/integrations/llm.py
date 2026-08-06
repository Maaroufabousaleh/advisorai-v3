"""OpenAI-compatible direct model gateway with typed, non-authoritative output."""

from __future__ import annotations

import json
from collections.abc import Mapping
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
        super().__init__(name="direct_provider", route=route, transport=self._complete_payload)

    def _complete_payload(self, request: GatewayRequest) -> Mapping[str, object]:
        payload: dict[str, Any] = {
            "model": request.route.model,
            "messages": [item.model_dump(mode="json") for item in request.messages],
            "temperature": 0,
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": f"Typed {tool.output_schema_version} evidence operation",
                        "parameters": {"type": "object"},
                    },
                }
                for tool in request.tools
            ]
        try:
            response = self.client.post_json(
                self._endpoint_url(self.endpoint_path),
                payload,
                headers={"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"},
            )
            decoded = json.loads(response.body)
        except (HttpTransportError, json.JSONDecodeError) as exc:
            raise GatewayTransportError("direct model gateway request failed") from exc
        if not isinstance(decoded, Mapping):
            raise GatewayTransportError("direct model gateway returned a non-object response")
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
        return {
            "content": content,
            "typed_payload": typed_payload,
            "tool_calls": tuple(calls),
            "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "output_tokens": int(usage.get("completion_tokens", 0) or 0),
            "estimated_cost_usd": float(decoded.get("estimated_cost_usd", 0) or 0),
            "provider_request_id": str(decoded["id"]) if decoded.get("id") is not None else None,
        }

    def _endpoint_url(self, path: str) -> str:
        # SafeHttpClient validates the host and HTTPS scheme.  It accepts an
        # absolute URL, so route clients can be configured with a path-bearing
        # base host by using a request-specific adapter instance.
        if not isinstance(getattr(self.client, "base_url", None), str):
            raise GatewayTransportError("model client must expose a base_url")
        return f"{self.client.base_url.rstrip('/')}/{path.lstrip('/')}"


__all__ = ["GatewayTransportError", "OpenAICompatibleGatewayAdapter"]
