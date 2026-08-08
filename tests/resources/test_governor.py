import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from advisorai.config import MissionMode, load_mode_configs
from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.resources import (
    MeasuredResources,
    ResourceGovernor,
    ResourceProfile,
    ResourceRequest,
    WorkloadClass,
)


class FixedProbe:
    def __init__(self, metrics: MeasuredResources) -> None:
        self.metrics = metrics

    def measure(self) -> MeasuredResources:
        return self.metrics


def _configs() -> dict:
    root = Path(__file__).parents[2]
    return load_mode_configs(root / "configs" / "modes")


def test_governor_rejects_memory_above_measured_mode_envelope():
    governor = ResourceGovernor(
        _configs(),
        FixedProbe(
            MeasuredResources(
                memory_used_gib=8.2,
                memory_available_gib=2.0,
                gpu_free_mib=6000,
                gpu_total_mib=8192,
                observed_at=datetime.now(UTC),
            )
        ),
    )
    decision = governor.admit(
        MissionMode.STANDARD,
        ResourceRequest(workload=WorkloadClass.CPU_BOUND, memory_reservation_gib=0.5),
    )
    assert not decision.granted
    assert "ceiling" in decision.reason


def test_governor_enforces_one_global_gpu_lease():
    governor = ResourceGovernor(
        _configs(),
        FixedProbe(
            MeasuredResources(
                memory_used_gib=4.0,
                memory_available_gib=7.0,
                gpu_free_mib=6000,
                gpu_total_mib=8192,
                observed_at=datetime.now(UTC),
            )
        ),
    )
    request = ResourceRequest(
        workload=WorkloadClass.GPU, memory_reservation_gib=0.5, requires_gpu=True
    )
    assert governor.admit(MissionMode.STANDARD, request).granted
    second = governor.admit(MissionMode.STANDARD, request)
    assert not second.granted
    assert "GPU" in second.reason


def test_load_shedding_excludes_critical_state():
    candidates = ResourceGovernor.load_shedding_candidates()
    assert WorkloadClass.RISK not in candidates
    assert WorkloadClass.RECONCILIATION not in candidates
    assert candidates[0] is WorkloadClass.ARCHIVE


def test_v3_core_mode_configuration_preserves_initial_caps():
    configs = _configs()
    assert configs[MissionMode.TRADE_FAST].memory_ceiling_gib == 6.5
    assert configs[MissionMode.STANDARD].remote_llm_limit == 2
    assert configs[MissionMode.DEEP].remote_llm_limit == 4
    assert configs[MissionMode.DEEP].gpu_jobs_limit == 1


def test_separate_browser_profile_has_one_global_job_and_its_own_envelope():
    governor = ResourceGovernor(
        _configs(),
        FixedProbe(
            MeasuredResources(
                memory_used_gib=7.6,
                memory_available_gib=3.0,
                gpu_free_mib=6000,
                gpu_total_mib=8192,
                observed_at=datetime.now(UTC),
            )
        ),
    )
    request = ResourceRequest(
        workload=WorkloadClass.BROWSER,
        memory_reservation_gib=0.1,
        profile=ResourceProfile.BROWSER,
    )
    first = governor.admit_profile(ResourceProfile.BROWSER, request)
    assert first.granted
    second = governor.admit_profile(ResourceProfile.BROWSER, request)
    assert not second.granted
    assert "browser" in second.reason.lower()


def test_trade_fast_gpu_requires_frozen_model_approval():
    governor = ResourceGovernor(
        _configs(),
        FixedProbe(
            MeasuredResources(
                memory_used_gib=4.0,
                memory_available_gib=7.0,
                gpu_free_mib=6000,
                gpu_total_mib=8192,
                observed_at=datetime.now(UTC),
            )
        ),
    )
    request = ResourceRequest(workload=WorkloadClass.GPU, requires_gpu=True)
    denied = governor.admit(MissionMode.TRADE_FAST, request)
    assert not denied.granted
    approved = governor.admit(
        MissionMode.TRADE_FAST,
        request.__class__(
            workload=WorkloadClass.GPU, requires_gpu=True, approved_frozen_inference=True
        ),
    )
    assert approved.granted


