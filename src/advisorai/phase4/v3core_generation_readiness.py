"""Offline readiness contracts for a fresh V3-Core Phase-4 generation.

This module is deliberately a launch refusal boundary, not an orchestrator.  It
reads a frozen, sanitized specification and reports whether a complete
non-baseline candidate path can start before the first eligible cutoff.  It has
no network, credential, model-loading, GPU-allocation, or execution operation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from advisorai.phase4.v3core_cadence import (
    V3_CORE_BASELINES,
    V3_CORE_MARKET_DATA_PROVIDER,
    V3_CORE_MARKET_DATA_REST_ENDPOINT,
    V3_CORE_MARKET_DATA_WS_ENDPOINT,
    V3_CORE_SYMBOLS,
)

READINESS_SCHEMA = "advisorai.phase4.v3-core-forward.generation-readiness.v1"
PREFLIGHT_SCHEMA = f"{READINESS_SCHEMA}.preflight"
EXPECTED_CANDIDATE_MODEL = "chronos-2-small"
EXPECTED_CONTEXT_BARS = 48
EXPECTED_OUTPUT_BARS = 30
EXPECTED_HORIZON_BARS = 12
EXPECTED_CASES_PER_SYMBOL = 64
EXPECTED_GPU_FAMILY_CAP = 1


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _is_sha256(value: str) -> bool:
    normalized = value.strip().lower()
    return len(normalized) == 64 and all(
        character in "0123456789abcdef" for character in normalized
    )


def _canonical_hash(payload: object) -> str:
    import json

    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class GenerationSourceContract(BaseModel):
    """Frozen source identity required before a new candidate can launch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_identity: str
    rest_endpoint: str
    websocket_endpoint: str
    collector_code_sha256: str
    preregistration_sha256: str
    phase3_gate_sha256: str
    source_snapshot_hash: str
    target_end_at: datetime
    first_eligible_cutoff: datetime
    cases_per_symbol_target: int = EXPECTED_CASES_PER_SYMBOL
    credentials_loaded: bool = False
    order_writes_attempted: bool = False
    credential_loader_configured: bool = False
    order_capability: bool = False

    @field_validator("target_end_at", "first_eligible_cutoff")
    @classmethod
    def aware_timestamp(cls, value: datetime, info: object) -> datetime:
        return _aware(value, getattr(info, "field_name", "timestamp"))


class GenerationCandidateContract(BaseModel):
    """Exact candidate identity and compatibility checks for a new run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    model_identity_sha256: str
    checkpoint_sha256: str
    qualification_evidence_sha256: str
    worker_code_sha256: str
    runner_sha256: str
    preprocessing_sha256: str
    runtime_environment_sha256: str
    context_bars: int = EXPECTED_CONTEXT_BARS
    output_bars: int = EXPECTED_OUTPUT_BARS
    horizon_bars: int = EXPECTED_HORIZON_BARS
    runtime_qualification_passed: bool = False
    input_context_compatible: bool = False
    output_horizon_compatible: bool = False
    prediction_schema_round_trip_passed: bool = False
    worker_identity_frozen: bool = False
    device: str = "cuda"


class GenerationResourceContract(BaseModel):
    """Resource safety assertions; the checker never acquires a lease."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    gpu_lease_available: bool = False
    resident_gpu_family_count: int = Field(default=0, ge=0)
    gpu_family_cap: int = Field(default=EXPECTED_GPU_FAMILY_CAP, ge=1)
    sidecar_available: bool = False
    collector_priority_preserved: bool = True
    memory_budget_measured: bool = False


class GenerationProspectiveContract(BaseModel):
    """Fresh-root and no-backfill assertions for prospective evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_started_at: datetime
    first_eligible_cutoff: datetime
    fresh_run_root: bool = False
    existing_completed_cases: int = Field(default=0, ge=0)
    historical_backfill_enabled: bool = False
    candidate_starts_before_first_cutoff: bool = False

    @field_validator("candidate_started_at", "first_eligible_cutoff")
    @classmethod
    def aware_timestamp(cls, value: datetime, info: object) -> datetime:
        return _aware(value, getattr(info, "field_name", "timestamp"))


class GenerationPreflightSpec(BaseModel):
    """Complete sanitized input to the offline launch preflight."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: str = Field(default=PREFLIGHT_SCHEMA, alias="schema")
    source: GenerationSourceContract
    candidate: GenerationCandidateContract
    resource: GenerationResourceContract
    prospective: GenerationProspectiveContract


class PreflightCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    passed: bool
    reason: str


