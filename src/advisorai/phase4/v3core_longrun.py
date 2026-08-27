"""Release-candidate contracts for the V3-Core Phase-4 long run.

This module is intentionally an offline control-plane boundary.  It describes
the reviewed long-run contract, coverage feasibility, bounded recovery, and a
dry-run readiness report.  It does not create a preregistration, acquire a
lease, start a process, access a credential, call a venue, or write scientific
evidence.

The 80-opportunity target and 64-clean-case minimum are separate values.  The
target preserves an explicit operational buffer; the minimum is the scientific
admission threshold.  Existing readiness/watchdog evaluators remain the single
coverage decision authority and are called by this module rather than copied.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.phase4.v3core_cadence import (
    V3_CORE_MARKET_DATA_PROVIDER,
    V3_CORE_MARKET_DATA_REST_ENDPOINT,
    V3_CORE_MARKET_DATA_WS_ENDPOINT,
    V3_CORE_SYMBOLS,
)
from advisorai.phase4.v3core_generation_readiness import (
    EXPECTED_LONG_RUN_CASES_PER_SYMBOL,
    EXPECTED_MINIMUM_CASES_PER_SYMBOL,
    GenerationCoverageInput,
    evaluate_generation_readiness,
)

LONG_RUN_CONTRACT_SCHEMA = "advisorai.phase4.v3-core.long-run.release-candidate.v1"
LONG_RUN_READINESS_SCHEMA = f"{LONG_RUN_CONTRACT_SCHEMA}.readiness"
LONG_RUN_COVERAGE_SCHEMA = f"{LONG_RUN_CONTRACT_SCHEMA}.coverage"

LONG_RUN_TARGET_CASES_PER_SYMBOL = EXPECTED_LONG_RUN_CASES_PER_SYMBOL
LONG_RUN_MINIMUM_CLEAN_CASES_PER_SYMBOL = EXPECTED_MINIMUM_CASES_PER_SYMBOL
LONG_RUN_CONTEXT_BARS = 48
LONG_RUN_CONTEXT_LAG_SECONDS = 600
LONG_RUN_FINALITY_GUARD_SECONDS = 60
LONG_RUN_REPEAT_RECEIPTS = 2
LONG_RUN_OBSERVATION_INTERVAL_SECONDS = 300
LONG_RUN_PREDICTION_CADENCE_SECONDS = 3600
LONG_RUN_OUTCOME_HORIZON_SECONDS = 3600
LONG_RUN_TERMINAL_MARGIN_SECONDS = 3600

QUALIFIED_V3B_CANARY_ID = "20260824T160000Z-chronos-monitor-audit-v3b"
QUALIFIED_V3B_PREREGISTRATION_SHA256 = (
    "458754d53a74def22d33d2f462f31d3701c072f7ad4beb064aa0a63f10e98916"
)
QUALIFIED_V3B_TERMINAL_REPORT_SHA256 = (
    "183dd854f5eee2418c10a27b74daa0d86044367eae461ac2c1bfc30f79d239bd"
)
QUALIFIED_V3B_REPOSITORY_COMMIT = "de31ff1a46e8c57cc299f29a6463283f3ddf2931"
QUALIFIED_CHRONOS_MODEL = "autogluon/chronos-2-small"
QUALIFIED_CHRONOS_REVISION = "ddec01313e50b6bc58ebaa92ede81bc24a3d9f9a"
QUALIFIED_CHRONOS_CHECKPOINT_SHA256 = (
    "492290ae82bb89f9769e3479ce90b3179de1f33e600c34daa0352531538b23cd"
)
QUALIFIED_CHRONOS_MODEL_IDENTITY_SHA256 = (
    "c9287ccd27f52d93a71f24be2ad967d0bad6c79e7a4abd0c758e7448994b68ca"
)
QUALIFIED_UV_LOCK_SHA256 = "2baec5b4342fa9cef1c199ceb958621b320e8107a70abae26237d173b83ce0ac"
QUALIFIED_REQUIREMENTS_LOCK_SHA256 = (
    "260b47a47432d58c80ec1f850563782d8302ecf8748950c2b85200152e1dfaec"
)
QUALIFIED_PHASE3_GATE_SHA256 = "4e00850787cc6dcd95cadcd6152f74d4875bf480d219d07736706dd47a11d232"


class LongRunIncidentDisposition(StrEnum):
    """The only permitted scientific dispositions for a long-run incident."""

    CASE_EXCLUDED = "CASE_EXCLUDED"
    COMPONENT_RECOVERY_ALLOWED = "COMPONENT_RECOVERY_ALLOWED"
    GENERATION_FATAL = "GENERATION_FATAL"
    GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION = "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION"


class LongRunIncidentType(StrEnum):
    """Typed incident classes used by the bounded recovery policy."""

    UNRESOLVED_REQUIRED_CONTEXT = "UNRESOLVED_REQUIRED_CONTEXT"
    MISSED_MANDATORY_CUTOFF = "MISSED_MANDATORY_CUTOFF"
    CANDIDATE_REJECTION = "CANDIDATE_REJECTION"
    RECOVERABLE_SOURCE_OUTAGE = "RECOVERABLE_SOURCE_OUTAGE"
    RECOVERABLE_CANDIDATE_OUTAGE = "RECOVERABLE_CANDIDATE_OUTAGE"
    SOURCE_PROCESS_DEATH = "SOURCE_PROCESS_DEATH"
    CANDIDATE_PROCESS_DEATH = "CANDIDATE_PROCESS_DEATH"
    WATCHDOG_FAILURE = "WATCHDOG_FAILURE"
    POST_ADMISSION_REVISION = "POST_ADMISSION_REVISION"
    RAW_HASH_CHAIN_FAILURE = "RAW_HASH_CHAIN_FAILURE"
    ADMITTED_IDENTITY_FAILURE = "ADMITTED_IDENTITY_FAILURE"
    PREDICTION_LEDGER_CORRUPTION = "PREDICTION_LEDGER_CORRUPTION"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    RUNTIME_LOCK_DRIFT = "RUNTIME_LOCK_DRIFT"
    FUTURE_LEAKAGE = "FUTURE_LEAKAGE"
    CREDENTIAL_DETECTED = "CREDENTIAL_DETECTED"
    ORDER_WRITE_DETECTED = "ORDER_WRITE_DETECTED"
    UNRECOVERABLE_CUDA_FAILURE = "UNRECOVERABLE_CUDA_FAILURE"
    MODEL_FAILURE = "MODEL_FAILURE"


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash_payload(payload: object) -> str:
    return sha256(_canonical(payload)).hexdigest()


def _is_digest(value: str) -> bool:
    normalized = value.strip().lower()
    return len(normalized) == 64 and all(
        character in "0123456789abcdef" for character in normalized
    )


def _is_commit(value: str) -> bool:
    normalized = value.strip().lower()
    return len(normalized) == 40 and all(
        character in "0123456789abcdef" for character in normalized
    )


def _digest(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if not _is_digest(normalized):
        raise ValueError(f"{field_name} must be a SHA-256 digest")
    return normalized


def _commit(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if not _is_commit(normalized):
        raise ValueError(f"{field_name} must be a Git commit identity")
    return normalized


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _symbols(value: Mapping[str, int], field_name: str) -> dict[str, int]:
    normalized = {key.strip().upper(): count for key, count in value.items()}
    if set(normalized) != set(V3_CORE_SYMBOLS):
        raise ValueError(f"{field_name} must contain exactly BTCUSDT and ETHUSDT")
    if any(not isinstance(count, int) or count < 0 for count in normalized.values()):
        raise ValueError(f"{field_name} values must be non-negative integers")
    return normalized


class LongRunScientificContract(BaseModel):
    """The fixed scientific contract proposed for the eventual long run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[LONG_RUN_CONTRACT_SCHEMA] = LONG_RUN_CONTRACT_SCHEMA
    symbols: tuple[str, ...] = V3_CORE_SYMBOLS
    market_data_provider: str = V3_CORE_MARKET_DATA_PROVIDER
    rest_endpoint: str = V3_CORE_MARKET_DATA_REST_ENDPOINT
    websocket_endpoint: str = V3_CORE_MARKET_DATA_WS_ENDPOINT
    observation_interval_seconds: int = LONG_RUN_OBSERVATION_INTERVAL_SECONDS
    prediction_cadence_seconds: int = LONG_RUN_PREDICTION_CADENCE_SECONDS
    outcome_horizon_seconds: int = LONG_RUN_OUTCOME_HORIZON_SECONDS
    context_bars: int = LONG_RUN_CONTEXT_BARS
    context_newest_lag_seconds: int = LONG_RUN_CONTEXT_LAG_SECONDS
    finality_guard_seconds: int = LONG_RUN_FINALITY_GUARD_SECONDS
    repeat_requirement: int = LONG_RUN_REPEAT_RECEIPTS
    distinct_receipts_required: bool = True
    raw_receipts_append_only: bool = True
    admitted_final_immutable: bool = True
    post_admission_revision_is_fatal: bool = True
    no_padding: bool = True
    no_interpolation: bool = True
    no_fallback: bool = True
    no_backfill: bool = True
    fresh_run_only: bool = True
    target_cases_per_symbol: int = LONG_RUN_TARGET_CASES_PER_SYMBOL
    minimum_clean_cases_per_symbol: int = LONG_RUN_MINIMUM_CLEAN_CASES_PER_SYMBOL

    @field_validator("symbols")
    @classmethod
    def fixed_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().upper() for item in value)
        if normalized != V3_CORE_SYMBOLS:
            raise ValueError("long-run universe must remain BTCUSDT and ETHUSDT")
        return normalized

    @model_validator(mode="after")
    def validate_fixed_contract(self) -> LongRunScientificContract:
        if self.schema != LONG_RUN_CONTRACT_SCHEMA:
            raise ValueError("unsupported long-run contract schema")
        if self.market_data_provider != V3_CORE_MARKET_DATA_PROVIDER:
            raise ValueError("long run requires the reviewed Binance public data provider")
        if self.rest_endpoint != V3_CORE_MARKET_DATA_REST_ENDPOINT:
            raise ValueError("long run requires the reviewed public klines endpoint")
        if self.websocket_endpoint != V3_CORE_MARKET_DATA_WS_ENDPOINT:
            raise ValueError("long run requires the reviewed public market-data stream")
        expected = (
            self.observation_interval_seconds == LONG_RUN_OBSERVATION_INTERVAL_SECONDS
            and self.prediction_cadence_seconds == LONG_RUN_PREDICTION_CADENCE_SECONDS
            and self.outcome_horizon_seconds == LONG_RUN_OUTCOME_HORIZON_SECONDS
            and self.context_bars == LONG_RUN_CONTEXT_BARS
            and self.context_newest_lag_seconds == LONG_RUN_CONTEXT_LAG_SECONDS
            and self.finality_guard_seconds == LONG_RUN_FINALITY_GUARD_SECONDS
            and self.repeat_requirement == LONG_RUN_REPEAT_RECEIPTS
            and self.distinct_receipts_required
        )
        if not expected:
            raise ValueError("long-run cadence, context, and finality values are frozen")
        if self.target_cases_per_symbol != LONG_RUN_TARGET_CASES_PER_SYMBOL:
            raise ValueError("release candidate target is fixed at 80 opportunities per symbol")
        if self.minimum_clean_cases_per_symbol != LONG_RUN_MINIMUM_CLEAN_CASES_PER_SYMBOL:
            raise ValueError("release candidate clean minimum is fixed at 64 per symbol")
        if self.target_cases_per_symbol < self.minimum_clean_cases_per_symbol:
            raise ValueError("target cannot be below the clean minimum")
        if not all(
            (
                self.raw_receipts_append_only,
                self.admitted_final_immutable,
                self.post_admission_revision_is_fatal,
                self.no_padding,
                self.no_interpolation,
                self.no_fallback,
                self.no_backfill,
                self.fresh_run_only,
            )
        ):
            raise ValueError("long-run evidence must remain append-only and prospective")
        return self


