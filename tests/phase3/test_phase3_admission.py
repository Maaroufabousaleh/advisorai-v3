from __future__ import annotations

from datetime import UTC, datetime

from scripts.evaluate_phase3_admission import (
    Phase3AdmissionReport,
    _evaluate_checks,
)

NOW = datetime(2026, 8, 11, 4, 0, tzinfo=UTC)


def _sample(symbol: str, *, healthy: bool = True, replay: bool = True) -> dict[str, object]:
    return {
        "source_id": "binance_spot_public_market_data",
        "provider_identity": "binance_spot_public_market_data",
        "endpoint": "wss://stream.binance.com:9443/ws",
        "symbol": symbol,
        "health_state": "HEALTHY" if healthy else "STALE",
        "source_contract_valid": True,
        "last_valid_event_age_seconds": 0.2 if healthy else 30.0,
        "replay_equivalent": replay,
        "sequence_gap_count": 0,
        "duplicate_count": 0,
        "out_of_order_count": 0,
        "stale_interval_count": 0 if healthy else 1,
        "valid_event_count": 5,
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "cycle_ended_at": NOW.isoformat(),
    }


def _logs(*samples: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    return {
        "samples.jsonl": list(samples),
        "source-selection.jsonl": [
            {
                "fail_closed": False,
                "silent_substitution": False,
                "selected_source_id": "binance_spot_public_market_data",
                "selected_provider_identity": "binance_spot_public_market_data",
                "actual_source_identity": "binance_spot_public_market_data",
            },
            {
                "fail_closed": False,
                "silent_substitution": False,
                "selected_source_id": "binance_spot_public_market_data",
                "selected_provider_identity": "binance_spot_public_market_data",
                "actual_source_identity": "binance_spot_public_market_data",
            },
        ],
        "disagreement.jsonl": [
            {"state": "NORMAL", "fail_closed": False},
        ],
        "health-transitions.jsonl": [{"state": "HEALTHY"}],
        "observations.jsonl": [{"source_id": "binance_spot_public_market_data"}],
    }


def _inputs(logs: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    return {
        "config": {
            "credentials_loaded": False,
            "order_writes_attempted": False,
            "duration_hours": 1.0,
            "source_health_policy": {"stale_after_seconds": 5.0},
        },
        "status": {
            "state": "multi_hour_window_complete",
            "started_at": "2026-08-11T03:00:00+00:00",
            "target_end_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        },
        "summary": {
            "state": "multi_hour_window_complete",
            "terminal_sample_count": 1,
        },
        "logs": logs,
        "resource_monitor": {
            "state": "deadline_reached",
            "sample_count": 1,
            "resource_errors": [],
            "summary_sha256": "d" * 64,
        },
    }


def test_admission_evaluator_requires_healthy_btc_eth_primary_source():
    logs = _logs(_sample("BTC"), _sample("ETH"))
    checks = _evaluate_checks(**_inputs(logs))

    assert all(check.passed for check in checks)
    report = Phase3AdmissionReport(
        evaluated_at=NOW,
        run_directory="/evidence",
        run_config_sha256="a" * 64,
        run_summary_sha256="b" * 64,
        resource_monitor_summary_sha256="d" * 64,
        recommendation="QUALIFIED_FOR_REVIEW",
        checks=checks,
        next_admissible_action="review",
        counts={"sample_count": 2},
    )
    assert report.phase3_admission is False
    assert report.formal_gate_recorded is False
    assert report.blocker_codes == ()


def test_admission_evaluator_preserves_fail_closed_blockers():
    logs = _logs(_sample("BTC", healthy=False, replay=False), _sample("ETH"))
    inputs = _inputs(logs)
    inputs["resource_monitor"] = None
    checks = _evaluate_checks(**inputs)
    failed = {check.blocker_code for check in checks if not check.passed}

    assert "no_healthy_primary_source_for_btc_eth" in failed
    assert "primary_snapshot_sequence_or_replay_failure" in failed
    assert "resource_sidecar_missing_or_failed" in failed
    assert all(check.blocker_code for check in checks if not check.passed)


def test_admission_evaluator_normalizes_lowercase_severe_disagreement():
    logs = _logs(_sample("BTC"), _sample("ETH"))
    logs["disagreement.jsonl"] = [{"state": "severe", "fail_closed": False}]

    checks = _evaluate_checks(**_inputs(logs))

    disagreement_check = next(
        check for check in checks if check.name == "disagreement_policy_is_fail_closed"
    )
    assert not disagreement_check.passed
    assert disagreement_check.blocker_code == "disagreement_policy_not_fail_closed"


def test_admission_evaluator_requires_terminal_sample_marker():
    logs = _logs(_sample("BTC"), _sample("ETH"))
    inputs = _inputs(logs)
    inputs["summary"] = {"state": "multi_hour_window_complete", "terminal_sample_count": 0}

    checks = _evaluate_checks(**inputs)

    window_check = next(check for check in checks if check.name == "multi_hour_window_complete")
    assert not window_check.passed
    assert window_check.blocker_code == "qualification_window_incomplete"