class GenerationPreflightReport(BaseModel):
    """Immutable decision report; only READY_TO_LAUNCH permits handoff."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: str = Field(default=PREFLIGHT_SCHEMA, alias="schema")
    decision: Literal["READY_TO_LAUNCH", "REFUSE_LAUNCH"]
    candidate_required: bool = True
    candidate_model: str
    checks: tuple[PreflightCheck, ...]
    refusal_reasons: tuple[str, ...] = ()
    report_hash: str


class GenerationCoverageInput(BaseModel):
    """Read-only counts used to detect an impossible candidate generation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_completed_cases: dict[str, int]
    candidate_predictions: dict[str, int]
    remaining_future_cutoffs: dict[str, int]
    candidate_root_healthy: bool = False
    cases_per_symbol_target: int = EXPECTED_CASES_PER_SYMBOL
    candidate_model: str = EXPECTED_CANDIDATE_MODEL

    @field_validator("source_completed_cases", "candidate_predictions", "remaining_future_cutoffs")
    @classmethod
    def non_negative_counts(cls, value: dict[str, int]) -> dict[str, int]:
        normalized = {key.strip().upper(): count for key, count in value.items()}
        if set(normalized) != set(V3_CORE_SYMBOLS):
            raise ValueError("coverage counts must contain exactly BTCUSDT and ETHUSDT")
        if any(count < 0 for count in normalized.values()):
            raise ValueError("coverage counts cannot be negative")
        return normalized

    @field_validator("cases_per_symbol_target")
    @classmethod
    def positive_target(cls, value: int) -> int:
        if value != EXPECTED_CASES_PER_SYMBOL:
            raise ValueError("V3-Core readiness requires 64 cases per symbol")
        return value

    @field_validator("candidate_model")
    @classmethod
    def fixed_candidate(cls, value: str) -> str:
        if value != EXPECTED_CANDIDATE_MODEL:
            raise ValueError("the next V3-Core candidate is fixed to Chronos-2-small")
        return value


