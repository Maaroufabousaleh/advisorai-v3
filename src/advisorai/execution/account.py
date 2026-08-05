"""Authoritative paper account, position, cash, fee, and funding state."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from advisorai.contracts import Fill
from advisorai.ledger import IdempotencyConflict, LedgerEvent, LedgerNamespace, SqliteLedgers
from advisorai.ports import EventBusPort, EventEnvelope


@dataclass(frozen=True, slots=True)
class AccountStateSnapshot:
    cash: Decimal
    positions: tuple[tuple[str, Decimal], ...]
    marks: tuple[tuple[str, Decimal], ...]
    fees_paid: Decimal
    funding_paid: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    margin_used: Decimal
    margin_available: Decimal | None
    as_of: datetime
    state_hash: str
    equity: Decimal = Decimal("0")
    gross_notional: Decimal = Decimal("0")
    net_notional: Decimal = Decimal("0")
    drawdown: Decimal = Decimal("0")
    daily_realized_pnl: Decimal = Decimal("0")
    rolling_realized_pnl: Decimal = Decimal("0")
    liquidation_buffer: Decimal | None = None
    borrow_paid: Decimal = Decimal("0")
    fx_adjustments: Decimal = Decimal("0")
    corporate_action_adjustments: Decimal = Decimal("0")
    average_cost: tuple[tuple[str, Decimal], ...] = ()

    def __post_init__(self) -> None:
        values = (
            self.cash,
            *[value for _name, value in self.positions],
            *[value for _name, value in self.marks],
            self.fees_paid,
            self.funding_paid,
            self.realized_pnl,
            self.unrealized_pnl,
            self.margin_used,
            self.equity,
            self.gross_notional,
            self.net_notional,
            self.drawdown,
            self.daily_realized_pnl,
            self.rolling_realized_pnl,
            self.borrow_paid,
            self.fx_adjustments,
            self.corporate_action_adjustments,
            *([self.margin_available] if self.margin_available is not None else []),
            *([self.liquidation_buffer] if self.liquidation_buffer is not None else []),
        )
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
            raise ValueError("account snapshot values must be finite Decimal instances")
        if any(not isinstance(name, str) or not name.strip() for name, _value in self.positions):
            raise ValueError("account snapshot position identifiers cannot be blank")
        if any(not isinstance(name, str) or not name.strip() for name, _value in self.marks):
            raise ValueError("account snapshot mark identifiers cannot be blank")
        if any(not isinstance(name, str) or not name.strip() for name, _value in self.average_cost):
            raise ValueError("account snapshot average-cost identifiers cannot be blank")
        if any(
            not isinstance(value, Decimal) or not value.is_finite() or value < 0
            for _name, value in self.average_cost
        ):
            raise ValueError("account snapshot average costs must be finite and non-negative")
        if len({name for name, _value in self.positions}) != len(self.positions):
            raise ValueError("account snapshot positions must be unique")
        if len({name for name, _value in self.marks}) != len(self.marks):
            raise ValueError("account snapshot marks must be unique")
        if len({name for name, _value in self.average_cost}) != len(self.average_cost):
            raise ValueError("account snapshot average costs must be unique")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("account snapshot timestamp must include a timezone")
        if len(self.state_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.state_hash
        ):
            raise ValueError("account snapshot state_hash must be a lowercase SHA-256 digest")


@dataclass
class AccountState:
    """Single source of truth for paper account state; methods are deterministic."""

    cash: Decimal
    positions: dict[str, Decimal] = field(default_factory=dict)
    marks: dict[str, Decimal] = field(default_factory=dict)
    average_cost: dict[str, Decimal] = field(default_factory=dict)
    fees_paid: Decimal = Decimal("0")
    funding_paid: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    margin_used: Decimal = Decimal("0")
    margin_available: Decimal | None = None
    starting_cash: Decimal | None = None
    peak_equity: Decimal | None = None
    daily_realized_pnl: Decimal = Decimal("0")
    rolling_realized_pnl: Decimal = Decimal("0")
    liquidation_buffer: Decimal | None = None
    borrow_paid: Decimal = Decimal("0")
    fx_adjustments: Decimal = Decimal("0")
    corporate_action_adjustments: Decimal = Decimal("0")
    as_of: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        monetary = [
            self.cash,
            self.fees_paid,
            self.funding_paid,
            self.realized_pnl,
            self.margin_used,
            self.daily_realized_pnl,
            self.rolling_realized_pnl,
            self.borrow_paid,
            self.fx_adjustments,
            self.corporate_action_adjustments,
        ]
        if self.margin_available is not None:
            monetary.append(self.margin_available)
        if self.starting_cash is not None:
            monetary.append(self.starting_cash)
        if self.peak_equity is not None:
            monetary.append(self.peak_equity)
        if self.liquidation_buffer is not None:
            monetary.append(self.liquidation_buffer)
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in monetary):
            raise ValueError("account monetary values must be finite")
        if any(not key.strip() for key in (*self.positions, *self.marks, *self.average_cost)):
            raise ValueError("account instrument identifiers cannot be blank")
        if any(
            not isinstance(value, Decimal) or not value.is_finite()
            for value in (*self.positions.values(), *self.marks.values())
        ):
            raise ValueError("account positions and marks must be finite")
        if any(value <= 0 for value in self.marks.values()):
            raise ValueError("account marks must be positive")
        if any(
            not isinstance(value, Decimal) or not value.is_finite() or value < 0
            for value in self.average_cost.values()
        ):
            raise ValueError("average costs cannot be negative")
        if min(self.fees_paid, self.funding_paid, self.margin_used, self.borrow_paid) < 0:
            raise ValueError("account fees, funding, and margin cannot be negative")
        if self.cash < 0:
            raise ValueError("paper account cash cannot start negative")
        if self.starting_cash is None:
            self.starting_cash = self.cash
        if self.starting_cash < 0:
            raise ValueError("starting cash cannot be negative")
        if self.peak_equity is None:
            self.peak_equity = self.cash
        if self.peak_equity < 0:
            raise ValueError("peak equity cannot be negative")
        self.as_of = self._aware(self.as_of)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("account timestamp must include a timezone")
        return value.astimezone(UTC)

    def apply_fill(self, fill: Fill, side: str, instrument_id: str) -> None:
        if not instrument_id.strip():
            raise ValueError("fill instrument identifier cannot be blank")
        instrument_id = instrument_id.strip()
        side = side.strip().lower()
        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        signed_quantity = fill.quantity if side == "buy" else -fill.quantity
        old_quantity = self.positions.get(instrument_id, Decimal("0"))
        old_average = self.average_cost.get(instrument_id, Decimal("0"))
        new_quantity = old_quantity + signed_quantity
        realized_before = self.realized_pnl
        if old_quantity and (old_quantity > 0) != (signed_quantity > 0):
            closed = min(abs(old_quantity), abs(signed_quantity))
            self.realized_pnl += (
                (fill.price - old_average) * closed * (1 if old_quantity > 0 else -1)
            )
        if new_quantity == 0:
            self.positions.pop(instrument_id, None)
            self.average_cost.pop(instrument_id, None)
        elif (old_quantity >= 0 and signed_quantity > 0) or (
            old_quantity <= 0 and signed_quantity < 0
        ):
            total = abs(old_quantity) + abs(signed_quantity)
            self.average_cost[instrument_id] = (
                old_average * abs(old_quantity) + fill.price * abs(signed_quantity)
            ) / total
            self.positions[instrument_id] = new_quantity
        else:
            self.positions[instrument_id] = new_quantity
            # A crossing fill opens the remaining quantity in the opposite
            # direction at the crossing price; retaining the old average would
            # corrupt future realized/unrealized P&L.
            if old_quantity and (old_quantity > 0) != (new_quantity > 0):
                self.average_cost[instrument_id] = fill.price
        self.cash -= fill.price * signed_quantity
        self.fees_paid += fill.fee
        self.cash -= fill.fee
        realized_delta = self.realized_pnl - realized_before
        # Loss controls include transaction costs even though gross realized P&L
        # remains separately auditable.
        self.daily_realized_pnl += realized_delta - fill.fee
        self.rolling_realized_pnl += realized_delta - fill.fee
        self.as_of = max(self.as_of, fill.occurred_at)
        self._update_peak_equity()

    def apply_funding(self, amount: Decimal, occurred_at: datetime) -> None:
        if not amount.is_finite() or amount < 0:
            raise ValueError("funding amount must be finite and non-negative")
        self.funding_paid += amount
        self.cash -= amount
        self.daily_realized_pnl -= amount
        self.rolling_realized_pnl -= amount
        self.as_of = max(self.as_of, self._aware(occurred_at))
        self._update_peak_equity()

    def apply_cash_transfer(self, amount: Decimal, occurred_at: datetime) -> None:
        """Apply a signed deposit/withdrawal without disguising it as P&L."""

        if not amount or not amount.is_finite():
            raise ValueError("cash transfer amount cannot be zero")
        occurred_at = self._aware(occurred_at)
        self.cash += amount
        self.as_of = max(self.as_of, occurred_at)
        self._update_peak_equity()

    def apply_borrow(self, amount: Decimal, occurred_at: datetime) -> None:
        """Record borrow financing as a distinct, auditable cost."""

        if not amount.is_finite() or amount < 0:
            raise ValueError("borrow amount must be finite and non-negative")
        occurred_at = self._aware(occurred_at)
        self.borrow_paid += amount
        self.cash -= amount
        self.daily_realized_pnl -= amount
        self.rolling_realized_pnl -= amount
        self.as_of = max(self.as_of, occurred_at)
        self._update_peak_equity()

    def apply_fx_adjustment(self, amount: Decimal, occurred_at: datetime) -> None:
        """Apply a signed FX conversion adjustment without changing positions."""

        if not amount.is_finite() or amount == 0:
            raise ValueError("FX adjustment must be finite and non-zero")
        occurred_at = self._aware(occurred_at)
        self.fx_adjustments += amount
        self.cash += amount
        self.as_of = max(self.as_of, occurred_at)
        self._update_peak_equity()

    def apply_corporate_action(self, amount: Decimal, occurred_at: datetime) -> None:
        """Apply a signed corporate-action cash adjustment.

        V3-Core crypto currently emits no corporate actions, but keeping the
        event type in the authoritative account ledger prevents later equity
        expansion from hiding cash/P&L adjustments in ad-hoc code.
        """

        if not amount.is_finite() or amount == 0:
            raise ValueError("corporate action adjustment must be finite and non-zero")
        occurred_at = self._aware(occurred_at)
        self.corporate_action_adjustments += amount
        self.cash += amount
        self.as_of = max(self.as_of, occurred_at)
        self._update_peak_equity()

    def apply_split(self, instrument_id: str, ratio: Decimal, occurred_at: datetime) -> None:
        """Apply a stock split while preserving the position's market value.

        Splits are represented as an accounting event rather than a cash
        adjustment: quantity and average cost are transformed by the same
        positive ratio and an existing mark is adjusted inversely.  The
        operation is deliberately asset-agnostic so the equity expansion can
        be replayed from the account ledger without importing an equity data
        source into the execution path.
        """

        instrument_id = instrument_id.strip()
        if not instrument_id:
            raise ValueError("split instrument identifier cannot be blank")
        if not ratio.is_finite() or ratio <= 0:
            raise ValueError("split ratio must be finite and positive")
        occurred_at = self._aware(occurred_at)
        if instrument_id in self.positions:
            self.positions[instrument_id] *= ratio
        if instrument_id in self.average_cost:
            self.average_cost[instrument_id] /= ratio
        if instrument_id in self.marks:
            self.marks[instrument_id] /= ratio
        self.as_of = max(self.as_of, occurred_at)
        self._update_peak_equity()

    def apply_dividend(
        self, instrument_id: str, cash_per_unit: Decimal, occurred_at: datetime
    ) -> None:
        """Apply a per-unit dividend, debiting short positions correctly."""

        instrument_id = instrument_id.strip()
        if not instrument_id:
            raise ValueError("dividend instrument identifier cannot be blank")
        if not cash_per_unit.is_finite() or cash_per_unit < 0:
            raise ValueError("dividend cash per unit must be finite and non-negative")
        occurred_at = self._aware(occurred_at)
        amount = self.positions.get(instrument_id, Decimal("0")) * cash_per_unit
        if amount:
            self.corporate_action_adjustments += amount
            self.cash += amount
        self.as_of = max(self.as_of, occurred_at)
        self._update_peak_equity()

    def update_margin(
        self, *, margin_used: Decimal, margin_available: Decimal, as_of: datetime
    ) -> None:
        if (
            not margin_used.is_finite()
            or not margin_available.is_finite()
            or margin_used < 0
            or margin_available < 0
        ):
            raise ValueError("margin values cannot be negative")
        self.margin_used = margin_used
        self.margin_available = margin_available
        self.liquidation_buffer = margin_available
        self.as_of = max(self.as_of, self._aware(as_of))
        self._update_peak_equity()

    def mark(self, instrument_id: str, price: Decimal, as_of: datetime) -> None:
        if not instrument_id.strip():
            raise ValueError("mark instrument identifier cannot be blank")
        if not price.is_finite() or price <= 0:
            raise ValueError("mark price must be positive")
        instrument_id = instrument_id.strip()
        as_of = self._aware(as_of)
        self.marks[instrument_id] = price
        self.as_of = max(self.as_of, as_of)
        self._update_peak_equity()

    def unrealized(self) -> Decimal:
        terms = (
            (self.marks[instrument] - self.average_cost.get(instrument, self.marks[instrument]))
            * quantity
            for instrument, quantity in self.positions.items()
            if instrument in self.marks
        )
        return sum(terms, Decimal("0"))

    def gross_notional(self) -> Decimal:
        terms = (
            abs(quantity) * self.marks.get(instrument, Decimal("0"))
            for instrument, quantity in self.positions.items()
        )
        return sum(terms, Decimal("0"))

    def net_notional(self) -> Decimal:
        terms = (
            quantity * self.marks.get(instrument, Decimal("0"))
            for instrument, quantity in self.positions.items()
        )
        return sum(terms, Decimal("0"))

    def equity(self) -> Decimal:
        terms = (
            quantity * self.marks.get(instrument, Decimal("0"))
            for instrument, quantity in self.positions.items()
        )
        return self.cash + sum(terms, Decimal("0"))

    def drawdown(self) -> Decimal:
        peak = self.peak_equity or Decimal("0")
        return max(Decimal("0"), peak - self.equity())

    def _update_peak_equity(self) -> None:
        equity = self.equity()
        self.peak_equity = max(self.peak_equity or equity, equity)

    def snapshot(self) -> AccountStateSnapshot:
        unrealized = self.unrealized()
        payload = {
            "cash": str(self.cash),
            "positions": sorted((key, str(value)) for key, value in self.positions.items()),
            # Average cost is part of the authoritative accounting projection;
            # omitting it would allow a restart to retain a different future
            # realized-P&L path while producing the same visible positions.
            "average_cost": sorted((key, str(value)) for key, value in self.average_cost.items()),
            "marks": sorted((key, str(value)) for key, value in self.marks.items()),
            "fees_paid": str(self.fees_paid),
            "funding_paid": str(self.funding_paid),
            "realized_pnl": str(self.realized_pnl),
            "unrealized_pnl": str(unrealized),
            "margin_used": str(self.margin_used),
            "margin_available": (
                str(self.margin_available) if self.margin_available is not None else None
            ),
            "starting_cash": str(self.starting_cash) if self.starting_cash is not None else None,
            "peak_equity": str(self.peak_equity) if self.peak_equity is not None else None,
            "daily_realized_pnl": str(self.daily_realized_pnl),
            "rolling_realized_pnl": str(self.rolling_realized_pnl),
            "borrow_paid": str(self.borrow_paid),
            "fx_adjustments": str(self.fx_adjustments),
            "corporate_action_adjustments": str(self.corporate_action_adjustments),
            "liquidation_buffer": (
                str(self.liquidation_buffer) if self.liquidation_buffer is not None else None
            ),
            "as_of": self.as_of.isoformat(),
        }
        state_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return AccountStateSnapshot(
            cash=self.cash,
            positions=tuple((key, Decimal(value)) for key, value in payload["positions"]),
            marks=tuple((key, Decimal(value)) for key, value in payload["marks"]),
            fees_paid=self.fees_paid,
            funding_paid=self.funding_paid,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=unrealized,
            margin_used=self.margin_used,
            margin_available=self.margin_available,
            as_of=self.as_of,
            state_hash=state_hash,
            equity=self.equity(),
            gross_notional=self.gross_notional(),
            net_notional=self.net_notional(),
            drawdown=self.drawdown(),
            daily_realized_pnl=self.daily_realized_pnl,
            rolling_realized_pnl=self.rolling_realized_pnl,
            liquidation_buffer=self.liquidation_buffer,
            borrow_paid=self.borrow_paid,
            fx_adjustments=self.fx_adjustments,
            corporate_action_adjustments=self.corporate_action_adjustments,
            average_cost=tuple((key, Decimal(value)) for key, value in payload["average_cost"]),
        )


class AccountLedger:
    """Ledger-coupled account mutations; all accounting events are idempotent."""

    def __init__(
        self,
        ledgers: SqliteLedgers,
        account: AccountState,
        *,
        hydrate: bool = True,
        event_bus: EventBusPort | None = None,
    ) -> None:
        self.ledgers = ledgers
        self.account = account
        self.event_bus = event_bus
        # Hydrate the in-process guard from the durable namespace so a retry
        # after a worker restart cannot apply a fill or funding event twice.
        self._applied_payloads: dict[str, dict[str, object]] = {}
        if hydrate:
            self._hydrate()

    def _hydrate(self) -> None:
        # SQLite row order is the append order.  Reordering by timestamps can
        # replay same-time events in a different sequence and produce a false
        # balance, so the ledger's durable order is authoritative.
        for event in self.ledgers.events(LedgerNamespace.ACCOUNT):
            if event.event_type not in {
                "fill_applied",
                "funding_applied",
                "cash_transfer_applied",
                "mark_applied",
                "margin_updated",
                "borrow_applied",
                "fx_adjustment_applied",
                "corporate_action_applied",
                "split_applied",
                "dividend_applied",
            }:
                continue
            payload = dict(event.payload)
            self._applied_payloads[event.idempotency_key] = payload
            if event.event_type == "fill_applied":
                try:
                    fill = Fill(
                        artifact_id=UUID(str(payload["fill_id"])),
                        order_id=UUID(str(payload["order_id"])),
                        venue_fill_id=str(payload["venue_fill_id"]),
                        quantity=Decimal(str(payload["quantity"])),
                        price=Decimal(str(payload["price"])),
                        fee=Decimal(str(payload["fee"])),
                        occurred_at=datetime.fromisoformat(str(payload["occurred_at"])),
                    )
                    self.account.apply_fill(
                        fill,
                        str(payload["side"]),
                        str(payload["instrument_id"]),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise IdempotencyConflict(
                        "account ledger contains invalid fill payload"
                    ) from exc
            elif event.event_type == "funding_applied":
                self.account.apply_funding(
                    Decimal(str(payload["amount"])),
                    datetime.fromisoformat(str(payload["occurred_at"])),
                )
            elif event.event_type == "cash_transfer_applied":
                self.account.apply_cash_transfer(
                    Decimal(str(payload["amount"])),
                    datetime.fromisoformat(str(payload["occurred_at"])),
                )
            elif event.event_type == "mark_applied":
                self.account.mark(
                    str(payload["instrument_id"]),
                    Decimal(str(payload["price"])),
                    datetime.fromisoformat(str(payload["as_of"])),
                )
            elif event.event_type == "margin_updated":
                self.account.update_margin(
                    margin_used=Decimal(str(payload["margin_used"])),
                    margin_available=Decimal(str(payload["margin_available"])),
                    as_of=datetime.fromisoformat(str(payload["as_of"])),
                )
            elif event.event_type == "borrow_applied":
                self.account.apply_borrow(
                    Decimal(str(payload["amount"])),
                    datetime.fromisoformat(str(payload["occurred_at"])),
                )
            elif event.event_type == "fx_adjustment_applied":
                self.account.apply_fx_adjustment(
                    Decimal(str(payload["amount"])),
                    datetime.fromisoformat(str(payload["occurred_at"])),
                )
            elif event.event_type == "corporate_action_applied":
                self.account.apply_corporate_action(
                    Decimal(str(payload["amount"])),
                    datetime.fromisoformat(str(payload["occurred_at"])),
                )
            elif event.event_type == "split_applied":
                self.account.apply_split(
                    str(payload["instrument_id"]),
                    Decimal(str(payload["ratio"])),
                    datetime.fromisoformat(str(payload["occurred_at"])),
                )
            elif event.event_type == "dividend_applied":
                self.account.apply_dividend(
                    str(payload["instrument_id"]),
                    Decimal(str(payload["cash_per_unit"])),
                    datetime.fromisoformat(str(payload["occurred_at"])),
                )
            expected_hash = payload.get("state_hash")
            if expected_hash is not None and expected_hash != self.account.snapshot().state_hash:
                raise IdempotencyConflict(
                    f"account ledger state hash mismatch after {event.event_type}"
                )

    def _commit(self, candidate: AccountState) -> None:
        """Commit a candidate without replacing the caller-owned account object."""

        self.account.__dict__.clear()
        self.account.__dict__.update(deepcopy(candidate.__dict__))

    @property
    def applied_fill_ids(self) -> frozenset[str]:
        return frozenset(
            str(payload["venue_fill_id"])
            for payload in self._applied_payloads.values()
            if "venue_fill_id" in payload
        )

    def apply_fill(self, fill: Fill, side: str, instrument_id: str) -> AccountStateSnapshot:
        instrument_id = instrument_id.strip()
        side = side.strip().lower()
        if not instrument_id:
            raise ValueError("fill instrument identifier cannot be blank")
        key = f"account-fill:{fill.venue_fill_id}"
        if key in self._applied_payloads:
            prior = self._applied_payloads[key]
            if any(
                prior.get(name) != value
                for name, value in {
                    "fill_id": str(fill.artifact_id),
                    "order_id": str(fill.order_id),
                    "venue_fill_id": fill.venue_fill_id,
                    "side": side,
                    "instrument_id": instrument_id,
                    "quantity": str(fill.quantity),
                    "price": str(fill.price),
                    "fee": str(fill.fee),
                    "occurred_at": fill.occurred_at.isoformat(),
                }.items()
            ):
                raise IdempotencyConflict(f"idempotency key {key!r} reused for different fill")
            self._republish(key, event_type="account_fill_applied", artifact_id=fill.artifact_id)
            return self.account.snapshot()
        candidate = deepcopy(self.account)
        candidate.apply_fill(fill, side, instrument_id)
        event = LedgerEvent(
            namespace=LedgerNamespace.ACCOUNT,
            event_type="fill_applied",
            idempotency_key=key,
            payload={
                "fill_id": str(fill.artifact_id),
                "venue_fill_id": fill.venue_fill_id,
                "order_id": str(fill.order_id),
                "instrument_id": instrument_id,
                "side": side,
                "quantity": str(fill.quantity),
                "price": str(fill.price),
                "fee": str(fill.fee),
                "occurred_at": fill.occurred_at.isoformat(),
                "state_hash": candidate.snapshot().state_hash,
            },
        )
        stored_event = self.ledgers.append(event)
        self._commit(candidate)
        # Mark the durable projection as applied before publishing the
        # best-effort outbox notification.  If the notification transport is
        # unavailable after the ledger commit, a retry must not apply the
        # accounting event a second time.
        self._applied_payloads[key] = {
            "fill_id": str(fill.artifact_id),
            "venue_fill_id": fill.venue_fill_id,
            "order_id": str(fill.order_id),
            "instrument_id": instrument_id,
            "side": side,
            "quantity": str(fill.quantity),
            "price": str(fill.price),
            "fee": str(fill.fee),
            "occurred_at": fill.occurred_at.isoformat(),
        }
        self._publish_event(
            stored_event,
            event_type="account_fill_applied",
            artifact_id=fill.artifact_id,
        )
        return self.account.snapshot()

    def apply_funding(
        self, amount: Decimal, occurred_at: datetime, funding_id: str
    ) -> AccountStateSnapshot:
        funding_id = funding_id.strip()
        if not funding_id:
            raise ValueError("funding ID is required")
        key = f"funding:{funding_id}"
        if key in self._applied_payloads:
            prior = self._applied_payloads[key]
            if (
                prior.get("amount") != str(amount)
                or prior.get("occurred_at") != occurred_at.isoformat()
            ):
                raise IdempotencyConflict(f"idempotency key {key!r} reused for different funding")
            self._republish(key, event_type="account_funding_applied")
            return self.account.snapshot()
        candidate = deepcopy(self.account)
        candidate.apply_funding(amount, occurred_at)
        event = LedgerEvent(
            namespace=LedgerNamespace.ACCOUNT,
            event_type="funding_applied",
            idempotency_key=key,
            payload={
                "amount": str(amount),
                "occurred_at": occurred_at.isoformat(),
                "funding_id": funding_id,
                "state_hash": candidate.snapshot().state_hash,
            },
        )
        stored_event = self.ledgers.append(event)
        self._commit(candidate)
        self._applied_payloads[key] = {
            "amount": str(amount),
            "occurred_at": occurred_at.isoformat(),
            "funding_id": funding_id,
        }
        self._publish_event(stored_event, event_type="account_funding_applied")
        return self.account.snapshot()

    def apply_cash_transfer(
        self, amount: Decimal, occurred_at: datetime, transfer_id: str
    ) -> AccountStateSnapshot:
        transfer_id = transfer_id.strip()
        if not transfer_id:
            raise ValueError("cash transfer ID is required")
        key = f"cash-transfer:{transfer_id}"
        if key in self._applied_payloads:
            prior = self._applied_payloads[key]
            if (
                prior.get("amount") != str(amount)
                or prior.get("occurred_at") != occurred_at.isoformat()
            ):
                raise IdempotencyConflict(
                    f"idempotency key {key!r} reused for different cash transfer"
                )
            self._republish(key, event_type="account_cash_transfer_applied")
            return self.account.snapshot()
        candidate = deepcopy(self.account)
        candidate.apply_cash_transfer(amount, occurred_at)
        payload = {
            "amount": str(amount),
            "occurred_at": occurred_at.isoformat(),
            "transfer_id": transfer_id,
            "state_hash": candidate.snapshot().state_hash,
        }
        event = LedgerEvent(
            namespace=LedgerNamespace.ACCOUNT,
            event_type="cash_transfer_applied",
            idempotency_key=key,
            payload=payload,
        )
        stored_event = self.ledgers.append(event)
        self._commit(candidate)
        self._applied_payloads[key] = payload
        self._publish_event(stored_event, event_type="account_cash_transfer_applied")
        return self.account.snapshot()

    def _apply_adjustment(
        self,
        *,
        amount: Decimal,
        occurred_at: datetime,
        adjustment_id: str,
        key_prefix: str,
        event_type: str,
        publish_type: str,
        apply,
    ) -> AccountStateSnapshot:
        adjustment_id = adjustment_id.strip()
        if not adjustment_id:
            raise ValueError("account adjustment ID is required")
        key = f"{key_prefix}:{adjustment_id}"
        payload = {
            "amount": str(amount),
            "occurred_at": occurred_at.isoformat(),
            "adjustment_id": adjustment_id,
        }
        prior = self._applied_payloads.get(key)
        if prior is not None:
            if any(prior.get(name) != value for name, value in payload.items()):
                raise IdempotencyConflict(
                    f"idempotency key {key!r} reused for different account adjustment"
                )
            self._republish(key, event_type=publish_type)
            return self.account.snapshot()
        candidate = deepcopy(self.account)
        apply(candidate, amount, occurred_at)
        event = LedgerEvent(
            namespace=LedgerNamespace.ACCOUNT,
            event_type=event_type,
            idempotency_key=key,
            payload={**payload, "state_hash": candidate.snapshot().state_hash},
        )
        stored_event = self.ledgers.append(event)
        self._commit(candidate)
        self._applied_payloads[key] = payload
        self._publish_event(stored_event, event_type=publish_type)
        return self.account.snapshot()

    def apply_borrow(
        self, amount: Decimal, occurred_at: datetime, borrow_id: str
    ) -> AccountStateSnapshot:
        return self._apply_adjustment(
            amount=amount,
            occurred_at=occurred_at,
            adjustment_id=borrow_id,
            key_prefix="borrow",
            event_type="borrow_applied",
            publish_type="account_borrow_applied",
            apply=lambda state, value, at: state.apply_borrow(value, at),
        )

    def apply_fx_adjustment(
        self, amount: Decimal, occurred_at: datetime, adjustment_id: str
    ) -> AccountStateSnapshot:
        return self._apply_adjustment(
            amount=amount,
            occurred_at=occurred_at,
            adjustment_id=adjustment_id,
            key_prefix="fx",
            event_type="fx_adjustment_applied",
            publish_type="account_fx_adjustment_applied",
            apply=lambda state, value, at: state.apply_fx_adjustment(value, at),
        )

    def apply_corporate_action(
        self, amount: Decimal, occurred_at: datetime, action_id: str
    ) -> AccountStateSnapshot:
        return self._apply_adjustment(
            amount=amount,
            occurred_at=occurred_at,
            adjustment_id=action_id,
            key_prefix="corporate-action",
            event_type="corporate_action_applied",
            publish_type="account_corporate_action_applied",
            apply=lambda state, value, at: state.apply_corporate_action(value, at),
        )

    def apply_corporate_action_record(self, action) -> AccountStateSnapshot:
        """Apply a validated equity ``CorporateAction`` through typed ledger paths.

        The execution package intentionally does not import the expansion
        package.  Duck-typing the already-validated record keeps the accounting
        spine independent while still making split/dividend replay explicit.
        Merger/spinoff cash-only records use the generic corporate-action cash
        event; non-cash transformations require a future instrument-mapping
        implementation instead of silently changing the position.
        """

        action_type = getattr(getattr(action, "action_type", None), "value", None)
        if action_type is None:
            action_type = str(getattr(action, "action_type", "")).strip().lower()
        instrument_id = getattr(getattr(action, "instrument", None), "canonical_id", "")
        action_id = str(getattr(action, "action_id", ""))
        occurred_at = getattr(action, "effective_at", None)
        if action_type == "split":
            ratio = getattr(action, "ratio", None)
            if ratio is None:
                raise ValueError("split corporate actions require a ratio")
            return self.apply_split(instrument_id, ratio, occurred_at, action_id)
        if action_type == "dividend":
            cash_amount = getattr(action, "cash_amount", None)
            if cash_amount is None:
                raise ValueError("dividend corporate actions require a cash amount")
            return self.apply_dividend(instrument_id, cash_amount, occurred_at, action_id)
        cash_amount = getattr(action, "cash_amount", None)
        if cash_amount is None or cash_amount == 0:
            raise ValueError(
                "merger/spinoff accounting requires a non-zero cash amount or a future mapping"
            )
        return self.apply_corporate_action(cash_amount, occurred_at, action_id)

    def _apply_instrument_action(
        self,
        *,
        instrument_id: str,
        amount: Decimal,
        occurred_at: datetime,
        action_id: str,
        key_prefix: str,
        event_type: str,
        publish_type: str,
        apply,
        amount_name: str,
    ) -> AccountStateSnapshot:
        instrument_id = instrument_id.strip()
        action_id = action_id.strip()
        if not instrument_id or not action_id:
            raise ValueError("corporate-action instrument and action IDs are required")
        key = f"{key_prefix}:{action_id}"
        payload = {
            "instrument_id": instrument_id,
            amount_name: str(amount),
            "occurred_at": occurred_at.isoformat(),
            "action_id": action_id,
        }
        prior = self._applied_payloads.get(key)
        if prior is not None:
            if any(prior.get(name) != value for name, value in payload.items()):
                raise IdempotencyConflict(
                    f"idempotency key {key!r} reused for a different corporate action"
                )
            self._republish(key, event_type=publish_type)
            return self.account.snapshot()
        candidate = deepcopy(self.account)
        apply(candidate, instrument_id, amount, occurred_at)
        event = LedgerEvent(
            namespace=LedgerNamespace.ACCOUNT,
            event_type=event_type,
            idempotency_key=key,
            payload={**payload, "state_hash": candidate.snapshot().state_hash},
        )
        stored_event = self.ledgers.append(event)
        self._commit(candidate)
        self._applied_payloads[key] = payload
        self._publish_event(stored_event, event_type=publish_type)
        return self.account.snapshot()

    def apply_split(
        self,
        instrument_id: str,
        ratio: Decimal,
        occurred_at: datetime,
        action_id: str,
    ) -> AccountStateSnapshot:
        return self._apply_instrument_action(
            instrument_id=instrument_id,
            amount=ratio,
            occurred_at=occurred_at,
            action_id=action_id,
            key_prefix="split",
            event_type="split_applied",
            publish_type="account_split_applied",
            apply=lambda state, instrument, value, at: state.apply_split(instrument, value, at),
            amount_name="ratio",
        )

    def apply_dividend(
        self,
        instrument_id: str,
        cash_per_unit: Decimal,
        occurred_at: datetime,
        action_id: str,
    ) -> AccountStateSnapshot:
        return self._apply_instrument_action(
            instrument_id=instrument_id,
            amount=cash_per_unit,
            occurred_at=occurred_at,
            action_id=action_id,
            key_prefix="dividend",
            event_type="dividend_applied",
            publish_type="account_dividend_applied",
            apply=lambda state, instrument, value, at: state.apply_dividend(instrument, value, at),
            amount_name="cash_per_unit",
        )

    def mark(
        self,
        instrument_id: str,
        price: Decimal,
        as_of: datetime,
        mark_id: str,
    ) -> AccountStateSnapshot:
        mark_id = mark_id.strip()
        instrument_id = instrument_id.strip()
        if not mark_id or not instrument_id:
            raise ValueError("mark and instrument IDs are required")
        key = f"mark:{mark_id}"
        if key in self._applied_payloads:
            prior = self._applied_payloads[key]
            if (
                prior.get("instrument_id") != instrument_id
                or prior.get("price") != str(price)
                or prior.get("as_of") != as_of.isoformat()
            ):
                raise IdempotencyConflict(f"idempotency key {key!r} reused for different mark")
            self._republish(key, event_type="account_mark_applied")
            return self.account.snapshot()
        candidate = deepcopy(self.account)
        candidate.mark(instrument_id, price, as_of)
        payload = {
            "instrument_id": instrument_id,
            "price": str(price),
            "as_of": as_of.isoformat(),
            "mark_id": mark_id,
            "state_hash": candidate.snapshot().state_hash,
        }
        event = LedgerEvent(
            namespace=LedgerNamespace.ACCOUNT,
            event_type="mark_applied",
            idempotency_key=key,
            payload=payload,
        )
        stored_event = self.ledgers.append(event)
        self._commit(candidate)
        self._applied_payloads[key] = payload
        self._publish_event(stored_event, event_type="account_mark_applied")
        return self.account.snapshot()

    def update_margin(
        self,
        *,
        margin_used: Decimal,
        margin_available: Decimal,
        as_of: datetime,
        margin_id: str,
    ) -> AccountStateSnapshot:
        margin_id = margin_id.strip()
        if not margin_id:
            raise ValueError("margin ID is required")
        key = f"margin:{margin_id}"
        payload = {
            "margin_used": str(margin_used),
            "margin_available": str(margin_available),
            "as_of": as_of.isoformat(),
            "margin_id": margin_id,
        }
        prior = self._applied_payloads.get(key)
        if prior is not None:
            if any(prior.get(name) != value for name, value in payload.items()):
                raise IdempotencyConflict(f"idempotency key {key!r} reused for different margin")
            self._republish(key, event_type="account_margin_updated")
            return self.account.snapshot()
        candidate = deepcopy(self.account)
        candidate.update_margin(
            margin_used=margin_used,
            margin_available=margin_available,
            as_of=as_of,
        )
        event = LedgerEvent(
            namespace=LedgerNamespace.ACCOUNT,
            event_type="margin_updated",
            idempotency_key=key,
            payload={**payload, "state_hash": candidate.snapshot().state_hash},
        )
        stored_event = self.ledgers.append(event)
        self._commit(candidate)
        self._applied_payloads[key] = payload
        self._publish_event(stored_event, event_type="account_margin_updated")
        return self.account.snapshot()

    def _publish_event(
        self,
        event: LedgerEvent,
        *,
        event_type: str,
        artifact_id: UUID | None = None,
    ) -> None:
        if self.event_bus is None:
            return
        self.event_bus.publish(
            EventEnvelope(
                event_id=event.event_id,
                event_type=event_type,
                occurred_at=event.occurred_at,
                artifact_ids=(artifact_id,) if artifact_id is not None else (),
                payload_ref=f"ledger://account/{event.event_id}",
            )
        )

    def _republish(
        self, idempotency_key: str, *, event_type: str, artifact_id: UUID | None = None
    ) -> None:
        if self.event_bus is None:
            return
        event = next(
            (
                item
                for item in self.ledgers.events(LedgerNamespace.ACCOUNT)
                if item.idempotency_key == idempotency_key
            ),
            None,
        )
        if event is not None:
            self._publish_event(event, event_type=event_type, artifact_id=artifact_id)
