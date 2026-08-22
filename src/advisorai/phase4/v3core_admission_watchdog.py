"""Read-only fail-fast feasibility checks for a future V3-Core generation.

The watchdog evaluates a sanitized snapshot.  It never starts, stops, or
restarts a process and has no data, credential, GPU, or execution capability.
Its purpose is to distinguish a live process from a candidate path that can
still satisfy complete prospective coverage.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from advisorai.phase4.v3core_cadence import V3_CORE_SYMBOLS
from advisorai.phase4.v3core_generation_readiness import (
    EXPECTED_CASES_PER_SYMBOL,
    GenerationCoverageInput,
    GenerationReadinessReport,
    evaluate_generation_readiness,
)

WATCHDOG_SCHEMA = "advisorai.phase4.v3-core.forward.admission-watchdog.v1"


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _hash_payload(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _symbols(value: dict[str, int]) -> dict[str, int]:
    normalized = {key.strip().upper(): count for key, count in value.items()}
    if set(normalized) != set(V3_CORE_SYMBOLS):
        raise ValueError("watchdog counts must contain exactly BTCUSDT and ETHUSDT")
    if any(count < 0 for count in normalized.values()):
        raise ValueError("watchdog counts cannot be negative")
    return normalized


class GenerationWatchdogSnapshot(BaseModel):
    """Sanitized point-in-time state supplied by an external monitor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime
    source_process_alive: bool
    source_health_valid: bool
    source_terminal: bool = False
    latest_raw_receipt_at: datetime | None = None
    latest_admitted_final_bar_at: datetime | None = None
    unresolved_bar_count: int = Field(default=0, ge=0)
    candidate_process_alive: bool
    candidate_model_loaded: bool
    latest_eligible_cutoff: datetime | None = None
    last_successful_prediction_at: dict[str, datetime | None]
    candidate_predictions: dict[str, int]
    source_completed_cases: dict[str, int]
    remaining_future_cutoffs: dict[str, int]
    rejection_count: int = Field(default=0, ge=0)
    consecutive_failures: int = Field(default=0, ge=0)
    candidate_ledger_healthy: bool
    candidate_root_healthy: bool
    cases_per_symbol_target: int = EXPECTED_CASES_PER_SYMBOL

    @field_validator(
        "observed_at",
        "latest_raw_receipt_at",
        "latest_admitted_final_bar_at",
        "latest_eligible_cutoff",
    )
    @classmethod
    def aware_timestamps(cls, value: datetime | None, info: object) -> datetime | None:
        return None if value is None else _aware(value, getattr(info, "field_name", "timestamp"))

    @field_validator("last_successful_prediction_at")
    @classmethod
    def validate_prediction_timestamps(
        cls, value: dict[str, datetime | None]
    ) -> dict[str, datetime | None]:
        if set(key.strip().upper() for key in value) != set(V3_CORE_SYMBOLS):
            raise ValueError("last successful prediction timestamps must cover BTCUSDT and ETHUSDT")
        return {
            key.strip().upper(): (None if timestamp is None else _aware(timestamp, key))
            for key, timestamp in value.items()
        }

    @field_validator("candidate_predictions", "source_completed_cases", "remaining_future_cutoffs")
    @classmethod
    def validate_counts(cls, value: dict[str, int]) -> dict[str, int]:
        return _symbols(value)

    @field_validator("cases_per_symbol_target")
    @classmethod
    def validate_target(cls, value: int) -> int:
        if value != EXPECTED_CASES_PER_SYMBOL:
            raise ValueError("V3-Core watchdog requires a 64-case target per symbol")
        return value


class GenerationWatchdogReport(BaseModel):
    """Deterministic read-only watchdog result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[WATCHDOG_SCHEMA] = WATCHDOG_SCHEMA
    status: Literal[
        "CANDIDATE_COVERAGE_POSSIBLE",
        "CANDIDATE_DEGRADED_BUT_RECOVERABLE",
        "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION",
    ]
    source_process_alive: bool
    candidate_process_alive: bool
    candidate_model_loaded: bool
    prediction_counts: dict[str, int]
    rejection_count: int
    consecutive_failures: int
    reasons: tuple[str, ...]
    readiness: GenerationReadinessReport
    snapshot_hash: str
    report_hash: str


def evaluate_watchdog(snapshot: GenerationWatchdogSnapshot) -> GenerationWatchdogReport:
    """Evaluate coverage feasibility without taking an operational action."""

    coverage = GenerationCoverageInput(
        source_completed_cases=snapshot.source_completed_cases,
        candidate_predictions=snapshot.candidate_predictions,
        remaining_future_cutoffs=snapshot.remaining_future_cutoffs,
        candidate_root_healthy=snapshot.candidate_root_healthy
        and snapshot.candidate_ledger_healthy,
        cases_per_symbol_target=snapshot.cases_per_symbol_target,
    )
    readiness = evaluate_generation_readiness(coverage)
    reasons = list(readiness.reasons)
    remaining = sum(snapshot.remaining_future_cutoffs.values())
    if remaining and not snapshot.candidate_process_alive:
        reasons.append("candidate_process_not_alive_with_future_cutoffs")
    if remaining and not snapshot.candidate_model_loaded:
        reasons.append("candidate_model_not_loaded_with_future_cutoffs")
    if remaining and not snapshot.source_process_alive and not snapshot.source_terminal:
        reasons.append("source_process_not_alive_before_terminal_state")
    if not snapshot.source_health_valid and remaining:
        reasons.append("source_health_invalid_with_future_cutoffs")
    if snapshot.rejection_count:
        reasons.append("candidate_rejections_observed")
    if snapshot.consecutive_failures:
        reasons.append("candidate_consecutive_failures_observed")
    reasons = list(dict.fromkeys(reasons))
    impossible = readiness.status == "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION"
    if remaining and (not snapshot.candidate_process_alive or not snapshot.candidate_model_loaded):
        impossible = True
    if remaining and not snapshot.source_process_alive and not snapshot.source_terminal:
        impossible = True
    if impossible:
        status: Literal[
            "CANDIDATE_COVERAGE_POSSIBLE",
            "CANDIDATE_DEGRADED_BUT_RECOVERABLE",
            "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION",
        ] = "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION"
    elif reasons:
        status = "CANDIDATE_DEGRADED_BUT_RECOVERABLE"
    else:
        status = "CANDIDATE_COVERAGE_POSSIBLE"
    snapshot_payload = snapshot.model_dump(mode="json")
    unsigned = {
        "schema": WATCHDOG_SCHEMA,
        "status": status,
        "source_process_alive": snapshot.source_process_alive,
        "candidate_process_alive": snapshot.candidate_process_alive,
        "candidate_model_loaded": snapshot.candidate_model_loaded,
        "prediction_counts": snapshot.candidate_predictions,
        "rejection_count": snapshot.rejection_count,
        "consecutive_failures": snapshot.consecutive_failures,
        "reasons": reasons,
        "readiness": readiness.model_dump(mode="json"),
        "snapshot_hash": _hash_payload(snapshot_payload),
    }
    return GenerationWatchdogReport(
        **{key: value for key, value in unsigned.items() if key != "schema"},
        report_hash=_hash_payload(unsigned),
    )


__all__ = [
    "GenerationWatchdogReport",
    "GenerationWatchdogSnapshot",
    "WATCHDOG_SCHEMA",
    "evaluate_watchdog",
]
