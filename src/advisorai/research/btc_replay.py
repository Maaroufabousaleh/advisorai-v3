"""Three independent historical replays for the BTCUSDT long/flat candidate.

Plumbing only: these agree-or-fail on the pinned historical snapshot. No
verdict language lives here; pass/fail applies to the forward shadow only.

Pinned profile (see docs/plans/btcusdt-paper-v1-candidate.md, amendment 1):
- Formation 63 closes, daily re-evaluation, long/flat only.
- Decision at a bar close uses only closes available then; entry at the
  next open, exit at the next close; realized open-to-close in bps.
- Costs: fee 10 + spread 1 + slippage 9 = 20 bps all-in per unit change.
  Buy-and-hold pays one entry leg only; cash is 0.
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


def replay_sign_only(bars: tuple[dict, ...]) -> dict[str, str | int]:
    """Cutoff/signal/costing loop mirroring the research replay structure."""
    closes = _closes(bars)
    opens = _opens(bars)
    net = Decimal("0")
    baseline = Decimal("0")
    previous = 0
    trades = 0
    observations = 0
    for index in range(FORMATION_CLOSES, len(closes) - 1):
        position = 1 if closes[index] > closes[index - FORMATION_CLOSES] else 0
        entry = opens[index + 1]
        exit_ = closes[index + 1]
        realized = (exit_ - entry) / entry * Decimal("10000") if entry else Decimal("0")
        net += Decimal(position) * realized
        baseline += realized
        change = abs(position - previous)
        if change:
            trades += 1
            net -= Decimal(change) * BTCUSDT_ALL_IN_BPS
        if index == FORMATION_CLOSES:
            baseline -= BTCUSDT_ALL_IN_BPS
        previous = position
        observations += 1
    return {
        "net_bps": str(net),
        "baseline_bps": str(baseline),
        "trades": trades,
        "obs": observations,
    }


def mirror_accounting(bars: tuple[dict, ...]) -> dict[str, str | int]:
    """Independent mirror: explicit state machine, separate cost ledger."""
    closes = _closes(bars)
    opens = _opens(bars)
    earned = Decimal("0")
    costs = Decimal("0")
    baseline_earned = Decimal("0")
    baseline_paid = False
    position = 0
    trades = 0
    scored = 0
    index = FORMATION_CLOSES
    while index < len(closes) - 1:
        signal = closes[index] > closes[index - FORMATION_CLOSES]
        wanted = 1 if signal else 0
        fill_open = opens[index + 1]
        fill_close = closes[index + 1]
        leg = (fill_close - fill_open) / fill_open * Decimal("10000") if fill_open else Decimal("0")
        if wanted != position:
            trades += 1
            costs += BTCUSDT_ALL_IN_BPS
            position = wanted
        earned += Decimal(position) * leg
        baseline_earned += leg
        if not baseline_paid:
            baseline_paid = True
            costs_baseline = BTCUSDT_ALL_IN_BPS
        scored += 1
        index += 1
    net = earned - costs
    baseline = baseline_earned - costs_baseline if scored else Decimal("0")
    return {"net_bps": str(net), "baseline_bps": str(baseline), "trades": trades, "obs": scored}


def plain_loop(bars: tuple[dict, ...]) -> dict[str, str | int]:
    """Minimal third loop: no state reuse, straight-line arithmetic."""
    closes = [Decimal(str(row["adj_close"])) for row in bars]
    opens = [Decimal(str(row["open"])) for row in bars]
    total = Decimal("0")
    base = Decimal("0")
    prior = 0
    count = 0
    n_trades = 0
    for i, _ in enumerate(closes):
        if i < FORMATION_CLOSES or i + 1 >= len(closes):
            continue
        pos = 1 if closes[i] > closes[i - FORMATION_CLOSES] else 0
        ret = (opens[i + 1], closes[i + 1])
        pct = (ret[1] - ret[0]) / ret[0] * Decimal("10000") if ret[0] else Decimal("0")
        total += pos * pct
        base += pct
        if pos != prior:
            n_trades += 1
            total -= BTCUSDT_ALL_IN_BPS
        if count == 0:
            base -= BTCUSDT_ALL_IN_BPS
        prior = pos
        count += 1
    return {"net_bps": str(total), "baseline_bps": str(base), "trades": n_trades, "obs": count}


def agree(bars: tuple[dict, ...]) -> dict[str, str | int]:
    """Run all three; return the shared result or raise on disagreement."""
    results = (replay_sign_only(bars), mirror_accounting(bars), plain_loop(bars))
    first = results[0]
    for other in results[1:]:
        if other != first:
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
