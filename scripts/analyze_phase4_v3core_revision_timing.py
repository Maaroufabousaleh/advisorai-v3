#!/usr/bin/env python3
"""Compute raw kline revision timing statistics without selecting a grace period.

This is a sealed-root, offline analysis boundary.  It reads the raw response
hash chain, records only timing/version statistics, and writes a new artifact.
It does not choose or activate a collector finality rule, normalize bars,
modify evidence, acquire data, load credentials, or make order calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from advisorai.phase4.v3core_integrity import (
    _decode_raw_observations,
    _load_raw_records,
)

SCHEMA = "advisorai.phase4.v3-core.revision-timing-analysis.v1"
TERMINAL_RUN_STATES = frozenset({"target_reached", "deadline_reached", "stopped_with_evidence"})


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _write_new(path: Path, payload: object) -> str:
    if path.exists():
        raise ValueError(f"immutable output already exists: {path}")
    encoded = json.dumps(payload, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
    return hashlib.sha256(encoded).hexdigest()


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def _lag(observed_at: datetime | None, interval_end: datetime) -> float | None:
    if observed_at is None:
        return None
    return max(0.0, (observed_at - interval_end).total_seconds())


def analyze(
    *,
    raw_responses_path: Path,
    terminal_observed_at: datetime,
    source_manifest_path: Path | None = None,
    terminal_evidence_eligible: bool = True,
) -> dict[str, object]:
    records = _load_raw_records(raw_responses_path)
    observations = tuple(
        observation for record in records for observation in _decode_raw_observations(record)
    )
    if observations and terminal_observed_at < max(item.receipt_at for item in observations):
        raise ValueError("terminal boundary precedes a raw response receipt")

    by_key: dict[tuple[str, datetime], list[object]] = defaultdict(list)
    for observation in observations:
        by_key[(observation.instrument, observation.interval_end)].append(observation)

    intervals: list[dict[str, object]] = []
    for instrument, interval_end in sorted(by_key):
        all_observations = by_key[(instrument, interval_end)]
        closed = tuple(item for item in all_observations if item.closed_at_receipt)
        if not closed:
            continue
        first_closed = closed[0]
        first_version = first_closed.raw_ohlcv_hash
        first_revision = next(
            (item for item in closed[1:] if item.raw_ohlcv_hash != first_version),
            None,
        )
        last_revision = next(
            (item for item in reversed(closed) if item.raw_ohlcv_hash != first_version),
            None,
        )
        terminal_version = closed[-1].raw_ohlcv_hash
        terminal_seen_sequences: set[int] = set()
        terminal_occurrences = []
        for item in closed:
            if (
                item.raw_ohlcv_hash == terminal_version
                and item.raw_response_sequence not in terminal_seen_sequences
            ):
                terminal_seen_sequences.add(item.raw_response_sequence)
                terminal_occurrences.append(item)
        first_repeated: object | None = None
        seen_by_version: dict[str, set[int]] = defaultdict(set)
        for item in closed:
            seen_by_version[item.raw_ohlcv_hash].add(item.raw_response_sequence)
            if len(seen_by_version[item.raw_ohlcv_hash]) >= 2:
                first_repeated = item
                break
        second_terminal = terminal_occurrences[1] if len(terminal_occurrences) >= 2 else None
        intervals.append(
            {
                "instrument": instrument,
                "interval_end": interval_end.isoformat().replace("+00:00", "Z"),
                "raw_observation_count": len(all_observations),
                "closed_observation_count": len(closed),
                "distinct_closed_versions": len({item.raw_ohlcv_hash for item in closed}),
                "first_post_close_receipt_lag_seconds": _lag(first_closed.receipt_at, interval_end),
                "first_revision_lag_seconds": _lag(
                    first_revision.receipt_at if first_revision else None, interval_end
                ),
                "last_revision_lag_seconds": _lag(
                    last_revision.receipt_at if last_revision else None, interval_end
                ),
                "first_repeated_version_lag_seconds": _lag(
                    first_repeated.receipt_at if first_repeated else None, interval_end
                ),
                "second_terminal_confirmation_lag_seconds": _lag(
                    second_terminal.receipt_at if second_terminal else None, interval_end
                ),
                "terminal_confirmation_count": len(terminal_occurrences),
            }
        )

    metric_names = (
        "first_post_close_receipt_lag_seconds",
        "first_revision_lag_seconds",
        "last_revision_lag_seconds",
        "first_repeated_version_lag_seconds",
        "second_terminal_confirmation_lag_seconds",
    )
    summary: dict[str, object] = {}
    for symbol in ("BTCUSDT", "ETHUSDT"):
        symbol_rows = [row for row in intervals if row["instrument"] == symbol]
        metric_summary = {
            metric: {
                "observed_count": sum(row[metric] is not None for row in symbol_rows),
                "p50_seconds": _percentile(
                    [float(row[metric]) for row in symbol_rows if row[metric] is not None],
                    0.50,
                ),
                "p95_seconds": _percentile(
                    [float(row[metric]) for row in symbol_rows if row[metric] is not None],
                    0.95,
                ),
                "max_seconds": max(
                    (float(row[metric]) for row in symbol_rows if row[metric] is not None),
                    default=None,
                ),
            }
            for metric in metric_names
        }
        summary[symbol] = {
            "interval_count": len(symbol_rows),
            "metrics": metric_summary,
        }

    payload: dict[str, object] = {
        "schema": SCHEMA,
        "terminal_observed_at": terminal_observed_at.isoformat().replace("+00:00", "Z"),
        "raw_responses_sha256": _sha256(raw_responses_path),
        "source_manifest_sha256": (
            _sha256(source_manifest_path) if source_manifest_path is not None else None
        ),
        "raw_response_count": len(records),
        "raw_observation_count": len(observations),
        "interval_count": len(intervals),
        "summary_by_symbol": summary,
        "intervals": intervals,
        "selection_status": "STATISTICS_ONLY_NO_GRACE_SELECTED",
        "terminal_evidence_eligible": terminal_evidence_eligible,
        "network_calls": 0,
        "credentials_loaded": False,
        "order_writes_attempted": False,
    }
    payload["analysis_fingerprint"] = hashlib.sha256(
        _canonical({key: value for key, value in payload.items() if key != "analysis_fingerprint"})
    ).hexdigest()
    return payload


def _validate_run_status(status: object, *, allow_unsealed: bool) -> bool:
    if not isinstance(status, dict):
        raise ValueError("forward status must be an object")
    state = status.get("state")
    if state == "running":
        if allow_unsealed:
            return False
        raise ValueError("refusing analysis of a running root; seal it first")
    if state not in TERMINAL_RUN_STATES:
        raise ValueError(f"forward root has no supported terminal state: {state!r}")
    if state == "target_reached" and status.get("minimum_reached") is not True:
        raise ValueError("target-reached root does not attest its frozen minimum")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run-directory", type=Path)
    source.add_argument("--raw-responses", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--terminal-observed-at", type=_timestamp, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-unsealed", action="store_true")
    args = parser.parse_args()

    if args.run_directory is not None:
        run = args.run_directory.resolve()
        status_path = run / "status.json"
        terminal_evidence_eligible = True
        if status_path.is_file():
            status = json.loads(status_path.read_text(encoding="utf-8"))
            try:
                terminal_evidence_eligible = _validate_run_status(
                    status,
                    allow_unsealed=args.allow_unsealed,
                )
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
        raw_path = run / "raw-responses.jsonl"
        manifest_path = args.source_manifest or run / "manifest.json"
    else:
        raw_path = args.raw_responses.resolve()
        manifest_path = args.source_manifest.resolve() if args.source_manifest else None
        terminal_evidence_eligible = True

    try:
        payload = analyze(
            raw_responses_path=raw_path,
            terminal_observed_at=args.terminal_observed_at,
            source_manifest_path=manifest_path if manifest_path.is_file() else None,
            terminal_evidence_eligible=terminal_evidence_eligible,
        )
        artifact_sha256 = _write_new(args.output.resolve(), payload)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"revision timing analysis refused ({type(exc).__name__})") from exc
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "artifact_sha256": artifact_sha256,
                "analysis_fingerprint": payload["analysis_fingerprint"],
                "interval_count": payload["interval_count"],
                "selection_status": payload["selection_status"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
