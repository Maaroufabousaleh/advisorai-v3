from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from advisorai.phase4.v3core_generation_readiness import (
    GenerationCoverageInput,
    evaluate_generation_readiness,
)
from advisorai.phase4.v3core_longrun import (
    LONG_RUN_MINIMUM_CLEAN_CASES_PER_SYMBOL,
    LONG_RUN_TARGET_CASES_PER_SYMBOL,
    QUALIFIED_CHRONOS_CHECKPOINT_SHA256,
    QUALIFIED_CHRONOS_MODEL_IDENTITY_SHA256,
    QUALIFIED_PHASE3_GATE_SHA256,
    QUALIFIED_REQUIREMENTS_LOCK_SHA256,
    QUALIFIED_UV_LOCK_SHA256,
    ComponentRecoveryVerification,
    LongRunCodeIdentity,
    LongRunCoverageSnapshot,
    LongRunIncidentDisposition,
    LongRunIncidentType,
    LongRunReleaseCandidateSpec,
    LongRunResourceState,
    LongRunVerification,
    RecoveryPolicy,
    ReleaseCandidateReadinessReport,
    classify_long_run_incident,
    derive_first_long_run_cutoff,
    derive_long_run_cutoffs,
    estimate_long_run_terminal_deadline,
    evaluate_long_run_coverage,
    evaluate_release_candidate,
    recovery_is_allowed,
)

HASH = "a" * 64
COMMIT = "b" * 40
START = datetime(2026, 8, 24, 16, 0, tzinfo=UTC)


def _identities() -> LongRunCodeIdentity:
    return LongRunCodeIdentity(
        repository_commit=COMMIT,
        collector_code_sha256=HASH,
        finality_code_sha256=HASH,
        chronos_worker_code_sha256=HASH,
        chronos_runner_sha256=HASH,
        watchdog_code_sha256=HASH,
        terminal_auditor_code_sha256=HASH,
        scheduler_code_sha256=HASH,
        preprocessing_sha256=HASH,
        checkpoint_sha256=QUALIFIED_CHRONOS_CHECKPOINT_SHA256,
        model_identity_sha256=QUALIFIED_CHRONOS_MODEL_IDENTITY_SHA256,
        uv_lock_sha256=QUALIFIED_UV_LOCK_SHA256,
        requirements_lock_sha256=QUALIFIED_REQUIREMENTS_LOCK_SHA256,
        phase3_gate_sha256=QUALIFIED_PHASE3_GATE_SHA256,
        source_snapshot_sha256=HASH,
    )


def _verification(**updates: bool) -> LongRunVerification:
    values = {field: True for field in LongRunVerification.model_fields}
    values.update(updates)
    return LongRunVerification(**values)


def _ready_spec(**updates: object) -> LongRunReleaseCandidateSpec:
    values: dict[str, object] = {
        "scientific": {},
        "identities": _identities(),
        "verification": _verification(),
        "resources": LongRunResourceState(
            gpu_lease_free=True,
            resident_advisorai_gpu_workers=0,
            gpu_family_cap=1,
            credentials_loaded=False,
            order_writes_attempted=False,
            execution_authority_present=False,
            evidence_root_empty=True,
            immutable_preregistration_created=False,
        ),
    }
    values.update(updates)
    return LongRunReleaseCandidateSpec(**values)


def _coverage(
    *,
    btc_clean: int = 0,
    eth_clean: int = 0,
    btc_excluded: int = 0,
    eth_excluded: int = 0,
    btc_remaining: int | None = None,
    eth_remaining: int | None = None,
) -> LongRunCoverageSnapshot:
    return LongRunCoverageSnapshot(
        clean_cases={"BTCUSDT": btc_clean, "ETHUSDT": eth_clean},
        excluded_cases={"BTCUSDT": btc_excluded, "ETHUSDT": eth_excluded},
        remaining_opportunities={
            "BTCUSDT": (
                LONG_RUN_TARGET_CASES_PER_SYMBOL - btc_clean - btc_excluded
                if btc_remaining is None
                else btc_remaining
            ),
            "ETHUSDT": (
                LONG_RUN_TARGET_CASES_PER_SYMBOL - eth_clean - eth_excluded
                if eth_remaining is None
                else eth_remaining
            ),
        },
    )