class LongRunCodeIdentity(BaseModel):
    """All source/runtime identities that must be frozen at launch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_commit: str
    collector_code_sha256: str
    finality_code_sha256: str
    chronos_worker_code_sha256: str
    chronos_runner_sha256: str
    watchdog_code_sha256: str
    terminal_auditor_code_sha256: str
    scheduler_code_sha256: str
    preprocessing_sha256: str
    checkpoint_sha256: str
    model_identity_sha256: str
    model: str = QUALIFIED_CHRONOS_MODEL
    model_revision: str = QUALIFIED_CHRONOS_REVISION
    uv_lock_sha256: str
    requirements_lock_sha256: str
    phase3_gate_sha256: str = QUALIFIED_PHASE3_GATE_SHA256
    source_snapshot_sha256: str | None = None
    qualification_preregistration_sha256: str = QUALIFIED_V3B_PREREGISTRATION_SHA256
    qualification_terminal_report_sha256: str = QUALIFIED_V3B_TERMINAL_REPORT_SHA256

    @field_validator("repository_commit")
    @classmethod
    def valid_repository_commit(cls, value: str) -> str:
        return _commit(value, "repository_commit")

    @field_validator(
        "collector_code_sha256",
        "finality_code_sha256",
        "chronos_worker_code_sha256",
        "chronos_runner_sha256",
        "watchdog_code_sha256",
        "terminal_auditor_code_sha256",
        "scheduler_code_sha256",
        "preprocessing_sha256",
        "checkpoint_sha256",
        "model_identity_sha256",
        "uv_lock_sha256",
        "requirements_lock_sha256",
        "phase3_gate_sha256",
        "source_snapshot_sha256",
        "qualification_preregistration_sha256",
        "qualification_terminal_report_sha256",
    )
    @classmethod
    def valid_hash(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _digest(value, getattr(info, "field_name", "identity"))

    @field_validator("model_revision")
    @classmethod
    def valid_model_revision(cls, value: str) -> str:
        if value != QUALIFIED_CHRONOS_REVISION:
            raise ValueError("the qualified Chronos revision is frozen")
        return value

    @field_validator("model")
    @classmethod
    def valid_model(cls, value: str) -> str:
        if value != QUALIFIED_CHRONOS_MODEL:
            raise ValueError("the qualified Chronos model is frozen")
        return value

    @model_validator(mode="after")
    def validate_lock_identity(self) -> LongRunCodeIdentity:
        if self.phase3_gate_sha256 != QUALIFIED_PHASE3_GATE_SHA256:
            raise ValueError("Phase-3 gate identity does not match the qualified V3B predecessor")
        if self.uv_lock_sha256 != QUALIFIED_UV_LOCK_SHA256:
            raise ValueError("repository uv.lock identity does not match the qualified V3B runtime")
        if self.requirements_lock_sha256 != QUALIFIED_REQUIREMENTS_LOCK_SHA256:
            raise ValueError("qualified requirements.lock identity does not match V3B")
        if self.model_identity_sha256 != QUALIFIED_CHRONOS_MODEL_IDENTITY_SHA256:
            raise ValueError("qualified Chronos model identity does not match V3B")
        return self


class QualifiedCanaryReference(BaseModel):
    """Reference to the qualified canary without importing its runtime evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    canary_id: str = QUALIFIED_V3B_CANARY_ID
    canary_qualified: Literal[True] = True
    evidence_class: Literal["PROSPECTIVE_CANARY_ONLY"] = "PROSPECTIVE_CANARY_ONLY"
    admission_eligible: Literal[False] = False
    preregistration_sha256: str = QUALIFIED_V3B_PREREGISTRATION_SHA256
    terminal_report_sha256: str = QUALIFIED_V3B_TERMINAL_REPORT_SHA256
    repository_commit: str = QUALIFIED_V3B_REPOSITORY_COMMIT
    model: Literal[QUALIFIED_CHRONOS_MODEL] = QUALIFIED_CHRONOS_MODEL
    model_revision: Literal[QUALIFIED_CHRONOS_REVISION] = QUALIFIED_CHRONOS_REVISION
    checkpoint_sha256: str = QUALIFIED_CHRONOS_CHECKPOINT_SHA256

    @field_validator("preregistration_sha256", "terminal_report_sha256", "checkpoint_sha256")
    @classmethod
    def valid_hash(cls, value: str, info: object) -> str:
        return _digest(value, getattr(info, "field_name", "hash"))

    @field_validator("repository_commit")
    @classmethod
    def valid_commit(cls, value: str) -> str:
        return _commit(value, "repository_commit")


