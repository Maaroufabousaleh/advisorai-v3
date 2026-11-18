"""Durable daily shadow job: stop/restart safe, idempotent, no rewrites.

Processes decision dates in order against a qualified view. Progress is
persisted after every date, so a stop or crash resumes after the last
completed date. Re-running a completed date appends nothing: records are
never rewritten, and missed dates are caught up in order with their own
decision dates and late record times (transparent catch-up, not
backdating). Fills are observed only for scored decisions once their
bars are received, into a separate fills log.

Read-only toward inputs: snapshot, feed, venue untouched. No orders.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from advisorai.research.btc_shadow_log import FillRecord, log_forward_day, observe_fill

STATE_FILENAME = "shadow_state.json"
FILLS_FILENAME = "fills.jsonl"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _read_state(state_dir: Path) -> dict[str, Any]:
    path = state_dir / STATE_FILENAME
    if not path.is_file():
        return {"last_completed_date": None, "runs": []}
    return json.loads(path.read_text())


def _write_state(state_dir: Path, state: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / STATE_FILENAME).write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def append_fill(state_dir: Path, record: FillRecord) -> Path:
    """Append one fill record to the out-of-tree fills log."""
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / FILLS_FILENAME
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")
    return log_path


def run_shadow_days(
    *,
    view: tuple[dict[str, Any], ...],
    forward_hash: str,
    state_dir: Path,
    dates: list[str],
    received_at: dict[str, int],
    recorded_at_ms: int,
    costs_bps: str = "20",
) -> dict[str, Any]:
    """Run the shadow job over decision dates in order; idempotent.

    Dates already present in the decisions log are skipped without
    rewriting. Dates whose bars have not completed are left unrecorded
    for a later run. Each processed date persists progress before moving
    on. After decisions, fills are observed for scored decisions at the
    first price printed after each decision's own record time.
    """
    state = _read_state(state_dir)
    entries = _read_jsonl(state_dir / "decisions.jsonl")
    recorded = {entry["date"] for entry in entries}
    decision_time = {entry["date"]: entry["recorded_at_ms"] for entry in entries}
    scored = {entry["date"] for entry in entries if not entry.get("abstained")}
    filled = {entry["date"] for entry in _read_jsonl(state_dir / FILLS_FILENAME)}
    processed: list[str] = []
    quarantined: list[str] = []
    skipped: list[str] = []
    for date in sorted(dates):
        if date in recorded:
            continue
        record = log_forward_day(
            view=view,
            forward_hash=forward_hash,
            state_dir=state_dir,
            date=date,
            received_at=received_at,
            recorded_at_ms=recorded_at_ms,
            costs_bps=costs_bps,
        )
        if record is None:
            skipped.append(date)
            continue
        recorded.add(date)
        decision_time[date] = record.recorded_at_ms
        if not record.abstained:
            scored.add(date)
        processed.append(date)
        if record.abstained:
            quarantined.append(date)
        state["last_completed_date"] = date
        state["runs"].append({"date": date, "recorded_at_ms": recorded_at_ms})
        _write_state(state_dir, state)
    fills_logged: list[str] = []
    for date in sorted(scored):
        if date in filled:
            continue
        try:
            fill = observe_fill(
                view,
                date,
                received_at=received_at,
                recorded_at_ms=recorded_at_ms,
                decision_recorded_at_ms=decision_time[date],
            )
        except ValueError:
            continue
        append_fill(state_dir, fill)
        fills_logged.append(date)
    return {
        "processed": processed,
        "quarantined": quarantined,
        "skipped": skipped,
        "fills_logged": fills_logged,
        "last_completed_date": state["last_completed_date"],
    }


__all__ = [
    "FILLS_FILENAME",
    "STATE_FILENAME",
    "append_fill",
    "run_shadow_days",
]
