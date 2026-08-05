"""Typed native venue/testnet adapter port.

Network behavior is injected. The adapter refuses a live environment in Phase 2
so tests can exercise request identity and reconnect semantics without credentials.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from advisorai.contracts import Order
from advisorai.execution.paper import VenueAcknowledgement


class NativeTransport(Protocol):
    def submit_order(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...

    def query_order(self, *, client_order_id: str) -> Mapping[str, object] | None: ...


@dataclass(frozen=True, slots=True)
class NativeVenueAdapter:
    venue: str
    environment: str
    transport: NativeTransport
    strict_venue: bool = False

    def __post_init__(self) -> None:
        if not self.venue.strip():
            raise ValueError("native venue name is required")
        if self.environment.strip().lower() not in {"paper", "testnet", "paper_testnet"}:
            raise ValueError("Phase 2 native adapter accepts only paper/testnet environments")

    def submit(self, order: Order) -> VenueAcknowledgement:
        if self.strict_venue and order.instrument.venue not in {None, self.venue}:
            raise ValueError(
                f"order instrument venue {order.instrument.venue!r} does not match native venue"
            )
        response = self.transport.submit_order(
            {
                "client_order_id": order.idempotency_key,
                "symbol": order.instrument.venue_symbol or order.instrument.canonical_id,
                "side": order.side,
                "quantity": str(order.quantity),
                "order_type": order.order_type,
                "price": str(order.price) if order.price is not None else None,
                "time_in_force": order.time_in_force,
            }
        )
        if not isinstance(response, Mapping):
            raise RuntimeError("native venue returned a non-mapping acknowledgement")
        venue_order_id = str(response.get("venue_order_id", "")).strip()
        if not venue_order_id or not isinstance(response.get("accepted"), bool):
            raise RuntimeError("native venue rejected order")
        return VenueAcknowledgement(
            order_id=order.artifact_id,
            venue_order_id=venue_order_id,
            accepted=response["accepted"],
        )

    def reconcile(self, order: Order) -> Mapping[str, object] | None:
        return self.transport.query_order(client_order_id=order.idempotency_key)
