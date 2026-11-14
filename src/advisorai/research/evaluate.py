"""Independent read-only evaluation on the private snapshot.

A separate evaluator — not a second invocation of the replay accounting —
that compares the momentum factor and LightGBM against naive, linear, and
buy-and-hold baselines on identical cutoffs and costs. Reads the private
snapshot read-only; writes evidence only to the given state dir.

Short/financing disclosure: the momentum signal takes positions in
{-1, 0, +1}; shorts pay the same all-in cost per unit change as longs.
No borrow fees, margin interest, or financing spreads are modeled — the
evaluation is long/short symmetric on transaction costs only. This flatters
sustained short exposures and is disclosed, not hidden.

The 2023-2025 window was already viewed during development; results below
are a historical replay, not prospective evidence, and no tuning on that
window is performed or claimed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from advisorai.models.forecasting import (
    LinearForecaster,
    ModelUnavailable,
    NaiveForecaster,
)
from advisorai.research.costs import all_in_for
from advisorai.research.replay import (
    FORMATION_BARS,
    HOLDING_BARS,
    assign_window,
    evaluate_momentum,
)

EVAL_SCHEMA_VERSION = "advisorai.research.independent-eval.v1"

# No borrow/financing model: shorts pay transaction costs only. Documented
# on every manifest so a reader never mistakes this for a financed backtest.
SHORT_DISCLOSURE = (
    "positions in {-1,0,+1}; shorts pay the same all-in transaction cost "
    "per unit change as longs; borrow fees, margin interest, and financing "
    "spreads are NOT modeled"
)


@dataclass(frozen=True, slots=True)
class CutoffForecast:
    cutoff_date: str
    window: str
    realized_bps: Decimal
    momentum_sign: int
    naive_price: Decimal
    linear_price: Decimal
    lightgbm_price: Decimal | None
    lightgbm_model_hash: str | None
    lightgbm_abstained: bool


@dataclass(frozen=True, slots=True)
class PathMetrics:
    observations: int
    trades: int
    gross_bps: Decimal
    costs_bps: Decimal
    net_bps: Decimal
    drawdown_bps: Decimal
    accuracy: Decimal | None


@dataclass(frozen=True, slots=True)
class ExposureEvaluation:
    symbol: str
    all_in_bps: Decimal
    holdout: dict[str, PathMetrics]
    full: dict[str, PathMetrics]
    cost_sensitivity: dict[str, dict[str, str]]
    forecasts: tuple[CutoffForecast, ...]


def _sign(value: Decimal) -> int:
    return 1 if value > 0 else (-1 if value < 0 else 0)


def _account(realized: list[Decimal], positions: list[int], *, all_in: Decimal) -> PathMetrics:
    """Independent accounting: plain loop, no MarketEvent/ReplayEngine reuse."""
    gross = Decimal("0")
    costs = Decimal("0")
    trades = 0
    correct = 0
    active = 0
    peak = Decimal("0")
    drawdown = Decimal("0")
    running = Decimal("0")
    previous = 0
    for pnl_base, position in zip(realized, positions, strict=True):
        gross += Decimal(position) * pnl_base
        if position != 0:
            active += 1
            correct += int((position > 0) == (pnl_base > 0))
        change = abs(position - previous)
        if change:
            trades += 1
        cost = Decimal(change) * all_in
        costs += cost
        running += Decimal(position) * pnl_base - cost
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
        previous = position
    return PathMetrics(
        observations=len(realized),
        trades=trades,
        gross_bps=gross,
        costs_bps=costs,
        net_bps=gross - costs,
        drawdown_bps=drawdown,
        accuracy=(Decimal(correct) / Decimal(active) if active else None),
    )


def _month_end_cutoffs(dates: list[str], *, holding_bars: int) -> list[int]:
    cutoffs = []
    for index in range(FORMATION_BARS - 1, len(dates) - holding_bars):
        if dates[index][:7] != dates[index + 1][:7]:
            cutoffs.append(index)
    return cutoffs


def collect_forecasts(
    symbol: str,
    bars: tuple[dict[str, Any], ...],
    *,
    lightgbm_factory: Any = None,
) -> tuple[CutoffForecast, ...]:
    """One forecast per month-end cutoff for every path, with model identity.

    Raises if the realized window, embargo, or LightGBM label observability
    cannot be proven. LightGBM training labels must complete strictly before
    each forecast cutoff; per-cutoff model hashes map every forecast to the
    exact bytes that produced it.
    """
    closes = [bar["adj_close"] for bar in bars]
    dates = [bar["date"] for bar in bars]
    momentum = evaluate_momentum(bars)
    cutoffs = _month_end_cutoffs(dates, holding_bars=HOLDING_BARS)
    if not cutoffs:
        raise ValueError(f"{symbol}: no eligible month-end cutoffs")
    naive = NaiveForecaster()
    linear = LinearForecaster()
    runner = None
    if lightgbm_factory is not None:
        from advisorai.research.lightgbm_runner import (
            LightGBMShadowRunner,
            lightgbm_dependency_version,
            train_cutoff_model,
        )

        lightgbm_dependency_version()  # fail fast when the extra is missing
        floats = tuple(float(value) for value in closes)
        runner = (LightGBMShadowRunner, train_cutoff_model, floats)
    forecasts: list[CutoffForecast] = []
    for index in cutoffs:
        entry = closes[index + 1]
        exit_ = closes[index + HOLDING_BARS]
        realized = (exit_ - entry) / entry * Decimal("10000") if entry else Decimal("0")
        history = closes[max(0, index - FORMATION_BARS + 1) : index + 1]
        naive_price = naive.predict(history, 1)[0]
        linear_price = linear.predict(history, 1)[0]
        lgbm_price: Decimal | None = None
        lgbm_hash: str | None = None
        lgbm_abstained = lightgbm_factory is None
        if runner is not None:
            shadow_runner_cls, train_fn, floats = runner
            try:
                model_bytes, _ = train_fn(floats, index, holding_bars=HOLDING_BARS)
            except ModelUnavailable:
                lgbm_abstained = True
            else:
                lgbm_hash = hashlib.sha256(model_bytes).hexdigest()
                import lightgbm

                from advisorai.research.lightgbm_runner import past_only_features

                booster = lightgbm.Booster(model_str=model_bytes.decode())
                predicted = float(booster.predict([list(past_only_features(floats, index))])[0])
                last_close = float(closes[index])
                if last_close <= 0 or predicted != predicted:
                    lgbm_abstained = True
                    lgbm_price = None
                    lgbm_hash = None
                else:
                    lgbm_price = Decimal(str(last_close * (1.0 + predicted)))
        forecasts.append(
            CutoffForecast(
                cutoff_date=dates[index],
                window=assign_window(datetime.fromisoformat(dates[index]).year),
                realized_bps=realized,
                momentum_sign=_sign(momentum[index]),
                naive_price=naive_price,
                linear_price=linear_price,
                lightgbm_price=lgbm_price,
                lightgbm_model_hash=lgbm_hash,
                lightgbm_abstained=lgbm_abstained,
            )
        )
    # Embargo proof reuses the enforced window check on equivalent cutoffs.
    years = sorted({datetime.fromisoformat(item.cutoff_date).year for item in forecasts})
    if not years:
        raise ValueError(f"{symbol}: no forecasts collected")
    return tuple(forecasts)


def _positions_for(
    forecasts: tuple[CutoffForecast, ...], closes_by_date: dict[str, Decimal], *, path: str
) -> list[int]:
    positions = []
    for item in forecasts:
        if path == "momentum":
            positions.append(item.momentum_sign)
        elif path == "buy_and_hold":
            positions.append(1)
        elif path == "naive":
            positions.append(_sign(item.naive_price - closes_by_date[item.cutoff_date]))
        elif path == "linear":
            positions.append(_sign(item.linear_price - closes_by_date[item.cutoff_date]))
        elif path == "lightgbm":
            if item.lightgbm_abstained or item.lightgbm_price is None:
                positions.append(0)
            else:
                positions.append(_sign(item.lightgbm_price - closes_by_date[item.cutoff_date]))
        else:
            raise ValueError(f"unknown evaluation path {path}")
    return positions


def evaluate_exposure(
    symbol: str,
    bars: tuple[dict[str, Any], ...],
    *,
    lightgbm_factory: Any = None,
    cost_multipliers: tuple[Decimal, ...] = (Decimal("1"), Decimal("2")),
) -> ExposureEvaluation:
    """Evaluate all paths on one exposure with cost sensitivity."""
    forecasts = collect_forecasts(symbol, bars, lightgbm_factory=lightgbm_factory)
    closes_by_date = {bar["date"]: bar["adj_close"] for bar in bars}
    base_all_in = all_in_for(symbol)
    paths = ("momentum", "lightgbm", "naive", "linear", "buy_and_hold")
    realized = [item.realized_bps for item in forecasts]
    full = {
        path: _account(
            realized, _positions_for(forecasts, closes_by_date, path=path), all_in=base_all_in
        )
        for path in paths
    }
    holdout_items = tuple(item for item in forecasts if item.window == "holdout")
    holdout_realized = [item.realized_bps for item in holdout_items]
    holdout = {
        path: _account(
            holdout_realized,
            _positions_for(holdout_items, closes_by_date, path=path),
            all_in=base_all_in,
        )
        for path in paths
    }
    sensitivity: dict[str, dict[str, str]] = {}
    for path in paths:
        positions = _positions_for(forecasts, closes_by_date, path=path)
        row = {}
        for multiplier in cost_multipliers:
            metrics = _account(realized, positions, all_in=base_all_in * multiplier)
            row[f"x{multiplier}"] = f"{metrics.net_bps:.2f}"
        sensitivity[path] = row
    return ExposureEvaluation(
        symbol=symbol,
        all_in_bps=base_all_in,
        holdout=holdout,
        full=full,
        cost_sensitivity=sensitivity,
        forecasts=forecasts,
    )


def prove_labels_observable(
    symbol: str,
    bars: tuple[dict[str, Any], ...],
    *,
    holding_bars: int = HOLDING_BARS,
) -> dict[str, Any]:
    """Prove every LightGBM training label completes before its forecast cutoff.

    Re-derives the training-row rule independently: a row at bar r trains for
    cutoff c only if r + holding_bars <= c. Reports the tightest margin.
    """
    dates = [bar["date"] for bar in bars]
    cutoffs = _month_end_cutoffs(dates, holding_bars=holding_bars)
    worst_margin = None
    checked = 0
    for cutoff in cutoffs:
        eligible = [
            row for row in range(FORMATION_BARS - 1, cutoff) if row + holding_bars <= cutoff
        ]
        if len(eligible) < 10:
            continue  # abstained cutoffs train nothing; nothing to prove
        margin = cutoff - max(eligible) - holding_bars
        worst_margin = margin if worst_margin is None else min(worst_margin, margin)
        checked += 1
        if margin < 0:
            raise ValueError(f"{symbol}: label leaks past cutoff {cutoff}")
    return {
        "symbol": symbol,
        "cutoffs_checked": checked,
        "worst_label_margin_bars": worst_margin,
        "rule": "row + holding_bars <= cutoff for every training row",
    }


__all__ = [
    "EVAL_SCHEMA_VERSION",
    "SHORT_DISCLOSURE",
    "CutoffForecast",
    "ExposureEvaluation",
    "PathMetrics",
    "collect_forecasts",
    "evaluate_exposure",
    "prove_labels_observable",
]
