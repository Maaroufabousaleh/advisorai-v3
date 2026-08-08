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
from advisorai.ports import (
    GATEWAY_OUTPUT_SCHEMAS,
    GatewayInvocationMode,
    GatewayRequest,
    GatewayRoute,
    GatewayTool,
    GenerationBudget,
    ToolExecutionStatus,
    validate_gateway_output,
)

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


# A request that reaches a concrete remote adapter without an explicit budget
# must still be safe during Phase 0.  Profiled production requests normally
# supply their own reviewed budget; this protects accidental direct use too.
_REMOTE_SAFE_GENERATION_BUDGET = GenerationBudget(
    max_output_tokens=256,
    max_expected_cost_usd=0.001,
    max_billed_cost_usd=0.001,
    timeout_seconds=30,
    maximum_attempts=2,
)


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
        clock: Callable[[], float] = time.monotonic,
        reviewed_token_counter: Callable[[str], int] | None = None,
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
        object.__setattr__(
            self, "_retry_jitter", retry_jitter or (lambda maximum: random.uniform(0, maximum))
        )
        object.__setattr__(self, "_clock", clock)
        object.__setattr__(self, "_reviewed_token_counter", reviewed_token_counter)
        super().__init__(
            name="direct_provider",
            route=route,
            transport=self._complete_payload,
            is_remote=True,
        )

    def _complete_payload(self, request: GatewayRequest) -> Mapping[str, object]:
        budget = self._effective_budget(request)
        payload = self._build_payload(request, budget)
        estimated_input = self._estimate_input_tokens(payload)
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

        self._authorize_pre_dispatch_cost(request, budget, estimated_input)

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        if request.route.gateway.lower() == "openrouter":
            # OpenRouter only returns the selected endpoint/provider when this
            # documented opt-in header is enabled.
            headers["X-OpenRouter-Metadata"] = "enabled"

        max_attempts = min(budget.maximum_attempts, 3)
        deadline = self._clock() + budget.timeout_seconds
        attempt_metadata: list[dict[str, object]] = []
        for attempt_number in range(1, max_attempts + 1):
            remaining = deadline - self._clock()
            if remaining <= 0:
                failure = {
                    "error_type": "deadline_exhausted",
                    "attempt": attempt_number,
                    "timeout_seconds": budget.timeout_seconds,
                }
                if failure not in attempt_metadata:
                    attempt_metadata.append(failure)
                raise GatewayTransportError(
                    "direct model gateway total deadline exhausted",
                    failure_metadata=failure,
                    attempt_metadata=attempt_metadata,
                    no_cross_provider_fallback=True,
                )
            try:
                response = self.client.post_json(
                    self._endpoint_url(self.endpoint_path),
                    payload,
                    headers=headers,
                    max_retries=0,
                    timeout_seconds=remaining,
                )
                if self._clock() > deadline:
                    failure = {
                        "error_type": "deadline_exhausted",
                        "attempt": attempt_number,
                        "timeout_seconds": budget.timeout_seconds,
                    }
                    attempt_metadata.append(failure)
                    raise GatewayTransportError(
                        "direct model gateway total deadline exhausted",
                        failure_metadata=failure,
                        attempt_metadata=attempt_metadata,
                    )
                decoded = json.loads(response.body)
                if not isinstance(decoded, Mapping):
                    raise GatewayTransportError(
                        "direct model gateway returned a non-object response",
                        failure_metadata={
                            "error_type": "invalid_response",
                            "attempt": attempt_number,
                        },
                        attempt_metadata=attempt_metadata,
                    )
                identity = self._parse_routing_identity(decoded, request, attempt_number)
                result = self._parse_success(
                    decoded,
                    request,
                    identity,
                    budget,
                    estimated_input,
                )
                result["routing_attempt"] = identity["routing_attempt"] or attempt_number
                result["attempt_metadata"] = tuple(attempt_metadata)
                return result
            except HttpTransportError as exc:
                failure = self._failure_metadata(exc, request, attempt_number)
                attempt_metadata.append(failure)
                status = exc.status_code
                if deadline - self._clock() <= 0:
                    failure["deadline_exhausted"] = True
                    raise GatewayTransportError(
                        "direct model gateway total deadline exhausted",
                        status_code=status,
                        failure_metadata=failure,
                        attempt_metadata=attempt_metadata,
                        no_cross_provider_fallback=True,
                    ) from exc
                if status in {429, 503} and attempt_number < max_attempts:
                    delay = self._retry_delay(failure, attempt_number)
                    remaining = deadline - self._clock()
                    if remaining <= 0 or delay >= remaining:
                        failure["deadline_exhausted"] = True
                        failure["retry_delay_seconds"] = delay
                        raise GatewayTransportError(
                            "direct model gateway total deadline exhausted before retry",
                            status_code=status,
                            failure_metadata=failure,
                            attempt_metadata=attempt_metadata,
                            no_cross_provider_fallback=True,
                        ) from exc
                    self._retry_sleeper(delay)
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

    def _build_payload(
        self,
        request: GatewayRequest,
        budget: GenerationBudget,
    ) -> dict[str, Any]:
        self._validate_invocation_capabilities(request)
        payload: dict[str, Any] = {
            "model": request.route.model,
            "messages": [item.model_dump(mode="json") for item in request.messages],
            "temperature": 0,
            "stream": False,
            "max_tokens": budget.max_output_tokens,
        }
        # Provider routing options are accepted only as a policy-produced
        # mapping.  Generation controls are always written after the merge so
        # a caller cannot override the budget or prompt/tool contents.
        if request.provider_options:
            options = dict(request.provider_options)
            # Generation controls belong solely to GenerationBudget and the
            # invocation contract.  Preserve harmless provider routing
            # options, but discard alternate token-cap spellings before the
            # final, policy-owned controls are written below.
            for controlled_key in (
                "max_tokens",
                "max_completion_tokens",
                "temperature",
                "stream",
                "response_format",
                "tool_choice",
            ):
                options.pop(controlled_key, None)
            provider_options = options.get("provider")
            if isinstance(provider_options, Mapping):
                governed_provider = dict(provider_options)
                if "only" in governed_provider and governed_provider["only"] != [
                    request.route.provider
                ]:
                    raise GatewayTransportError(
                        "provider selector override is not permitted",
                        failure_metadata={"error_type": "routing_policy_override"},
                    )
                if (
                    "allow_fallbacks" in governed_provider
                    and governed_provider["allow_fallbacks"] is not False
                ):
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
                if (
                    "require_parameters" in governed_provider
                    and governed_provider["require_parameters"] is not True
                ):
                    raise GatewayTransportError(
                        "provider parameter requirement override is not permitted",
                        failure_metadata={"error_type": "routing_policy_override"},
                    )
                options["provider"] = governed_provider
            payload.update(options)
        payload.update(
            {
                "model": request.route.model,
                "messages": [item.model_dump(mode="json") for item in request.messages],
                "temperature": 0,
                "stream": False,
                "max_tokens": budget.max_output_tokens,
            }
        )
        # Tool calls and strict structured output are independent endpoint
        # capabilities.  Do not send both unless the frozen RouteProfile
        # admission explicitly enabled the combination.
        if request.invocation_mode is GatewayInvocationMode.STRUCTURED_OUTPUT or (
            request.invocation_mode is not GatewayInvocationMode.STRUCTURED_OUTPUT
            and request.response_format_with_tools_admitted
        ):
            payload["response_format"] = self._response_format(request)
        else:
            payload.pop("response_format", None)
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
        if request.invocation_mode is GatewayInvocationMode.TOOL_REQUIRED:
            payload["tool_choice"] = "required"
        else:
            payload.pop("tool_choice", None)
        return payload

    @staticmethod
    def _effective_budget(request: GatewayRequest) -> GenerationBudget:
        # A concrete adapter is always remote, including its direct test/use
        # path.  Its implicit budget must never silently become the general
        # $1 port default merely because provider options were omitted.
        if "generation_budget" not in request.model_fields_set:
            return _REMOTE_SAFE_GENERATION_BUDGET
        return request.generation_budget

    @staticmethod
    def _validate_invocation_capabilities(request: GatewayRequest) -> None:
        if request.response_format_with_tools_admitted and (
            not request.admission_capabilities_enforced
            or request.admission_supports_tools is not True
            or request.admission_supports_structured_output is not True
        ):
            raise GatewayTransportError(
                "combined tools and structured output require frozen endpoint admission",
                failure_metadata={"error_type": "unsupported_invocation_mode"},
            )
        if request.invocation_mode is GatewayInvocationMode.TOOL_REQUIRED and (
            not request.admission_capabilities_enforced
            or request.admission_supports_tool_choice_required is not True
        ):
            raise GatewayTransportError(
                "required tool choice requires frozen endpoint admission support",
                failure_metadata={"error_type": "unsupported_tool_choice"},
            )
        if not request.admission_capabilities_enforced:
            return
        if request.invocation_mode is GatewayInvocationMode.STRUCTURED_OUTPUT:
            if request.admission_supports_structured_output is not True:
                raise GatewayTransportError(
                    "endpoint admission does not support structured output",
                    failure_metadata={"error_type": "unsupported_invocation_mode"},
                )
            return
        if request.admission_supports_tools is not True:
            raise GatewayTransportError(
                "endpoint admission does not support tools",
                failure_metadata={"error_type": "unsupported_invocation_mode"},
            )

    def _authorize_pre_dispatch_cost(
        self,
        request: GatewayRequest,
        budget: GenerationBudget,
        estimated_input_tokens: int,
    ) -> None:
        provider = request.provider_options.get("provider")
        provider = provider if isinstance(provider, Mapping) else {}
        max_price = provider.get("max_price") if isinstance(provider, Mapping) else None
        max_price = max_price if isinstance(max_price, Mapping) else {}
        input_price = self._optional_number(
            max_price.get("prompt", self._input_price_per_million), "provider.max_price.prompt"
        )
        output_price = self._optional_number(
            max_price.get("completion", self._output_price_per_million),
            "provider.max_price.completion",
        )
        request_price = self._optional_number(
            max_price.get("request", self._request_price_usd), "provider.max_price.request"
        )
        paid_or_admitted_remote = (
            provider.get("zdr") is True
            or request.admission_capabilities_enforced
            or isinstance(provider.get("max_price"), Mapping)
        )
        if paid_or_admitted_remote and (
            input_price is None or output_price is None or request_price is None
        ):
            raise GatewayTransportError(
                "paid or admitted remote route omitted admitted price limits",
                failure_metadata={"error_type": "missing_admitted_prices"},
                no_cross_provider_fallback=True,
            )
        if input_price is None or output_price is None:
            return
        maximum = (
            estimated_input_tokens * input_price / 1_000_000
            + budget.max_output_tokens * output_price / 1_000_000
            + (request_price or 0.0)
        )
        if maximum > budget.max_expected_cost_usd:
            raise GatewayTransportError(
                "direct model gateway maximum expected cost exceeds generation budget",
                failure_metadata={
                    "error_type": "budget_exceeded",
                    "budget": "max_expected_cost_usd",
                    "estimated_input_tokens": estimated_input_tokens,
                    "max_output_tokens": budget.max_output_tokens,
                    "maximum_expected_cost_usd": maximum,
                    "max_expected_cost_usd": budget.max_expected_cost_usd,
                },
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

    def _estimate_input_tokens(self, payload: Mapping[str, object]) -> int:
        """Estimate all provider-visible request bytes before network access.

        A reviewed route can inject its tokenizer.  Without one, count every
        serialized byte as a possible token and add a 25% margin.  This is
        intentionally pessimistic for JSON-heavy tool and schema payloads;
        an estimate must never make a budget authorization less strict.
        """

        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        counter = self._reviewed_token_counter
        if counter is not None:
            counted = counter(serialized)
            if isinstance(counted, bool) or not isinstance(counted, int) or counted < 1:
                raise GatewayTransportError(
                    "reviewed tokenizer returned an invalid token count",
                    failure_metadata={"error_type": "invalid_tokenizer_result"},
                )
            return counted
        serialized_bytes = len(serialized.encode("utf-8"))
        return max(1, ceil(serialized_bytes * 1.25))

    def _parse_success(
        self,
        decoded: Mapping[str, object],
        request: GatewayRequest,
        identity: Mapping[str, object],
        budget: GenerationBudget,
        estimated_input_tokens: int,
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
            if not request.tools:
                raise GatewayTransportError(
                    "direct model gateway returned unexpected tool calls",
                    failure_metadata={"error_type": "unexpected_tool_calls"},
                )
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
                calls.append(
                    {"name": function["name"], "arguments": function.get("arguments", "{}")}
                )
        if calls:
            self._validate_returned_tool_calls(request, calls)
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", "")) for part in content if isinstance(part, Mapping)
            )
        if content is None and calls:
            content = ""
        if request.invocation_mode is GatewayInvocationMode.TOOL_REQUIRED and not calls:
            raise GatewayTransportError(
                "required-tool response omitted a tool call",
                failure_metadata={"error_type": "missing_required_tool_call"},
            )
        if not isinstance(content, str) or (not content.strip() and not calls):
            raise GatewayTransportError(
                "direct model gateway response content is blank",
                failure_metadata={"error_type": "blank_content"},
            )
        typed_payload: Mapping[str, object] | None = None
        # A valid tool call is sufficient in either tool mode.  Providers may
        # include explanatory text beside one, and treating that text as a
        # mandatory JSON payload would incorrectly reject an otherwise
        # reviewed call.  Structured-output responses, and optional-tool
        # responses without a call, still require typed JSON.
        parse_typed_content = content.strip() and (
            request.invocation_mode is GatewayInvocationMode.STRUCTURED_OUTPUT
            or (request.invocation_mode is GatewayInvocationMode.TOOL_OPTIONAL and not calls)
            or (request.route.schema_mode == "typed_json" and not calls)
        )
        if parse_typed_content:
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
            try:
                typed_payload = validate_gateway_output(request.output_kind, parsed)
            except (TypeError, ValueError) as exc:
                raise GatewayTransportError(
                    "direct model gateway typed output violates the selected schema",
                    failure_metadata={"error_type": "invalid_typed_output"},
                ) from exc
        if (
            request.invocation_mode is GatewayInvocationMode.TOOL_OPTIONAL
            and not calls
            and typed_payload is None
        ):
            raise GatewayTransportError(
                "optional-tool response omitted both a tool call and typed output",
                failure_metadata={"error_type": "invalid_optional_tool_response"},
            )
        usage = decoded.get("usage")
        usage = usage if isinstance(usage, Mapping) else {}
        billed_cost = self._optional_number(usage.get("cost"), "usage.cost")
        input_tokens = self._usage_int(usage.get("prompt_tokens"), "prompt_tokens")
        output_tokens = self._usage_int(usage.get("completion_tokens"), "completion_tokens")
        if output_tokens > budget.max_output_tokens:
            raise GatewayTransportError(
                "direct model gateway output exceeds generation budget",
                failure_metadata={
                    "error_type": "budget_exceeded",
                    "budget": "max_output_tokens",
                    "output_tokens": output_tokens,
                    "max_output_tokens": budget.max_output_tokens,
                },
            )
        if budget.max_input_tokens is not None and input_tokens > budget.max_input_tokens:
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
        if expected_cost > budget.max_expected_cost_usd:
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
        if billed_cost is not None and billed_cost > budget.max_billed_cost_usd:
            raise GatewayTransportError(
                "direct model gateway billed cost exceeds generation budget",
                failure_metadata={
                    "error_type": "budget_exceeded",
                    "budget": "max_billed_cost_usd",
                    "billed_cost_usd": billed_cost,
                    "max_billed_cost_usd": budget.max_billed_cost_usd,
                },
            )
        cost_details = usage.get("cost_details")
        cost_metadata: dict[str, object] = {
            "source": "openrouter.usage.cost"
            if request.route.gateway.lower() == "openrouter" and billed_cost is not None
            else "provider.usage",
            "usage": dict(usage),
            "usage_cost_usd": billed_cost,
            "estimated_input_tokens": estimated_input_tokens,
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
            "invocation_mode": request.invocation_mode,
            "tool_used": bool(calls),
            "tool_called": bool(calls),
            "tool_execution_status": ToolExecutionStatus.NOT_EXECUTED,
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
    def _validate_returned_tool_calls(
        cls,
        request: GatewayRequest,
        calls: Sequence[Mapping[str, object]],
    ) -> None:
        reviewed_tools: dict[str, GatewayTool] = {
            tool.name.strip().lower(): tool for tool in request.tools
        }
        for call in calls:
            name = call.get("name")
            if not isinstance(name, str) or name.strip().lower() not in reviewed_tools:
                raise GatewayTransportError(
                    "direct model gateway returned an unreviewed tool",
                    failure_metadata={"error_type": "unreviewed_tool_call"},
                )
            arguments = call.get("arguments")
            if isinstance(arguments, Mapping):
                parsed_arguments: Mapping[str, object] = arguments
            elif isinstance(arguments, str):
                try:
                    decoded_arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise GatewayTransportError(
                        "direct model gateway tool arguments are malformed JSON",
                        failure_metadata={"error_type": "malformed_tool_arguments"},
                    ) from exc
                if not isinstance(decoded_arguments, Mapping):
                    raise GatewayTransportError(
                        "direct model gateway tool arguments must be an object",
                        failure_metadata={"error_type": "invalid_tool_arguments"},
                    )
                parsed_arguments = decoded_arguments
            else:
                raise GatewayTransportError(
                    "direct model gateway tool arguments are required",
                    failure_metadata={"error_type": "invalid_tool_arguments"},
                )
            cls._validate_tool_schema(
                parsed_arguments,
                reviewed_tools[name.strip().lower()].input_schema,
            )

    @classmethod
    def _validate_tool_schema(
        cls,
        value: object,
        schema: Mapping[str, object],
        path: str = "$",
    ) -> None:
        schema_type = schema.get("type")
        if schema_type == "object":
            if not isinstance(value, Mapping):
                raise GatewayTransportError(
                    f"tool arguments at {path} must be an object",
                    failure_metadata={"error_type": "invalid_tool_arguments"},
                )
            properties = schema.get("properties", {})
            if not isinstance(properties, Mapping):
                raise GatewayTransportError(
                    "reviewed tool schema properties are invalid",
                    failure_metadata={"error_type": "invalid_tool_schema"},
                )
            required = schema.get("required", ())
            for name in required if isinstance(required, (list, tuple)) else ():
                if name not in value:
                    raise GatewayTransportError(
                        f"tool arguments missing required field: {name}",
                        failure_metadata={"error_type": "invalid_tool_arguments"},
                    )
            if schema.get("additionalProperties", True) is False:
                unknown = set(value) - set(properties)
                if unknown:
                    raise GatewayTransportError(
                        "tool arguments contain unknown fields",
                        failure_metadata={"error_type": "invalid_tool_arguments"},
                    )
            for name, child_schema in properties.items():
                if name in value and isinstance(child_schema, Mapping):
                    cls._validate_tool_schema(value[name], child_schema, f"{path}.{name}")
            return
        if schema_type == "array":
            if not isinstance(value, (list, tuple)):
                raise GatewayTransportError(
                    f"tool arguments at {path} must be an array",
                    failure_metadata={"error_type": "invalid_tool_arguments"},
                )
            item_schema = schema.get("items")
            if isinstance(item_schema, Mapping):
                for index, item in enumerate(value):
                    cls._validate_tool_schema(item, item_schema, f"{path}[{index}]")
            return
        if schema_type == "string" and not isinstance(value, str):
            raise GatewayTransportError(
                f"tool arguments at {path} must be a string",
                failure_metadata={"error_type": "invalid_tool_arguments"},
            )
        if schema_type == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            raise GatewayTransportError(
                f"tool arguments at {path} must be an integer",
                failure_metadata={"error_type": "invalid_tool_arguments"},
            )
        if schema_type == "number" and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise GatewayTransportError(
                f"tool arguments at {path} must be a number",
                failure_metadata={"error_type": "invalid_tool_arguments"},
            )
        if schema_type == "boolean" and not isinstance(value, bool):
            raise GatewayTransportError(
                f"tool arguments at {path} must be boolean",
                failure_metadata={"error_type": "invalid_tool_arguments"},
            )
        allowed = schema.get("enum")
        if isinstance(allowed, (list, tuple)) and value not in allowed:
            raise GatewayTransportError(
                f"tool arguments at {path} contain a value outside the enum",
                failure_metadata={"error_type": "invalid_tool_arguments"},
            )

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
                    failure_metadata={
                        "error_type": "missing_routing_metadata",
                        "attempt": attempt_number,
                    },
                )
            endpoints = raw_metadata.get("endpoints")
            available = endpoints.get("available") if isinstance(endpoints, Mapping) else None
            if not isinstance(available, list):
                raise GatewayTransportError(
                    "OpenRouter metadata omitted endpoint candidates",
                    failure_metadata={
                        "error_type": "missing_endpoint_candidates",
                        "attempt": attempt_number,
                    },
                )
            selected = [
                item
                for item in available
                if isinstance(item, Mapping) and item.get("selected") is True
            ]
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
                    failure_metadata={
                        "error_type": "missing_provider_identity",
                        "attempt": attempt_number,
                    },
                )
            if not isinstance(model, str) or not model.strip():
                raise GatewayTransportError(
                    "OpenRouter metadata omitted the selected model tag",
                    failure_metadata={
                        "error_type": "missing_resolved_model",
                        "attempt": attempt_number,
                    },
                )
            if not isinstance(top_level_model, str) or not top_level_model.strip():
                raise GatewayTransportError(
                    "OpenRouter response omitted top-level model",
                    failure_metadata={
                        "error_type": "missing_top_level_model",
                        "attempt": attempt_number,
                    },
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
                if isinstance(raw_metadata.get("attempt"), int)
                and not isinstance(raw_metadata.get("attempt"), bool)
                else attempt_number,
                "is_byok": cls._optional_bool(
                    endpoint.get("is_byok"), raw_metadata.get("is_byok"), decoded.get("is_byok")
                ),
                "routing_metadata": dict(raw_metadata),
            }

        provider = decoded.get("provider")
        model = decoded.get("model")
        endpoint_variant = decoded.get("provider_variant") or decoded.get("actual_endpoint_variant")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (provider, model, endpoint_variant)
        ):
            raise GatewayTransportError(
                "provider response omitted exact routing identity metadata",
                failure_metadata={
                    "error_type": "missing_routing_identity",
                    "attempt": attempt_number,
                },
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
                    for key in (
                        "provider",
                        "provider_name",
                        "model",
                        "resolved_model",
                        "selected",
                        "is_byok",
                    )
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
            raise GatewayTransportError(
                f"direct model gateway usage {field} must be a non-negative integer"
            )
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
            raise GatewayTransportError(
                f"direct model gateway {field} must be finite and non-negative"
            )
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
        error_payload = decoded.get("error")
        error_payload = error_payload if isinstance(error_payload, Mapping) else {}
        nested_error = error_payload.get("error")
        nested_error = nested_error if isinstance(nested_error, Mapping) else {}

        # OpenRouter error payloads have appeared in all four of these forms.
        # Read only whitelisted routing values; error messages, user IDs and
        # opaque provider payloads must never reach a ledger record.
        raw_metadata = next(
            (
                value
                for value in (
                    decoded.get("openrouter_metadata"),
                    error_payload.get("openrouter_metadata"),
                    nested_error.get("metadata"),
                    error_payload.get("metadata"),
                )
                if isinstance(value, Mapping) and isinstance(value.get("endpoints"), Mapping)
            ),
            {},
        )
        metadata_sources = tuple(
            value
            for value in (
                nested_error.get("metadata"),
                error_payload.get("metadata"),
                error_payload.get("openrouter_metadata"),
                decoded.get("openrouter_metadata"),
                nested_error,
                error_payload,
                decoded,
            )
            if isinstance(value, Mapping)
        )
        endpoints = raw_metadata.get("endpoints")
        available = endpoints.get("available") if isinstance(endpoints, Mapping) else None
        attempted = cls._safe_endpoint_attempts(available) if isinstance(available, list) else ()
        provider_name = cls._first_text(
            *(source.get("provider_name") for source in metadata_sources),
            *(source.get("provider") for source in metadata_sources),
            *(item.get("provider") or item.get("provider_name") for item in attempted),
        )
        resolved_model = cls._first_text(
            *(source.get("resolved_model") for source in metadata_sources),
            *(source.get("attempted_model") for source in metadata_sources),
            *(source.get("model") for source in metadata_sources),
            *(item.get("model") or item.get("resolved_model") for item in attempted),
        )
        route_attempt = cls._first_positive_int(
            *(source.get("routing_attempt") for source in metadata_sources),
            *(source.get("attempt") for source in metadata_sources),
        )
        provider_code = cls._first_safe_scalar(
            nested_error.get("provider_code"),
            nested_error.get("code"),
            *(source.get("provider_code") for source in metadata_sources),
            *(source.get("code") for source in metadata_sources),
            decoded.get("code"),
        )
        raw_classification = cls._first_text(
            nested_error.get("raw_provider_error_classification"),
            nested_error.get("provider_error_classification"),
            nested_error.get("error_type"),
            nested_error.get("type"),
            *(source.get("raw_provider_error_classification") for source in metadata_sources),
            *(source.get("provider_error_classification") for source in metadata_sources),
            *(source.get("error_type") for source in metadata_sources),
            error_payload.get("type"),
        )
        limit_source = cls._first_text(
            *(source.get("limit_source") for source in metadata_sources),
        )
        is_byok = cls._optional_bool(
            *(source.get("is_byok") for source in metadata_sources),
        )
        retry_after = cls._retry_after_seconds(error.response_headers)
        metadata: dict[str, object] = {
            "http_status": error.status_code,
            "status_code": error.status_code,
            "error_type": raw_classification
            or error.error_type
            or f"http_{error.status_code or 'transport'}",
            "raw_provider_error_classification": raw_classification,
            "provider_code": provider_code,
            "provider_name": provider_name,
            "resolved_model": resolved_model,
            "limit_source": limit_source,
            "is_byok": is_byok,
            "attempt": route_attempt or attempt_number,
            "retry_after": retry_after,
            "retry_after_seconds": retry_after,
            "attempted_endpoints": attempted,
            "requested_provider_selector": request.route.provider,
            "requested_model": request.route.model,
        }
        return {key: value for key, value in metadata.items() if value is not None}

    @staticmethod
    def _first_text(*values: object) -> str | None:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _first_safe_scalar(*values: object) -> str | int | float | bool | None:
        for value in values:
            if isinstance(value, (str, int, float, bool)) and not isinstance(value, bytes):
                return value
        return None

    @staticmethod
    def _first_positive_int(*values: object) -> int | None:
        for value in values:
            if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
                return value
        return None

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

    def _retry_delay(self, failure: Mapping[str, object], attempt_number: int) -> float:
        retry_after = failure.get("retry_after_seconds")
        base = min(0.25 * (2 ** (attempt_number - 1)), 8.0)
        if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool):
            delay = max(0.0, min(float(retry_after), 60.0))
        else:
            delay = base + max(0.0, min(float(self._retry_jitter(base * 0.25)), base * 0.25))
        return delay

    def _endpoint_url(self, path: str) -> str:
        if not isinstance(getattr(self.client, "base_url", None), str):
            raise GatewayTransportError("model client must expose a base_url")
        return f"{self.client.base_url.rstrip('/')}/{path.lstrip('/')}"


__all__ = ["GatewayTransportError", "OpenAICompatibleGatewayAdapter"]
