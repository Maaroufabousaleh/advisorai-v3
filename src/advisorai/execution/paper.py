"""Paper/testnet-only native venue adapter with explicit ambiguity handling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from advisorai.contracts import Fill, Order
from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers


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


class SimulatedPaperVenueAdapter:
    """Durable, local paper venue with explicit deterministic fill modeling.

    This adapter is intentionally not a testnet/native transport. It has no
    credential or network path. Its independent venue ledger is queried for
    acknowledgements, active orders, fills, and account state during recovery.
    """

    def __init__(
        self,
        ledgers: SqliteLedgers,
        *,
        venue: str = "paper",
        initial_cash: Decimal = Decimal("100000"),
        marks: dict[str, Decimal] | None = None,
        as_of: datetime | None = None,
        auto_fill: bool = False,
        fee_bps: Decimal = Decimal("1"),
    ) -> None:
        if not venue.strip() or initial_cash < 0 or fee_bps < 0:
            raise ValueError("simulated paper venue settings are invalid")
        if not initial_cash.is_finite() or not fee_bps.is_finite():
            raise ValueError("simulated paper venue values must be finite")
        self.ledgers = ledgers
        self.venue = venue.strip()
        self.auto_fill = auto_fill
        self.fee_bps = fee_bps
        self._orders: dict[UUID, Order] = {}
        self._acknowledgements: dict[UUID, VenueAcknowledgement] = {}
        self._fills: dict[str, Fill] = {}
        self._filled_order_ids: set[UUID] = set()
        self._cancelled_order_ids: set[UUID] = set()
        initial_time = as_of or datetime.now(UTC)
        if initial_time.tzinfo is None or initial_time.utcoffset() is None:
            raise ValueError("simulated venue timestamp must include a timezone")
        self._account = _SimulatedVenueAccount(initial_cash, dict(marks or {}), initial_time)
        self._hydrate()

    def _hydrate(self) -> None:
        for event in self.ledgers.events(LedgerNamespace.VENUE):
            if event.event_type == "paper_order_accepted":
                raw_order = event.payload.get("order")
                if not isinstance(raw_order, dict):
                    raise ValueError("paper venue ledger has an invalid order")
                order = Order.model_validate(raw_order)
                acknowledgement = VenueAcknowledgement(
                    order_id=order.artifact_id,
                    venue_order_id=str(event.payload["venue_order_id"]),
                    accepted=True,
                )
                self._orders[order.artifact_id] = order
                self._acknowledgements[order.artifact_id] = acknowledgement
            elif event.event_type == "paper_fill_recorded":
                raw_fill = event.payload.get("fill")
                if not isinstance(raw_fill, dict):
                    raise ValueError("paper venue ledger has an invalid fill")
                fill = Fill.model_validate(raw_fill)
                order = self._orders.get(fill.order_id)
                if order is None:
                    raise ValueError("paper venue fill references an unknown order")
                self._fills[fill.venue_fill_id] = fill
                self._filled_order_ids.add(fill.order_id)
                self._account.apply_fill(fill, order.side, order.instrument.canonical_id)
            elif event.event_type == "paper_order_cancelled":
                self._cancelled_order_ids.add(UUID(str(event.payload["order_id"])))

    def submit(self, order: Order) -> VenueAcknowledgement:
        if order.instrument.venue not in {None, self.venue}:
            raise ValueError("simulated paper venue refuses an order for another venue")
        prior = next(
            (
                item
                for item in self._orders.values()
                if item.idempotency_key == order.idempotency_key
            ),
            None,
        )
        if prior is not None:
            if Order.model_validate(prior).model_dump_json(
                exclude={"artifact_id", "created_at", "state"}
            ) != order.model_dump_json(exclude={"artifact_id", "created_at", "state"}):
                raise ValueError("paper order idempotency key was reused with different content")
            return self._acknowledgements[prior.artifact_id]
        acknowledgement = VenueAcknowledgement(
            order_id=order.artifact_id,
            venue_order_id=f"paper-{order.artifact_id}",
            accepted=True,
        )
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.VENUE,
                event_type="paper_order_accepted",
                idempotency_key=f"paper-venue-order:{order.idempotency_key}",
                payload={
                    "order": order.model_dump(mode="json", round_trip=True),
                    "venue_order_id": acknowledgement.venue_order_id,
                },
            )
        )
        self._orders[order.artifact_id] = order
        self._acknowledgements[order.artifact_id] = acknowledgement
        return acknowledgement

    def reconcile(self, order: Order) -> dict[str, object] | None:
        acknowledgement = self._acknowledgements.get(order.artifact_id)
        if acknowledgement is None:
            return None
        state = (
            "cancelled"
            if order.artifact_id in self._cancelled_order_ids
            else "filled"
            if order.artifact_id in self._filled_order_ids
            else "open"
        )
        return {
            "order_id": str(order.artifact_id),
            "venue_order_id": acknowledgement.venue_order_id,
            "client_order_id": order.idempotency_key,
            "accepted": True,
            "state": state,
        }

    def open_orders(self) -> tuple[VenueAcknowledgement, ...]:
        return tuple(
            acknowledgement
            for order_id, acknowledgement in self._acknowledgements.items()
            if order_id not in self._filled_order_ids and order_id not in self._cancelled_order_ids
        )

    def cancel(self, order: Order) -> bool:
        if order.artifact_id not in self._acknowledgements:
            return False
        if order.artifact_id in self._filled_order_ids:
            return False
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.VENUE,
                event_type="paper_order_cancelled",
                idempotency_key=f"paper-venue-cancel:{order.idempotency_key}",
                payload={"order_id": str(order.artifact_id)},
            )
        )
        self._cancelled_order_ids.add(order.artifact_id)
        return True

    def execute_pending(self, *, at: datetime | None = None) -> tuple[Fill, ...]:
        """Execute accepted orders under this simulator's explicit fill model."""
        if not self.auto_fill:
            return ()
        occurred_at = at or datetime.now(UTC)
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("simulated fill timestamp must include a timezone")
        created: list[Fill] = []
        for order_id, order in sorted(self._orders.items(), key=lambda item: str(item[0])):
            if order_id in self._filled_order_ids or order_id in self._cancelled_order_ids:
                continue
            if order.state.value not in {"routed", "acknowledged"}:
                continue
            price = order.price or self._account.marks.get(order.instrument.canonical_id)
            if price is None:
                continue
            fill_id = uuid5(NAMESPACE_URL, f"advisorai-v3/paper-fill/{order.idempotency_key}")
            venue_fill_id = f"paper-fill-{fill_id}"
            fee = order.quantity * price * self.fee_bps / Decimal("10000")
            fill = Fill(
                artifact_id=fill_id,
                order_id=order_id,
                venue_fill_id=venue_fill_id,
                quantity=order.quantity,
                price=price,
                fee=fee,
                occurred_at=occurred_at,
            )
            stored = self.ledgers.append(
                LedgerEvent(
                    namespace=LedgerNamespace.VENUE,
                    event_type="paper_fill_recorded",
                    idempotency_key=f"paper-venue-fill:{venue_fill_id}",
                    payload={"fill": fill.model_dump(mode="json", round_trip=True)},
                )
            )
            persisted = Fill.model_validate(stored.payload["fill"])
            self._fills[venue_fill_id] = persisted
            self._filled_order_ids.add(order_id)
            self._account.apply_fill(persisted, order.side, order.instrument.canonical_id)
            created.append(persisted)
        return tuple(created)

    def account_snapshot(self):
        from advisorai.execution.reconciliation import VenueAccountSnapshot

        return VenueAccountSnapshot(
            as_of=self._account.as_of,
            cash=self._account.cash,
            positions=dict(self._account.positions),
            venue_open_order_ids=frozenset(item.order_id for item in self.open_orders()),
        )

    @property
    def fills(self) -> tuple[Fill, ...]:
        return tuple(self._fills.values())


class _SimulatedVenueAccount:
    """Minimal account projection owned by the independent paper venue."""

    def __init__(self, cash: Decimal, marks: dict[str, Decimal], as_of: datetime) -> None:
        self.cash = cash
        self.positions: dict[str, Decimal] = {}
        self.marks = marks
        self.as_of = as_of.astimezone(UTC)

    def apply_fill(self, fill: Fill, side: str, instrument_id: str) -> None:
        signed_quantity = fill.quantity if side == "buy" else -fill.quantity
        self.positions[instrument_id] = (
            self.positions.get(instrument_id, Decimal("0")) + signed_quantity
        )
        if self.positions[instrument_id] == 0:
            del self.positions[instrument_id]
        self.cash -= fill.price * signed_quantity + fill.fee
        self.as_of = max(self.as_of, fill.occurred_at.astimezone(UTC))
