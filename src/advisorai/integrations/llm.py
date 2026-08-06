"""OpenAI-compatible direct model gateway with governed routing and budgets."""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC
from email.utils import parsedate_to_datetime
from math import ceil, isfinite
from typing import Any

from advisorai.gateway.adapters import TypedGatewayAdapter
from advisorai.ports import GATEWAY_OUTPUT_SCHEMAS, GatewayRequest, GatewayRoute

from .http import HttpTransportError, SafeHttpClient


class GatewayTransportError(RuntimeError):
    """Provider failure that must become recovery/abstention at the caller."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        failure_metadata: Mapping[str, object] | None = None,
        attempt_metadata: Sequence[Mapping[str, object]] = (),
        retryable: bool = False,
        no_cross_provider_fallback: bool = True,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.failure_metadata = dict(failure_metadata or {})
        self.attempt_metadata = tuple(dict(item) for item in attempt_metadata)
        self.retryable = retryable
        self.no_cross_provider_fallback = no_cross_provider_fallback


class OpenAICompatibleGatewayAdapter(TypedGatewayAdapter):
    """A single admitted direct route; no fallback or trading authority is implicit."""

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
        retry_sleeper: Callable[[float], None] = time.sleep,
        retry_jitter: Callable[[float], float] | None = None,
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
        object.__setattr__(self, "_retry_sleeper", retry_sleeper)
        object.__setattr__(self, "_retry_jitter", retry_jitter or (lambda maximum: random.uniform(0, maximum)))
        super().__init__(name="direct_provider", route=route, transport=self._complete_payload)

    def _complete_payload(self, request: GatewayRequest) -> Mapping[str, object]:
        budget = request.generation_budget
        estimated_input = self._estimate_input_tokens(request)
        if budget.max_input_tokens is not None and estimated_input > budget.max_input_tokens:
            raise GatewayTransportError(
                "input exceeds generation budget",
                failure_metadata={
                    "error_type": "budget_exceeded",
                    "budget": "max_input_tokens",
                    "estimated_input_tokens": estimated_input,
                    "max_input_tokens": budget.max_input_tokens,
                },
                no_cross_provider_fallback=True,
            )

        payload: dict[str, Any] = {
            "model": request.route.model,
            "messages": [item.model_dump(mode="json") for item in request.messages],
            "temperature": 0,
            "stream": False,
            "max_tokens": budget.max_output_tokens,
            "response_format": self._response_format(request),
        }
        # Provider routing options are accepted only as a policy-produced
        # mapping.  Generation controls are always written after the merge so
        # a caller cannot override the budget or prompt/tool contents.
        if request.provider_options:
            options = dict(request.provider_options)
            provider_options = options.get("provider")
            if isinstance(provider_options, Mapping):
                governed_provider = dict(provider_options)
                if "only" in governed_provider and governed_provider["only"] != [request.route.provider]:
                    raise GatewayTransportError(
                        "provider selector override is not permitted",
                        failure_metadata={"error_type": "routing_policy_override"},
                    )
                if "allow_fallbacks" in governed_provider and governed_provider["allow_fallbacks"] is not False:
                    raise GatewayTransportError(
                        "provider fallback override is not permitted",
                        failure_metadata={"error_type": "routing_policy_override"},
                    )
                if request.route.training_policy.lower() in {
                    "no_training",
                    "no_training_zdr",
                    "zdr",
                    "zero_data_retention",
                }:
                    if governed_provider.get("zdr") is not True:
                        raise GatewayTransportError(
                            "private route requires ZDR",
                            failure_metadata={"error_type": "routing_policy_override"},
                        )
                    if governed_provider.get("data_collection") != "deny":
                        raise GatewayTransportError(
                            "private route requires data-collection denial",
                            failure_metadata={"error_type": "routing_policy_override"},
                        )
                if "require_parameters" in governed_provider and governed_provider["require_parameters"] is not True:
                    raise GatewayTransportError(
                        "provider parameter requirement override is not permitted",
                        failure_metadata={"error_type": "routing_policy_override"},
                    )
                options["provider"] = governed_provider
            payload.update(options)
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
        payload.update(
            {
                "model": request.route.model,
                "messages": [item.model_dump(mode="json") for item in request.messages],
                "temperature": 0,
                "stream": False,
                "max_tokens": budget.max_output_tokens,
                "response_format": self._response_format(request),
            }
        )
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

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        if request.route.gateway.lower() == "openrouter":
            # OpenRouter only returns the selected endpoint/provider when this
            # documented opt-in header is enabled.
            headers["X-OpenRouter-Metadata"] = "enabled"

        max_attempts = min(budget.maximum_attempts, 3)
        attempt_metadata: list[dict[str, object]] = []
        for attempt_number in range(1, max_attempts + 1):
            try:
                response = self.client.post_json(
                    self._endpoint_url(self.endpoint_path),
                    payload,
                    headers=headers,
                    max_retries=0,
                    timeout_seconds=budget.timeout_seconds,
                )
                decoded = json.loads(response.body)
                if not isinstance(decoded, Mapping):
                    raise GatewayTransportError(
                        "direct model gateway returned a non-object response",
                        failure_metadata={"error_type": "invalid_response", "attempt": attempt_number},
                        attempt_metadata=attempt_metadata,
                    )
                identity = self._parse_routing_identity(decoded, request, attempt_number)
                result = self._parse_success(decoded, request, identity)
                result["routing_attempt"] = identity["routing_attempt"] or attempt_number
                result["attempt_metadata"] = tuple(attempt_metadata)
                return result
            except HttpTransportError as exc:
                failure = self._failure_metadata(exc, request, attempt_number)
                attempt_metadata.append(failure)
                status = exc.status_code
                if status in {429, 503} and attempt_number < max_attempts:
                    self._sleep_before_retry(failure, attempt_number)
                    continue
                raise GatewayTransportError(
                    "direct model gateway request failed",
                    status_code=status,
                    failure_metadata=failure,
                    attempt_metadata=attempt_metadata,
                    retryable=status in {429, 503},
                    no_cross_provider_fallback=True,
                ) from exc
            except GatewayTransportError as exc:
                metadata = dict(exc.failure_metadata)
                metadata.setdefault("attempt", attempt_number)
                if metadata and not exc.attempt_metadata:
                    attempt_metadata.append(metadata)
                raise GatewayTransportError(
                    str(exc),
                    status_code=exc.status_code,
                    failure_metadata=metadata,
                    attempt_metadata=tuple(attempt_metadata) or exc.attempt_metadata,
                    retryable=False,
                    no_cross_provider_fallback=True,
                ) from exc
            except (json.JSONDecodeError, TimeoutError) as exc:
                failure = {
                    "error_type": "timeout" if isinstance(exc, TimeoutError) else "invalid_json",
                    "attempt": attempt_number,
                }
                attempt_metadata.append(failure)
                raise GatewayTransportError(
                    "direct model gateway request failed",
                    failure_metadata=failure,
                    attempt_metadata=attempt_metadata,
                    no_cross_provider_fallback=True,
                ) from exc
        raise GatewayTransportError(
            "direct model gateway request failed",
            failure_metadata={"error_type": "retry_exhausted"},
            attempt_metadata=attempt_metadata,
            no_cross_provider_fallback=True,
        )

    @staticmethod
    def _response_format(request: GatewayRequest) -> dict[str, object]:
        schema = GATEWAY_OUTPUT_SCHEMAS[request.output_kind].model_json_schema()
        return {
            "type": "json_schema",
            "json_schema": {
                "name": request.output_kind.value,
                "strict": True,
                "schema": schema,
            },
        }

    @staticmethod
    def _estimate_input_tokens(request: GatewayRequest) -> int:
        chars = sum(len(message.role) + len(message.content) for message in request.messages)
        return max(1, ceil(chars / 4))

    def _parse_success(
        self,
        decoded: Mapping[str, object],
        request: GatewayRequest,
        identity: Mapping[str, object],
    ) -> dict[str, object]:
        choices = decoded.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise GatewayTransportError(
                "direct model gateway response has no choices",
                failure_metadata={"error_type": "missing_choices"},
            )
        message = choices[0].get("message")
        if not isinstance(message, Mapping):
            raise GatewayTransportError(
                "direct model gateway response has no message",
                failure_metadata={"error_type": "missing_message"},
            )
        raw_calls = message.get("tool_calls", ())
        calls: list[Mapping[str, object]] = []
        if raw_calls:
            if not isinstance(raw_calls, list):
                raise GatewayTransportError(
                    "direct model gateway tool calls must be a list",
                    failure_metadata={"error_type": "invalid_tool_calls"},
                )
            for item in raw_calls:
                if not isinstance(item, Mapping):
                    raise GatewayTransportError(
                        "direct model gateway tool call must be an object",
                        failure_metadata={"error_type": "invalid_tool_call"},
                    )
                function = item.get("function")
                if not isinstance(function, Mapping) or not isinstance(function.get("name"), str):
                    raise GatewayTransportError(
                        "direct model gateway tool call has no name",
                        failure_metadata={"error_type": "invalid_tool_call"},
                    )
                calls.append({"name": function["name"], "arguments": function.get("arguments", "{}")})
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", "")) for part in content if isinstance(part, Mapping)
            )
        if content is None and calls:
            content = ""
        if not isinstance(content, str) or (not content.strip() and not calls):
            raise GatewayTransportError(
                "direct model gateway response content is blank",
                failure_metadata={"error_type": "blank_content"},
            )
        typed_payload: Mapping[str, object] | None = None
        if request.route.schema_mode == "typed_json" and content.strip():
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                raise GatewayTransportError(
                    "direct model gateway returned malformed typed JSON",
                    failure_metadata={"error_type": "malformed_typed_json"},
                ) from exc
            if not isinstance(parsed, Mapping):
                raise GatewayTransportError(
                    "direct model gateway typed output must be an object",
                    failure_metadata={"error_type": "typed_output_not_object"},
                )
            typed_payload = parsed
        usage = decoded.get("usage")
        usage = usage if isinstance(usage, Mapping) else {}
        billed_cost = self._optional_number(usage.get("cost"), "usage.cost")
        input_tokens = self._usage_int(usage.get("prompt_tokens"), "prompt_tokens")
        output_tokens = self._usage_int(usage.get("completion_tokens"), "completion_tokens")
        if output_tokens > request.generation_budget.max_output_tokens:
            raise GatewayTransportError(
                "direct model gateway output exceeds generation budget",
                failure_metadata={
                    "error_type": "budget_exceeded",
                    "budget": "max_output_tokens",
                    "output_tokens": output_tokens,
                    "max_output_tokens": request.generation_budget.max_output_tokens,
                },
            )
        if request.generation_budget.max_input_tokens is not None and input_tokens > request.generation_budget.max_input_tokens:
            raise GatewayTransportError(
                "direct model gateway input exceeds generation budget",
                failure_metadata={"error_type": "budget_exceeded", "budget": "max_input_tokens"},
            )
        input_price = self._optional_number(
            decoded.get("input_price_per_million", self._input_price_per_million),
            "input_price_per_million",
        )
        output_price = self._optional_number(
            decoded.get("output_price_per_million", self._output_price_per_million),
            "output_price_per_million",
        )
        request_price = self._optional_number(
            decoded.get("request_price_usd", self._request_price_usd), "request_price_usd"
        )
        expected_cost = self._expected_cost(
            input_tokens, output_tokens, input_price, output_price, request_price, billed_cost
        )
        if expected_cost > request.generation_budget.max_expected_cost_usd:
            raise GatewayTransportError(
                "direct model gateway expected cost exceeds generation budget",
                failure_metadata={
                    "error_type": "budget_exceeded",
                    "budget": "max_expected_cost_usd",
                    "expected_cost_usd": expected_cost,
                },
            )
        provider_options = request.provider_options.get("provider")
        paid_private = isinstance(provider_options, Mapping) and provider_options.get("zdr") is True
        if paid_private and billed_cost is None:
            raise GatewayTransportError(
                "direct model gateway omitted billed usage cost",
                failure_metadata={"error_type": "missing_billed_cost"},
            )
        if billed_cost is not None and billed_cost > request.generation_budget.max_billed_cost_usd:
            raise GatewayTransportError(
                "direct model gateway billed cost exceeds generation budget",
                failure_metadata={
                    "error_type": "budget_exceeded",
                    "budget": "max_billed_cost_usd",
                    "billed_cost_usd": billed_cost,
                    "max_billed_cost_usd": request.generation_budget.max_billed_cost_usd,
                },
            )
        cost_details = usage.get("cost_details")
        cost_metadata: dict[str, object] = {
            "source": "openrouter.usage.cost"
            if request.route.gateway.lower() == "openrouter" and billed_cost is not None
            else "provider.usage",
            "usage": dict(usage),
            "usage_cost_usd": billed_cost,
            "expected_cost_usd": expected_cost,
            "cost_difference_usd": billed_cost - expected_cost if billed_cost is not None else None,
            "cost_details": dict(cost_details) if isinstance(cost_details, Mapping) else {},
        }
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if key in usage:
                cost_metadata[key] = usage[key]
        estimated_cost = billed_cost if billed_cost is not None else expected_cost
        return {
            "content": content,
            "typed_payload": typed_payload,
            "tool_calls": tuple(calls),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": estimated_cost,
            "expected_cost_usd": expected_cost,
            "cost_difference_usd": billed_cost - expected_cost if billed_cost is not None else None,
            "provider_request_id": str(decoded["id"]) if decoded.get("id") is not None else None,
            **(
                {
                    "route_provider": request.route.provider,
                    "route_model": request.route.model,
                    "route_gateway": request.route.gateway,
                    "route_endpoint_variant": request.route.endpoint_variant,
                }
                if request.route.gateway.lower() == "openrouter"
                else {}
            ),
            "actual_provider": identity["observed_provider_name"],
            "actual_model": identity["resolved_endpoint_model"],
            "actual_gateway": identity["actual_gateway"],
            "actual_endpoint_variant": (
                identity["endpoint_selector_proof"]
                if request.route.gateway.lower() == "openrouter"
                and (
                    identity["observed_provider_name"] != request.route.provider
                    or identity["resolved_endpoint_model"] != request.route.model
                )
                else request.route.endpoint_variant
            ),
            "requested_provider_selector": request.route.provider,
            "requested_model": request.route.model,
            "requested_gateway": request.route.gateway,
            "requested_endpoint_selector": request.route.endpoint_variant,
            "observed_provider_name": identity["observed_provider_name"],
            "top_level_response_model": identity["top_level_response_model"],
            "resolved_model": identity["resolved_endpoint_model"],
            "resolved_endpoint_model": identity["resolved_endpoint_model"],
            "endpoint_selector_proof": identity["endpoint_selector_proof"],
            "endpoint_selected": True,
            "routing_strategy": identity["routing_strategy"],
            "routing_attempt": identity["routing_attempt"],
            "is_byok": identity["is_byok"],
            "input_price_per_million": input_price,
            "output_price_per_million": output_price,
            "request_price_usd": request_price,
            "billed_cost_usd": billed_cost,
            "cost_metadata": cost_metadata,
            "routing_metadata": dict(identity["routing_metadata"]),
        }

    @classmethod
    def _parse_routing_identity(
        cls,
        decoded: Mapping[str, object],
        request: GatewayRequest,
        attempt_number: int,
    ) -> dict[str, object]:
        """Extract route selector and observed endpoint identities separately."""

        if request.route.gateway.lower() == "openrouter":
            raw_metadata = decoded.get("openrouter_metadata")
            if not isinstance(raw_metadata, Mapping):
                raise GatewayTransportError(
                    "OpenRouter response omitted openrouter_metadata",
                    failure_metadata={"error_type": "missing_routing_metadata", "attempt": attempt_number},
                )
            endpoints = raw_metadata.get("endpoints")
            available = endpoints.get("available") if isinstance(endpoints, Mapping) else None
            if not isinstance(available, list):
                raise GatewayTransportError(
                    "OpenRouter metadata omitted endpoint candidates",
                    failure_metadata={"error_type": "missing_endpoint_candidates", "attempt": attempt_number},
                )
            selected = [item for item in available if isinstance(item, Mapping) and item.get("selected") is True]
            if len(selected) != 1:
                raise GatewayTransportError(
                    "OpenRouter metadata must identify exactly one selected endpoint",
                    failure_metadata={
                        "error_type": "selected_endpoint_count",
                        "selected_count": len(selected),
                        "attempt": attempt_number,
                        "attempted_endpoints": cls._safe_endpoint_attempts(available),
                    },
                )
            endpoint = selected[0]
            provider = endpoint.get("provider") or endpoint.get("provider_name")
            model = endpoint.get("model") or endpoint.get("resolved_model")
            top_level_model = decoded.get("model")
            if (
                top_level_model is None
                and isinstance(raw_metadata.get("requested"), str)
                and raw_metadata["requested"] == request.route.model
                and model == request.route.model
                and decoded.get("provider") is None
            ):
                # Older OpenRouter responses omitted the top-level model while
                # echoing the requested alias in metadata.  Treat this only as
                # a compatibility observation; admissions still validate it.
                top_level_model = raw_metadata["requested"]
            if not isinstance(provider, str) or not provider.strip():
                raise GatewayTransportError(
                    "OpenRouter metadata omitted the selected provider tag",
                    failure_metadata={"error_type": "missing_provider_identity", "attempt": attempt_number},
                )
            if not isinstance(model, str) or not model.strip():
                raise GatewayTransportError(
                    "OpenRouter metadata omitted the selected model tag",
                    failure_metadata={"error_type": "missing_resolved_model", "attempt": attempt_number},
                )
            if not isinstance(top_level_model, str) or not top_level_model.strip():
                raise GatewayTransportError(
                    "OpenRouter response omitted top-level model",
                    failure_metadata={"error_type": "missing_top_level_model", "attempt": attempt_number},
                )
            proof = cls._endpoint_proof(endpoint)
            return {
                "observed_provider_name": provider.strip(),
                "top_level_response_model": top_level_model.strip(),
                "resolved_endpoint_model": model.strip(),
                "endpoint_selector_proof": proof,
                "actual_gateway": request.route.gateway,
                "routing_strategy": raw_metadata.get("strategy")
                if isinstance(raw_metadata.get("strategy"), str)
                else None,
                "routing_attempt": raw_metadata.get("attempt")
                if isinstance(raw_metadata.get("attempt"), int) and not isinstance(raw_metadata.get("attempt"), bool)
                else attempt_number,
                "is_byok": cls._optional_bool(
                    endpoint.get("is_byok"), raw_metadata.get("is_byok"), decoded.get("is_byok")
                ),
                "routing_metadata": dict(raw_metadata),
            }

        provider = decoded.get("provider")
        model = decoded.get("model")
        endpoint_variant = decoded.get("provider_variant") or decoded.get("actual_endpoint_variant")
        if any(not isinstance(value, str) or not value.strip() for value in (provider, model, endpoint_variant)):
            raise GatewayTransportError(
                "provider response omitted exact routing identity metadata",
                failure_metadata={"error_type": "missing_routing_identity", "attempt": attempt_number},
            )
        raw_metadata = decoded.get("routing_metadata")
        return {
            "observed_provider_name": str(provider).strip(),
            "top_level_response_model": str(model).strip(),
            "resolved_endpoint_model": str(model).strip(),
            "endpoint_selector_proof": str(endpoint_variant).strip(),
            "actual_gateway": str(decoded.get("actual_gateway") or request.route.gateway),
            "routing_strategy": None,
            "routing_attempt": attempt_number,
            "is_byok": cls._optional_bool(decoded.get("is_byok")),
            "routing_metadata": dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {},
        }

    @staticmethod
    def _endpoint_proof(endpoint: Mapping[str, object]) -> str:
        canonical = json.dumps(dict(endpoint), sort_keys=True, separators=(",", ":"), default=str)
        # This is a proof of the observed endpoint record, not an endpoint
        # identity or a display-name-to-selector conversion.
        import hashlib

        return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"

    @staticmethod
    def _safe_endpoint_attempts(available: Sequence[object]) -> tuple[dict[str, object], ...]:
        attempts: list[dict[str, object]] = []
        for item in available:
            if not isinstance(item, Mapping):
                continue
            attempts.append(
                {
                    key: item[key]
                    for key in ("provider", "provider_name", "model", "resolved_model", "selected", "is_byok")
                    if key in item and isinstance(item[key], (str, bool, int, float))
                }
            )
        return tuple(attempts)

    @staticmethod
    def _optional_bool(*values: object) -> bool | None:
        for value in values:
            if isinstance(value, bool):
                return value
        return None

    @staticmethod
    def _usage_int(value: object, field: str) -> int:
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise GatewayTransportError(f"direct model gateway usage {field} must be a non-negative integer")
        return value

    @staticmethod
    def _expected_cost(
        input_tokens: int,
        output_tokens: int,
        input_price: float | None,
        output_price: float | None,
        request_price: float | None,
        billed_cost: float | None,
    ) -> float:
        if input_price is None or output_price is None:
            return billed_cost or 0.0
        return (
            input_tokens * input_price / 1_000_000
            + output_tokens * output_price / 1_000_000
            + (request_price or 0.0)
        )

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
    def _failure_metadata(
        cls, error: HttpTransportError, request: GatewayRequest, attempt_number: int
    ) -> dict[str, object]:
        decoded: Mapping[str, object] = {}
        if error.response_body:
            try:
                candidate = json.loads(error.response_body)
                if isinstance(candidate, Mapping):
                    decoded = candidate
            except (TypeError, json.JSONDecodeError):
                decoded = {}
        raw_metadata = decoded.get("openrouter_metadata")
        raw_metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
        error_payload = decoded.get("error")
        error_payload = error_payload if isinstance(error_payload, Mapping) else {}
        endpoints = raw_metadata.get("endpoints")
        available = endpoints.get("available") if isinstance(endpoints, Mapping) else None
        attempted = cls._safe_endpoint_attempts(available) if isinstance(available, list) else ()
        retry_after = cls._retry_after_seconds(error.response_headers)
        provider_name = next(
            (
                item.get("provider") or item.get("provider_name")
                for item in attempted
                if isinstance(item.get("provider") or item.get("provider_name"), str)
            ),
            None,
        )
        if provider_name is None:
            candidate_provider = decoded.get("provider") or raw_metadata.get("provider")
            if isinstance(candidate_provider, str) and candidate_provider.strip():
                provider_name = candidate_provider.strip()
        resolved_model = next(
            (
                item.get("model") or item.get("resolved_model")
                for item in attempted
                if isinstance(item.get("model") or item.get("resolved_model"), str)
            ),
            None,
        )
        if resolved_model is None:
            candidate_model = decoded.get("model") or raw_metadata.get("model")
            if isinstance(candidate_model, str) and candidate_model.strip():
                resolved_model = candidate_model.strip()
        metadata: dict[str, object] = {
            "http_status": error.status_code,
            "status_code": error.status_code,
            "error_type": error_payload.get("type")
            if isinstance(error_payload.get("type"), str)
            else error.error_type or f"http_{error.status_code or 'transport'}",
            "provider_code": error_payload.get("code")
            if isinstance(error_payload.get("code"), (str, int))
            else decoded.get("code"),
            "provider_name": provider_name,
            "resolved_model": resolved_model,
            "limit_source": decoded.get("limit_source") or raw_metadata.get("limit_source"),
            "is_byok": cls._optional_bool(
                decoded.get("is_byok"), raw_metadata.get("is_byok")
            ),
            "attempt": attempt_number,
            "retry_after": retry_after,
            "retry_after_seconds": retry_after,
            "attempted_endpoints": attempted,
            "requested_provider_selector": request.route.provider,
            "requested_model": request.route.model,
        }
        return {key: value for key, value in metadata.items() if value is not None}

    @staticmethod
    def _retry_after_seconds(headers: Sequence[tuple[str, str]]) -> float | None:
        value = next((v for k, v in headers if k.lower() == "retry-after"), None)
        if value is None:
            return None
        try:
            seconds = float(value.strip())
            if isfinite(seconds) and seconds >= 0:
                return min(seconds, 60.0)
        except ValueError:
            pass
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=UTC)
            seconds = target.timestamp() - time.time()
            return min(max(0.0, seconds), 60.0)
        except (TypeError, ValueError, OverflowError):
            return None

    def _sleep_before_retry(self, failure: Mapping[str, object], attempt_number: int) -> None:
        retry_after = failure.get("retry_after_seconds")
        base = min(0.25 * (2 ** (attempt_number - 1)), 8.0)
        if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool):
            delay = max(0.0, min(float(retry_after), 60.0))
        else:
            delay = base + max(0.0, min(float(self._retry_jitter(base * 0.25)), base * 0.25))
        self._retry_sleeper(delay)

    def _endpoint_url(self, path: str) -> str:
        if not isinstance(getattr(self.client, "base_url", None), str):
            raise GatewayTransportError("model client must expose a base_url")
        return f"{self.client.base_url.rstrip('/')}/{path.lstrip('/')}"


__all__ = ["GatewayTransportError", "OpenAICompatibleGatewayAdapter"]
