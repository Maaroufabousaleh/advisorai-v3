"""Typed native venue/testnet adapter port.

Network behavior is injected. The adapter refuses a live environment in Phase 2
so tests can exercise request identity and reconnect semantics without credentials.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from advisorai.contracts import Order
from advisorai.execution.paper import VenueAcknowledgement


class NativeTransport(Protocol):
    def submit_order(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...

    def query_order(self, *, client_order_id: str) -> Mapping[str, object] | None: ...

    def list_open_orders(self) -> Sequence[Mapping[str, object]]: ...


@dataclass(frozen=True, slots=True)
class NativeVenueAdapter:
    venue: str
    environment: str
    transport: NativeTransport
    strict_venue: bool = False
    _acknowledgements: dict[UUID, VenueAcknowledgement] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

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
                "order_id": str(order.artifact_id),
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
        acknowledgement = VenueAcknowledgement(
            order_id=order.artifact_id,
            venue_order_id=venue_order_id,
            accepted=response["accepted"],
        )
        if acknowledgement.accepted:
            self._acknowledgements[order.artifact_id] = acknowledgement
        return acknowledgement

    def reconcile(self, order: Order) -> Mapping[str, object] | None:
        return self.transport.query_order(client_order_id=order.idempotency_key)

    def open_orders(self) -> tuple[VenueAcknowledgement, ...]:
        """Return locally resolved acknowledgements for the OMS reconnect port.

        A venue-specific transport may additionally implement ``list_open_orders``;
        those records are deliberately not promoted into local order authority
        until the caller binds their client IDs to known local orders.
        """

        list_open_orders = getattr(self.transport, "list_open_orders", None)
        if callable(list_open_orders):
            for record in list_open_orders():
                if not isinstance(record, Mapping):
                    continue
                raw_order_id = record.get("order_id", record.get("local_order_id"))
                try:
                    order_id = UUID(str(raw_order_id))
                except (TypeError, ValueError):
                    continue
                venue_order_id = str(
                    record.get("venue_order_id", record.get("id", record.get("orderId", "")))
                ).strip()
                if venue_order_id:
                    self._acknowledgements[order_id] = VenueAcknowledgement(
                        order_id=order_id,
                        venue_order_id=venue_order_id,
                        accepted=bool(record.get("accepted", True)),
                    )
        return tuple(self._acknowledgements.values())

    def account_snapshot(self):
        """Return an optional read-only venue account projection.

        The native OMS transport remains the only order authority.  Account
        reconciliation is deliberately an optional capability so existing
        paper fakes and venues that expose balances on a separate API keep the
        minimal submit/query/list contract.
        """

        fetch = getattr(self.transport, "fetch_account_snapshot", None)
        if not callable(fetch):
            raise RuntimeError("native venue transport does not expose account snapshots")
        return fetch()
