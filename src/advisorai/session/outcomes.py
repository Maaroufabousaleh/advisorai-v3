"""Explicit quote-currency conversion and measured quality for paper outcomes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from advisorai.contracts import Fill, Order, Snapshot
from advisorai.execution import RiskMarketState
from advisorai.learning import PaperDecisionRecord, PaperOutcome


@dataclass(frozen=True, slots=True)
class QuoteToUsdRate:
    """PIT-bound USD value for one unit of a quote currency."""

    quote_currency: str
    usd_per_quote: Decimal
    as_of: datetime
    snapshot_id: UUID
    evidence_id: UUID
    source: str

    def __post_init__(self) -> None:
        currency = self.quote_currency.strip().upper()
        source = self.source.strip()
        if not currency or not source:
            raise ValueError("FX rate requires a currency and source")
        if not isinstance(self.usd_per_quote, Decimal) or (
            not self.usd_per_quote.is_finite() or self.usd_per_quote <= 0
        ):
            raise ValueError("FX rate must be a positive finite Decimal")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("FX rate timestamp must include a timezone")
        object.__setattr__(self, "quote_currency", currency)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "as_of", self.as_of.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class PaperOutcomeQuality:
    """Measured data, latency, and execution reliability for one outcome."""

    data_reliability: Decimal
    latency_ms: int
    failure_rate: Decimal
    evidence_id: UUID
    source: str
    snapshot_id: UUID
    as_of: datetime

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, Decimal)
            or not value.is_finite()
            or not Decimal("0") <= value <= Decimal("1")
            for value in (self.data_reliability, self.failure_rate)
        ):
            raise ValueError("paper outcome quality rates must be finite values in [0, 1]")
        if not isinstance(self.latency_ms, int) or isinstance(self.latency_ms, bool):
            raise ValueError("paper outcome latency must be an integer number of milliseconds")
        if self.latency_ms < 0:
            raise ValueError("paper outcome latency cannot be negative")
        if not self.source.strip():
            raise ValueError("paper outcome quality requires an evidence source")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("paper outcome quality timestamp must include a timezone")
        object.__setattr__(self, "source", self.source.strip())
        object.__setattr__(self, "as_of", self.as_of.astimezone(UTC))


FxRateProvider = Callable[[Snapshot], QuoteToUsdRate]
OutcomeQualityProvider = Callable[..., PaperOutcomeQuality]


class QuoteAwarePaperOutcomeAdapter:
    """Convert quoted market movement and fees to USD using same-snapshot FX.

    The rate and quality callbacks are explicit data/evidence boundaries. This
    adapter marks every decision fill from its execution price to the outcome
    mark. It never assumes a stablecoin peg, synthesizes quality values, or
    accepts a conversion rate from a different snapshot or cutoff.
    """

    def __init__(
        self,
        *,
        quote_currency: str,
        fx_rate_provider: FxRateProvider,
        quality_provider: OutcomeQualityProvider,
    ) -> None:
        self.quote_currency = quote_currency.strip().upper()
        if not self.quote_currency:
            raise ValueError("quote currency is required")
        if not callable(fx_rate_provider) or not callable(quality_provider):
            raise ValueError("quote-aware outcomes require FX and quality providers")
        self.fx_rate_provider = fx_rate_provider
        self.quality_provider = quality_provider

    def build_outcome(
        self,
        *,
        decision: PaperDecisionRecord,
        as_of: datetime,
        snapshot: Snapshot,
        market: RiskMarketState,
        fills: tuple[Fill, ...],
        orders: Mapping[UUID, Order],
        initial_marks: Mapping[str, Decimal],
    ) -> PaperOutcome:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("quote-aware outcome requires an aware, closed decision horizon")
        cutoff = as_of.astimezone(UTC)
        if cutoff < decision.horizon_end:
            raise ValueError("quote-aware outcome requires an aware, closed decision horizon")
        if snapshot.as_of.astimezone(UTC) != cutoff:
            raise ValueError("outcome and FX snapshot must share the same current cutoff")
        if "," in decision.asset or not decision.asset.strip():
            raise ValueError("quote-aware outcome supports exactly one canonical asset")
        fx = self.fx_rate_provider(snapshot)
        if not isinstance(fx, QuoteToUsdRate):
            raise ValueError("FX provider must return a typed QuoteToUsdRate")
        if (
            fx.quote_currency != self.quote_currency
            or fx.snapshot_id != snapshot.artifact_id
            or fx.as_of != cutoff
        ):
            raise ValueError("FX rate is not current and bound to the outcome snapshot")
        current_mark = market.marks.get(decision.asset)
        if (
            not isinstance(current_mark, Decimal)
            or not current_mark.is_finite()
            or current_mark <= 0
        ):
            raise ValueError("quote-aware outcome requires a positive current mark")

        if len({fill.artifact_id for fill in fills}) != len(fills):
            raise ValueError("outcome input contains duplicate fill identities")
        if len(decision.order_ids) != len(set(decision.order_ids)) or len(
            decision.fill_ids
        ) != len(set(decision.fill_ids)):
            raise ValueError("outcome decision contains duplicate order or fill identities")
        if any(order.artifact_id != order_id for order_id, order in orders.items()):
            raise ValueError("outcome order map contains a mismatched local identity")
        fill_by_id = {fill.artifact_id: fill for fill in fills}
        missing_fill_ids = set(decision.fill_ids) - set(fill_by_id)
        if missing_fill_ids:
            raise ValueError("outcome decision references a fill absent from current OMS state")
        missing_order_ids = set(decision.order_ids) - set(orders)
        if missing_order_ids:
            raise ValueError("outcome decision references an order absent from current OMS state")
        related_fills = tuple(fill for fill in fills if fill.order_id in decision.order_ids)
        quote_utility = Decimal("0")
        quote_fees = Decimal("0")
        filled_by_order: dict[UUID, Decimal] = {}
        for fill in related_fills:
            order = orders.get(fill.order_id)
            if order is None or fill.order_id not in decision.order_ids:
                raise ValueError("outcome fill is not linked to one of its decision orders")
            if order.instrument.canonical_id != decision.asset:
                raise ValueError("outcome fill instrument differs from the decision asset")
            if fill.fee and fill.fee_currency != self.quote_currency:
                raise ValueError("outcome fee currency differs from the configured quote currency")
            quote_fees += fill.fee
            quote_utility += (
                (current_mark - fill.price) * fill.quantity
                if order.side == "buy"
                else (fill.price - current_mark) * fill.quantity
            )
            filled_by_order[order.artifact_id] = (
                filled_by_order.get(order.artifact_id, Decimal("0")) + fill.quantity
            )
        if any(
            quantity > orders[order_id].quantity
            for order_id, quantity in filled_by_order.items()
        ):
            raise ValueError("current fills exceed a decision order quantity")

        quality = self.quality_provider(
            decision=decision,
            as_of=cutoff,
            snapshot=snapshot,
            market=market,
            fills=related_fills,
        )
        if not isinstance(quality, PaperOutcomeQuality):
            raise ValueError("quality provider must return typed PaperOutcomeQuality evidence")
        if quality.snapshot_id != snapshot.artifact_id or quality.as_of != cutoff:
            raise ValueError("quality evidence is not current and bound to the outcome snapshot")
        for order_id in decision.order_ids:
            if orders[order_id].instrument.canonical_id != decision.asset:
                raise ValueError("outcome decision contains an order for another asset")
        quote_utility -= quote_fees
        return PaperOutcome(
            decision_id=decision.decision_id,
            as_of=cutoff,
            forecast_value=None,
            realized_value=current_mark * fx.usd_per_quote,
            net_utility=quote_utility * fx.usd_per_quote,
            cost_usd=quote_fees * fx.usd_per_quote,
            fill_quality=(
                Decimal("1") if related_fills or not decision.order_ids else Decimal("0")
            ),
            data_reliability=quality.data_reliability,
            latency_ms=quality.latency_ms,
            failure_rate=quality.failure_rate,
            fx_quote_currency=fx.quote_currency,
            fx_usd_per_quote=fx.usd_per_quote,
            fx_snapshot_id=fx.snapshot_id,
            fx_evidence_id=fx.evidence_id,
            fx_rate_source=fx.source,
            fx_rate_as_of=fx.as_of,
            quality_evidence_id=quality.evidence_id,
            quality_source=quality.source,
            quality_snapshot_id=quality.snapshot_id,
            quality_as_of=quality.as_of,
        )


__all__ = [
    "OutcomeQualityProvider",
    "PaperOutcomeQuality",
    "QuoteAwarePaperOutcomeAdapter",
    "QuoteToUsdRate",
]
