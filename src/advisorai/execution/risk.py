"""Deterministic RiskKernel and kill switch for paper orders."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from advisorai.contracts import (
    Order,
    RiskDecision,
    RiskOutcome,
    RiskPolicy,
    TargetPortfolio,
)
from advisorai.execution.account import AccountState
from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers


class KillSwitch:
    """Independent kill switch with optional durable incident-ledger state."""

    def __init__(self, ledgers: SqliteLedgers | None = None) -> None:
        self._tripped = False
        self._reason: str | None = None
        self._ledgers = ledgers
        if ledgers is not None:
            self._hydrate()

    def _hydrate(self) -> None:
        assert self._ledgers is not None
        for event in self._ledgers.events(LedgerNamespace.INCIDENT):
            if event.event_type == "kill_switch_tripped":
                self._tripped = True
                self._reason = str(event.payload.get("reason", "durably tripped"))
            elif event.event_type == "kill_switch_reset":
                self._tripped = False
                self._reason = None

    def _record(self, event_type: str, payload: dict[str, object]) -> None:
        if self._ledgers is None:
            return
        sequence = len(self._ledgers.events(LedgerNamespace.INCIDENT)) + 1
        digest = hashlib.sha256(f"{event_type}:{sequence}:{payload}".encode()).hexdigest()[:16]
        self._ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.INCIDENT,
                event_type=event_type,
                idempotency_key=f"kill-switch:{sequence}:{digest}",
                occurred_at=datetime.now(UTC),
                payload=payload,
            )
        )

    @property
    def tripped(self) -> bool:
        return self._tripped

    @property
    def reason(self) -> str | None:
        return self._reason

    def trip(self, reason: str) -> None:
        if not reason.strip():
            raise ValueError("kill switch trip requires a reason")
        normalized = reason.strip()
        if self._tripped and self._reason == normalized:
            return
        self._record("kill_switch_tripped", {"reason": normalized})
        self._tripped = True
        self._reason = normalized

    def reset(self, *, approved_by: str) -> None:
        if not approved_by.strip():
            raise ValueError("kill switch reset requires explicit human approver")
        normalized = approved_by.strip()
        if not self._tripped:
            return
        self._record("kill_switch_reset", {"approved_by": normalized})
        self._tripped = False
        self._reason = None


@dataclass(frozen=True, slots=True)
class RiskMarketState:
    marks: dict[str, Decimal]
    stale_instruments: frozenset[str] = frozenset()
    market_state_hash: str = ""
    stale_seconds: dict[str, int] = field(default_factory=dict)
    disagreed_instruments: frozenset[str] = frozenset()
    clock_drift_seconds: int = 0
    volatility: dict[str, Decimal] = field(default_factory=dict)
    liquidity_notional: dict[str, Decimal] = field(default_factory=dict)
    spread_bps: dict[str, Decimal] = field(default_factory=dict)
    expected_slippage_bps: dict[str, Decimal] = field(default_factory=dict)
    expected_impact_bps: dict[str, Decimal] = field(default_factory=dict)
    venue_healthy: dict[str, bool] = field(default_factory=dict)
    model_drift: bool = False
    unsupported_regime: bool = False
    expired_forecasts: frozenset[str] = frozenset()
    reconciliation_clean: bool = True
    order_count: int = 0
    funding_cost_bps: Decimal = Decimal("0")
    borrow_cost_bps: Decimal = Decimal("0")
    counterparty_exposure: Decimal = Decimal("0")
    collateral_available: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.clock_drift_seconds, int) or isinstance(
            self.clock_drift_seconds, bool
        ):
            raise ValueError("clock drift must be an integer number of seconds")
        if not isinstance(self.order_count, int) or isinstance(self.order_count, bool):
            raise ValueError("order count must be an integer")
        if (
            not isinstance(self.funding_cost_bps, Decimal)
            or not isinstance(self.borrow_cost_bps, Decimal)
            or not isinstance(self.counterparty_exposure, Decimal)
            or (
                self.collateral_available is not None
                and not isinstance(self.collateral_available, Decimal)
            )
        ):
            raise ValueError("risk market scalar values must be Decimal instances")
        if self.market_state_hash and (
            len(self.market_state_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.market_state_hash)
        ):
            raise ValueError("market_state_hash must be a lowercase SHA-256 digest")
        if any(
            not key.strip()
            for key in (
                *self.marks,
                *self.stale_seconds,
                *self.volatility,
                *self.liquidity_notional,
                *self.spread_bps,
                *self.expected_slippage_bps,
                *self.expected_impact_bps,
                *self.venue_healthy,
                *self.expired_forecasts,
                *self.stale_instruments,
                *self.disagreed_instruments,
            )
        ):
            raise ValueError("risk market instrument identifiers cannot be blank")
        if any(
            not isinstance(value, Decimal)
            for values in (
                self.marks,
                self.volatility,
                self.liquidity_notional,
                self.spread_bps,
                self.expected_slippage_bps,
                self.expected_impact_bps,
            )
            for value in values.values()
        ):
            raise ValueError("risk market numeric values must be Decimal instances")
        if any(not isinstance(value, bool) for value in self.venue_healthy.values()):
            raise ValueError("venue health values must be booleans")
        if any(
            not value.is_finite()
            for values in (
                self.marks,
                self.volatility,
                self.liquidity_notional,
                self.spread_bps,
                self.expected_slippage_bps,
                self.expected_impact_bps,
            )
            for value in values.values()
        ):
            raise ValueError("risk market numeric values must be finite")
        if (
            not self.funding_cost_bps.is_finite()
            or not self.borrow_cost_bps.is_finite()
            or not self.counterparty_exposure.is_finite()
        ):
            raise ValueError("risk market counters must be finite")
        if any(value <= 0 for value in self.marks.values()):
            raise ValueError("risk marks must be positive")
        if any(value < 0 for value in self.stale_seconds.values()):
            raise ValueError("stale durations cannot be negative")
        if any(value < 0 for value in self.volatility.values()):
            raise ValueError("volatility values cannot be negative")
        if any(value <= 0 for value in self.liquidity_notional.values()):
            raise ValueError("liquidity notionals must be positive")
        if any(
            value < 0
            for values in (
                self.spread_bps,
                self.expected_slippage_bps,
                self.expected_impact_bps,
            )
            for value in values.values()
        ):
            raise ValueError("market cost estimates cannot be negative")
        if self.clock_drift_seconds < 0:
            raise ValueError("clock drift cannot be negative")
        if (
            self.order_count < 0
            or self.funding_cost_bps < 0
            or self.borrow_cost_bps < 0
            or self.counterparty_exposure < 0
        ):
            raise ValueError("risk market counters/costs cannot be negative")
        if self.collateral_available is not None and not self.collateral_available.is_finite():
            raise ValueError("collateral availability must be finite")
        if self.market_state_hash and self.market_state_hash != self.canonical_hash():
            raise ValueError("market_state_hash does not match canonical market state")

    def canonical_hash(self) -> str:
        payload = {
            "marks": sorted((key, str(value)) for key, value in self.marks.items()),
            "stale_instruments": sorted(self.stale_instruments),
            "stale_seconds": sorted(self.stale_seconds.items()),
            "disagreed_instruments": sorted(self.disagreed_instruments),
            "clock_drift_seconds": self.clock_drift_seconds,
            "volatility": sorted((key, str(value)) for key, value in self.volatility.items()),
            "liquidity_notional": sorted(
                (key, str(value)) for key, value in self.liquidity_notional.items()
            ),
            "spread_bps": sorted((key, str(value)) for key, value in self.spread_bps.items()),
            "expected_slippage_bps": sorted(
                (key, str(value)) for key, value in self.expected_slippage_bps.items()
            ),
            "expected_impact_bps": sorted(
                (key, str(value)) for key, value in self.expected_impact_bps.items()
            ),
            "venue_healthy": sorted(self.venue_healthy.items()),
            "model_drift": self.model_drift,
            "unsupported_regime": self.unsupported_regime,
            "expired_forecasts": sorted(self.expired_forecasts),
            "reconciliation_clean": self.reconciliation_clean,
            "order_count": self.order_count,
            "funding_cost_bps": str(self.funding_cost_bps),
            "borrow_cost_bps": str(self.borrow_cost_bps),
            "counterparty_exposure": str(self.counterparty_exposure),
            "collateral_available": (
                str(self.collateral_available) if self.collateral_available is not None else None
            ),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @property
    def effective_hash(self) -> str:
        return self.market_state_hash or self.canonical_hash()


@dataclass(frozen=True, slots=True)
class RiskRequest:
    target: TargetPortfolio
    account: AccountState
    market: RiskMarketState
    policy: RiskPolicy


@dataclass(frozen=True, slots=True)
class OrderRiskCheck:
    approved: bool
    reasons: tuple[str, ...] = ()
    authoritative_state_hash: str | None = None
    risk_policy_id: UUID | None = None


class RiskKernel:
    """May only approve, reduce, or reject; it cannot loosen the policy."""

    SUPPORTED_HARD_LIMITS = frozenset(
        {
            "max_order_notional",
            "max_position_notional",
            "price_collar_bps",
            "max_gross_notional",
            "max_net_notional",
            "max_turnover_notional",
            "max_leverage",
            "max_margin_used",
            "max_current_gross_notional",
            "max_daily_loss",
            "max_rolling_loss",
            "max_drawdown",
            "max_concentration",
            "max_stale_seconds",
            "max_data_disagreement",
            "max_clock_drift_seconds",
            "max_cash_deficit",
            "max_liquidation_buffer_deficit",
            "max_volatility",
            "max_liquidity_participation",
            "max_spread_bps",
            "max_expected_slippage_bps",
            "max_expected_impact_bps",
            "max_order_rate",
            "max_funding_cost_bps",
            "max_borrow_cost_bps",
            "max_collateral_deficit",
            "max_counterparty_exposure",
            "max_model_drift",
            "max_unsupported_regime",
            "max_expired_forecast",
            "max_reconciliation_discrepancies",
            "max_venue_health",
        }
    )

    def __init__(self, kill_switch: KillSwitch | None = None) -> None:
        self.kill_switch = kill_switch or KillSwitch()

    def evaluate(self, request: RiskRequest, *, allow_reduction: bool = False) -> RiskDecision:
        reasons: list[str] = []
        reasons.extend(
            f"unsupported_hard_limit:{limit.name}"
            for limit in request.policy.hard_limits
            if limit.name not in self.SUPPORTED_HARD_LIMITS
        )
        if request.policy.effective_at > request.account.as_of:
            reasons.append("risk_policy_not_yet_effective")
        if request.target.risk_constraints_version != request.policy.policy_version:
            reasons.append("risk_policy_version_mismatch")
        if self.kill_switch.tripped:
            reasons.append(f"kill_switch:{self.kill_switch.reason}")
        target_instruments = self._target_instruments(request.target)
        if target_instruments.intersection(request.market.stale_instruments) or any(
            request.market.stale_seconds.get(instrument, 0) > 0 for instrument in target_instruments
        ):
            reasons.append("stale_market_data")
        if target_instruments.intersection(request.market.disagreed_instruments):
            reasons.append("disagreed_market_data")
        if request.market.model_drift:
            reasons.append("model_drift")
        if request.market.unsupported_regime:
            reasons.append("unsupported_regime")
        if target_instruments.intersection(request.market.expired_forecasts):
            reasons.append("expired_forecast")
        if not request.market.reconciliation_clean:
            reasons.append("reconciliation_not_clean")
        missing_marks = {
            position.instrument.canonical_id
            for position in request.target.positions
            if position.instrument.canonical_id not in request.market.marks
        }
        if missing_marks:
            reasons.append("missing_market_data:" + ",".join(sorted(missing_marks)))
        current = request.account
        proposed = self._proposed_notional(request.target, request.market)
        current_gross = current.gross_notional()
        proposed_gross = sum(abs(value) for value in proposed.values())
        proposed_net = sum(proposed.values())
        for limit in request.policy.hard_limits:
            value = self._limit_value(
                limit.name,
                request,
                proposed_gross=proposed_gross,
                proposed_net=proposed_net,
                current_gross=current_gross,
            )
            if value is None:
                reasons.append(f"unsupported_hard_limit:{limit.name}")
            elif value > limit.limit:
                reasons.append(f"{limit.name}:{value}>{limit.limit}{limit.unit}")
        account_hash = current.snapshot().state_hash
        # Always bind the decision to both authoritative account state and the
        # exact market snapshot. An omitted caller-supplied hash still gets a
        # deterministic canonical hash; it must never silently degrade to an
        # account-only decision.
        authoritative_hash = hashlib.sha256(
            f"{account_hash}:{request.market.effective_hash}".encode()
        ).hexdigest()
        if reasons and allow_reduction:
            reduced = self._reduced_target(request, reasons)
            if reduced is not None:
                reduced_check = self.evaluate(
                    RiskRequest(
                        target=reduced[0],
                        account=request.account,
                        market=request.market,
                        policy=request.policy,
                    )
                )
                if reduced_check.outcome is RiskOutcome.APPROVED:
                    return RiskDecision(
                        target_portfolio_id=request.target.artifact_id,
                        risk_policy_id=request.policy.artifact_id,
                        outcome=RiskOutcome.REDUCED,
                        authoritative_state_hash=authoritative_hash,
                        reasons=tuple(dict.fromkeys((*reasons, "target_reduced_to_policy_limits"))),
                        reduced_positions=reduced[1],
                    )
        outcome = RiskOutcome.REJECTED if reasons else RiskOutcome.APPROVED
        return RiskDecision(
            target_portfolio_id=request.target.artifact_id,
            risk_policy_id=request.policy.artifact_id,
            outcome=outcome,
            authoritative_state_hash=authoritative_hash,
            reasons=tuple(dict.fromkeys(reasons)),
        )

    def _reduced_target(
        self, request: RiskRequest, reasons: list[str]
    ) -> tuple[TargetPortfolio, tuple[object, ...]] | None:
        """Find a monotonic target reduction for explicitly reducible limits.

        Reduction is intentionally conservative: only exposure/cash limits can
        trigger it, and all non-reducible conditions (stale data, kill switch,
        disagreement, model drift, policy mismatch, or venue health) continue
        to reject. A binary search scales each target quantity toward the
        authoritative current position and then re-runs the complete kernel.
        """

        reducible = {
            "max_order_notional",
            "max_turnover_notional",
            "max_gross_notional",
            "max_net_notional",
            "max_position_notional",
            "max_leverage",
            "max_concentration",
            "max_cash_deficit",
        }
        if any(reason.split(":", 1)[0] not in reducible for reason in reasons):
            return None
        if not request.target.positions:
            return None
        marks = request.market.marks
        if any(
            position.instrument.canonical_id not in marks for position in request.target.positions
        ):
            return None
        original_cost = request.target.expected_cost
        trade_cash_flow = request.account.cash - request.target.cash_target - original_cost
        current_quantities = {
            position.instrument.canonical_id: request.account.positions.get(
                position.instrument.canonical_id, Decimal("0")
            )
            for position in request.target.positions
        }

        def candidate(factor: Decimal) -> tuple[TargetPortfolio, tuple[object, ...]]:
            reduced_positions = tuple(
                position.model_copy(
                    update={
                        "target_quantity": current_quantities[position.instrument.canonical_id]
                        + (
                            position.target_quantity
                            - current_quantities[position.instrument.canonical_id]
                        )
                        * factor
                    }
                )
                for position in request.target.positions
            )
            reduced_cost = original_cost * factor
            reduced_cash = request.account.cash - trade_cash_flow * factor - reduced_cost
            reduced_target = request.target.model_copy(
                update={
                    "positions": reduced_positions,
                    "cash_target": reduced_cash,
                    "expected_cost": reduced_cost,
                }
            )
            return reduced_target, reduced_positions

        zero_target, zero_positions = candidate(Decimal("0"))
        zero_check = self.evaluate(
            RiskRequest(
                target=zero_target,
                account=request.account,
                market=request.market,
                policy=request.policy,
            )
        )
        if zero_check.outcome is not RiskOutcome.APPROVED:
            return None
        low = Decimal("0")
        high = Decimal("1")
        for _ in range(48):
            midpoint = (low + high) / Decimal("2")
            reduced_target, _positions = candidate(midpoint)
            check = self.evaluate(
                RiskRequest(
                    target=reduced_target,
                    account=request.account,
                    market=request.market,
                    policy=request.policy,
                )
            )
            if check.outcome is RiskOutcome.APPROVED:
                low = midpoint
            else:
                high = midpoint
        if low <= 0:
            return None
        return candidate(low)

    def check_order(
        self,
        *,
        order: Order,
        account: AccountState,
        market: RiskMarketState,
        policy: RiskPolicy,
    ) -> OrderRiskCheck:
        reasons: list[str] = []
        reasons.extend(
            f"unsupported_hard_limit:{limit.name}"
            for limit in policy.hard_limits
            if limit.name not in self.SUPPORTED_HARD_LIMITS
        )
        if policy.effective_at > account.as_of:
            reasons.append("risk_policy_not_yet_effective")
        instrument = order.instrument.canonical_id
        if self.kill_switch.tripped:
            reasons.append(f"kill_switch:{self.kill_switch.reason}")
        if (
            instrument in market.stale_instruments
            or instrument not in market.marks
            or market.stale_seconds.get(instrument, 0) > 0
        ):
            reasons.append("stale_or_missing_market_data")
        if instrument in market.disagreed_instruments:
            reasons.append("disagreed_market_data")
        if market.clock_drift_seconds:
            reasons.append("clock_drift")
        if market.model_drift:
            reasons.append("model_drift")
        if market.unsupported_regime:
            reasons.append("unsupported_regime")
        if instrument in market.expired_forecasts:
            reasons.append("expired_forecast")
        if not market.reconciliation_clean:
            reasons.append("reconciliation_not_clean")
        if market.venue_healthy.get(instrument) is False:
            reasons.append("venue_unhealthy")
        if order.price is None:
            reasons.append("order_price_required_for_deterministic_paper_check")
        mark = market.marks.get(instrument)
        if order.price is not None and mark is not None:
            notional = order.price * order.quantity
            direction = Decimal("1") if order.side.lower() == "buy" else Decimal("-1")
            proposed_position = (
                account.positions.get(instrument, Decimal("0")) + direction * order.quantity
            )
            proposed_positions = dict(account.positions)
            proposed_positions[instrument] = proposed_position
            proposed_notionals = {
                name: quantity * market.marks.get(name, Decimal("0"))
                for name, quantity in proposed_positions.items()
            }
            current_gross = account.gross_notional()
            proposed_gross = sum(abs(value) for value in proposed_notionals.values())
            proposed_net = sum(proposed_notionals.values())
            proposed_notional = abs(proposed_position * mark)
            equity = account.equity()
            post_trade_cash = account.cash - direction * notional
            for limit in policy.hard_limits:
                if limit.name == "max_order_notional" and notional > limit.limit:
                    reasons.append(f"max_order_notional:{notional}>{limit.limit}{limit.unit}")
                elif limit.name == "max_position_notional" and proposed_notional > limit.limit:
                    reasons.append(
                        f"max_position_notional:{proposed_notional}>{limit.limit}{limit.unit}"
                    )
                elif limit.name == "price_collar_bps":
                    collar_bps = abs(order.price - mark) / mark * Decimal("10000")
                    if collar_bps > limit.limit:
                        reasons.append(f"price_collar_bps:{collar_bps}>{limit.limit}{limit.unit}")
                elif limit.name == "max_gross_notional" and proposed_gross > limit.limit:
                    reasons.append(f"max_gross_notional:{proposed_gross}>{limit.limit}{limit.unit}")
                elif limit.name == "max_net_notional" and abs(proposed_net) > limit.limit:
                    reasons.append(
                        f"max_net_notional:{abs(proposed_net)}>{limit.limit}{limit.unit}"
                    )
                elif limit.name == "max_turnover_notional" and notional > limit.limit:
                    reasons.append(f"max_turnover_notional:{notional}>{limit.limit}{limit.unit}")
                elif limit.name == "max_leverage":
                    leverage = proposed_gross / equity if equity > 0 else Decimal("999999")
                    if leverage > limit.limit:
                        reasons.append(f"max_leverage:{leverage}>{limit.limit}{limit.unit}")
                elif limit.name == "max_margin_used" and account.margin_used > limit.limit:
                    reasons.append(
                        f"max_margin_used:{account.margin_used}>{limit.limit}{limit.unit}"
                    )
                elif limit.name == "max_current_gross_notional" and current_gross > limit.limit:
                    reasons.append(
                        f"max_current_gross_notional:{current_gross}>{limit.limit}{limit.unit}"
                    )
                elif (
                    limit.name == "max_daily_loss"
                    and max(Decimal("0"), -account.daily_realized_pnl) > limit.limit
                ):
                    loss = max(Decimal("0"), -account.daily_realized_pnl)
                    reasons.append(f"max_daily_loss:{loss}>{limit.limit}{limit.unit}")
                elif (
                    limit.name == "max_rolling_loss"
                    and max(Decimal("0"), -account.rolling_realized_pnl) > limit.limit
                ):
                    loss = max(Decimal("0"), -account.rolling_realized_pnl)
                    reasons.append(f"max_rolling_loss:{loss}>{limit.limit}{limit.unit}")
                elif limit.name == "max_drawdown" and account.drawdown() > limit.limit:
                    reasons.append(f"max_drawdown:{account.drawdown()}>{limit.limit}{limit.unit}")
                elif limit.name == "max_concentration" and proposed_gross:
                    concentration = proposed_notional / proposed_gross
                    if concentration > limit.limit:
                        reasons.append(
                            f"max_concentration:{concentration}>{limit.limit}{limit.unit}"
                        )
                elif limit.name == "max_stale_seconds" and (
                    instrument not in market.stale_seconds
                    or market.stale_seconds[instrument] > limit.limit
                ):
                    if instrument not in market.stale_seconds:
                        reasons.append("max_stale_seconds:missing_market_freshness")
                    else:
                        stale = market.stale_seconds[instrument]
                        reasons.append(f"max_stale_seconds:{stale}>{limit.limit}{limit.unit}")
                elif (
                    limit.name == "max_data_disagreement"
                    and instrument in market.disagreed_instruments
                ):
                    reasons.append(f"max_data_disagreement:1>{limit.limit}{limit.unit}")
                elif (
                    limit.name == "max_clock_drift_seconds"
                    and market.clock_drift_seconds > limit.limit
                ):
                    reasons.append(
                        f"max_clock_drift_seconds:{market.clock_drift_seconds}>{limit.limit}{limit.unit}"
                    )
                elif (
                    limit.name == "max_cash_deficit"
                    and max(Decimal("0"), -post_trade_cash) > limit.limit
                ):
                    deficit = max(Decimal("0"), -post_trade_cash)
                    reasons.append(f"max_cash_deficit:{deficit}>{limit.limit}{limit.unit}")
                elif limit.name == "max_liquidation_buffer_deficit":
                    available = account.liquidation_buffer
                    if available is None:
                        available = account.margin_available
                    if available is None:
                        reasons.append("max_liquidation_buffer_deficit:missing_liquidation_buffer")
                    else:
                        deficit = max(Decimal("0"), -available)
                        if deficit > limit.limit:
                            reasons.append(
                                f"max_liquidation_buffer_deficit:{deficit}>{limit.limit}{limit.unit}"
                            )
                elif limit.name == "max_volatility":
                    volatility = market.volatility.get(instrument)
                    if volatility is None or volatility > limit.limit:
                        reasons.append(
                            f"max_volatility:{volatility if volatility is not None else 'missing'}>{limit.limit}{limit.unit}"
                        )
                elif limit.name == "max_liquidity_participation":
                    liquidity = market.liquidity_notional.get(instrument)
                    participation = notional / liquidity if liquidity else Decimal("999999")
                    if liquidity is None:
                        reasons.append("max_liquidity_participation:missing_liquidity")
                    elif participation > limit.limit:
                        reasons.append(
                            f"max_liquidity_participation:{participation}>{limit.limit}{limit.unit}"
                        )
                elif limit.name == "max_spread_bps":
                    spread = market.spread_bps.get(instrument, Decimal("999999"))
                    if spread > limit.limit:
                        reasons.append(f"max_spread_bps:{spread}>{limit.limit}{limit.unit}")
                elif limit.name == "max_expected_slippage_bps":
                    slippage = market.expected_slippage_bps.get(instrument, Decimal("999999"))
                    if slippage > limit.limit:
                        reasons.append(
                            f"max_expected_slippage_bps:{slippage}>{limit.limit}{limit.unit}"
                        )
                elif limit.name == "max_expected_impact_bps":
                    impact = market.expected_impact_bps.get(instrument, Decimal("999999"))
                    if impact > limit.limit:
                        reasons.append(
                            f"max_expected_impact_bps:{impact}>{limit.limit}{limit.unit}"
                        )
                elif limit.name == "max_order_rate" and market.order_count > limit.limit:
                    reasons.append(f"max_order_rate:{market.order_count}>{limit.limit}{limit.unit}")
                elif limit.name == "max_funding_cost_bps" and market.funding_cost_bps > limit.limit:
                    reasons.append(
                        f"max_funding_cost_bps:{market.funding_cost_bps}>{limit.limit}{limit.unit}"
                    )
                elif limit.name == "max_borrow_cost_bps" and market.borrow_cost_bps > limit.limit:
                    reasons.append(
                        f"max_borrow_cost_bps:{market.borrow_cost_bps}>{limit.limit}{limit.unit}"
                    )
                elif limit.name == "max_collateral_deficit":
                    collateral = (
                        market.collateral_available
                        if market.collateral_available is not None
                        else account.margin_available
                    )
                    if collateral is None:
                        reasons.append("max_collateral_deficit:missing_collateral")
                    else:
                        deficit = max(Decimal("0"), -collateral)
                        if deficit > limit.limit:
                            reasons.append(
                                f"max_collateral_deficit:{deficit}>{limit.limit}{limit.unit}"
                            )
                elif (
                    limit.name == "max_counterparty_exposure"
                    and market.counterparty_exposure > limit.limit
                ):
                    reasons.append(
                        f"max_counterparty_exposure:{market.counterparty_exposure}>{limit.limit}{limit.unit}"
                    )
                elif limit.name == "max_model_drift" and market.model_drift:
                    reasons.append(f"max_model_drift:1>{limit.limit}{limit.unit}")
                elif limit.name == "max_unsupported_regime" and market.unsupported_regime:
                    reasons.append(f"max_unsupported_regime:1>{limit.limit}{limit.unit}")
                elif (
                    limit.name == "max_expired_forecast" and instrument in market.expired_forecasts
                ):
                    reasons.append(f"max_expired_forecast:1>{limit.limit}{limit.unit}")
                elif (
                    limit.name == "max_reconciliation_discrepancies"
                    and not market.reconciliation_clean
                ):
                    reasons.append(f"max_reconciliation_discrepancies:1>{limit.limit}{limit.unit}")
                elif limit.name == "max_venue_health" and (
                    instrument not in market.venue_healthy or not market.venue_healthy[instrument]
                ):
                    if instrument not in market.venue_healthy:
                        reasons.append("max_venue_health:missing_venue_health")
                    else:
                        reasons.append(f"max_venue_health:1>{limit.limit}{limit.unit}")
        account_hash = account.snapshot().state_hash
        authoritative_hash = hashlib.sha256(
            f"{account_hash}:{market.effective_hash}".encode()
        ).hexdigest()
        return OrderRiskCheck(
            approved=not reasons,
            reasons=tuple(reasons),
            authoritative_state_hash=authoritative_hash,
            risk_policy_id=policy.artifact_id,
        )

    @staticmethod
    def _proposed_notional(target: TargetPortfolio, market: RiskMarketState) -> dict[str, Decimal]:
        return {
            position.instrument.canonical_id: position.target_quantity
            * market.marks[position.instrument.canonical_id]
            for position in target.positions
            if position.instrument.canonical_id in market.marks
        }

    @staticmethod
    def _limit_value(
        name: str,
        request: RiskRequest,
        *,
        proposed_gross: Decimal,
        proposed_net: Decimal,
        current_gross: Decimal,
    ) -> Decimal | None:
        target_instruments = RiskKernel._target_instruments(request.target)
        if name == "max_gross_notional":
            return proposed_gross
        if name == "max_net_notional":
            return abs(proposed_net)
        if name == "max_turnover_notional":
            current = {
                instrument: quantity * request.market.marks.get(instrument, Decimal("0"))
                for instrument, quantity in request.account.positions.items()
            }
            target = RiskKernel._proposed_notional(request.target, request.market)
            instruments = set(current) | set(target)
            return sum(
                abs(target.get(instrument, Decimal("0")) - current.get(instrument, Decimal("0")))
                for instrument in instruments
            )
        if name == "max_order_notional":
            current = {
                instrument: quantity * request.market.marks.get(instrument, Decimal("0"))
                for instrument, quantity in request.account.positions.items()
            }
            target = RiskKernel._proposed_notional(request.target, request.market)
            instruments = set(current) | set(target)
            return max(
                (
                    abs(
                        target.get(instrument, Decimal("0")) - current.get(instrument, Decimal("0"))
                    )
                    for instrument in instruments
                ),
                default=Decimal("0"),
            )
        if name == "max_leverage":
            equity = request.account.equity()
            if equity <= 0:
                return Decimal("999999") if proposed_gross else Decimal("0")
            return proposed_gross / equity
        if name == "max_position_notional":
            return max(
                (
                    abs(value)
                    for value in RiskKernel._proposed_notional(
                        request.target, request.market
                    ).values()
                ),
                default=Decimal("0"),
            )
        if name == "max_margin_used":
            return request.account.margin_used
        if name == "max_current_gross_notional":
            return current_gross
        if name == "max_daily_loss":
            return max(Decimal("0"), -request.account.daily_realized_pnl)
        if name == "max_rolling_loss":
            return max(Decimal("0"), -request.account.rolling_realized_pnl)
        if name == "max_drawdown":
            return request.account.drawdown()
        if name == "max_concentration":
            if proposed_gross == 0:
                return Decimal("0")
            return max(
                (
                    abs(value) / proposed_gross
                    for value in RiskKernel._proposed_notional(
                        request.target, request.market
                    ).values()
                ),
                default=Decimal("0"),
            )
        if name == "max_stale_seconds":
            if any(
                instrument not in request.market.stale_seconds for instrument in target_instruments
            ):
                return Decimal("999999")
            return Decimal(
                max(
                    (request.market.stale_seconds[instrument] for instrument in target_instruments),
                    default=0,
                )
            )
        if name == "max_data_disagreement":
            return Decimal(
                bool(target_instruments.intersection(request.market.disagreed_instruments))
            )
        if name == "max_clock_drift_seconds":
            return Decimal(request.market.clock_drift_seconds)
        if name == "max_cash_deficit":
            return max(Decimal("0"), -request.target.cash_target)
        if name == "max_liquidation_buffer_deficit":
            available = request.account.margin_available
            return max(Decimal("0"), -available) if available is not None else Decimal("999999")
        if name == "max_volatility":
            return max(
                (
                    request.market.volatility.get(instrument, Decimal("999999"))
                    for instrument in target_instruments
                ),
                default=Decimal("999999"),
            )
        if name == "max_liquidity_participation":
            notionals = RiskKernel._proposed_notional(request.target, request.market)
            if any(instrument not in request.market.liquidity_notional for instrument in notionals):
                return Decimal("999999")
            return max(
                (
                    abs(value) / request.market.liquidity_notional[instrument]
                    for instrument, value in notionals.items()
                    if instrument in request.market.liquidity_notional
                ),
                default=Decimal("1"),
            )
        if name == "max_spread_bps":
            return max(
                (
                    request.market.spread_bps.get(instrument, Decimal("999999"))
                    for instrument in target_instruments
                ),
                default=Decimal("999999"),
            )
        if name == "max_expected_slippage_bps":
            return max(
                (
                    request.market.expected_slippage_bps.get(instrument, Decimal("999999"))
                    for instrument in target_instruments
                ),
                default=Decimal("999999"),
            )
        if name == "max_expected_impact_bps":
            return max(
                (
                    request.market.expected_impact_bps.get(instrument, Decimal("999999"))
                    for instrument in target_instruments
                ),
                default=Decimal("999999"),
            )
        if name == "max_order_rate":
            return Decimal(request.market.order_count)
        if name == "max_funding_cost_bps":
            return request.market.funding_cost_bps
        if name == "max_borrow_cost_bps":
            return request.market.borrow_cost_bps
        if name == "max_collateral_deficit":
            available = request.market.collateral_available
            if available is None:
                available = request.account.margin_available
            return max(Decimal("0"), -available) if available is not None else Decimal("999999")
        if name == "max_counterparty_exposure":
            return request.market.counterparty_exposure
        if name in {"max_model_drift", "max_unsupported_regime"}:
            return Decimal(
                int(
                    request.market.model_drift
                    if name == "max_model_drift"
                    else request.market.unsupported_regime
                )
            )
        if name == "max_expired_forecast":
            return Decimal(bool(target_instruments.intersection(request.market.expired_forecasts)))
        if name == "max_reconciliation_discrepancies":
            return Decimal(not request.market.reconciliation_clean)
        if name == "max_venue_health":
            if any(
                instrument not in request.market.venue_healthy for instrument in target_instruments
            ):
                return Decimal("999999")
            return Decimal(
                any(
                    not request.market.venue_healthy[instrument]
                    for instrument in target_instruments
                )
            )
        # Unknown hard limits fail closed in the decision rather than being
        # silently ignored or causing an unhandled exception in the hot path.
        return None

    @staticmethod
    def _target_instruments(target: TargetPortfolio) -> set[str]:
        return {position.instrument.canonical_id for position in target.positions}
