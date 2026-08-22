from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from advisorai.phase4 import (
    ForwardPredictionLedger,
    GenerationWatchdogSnapshot,
    RetrospectiveDiagnosticLedger,
    RetrospectiveDiagnosticRecord,
    RetrospectiveEvidenceRefused,
    evaluate_watchdog,
    reject_retrospective_for_admission,
)
from advisorai.phase4.v3core_forward import ForwardPredictionRecord

HASH = "a" * 64
NOW = datetime(2026, 8, 22, 20, 0, tzinfo=UTC)


def _prediction(instrument: str = "BTCUSDT") -> ForwardPredictionRecord:
    return ForwardPredictionRecord(
        prediction_id=f"{instrument}:{NOW.isoformat()}:chronos-2-small",
        instrument=instrument,
        model="chronos-2-small",
        model_identity_hash=HASH,
        cutoff=NOW,
        input_snapshot_hash=HASH,
        predicted_return_bps=Decimal("1"),
        generated_at=NOW,
        runtime_latency_ms=Decimal("1"),
    )


def _snapshot(**updates: object) -> GenerationWatchdogSnapshot:
    payload: dict[str, object] = {
        "observed_at": NOW,
        "source_process_alive": True,
        "source_health_valid": True,
        "candidate_process_alive": True,
        "candidate_model_loaded": True,
        "latest_raw_receipt_at": NOW,
        "latest_admitted_final_bar_at": NOW,
        "latest_eligible_cutoff": NOW,
        "last_successful_prediction_at": {"BTCUSDT": NOW, "ETHUSDT": NOW},
        "candidate_predictions": {"BTCUSDT": 10, "ETHUSDT": 10},
        "source_completed_cases": {"BTCUSDT": 10, "ETHUSDT": 10},
        "remaining_future_cutoffs": {"BTCUSDT": 54, "ETHUSDT": 54},
        "candidate_ledger_healthy": True,
        "candidate_root_healthy": True,
    }
    payload.update(updates)
    return GenerationWatchdogSnapshot(**payload)


def test_retrospective_diagnostic_ledger_is_separate_and_round_trips(tmp_path) -> None:
    path = tmp_path / "retrospective-diagnostic.jsonl"
    record = RetrospectiveDiagnosticRecord(
        diagnostic_reason="sealed bars used only to qualify the corrected worker",
        prediction=_prediction(),
    )
    ledger = RetrospectiveDiagnosticLedger(path)
    assert ledger.append(record)
    assert not ledger.append(record)
    reopened = RetrospectiveDiagnosticLedger(path)
    assert len(reopened.records) == 1
    assert reopened.records[0].record.evidence_class == "RETROSPECTIVE_DIAGNOSTIC"
    assert reopened.records[0].record.admission_evidence is False


def test_retrospective_records_fail_closed_at_a_prospective_boundary() -> None:
    record = RetrospectiveDiagnosticRecord(
        diagnostic_reason="runtime-only test",
        prediction=_prediction(),
    )
    with pytest.raises(RetrospectiveEvidenceRefused, match="cannot satisfy prospective"):
        reject_retrospective_for_admission((record,))


def test_retrospective_ledger_cannot_be_opened_as_prospective_ledger(tmp_path) -> None:
    path = tmp_path / "diagnostic.jsonl"
    ledger = RetrospectiveDiagnosticLedger(path)
    ledger.append(
        RetrospectiveDiagnosticRecord(
            diagnostic_reason="runtime-only test",
            prediction=_prediction(),
        )
    )
    with pytest.raises(RuntimeError, match="forward prediction ledger is corrupt"):
        ForwardPredictionLedger(path)


def test_watchdog_reports_recoverable_candidate_degradation() -> None:
    report = evaluate_watchdog(_snapshot(rejection_count=1, consecutive_failures=1))
    assert report.status == "CANDIDATE_DEGRADED_BUT_RECOVERABLE"
    assert "candidate_rejections_observed" in report.reasons
    assert "candidate_consecutive_failures_observed" in report.reasons


def test_watchdog_reports_impossible_coverage_when_no_future_cutoffs_remain() -> None:
    report = evaluate_watchdog(
        _snapshot(
            candidate_predictions={"BTCUSDT": 63, "ETHUSDT": 64},
            source_completed_cases={"BTCUSDT": 64, "ETHUSDT": 64},
            remaining_future_cutoffs={"BTCUSDT": 0, "ETHUSDT": 0},
        )
    )
    assert report.status == "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION"
    assert "BTCUSDT_cannot_reach_64_candidate_predictions" in report.reasons


def test_watchdog_does_not_equate_alive_process_with_healthy_coverage() -> None:
    report = evaluate_watchdog(
        _snapshot(
            candidate_predictions={"BTCUSDT": 0, "ETHUSDT": 0},
            source_completed_cases={"BTCUSDT": 64, "ETHUSDT": 64},
            remaining_future_cutoffs={"BTCUSDT": 0, "ETHUSDT": 0},
            candidate_process_alive=True,
            candidate_model_loaded=True,
        )
    )
    assert report.status == "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION"
    assert report.readiness.complete_coverage_possible is False


def test_watchdog_fails_closed_when_source_dies_before_terminal_state() -> None:
    report = evaluate_watchdog(_snapshot(source_process_alive=False, source_terminal=False))
    assert report.status == "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION"
    assert "source_process_not_alive_before_terminal_state" in report.reasons
