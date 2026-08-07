from __future__ import annotations

import socket
from pathlib import Path

import pytest

from advisorai.phase0.runtime_qualification import (
    REQUIRED_HUGGINGFACE_HUB_VERSION,
    REQUIRED_TRANSFORMERS_VERSION,
    ArtifactPin,
    BenchmarkDataset,
    CandidateSpec,
    CheckpointIntegrityError,
    CheckpointPin,
    CheckpointPinError,
    ComponentKind,
    FunctionalRunner,
    ModelFamily,
    ModelTask,
    QualificationStatus,
    RepositoryPin,
    ResourceCeiling,
    RuntimeEnvironment,
    RuntimeResourceResult,
    cached_artifact_inventory,
    default_runtime_candidates,
    manifest_bytes,
    project_bakeoff_gate,
    run_finbert_qualification,
    run_forecast_baseline_benchmark,
    run_forecast_candidate_benchmark,
    run_runtime_qualification,
    run_tspulse_qualification,
    runtime_environment,
    sha256_file,
    validate_model_output,
    verify_checkpoint_artifacts,
    write_qualification_manifest,
)


def _checkpoint(
    tmp_path: Path,
    *,
    digest: str | None = None,
    family: str = ModelFamily.FINBERT.value,
) -> CheckpointPin:
    return CheckpointPin(
        model_family=family,
        repository=RepositoryPin(
            repository_id="fixture/finbert",
            revision="a" * 40,
            license="apache-2.0",
            artifacts=(ArtifactPin(relative_path="weights.bin", sha256=digest),),
        ),
        cache_path=str(tmp_path / "cache"),
    )


def _forecast_candidate(*, checkpoint: CheckpointPin | None = None) -> CandidateSpec:
    return CandidateSpec(
        name="fixture-forecast",
        family=ModelFamily.NAIVE,
        task=ModelTask.FORECAST,
        external_checkpoint=checkpoint,
        requires_transformers=checkpoint is not None,
        output_schema="forecast[1]",
    )


def _runner(family: str = ModelFamily.NAIVE.value, *, network: bool = False) -> FunctionalRunner:
    def load(_checkpoint, offline):
        assert offline is True
        return object()

    def infer(_model, _payload):
        if network:
            socket.create_connection(("example.invalid", 443))
        return (1.0,)

    return FunctionalRunner(model_family=family, load_fn=load, infer_fn=infer)


def test_default_registry_pins_exact_candidate_revisions_and_roles():
    by_name = {candidate.name: candidate for candidate in default_runtime_candidates()}
    assert by_name["finbert-family"].external_checkpoint.repository_id == "ProsusAI/finbert"
    assert by_name["finbert-family"].external_checkpoint.revision == "4556d13015211d73dccd3fdd39d39232506f3e43"
    assert by_name["ttm-r2"].external_checkpoint.repository_id == "ibm-granite/granite-timeseries-ttm-r2"
    assert by_name["chronos-2-small"].external_checkpoint.repository_id == "autogluon/chronos-2-small"
    assert by_name["kronos-mini"].external_checkpoint.tokenizer.repository_id == "NeoQuasar/Kronos-Tokenizer-2k"
    assert by_name["tabpfn-ts"].external_checkpoint.repository_id == "PriorLabs/tabpfn-time-series"
    assert by_name["tspulse"].task == ModelTask.TSPULSE_FEATURES
    with pytest.raises(ValueError, match="never a price forecaster"):
        CandidateSpec(
            name="bad-tspulse",
            family=ModelFamily.TSPULSE,
            task=ModelTask.FORECAST,
            output_schema="forecast[1]",
        )


