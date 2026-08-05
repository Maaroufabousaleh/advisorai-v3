"""Minimal constrained target-portfolio constructor."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from advisorai.contracts import InstrumentIdentity, Snapshot, TargetPortfolio, TargetPosition
from advisorai.execution.account import AccountState


@dataclass(frozen=True, slots=True)
class PortfolioConstraints:
    """Hard construction bounds applied before RiskKernel evaluation.

    These bounds are deliberately optional and monotonic: a constructor can
    only reject a target that violates an approved bound.  RiskKernel remains
    the final veto and re-evaluates authoritative state at order time.
    """

    max_gross_notional: Decimal | None = None
    max_net_notional: Decimal | None = None
    max_turnover_notional: Decimal | None = None
    max_position_notional: Decimal | None = None
    max_leverage: Decimal | None = None
    max_concentration: Decimal | None = None
    min_cash_reserve: Decimal = Decimal("0")
    max_liquidity_participation: Decimal | None = None
    liquidity_notional: Mapping[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        optional = (
            self.max_gross_notional,
            self.max_net_notional,
            self.max_turnover_notional,
            self.max_position_notional,
            self.max_leverage,
            self.max_concentration,
            self.max_liquidity_participation,
            self.min_cash_reserve,
        )
        if any(value is not None and (not value.is_finite() or value < 0) for value in optional):
            raise ValueError("portfolio constraints must be finite and non-negative")
        if self.max_concentration is not None and self.max_concentration > 1:
            raise ValueError("max_concentration cannot exceed one")
        if self.max_liquidity_participation is not None and self.max_liquidity_participation > 1:
            raise ValueError("max_liquidity_participation cannot exceed one")
        if any(
            not instrument.strip() or not value.is_finite() or value <= 0
            for instrument, value in self.liquidity_notional.items()
        ):
            raise ValueError("liquidity bounds require positive finite instrument notionals")


class TargetPortfolioBuilder:
    """Build a target allocation with no-trade bands and explicit expected costs."""

    def __init__(
        self,
        *,
        minimum_trade_notional: Decimal = Decimal("0"),
        no_trade_band: Decimal = Decimal("0"),
        fee_bps: Decimal = Decimal("10"),
        spread_bps: Decimal = Decimal("0"),
        impact_bps: Decimal = Decimal("0"),
    ) -> None:
        if (
            any(
                not value.is_finite()
                for value in (
                    minimum_trade_notional,
                    no_trade_band,
                    fee_bps,
                    spread_bps,
                    impact_bps,
                )
            )
            or minimum_trade_notional < 0
            or no_trade_band < 0
            or min(fee_bps, spread_bps, impact_bps) < 0
        ):
            raise ValueError("portfolio thresholds and cost assumptions cannot be negative")
        self.minimum_trade_notional = minimum_trade_notional
        self.no_trade_band = no_trade_band
        self.fee_bps = fee_bps
        self.spread_bps = spread_bps
        self.impact_bps = impact_bps

    def build(
        self,
        *,
        snapshot: Snapshot,
        account: AccountState,
        targets: dict[InstrumentIdentity, Decimal],
        marks: dict[str, Decimal],
        expected_returns: Mapping[str, Decimal] | None = None,
        construction_method: str = "constrained_target_v1",
        risk_constraints_version: str = "risk-policy-unset",
        constraints: PortfolioConstraints | None = None,
    ) -> TargetPortfolio:
        positions: list[TargetPosition] = []
        expected_cost = Decimal("0")
        # Cash must reflect the signed notional delta as well as explicit
        # fees/spread/impact.  Using starting cash minus costs alone would
        # make a buy appear free (and a sell fail to release proceeds).
        cash_target = account.cash
        seen: set[str] = set()
        expected_returns = expected_returns or {}
        for instrument, target_quantity in sorted(
            targets.items(), key=lambda item: item[0].canonical_id
        ):
            if instrument.canonical_id in seen:
                raise ValueError("target portfolio cannot contain duplicate instruments")
            seen.add(instrument.canonical_id)
            mark = marks.get(instrument.canonical_id)
            if mark is None or not mark.is_finite() or mark <= 0:
                raise ValueError(f"missing positive mark for {instrument.canonical_id}")
            if not target_quantity.is_finite():
                raise ValueError("target quantities must be finite")
            current = account.positions.get(instrument.canonical_id, Decimal("0"))
            delta_notional = abs(target_quantity - current) * mark
            if delta_notional <= self.no_trade_band or delta_notional < self.minimum_trade_notional:
                target_quantity = current
                delta_notional = Decimal("0")
            else:
                cash_target -= (target_quantity - current) * mark
                expected_cost += (
                    delta_notional
                    * (self.fee_bps + self.spread_bps + self.impact_bps)
                    / Decimal("10000")
                )
            expected_return = expected_returns.get(instrument.canonical_id)
            if expected_return is not None and not expected_return.is_finite():
                raise ValueError("expected returns must be finite")
            positions.append(
                TargetPosition(
                    instrument=instrument,
                    target_quantity=target_quantity,
                    expected_return_after_costs=expected_return,
                )
            )
        cash_target -= expected_cost
        if cash_target < 0:
            raise ValueError("target portfolio costs exceed available account cash")
        if constraints is not None:
            self._validate_constraints(
                account=account,
                positions=positions,
                marks=marks,
                expected_cost=expected_cost,
                cash_target=cash_target,
                constraints=constraints,
            )
        return TargetPortfolio(
            snapshot_id=snapshot.artifact_id,
            positions=tuple(positions),
            # Reserve deterministic fees/spread/impact in the target rather
            # than pretending that the gross quantity is free to execute.
            cash_target=cash_target,
            construction_method=construction_method,
            expected_cost=expected_cost,
            risk_constraints_version=risk_constraints_version,
            no_trade_comparison="explicit_current_positions_no_trade_baseline",
        )

    @staticmethod
    def _validate_constraints(
        *,
        account: AccountState,
        positions: list[TargetPosition],
        marks: Mapping[str, Decimal],
        expected_cost: Decimal,
        cash_target: Decimal,
        constraints: PortfolioConstraints,
    ) -> None:
        notionals = {
            position.instrument.canonical_id: position.target_quantity
            * marks[position.instrument.canonical_id]
            for position in positions
        }
        gross = sum(abs(value) for value in notionals.values())
        net = sum(notionals.values())
        turnover = sum(
            abs(
                notionals[instrument]
                - account.positions.get(instrument, Decimal("0")) * marks[instrument]
            )
            for instrument in notionals
        )
        if constraints.max_gross_notional is not None and gross > constraints.max_gross_notional:
            raise ValueError("target portfolio exceeds max gross notional")
        if constraints.max_net_notional is not None and abs(net) > constraints.max_net_notional:
            raise ValueError("target portfolio exceeds max net notional")
        if (
            constraints.max_turnover_notional is not None
            and turnover > constraints.max_turnover_notional
        ):
            raise ValueError("target portfolio exceeds max turnover notional")
        if constraints.max_position_notional is not None and any(
            abs(value) > constraints.max_position_notional for value in notionals.values()
        ):
            raise ValueError("target portfolio exceeds max position notional")
        if constraints.max_leverage is not None:
            equity = account.equity()
            leverage = (
                gross / equity if equity > 0 else (Decimal("999999") if gross else Decimal("0"))
            )
            if leverage > constraints.max_leverage:
                raise ValueError("target portfolio exceeds max leverage")
        if constraints.max_concentration is not None and gross:
            concentration = max(abs(value) / gross for value in notionals.values())
            if concentration > constraints.max_concentration:
                raise ValueError("target portfolio exceeds max concentration")
        if cash_target < constraints.min_cash_reserve:
            raise ValueError("target portfolio violates minimum cash reserve")
        if constraints.max_liquidity_participation is not None:
            for instrument, target_notional in notionals.items():
                liquidity = constraints.liquidity_notional.get(instrument)
                if liquidity is None:
                    raise ValueError(f"missing liquidity for {instrument}")
                delta = abs(
                    target_notional
                    - account.positions.get(instrument, Decimal("0")) * marks[instrument]
                )
                if delta / liquidity > constraints.max_liquidity_participation:
                    raise ValueError(
                        f"target portfolio exceeds liquidity participation for {instrument}"
                    )
