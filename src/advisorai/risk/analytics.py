"""Robust covariance, factor, liquidity, margin, and scenario calculations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from math import ceil

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FactorExposure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    portfolio: str
    exposures: tuple[tuple[str, Decimal], ...]
    gross_exposure: Decimal = Field(ge=0)
    net_exposure: Decimal

    @field_validator("exposures")
    @classmethod
    def normalize_factor_names(
        cls, value: tuple[tuple[str, Decimal], ...]
    ) -> tuple[tuple[str, Decimal], ...]:
        normalized = tuple((name.strip(), exposure) for name, exposure in value)
        if any(not name for name, _ in normalized):
            raise ValueError("factor names cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_exposure(self) -> FactorExposure:
        if not self.portfolio.strip() or any(not name.strip() for name, _ in self.exposures):
            raise ValueError("factor exposures require named portfolio and factors")
        names = [name for name, _ in self.exposures]
        if len(names) != len(set(names)):
            raise ValueError("factor names must be unique")
        values = [value for _, value in self.exposures]
        if any(
            not value.is_finite() for value in (*values, self.gross_exposure, self.net_exposure)
        ):
            raise ValueError("factor exposures must be finite")
        if self.gross_exposure != sum(abs(value) for value in values):
            raise ValueError("gross factor exposure does not match components")
        if self.net_exposure != sum(values):
            raise ValueError("net factor exposure does not match components")
        return self


class CapacityEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument: str
    daily_volume: Decimal = Field(gt=0)
    participation_limit: Decimal = Field(gt=0, le=1)
    max_notional: Decimal = Field(ge=0)
    expected_impact_bps: Decimal = Field(ge=0)
    capacity_passed: bool

    @model_validator(mode="after")
    def validate_capacity(self) -> CapacityEstimate:
        if not self.instrument.strip() or any(
            not value.is_finite()
            for value in (
                self.daily_volume,
                self.participation_limit,
                self.max_notional,
                self.expected_impact_bps,
            )
        ):
            raise ValueError("capacity estimate identity and impact must be valid")
        if self.max_notional != self.daily_volume * self.participation_limit:
            raise ValueError("capacity max_notional must match volume participation")
        return self


class MarginEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    equity: Decimal
    initial_margin: Decimal = Field(ge=0)
    maintenance_margin: Decimal = Field(ge=0)
    liquidation_buffer: Decimal
    passed: bool

    @model_validator(mode="after")
    def validate_margin(self) -> MarginEstimate:
        if any(
            not value.is_finite()
            for value in (
                self.equity,
                self.initial_margin,
                self.maintenance_margin,
                self.liquidation_buffer,
            )
        ):
            raise ValueError("margin estimate values must be finite")
        if self.liquidation_buffer != self.equity - self.maintenance_margin:
            raise ValueError("liquidation buffer must equal equity less maintenance margin")
        return self


class VolatilityEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument: str
    observations: int = Field(ge=2)
    periods_per_year: int = Field(gt=0)
    annualized_volatility: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def validate_volatility(self) -> VolatilityEstimate:
        if not self.instrument.strip() or not self.annualized_volatility.is_finite():
            raise ValueError("volatility estimate requires a finite instrument value")
        return self


class TailRiskEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    confidence: Decimal = Field(gt=Decimal("0"), lt=Decimal("1"))
    observations: int = Field(gt=0)
    var_loss: Decimal = Field(ge=0)
    expected_shortfall_loss: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def validate_tail(self) -> TailRiskEstimate:
        if not self.var_loss.is_finite() or not self.expected_shortfall_loss.is_finite():
            raise ValueError("tail-risk values must be finite")
        if self.expected_shortfall_loss < self.var_loss:
            raise ValueError("expected shortfall cannot be below VaR")
        return self


class StressResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario: str
    shocked_notional: Decimal
    pnl_change: Decimal
    liquidity_loss: Decimal
    margin_breach: bool
    passed: bool
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_stress_result(self) -> StressResult:
        if not self.scenario.strip() or any(
            not value.is_finite()
            for value in (self.shocked_notional, self.pnl_change, self.liquidity_loss)
        ):
            raise ValueError("stress result requires a named scenario and finite values")
        if self.shocked_notional < 0 or self.liquidity_loss < 0:
            raise ValueError("stress notionals and liquidity loss cannot be negative")
        if self.passed and self.reasons:
            raise ValueError("a passed stress result cannot contain rejection reasons")
        if not self.passed and not self.reasons:
            raise ValueError("a failed stress result requires rejection reasons")
        return self


class StressScenario(StrEnum):
    PRICE_GAP = "price_gap"
    VOLATILITY_JUMP = "volatility_jump"
    CORRELATION_BREAKDOWN = "correlation_breakdown"
    SPREAD_DEPTH_COLLAPSE = "spread_depth_collapse"
    HALT_DELIST = "halt_delist"
    FUNDING_LIQUIDATION_CASCADE = "funding_liquidation_cascade"
    STABLECOIN_DEPEG = "stablecoin_depeg"
    VENUE_OUTAGE = "venue_outage"
    WITHDRAWAL_FREEZE = "withdrawal_freeze"
    COUNTERPARTY_FAILURE = "counterparty_failure"
    STALE_DUPLICATED_DATA = "stale_duplicated_data"
    CLOCK_DRIFT = "clock_drift"
    DUPLICATE_PARTIAL_FILL = "duplicate_partial_fill"


@dataclass(frozen=True, slots=True)
class RiskAnalytics:
    covariance_shrinkage: Decimal = Decimal("0.10")

    def __post_init__(self) -> None:
        if not self.covariance_shrinkage.is_finite() or not Decimal(
            "0"
        ) <= self.covariance_shrinkage <= Decimal("1"):
            raise ValueError("covariance shrinkage must be between zero and one")

    def volatility(
        self,
        *,
        instrument: str,
        returns: Sequence[Decimal],
        periods_per_year: int = 365,
    ) -> VolatilityEstimate:
        if len(returns) < 2 or periods_per_year < 1:
            raise ValueError("volatility needs two returns and a positive annualization factor")
        if any(not value.is_finite() for value in returns):
            raise ValueError("volatility returns must be finite")
        mean = sum(returns) / Decimal(len(returns))
        variance = sum((value - mean) ** 2 for value in returns) / Decimal(len(returns) - 1)
        annualized = Decimal(str(float(variance * periods_per_year) ** 0.5))
        return VolatilityEstimate(
            instrument=instrument,
            observations=len(returns),
            periods_per_year=periods_per_year,
            annualized_volatility=annualized,
        )

    def beta(self, *, returns: Sequence[Decimal], benchmark_returns: Sequence[Decimal]) -> Decimal:
        if len(returns) != len(benchmark_returns) or len(returns) < 2:
            raise ValueError("beta series must be equal length with at least two observations")
        if any(not value.is_finite() for value in (*returns, *benchmark_returns)):
            raise ValueError("beta returns must be finite")
        mean_return = sum(returns) / Decimal(len(returns))
        mean_benchmark = sum(benchmark_returns) / Decimal(len(benchmark_returns))
        covariance = sum(
            (left - mean_return) * (right - mean_benchmark)
            for left, right in zip(returns, benchmark_returns, strict=True)
        )
        variance = sum((value - mean_benchmark) ** 2 for value in benchmark_returns)
        if variance == 0:
            raise ValueError("benchmark variance must be positive for beta")
        return covariance / variance

    def tail_risk(
        self, *, returns: Sequence[Decimal], confidence: Decimal = Decimal("0.95")
    ) -> TailRiskEstimate:
        if not returns:
            raise ValueError("tail risk needs at least one return")
        if any(not value.is_finite() for value in returns):
            raise ValueError("tail returns must be finite")
        if not Decimal("0") < confidence < Decimal("1"):
            raise ValueError("confidence must be between zero and one")
        losses = sorted((-value for value in returns), reverse=True)
        tail_count = max(1, ceil(float(len(losses) * (Decimal("1") - confidence))))
        tail = losses[:tail_count]
        return TailRiskEstimate(
            confidence=confidence,
            observations=len(returns),
            var_loss=max(Decimal("0"), losses[tail_count - 1]),
            expected_shortfall_loss=max(Decimal("0"), sum(tail) / Decimal(len(tail))),
        )

    def robust_covariance(
        self, returns: Mapping[str, Sequence[Decimal]]
    ) -> tuple[tuple[str, ...], tuple[tuple[Decimal, ...], ...]]:
        instruments = tuple(sorted(returns))
        if not instruments or any(len(returns[name]) < 2 for name in instruments):
            raise ValueError("each instrument needs at least two returns")
        if any(not name.strip() for name in instruments):
            raise ValueError("covariance instrument names cannot be blank")
        lengths = {len(returns[name]) for name in instruments}
        if len(lengths) != 1:
            raise ValueError("covariance series must have equal lengths")
        if any(not value.is_finite() for series in returns.values() for value in series):
            raise ValueError("covariance returns must be finite")
        means = {name: sum(returns[name]) / Decimal(len(returns[name])) for name in instruments}
        raw: list[list[Decimal]] = []
        for left in instruments:
            row: list[Decimal] = []
            for right in instruments:
                count = min(len(returns[left]), len(returns[right]))
                covariance = sum(
                    (returns[left][index] - means[left]) * (returns[right][index] - means[right])
                    for index in range(count)
                ) / Decimal(max(count - 1, 1))
                row.append(covariance)
            raw.append(row)
        diagonal_mean = sum(raw[index][index] for index in range(len(instruments))) / Decimal(
            len(instruments)
        )
        shrunk = tuple(
            tuple(
                value * (Decimal("1") - self.covariance_shrinkage)
                + (
                    diagonal_mean * self.covariance_shrinkage
                    if row_index == column_index
                    else Decimal("0")
                )
                for column_index, value in enumerate(row)
            )
            for row_index, row in enumerate(raw)
        )
        return instruments, shrunk

    def factor_exposure(
        self,
        *,
        portfolio: str,
        positions: Mapping[str, Decimal],
        factor_loadings: Mapping[str, Mapping[str, Decimal]],
    ) -> FactorExposure:
        factors: dict[str, Decimal] = {}
        if any(not value.is_finite() for value in positions.values()) or any(
            not value.is_finite()
            for loadings in factor_loadings.values()
            for value in loadings.values()
        ):
            raise ValueError("factor positions and loadings must be finite")
        for instrument, notional in positions.items():
            for factor, loading in factor_loadings.get(instrument, {}).items():
                factors[factor] = factors.get(factor, Decimal("0")) + notional * loading
        values = tuple(sorted(factors.items()))
        return FactorExposure(
            portfolio=portfolio,
            exposures=values,
            gross_exposure=sum(abs(value) for _, value in values),
            net_exposure=sum(value for _, value in values),
        )

    def capacity(
        self,
        *,
        instrument: str,
        daily_volume: Decimal,
        participation_limit: Decimal,
        order_notional: Decimal,
        spread_bps: Decimal,
    ) -> CapacityEstimate:
        if any(
            not value.is_finite()
            for value in (daily_volume, participation_limit, order_notional, spread_bps)
        ):
            raise ValueError("capacity inputs must be finite")
        if daily_volume <= 0 or not Decimal("0") < participation_limit <= Decimal("1"):
            raise ValueError("capacity inputs must have positive volume and bounded participation")
        if order_notional < 0 or spread_bps < 0:
            raise ValueError("capacity order and spread values cannot be negative")
        max_notional = daily_volume * participation_limit
        impact = spread_bps + (order_notional / daily_volume * Decimal("10000"))
        return CapacityEstimate(
            instrument=instrument,
            daily_volume=daily_volume,
            participation_limit=participation_limit,
            max_notional=max_notional,
            expected_impact_bps=impact,
            capacity_passed=order_notional <= max_notional,
        )

    def margin(
        self,
        *,
        equity: Decimal,
        gross_notional: Decimal,
        initial_rate: Decimal,
        maintenance_rate: Decimal,
    ) -> MarginEstimate:
        if any(
            not value.is_finite()
            for value in (equity, gross_notional, initial_rate, maintenance_rate)
        ):
            raise ValueError("margin inputs must be finite")
        if equity < 0 or gross_notional < 0 or not Decimal("0") <= maintenance_rate <= initial_rate:
            raise ValueError("margin inputs are outside their valid bounds")
        if initial_rate < 0:
            raise ValueError("initial margin rate cannot be negative")
        initial = gross_notional * initial_rate
        maintenance = gross_notional * maintenance_rate
        buffer = equity - maintenance
        return MarginEstimate(
            equity=equity,
            initial_margin=initial,
            maintenance_margin=maintenance,
            liquidation_buffer=buffer,
            passed=equity >= initial and buffer >= 0,
        )

    def stress(
        self,
        *,
        scenario: str,
        notionals: Mapping[str, Decimal],
        price_shock: Decimal,
        liquidity_multiplier: Decimal,
        equity: Decimal,
        maintenance_rate: Decimal,
    ) -> StressResult:
        if not scenario.strip() or any(
            not value.is_finite()
            for value in (
                price_shock,
                liquidity_multiplier,
                equity,
                maintenance_rate,
                *notionals.values(),
            )
        ):
            raise ValueError("stress inputs must be finite")
        if not Decimal("0") <= liquidity_multiplier <= Decimal("1"):
            raise ValueError("liquidity multiplier must be between zero and one")
        if maintenance_rate < 0 or equity < 0:
            raise ValueError("maintenance rate and equity cannot be negative")
        shocked = sum(abs(value) for value in notionals.values())
        pnl_change = sum(value * price_shock for value in notionals.values())
        liquidity_loss = shocked * (Decimal("1") - liquidity_multiplier)
        margin_breach = equity + pnl_change < shocked * maintenance_rate
        reasons: list[str] = []
        if margin_breach:
            reasons.append("maintenance_margin_breach")
        if liquidity_multiplier <= 0:
            reasons.append("liquidity_collapsed")
        return StressResult(
            scenario=scenario,
            shocked_notional=shocked,
            pnl_change=pnl_change,
            liquidity_loss=liquidity_loss,
            margin_breach=margin_breach,
            passed=not reasons,
            reasons=tuple(reasons),
        )

    def stress_suite(
        self,
        *,
        notionals: Mapping[str, Decimal],
        equity: Decimal,
        maintenance_rate: Decimal,
    ) -> tuple[StressResult, ...]:
        shocks = {
            StressScenario.PRICE_GAP: (Decimal("-0.20"), Decimal("0.7")),
            StressScenario.VOLATILITY_JUMP: (Decimal("-0.15"), Decimal("0.8")),
            StressScenario.CORRELATION_BREAKDOWN: (Decimal("-0.18"), Decimal("0.8")),
            StressScenario.SPREAD_DEPTH_COLLAPSE: (Decimal("-0.10"), Decimal("0.2")),
            StressScenario.HALT_DELIST: (Decimal("-1"), Decimal("0")),
            StressScenario.FUNDING_LIQUIDATION_CASCADE: (Decimal("-0.35"), Decimal("0.3")),
            StressScenario.STABLECOIN_DEPEG: (Decimal("-0.10"), Decimal("0.5")),
            StressScenario.VENUE_OUTAGE: (Decimal("0"), Decimal("0.3")),
            StressScenario.WITHDRAWAL_FREEZE: (Decimal("0"), Decimal("0.5")),
            StressScenario.COUNTERPARTY_FAILURE: (Decimal("-0.25"), Decimal("0.4")),
            StressScenario.STALE_DUPLICATED_DATA: (Decimal("-0.15"), Decimal("0.7")),
            StressScenario.CLOCK_DRIFT: (Decimal("-0.05"), Decimal("0.8")),
            StressScenario.DUPLICATE_PARTIAL_FILL: (Decimal("-0.20"), Decimal("0.6")),
        }
        return tuple(
            self.stress(
                scenario=scenario.value,
                notionals=notionals,
                price_shock=shock,
                liquidity_multiplier=liquidity,
                equity=equity,
                maintenance_rate=maintenance_rate,
            )
            for scenario, (shock, liquidity) in shocks.items()
        )
