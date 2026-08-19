from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from advisorai.phase4.v3core_generation_readiness import (
    EXPECTED_CANDIDATE_MODEL,
    EXPECTED_CASES_PER_SYMBOL,
    GenerationCandidateContract,
    GenerationCoverageInput,
    GenerationPreflightSpec,
    GenerationProspectiveContract,
    GenerationResourceContract,
    GenerationSourceContract,
    evaluate_generation_readiness,
    evaluate_preflight,
)

HASH = "a" * 64
FIRST_CUTOFF = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)


def _spec() -> GenerationPreflightSpec:
    return GenerationPreflightSpec(
        source=GenerationSourceContract(
            provider_identity="binance_spot_public_market_data",
            rest_endpoint="https://data-api.binance.vision/api/v3/klines",
            websocket_endpoint="wss://data-stream.binance.vision/ws",
            collector_code_sha256=HASH,
            preregistration_sha256=HASH,
            phase3_gate_sha256=HASH,
            source_snapshot_hash=HASH,
            target_end_at=FIRST_CUTOFF + timedelta(days=4),
            first_eligible_cutoff=FIRST_CUTOFF,
        ),
        candidate=GenerationCandidateContract(
            model=EXPECTED_CANDIDATE_MODEL,
            model_identity_sha256=HASH,
            checkpoint_sha256=HASH,
            qualification_evidence_sha256=HASH,
            worker_code_sha256=HASH,
            runner_sha256=HASH,
            preprocessing_sha256=HASH,
            runtime_environment_sha256=HASH,
            runtime_qualification_passed=True,
            input_context_compatible=True,
            output_horizon_compatible=True,
            prediction_schema_round_trip_passed=True,
            worker_identity_frozen=True,
        ),
        resource=GenerationResourceContract(
            gpu_lease_available=True,
            resident_gpu_family_count=0,
            sidecar_available=True,
            memory_budget_measured=True,
        ),
        prospective=GenerationProspectiveContract(
            candidate_started_at=FIRST_CUTOFF - timedelta(minutes=5),
            first_eligible_cutoff=FIRST_CUTOFF,
            fresh_run_root=True,
            candidate_starts_before_first_cutoff=True,
        ),
    )


def test_preflight_accepts_complete_frozen_candidate_path() -> None:
    report = evaluate_preflight(_spec())
    assert report.decision == "READY_TO_LAUNCH"
    assert report.refusal_reasons == ()
    assert report.report_hash


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        (
            "source.rest_endpoint",
            "https://api.binance.com/api/v3/klines",
            "market_data_only_endpoints",
        ),
        ("source.websocket_endpoint", "wss://stream.binance.com/ws", "market_data_only_endpoints"),
        ("source.provider_identity", "binance_spot_testnet", "source_provider_identity"),
        ("source.credentials_loaded", True, "source_write_and_credential_boundary"),
        ("source.order_capability", True, "source_write_and_credential_boundary"),
        ("candidate.model", "lightgbm", "candidate_non_baseline"),
        ("candidate.context_bars", 47, "candidate_v3core_compatibility"),
        ("candidate.prediction_schema_round_trip_passed", False, "candidate_schema_and_runtime"),
        ("resource.gpu_lease_available", False, "resource_safety"),
        ("resource.resident_gpu_family_count", 1, "resource_safety"),
        ("prospective.existing_completed_cases", 1, "fresh_prospective_root"),
        ("prospective.historical_backfill_enabled", True, "fresh_prospective_root"),
        (
            "prospective.candidate_starts_before_first_cutoff",
            False,
            "candidate_starts_before_first_cutoff",
        ),
    ],
)
def test_preflight_refuses_each_unsafe_launch_condition(
    path: str, value: object, reason: str
) -> None:
    spec = _spec()
    section, field = path.split(".")
    updated = getattr(spec, section).model_copy(update={field: value})
    spec = spec.model_copy(update={section: updated})
    report = evaluate_preflight(spec)
    assert report.decision == "REFUSE_LAUNCH"
    assert reason in report.refusal_reasons


def _coverage(
    *,
    btc_predictions: int = 0,
    eth_predictions: int = 0,
    btc_remaining: int = EXPECTED_CASES_PER_SYMBOL,
    eth_remaining: int = EXPECTED_CASES_PER_SYMBOL,
    healthy: bool = True,
) -> GenerationCoverageInput:
    return GenerationCoverageInput(
        source_completed_cases={"BTCUSDT": 0, "ETHUSDT": 0},
        candidate_predictions={"BTCUSDT": btc_predictions, "ETHUSDT": eth_predictions},
        remaining_future_cutoffs={"BTCUSDT": btc_remaining, "ETHUSDT": eth_remaining},
        candidate_root_healthy=healthy,
    )


def test_readiness_reports_complete_candidate_coverage_possible() -> None:
    report = evaluate_generation_readiness(_coverage())
    assert report.status == "CANDIDATE_COVERAGE_POSSIBLE"
    assert report.complete_coverage_possible is True
    assert report.expected_predictions_total == 128


def test_readiness_refuses_impossible_candidate_coverage() -> None:
    report = evaluate_generation_readiness(
        _coverage(btc_predictions=20, eth_predictions=20, btc_remaining=10, eth_remaining=10)
    )
    assert report.status == "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION"
    assert report.complete_coverage_possible is False
    assert "BTCUSDT_cannot_reach_64_candidate_predictions" in report.reasons
    assert "ETHUSDT_cannot_reach_64_candidate_predictions" in report.reasons


def test_readiness_requires_healthy_candidate_root_even_with_enough_cutoffs() -> None:
    report = evaluate_generation_readiness(_coverage(healthy=False))
    assert report.status == "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION"
    assert report.reasons == ("candidate_root_unhealthy",)


def test_coverage_contract_rejects_wrong_symbols_or_target() -> None:
    with pytest.raises(ValueError, match="exactly BTCUSDT and ETHUSDT"):
        GenerationCoverageInput(
            source_completed_cases={"BTCUSDT": 0, "ETHUSDT": 0},
            candidate_predictions={"BTCUSDT": 64, "SOLUSDT": 64},
            remaining_future_cutoffs={"BTCUSDT": 0, "ETHUSDT": 64},
            candidate_root_healthy=True,
        )

    with pytest.raises(ValueError, match="requires 64 cases"):
        GenerationCoverageInput(
            source_completed_cases={"BTCUSDT": 0, "ETHUSDT": 0},
            candidate_predictions={"BTCUSDT": 0, "ETHUSDT": 0},
            remaining_future_cutoffs={"BTCUSDT": 64, "ETHUSDT": 64},
            candidate_root_healthy=True,
            cases_per_symbol_target=63,
        )