def test_browser_and_training_profiles_respect_gpu_and_hermes_exclusivity():
    governor = ResourceGovernor(
        _configs(),
        FixedProbe(
            MeasuredResources(
                memory_used_gib=4.0,
                memory_available_gib=7.0,
                gpu_free_mib=6000,
                gpu_total_mib=8192,
                observed_at=datetime.now(UTC),
            )
        ),
    )
    gpu = governor.admit(
        MissionMode.STANDARD,
        ResourceRequest(workload=WorkloadClass.GPU, requires_gpu=True),
    )
    assert gpu.granted
    browser = governor.admit_profile(
        ResourceProfile.BROWSER,
        ResourceRequest(workload=WorkloadClass.BROWSER, profile=ResourceProfile.BROWSER),
    )
    assert not browser.granted
    assert "GPU" in browser.reason

    governor.release(gpu.lease.lease_id)
    hermes = governor.admit(
        MissionMode.BUILDER,
        ResourceRequest(workload=WorkloadClass.HERMES_SKILL_FOUNDRY),
    )
    assert hermes.granted
    train = governor.admit_profile(
        ResourceProfile.TRAIN,
        ResourceRequest(
            workload=WorkloadClass.TRAINING_BACKTESTS,
            requires_gpu=True,
            profile=ResourceProfile.TRAIN,
        ),
    )
    assert not train.granted
    assert "Hermes" in train.reason


def test_browser_gpu_overlap_requires_measured_headroom_review():
    governor = ResourceGovernor(
        _configs(),
        FixedProbe(
            MeasuredResources(
                memory_used_gib=4.0,
                memory_available_gib=7.0,
                gpu_free_mib=6000,
                gpu_total_mib=8192,
                observed_at=datetime.now(UTC),
            )
        ),
    )
    gpu = governor.admit(
        MissionMode.STANDARD,
        ResourceRequest(workload=WorkloadClass.GPU, requires_gpu=True),
    )
    assert gpu.granted
    request = ResourceRequest(
        workload=WorkloadClass.BROWSER,
        profile=ResourceProfile.BROWSER,
        headroom_reviewed=True,
    )
    browser = governor.admit_profile(ResourceProfile.BROWSER, request)
    assert browser.granted


def test_browser_gpu_overlap_rejects_insufficient_measured_headroom():
    governor = ResourceGovernor(
        _configs(),
        FixedProbe(
            MeasuredResources(
                memory_used_gib=4.0,
                memory_available_gib=1.0,
                gpu_free_mib=6000,
                gpu_total_mib=8192,
                observed_at=datetime.now(UTC),
            )
        ),
    )
    gpu = governor.admit(
        MissionMode.STANDARD,
        ResourceRequest(workload=WorkloadClass.GPU, requires_gpu=True),
    )
    assert not gpu.granted


def test_resource_measurements_and_leases_are_durably_recorded(tmp_path):
    observed_at = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    governor = ResourceGovernor(
        _configs(),
        FixedProbe(
            MeasuredResources(
                memory_used_gib=4.0,
                memory_available_gib=7.0,
                gpu_free_mib=6000,
                gpu_total_mib=8192,
                observed_at=observed_at,
            )
        ),
        SqliteLedgers(tmp_path / "resource.sqlite3"),
    )
    decision = governor.admit(
        MissionMode.STANDARD,
        ResourceRequest(workload=WorkloadClass.CPU_BOUND, memory_reservation_gib=0.1),
    )
    assert decision.granted and decision.lease is not None
    governor.release(decision.lease.lease_id)
    events = governor.ledgers.events(LedgerNamespace.CAPABILITY)
    assert [event.event_type for event in events] == [
        "resource_measurement_recorded",
        "resource_lease_admitted",
        "resource_lease_released",
    ]
    assert events[0].payload["observed_at"] == observed_at.isoformat()


def test_concurrent_admissions_cannot_oversubscribe_the_global_gpu_lease():
    metrics = MeasuredResources(
        memory_used_gib=4.0,
        memory_available_gib=7.0,
        gpu_free_mib=6000,
        gpu_total_mib=8192,
        observed_at=datetime.now(UTC),
    )

    class SlowProbe:
        def measure(self):
            time.sleep(0.01)
            return metrics

    governor = ResourceGovernor(_configs(), SlowProbe())
    request = ResourceRequest(
        workload=WorkloadClass.GPU, memory_reservation_gib=0.5, requires_gpu=True
    )

    with ThreadPoolExecutor(max_workers=16) as executor:
        decisions = tuple(
            executor.map(lambda _index: governor.admit(MissionMode.STANDARD, request), range(16))
        )

    assert sum(decision.granted for decision in decisions) == 1
    assert len(governor.active_leases()) == 1