def test_first_cutoff_uses_fresh_run_context_geometry() -> None:
    assert derive_first_long_run_cutoff(START) == datetime(2026, 8, 24, 21, tzinfo=UTC)
    assert derive_long_run_cutoffs(START, count=3) == (
        datetime(2026, 8, 24, 21, tzinfo=UTC),
        datetime(2026, 8, 24, 22, tzinfo=UTC),
        datetime(2026, 8, 24, 23, tzinfo=UTC),
    )


def test_terminal_deadline_includes_80_opportunities_outcome_and_margin() -> None:
    deadline = estimate_long_run_terminal_deadline(START)
    assert deadline - START == timedelta(hours=86)
    assert deadline == datetime(2026, 8, 28, 6, tzinfo=UTC)


def test_shared_readiness_evaluator_supports_80_target_and_64_minimum() -> None:
    report = evaluate_generation_readiness(
        GenerationCoverageInput(
            source_completed_cases={"BTCUSDT": 0, "ETHUSDT": 0},
            candidate_predictions={"BTCUSDT": 0, "ETHUSDT": 0},
            remaining_future_cutoffs={"BTCUSDT": 80, "ETHUSDT": 80},
            candidate_root_healthy=True,
            cases_per_symbol_target=80,
            minimum_clean_cases_per_symbol=64,
        )
    )
    assert report.status == "CANDIDATE_COVERAGE_POSSIBLE"
    assert report.expected_predictions_total == 160
    assert report.minimum_clean_cases_per_symbol == 64


@pytest.mark.parametrize("excluded", [0, 1, 15, 16])
def test_target_buffer_keeps_clean_minimum_attainable(excluded: int) -> None:
    report = evaluate_long_run_coverage(_coverage(btc_excluded=excluded, eth_excluded=excluded))
    assert report.status == "COVERAGE_POSSIBLE"


def test_seventeenth_exclusion_is_mathematically_impossible() -> None:
    report = evaluate_long_run_coverage(_coverage(btc_excluded=17, eth_excluded=17))
    assert report.status == "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION"
    assert "BTCUSDT_cannot_reach_64_candidate_predictions" in report.reasons
    assert "ETHUSDT_cannot_reach_64_candidate_predictions" in report.reasons


@pytest.mark.parametrize(
    ("clean", "remaining", "expected"),
    [
        (0, 80, "COVERAGE_POSSIBLE"),
        (40, 40, "COVERAGE_POSSIBLE"),
        (63, 0, "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION"),
    ],
)
def test_coverage_is_deterministic_at_early_middle_and_near_deadline(
    clean: int, remaining: int, expected: str
) -> None:
    report = evaluate_long_run_coverage(
        _coverage(
            btc_clean=clean,
            eth_clean=clean,
            btc_remaining=remaining,
            eth_remaining=remaining,
            btc_excluded=LONG_RUN_TARGET_CASES_PER_SYMBOL - clean - remaining,
            eth_excluded=LONG_RUN_TARGET_CASES_PER_SYMBOL - clean - remaining,
        )
    )
    assert report.status == expected


def test_asymmetric_symbol_counts_fail_or_pass_independently() -> None:
    failed = evaluate_long_run_coverage(
        _coverage(
            btc_clean=64,
            eth_clean=63,
            btc_excluded=16,
            eth_excluded=17,
            btc_remaining=0,
            eth_remaining=0,
        )
    )
    assert failed.status == "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION"

    passed = evaluate_long_run_coverage(
        _coverage(
            btc_clean=80,
            eth_clean=64,
            btc_excluded=0,
            eth_excluded=16,
            btc_remaining=0,
            eth_remaining=0,
        )
    )
    assert passed.status == "COVERAGE_POSSIBLE"


def test_release_candidate_dry_run_is_ready_but_never_launch_ready() -> None:
    report = evaluate_release_candidate(_ready_spec())
    assert report.decision == "RELEASE_CANDIDATE_READY_FOR_HUMAN_REVIEW"
    assert report.refusal_reasons == ()
    assert report.long_run_ready is False
    assert report.report_hash


