"""Idempotent paper OMS state machine."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from advisorai.contracts import Fill, Order, OrderState, RiskDecision, RiskOutcome
from advisorai.execution.paper import (
    AmbiguousAcknowledgement,
    PaperVenueAdapter,
    VenueAcknowledgement,
)
from advisorai.execution.risk import OrderRiskCheck
from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers
from advisorai.ports import EventBusPort, EventEnvelope


class OrderStateError(RuntimeError):
    pass


ALLOWED_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.CREATED: frozenset(
        {OrderState.RISK_APPROVED, OrderState.REJECTED, OrderState.EXPIRED}
    ),
    OrderState.RISK_APPROVED: frozenset(
        {OrderState.ROUTED, OrderState.REJECTED, OrderState.EXPIRED}
    ),
    OrderState.ROUTED: frozenset(
        {OrderState.ACKNOWLEDGED, OrderState.REJECTED, OrderState.EXPIRED}
    ),
    OrderState.ACKNOWLEDGED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCEL_PENDING,
            OrderState.EXPIRED,
        }
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.CANCEL_PENDING}
    ),
    OrderState.CANCEL_PENDING: frozenset(
        {OrderState.CANCELLED, OrderState.PARTIALLY_FILLED, OrderState.FILLED}
    ),
    OrderState.FILLED: frozenset({OrderState.RECONCILED}),
    OrderState.CANCELLED: frozenset({OrderState.RECONCILED}),
    OrderState.REJECTED: frozenset({OrderState.RECONCILED}),
    OrderState.EXPIRED: frozenset({OrderState.RECONCILED}),
    OrderState.RECONCILED: frozenset(),
}


class OrderManager:
    def __init__(
        self,
        ledgers: SqliteLedgers,
        adapter: PaperVenueAdapter,
        *,
        event_bus: EventBusPort | None = None,
    ) -> None:
        self.ledgers = ledgers
        self.adapter = adapter
        self.event_bus = event_bus
        self.orders: dict[UUID, Order] = {}
        self.fills: dict[str, Fill] = {}
        self.ambiguous_orders: set[UUID] = set()
        self._hydrate()

    def _hydrate(self) -> None:
        """Rebuild the in-memory projection from the append-only order ledger."""

        for event in self.ledgers.events(LedgerNamespace.ORDER):
            artifact = event.payload.get("artifact")
            if not isinstance(artifact, dict):
                continue
            try:
                if event.event_type.startswith("order_"):
                    order = Order.model_validate(artifact)
                    prior = self.orders.get(order.artifact_id)
                    if prior is None:
                        if event.event_type != "order_created":
                            raise OrderStateError(
                                "order ledger contains a transition before order creation"
                            )
                        if order.state is not OrderState.CREATED:
                            raise OrderStateError(
                                "order creation ledger entry has a non-created state"
                            )
                    elif (
                        event.event_type != "order_ack_ambiguous"
                        and order.state not in ALLOWED_TRANSITIONS[prior.state]
                    ):
                        raise OrderStateError(
                            f"order ledger contains invalid transition {prior.state.value}->{order.state.value}"
                        )
                    self.orders[order.artifact_id] = order
                    if event.event_type == "order_ack_ambiguous":
                        self.ambiguous_orders.add(order.artifact_id)
                    elif event.event_type == "order_acknowledged":
                        self.ambiguous_orders.discard(order.artifact_id)
                elif event.event_type == "fill_recorded":
                    fill = Fill.model_validate(artifact)
                    if fill.order_id not in self.orders:
                        raise OrderStateError("fill ledger entry precedes its order")
                    self.fills[fill.venue_fill_id] = fill
            except Exception as exc:
                raise OrderStateError("order ledger contains an invalid artifact") from exc

    def create(self, order: Order) -> Order:
        existing_artifact = self.orders.get(order.artifact_id)
        if existing_artifact is not None:
            if self._logical_payload(existing_artifact) != self._logical_payload(order):
                raise OrderStateError("order artifact ID is immutable")
            self._republish_key(
                f"order:{existing_artifact.idempotency_key}:order_created",
                event_type="order_created",
                artifact_id=existing_artifact.artifact_id,
            )
            return existing_artifact
        for prior in self.orders.values():
            if prior.idempotency_key == order.idempotency_key:
                if self._logical_payload(prior) != self._logical_payload(order):
                    raise OrderStateError("idempotency key reused for different order")
                self._republish_key(
                    f"order:{order.idempotency_key}:order_created",
                    event_type="order_created",
                    artifact_id=prior.artifact_id,
                )
                return prior
        stored_event = self._record(order, "order_created", publish=False)
        self.orders[order.artifact_id] = order
        self._publish_event(stored_event, event_type="order_created", artifact_id=order.artifact_id)
        return order

    def transition(self, order_id: UUID, new_state: OrderState) -> Order:
        order = self._get(order_id)
        if new_state is order.state:
            self._republish_key(
                f"order:{order.idempotency_key}:order_{new_state.value}",
                event_type=f"order_{new_state.value}",
                artifact_id=order.artifact_id,
            )
            return order
        if new_state not in ALLOWED_TRANSITIONS[order.state]:
            raise OrderStateError(
                f"invalid order transition {order.state.value}->{new_state.value}"
            )
        updated = order.model_copy(update={"state": new_state})
        stored_event = self._record(updated, f"order_{new_state.value}", publish=False)
        self.orders[order_id] = updated
        self._publish_event(
            stored_event, event_type=f"order_{new_state.value}", artifact_id=updated.artifact_id
        )
        return updated

    def approve_risk(
        self,
        order_id: UUID,
        decision: RiskDecision,
        *,
        order_check: OrderRiskCheck | None = None,
    ) -> Order:
        if decision.outcome is not RiskOutcome.APPROVED:
            raise OrderStateError("OMS cannot risk-approve a rejected or reduced decision")
        if order_check is None:
            raise OrderStateError("OMS requires a deterministic order-level risk check")
        if not order_check.approved:
            raise OrderStateError(
                "OMS cannot risk-approve an order rejected by the deterministic risk check"
            )
        if order_check.authoritative_state_hash is None:
            raise OrderStateError("order-level risk check must bind authoritative state")
        if (
            order_check.risk_policy_id is not None
            and decision.risk_policy_id != order_check.risk_policy_id
        ):
            raise OrderStateError("risk decision policy does not match order-level risk check")
        if decision.authoritative_state_hash != order_check.authoritative_state_hash:
            raise OrderStateError("risk decision state hash does not match order-level check")
        return self.transition(order_id, OrderState.RISK_APPROVED)

    def route(self, order_id: UUID) -> VenueAcknowledgement | None:
        if order_id in self.ambiguous_orders:
            raise OrderStateError(
                "invalid order transition: ambiguous acknowledgement requires venue reconciliation before retry"
            )
        if self._get(order_id).state is OrderState.ROUTED:
            raise OrderStateError(
                "invalid order transition: routed order requires venue reconciliation before retry"
            )
        order = self.transition(order_id, OrderState.ROUTED)
        try:
            acknowledgement = self.adapter.submit(order)
        except AmbiguousAcknowledgement:
            self.ambiguous_orders.add(order.artifact_id)
            stored_event = self._record(order, "order_ack_ambiguous", publish=False)
            self._publish_event(
                stored_event, event_type="order_ack_ambiguous", artifact_id=order.artifact_id
            )
            return None
        if acknowledgement.order_id != order.artifact_id:
            raise OrderStateError("venue acknowledgement references a different order")
        self.transition(
            order.artifact_id,
            OrderState.ACKNOWLEDGED if acknowledgement.accepted else OrderState.REJECTED,
        )
        return acknowledgement

    def reconcile_ambiguous(self, order_id: UUID) -> VenueAcknowledgement:
        if order_id not in self.ambiguous_orders:
            raise OrderStateError("order is not awaiting ambiguous acknowledgement reconciliation")
        matches = [item for item in self.adapter.open_orders() if item.order_id == order_id]
        if not matches:
            raise OrderStateError(
                "venue has no matching order; operator must investigate before retry"
            )
        self.transition(order_id, OrderState.ACKNOWLEDGED)
        self.ambiguous_orders.remove(order_id)
        return matches[0]

    def reconcile_routed(self, order_id: UUID) -> VenueAcknowledgement:
        """Resolve a lost normal acknowledgement after reconnect, never resubmit blindly."""

        order = self._get(order_id)
        if order.state is not OrderState.ROUTED:
            raise OrderStateError("only routed orders require reconnect reconciliation")
        matches = [item for item in self.adapter.open_orders() if item.order_id == order_id]
        if not matches:
            raise OrderStateError("venue has no matching routed order")
        self.transition(order_id, OrderState.ACKNOWLEDGED)
        return matches[0]

    def expire_unacknowledged(self, order_id: UUID) -> Order:
        """Close a routed order only after an explicit failed reconciliation."""

        order = self._get(order_id)
        if order.state is not OrderState.ROUTED:
            raise OrderStateError("only routed orders may be expired as unacknowledged")
        return self.transition(order_id, OrderState.EXPIRED)

    def record_fill(self, fill: Fill, side: Literal["buy", "sell"]) -> Order:
        side = side.lower()
        if side not in {"buy", "sell"}:
            raise OrderStateError("fill side must be buy or sell")
        existing_artifact = next(
            (item for item in self.fills.values() if item.artifact_id == fill.artifact_id),
            None,
        )
        if existing_artifact is not None and self._logical_payload(
            existing_artifact
        ) != self._logical_payload(fill):
            raise OrderStateError("fill artifact ID is immutable")
        prior_fill = self.fills.get(fill.venue_fill_id)
        if prior_fill is not None and self._logical_payload(prior_fill) != self._logical_payload(
            fill
        ):
            raise OrderStateError("venue fill ID reused for different fill content")
        order = self._get(fill.order_id)
        if order.side != side:
            raise OrderStateError("fill side does not match the canonical order")
        filled_quantity = sum(
            item.quantity for item in self.fills.values() if item.order_id == order.artifact_id
        )
        effective_filled_quantity = (
            filled_quantity if prior_fill is not None else filled_quantity + fill.quantity
        )
        if effective_filled_quantity > order.quantity:
            raise OrderStateError("fills exceed order quantity")
        new_state = (
            OrderState.FILLED
            if effective_filled_quantity == order.quantity
            else OrderState.PARTIALLY_FILLED
        )
        if prior_fill is None:
            if order.state not in {
                OrderState.ACKNOWLEDGED,
                OrderState.PARTIALLY_FILLED,
                OrderState.CANCEL_PENDING,
            }:
                raise OrderStateError(f"cannot fill order in state {order.state.value}")
            # Persist the fill before mutating the projection. If a worker dies
            # after this commit, hydration sees the immutable fill and the
            # retry below can finish the order transition without duplicating it.
            stored_event = self._record(
                fill,
                "fill_recorded",
                key=f"fill:{fill.venue_fill_id}",
                publish=False,
            )
            self.fills[fill.venue_fill_id] = fill
        else:
            stored_event = next(
                (
                    event
                    for event in self.ledgers.events(LedgerNamespace.ORDER)
                    if event.idempotency_key == f"fill:{fill.venue_fill_id}"
                ),
                None,
            )
            if stored_event is None:
                raise OrderStateError("in-memory fill is missing its durable ledger event")
        # Calling transition even when the projection is already at the target
        # state is intentional: it republishes a durable notification after a
        # prior transport failure and is idempotent for a normal retry.
        self.transition(order.artifact_id, new_state)
        self._publish_event(stored_event, event_type="fill_recorded", artifact_id=fill.artifact_id)
        return self._get(order.artifact_id)

    def cancel(self, order_id: UUID) -> Order:
        return self.transition(order_id, OrderState.CANCEL_PENDING)

    def acknowledge_cancel(self, order_id: UUID) -> Order:
        return self.transition(order_id, OrderState.CANCELLED)

    def reconcile(self, order_id: UUID) -> Order:
        order = self._get(order_id)
        if order.state not in {
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
        }:
            raise OrderStateError("order must be terminal before reconciliation")
        return self.transition(order_id, OrderState.RECONCILED)

    def _get(self, order_id: UUID) -> Order:
        try:
            return self.orders[order_id]
        except KeyError as exc:
            raise OrderStateError(f"unknown order {order_id}") from exc

    @staticmethod
    def _logical_payload(artifact: Order | Fill) -> str:
        return artifact.model_dump_json(exclude={"artifact_id", "created_at"})

    def _record(
        self,
        artifact: object,
        event_type: str,
        key: str | None = None,
        *,
        publish: bool = True,
    ) -> LedgerEvent:
        if isinstance(artifact, Order):
            event_id = artifact.artifact_id
            payload = artifact.model_dump(mode="json", round_trip=True)
            idempotency_key = key or f"order:{artifact.idempotency_key}:{event_type}"
        elif isinstance(artifact, Fill):
            event_id = artifact.artifact_id
            payload = artifact.model_dump(mode="json", round_trip=True)
            idempotency_key = key or f"fill:{artifact.venue_fill_id}:{event_type}"
        else:
            raise TypeError("OMS can only ledger Order or Fill artifacts")
        event = LedgerEvent(
            namespace=LedgerNamespace.ORDER,
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload={"artifact_id": str(event_id), "artifact": payload},
        )
        stored_event = self.ledgers.append(event)
        if publish:
            self._publish_event(stored_event, event_type=event_type, artifact_id=event_id)
        return stored_event

    def _publish_event(self, event: LedgerEvent, *, event_type: str, artifact_id: UUID) -> None:
        if self.event_bus is None:
            return
        self.event_bus.publish(
            EventEnvelope(
                event_id=event.event_id,
                event_type=event_type,
                occurred_at=event.occurred_at,
                artifact_ids=(artifact_id,),
                payload_ref=f"ledger://order/{event.event_id}",
            )
        )

    def _republish_key(self, key: str, *, event_type: str, artifact_id: UUID) -> None:
        if self.event_bus is None:
            return
        event = next(
            (
                item
                for item in self.ledgers.events(LedgerNamespace.ORDER)
                if item.idempotency_key == key
            ),
            None,
        )
        if event is not None:
            self._publish_event(event, event_type=event_type, artifact_id=artifact_id)
