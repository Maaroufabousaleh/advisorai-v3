"""Single recorded cost configuration for the research replay family.

Replay, shadow comparison, and Hermes verification must evaluate with
identical costs or their results cannot be compared. This module is the one
place those costs live: per-symbol (fee, spread, slippage) in basis points,
plus the derived all-in totals the Hermes mirror consumes. CLI defaults and
the sandbox contract both import from here; any divergence is a bug, and the
Hermes net comparison detects it instead of hiding it.
"""

from __future__ import annotations

from decimal import Decimal

# Conservative liquid-ETF costs, basis points per unit position change.
# SPY: fee 1 + spread 1 + slippage 2. TLT: fee 1 + spread 2 + slippage 2.
COSTS_BY_SYMBOL: dict[str, tuple[Decimal, Decimal, Decimal]] = {
    "SPY": (Decimal("1"), Decimal("1"), Decimal("2")),
    "TLT": (Decimal("1"), Decimal("2"), Decimal("2")),
}

ALL_IN_BPS_BY_SYMBOL: dict[str, Decimal] = {
    symbol: fee + spread + slippage for symbol, (fee, spread, slippage) in COSTS_BY_SYMBOL.items()
}


def costs_for(symbol: str) -> tuple[Decimal, Decimal, Decimal]:
    """Return the pinned (fee, spread, slippage) triple for an exposure."""
    try:
        return COSTS_BY_SYMBOL[symbol]
    except KeyError as exc:
        raise ValueError(f"{symbol}: no pinned cost configuration") from exc


def all_in_for(symbol: str) -> Decimal:
    """Return the pinned all-in cost per unit position change."""
    try:
        return ALL_IN_BPS_BY_SYMBOL[symbol]
    except KeyError as exc:
        raise ValueError(f"{symbol}: no pinned all-in cost in the verification contract") from exc


__all__ = ["ALL_IN_BPS_BY_SYMBOL", "COSTS_BY_SYMBOL", "all_in_for", "costs_for"]