def test_release_candidate_requires_dual_lock_identity() -> None:
    bad = _identities().model_copy(update={"requirements_lock_sha256": HASH})
    with pytest.raises(ValueError, match="requirements.lock identity"):
        LongRunReleaseCandidateSpec(
            scientific={},
            identities=bad,
            verification=_verification(),
            resources=LongRunResourceState(gpu_lease_free=True),
        )


def test_release_candidate_refuses_unsafe_resource_state() -> None:
    report = evaluate_release_candidate(
        _ready_spec(resources=LongRunResourceState(gpu_lease_free=False))
    )
    assert report.decision == "RELEASE_CANDIDATE_REFUSED"
    assert "resource_and_security_boundary" in report.refusal_reasons


def test_readiness_fingerprint_rejects_mutation() -> None:
    report = evaluate_release_candidate(_ready_spec())
    payload = report.model_dump(mode="json")
    payload["checks"][0]["passed"] = False
    with pytest.raises(ValueError, match="readiness fingerprint"):
        ReleaseCandidateReadinessReport.model_validate(payload)


def test_readiness_fingerprint_covers_identity_input() -> None:
    report = evaluate_release_candidate(_ready_spec())
    payload = report.model_dump(mode="json")
    payload["input_fingerprint_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="readiness fingerprint"):
        ReleaseCandidateReadinessReport.model_validate(payload)


def test_recovery_policy_is_identity_preserving_and_bounded() -> None:
    verification = ComponentRecoveryVerification(
        same_repository_identity=True,
        same_code_identity=True,
        same_model_checkpoint_identity=True,
        same_runtime_lock_identity=True,
        append_only_state_valid=True,
        resume_identity_valid=True,
        no_conflicting_duplicates=True,
        no_evidence_rewrite=True,
        watchdog_recorded_interruption=True,
        scientific_continuity_valid=True,
    )
    assert recovery_is_allowed(verification, attempt_number=1)
    assert not recovery_is_allowed(verification, attempt_number=2)
    assert not recovery_is_allowed(
        verification.model_copy(update={"no_evidence_rewrite": False}), attempt_number=1
    )
    assert RecoveryPolicy().max_automatic_recovery_attempts_per_component_incident == 1


def test_incident_dispositions_separate_case_exclusion_recovery_and_fatal() -> None:
    assert (
        classify_long_run_incident(
            LongRunIncidentType.MISSED_MANDATORY_CUTOFF,
            clean_minimum_still_attainable=True,
        )
        == LongRunIncidentDisposition.CASE_EXCLUDED
    )
    assert (
        classify_long_run_incident(
            LongRunIncidentType.MISSED_MANDATORY_CUTOFF,
            clean_minimum_still_attainable=False,
        )
        == LongRunIncidentDisposition.GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION
    )
    assert (
        classify_long_run_incident(
            LongRunIncidentType.CANDIDATE_PROCESS_DEATH,
            clean_minimum_still_attainable=True,
        )
        == LongRunIncidentDisposition.GENERATION_FATAL
    )
    assert (
        classify_long_run_incident(
            LongRunIncidentType.CANDIDATE_PROCESS_DEATH,
            clean_minimum_still_attainable=True,
            recovery_verified=True,
        )
        == LongRunIncidentDisposition.COMPONENT_RECOVERY_ALLOWED
    )
    assert (
        classify_long_run_incident(
            LongRunIncidentType.POST_ADMISSION_REVISION,
            clean_minimum_still_attainable=True,
        )
        == LongRunIncidentDisposition.GENERATION_FATAL
    )


def test_v3b_reference_and_scientific_minimum_remain_fixed() -> None:
    spec = _ready_spec()
    assert spec.qualified_canary.canary_qualified is True
    assert spec.qualified_canary.admission_eligible is False
    assert spec.scientific.minimum_clean_cases_per_symbol == LONG_RUN_MINIMUM_CLEAN_CASES_PER_SYMBOL
    assert spec.scientific.target_cases_per_symbol == LONG_RUN_TARGET_CASES_PER_SYMBOL
    assert spec.identities.checkpoint_sha256 == QUALIFIED_CHRONOS_CHECKPOINT_SHA256
