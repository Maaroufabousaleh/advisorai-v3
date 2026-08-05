"""ModelGatewayPort implementations for Phase 0 tests and recovery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter_ns

from advisorai.ports import GatewayRequest, GatewayResponse, GatewayRoute, ModelGatewayPort


class GatewayFailure(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GatewayAttempt:
    adapter: str
    route: GatewayRoute
    succeeded: bool
    latency_ms: int
    error: str | None = None


class GatewayRecorder:
    def __init__(self) -> None:
        self.attempts: list[GatewayAttempt] = []

    def record(self, attempt: GatewayAttempt) -> None:
        self.attempts.append(attempt)


class LocalDeterministicGateway:
    """Typed contract/recovery gateway; never represents a production LLM."""

    name = "direct_recovery"

    def __init__(
        self, responder: Callable[[GatewayRequest], dict[str, object]] | None = None
    ) -> None:
        self.responder = responder or (
            lambda request: {"ok": True, "request_hash": request.content_hash()}
        )

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        if request.privacy_class.lower() in {"secret", "credential"}:
            raise GatewayFailure("recovery gateway refuses secret or credential-class requests")
        started = perf_counter_ns()
        payload = self.responder(request)
        return GatewayResponse(
            request_id=request.request_id,
            route=request.route,
            content="typed local recovery response",
            typed_payload=payload,
            latency_ms=max(0, (perf_counter_ns() - started) // 1_000_000),
            input_tokens=0,
            output_tokens=0,
            estimated_cost_usd=0,
            provider_request_id=f"local-{request.request_id}",
        )


class GatewayChain:
    """Pinned ordered routes; fallback is explicit and recorded, never opaque."""

    def __init__(
        self, adapters: tuple[ModelGatewayPort, ...], recorder: GatewayRecorder | None = None
    ) -> None:
        if not adapters:
            raise ValueError("gateway chain requires at least one adapter")
        self.adapters = adapters
        self.recorder = recorder or GatewayRecorder()

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        failures: list[str] = []
        permitted_gateways = {request.route.gateway, *request.route.fallback_chain}
        for adapter in self.adapters:
            started = perf_counter_ns()
            attempt_request = request
            adapter_route = getattr(adapter, "route", None)
            if adapter_route is not None:
                if adapter_route.gateway not in permitted_gateways:
                    error = f"{adapter.name}:route_not_in_fallback_chain:{adapter_route.gateway}"
                    failures.append(error)
                    self.recorder.record(
                        GatewayAttempt(
                            adapter=adapter.name,
                            route=adapter_route,
                            succeeded=False,
                            latency_ms=0,
                            error=error,
                        )
                    )
                    continue
                # Each adapter receives the same typed content, but its pinned
                # route identity is explicit.  This is what makes fallback
                # ordering auditable instead of relying on opaque gateway
                # auto-routing.
                attempt_request = request.model_copy(update={"route": adapter_route})
            try:
                response = adapter.complete(attempt_request)
                if response.request_id != request.request_id:
                    raise GatewayFailure(
                        f"{adapter.name} returned a response for a different request"
                    )
                if response.route != attempt_request.route:
                    raise GatewayFailure(
                        f"{adapter.name} returned a route identity different from its pinned request"
                    )
                if response.route.gateway not in permitted_gateways:
                    raise GatewayFailure(
                        f"{adapter.name} returned an unpinned route {response.route.gateway!r}"
                    )
                elapsed = max(0, (perf_counter_ns() - started) // 1_000_000)
                self.recorder.record(
                    GatewayAttempt(
                        adapter=adapter.name,
                        route=response.route,
                        succeeded=True,
                        latency_ms=elapsed,
                    )
                )
                return response
            except Exception as exc:
                elapsed = max(0, (perf_counter_ns() - started) // 1_000_000)
                failures.append(f"{adapter.name}:{type(exc).__name__}:{exc}")
                self.recorder.record(
                    GatewayAttempt(
                        adapter=adapter.name,
                        route=adapter_route or request.route,
                        succeeded=False,
                        latency_ms=elapsed,
                        error=str(exc),
                    )
                )
        raise GatewayFailure("all pinned gateway routes failed: " + " | ".join(failures))
