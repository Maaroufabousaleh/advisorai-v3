#!/usr/bin/env python3
"""Create immutable outcome links for a sealed forward prediction ledger.

This is an offline post-outcome boundary.  It reads immutable prediction and
completed-case ledgers, links only exact ``(instrument, cutoff)`` identities,
and writes a new append-only outcome-link ledger.  It never changes a
prediction, generates a missed prediction, acquires data, loads credentials,
or submits an order.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from advisorai.phase4 import (
    FORWARD_CASE_SCHEMA,
    ForwardPredictionLedgerEntry,
    ForwardPredictionOutcomeLinkLedger,
    V3CoreForecastCase,
)
from advisorai.phase4.v3core_cadence import sha256_json


class OutcomeLinkRefused(ValueError):
    """Raised when immutable prediction/case identities cannot be linked."""


def _load_cases(path: Path) -> dict[tuple[str, datetime], V3CoreForecastCase]:
    if not path.is_file():
        raise OutcomeLinkRefused(f"completed-case ledger is missing: {path}")
    cases: dict[tuple[str, datetime], V3CoreForecastCase] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if record.get("schema") != FORWARD_CASE_SCHEMA:
                raise ValueError("case schema is not the forward case schema")
            case_payload = record["case"]
            if sha256_json(case_payload) != str(record["case_hash"]):
                raise ValueError("case hash mismatch")
            case = V3CoreForecastCase.model_validate(case_payload)
            case_key = (case.instrument, case.cutoff)
            if case_key in cases and cases[case_key].case_id != case.case_id:
                raise ValueError("multiple case identities share one instrument/cutoff")
            cases[case_key] = case
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OutcomeLinkRefused(f"invalid completed case at line {line_number}") from exc
    return cases


def _load_predictions(paths: tuple[Path, ...]) -> tuple[ForwardPredictionLedgerEntry, ...]:
    entries: list[ForwardPredictionLedgerEntry] = []
    seen: set[str] = set()
    for path in paths:
        if not path.is_file():
            raise OutcomeLinkRefused(f"prediction ledger is missing: {path}")
        previous: str | None = None
        expected_sequence = 1
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entry = ForwardPredictionLedgerEntry.model_validate_json(line)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise OutcomeLinkRefused(
                    f"invalid prediction ledger entry at {path}:{line_number}"
                ) from exc
            if entry.sequence != expected_sequence or entry.previous_record_hash != previous:
                raise OutcomeLinkRefused(f"prediction ledger chain is invalid: {path}")
            if entry.prediction.prediction_id in seen:
                raise OutcomeLinkRefused("prediction ledgers contain a duplicate prediction")
            seen.add(entry.prediction.prediction_id)
            entries.append(entry)
            previous = entry.record_hash
            expected_sequence += 1
    return tuple(entries)


def link_predictions_to_cases(
    *,
    prediction_ledger_paths: tuple[Path, ...],
    completed_cases_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Write a new deterministic outcome-link ledger for exact case matches."""

    output_path = output_path.resolve()
    if output_path.exists():
        raise OutcomeLinkRefused(f"outcome-link output already exists: {output_path}")
    cases = _load_cases(completed_cases_path.resolve())
    entries = _load_predictions(tuple(path.resolve() for path in prediction_ledger_paths))
    if not entries:
        raise OutcomeLinkRefused("prediction ledgers contain no predictions")
    links: list[tuple[ForwardPredictionLedgerEntry, V3CoreForecastCase]] = []
    missing: list[str] = []
    for entry in entries:
        prediction = entry.prediction
        case = cases.get((prediction.instrument, prediction.cutoff))
        if case is None:
            missing.append(prediction.prediction_id)
            continue
        if case.realized_at <= prediction.cutoff:
            raise OutcomeLinkRefused(
                f"outcome is not strictly after prediction cutoff: {prediction.prediction_id}"
            )
        links.append((entry, case))
    if missing:
        raise OutcomeLinkRefused(
            "prediction outcomes are not available for: " + ", ".join(sorted(missing))
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ledger = ForwardPredictionOutcomeLinkLedger(output_path)
    for entry, case in sorted(
        links,
        key=lambda item: (
            item[0].prediction.cutoff,
            item[0].prediction.instrument,
            item[0].prediction.model,
            item[0].prediction.prediction_id,
        ),
    ):
        if not ledger.append(
            prediction_id=entry.prediction.prediction_id,
            outcome_case_id=case.case_id,
            linked_at=case.realized_at,
        ):
            raise OutcomeLinkRefused("outcome-link identity was unexpectedly duplicated")
    return {
        "output": str(output_path),
        "prediction_count": len(entries),
        "linked_count": len(links),
        "network_calls": 0,
        "credentials_loaded": False,
        "order_writes_attempted": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prediction-ledger", type=Path, action="append", required=True)
    parser.add_argument("--completed-cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    result = link_predictions_to_cases(
        prediction_ledger_paths=tuple(path.resolve() for path in args.prediction_ledger),
        completed_cases_path=args.completed_cases.resolve(),
        output_path=args.output.resolve(),
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
