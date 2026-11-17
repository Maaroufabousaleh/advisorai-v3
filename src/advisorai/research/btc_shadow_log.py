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
from decimal import Decimal
from pathlib import Path
from typing import Any

from advisorai.research.btc_replay import FORMATION_CLOSES
from advisorai.research.btc_spot import check_continuity
from advisorai.research.replay import load_snapshot_bars

MODEL_ID = "btc-tsmom-63d-longflat-v1"


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """Writable from received bars only: date, signal, position, the hash
    of the qualified input, the receipt frontier, and the record time. A
    bar date alone is not proof of receipt: every formation close must
    carry a receipt at or before the record time. No fill, no future bar,
    no venue data."""

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


@dataclass(frozen=True, slots=True)
class FillRecord:
    """Separate artifact, observable only after the decision: the fill
    prints at the next open, so it requires the next bar received at or
    before the fill record time, with a price timestamp strictly after
    the recorded decision."""

    date: str
    fill_date: str
    fill_price: str
    observable_after: str
    recorded_at_ms: int


def verify_inputs_received(
    dates: list[str], index: int, received_at: dict[str, int], recorded_at_ms: int
) -> None:
    """Prove the 64 formation closes were received by the record time."""
    for position in range(index - FORMATION_CLOSES, index + 1):
        day = dates[position]
        receipt = received_at.get(day)
        if receipt is None:
            raise ValueError(f"decision input {day} has no receipt timestamp")
        if receipt > recorded_at_ms:
            raise ValueError(f"decision input {day} received after the record time")


def observe_fill(
    view: tuple[dict[str, Any], ...],
    date: str,
    *,
    received_at: dict[str, int],
    recorded_at_ms: int,
) -> FillRecord:
    """Record the simulated fill for a decision once its bar is received.

    The fill price timestamp must be strictly after the recorded decision,
    and the fill bar must carry a receipt at or before the fill record
    time. Anything else is rejected, never recorded.
    """
    dates = [str(row["date"]) for row in view]
    if date not in dates:
        raise ValueError(f"decision date {date} is not a received bar")
    index = dates.index(date)
    if index + 1 >= len(view):
        raise ValueError(f"fill for {date} is not yet observable: next bar not received")
    fill_row = view[index + 1]
    fill_date = str(fill_row["date"])
    if fill_date <= date:
        raise ValueError(f"fill price timestamp {fill_date} precedes recorded decision {date}")
    receipt = received_at.get(fill_date)
    if receipt is None or receipt > recorded_at_ms:
        raise ValueError(f"fill bar {fill_date} was not received by the fill record time")
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
) -> DecisionRecord:
    """Record one decision from its own completed close, or quarantine it.

    ``date`` is the decision date: the signal uses that date's completed
    close, never the prior bar. Every formation close must carry a receipt
    at or before the record time — a bar date alone is not proof of
    receipt. The view's segments are qualified by the caller (collector +
    session cutoff). Violations here — unknown date, insufficient history,
    duplicate dates, late or missing receipts — are recorded as
    abstentions, never scored.
    """
    try:
        dates = [str(row["date"]) for row in view]
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
        )
    append_decision(state_dir, record)
    return record


__all__ = [
    "MODEL_ID",
    "DecisionRecord",
    "FillRecord",
    "append_decision",
    "decide",
    "load_view",
    "log_forward_day",
    "observe_fill",
    "verify_inputs_received",
]
