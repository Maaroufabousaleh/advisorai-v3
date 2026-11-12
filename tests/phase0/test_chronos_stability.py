from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from advisorai.phase0 import (
    CHRONOS_MAX_RESIDUAL_RSS_MIB,
    CHRONOS_MAX_RESIDUAL_VRAM_MIB,
    ChronosStabilityConfig,
    ChronosStabilitySample,
    append_chronos_cycle,
    make_chronos_cycle,
    read_chronos_cycles,
    summarize_chronos_stability,
)
from advisorai.phase0.runtime_qualification import QualificationStatus
from scripts import run_chronos_runtime_stability as runner
from scripts.run_model_stability import SELECTED_CANDIDATES
from scripts.validate_model_stability import REQUIRED_ROLES


def _config(tmp_path, *, started_at: datetime | None = None) -> ChronosStabilityConfig:
    digest = "a" * 64
    return ChronosStabilityConfig(
        run_id="chronos-fixture",
        started_at=started_at or datetime(2026, 9, 1, tzinfo=UTC),
        duration_hours=24,
        interval_seconds=300,
        admission_path=str(tmp_path / "admission.json"),
        admission_sha256=digest,
        qualification_evidence_path=str(tmp_path / "qualification.json"),
        qualification_evidence_sha256=digest,
        repository_root=str(tmp_path),
        repository_commit="b" * 40,
        stability_runner_sha256=digest,
        chronos_contract_sha256=digest,
        runtime_qualification_sha256=digest,
        model_revision="ddec01313e50b6bc58ebaa92ede81bc24a3d9f9a",
        checkpoint_sha256=digest,
        config_sha256=digest,
        python_launcher_hash=digest,
        resolved_python_binary_hash=digest,
        pyvenv_cfg_hash=digest,
        installed_environment_sha256=digest,
        runtime_lock_hash=digest,
        runner_hash=digest,
        worker_script_sha256=digest,
        environment_fingerprint=digest,
        runtime_identity_sha256=digest,
        dataset_id="synthetic-chronos-fixture",
        dataset_hash=digest,
    )


def _sample(
    timestamp: datetime,
    *,
    identity: str = "a" * 64,
    residual_rss: float = 32,
    residual_vram: float = 32,
    gpu_lease_released: bool = True,
    failure_reason: str | None = None,
) -> ChronosStabilitySample:
    return ChronosStabilitySample(
        sampled_at=timestamp,
        status="measured" if failure_reason is None else "failed",
        runtime_identity_sha256=identity,
        qualification_manifest_hash="c" * 64,
        offline_cached_inference=failure_reason is None,
        network_access_attempted=False,
        model_loaded=failure_reason is None,
        inference_completed=failure_reason is None,
        batch_completed=failure_reason is None,
        output_schema_valid=failure_reason is None,
        finite_output_valid=failure_reason is None,
        repeated_outputs_equal=failure_reason is None,
        worker_process_terminated=failure_reason is None,
        gpu_lease_released=gpu_lease_released,
        resource_limit_passed=failure_reason is None,
        memory_released=failure_reason is None,
        rss_peak_mib=512,
        rss_after_unload_mib=128,
        rss_residual_mib=residual_rss,
        vram_peak_mib=1024,
        vram_after_unload_mib=64,
        vram_residual_mib=residual_vram,
        failure_reason=failure_reason,
    )


def test_chronos_profile_requires_24_hours_and_fixed_resource_ceiling(tmp_path):
    config = _config(tmp_path)
    assert config.duration_hours == 24
    assert config.interval_seconds == 300
    with pytest.raises(ValidationError, match="greater than or equal to 24"):
        ChronosStabilityConfig.model_validate({**config.model_dump(), "duration_hours": 23})
    with pytest.raises(ValidationError, match="cannot be weakened"):
        ChronosStabilityConfig.model_validate({**config.model_dump(), "max_residual_rss_mib": 257})
    with pytest.raises(ValidationError):
        ChronosStabilityConfig.model_validate({**config.model_dump(), "candidate": "kronos-mini"})
    with pytest.raises(ValidationError):
        ChronosStabilityConfig.model_validate({**config.model_dump(), "credentials_loaded": True})


