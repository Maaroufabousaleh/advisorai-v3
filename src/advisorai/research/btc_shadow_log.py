"""Read-only forward shadow log for the BTCUSDT candidate (no order authority).

Appends one JSONL decision record per scored day into an out-of-tree state
dir. Never writes to snapshot or feed inputs, never contacts the venue, and
never submits anything. The forward segment is qualified once per session
at collection; here, bars that violate the view contract are logged as
abstentions (quarantine), never scored.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from advisorai.research.btc_replay import FORMATION_CLOSES
from advisorai.research.btc_spot import check_continuity
from advisorai.research.replay import load_snapshot_bars

MODEL_ID = "btc-tsmom-63d-longflat-v1"
DAY_MS = 86_400_000


def bar_open_ms(date: str) -> int:
    """Market timestamp of a daily bar's open print (00:00 UTC)."""
    moment = datetime.fromisoformat(date).replace(tzinfo=UTC)
    return int(moment.timestamp() * 1000)


def bar_close_ms(date: str) -> int:
    """Market timestamp of a daily bar's close (next 00:00 UTC)."""
    return bar_open_ms(date) + DAY_MS


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """Writable from received bars only: date, signal, position, the hash
    of the qualified input, the receipt frontier, and the record time. A
    bar date alone is not proof of receipt: every formation close must
    carry a receipt at or before the record time, and no receipt may
    precede its bar's market close. Missed days (bar absent after the
    backfill window) are recorded as missed and excluded from scoring.
    No fill, no future bar, no venue data."""

    date: str
    signal: int
    position: int
    bars_hash: str
    received_through: str
    recorded_at_ms: int
    model_id: str
    costs_bps: str
    abstained: bool
    reason: str
    missed: bool = False


@dataclass(frozen=True, slots=True)
class FillRecord:
    """Separate artifact, observable only after the decision: the fill is
    priced at the first open printing strictly after the decision was
    actually recorded, so it requires that bar received (after its own
    market close) at or before the fill record time."""

    date: str
    fill_date: str
    fill_price: str
    observable_after: str
    recorded_at_ms: int


def verify_inputs_received(
    dates: list[str], index: int, received_at: dict[str, int], recorded_at_ms: int
) -> None:
    """Prove the 64 formation closes arrived after their closes and by the record time."""
    for position in range(index - FORMATION_CLOSES, index + 1):
        day = dates[position]
        receipt = received_at.get(day)
        if receipt is None:
            raise ValueError(f"decision input {day} has no receipt timestamp")
        if receipt < bar_close_ms(day):
            raise ValueError(f"decision input {day} received before its close")
        if receipt > recorded_at_ms:
            raise ValueError(f"decision input {day} received after the record time")


def observe_fill(
    view: tuple[dict[str, Any], ...],
    date: str,
    *,
    received_at: dict[str, int],
    recorded_at_ms: int,
    decision_recorded_at_ms: int,
) -> FillRecord:
    """Record the simulated fill at the first price printed after the decision.

    Three timestamps stay distinct: the price's market timestamp (the fill
    bar's open print), the input receipt timestamp (when the completed bar
    arrived), and the decision record timestamp (when the decision was
    actually recorded). A fill priced at or before the actual decision
    time is rejected, as is a fill bar received before its market close.
    """
    dates = [str(row["date"]) for row in view]
    if date not in dates:
        raise ValueError(f"decision date {date} is not a received bar")
    index = dates.index(date)
    fill_row: dict[str, Any] | None = None
    for candidate in view[index + 1 :]:
        if bar_open_ms(str(candidate["date"])) > decision_recorded_at_ms:
            fill_row = candidate
            break
    if fill_row is None:
        raise ValueError(
            f"no eligible fill for {date}: "
            "every received price is at or before the actual decision time"
        )
    fill_date = str(fill_row["date"])
    receipt = received_at.get(fill_date)
    if receipt is None or receipt > recorded_at_ms:
        raise ValueError(f"fill bar {fill_date} was not received by the fill record time")
    if receipt < bar_close_ms(fill_date):
        raise ValueError(f"fill bar {fill_date} received before its close")
    return FillRecord(
        date=date,
        fill_date=fill_date,
        fill_price=str(fill_row["open"]),
        observable_after=fill_date,
        recorded_at_ms=recorded_at_ms,
    )