class LongRunVerification(BaseModel):
    """Results of the offline qualification suite used by release preflight."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    finality_tests_passed: bool = False
    candidate_contract_tests_passed: bool = False
    warmup_tests_passed: bool = False
    watchdog_tests_passed: bool = False
    restart_resume_tests_passed: bool = False
    terminal_auditor_tests_passed: bool = False
    readiness_fingerprint_tests_passed: bool = False
    target_accounting_tests_passed: bool = False
    scheduler_tests_passed: bool = False
    artifact_backed_tests_passed: bool = False
    cuda_acceptance_passed: bool = False
    terminal_workflow_dry_run_passed: bool = False
    no_dependency_changes: bool = False

    @property
    def all_required_passed(self) -> bool:
        return all(self.model_dump().values())


class LongRunResourceState(BaseModel):
    """Sanitized resource/security state; no method acquires resources."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    gpu_lease_free: bool = False
    resident_advisorai_gpu_workers: int = Field(default=0, ge=0)
    gpu_family_cap: int = Field(default=1, ge=1)
    credentials_loaded: bool = False
    order_writes_attempted: bool = False
    execution_authority_present: bool = False
    evidence_root_empty: bool = True
    immutable_preregistration_created: bool = False


class LongRunReleaseCandidateSpec(BaseModel):
    """Complete sanitized input to the release-candidate dry-run preflight."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[LONG_RUN_CONTRACT_SCHEMA] = LONG_RUN_CONTRACT_SCHEMA
    scientific: LongRunScientificContract
    identities: LongRunCodeIdentity
    qualified_canary: QualifiedCanaryReference = QualifiedCanaryReference()
    verification: LongRunVerification
    resources: LongRunResourceState
    planned_start_at: datetime | None = None
    preregistration_sha256: str | None = None
    release_candidate_dry_run: Literal[True] = True

    @field_validator("planned_start_at")
    @classmethod
    def aware_start(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value, "planned_start_at")

    @field_validator("preregistration_sha256")
    @classmethod
    def valid_optional_preregistration_hash(cls, value: str | None) -> str | None:
        return None if value is None else _digest(value, "preregistration_sha256")

    @model_validator(mode="after")
    def validate_draft_boundary(self) -> LongRunReleaseCandidateSpec:
        if self.schema != LONG_RUN_CONTRACT_SCHEMA:
            raise ValueError("unsupported long-run release-candidate schema")
        if self.preregistration_sha256 is not None:
            raise ValueError("immutable long-run preregistration must not exist in this draft")
        if self.planned_start_at is not None:
            raise ValueError("the release-candidate task must not choose a launch start")
        if self.qualified_canary.canary_id != QUALIFIED_V3B_CANARY_ID:
            raise ValueError("release candidate must reference the qualified V3B canary")
        if self.qualified_canary.repository_commit != QUALIFIED_V3B_REPOSITORY_COMMIT:
            raise ValueError("qualified canary repository identity is incorrect")
        if not self.resources.evidence_root_empty:
            raise ValueError("release-candidate preflight must not reuse an evidence root")
        return self


class ReleaseCandidateCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    passed: bool
    reason: str


class ReleaseCandidateReadinessReport(BaseModel):
    """Fingerprint-protected, non-launching readiness result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[LONG_RUN_READINESS_SCHEMA] = LONG_RUN_READINESS_SCHEMA
    decision: Literal[
        "RELEASE_CANDIDATE_READY_FOR_HUMAN_REVIEW",
        "RELEASE_CANDIDATE_REFUSED",
    ]
    checks: tuple[ReleaseCandidateCheck, ...]
    refusal_reasons: tuple[str, ...] = ()
    long_run_ready: Literal[False] = False
    input_fingerprint_sha256: str
    report_hash: str

    @field_validator("input_fingerprint_sha256", "report_hash")
    @classmethod
    def valid_report_hash(cls, value: str, info: object) -> str:
        return _digest(value, getattr(info, "field_name", "hash"))

    @model_validator(mode="after")
    def validate_fingerprint(self) -> ReleaseCandidateReadinessReport:
        payload = self.model_dump(mode="json", exclude={"report_hash"})
        if _hash_payload(payload) != self.report_hash:
            raise ValueError("release-candidate readiness fingerprint is inconsistent")
        return self