def test_historical_three_role_stability_contract_is_unchanged():
    expected = ("ttm-r2", "finsentiment-deberta-v3", "finbert-minilm")
    assert SELECTED_CANDIDATES == expected
    assert REQUIRED_ROLES == expected


def test_runner_uses_only_the_named_synthetic_chronos_fixture():
    dataset = runner._fresh_dataset()
    assert dataset.dataset_id == "advisorai-phase0-chronos-runtime-fixture"
    assert dataset.source.startswith("synthetic://advisorai/phase0/chronos")


def test_chronos_cycle_is_append_only_and_tamper_evident(tmp_path):
    config = _config(tmp_path)
    path = tmp_path / "cycles.jsonl"
    first = make_chronos_cycle(config, _sample(config.started_at), sequence=0)
    second = make_chronos_cycle(
        config,
        _sample(config.started_at + timedelta(hours=24)),
        sequence=1,
        previous_record_hash=first.record_hash,
    )
    append_chronos_cycle(path, first)
    append_chronos_cycle(path, second)
    assert read_chronos_cycles(path) == (first, second)

    lines = path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    payload["sample"]["rss_after_unload_mib"] = 999
    path.write_text(json.dumps(payload) + "\n" + lines[1] + "\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="cycle hash"):
        read_chronos_cycles(path)


def test_chronos_summary_requires_terminal_passing_cycle(tmp_path):
    config = _config(tmp_path)
    short_cycle = make_chronos_cycle(
        config,
        _sample(config.started_at + timedelta(hours=1)),
        sequence=0,
    )
    short = summarize_chronos_stability(config, (short_cycle,))
    assert short.status == "short_smoke_complete"
    assert short.stability_24h_passed is False

    # The second record must be linked to the first record.
    first = make_chronos_cycle(config, _sample(config.started_at), sequence=0)
    cycles = (
        first,
        make_chronos_cycle(
            config,
            _sample(config.started_at + timedelta(hours=24)),
            sequence=1,
            previous_record_hash=first.record_hash,
        ),
    )
    summary = summarize_chronos_stability(config, cycles)
    assert summary.status == "passed"
    assert summary.stability_24h_passed is True


def test_failed_resource_or_gpu_cleanup_cycle_cannot_pass(tmp_path):
    config = _config(tmp_path)
    over_residual = _sample(
        config.started_at,
        residual_rss=CHRONOS_MAX_RESIDUAL_RSS_MIB + 1,
        failure_reason="residual_resource_ceiling_failed",
    )
    no_lease = _sample(
        config.started_at + timedelta(seconds=1),
        gpu_lease_released=False,
        failure_reason="gpu_lease_cleanup_failed",
    )
    assert over_residual.passed is False
    assert no_lease.passed is False


def test_runner_sample_requires_full_runtime_result_and_residual_limits():
    resource = SimpleNamespace(
        rss_peak_mib=1000,
        rss_after_unload_mib=128,
        rss_residual_mib=64,
        vram_peak_mib=2000,
        vram_after_unload_mib=32,
        vram_residual_mib=64,
        resource_limit_passed=True,
        worker_process_terminated=True,
        memory_released=True,
    )
    result = SimpleNamespace(
        status=QualificationStatus.MEASURED,
        candidate=SimpleNamespace(name="chronos-2-small"),
        resource=resource,
        offline_cached_inference=True,
        network_access_attempted=False,
        one_inference_completed=True,
        batch_completed=True,
        output_schema_valid=True,
        nan_inf_rejection_passed=True,
        repeated_outputs_equal=True,
        output_shape="forecast[30]",
        loaded_model_class="Chronos2Pipeline",
        failure_reason=None,
        model_dump=lambda **_kwargs: {"candidate": "chronos-2-small", "status": "measured"},
    )
    sample = runner._sample_from_result(
        result,
        runtime_identity_sha256="a" * 64,
        sampled_at=datetime.now(UTC),
        gpu_lease_released=True,
    )
    assert sample.passed is True

    resource.vram_residual_mib = CHRONOS_MAX_RESIDUAL_VRAM_MIB + 1
    rejected = runner._sample_from_result(
        result,
        runtime_identity_sha256="a" * 64,
        sampled_at=datetime.now(UTC),
        gpu_lease_released=True,
    )
    assert rejected.passed is False
    assert rejected.failure_reason == "residual_resource_ceiling_failed"


def test_runner_rejects_stale_resolved_python_identity(monkeypatch, tmp_path):
    config = _config(tmp_path)
    digest = "a" * 64
    identity = SimpleNamespace(
        candidate_name="chronos-2-small",
        checkpoint_revision=config.model_revision,
        checkpoint_hash=config.checkpoint_sha256,
        config_hash=config.config_sha256,
        python_launcher_hash=config.python_launcher_hash,
        resolved_python_binary_hash="b" * 64,
        pyvenv_cfg_hash=config.pyvenv_cfg_hash,
        installed_environment_sha256=config.installed_environment_sha256,
        lock_hash=config.runtime_lock_hash,
        runner_hash=config.runner_hash,
        runner_script_hash=config.worker_script_sha256,
        environment_fingerprint=config.environment_fingerprint,
        admission_sha256=config.admission_sha256,
        qualification_evidence_sha256=config.qualification_evidence_sha256,
        model_dump=lambda **_kwargs: {},
    )
    binding = {
        "repository_commit": config.repository_commit,
        "repository_clean": True,
        "stability_runner_sha256": config.stability_runner_sha256,
        "chronos_contract_sha256": config.chronos_contract_sha256,
        "runtime_qualification_sha256": config.runtime_qualification_sha256,
        "runtime_identity_sha256": config.runtime_identity_sha256,
    }
    monkeypatch.setattr(runner, "payload_hash", lambda _payload: digest)
    with pytest.raises(runner.IdentityDriftError, match="resolved_python_binary_hash_changed"):
        runner._assert_identity_unchanged(config, identity=identity, binding=binding)


@pytest.mark.parametrize("field_name", ("checkpoint_hash", "runner_hash", "runner_script_hash"))
def test_runner_rejects_checkpoint_and_worker_identity_drift(monkeypatch, tmp_path, field_name):
    config = _config(tmp_path)
    identity = SimpleNamespace(
        candidate_name="chronos-2-small",
        checkpoint_revision=config.model_revision,
        checkpoint_hash=config.checkpoint_sha256,
        config_hash=config.config_sha256,
        python_launcher_hash=config.python_launcher_hash,
        resolved_python_binary_hash=config.resolved_python_binary_hash,
        pyvenv_cfg_hash=config.pyvenv_cfg_hash,
        installed_environment_sha256=config.installed_environment_sha256,
        lock_hash=config.runtime_lock_hash,
        runner_hash=config.runner_hash,
        runner_script_hash=config.worker_script_sha256,
        environment_fingerprint=config.environment_fingerprint,
        admission_sha256=config.admission_sha256,
        qualification_evidence_sha256=config.qualification_evidence_sha256,
        model_dump=lambda **_kwargs: {},
    )
    setattr(identity, field_name, "b" * 64)
    binding = {
        "repository_commit": config.repository_commit,
        "repository_clean": True,
        "stability_runner_sha256": config.stability_runner_sha256,
        "chronos_contract_sha256": config.chronos_contract_sha256,
        "runtime_qualification_sha256": config.runtime_qualification_sha256,
        "runtime_identity_sha256": config.runtime_identity_sha256,
    }
    monkeypatch.setattr(runner, "payload_hash", lambda _payload: "a" * 64)
    with pytest.raises(runner.IdentityDriftError, match=f"{field_name}_changed"):
        runner._assert_identity_unchanged(config, identity=identity, binding=binding)


def test_missing_admission_fails_before_stability_root_initialization(tmp_path):
    with pytest.raises(ValueError, match="admission or qualification evidence is missing"):
        runner._current_identity(
            admission_path=tmp_path / "missing-admission.json",
            qualification_evidence_path=tmp_path / "missing-qualification.json",
            repository_root=tmp_path,
        )
