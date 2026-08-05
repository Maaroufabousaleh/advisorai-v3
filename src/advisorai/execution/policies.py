"""Deterministic immediate/passive execution choices for the paper core."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from advisorai.contracts import ExecutionPlan, InstrumentIdentity, Order


class ExecutionPolicyKind(StrEnum):
    IMMEDIATE = "immediate"
    PASSIVE_LIMIT = "passive_limit"


@dataclass(frozen=True, slots=True)
class QuoteState:
    mark: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.mark.is_finite() or self.mark <= 0:
            raise ValueError("execution mark must be positive")
        if self.bid is not None and (not self.bid.is_finite() or self.bid <= 0):
            raise ValueError("execution bid must be positive")
        if self.ask is not None and (not self.ask.is_finite() or self.ask <= 0):
            raise ValueError("execution ask must be positive")
        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise ValueError("execution ask cannot be below bid")


@dataclass(frozen=True, slots=True)
class ExecutionChoice:
    policy: ExecutionPolicyKind
    side: str
    quantity: Decimal
    order_type: str
    price: Decimal
    time_in_force: str
    reason: str


class DeterministicExecutionPolicy:
    """Choose only order parameters; the OMS and RiskKernel remain authoritative."""

    def choose(
        self,
        *,
        signed_delta: Decimal,
        quote: QuoteState,
        policy: ExecutionPolicyKind,
        urgency: Decimal = Decimal("0.5"),
    ) -> ExecutionChoice | None:
        if not signed_delta.is_finite():
            raise ValueError("signed execution delta must be finite")
        if not signed_delta:
            return None
        if not urgency.is_finite() or not Decimal("0") <= urgency <= Decimal("1"):
            raise ValueError("execution urgency must be between zero and one")
        side = "buy" if signed_delta > 0 else "sell"
        quantity = abs(signed_delta)
        if policy is ExecutionPolicyKind.IMMEDIATE:
            return ExecutionChoice(
                policy=policy,
                side=side,
                quantity=quantity,
                order_type="market",
                price=quote.ask
                if side == "buy" and quote.ask
                else quote.bid
                if side == "sell" and quote.bid
                else quote.mark,
                time_in_force="ioc",
                reason="immediate policy selected by deterministic urgency rule",
            )
        if policy is not ExecutionPolicyKind.PASSIVE_LIMIT:
            raise ValueError(f"unsupported execution policy: {policy}")
        passive_price = quote.bid if side == "buy" else quote.ask
        if passive_price is None:
            passive_price = quote.mark
        return ExecutionChoice(
            policy=policy,
            side=side,
            quantity=quantity,
            order_type="passive_limit",
            price=passive_price,
            time_in_force="gtc",
            reason="passive limit policy selected to reduce spread cost",
        )


def build_order_from_choice(
    *,
    choice: ExecutionChoice,
    parent_intent_id: UUID,
    execution_plan: ExecutionPlan,
    instrument: InstrumentIdentity,
    idempotency_key: str,
) -> Order:
    """Materialize a typed order without submitting it to a venue."""

    return Order(
        parent_intent_id=parent_intent_id,
        execution_plan_id=execution_plan.artifact_id,
        instrument=instrument,
        side=choice.side,
        quantity=choice.quantity,
        order_type=choice.order_type,
        price=choice.price,
        time_in_force=choice.time_in_force,
        idempotency_key=idempotency_key,
    )