class LongRunCoverageSnapshot(BaseModel):
    """Per-symbol target/minimum accounting for an active future run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[LONG_RUN_COVERAGE_SCHEMA] = LONG_RUN_COVERAGE_SCHEMA
    clean_cases: dict[str, int]
    excluded_cases: dict[str, int]
    remaining_opportunities: dict[str, int]
    target_cases_per_symbol: int = LONG_RUN_TARGET_CASES_PER_SYMBOL
    minimum_clean_cases_per_symbol: int = LONG_RUN_MINIMUM_CLEAN_CASES_PER_SYMBOL

    @field_validator("clean_cases", "excluded_cases", "remaining_opportunities")
    @classmethod
    def valid_counts(cls, value: dict[str, int], info: object) -> dict[str, int]:
        return _symbols(value, getattr(info, "field_name", "counts"))

    @model_validator(mode="after")
    def validate_accounting(self) -> LongRunCoverageSnapshot:
        if self.target_cases_per_symbol != LONG_RUN_TARGET_CASES_PER_SYMBOL:
            raise ValueError("long-run target must remain 80 opportunities per symbol")
        if self.minimum_clean_cases_per_symbol != LONG_RUN_MINIMUM_CLEAN_CASES_PER_SYMBOL:
            raise ValueError("long-run clean minimum must remain 64 per symbol")
        for symbol in V3_CORE_SYMBOLS:
            accounted = (
                self.clean_cases[symbol]
                + self.excluded_cases[symbol]
                + self.remaining_opportunities[symbol]
            )
            if accounted != self.target_cases_per_symbol:
                raise ValueError(f"{symbol} accounting must equal the 80-opportunity target")
        return self


class LongRunCoverageReport(BaseModel):
    """Fingerprint-protected feasibility result for target/minimum accounting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[LONG_RUN_COVERAGE_SCHEMA] = LONG_RUN_COVERAGE_SCHEMA
    status: Literal[
        "COVERAGE_POSSIBLE",
        "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION",
    ]
    clean_cases: dict[str, int]
    excluded_cases: dict[str, int]
    remaining_opportunities: dict[str, int]
    target_cases_per_symbol: int
    minimum_clean_cases_per_symbol: int
    reasons: tuple[str, ...]
    report_hash: str

    @field_validator("report_hash")
    @classmethod
    def valid_report_hash(cls, value: str) -> str:
        return _digest(value, "report_hash")

    @model_validator(mode="after")
    def validate_fingerprint(self) -> LongRunCoverageReport:
        payload = self.model_dump(mode="json", exclude={"report_hash"})
        if _hash_payload(payload) != self.report_hash:
            raise ValueError("coverage report fingerprint is inconsistent")
        return self


