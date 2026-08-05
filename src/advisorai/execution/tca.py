"""Initial paper transaction-cost analysis."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from advisorai.contracts import Fill


class TCAReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: UUID
    arrival_price: Decimal = Field(gt=0)
    filled_quantity: Decimal = Field(ge=0)
    notional: Decimal = Field(ge=0)
    fees: Decimal = Field(ge=0)
    spread_cost: Decimal = Field(ge=0)
    delay_cost: Decimal = Field(ge=0)
    opportunity_cost: Decimal = Field(ge=0)
    adverse_selection: Decimal = Field(ge=0)
    implementation_shortfall: Decimal
    fill_ratio: Decimal = Field(ge=0, le=1)
    market_impact: Decimal = Field(default=Decimal("0"), ge=0)
    venue: str | None = None
    venue_performance: Decimal | None = None

    @model_validator(mode="after")
    def validate_metrics(self) -> TCAReport:
        values = (
            self.arrival_price,
            self.filled_quantity,
            self.notional,
            self.fees,
            self.spread_cost,
            self.delay_cost,
            self.opportunity_cost,
            self.adverse_selection,
            self.implementation_shortfall,
            self.fill_ratio,
            self.market_impact,
        )
        if any(not value.is_finite() for value in values):
            raise ValueError("TCA metrics must be finite")
        if self.venue is not None and not self.venue.strip():
            raise ValueError("TCA venue cannot be blank")
        if self.venue_performance is not None and not self.venue_performance.is_finite():
            raise ValueError("TCA venue performance must be finite")
        return self


def compute_tca(
    *,
    order_id: UUID,
    order_quantity: Decimal,
    side: str,
    arrival_price: Decimal,
    fills: tuple[Fill, ...],
    best_bid: Decimal | None = None,
    best_ask: Decimal | None = None,
    delay_cost: Decimal = Decimal("0"),
    opportunity_cost: Decimal = Decimal("0"),
    adverse_selection: Decimal = Decimal("0"),
    market_impact: Decimal = Decimal("0"),
    venue: str | None = None,
    venue_performance: Decimal | None = None,
) -> TCAReport:
    side = side.strip().lower()
    if (
        side not in {"buy", "sell"}
        or not arrival_price.is_finite()
        or not order_quantity.is_finite()
        or not delay_cost.is_finite()
        or not opportunity_cost.is_finite()
        or not adverse_selection.is_finite()
        or not market_impact.is_finite()
        or arrival_price <= 0
        or order_quantity <= 0
        or min(delay_cost, opportunity_cost, adverse_selection, market_impact) < 0
    ):
        raise ValueError("invalid side, quantity, arrival price, or TCA cost")
    if venue is not None and not venue.strip():
        raise ValueError("TCA venue cannot be blank")
    if venue_performance is not None and not venue_performance.is_finite():
        raise ValueError("TCA venue performance must be finite")
    if (
        best_bid is not None
        and (not best_bid.is_finite() or best_bid <= 0)
        or best_ask is not None
        and (not best_ask.is_finite() or best_ask <= 0)
    ):
        raise ValueError("best bid and ask must be positive when supplied")
    if any(fill.order_id != order_id for fill in fills):
        raise ValueError("TCA fills must belong to the requested order")
    filled_quantity = sum(fill.quantity for fill in fills)
    if filled_quantity > order_quantity:
        raise ValueError("TCA fills cannot exceed the order quantity")
    notional = sum(fill.quantity * fill.price for fill in fills)
    fees = sum(fill.fee for fill in fills)
    average_price = notional / filled_quantity if filled_quantity else arrival_price
    direction = Decimal("1") if side == "buy" else Decimal("-1")
    implementation_shortfall = (
        (average_price - arrival_price) * direction * filled_quantity
        + fees
        + delay_cost
        + opportunity_cost
        + market_impact
    )
    spread_cost = Decimal("0")
    if best_bid is not None and best_ask is not None and best_ask >= best_bid:
        spread_cost = ((best_ask - best_bid) / 2) * filled_quantity
    if best_bid is not None and best_ask is not None and best_ask < best_bid:
        raise ValueError("best ask cannot be below best bid")
    implementation_shortfall += spread_cost + adverse_selection
    return TCAReport(
        order_id=order_id,
        arrival_price=arrival_price,
        filled_quantity=filled_quantity,
        notional=notional,
        fees=fees,
        spread_cost=spread_cost,
        delay_cost=delay_cost,
        opportunity_cost=opportunity_cost,
        adverse_selection=adverse_selection,
        implementation_shortfall=implementation_shortfall,
        fill_ratio=filled_quantity / order_quantity,
        market_impact=market_impact,
        venue=venue.strip() if venue is not None else None,
        venue_performance=venue_performance,
    )
