from datetime import UTC, datetime, timedelta

import pytest

from advisorai.runtime import CadenceGate, CadencePolicy


def test_v3_core_cadence_requires_five_minute_observations_for_hourly_decisions():
    gate = CadenceGate()
    cutoff = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    expected = gate.expected_observation_cutoffs(cutoff)
    assert len(expected) == 12
    assert expected[0] == datetime(2026, 8, 5, 14, 5, tzinfo=UTC)
    assert expected[-1] == cutoff
    ready = gate.check(
        cutoff=cutoff,
        observed_cutoffs=expected,
        snapshot_as_of=cutoff,
        snapshot_quality="validated",
    )
    assert ready.passed
    assert not ready.reasons


def test_cadence_readiness_fails_closed_for_missing_data_quality_and_future_snapshot():
    gate = CadenceGate()
    cutoff = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    expected = gate.expected_observation_cutoffs(cutoff)
    result = gate.check(
        cutoff=cutoff,
        observed_cutoffs=expected[:-1],
        snapshot_as_of=cutoff + timedelta(minutes=1),
        snapshot_quality="review",
    )
    assert not result.passed
    assert set(result.reasons) == {
        "missing_closed_observation_data",
        "snapshot_after_cutoff",
        "data_quality:review",
    }
    with pytest.raises(ValueError, match="align"):
        gate.check(cutoff=cutoff + timedelta(minutes=1), observed_cutoffs=expected)


def test_cadence_policy_requires_integral_intervals():
    with pytest.raises(ValueError, match="whole observation"):
        CadencePolicy(observation_interval_seconds=300, decision_interval_seconds=1000)
    with pytest.raises(ValueError, match="whole decision"):
        CadencePolicy(
            observation_interval_seconds=300,
            decision_interval_seconds=3600,
            context_interval_seconds=5000,
        )