class RecoveryPolicy(BaseModel):
    """Bounded, identity-preserving recovery policy; it never restarts a process."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_automatic_recovery_attempts_per_component_incident: int = 1
    no_backfill: bool = True
    no_deadline_extension: bool = True
    no_rule_relaxation: bool = True

    @model_validator(mode="after")
    def validate_policy(self) -> RecoveryPolicy:
        if self.max_automatic_recovery_attempts_per_component_incident != 1:
            raise ValueError("long-run recovery is limited to one automatic attempt")
        if not self.no_backfill or not self.no_deadline_extension or not self.no_rule_relaxation:
            raise ValueError("recovery cannot backfill, extend, or relax the run")
        return self


class ComponentRecoveryVerification(BaseModel):
    """Evidence required before a separately reviewed component resume."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    same_repository_identity: bool = False
    same_code_identity: bool = False
    same_model_checkpoint_identity: bool = False
    same_runtime_lock_identity: bool = False
    append_only_state_valid: bool = False
    resume_identity_valid: bool = False
    no_conflicting_duplicates: bool = False
    no_evidence_rewrite: bool = False
    watchdog_recorded_interruption: bool = False
    scientific_continuity_valid: bool = False
    credentials_loaded: bool = False
    order_writes_attempted: bool = False

    @property
    def all_required_passed(self) -> bool:
        return all(
            (
                self.same_repository_identity,
                self.same_code_identity,
                self.same_model_checkpoint_identity,
                self.same_runtime_lock_identity,
                self.append_only_state_valid,
                self.resume_identity_valid,
                self.no_conflicting_duplicates,
                self.no_evidence_rewrite,
                self.watchdog_recorded_interruption,
                self.scientific_continuity_valid,
                not self.credentials_loaded,
                not self.order_writes_attempted,
            )
        )