def decide(closes: list[Decimal], decision_index: int) -> int:
    """Signal from closes available at the decision close; long/flat only."""
    if decision_index < FORMATION_CLOSES or decision_index - FORMATION_CLOSES < 0:
        raise ValueError("decision index lacks formation history")
    return 1 if closes[decision_index] > closes[decision_index - FORMATION_CLOSES] else 0


def load_view(snapshot_dir: Path, forward_dir: Path) -> tuple[dict[str, Any], ...]:
    """Concatenate snapshot + forward bars after proving a gapless handoff."""
    snapshot_bars, _ = load_snapshot_bars(snapshot_dir)
    forward_bars, _ = load_snapshot_bars(forward_dir)
    ordered = snapshot_bars["BTCUSDT"] + forward_bars["BTCUSDT"]
    dates = [str(row["date"]) for row in ordered]
    if len(set(dates)) != len(dates):
        raise ValueError("shadow view has duplicate bar dates")
    check_continuity(dates[len(snapshot_bars["BTCUSDT"]) - 1], dates[len(snapshot_bars["BTCUSDT"])])
    return ordered


def append_decision(state_dir: Path, record: DecisionRecord) -> Path:
    """Append one record to the out-of-tree shadow log."""
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "decisions.jsonl"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")
    return log_path


def log_forward_day(
    *,
    view: tuple[dict[str, Any], ...],
    forward_hash: str,
    state_dir: Path,
    date: str,
    received_at: dict[str, int],
    recorded_at_ms: int,
    costs_bps: str = "20",
) -> DecisionRecord | None:
    """Record one decision from its own completed close, quarantine it, or skip it.

    ``date`` is the decision date: the signal uses that date's completed
    close, never the prior bar. Every formation close must carry a receipt
    at or before the record time — a bar date alone is not proof of
    receipt — and no receipt may precede its bar's market close. The
    view's segments are qualified by the caller (collector + session
    cutoff); re-running collection backfills gaps before this runs.
    A requested date whose bar has not completed returns None (retry
    later, nothing recorded). A requested date whose close passed but
    whose bar is still absent is recorded as missed, never scored.
    Other violations — insufficient history, duplicate dates, late or
    missing receipts — are recorded as abstentions, never scored.
    """
    dates = [str(row["date"]) for row in view]
    if date not in dates and bar_close_ms(date) > recorded_at_ms:
        return None
    try:
        if len(set(dates)) != len(dates):
            raise ValueError("shadow view has duplicate bar dates")
        if date not in dates:
            raise ValueError(f"decision date {date} is not a qualified bar")
        index = dates.index(date)
        closes = [Decimal(str(row["adj_close"])) for row in view]
        signal = decide(closes, index)
        verify_inputs_received(dates, index, received_at, recorded_at_ms)
        record = DecisionRecord(
            date=date,
            signal=signal,
            position=signal,
            bars_hash=forward_hash,
            received_through=dates[-1],
            recorded_at_ms=recorded_at_ms,
            model_id=MODEL_ID,
            costs_bps=costs_bps,
            abstained=False,
            reason="",
        )
    except ValueError as exc:
        record = DecisionRecord(
            date=date,
            signal=0,
            position=0,
            bars_hash="",
            received_through="",
            recorded_at_ms=recorded_at_ms,
            model_id=MODEL_ID,
            costs_bps=costs_bps,
            abstained=True,
            reason=f"{type(exc).__name__}: {exc}",
            missed=date not in dates,
        )
    append_decision(state_dir, record)
    return record


__all__ = [
    "MODEL_ID",
    "DecisionRecord",
    "FillRecord",
    "append_decision",
    "bar_close_ms",
    "bar_open_ms",
    "decide",
    "load_view",
    "log_forward_day",
    "observe_fill",
    "verify_inputs_received",
]