def test_revision_and_artifact_hashes_are_fail_closed(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    artifact = cache / "weights.bin"
    artifact.write_bytes(b"model-fixture")
    digest = sha256_file(artifact)
    pin = _checkpoint(tmp_path, digest=digest)
    assert verify_checkpoint_artifacts(pin, cache_root=cache)[0].sha256 == digest
    artifact.write_bytes(b"corrupt")
    with pytest.raises(CheckpointIntegrityError):
        verify_checkpoint_artifacts(pin, cache_root=cache)
    with pytest.raises(CheckpointPinError, match="no pinned hash"):
        verify_checkpoint_artifacts(_checkpoint(tmp_path), cache_root=cache)
    inventory = cached_artifact_inventory(pin, cache_root=cache)
    assert inventory[0].relative_path == "weights.bin"
    assert inventory[0].sha256 == sha256_file(artifact)
    with pytest.raises(ValueError, match="40-character"):
        RepositoryPin(
            repository_id="fixture/model",
            revision="not-a-sha",
            license="mit",
            artifacts=(),
        )


def test_cache_cannot_be_inside_repository(tmp_path):
    pin = _checkpoint(tmp_path / "repo")
    with pytest.raises(CheckpointPinError, match="inside"):
        pin.assert_cache_outside_repository(tmp_path / "repo")


def test_transformers_5_5_4_compatibility_boundary():
    environment = runtime_environment(
        cache_path="/tmp/advisorai-runtime-cache",
        runner_version="fixture",
    )
    compatible = RuntimeEnvironment.model_validate(
        {
            **environment.model_dump(),
            "transformers_version": REQUIRED_TRANSFORMERS_VERSION,
            "huggingface_hub_version": REQUIRED_HUGGINGFACE_HUB_VERSION,
        }
    )
    compatible.assert_transformers_baseline(requires_transformers=True)
    incompatible = RuntimeEnvironment.model_validate(
        {
            **compatible.model_dump(),
            "transformers_version": "4.57.6",
        }
    )
    with pytest.raises(Exception, match="5.5.4"):
        incompatible.assert_transformers_baseline(requires_transformers=True)


def test_invalid_output_and_nonfinite_values_are_rejected():
    with pytest.raises(Exception, match="NaN"):
        validate_model_output(ModelTask.FORECAST, (float("nan"),))
    with pytest.raises(Exception, match="label"):
        validate_model_output(ModelTask.FINANCE_SENTIMENT, {"label": "buy", "confidence": 0.9})
    with pytest.raises(Exception, match="confidence"):
        validate_model_output(ModelTask.FINANCE_SENTIMENT, {"label": "positive", "confidence": 2})
    assert validate_model_output(
        ModelTask.FINANCE_SENTIMENT,
        {"label": "neutral", "confidence": 0.5},
    ) == "sentiment(label,confidence)"


def test_missing_runner_is_quarantined_without_fallback():
    dataset = BenchmarkDataset.synthetic_forecast()
    result = run_runtime_qualification(
        _forecast_candidate(),
        runner=None,
        dataset=dataset,
        sample_input=dataset.inputs[:4],
        batch_input=(dataset.inputs[:4],),
    )
    assert result.status == QualificationStatus.QUARANTINED
    assert "runner/checkpoint" in result.failure_reason


def test_no_silent_fallback_between_model_families():
    dataset = BenchmarkDataset.synthetic_forecast()
    result = run_runtime_qualification(
        _forecast_candidate(),
        runner=_runner(ModelFamily.DRIFT.value),
        dataset=dataset,
        sample_input=dataset.inputs[:4],
        batch_input=(dataset.inputs[:4],),
    )
    assert result.status == QualificationStatus.QUARANTINED
    assert "does not match" in result.failure_reason


def test_offline_cached_inference_requires_offline_runner_and_is_repeatable(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    weights = cache / "weights.bin"
    weights.write_bytes(b"cached")
    pin = _checkpoint(tmp_path, digest=sha256_file(weights), family=ModelFamily.NAIVE.value)
    candidate = _forecast_candidate(checkpoint=pin)
    dataset = BenchmarkDataset.synthetic_forecast()
    environment = RuntimeEnvironment.model_validate(
        {
            **runtime_environment(cache_path=str(cache), runner_version="fixture").model_dump(),
            "transformers_version": REQUIRED_TRANSFORMERS_VERSION,
            "huggingface_hub_version": REQUIRED_HUGGINGFACE_HUB_VERSION,
        }
    )
    result = run_runtime_qualification(
        candidate,
        runner=_runner(),
        dataset=dataset,
        sample_input=dataset.inputs[:4],
        batch_input=(dataset.inputs[:4], dataset.inputs[4:8]),
        environment=environment,
        repository_root=tmp_path / "repository",
    )
    assert result.status == QualificationStatus.MEASURED
    assert result.offline_cached_inference is True
    assert result.repeated_outputs_equal is True
    assert result.nan_inf_rejection_passed is True
    assert result.resource.unload_succeeded is True
    assert result.observed_artifacts[0].sha256 == sha256_file(weights)


def test_network_attempt_after_cache_fails_closed(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    weights = cache / "weights.bin"
    weights.write_bytes(b"cached")
    pin = _checkpoint(tmp_path, digest=sha256_file(weights), family=ModelFamily.NAIVE.value)
    candidate = _forecast_candidate(checkpoint=pin)
    dataset = BenchmarkDataset.synthetic_forecast()
    environment = RuntimeEnvironment.model_validate(
        {
            **runtime_environment(cache_path=str(cache), runner_version="fixture").model_dump(),
            "transformers_version": REQUIRED_TRANSFORMERS_VERSION,
            "huggingface_hub_version": REQUIRED_HUGGINGFACE_HUB_VERSION,
        }
    )
    result = run_runtime_qualification(
        candidate,
        runner=_runner(network=True),
        dataset=dataset,
        sample_input=dataset.inputs[:4],
        batch_input=(dataset.inputs[:4],),
        environment=environment,
        repository_root=tmp_path / "repository",
    )
    assert result.status == QualificationStatus.FAILED
    assert result.failure_reason == "runner failure: OSError"


def test_missing_or_corrupt_checkpoint_is_quarantined_with_reason(tmp_path):
    dataset = BenchmarkDataset.synthetic_forecast()
    missing_pin = _checkpoint(tmp_path, digest="a" * 64, family=ModelFamily.NAIVE.value)
    candidate = _forecast_candidate(checkpoint=missing_pin)
    environment = RuntimeEnvironment.model_validate(
        {
            **runtime_environment(cache_path=str(tmp_path / "missing"), runner_version="fixture").model_dump(),
            "transformers_version": REQUIRED_TRANSFORMERS_VERSION,
            "huggingface_hub_version": REQUIRED_HUGGINGFACE_HUB_VERSION,
        }
    )
    missing = run_runtime_qualification(
        candidate,
        runner=_runner(),
        dataset=dataset,
        sample_input=dataset.inputs[:4],
        batch_input=(dataset.inputs[:4],),
        environment=environment,
        repository_root=tmp_path / "repository",
    )
    assert missing.status == QualificationStatus.QUARANTINED
    assert missing.missing_checkpoint_quarantined is True

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "weights.bin").write_bytes(b"corrupt")
    corrupt_pin = _checkpoint(tmp_path, digest="b" * 64, family=ModelFamily.NAIVE.value)
    corrupt_candidate = _forecast_candidate(checkpoint=corrupt_pin)
    corrupt_environment = RuntimeEnvironment.model_validate(
        {
            **runtime_environment(cache_path=str(cache), runner_version="fixture").model_dump(),
            "transformers_version": REQUIRED_TRANSFORMERS_VERSION,
            "huggingface_hub_version": REQUIRED_HUGGINGFACE_HUB_VERSION,
        }
    )
    corrupt = run_runtime_qualification(
        corrupt_candidate,
        runner=_runner(),
        dataset=dataset,
        sample_input=dataset.inputs[:4],
        batch_input=(dataset.inputs[:4],),
        environment=corrupt_environment,
        repository_root=tmp_path / "repository",
    )
    assert corrupt.status == QualificationStatus.QUARANTINED
    assert corrupt.corrupt_checkpoint_quarantined is True


def test_resource_contract_rejects_invalid_latency_and_peak():
    with pytest.raises(ValueError, match="p95"):
        RuntimeResourceResult(
            cold_load_ms=1,
            warm_inference_p50_ms=2,
            warm_inference_p95_ms=1,
            batch_inference_ms=1,
            batch_size=1,
            batch_throughput_per_second=1,
            rss_before_mib=1,
            rss_after_load_mib=2,
            rss_peak_mib=2,
            rss_after_unload_mib=1,
            unload_succeeded=True,
            resource_limit_passed=True,
        )


def test_gpu_resource_ceiling_and_single_lease_are_explicit():
    ceiling = ResourceCeiling(max_rss_mib=10, max_vram_mib=20)
    assert ceiling.gpu_models_at_once == 1


def test_forecast_benchmark_contains_all_mandatory_baselines():
    evaluations = run_forecast_baseline_benchmark(BenchmarkDataset.synthetic_forecast())
    names = {evaluation.model_name for evaluation in evaluations}
    assert {"naive", "drift", "seasonal", "linear", "lightgbm"} == names
    assert all(evaluation.past_only for evaluation in evaluations)
    assert all(evaluation.mae.is_finite() and evaluation.rmse.is_finite() for evaluation in evaluations)


def test_forecast_candidate_benchmark_keeps_single_series_non_authoritative():
    dataset = BenchmarkDataset.synthetic_forecast()
    result = run_forecast_candidate_benchmark(
        _forecast_candidate(),
        runner=_runner(),
        dataset=dataset,
    )
    assert result.status == QualificationStatus.MEASURED
    assert result.forecast_evaluations
    assert result.forecast_evaluations[0].adds_marginal_value is False
    assert "single_dataset_no_admission" in result.forecast_evaluations[0].regime_failures


def test_short_result_projects_into_existing_pending_bakeoff_gate():
    candidate = _forecast_candidate()
    dataset = BenchmarkDataset.synthetic_forecast()
    result = run_runtime_qualification(
        candidate,
        runner=_runner(),
        dataset=dataset,
        sample_input=dataset.inputs[:4],
        batch_input=(dataset.inputs[:4],),
    )
    gate = project_bakeoff_gate((result,))
    assert gate.decision == "pending"
    assert gate.results[0].candidate_name == candidate.name


def test_tspulse_projects_as_feature_compute_not_forecast():
    result = run_tspulse_qualification(runner=None)
    assert result.to_bakeoff_result().kind == ComponentKind.FEATURE_COMPUTE


def test_measured_result_requires_complete_evidence():
    candidate = _forecast_candidate()
    dataset = BenchmarkDataset.synthetic_forecast()
    measured = run_runtime_qualification(
        candidate,
        runner=_runner(),
        dataset=dataset,
        sample_input=dataset.inputs[:4],
        batch_input=(dataset.inputs[:4],),
    )
    with pytest.raises(ValueError, match="complete offline"):
        type(measured).model_validate(
            {**measured.model_dump(), "batch_completed": False}
        )


def test_manifest_is_canonical_and_immutable(tmp_path):
    candidate = _forecast_candidate()
    dataset = BenchmarkDataset.synthetic_forecast()
    result = run_runtime_qualification(
        candidate,
        runner=_runner(),
        dataset=dataset,
        sample_input=dataset.inputs[:4],
        batch_input=(dataset.inputs[:4],),
    )
    path = write_qualification_manifest(result, tmp_path)
    assert path.exists()
    assert result.manifest_hash is None
    assert len(manifest_bytes(result)) > 100
    changed = result.model_copy(update={"warnings": ("changed",)})
    with pytest.raises(FileExistsError, match="immutable"):
        write_qualification_manifest(changed, tmp_path)


def test_finbert_fixture_is_fixed_and_labeled():
    dataset = BenchmarkDataset.finbert_fixture()
    assert dataset.task == ModelTask.FINANCE_SENTIMENT
    assert len(dataset.public_text_fixture) == 5
    assert {label for _text, label in dataset.public_text_fixture} == {"positive", "negative", "neutral"}


def test_finbert_and_tspulse_helpers_keep_roles_explicit():
    finbert = run_finbert_qualification(runner=None)
    tspulse = run_tspulse_qualification(runner=None)
    assert finbert.status == QualificationStatus.QUARANTINED
    assert finbert.candidate.task == ModelTask.FINANCE_SENTIMENT
    assert tspulse.status == QualificationStatus.QUARANTINED
    assert tspulse.candidate.task == ModelTask.TSPULSE_FEATURES