_CASE_LEVEL_INCIDENTS = frozenset(
    {
        LongRunIncidentType.UNRESOLVED_REQUIRED_CONTEXT,
        LongRunIncidentType.MISSED_MANDATORY_CUTOFF,
        LongRunIncidentType.CANDIDATE_REJECTION,
        LongRunIncidentType.RECOVERABLE_SOURCE_OUTAGE,
        LongRunIncidentType.RECOVERABLE_CANDIDATE_OUTAGE,
    }
)
_RECOVERABLE_COMPONENT_INCIDENTS = frozenset(
    {
        LongRunIncidentType.SOURCE_PROCESS_DEATH,
        LongRunIncidentType.CANDIDATE_PROCESS_DEATH,
        LongRunIncidentType.WATCHDOG_FAILURE,
    }
)
_GENERATION_FATAL_INCIDENTS = frozenset(LongRunIncidentType) - (
    _CASE_LEVEL_INCIDENTS | _RECOVERABLE_COMPONENT_INCIDENTS
)


def classify_long_run_incident(
    incident: LongRunIncidentType,
    *,
    clean_minimum_still_attainable: bool,
    recovery_verified: bool = False,
) -> LongRunIncidentDisposition:
    """Classify an incident without taking a recovery or process action."""

    incident = LongRunIncidentType(incident)
    if incident in _GENERATION_FATAL_INCIDENTS:
        return LongRunIncidentDisposition.GENERATION_FATAL
    if incident in _RECOVERABLE_COMPONENT_INCIDENTS:
        return (
            LongRunIncidentDisposition.COMPONENT_RECOVERY_ALLOWED
            if recovery_verified
            else LongRunIncidentDisposition.GENERATION_FATAL
        )
    if not clean_minimum_still_attainable:
        return LongRunIncidentDisposition.GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION
    return LongRunIncidentDisposition.CASE_EXCLUDED


def recovery_is_allowed(
    verification: ComponentRecoveryVerification,
    *,
    attempt_number: int,
    policy: RecoveryPolicy | None = None,
) -> bool:
    """Return whether a bounded resume would be admissible for review."""

    active_policy = policy or RecoveryPolicy()
    return (
        1 <= attempt_number <= active_policy.max_automatic_recovery_attempts_per_component_incident
        and verification.all_required_passed
    )


def derive_first_long_run_cutoff(
    start_at: datetime,
    *,
    context_bars: int = LONG_RUN_CONTEXT_BARS,
    interval_seconds: int = LONG_RUN_OBSERVATION_INTERVAL_SECONDS,
    context_lag_seconds: int = LONG_RUN_CONTEXT_LAG_SECONDS,
) -> datetime:
    """Derive the first top-of-hour cutoff from fresh-run source geometry.

    With no qualified warm-start snapshot, the first required closed interval
    is the first interval ending at or after ``start_at``.  The first cutoff
    is the earliest UTC hour strictly after the last required context close plus
    the fixed context lag.  This is the same causal arithmetic as the reviewed
    canary contract, expressed for the 80-case long-run planner.
    """

    normalized_start = _aware(start_at, "start_at")
    if normalized_start.minute or normalized_start.second or normalized_start.microsecond:
        raise ValueError("fresh-run start must be aligned to a UTC hour")
    if context_bars < 1 or interval_seconds <= 0 or context_lag_seconds < interval_seconds:
        raise ValueError("long-run timing parameters are invalid")
    latest_required_bar = normalized_start + timedelta(
        seconds=(context_bars - 1) * interval_seconds
    )
    earliest_cutoff = latest_required_bar + timedelta(seconds=context_lag_seconds)
    cutoff = earliest_cutoff.replace(minute=0, second=0, microsecond=0)
    if cutoff < earliest_cutoff:
        cutoff += timedelta(hours=1)
    return cutoff


def derive_long_run_cutoffs(
    start_at: datetime,
    *,
    count: int = LONG_RUN_TARGET_CASES_PER_SYMBOL,
) -> tuple[datetime, ...]:
    """Return the fixed hourly opportunity schedule for planning."""

    if count < 1:
        raise ValueError("long-run opportunity count must be positive")
    first = derive_first_long_run_cutoff(start_at)
    return tuple(first + timedelta(hours=index) for index in range(count))


