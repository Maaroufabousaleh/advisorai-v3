"""Required no-trade and simple benchmark portfolio comparisons."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PortfolioComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    expected_return: Decimal
    expected_cost: Decimal
    expected_net_utility: Decimal
    turnover: Decimal = Field(ge=0)
    max_concentration: Decimal = Field(ge=0, le=1)
    stable_out_of_sample: bool
    admitted: bool
    rejection_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_comparison(self) -> PortfolioComparison:
        if not self.name.strip():
            raise ValueError("portfolio comparison requires a name")
        if any(
            not value.is_finite()
            for value in (
                self.expected_return,
                self.expected_cost,
                self.expected_net_utility,
                self.turnover,
                self.max_concentration,
            )
        ):
            raise ValueError("portfolio comparison metrics must be finite")
        if self.expected_cost < 0 or self.turnover < 0:
            raise ValueError("portfolio comparison cost and turnover cannot be negative")
        if self.expected_net_utility != self.expected_return - self.expected_cost:
            raise ValueError("expected net utility must equal return less expected cost")
        if self.admitted and self.rejection_reasons:
            raise ValueError("an admitted portfolio cannot contain rejection reasons")
        return self


class PortfolioComparator:
    REQUIRED_BENCHMARKS = (
        "no_trade",
        "equal_weight",
        "inverse_volatility",
        "simple_risk_budget",
        "prior_champion",
    )

    def compare(
        self,
        *,
        name: str,
        expected_return: Decimal,
        expected_cost: Decimal,
        turnover: Decimal,
        max_concentration: Decimal,
        stable_out_of_sample: bool,
        risk_limit: Decimal,
    ) -> PortfolioComparison:
        if not all(
            value.is_finite()
            for value in (expected_return, expected_cost, turnover, max_concentration, risk_limit)
        ):
            raise ValueError("portfolio metrics must be finite")
        if (
            expected_cost < 0
            or turnover < 0
            or not Decimal("0") <= max_concentration <= Decimal("1")
            or not Decimal("0") <= risk_limit <= Decimal("1")
        ):
            raise ValueError("portfolio metrics are outside valid ranges")
        net = expected_return - expected_cost
        reasons: list[str] = []
        if not stable_out_of_sample:
            reasons.append("unstable_out_of_sample")
        if max_concentration > risk_limit:
            reasons.append("concentration_limit")
        return PortfolioComparison(
            name=name,
            expected_return=expected_return,
            expected_cost=expected_cost,
            expected_net_utility=net,
            turnover=turnover,
            max_concentration=max_concentration,
            stable_out_of_sample=stable_out_of_sample,
            admitted=not reasons,
            rejection_reasons=tuple(reasons),
        )

    def require_benchmark_set(
        self, comparisons: Iterable[PortfolioComparison]
    ) -> tuple[PortfolioComparison, ...]:
        """Require the non-negotiable benchmark set before admitting complexity."""

        ordered = tuple(comparisons)
        names = {comparison.name for comparison in ordered}
        missing = set(self.REQUIRED_BENCHMARKS).difference(names)
        if missing:
            raise ValueError(f"missing required portfolio benchmarks: {sorted(missing)}")
        if len(names) != len(ordered):
            raise ValueError("portfolio comparison names must be unique")
        return tuple(
            sorted(
                ordered,
                key=lambda comparison: (
                    self.REQUIRED_BENCHMARKS.index(comparison.name)
                    if comparison.name in self.REQUIRED_BENCHMARKS
                    else len(self.REQUIRED_BENCHMARKS),
                    comparison.name,
                ),
            )
        )
