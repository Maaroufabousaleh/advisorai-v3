"""Paper account/order reconciliation service."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from advisorai.contracts import Reconciliation
from advisorai.execution.account import AccountLedger, AccountState
from advisorai.execution.oms import OrderManager
from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers


@dataclass(frozen=True, slots=True)
class VenueAccountSnapshot:
    """Independent venue projection used to reconcile the local account.

    The venue snapshot is an input to reconciliation only; it never becomes a
    second account authority and cannot approve an order.
    """

    as_of: datetime
    cash: Decimal
    positions: Mapping[str, Decimal]
    margin_used: Decimal | None = None
    margin_available: Decimal | None = None
    # Optional independent projection of active venue orders.  These are local
    # parent artifact IDs after the venue adapter has resolved its client order
    # identity; omitting the projection keeps account-only reconciliation
    # useful for venues that expose balances before open-order state.
    venue_open_order_ids: frozenset[UUID] | None = None

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("venue snapshot timestamp must include a timezone")
        if not isinstance(self.cash, Decimal) or any(
            not isinstance(value, Decimal) for value in self.positions.values()
        ):
            raise ValueError("venue account values must be Decimal instances")
        if any(not isinstance(key, str) for key in self.positions):
            raise ValueError("venue position identifiers must be strings")
        if not self.cash.is_finite() or any(
            not value.is_finite() for value in self.positions.values()
        ):
            raise ValueError("venue account values must be finite")
        if any(not key.strip() for key in self.positions):
            raise ValueError("venue position identifiers cannot be blank")
        if self.margin_used is not None and not isinstance(self.margin_used, Decimal):
            raise ValueError("venue margin used must be a Decimal")
        if self.margin_used is not None and (
            not self.margin_used.is_finite() or self.margin_used < 0
        ):
            raise ValueError("venue margin used must be finite and non-negative")
        if self.margin_available is not None and not isinstance(self.margin_available, Decimal):
            raise ValueError("venue margin available must be a Decimal")
        if self.margin_available is not None and (
            not self.margin_available.is_finite() or self.margin_available < 0
        ):
            raise ValueError("venue margin available must be finite and non-negative")
        if self.venue_open_order_ids is not None and any(
            not isinstance(order_id, UUID) for order_id in self.venue_open_order_ids
        ):
            raise ValueError("venue open order identifiers must be UUIDs")

    @property
    def normalized_as_of(self) -> datetime:
        return self.as_of.astimezone(UTC)


class ReconciliationService:
    def __init__(self, ledgers: SqliteLedgers | None = None) -> None:
        self.ledgers = ledgers

    def run(
        self,
        *,
        account: AccountState,
        orders: OrderManager,
        account_ledger: AccountLedger | None = None,
        venue_snapshot: VenueAccountSnapshot | None = None,
    ) -> Reconciliation:
        discrepancies: list[str] = []
        if orders.ambiguous_orders:
            discrepancies.append("ambiguous_order_acknowledgement_pending")
        routed_pending = [
            order.artifact_id for order in orders.orders.values() if order.state.value == "routed"
        ]
        if routed_pending:
            discrepancies.append("routed_order_acknowledgement_pending")
        unknown_fills = [
            fill.venue_fill_id
            for fill in orders.fills.values()
            if fill.order_id not in orders.orders
        ]
        if unknown_fills:
            discrepancies.append("fills_reference_unknown_order")
        for order in orders.orders.values():
            order_fills = [
                fill for fill in orders.fills.values() if fill.order_id == order.artifact_id
            ]
            filled_quantity = sum((fill.quantity for fill in order_fills), Decimal("0"))
            if filled_quantity > order.quantity:
                discrepancies.append(
                    f"order_fill_quantity_overrun:{order.artifact_id}:{filled_quantity}>{order.quantity}"
                )
            if order.state.value == "filled" and filled_quantity != order.quantity:
                discrepancies.append(f"filled_order_quantity_mismatch:{order.artifact_id}")
            if order.state.value == "partially_filled" and not order_fills:
                discrepancies.append(f"partial_order_without_fill:{order.artifact_id}")
        if account_ledger is not None:
            applied_fills = account_ledger.applied_fill_ids
            missing_account_fills = [
                fill.venue_fill_id
                for fill in orders.fills.values()
                if fill.venue_fill_id not in applied_fills
            ]
            if missing_account_fills:
                discrepancies.append(
                    "fills_missing_from_account_ledger:" + ",".join(sorted(missing_account_fills))
                )
        unreconciled_terminal = [
            order.artifact_id
            for order in orders.orders.values()
            if order.state.value in {"filled", "cancelled", "rejected", "expired"}
        ]
        if unreconciled_terminal:
            discrepancies.append("terminal_orders_not_reconciled")
        local_snapshot = account.snapshot()
        if venue_snapshot is not None:
            if venue_snapshot.normalized_as_of > local_snapshot.as_of:
                discrepancies.append("local_account_state_lags_venue_snapshot")
            if venue_snapshot.cash != local_snapshot.cash:
                discrepancies.append(
                    f"venue_cash_mismatch:{venue_snapshot.cash}!={local_snapshot.cash}"
                )
            local_positions = dict(local_snapshot.positions)
            for instrument in sorted(set(local_positions) | set(venue_snapshot.positions)):
                local_value = local_positions.get(instrument, Decimal("0"))
                venue_value = venue_snapshot.positions.get(instrument, Decimal("0"))
                if local_value != venue_value:
                    discrepancies.append(
                        f"venue_position_mismatch:{instrument}:{venue_value}!={local_value}"
                    )
            if (
                venue_snapshot.margin_used is not None
                and venue_snapshot.margin_used != local_snapshot.margin_used
            ):
                discrepancies.append(
                    f"venue_margin_used_mismatch:{venue_snapshot.margin_used}!={local_snapshot.margin_used}"
                )
            if (
                venue_snapshot.margin_available is not None
                and venue_snapshot.margin_available != local_snapshot.margin_available
            ):
                discrepancies.append(
                    "venue_margin_available_mismatch:"
                    f"{venue_snapshot.margin_available}!={local_snapshot.margin_available}"
                )
            if venue_snapshot.venue_open_order_ids is not None:
                local_open_order_ids = {
                    order.artifact_id
                    for order in orders.orders.values()
                    if order.state.value
                    in {"routed", "acknowledged", "partially_filled", "cancel_pending"}
                }
                missing_at_venue = sorted(
                    local_open_order_ids - set(venue_snapshot.venue_open_order_ids), key=str
                )
                unknown_at_venue = sorted(
                    set(venue_snapshot.venue_open_order_ids) - local_open_order_ids, key=str
                )
                if missing_at_venue:
                    discrepancies.append(
                        "local_open_orders_missing_at_venue:" + ",".join(map(str, missing_at_venue))
                    )
                if unknown_at_venue:
                    discrepancies.append(
                        "venue_open_orders_unknown_locally:" + ",".join(map(str, unknown_at_venue))
                    )
        reconciliation = Reconciliation(
            as_of=account.as_of,
            account_state_hash=local_snapshot.state_hash,
            order_ids=tuple(sorted(orders.orders, key=str)),
            fill_ids=tuple(sorted((fill.artifact_id for fill in orders.fills.values()), key=str)),
            reconciled=not discrepancies,
            discrepancies=tuple(discrepancies),
        )
        if self.ledgers is not None:
            logical_payload = reconciliation.model_dump(
                mode="json", round_trip=True, exclude={"artifact_id", "created_at"}
            )
            logical_hash = hashlib.sha256(
                json.dumps(logical_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            # Reconciliation is a projection of the same authoritative state;
            # repeated runs should be idempotent rather than append a new
            # random artifact for an unchanged state.
            reconciliation = reconciliation.model_copy(
                update={
                    "artifact_id": uuid5(
                        NAMESPACE_URL, f"advisorai-v3/reconciliation/{logical_hash}"
                    ),
                    "created_at": reconciliation.as_of,
                }
            )
            payload = reconciliation.model_dump(mode="json", round_trip=True)
            digest = logical_hash[:24]
            self.ledgers.append(
                LedgerEvent(
                    namespace=LedgerNamespace.ORDER,
                    event_type="reconciliation_recorded",
                    idempotency_key=f"reconciliation:{local_snapshot.state_hash}:{digest}",
                    payload={"artifact": payload},
                )
            )
        return reconciliation