def estimate_long_run_terminal_deadline(
    start_at: datetime,
    *,
    target_cases_per_symbol: int = LONG_RUN_TARGET_CASES_PER_SYMBOL,
    outcome_horizon_seconds: int = LONG_RUN_OUTCOME_HORIZON_SECONDS,
    terminal_margin_seconds: int = LONG_RUN_TERMINAL_MARGIN_SECONDS,
) -> datetime:
    """Estimate a planning deadline; no deadline is created or launched."""

    normalized_start = _aware(start_at, "start_at")
    if target_cases_per_symbol != LONG_RUN_TARGET_CASES_PER_SYMBOL:
        raise ValueError("release-candidate target is fixed at 80 opportunities per symbol")
    if outcome_horizon_seconds != LONG_RUN_OUTCOME_HORIZON_SECONDS:
        raise ValueError("one-hour outcome maturity is fixed")
    if terminal_margin_seconds < 0:
        raise ValueError("terminal margin cannot be negative")
    first = derive_first_long_run_cutoff(normalized_start)
    last = first + timedelta(hours=target_cases_per_symbol - 1)
    return last + timedelta(seconds=outcome_horizon_seconds + terminal_margin_seconds)


def evaluate_long_run_coverage(snapshot: LongRunCoverageSnapshot) -> LongRunCoverageReport:
    """Evaluate clean-case feasibility through the shared readiness evaluator."""

    source_completed = {
        symbol: snapshot.clean_cases[symbol] + snapshot.excluded_cases[symbol]
        for symbol in V3_CORE_SYMBOLS
    }
    readiness = evaluate_generation_readiness(
        GenerationCoverageInput(
            source_completed_cases=source_completed,
            candidate_predictions=snapshot.clean_cases,
            remaining_future_cutoffs=snapshot.remaining_opportunities,
            candidate_root_healthy=True,
            cases_per_symbol_target=snapshot.target_cases_per_symbol,
            minimum_clean_cases_per_symbol=snapshot.minimum_clean_cases_per_symbol,
        )
    )
    reasons = tuple(readiness.reasons)
    status: Literal[
        "COVERAGE_POSSIBLE",
        "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION",
    ] = (
        "COVERAGE_POSSIBLE"
        if readiness.complete_coverage_possible
        else "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION"
    )
    unsigned = {
        "schema": LONG_RUN_COVERAGE_SCHEMA,
        "status": status,
        "clean_cases": snapshot.clean_cases,
        "excluded_cases": snapshot.excluded_cases,
        "remaining_opportunities": snapshot.remaining_opportunities,
        "target_cases_per_symbol": snapshot.target_cases_per_symbol,
        "minimum_clean_cases_per_symbol": snapshot.minimum_clean_cases_per_symbol,
        "reasons": reasons,
    }
    return LongRunCoverageReport(
        **{key: value for key, value in unsigned.items() if key != "schema"},
        report_hash=_hash_payload(unsigned),
    )


