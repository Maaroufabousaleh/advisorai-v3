from datetime import timedelta

import pytest

from advisorai.phase0.bakeoffs import ResourceSample, evaluate_stability


def test_stability_window_requires_twenty_four_hours_and_bounds_growth(timestamp):
    samples = (
        ResourceSample(rss_mib=100, vms_mib=200, cpu_percent=1, sampled_at=timestamp),
        ResourceSample(
            rss_mib=110, vms_mib=210, cpu_percent=1, sampled_at=timestamp + timedelta(days=1)
        ),
    )
    window = evaluate_stability(
        started_at=timestamp,
        ended_at=timestamp + timedelta(days=1),
        samples=samples,
        allowed_growth_mib=20,
    )
    assert window.passed


def test_stability_window_sorts_samples_and_rejects_out_of_window_measurements(timestamp):
    samples = (
        ResourceSample(
            rss_mib=110,
            vms_mib=210,
            cpu_percent=1,
            sampled_at=timestamp + timedelta(days=1),
        ),
        ResourceSample(rss_mib=100, vms_mib=200, cpu_percent=1, sampled_at=timestamp),
    )
    window = evaluate_stability(
        started_at=timestamp,
        ended_at=timestamp + timedelta(days=1),
        samples=samples,
        allowed_growth_mib=20,
    )
    assert window.samples[0].sampled_at == timestamp
    with pytest.raises(ValueError, match="within the measured window"):
        evaluate_stability(
            started_at=timestamp,
            ended_at=timestamp + timedelta(days=1),
            samples=(
                ResourceSample(
                    rss_mib=100,
                    vms_mib=200,
                    cpu_percent=1,
                    sampled_at=timestamp + timedelta(days=2),
                ),
            ),
            allowed_growth_mib=20,
        )
