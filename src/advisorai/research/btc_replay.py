"""Three independent historical replays for the BTCUSDT long/flat candidate.

Plumbing only: these agree-or-fail on the pinned historical snapshot. No
verdict language lives here; pass/fail applies to the forward shadow only.

Amendment-3 economics (see docs/plans/btcusdt-paper-v1-candidate.md):
- Continuous holdings. The position for day j is decided at the close of
  day j-1 from closes then available; fills print at the open of day j,
  observable only after the decision (the simulated fill is a separate
  artifact from the decision record).
- Entry day: open-to-close of the fill day. Held days: close-to-close.
  Exit day: prior close through the exit fill (the exit gap belongs to
  the closing position). Entry gaps are never attributed (flat before
  the entry fill).
- Costs: fee 10 + spread 1 + slippage 9 = 20 bps all-in per unit change,
  applied on every fill day. Buy-and-hold is portfolio value: enter the
  first scored open, hold to the last close, one entry leg. Cash is 0.
- Formation 63 closes, long/flat only (never short).
"""

from __future__ import annotations

from decimal import Decimal

FORMATION_CLOSES = 63

BTCUSDT_FEE_BPS = Decimal("10")
BTCUSDT_SPREAD_BPS = Decimal("1")
BTCUSDT_SLIPPAGE_BPS = Decimal("9")
BTCUSDT_ALL_IN_BPS = BTCUSDT_FEE_BPS + BTCUSDT_SPREAD_BPS + BTCUSDT_SLIPPAGE_BPS


def _closes(bars: tuple[dict, ...]) -> list[Decimal]:
    return [Decimal(str(row["adj_close"])) for row in bars]


def _opens(bars: tuple[dict, ...]) -> list[Decimal]:
    return [Decimal(str(row["open"])) for row in bars]


def _bps(numerator: Decimal, denominator: Decimal) -> Decimal:
    if not denominator:
        return Decimal("0")
    return (numerator / denominator) * Decimal("10000")


def _buy_and_hold(closes: list[Decimal], opens: list[Decimal]) -> Decimal:
    """True buy-and-hold on portfolio value: enter the first scored open,
    hold to the last close, pay one entry leg. Path-independent."""
    first = FORMATION_CLOSES + 1
    return _bps(closes[-1] - opens[first], opens[first]) - BTCUSDT_ALL_IN_BPS


def replay_sign_only(bars: tuple[dict, ...]) -> dict[str, str | int]:
    """Day-based loop mirroring the research replay structure."""
    closes = _closes(bars)
    opens = _opens(bars)
    net = Decimal("0")
    previous = 0
    trades = 0
    observations = 0
    for day in range(FORMATION_CLOSES + 1, len(closes)):
        signal = 1 if closes[day - 1] > closes[day - 1 - FORMATION_CLOSES] else 0
        if signal != previous:
            trades += 1
            net -= BTCUSDT_ALL_IN_BPS
            if signal == 1:
                net += _bps(closes[day] - opens[day], opens[day])
            else:
                net += _bps(opens[day] - closes[day - 1], closes[day - 1])
            previous = signal
        elif signal == 1:
            net += _bps(closes[day] - closes[day - 1], closes[day - 1])
        observations += 1
    baseline = _buy_and_hold(closes, opens) if observations else Decimal("0")
    return {
        "net_bps": str(net),
        "baseline_bps": str(baseline),
        "trades": trades,
        "obs": observations,
    }


def mirror_accounting(bars: tuple[dict, ...]) -> dict[str, str | int]:
    """Independent mirror: explicit holding state, separate cost ledger."""
    closes = _closes(bars)
    opens = _opens(bars)
    earned = Decimal("0")
    costs = Decimal("0")
    holding = 0
    trades = 0
    scored = 0
    day = FORMATION_CLOSES + 1
    while day < len(closes):
        wanted = 1 if closes[day - 1] > closes[day - 1 - FORMATION_CLOSES] else 0
        if wanted != holding:
            trades += 1
            costs += BTCUSDT_ALL_IN_BPS
            if wanted == 1:
                earned += _bps(closes[day] - opens[day], opens[day])
            else:
                earned += _bps(opens[day] - closes[day - 1], closes[day - 1])
            holding = wanted
        elif holding == 1:
            earned += _bps(closes[day] - closes[day - 1], closes[day - 1])
        scored += 1
        day += 1
    net = earned - costs
    baseline = _buy_and_hold(closes, opens) if scored else Decimal("0")
    return {"net_bps": str(net), "baseline_bps": str(baseline), "trades": trades, "obs": scored}


def plain_loop(bars: tuple[dict, ...]) -> dict[str, str | int]:
    """Minimal third loop: straight-line day arithmetic, no reused state."""
    closes = [Decimal(str(row["adj_close"])) for row in bars]
    opens = [Decimal(str(row["open"])) for row in bars]
    total = Decimal("0")
    prior = 0
    count = 0
    n_trades = 0
    for day, _ in enumerate(closes):
        if day < FORMATION_CLOSES + 1 or day >= len(closes):
            continue
        pos = 1 if closes[day - 1] > closes[day - 1 - FORMATION_CLOSES] else 0
        if pos != prior:
            n_trades += 1
            total -= BTCUSDT_ALL_IN_BPS
            if pos == 1:
                total += _bps(closes[day] - opens[day], opens[day])
            else:
                total += _bps(opens[day] - closes[day - 1], closes[day - 1])
            prior = pos
        elif pos == 1:
            total += _bps(closes[day] - closes[day - 1], closes[day - 1])
        count += 1
    base = _buy_and_hold(closes, opens) if count else Decimal("0")
    return {"net_bps": str(total), "baseline_bps": str(base), "trades": n_trades, "obs": count}


def _reported(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def agree(bars: tuple[dict, ...]) -> dict[str, str | int]:
    """Run all three; return the shared result or raise on disagreement.

    Nets agree at reporting precision (0.01 bps): 28-digit intermediate
    division can differ by a unit in the last place across summation
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
    "FORMATION_CLOSES",
    "agree",
    "mirror_accounting",
    "plain_loop",
    "replay_sign_only",
]