def evaluate_release_candidate(
    spec: LongRunReleaseCandidateSpec,
) -> ReleaseCandidateReadinessReport:
    """Run the complete no-launch release-candidate dry-run preflight."""

    identity = spec.identities
    canary = spec.qualified_canary
    scientific = spec.scientific
    resources = spec.resources
    verification = spec.verification
    checks = (
        ReleaseCandidateCheck(
            name="qualified_canary_reference",
            passed=(
                canary.canary_qualified
                and canary.evidence_class == "PROSPECTIVE_CANARY_ONLY"
                and not canary.admission_eligible
                and canary.preregistration_sha256 == identity.qualification_preregistration_sha256
                and canary.terminal_report_sha256 == identity.qualification_terminal_report_sha256
                and canary.repository_commit == QUALIFIED_V3B_REPOSITORY_COMMIT
                and canary.model == identity.model
                and canary.model_revision == identity.model_revision
                and canary.checkpoint_sha256 == identity.checkpoint_sha256
            ),
            reason="V3B qualification is evidence-only and must remain below Phase-4 admission",
        ),
        ReleaseCandidateCheck(
            name="scientific_contract",
            passed=(
                scientific.target_cases_per_symbol == LONG_RUN_TARGET_CASES_PER_SYMBOL
                and scientific.minimum_clean_cases_per_symbol
                == LONG_RUN_MINIMUM_CLEAN_CASES_PER_SYMBOL
                and scientific.context_bars == LONG_RUN_CONTEXT_BARS
                and scientific.context_newest_lag_seconds == LONG_RUN_CONTEXT_LAG_SECONDS
                and scientific.finality_guard_seconds == LONG_RUN_FINALITY_GUARD_SECONDS
                and scientific.repeat_requirement == LONG_RUN_REPEAT_RECEIPTS
                and scientific.no_backfill
                and scientific.fresh_run_only
            ),
            reason="80 scheduled opportunities, 64 clean minimum, and the V3B scientific rules are frozen",
        ),
        ReleaseCandidateCheck(
            name="dual_runtime_lock_identities",
            passed=(
                _is_digest(identity.uv_lock_sha256)
                and identity.requirements_lock_sha256 == QUALIFIED_REQUIREMENTS_LOCK_SHA256
            ),
            reason="repository uv.lock and separately qualified requirements.lock must both be pinned",
        ),
        ReleaseCandidateCheck(
            name="code_model_and_checkpoint_identities",
            passed=(
                _is_commit(identity.repository_commit)
                and identity.model_revision == QUALIFIED_CHRONOS_REVISION
                and identity.checkpoint_sha256 == QUALIFIED_CHRONOS_CHECKPOINT_SHA256
                and all(
                    _is_digest(value)
                    for value in identity.model_dump().values()
                    if isinstance(value, str)
                    and value
                    not in {identity.repository_commit, identity.model, identity.model_revision}
                )
            ),
            reason="all collector, finality, candidate, watchdog, auditor, scheduler, model, and source identities are pinned",
        ),
        ReleaseCandidateCheck(
            name="offline_verification_suite",
            passed=verification.all_required_passed,
            reason="all focused, artifact-backed, runtime, scheduler, and dry-run checks must pass",
        ),
        ReleaseCandidateCheck(
            name="resource_and_security_boundary",
            passed=(
                resources.gpu_lease_free
                and resources.resident_advisorai_gpu_workers < resources.gpu_family_cap
                and resources.gpu_family_cap == 1
                and not resources.credentials_loaded
                and not resources.order_writes_attempted
                and not resources.execution_authority_present
            ),
            reason="the release candidate must not hold a GPU lease or execution capability",
        ),
        ReleaseCandidateCheck(
            name="no_runtime_artifacts_or_preregistration",
            passed=(
                resources.evidence_root_empty and not resources.immutable_preregistration_created
            ),
            reason="planning must not create or reuse long-run scientific evidence",
        ),
    )
    refusal_reasons = tuple(check.name for check in checks if not check.passed)
    decision: Literal[
        "RELEASE_CANDIDATE_READY_FOR_HUMAN_REVIEW",
        "RELEASE_CANDIDATE_REFUSED",
    ] = (
        "RELEASE_CANDIDATE_READY_FOR_HUMAN_REVIEW"
        if not refusal_reasons
        else "RELEASE_CANDIDATE_REFUSED"
    )
    unsigned = {
        "schema": LONG_RUN_READINESS_SCHEMA,
        "decision": decision,
        "checks": [check.model_dump(mode="json") for check in checks],
        "refusal_reasons": list(refusal_reasons),
        "long_run_ready": False,
        "input_fingerprint_sha256": _hash_payload(spec.model_dump(mode="json")),
    }
    return ReleaseCandidateReadinessReport(
        **{key: value for key, value in unsigned.items() if key != "schema"},
        report_hash=_hash_payload(unsigned),
    )


__all__ = [
    "ComponentRecoveryVerification",
    "LONG_RUN_CONTEXT_BARS",
    "LONG_RUN_CONTEXT_LAG_SECONDS",
    "LONG_RUN_CONTRACT_SCHEMA",
    "LONG_RUN_COVERAGE_SCHEMA",
    "LONG_RUN_FINALITY_GUARD_SECONDS",
    "LONG_RUN_MINIMUM_CLEAN_CASES_PER_SYMBOL",
    "LONG_RUN_OBSERVATION_INTERVAL_SECONDS",
    "LONG_RUN_OUTCOME_HORIZON_SECONDS",
    "LONG_RUN_PREDICTION_CADENCE_SECONDS",
    "LONG_RUN_READINESS_SCHEMA",
    "LONG_RUN_REPEAT_RECEIPTS",
    "LONG_RUN_TARGET_CASES_PER_SYMBOL",
    "LONG_RUN_TERMINAL_MARGIN_SECONDS",
    "LongRunCodeIdentity",
    "LongRunCoverageReport",
    "LongRunCoverageSnapshot",
    "LongRunIncidentDisposition",
    "LongRunIncidentType",
    "LongRunReleaseCandidateSpec",
    "LongRunResourceState",
    "LongRunScientificContract",
    "LongRunVerification",
    "QualifiedCanaryReference",
    "RecoveryPolicy",
    "ReleaseCandidateCheck",
    "ReleaseCandidateReadinessReport",
    "QUALIFIED_CHRONOS_CHECKPOINT_SHA256",
    "QUALIFIED_CHRONOS_MODEL",
    "QUALIFIED_CHRONOS_MODEL_IDENTITY_SHA256",
    "QUALIFIED_PHASE3_GATE_SHA256",
    "QUALIFIED_CHRONOS_REVISION",
    "QUALIFIED_REQUIREMENTS_LOCK_SHA256",
    "QUALIFIED_UV_LOCK_SHA256",
    "QUALIFIED_V3B_CANARY_ID",
    "QUALIFIED_V3B_PREREGISTRATION_SHA256",
    "QUALIFIED_V3B_REPOSITORY_COMMIT",
    "QUALIFIED_V3B_TERMINAL_REPORT_SHA256",
    "classify_long_run_incident",
    "derive_first_long_run_cutoff",
    "derive_long_run_cutoffs",
    "estimate_long_run_terminal_deadline",
    "evaluate_long_run_coverage",
    "evaluate_release_candidate",
    "recovery_is_allowed",
]
