"""Paper/testnet-only native venue adapter with explicit ambiguity handling."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from advisorai.contracts import Order


class AmbiguousAcknowledgement(RuntimeError):
    """The request may have reached the venue; reconciliation is required."""


@dataclass(frozen=True, slots=True)
class VenueAcknowledgement:
    order_id: UUID
    venue_order_id: str
    accepted: bool

    def __post_init__(self) -> None:
        if not self.venue_order_id.strip():
            raise ValueError("venue acknowledgement requires an order ID")
        if not isinstance(self.accepted, bool):
            raise ValueError("venue acknowledgement acceptance must be boolean")


class PaperVenueAdapter:
    """Deterministic adapter used by paper/testnet tests; no live credentials exist."""

    def __init__(self, *, venue: str = "approved-paper-venue", strict_venue: bool = False) -> None:
        if not venue.strip():
            raise ValueError("paper venue name is required")
        self.venue = venue
        self.strict_venue = strict_venue
        self._orders: dict[str, VenueAcknowledgement] = {}
        self._order_fingerprints: dict[str, str] = {}
        self._ambiguous_once: set[str] = set()
        self._outage_once = False

    def inject_ambiguous_ack_once(self, idempotency_key: str) -> None:
        if not idempotency_key.strip():
            raise ValueError("idempotency key is required")
        self._ambiguous_once.add(idempotency_key)

    def inject_outage_once(self) -> None:
        self._outage_once = True

    def submit(self, order: Order) -> VenueAcknowledgement:
        if self.strict_venue and order.instrument.venue not in {None, self.venue}:
            raise ValueError(
                f"order instrument venue {order.instrument.venue!r} does not match paper venue"
            )
        if self._outage_once:
            self._outage_once = False
            raise RuntimeError("paper venue outage")
        fingerprint = sha256(
            order.model_dump_json(exclude={"artifact_id", "created_at"}).encode("utf-8")
        ).hexdigest()
        prior = self._orders.get(order.idempotency_key)
        if prior is not None:
            if self._order_fingerprints.get(order.idempotency_key) != fingerprint:
                raise ValueError("idempotency key was reused for a different order")
            return prior
        if order.idempotency_key in self._ambiguous_once:
            self._ambiguous_once.remove(order.idempotency_key)
            self._orders[order.idempotency_key] = VenueAcknowledgement(
                order_id=order.artifact_id,
                venue_order_id=f"paper-{order.artifact_id}",
                accepted=True,
            )
            self._order_fingerprints[order.idempotency_key] = fingerprint
            raise AmbiguousAcknowledgement("venue response was lost; reconcile before retry")
        acknowledgement = VenueAcknowledgement(
            order_id=order.artifact_id,
            venue_order_id=f"paper-{order.artifact_id}",
            accepted=True,
        )
        self._orders[order.idempotency_key] = acknowledgement
        self._order_fingerprints[order.idempotency_key] = fingerprint
        return acknowledgement

    def open_orders(self) -> tuple[VenueAcknowledgement, ...]:
        return tuple(self._orders.values())

    def cancel(self, order: Order) -> bool:
        """Apply the deterministic paper venue cancellation acknowledgement."""

        acknowledgement = self._orders.get(order.idempotency_key)
        if acknowledgement is None:
            return False
        if acknowledgement.order_id != order.artifact_id:
            raise ValueError("paper venue order identity does not match cancellation")
        return acknowledgement.accepted
