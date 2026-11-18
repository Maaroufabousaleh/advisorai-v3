"""Three independent historical replays for the BTCUSDT long/flat candidate.

Plumbing only: these agree-or-fail on the pinned historical snapshot. No
verdict language lives here; pass/fail applies to the forward shadow only.

Amendment-4 economics (see docs/plans/btcusdt-paper-v1-candidate.md):
- Portfolio value for strategy AND buy-and-hold from identical starting
  capital (unit notional 1). Reported nets are (V_end - 1) in bps.
- The position for day j is decided at the close of day j-1; fills print
  at the open of day j. Entry day multiplies close/open of the fill day;
  held days multiply close/close-prev; exit day multiplies open/close-prev
  (the exit gap belongs to the closing position).
- Consistent cost deductions: every unit position change multiplies value
  by (1 - 20/10000) on the fill day. BH pays it once at entry. Cash is 0.
- Formation 63 closes, long/flat only (never short).
"""

from __future__ import annotations

from decimal import Decimal

FORMATION_CLOSES = 63
STARTING_CAPITAL = Decimal("1")

BTCUSDT_FEE_BPS = Decimal("10")
BTCUSDT_SPREAD_BPS = Decimal("1")
BTCUSDT_SLIPPAGE_BPS = Decimal("9")
BTCUSDT_ALL_IN_BPS = BTCUSDT_FEE_BPS + BTCUSDT_SPREAD_BPS + BTCUSDT_SLIPPAGE_BPS
COST_FACTOR = Decimal("1") - BTCUSDT_ALL_IN_BPS / Decimal("10000")


def _closes(bars: tuple[dict, ...]) -> list[Decimal]:
    return [Decimal(str(row["adj_close"])) for row in bars]


def _opens(bars: tuple[dict, ...]) -> list[Decimal]:
    return [Decimal(str(row["open"])) for row in bars]


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if not denominator:
        return Decimal("1")
    return numerator / denominator


def _to_bps(value: Decimal) -> Decimal:
    return (value - STARTING_CAPITAL) * Decimal("10000")


def _buy_and_hold_value(closes: list[Decimal], opens: list[Decimal]) -> Decimal:
    """True buy-and-hold portfolio value: enter the first scored open, hold
    to the last close, pay the entry cost once. Path-independent."""
    first = FORMATION_CLOSES + 1
    return _ratio(closes[-1], opens[first]) * COST_FACTOR


def replay_sign_only(bars: tuple[dict, ...]) -> dict[str, str | int]:
    """Day-based loop mirroring the research replay structure."""
    closes = _closes(bars)
    opens = _opens(bars)
    value = STARTING_CAPITAL
    previous = 0
    trades = 0
    observations = 0
    for day in range(FORMATION_CLOSES + 1, len(closes)):
        signal = 1 if closes[day - 1] > closes[day - 1 - FORMATION_CLOSES] else 0
        if signal != previous:
            trades += 1
            value *= COST_FACTOR
            if signal == 1:
                value *= _ratio(closes[day], opens[day])
            else:
                value *= _ratio(opens[day], closes[day - 1])
            previous = signal
        elif signal == 1:
            value *= _ratio(closes[day], closes[day - 1])
        observations += 1
    net = _to_bps(value) if observations else Decimal("0")
    baseline = _to_bps(_buy_and_hold_value(closes, opens)) if observations else Decimal("0")
    return {
        "net_bps": str(net),
        "baseline_bps": str(baseline),
        "trades": trades,
        "obs": observations,
    }


def mirror_accounting(bars: tuple[dict, ...]) -> dict[str, str | int]:
    """Independent mirror: explicit holding state, factored day multipliers."""
    closes = _closes(bars)
    opens = _opens(bars)
    value = STARTING_CAPITAL
    holding = 0
    trades = 0
    scored = 0
    day = FORMATION_CLOSES + 1
    while day < len(closes):
        wanted = 1 if closes[day - 1] > closes[day - 1 - FORMATION_CLOSES] else 0
        factor = Decimal("1")
        if wanted != holding:
            trades += 1
            factor *= COST_FACTOR
            if wanted == 1:
                factor *= _ratio(closes[day], opens[day])
            else:
                factor *= _ratio(opens[day], closes[day - 1])
            holding = wanted
        elif holding == 1:
            factor *= _ratio(closes[day], closes[day - 1])
        value *= factor
        scored += 1
        day += 1
    net = _to_bps(value) if scored else Decimal("0")
    baseline = _to_bps(_buy_and_hold_value(closes, opens)) if scored else Decimal("0")
    return {"net_bps": str(net), "baseline_bps": str(baseline), "trades": trades, "obs": scored}


def plain_loop(bars: tuple[dict, ...]) -> dict[str, str | int]:
    """Minimal third loop: straight-line day arithmetic, no reused state."""
    closes = [Decimal(str(row["adj_close"])) for row in bars]
    opens = [Decimal(str(row["open"])) for row in bars]
    total = STARTING_CAPITAL
    prior = 0
    count = 0
    n_trades = 0
    for day, _ in enumerate(closes):
        if day < FORMATION_CLOSES + 1 or day >= len(closes):
            continue
        pos = 1 if closes[day - 1] > closes[day - 1 - FORMATION_CLOSES] else 0
        move = Decimal("1")
        if pos != prior:
            n_trades += 1
            move = COST_FACTOR
            if pos == 1:
                move *= _ratio(closes[day], opens[day])
            else:
                move *= _ratio(opens[day], closes[day - 1])
            prior = pos
        elif pos == 1:
            move = _ratio(closes[day], closes[day - 1])
        total *= move
        count += 1
    net = _to_bps(total) if count else Decimal("0")
    base = _to_bps(_buy_and_hold_value(closes, opens)) if count else Decimal("0")
    return {"net_bps": str(net), "baseline_bps": str(base), "trades": n_trades, "obs": count}


def _reported(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def agree(bars: tuple[dict, ...]) -> dict[str, str | int]:
    """Run all three; return the shared result or raise on disagreement.

    Nets agree at reporting precision (0.01 bps): 28-digit intermediate
    arithmetic can differ by a unit in the last place across evaluation
    orders. Trades and observations must match exactly.
    """
    results = (replay_sign_only(bars), mirror_accounting(bars), plain_loop(bars))
    first = results[0]
    for other in results[1:]:
        if (
            other["trades"] != first["trades"]
            or other["obs"] != first["obs"]
            or _reported(Decimal(str(other["net_bps"])))
            != _reported(Decimal(str(first["net_bps"])))
            or _reported(Decimal(str(other["baseline_bps"])))
            != _reported(Decimal(str(first["baseline_bps"])))
        ):
            raise ValueError(f"three-way replay disagreement: {first} vs {other}")
    return first


__all__ = [
    "BTCUSDT_ALL_IN_BPS",
    "BTCUSDT_FEE_BPS",
    "BTCUSDT_SLIPPAGE_BPS",
    "BTCUSDT_SPREAD_BPS",
    "COST_FACTOR",
    "FORMATION_CLOSES",
    "STARTING_CAPITAL",
    "agree",
    "mirror_accounting",
    "plain_loop",
    "replay_sign_only",
]