class GenerationReadinessReport(BaseModel):
    """Non-terminal feasibility report for an active or planned generation."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: str = Field(default=READINESS_SCHEMA, alias="schema")
    status: Literal[
        "CANDIDATE_COVERAGE_POSSIBLE",
        "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION",
    ]
    candidate_required: bool = True
    candidate_model: str
    expected_predictions_total: int
    candidate_prediction_counts: dict[str, int]
    source_target_counts: dict[str, int]
    remaining_future_cutoffs: dict[str, int]
    complete_coverage_possible: bool
    candidate_root_healthy: bool
    reasons: tuple[str, ...]
    report_hash: str


def _checks(spec: GenerationPreflightSpec) -> tuple[PreflightCheck, ...]:
    source = spec.source
    candidate = spec.candidate
    resource = spec.resource
    prospective = spec.prospective
    return (
        PreflightCheck(
            name="source_provider_identity",
            passed=source.provider_identity == V3_CORE_MARKET_DATA_PROVIDER,
            reason="the reviewed credential-free Binance public market-data identity is required",
        ),
        PreflightCheck(
            name="market_data_only_endpoints",
            passed=(
                source.rest_endpoint == V3_CORE_MARKET_DATA_REST_ENDPOINT
                and source.websocket_endpoint == V3_CORE_MARKET_DATA_WS_ENDPOINT
            ),
            reason="REST/WSS must be the exact reviewed market-data-only surfaces",
        ),
        PreflightCheck(
            name="source_write_and_credential_boundary",
            passed=(
                not source.credentials_loaded
                and not source.order_writes_attempted
                and not source.credential_loader_configured
                and not source.order_capability
            ),
            reason="source collection must have no credential or order capability",
        ),
        PreflightCheck(
            name="source_hash_identity",
            passed=all(
                _is_sha256(value)
                for value in (
                    source.collector_code_sha256,
                    source.preregistration_sha256,
                    source.phase3_gate_sha256,
                    source.source_snapshot_hash,
                )
            ),
            reason="collector, preregistration, Phase-3, and source snapshot hashes must be pinned",
        ),
        PreflightCheck(
            name="source_deadline",
            passed=source.target_end_at > source.first_eligible_cutoff,
            reason="the immutable target end must be after the first eligible cutoff",
        ),
        PreflightCheck(
            name="candidate_non_baseline",
            passed=candidate.model not in V3_CORE_BASELINES,
            reason="the current reviewer requires a non-baseline candidate",
        ),
        PreflightCheck(
            name="candidate_model_identity",
            passed=(
                candidate.model == EXPECTED_CANDIDATE_MODEL
                and all(
                    _is_sha256(value)
                    for value in (
                        candidate.model_identity_sha256,
                        candidate.checkpoint_sha256,
                        candidate.qualification_evidence_sha256,
                        candidate.worker_code_sha256,
                        candidate.runner_sha256,
                        candidate.preprocessing_sha256,
                        candidate.runtime_environment_sha256,
                    )
                )
                and candidate.worker_identity_frozen
            ),
            reason="the corrected Chronos identity must be exact and frozen before launch",
        ),
        PreflightCheck(
            name="candidate_v3core_compatibility",
            passed=(
                candidate.context_bars == EXPECTED_CONTEXT_BARS
                and candidate.output_bars == EXPECTED_OUTPUT_BARS
                and candidate.horizon_bars == EXPECTED_HORIZON_BARS
                and candidate.input_context_compatible
                and candidate.output_horizon_compatible
            ),
            reason="the candidate must implement 48 closed bars to 30 outputs with output 12 as 1h",
        ),
        PreflightCheck(
            name="candidate_schema_and_runtime",
            passed=(
                candidate.runtime_qualification_passed
                and candidate.prediction_schema_round_trip_passed
                and candidate.device.strip().lower() == "cuda"
            ),
            reason="offline runtime qualification and typed schema round-trip must pass",
        ),
        PreflightCheck(
            name="resource_safety",
            passed=(
                resource.gpu_lease_available
                and resource.resident_gpu_family_count < resource.gpu_family_cap
                and resource.gpu_family_cap == EXPECTED_GPU_FAMILY_CAP
                and resource.sidecar_available
                and resource.collector_priority_preserved
                and resource.memory_budget_measured
            ),
            reason="one GPU family, a healthy sidecar, measured memory, and collector priority are required",
        ),
        PreflightCheck(
            name="fresh_prospective_root",
            passed=(
                prospective.fresh_run_root
                and prospective.existing_completed_cases == 0
                and not prospective.historical_backfill_enabled
            ),
            reason="candidate evidence must start in an empty root with no backfill path",
        ),
        PreflightCheck(
            name="candidate_starts_before_first_cutoff",
            passed=(
                prospective.candidate_starts_before_first_cutoff
                and prospective.candidate_started_at <= prospective.first_eligible_cutoff
            ),
            reason="prospective candidate coverage must begin before the first eligible cutoff",
        ),
    )


def evaluate_preflight(spec: GenerationPreflightSpec) -> GenerationPreflightReport:
    """Evaluate a frozen launch specification without any external side effect."""

    if spec.schema_version != PREFLIGHT_SCHEMA:
        raise ValueError("unsupported Phase-4 generation preflight schema")
    checks = _checks(spec)
    refusal_reasons = tuple(check.name for check in checks if not check.passed)
    decision: Literal["READY_TO_LAUNCH", "REFUSE_LAUNCH"] = (
        "READY_TO_LAUNCH" if not refusal_reasons else "REFUSE_LAUNCH"
    )
    unsigned = {
        "schema": PREFLIGHT_SCHEMA,
        "decision": decision,
        "candidate_required": True,
        "candidate_model": spec.candidate.model,
        "checks": [check.model_dump(mode="json") for check in checks],
        "refusal_reasons": list(refusal_reasons),
    }
    return GenerationPreflightReport(
        schema_version=PREFLIGHT_SCHEMA,
        **{key: value for key, value in unsigned.items() if key != "schema"},
        report_hash=_canonical_hash(unsigned),
    )


def evaluate_generation_readiness(
    coverage: GenerationCoverageInput,
) -> GenerationReadinessReport:
    """Report whether a complete candidate set remains mathematically possible."""

    counts = coverage.candidate_predictions
    target = coverage.cases_per_symbol_target
    possible_by_symbol = {
        symbol: counts[symbol] + coverage.remaining_future_cutoffs[symbol] >= target
        for symbol in V3_CORE_SYMBOLS
    }
    complete = all(possible_by_symbol.values()) and coverage.candidate_root_healthy
    reasons: list[str] = []
    if not coverage.candidate_root_healthy:
        reasons.append("candidate_root_unhealthy")
    for symbol in V3_CORE_SYMBOLS:
        if not possible_by_symbol[symbol]:
            reasons.append(f"{symbol}_cannot_reach_{target}_candidate_predictions")
    status: Literal[
        "CANDIDATE_COVERAGE_POSSIBLE",
        "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION",
    ] = "CANDIDATE_COVERAGE_POSSIBLE" if complete else "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION"
    unsigned = {
        "schema": READINESS_SCHEMA,
        "status": status,
        "candidate_required": True,
        "candidate_model": coverage.candidate_model,
        "expected_predictions_total": target * len(V3_CORE_SYMBOLS),
        "candidate_prediction_counts": counts,
        "source_target_counts": {
            symbol: coverage.cases_per_symbol_target for symbol in V3_CORE_SYMBOLS
        },
        "remaining_future_cutoffs": coverage.remaining_future_cutoffs,
        "complete_coverage_possible": complete,
        "candidate_root_healthy": coverage.candidate_root_healthy,
        "reasons": reasons,
    }
    return GenerationReadinessReport(
        schema_version=READINESS_SCHEMA,
        **{key: value for key, value in unsigned.items() if key != "schema"},
        report_hash=_canonical_hash(unsigned),
    )


__all__ = [
    "EXPECTED_CANDIDATE_MODEL",
    "EXPECTED_CASES_PER_SYMBOL",
    "EXPECTED_CONTEXT_BARS",
    "EXPECTED_GPU_FAMILY_CAP",
    "EXPECTED_HORIZON_BARS",
    "EXPECTED_OUTPUT_BARS",
    "GenerationCandidateContract",
    "GenerationCoverageInput",
    "GenerationPreflightReport",
    "GenerationPreflightSpec",
    "GenerationProspectiveContract",
    "GenerationReadinessReport",
    "GenerationResourceContract",
    "GenerationSourceContract",
    "PREFLIGHT_SCHEMA",
    "READINESS_SCHEMA",
    "PreflightCheck",
    "evaluate_generation_readiness",
    "evaluate_preflight",
]
