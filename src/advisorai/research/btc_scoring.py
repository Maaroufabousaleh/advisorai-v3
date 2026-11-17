"""Offline forward-window scoring for the BTCUSDT candidate (no orders).

Pure accounting over recorded decisions, fills, and bars. Pinned rules:
- Missed or quarantined days hold the prior position (no new decision, no
  trade); only scored days can change the position.
- Position changes execute at the recorded fill (first open after the
  decision record time); a missing fill for a traded day fails closed.
- Every unit position change pays ``costs_bps``; both sides mark at the
  final close. The benchmark enters at the first open after the first
  decision record — the same timing rule as strategy fills.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from advisorai.research.btc_shadow_log import first_open_after


@dataclass(frozen=True, slots=True)
class Execution:
    date: str
    side: str
    fill_date: str
    fill_price: Decimal


@dataclass(frozen=True, slots=True)
class ScoringReport:
    strategy_value: Decimal
    benchmark_value: Decimal
    benchmark_entry_date: str
    mark_date: str
    executions: tuple[Execution, ...]
    positions: tuple[tuple[str, int], ...]


def score_window(
    *,
    view: tuple[dict[str, Any], ...],
    decisions: list[dict[str, Any]],
    fills: list[dict[str, Any]],
    costs_bps: str = "20",
    mark_date: str | None = None,
) -> ScoringReport:
    """Score recorded decisions against buy-and-hold over one window."""
    bars = {str(row["date"]): row for row in view}
    dates = sorted(bars)
    mark = mark_date if mark_date is not None else dates[-1]
    if mark not in bars:
        raise ValueError(f"mark date {mark} is not a received bar")
    scored = sorted(
        (entry for entry in decisions if str(entry["date"]) <= mark),
        key=lambda entry: str(entry["date"]),
    )
    if not scored:
        raise ValueError("no decisions on or before the mark date")
    fills_by_date = {str(item["date"]): item for item in fills}
    factor = Decimal(1) - Decimal(costs_bps) / Decimal(10_000)

    value = Decimal(1)
    position = 0
    anchor: Decimal | None = None
    executions: list[Execution] = []
    positions: list[tuple[str, int]] = []
    for entry in scored:
        day = str(entry["date"])
        target = position if entry.get("abstained") else int(entry["signal"])
        if target != position:
            fill = fills_by_date.get(day)
            if fill is None:
                raise ValueError(f"cannot score: fill missing for traded day {day}")
            price = Decimal(str(fill["fill_price"]))
            if position == 1:
                assert anchor is not None
                value = value * price / anchor
            value = value * factor
            anchor = price
            position = target
            executions.append(
                Execution(
                    date=day,
                    side="buy" if target == 1 else "sell",
                    fill_date=str(fill["fill_date"]),
                    fill_price=price,
                )
            )
        positions.append((day, position))
    if position == 1:
        assert anchor is not None
        value = value * Decimal(str(bars[mark]["adj_close"])) / anchor

    first_record = min(int(entry["recorded_at_ms"]) for entry in scored)
    entry_day = first_open_after(dates, first_record)
    if entry_day is None:
        raise ValueError("cannot score: benchmark entry not yet observable")
    benchmark_value = (
        factor * Decimal(str(bars[mark]["adj_close"])) / Decimal(str(bars[entry_day]["open"]))
    )
    return ScoringReport(
        strategy_value=value,
        benchmark_value=benchmark_value,
        benchmark_entry_date=entry_day,
        mark_date=mark,
        executions=tuple(executions),
        positions=tuple(positions),
    )


__all__ = ["Execution", "ScoringReport", "score_window"]
