from __future__ import annotations

import json
import platform
import socket
import subprocess
import sys
import venv
from datetime import UTC, datetime
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
    LicenseAdmission,
    LicenseAdmissionStatus,
    ModelFamily,
    ModelTask,
    QualificationStatus,
    RepeatabilityPolicy,
    RepositoryPin,
    ResourceCeiling,
    RuntimeAdmissionStatus,
    RuntimeEnvironment,
    RuntimePin,
    RuntimeResourceResult,
    _environment_fingerprint,
    _launcher_identity_hash,
    _worker_runner_hash,
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
    validate_candidate_batch_output,
    validate_model_batch_output,
    validate_model_output,
    verify_checkpoint_artifacts,
    verify_runtime_pin,
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
        license_admission=LicenseAdmission(
            status=LicenseAdmissionStatus.APPROVED,
            license_identifier="Apache-2.0",
            reviewed_at=datetime(2026, 8, 7, tzinfo=UTC),
            evidence_reference="fixture://license",
        ),
    )


def _forecast_candidate(*, checkpoint: CheckpointPin | None = None) -> CandidateSpec:
    return CandidateSpec(
        name="fixture-forecast",
        family=ModelFamily.NAIVE,
        task=ModelTask.FORECAST,
        external_checkpoint=checkpoint,
        requires_transformers=checkpoint is not None,
        output_schema="forecast[1]",
        runtime_pin=(
            RuntimePin(
                project="fixture-runner",
                version_or_commit="fixture-v1",
                python_constraint=">=3.12,<3.13",
                dependencies=("fixture==1.0",),
                lock_hash="c" * 64,
                environment_path="/tmp/advisorai-fixture-runtime",
                status="pending",
                evidence_reference="fixture://runtime",
            )
            if checkpoint is not None
            else None
        ),
    )


def _isolated_runtime(
    tmp_path: Path,
    *,
    worker_kind: str = "qualification",
    dependencies: tuple[str, ...] = (),
    python_constraint: str = ">=3.12,<3.13",
) -> RuntimePin:
    runtime = tmp_path / "isolated-runtime"
    venv.EnvBuilder(with_pip=False, clear=True, symlinks=True).create(runtime)
    executable = runtime / "bin" / "python"
    lock = runtime / "uv.lock"
    lock.write_text("fixture-lock-v1\n", encoding="utf-8")
    worker = Path("scripts/runtime_qualification_worker.py").resolve()
    inventory = subprocess.run(
        [str(executable), "-I", str(worker), "--inventory"],
        check=True,
        capture_output=True,
        text=True,
    )
    manifest = runtime / "installed-environment.txt"
    manifest.write_text(inventory.stdout.strip(), encoding="utf-8")
    lock_hash = sha256_file(lock)
    manifest_hash = sha256_file(manifest)
    runner_version = "fixture-worker-v1"
    runner_hash = _worker_runner_hash(worker, runner_version)
    binary = executable.resolve()
    launcher_target = str(binary) if executable.is_symlink() else None
    cfg = runtime / "pyvenv.cfg"
    base_prefix = Path(sys.base_prefix).resolve()
    fingerprint = _environment_fingerprint(
        sys_executable=str(executable),
        python_version=platform.python_version(),
        package_versions={},
        torch_version=None,
        cuda_version=None,
        runtime_lock_hash=lock_hash,
        installed_environment_sha256=manifest_hash,
        sys_prefix=str(runtime.resolve()),
        sys_base_prefix=str(base_prefix),
    )
    return RuntimePin(
        project="fixture-worker",
        version_or_commit="fixture-v1",
        python_constraint=python_constraint,
        dependencies=dependencies,
        lock_hash=lock_hash,
        lock_artifact_path=str(lock),
        installed_environment_manifest_path=str(manifest),
        installed_environment_sha256=manifest_hash,
        environment_fingerprint=fingerprint,
        python_executable=str(executable),
        python_executable_hash=sha256_file(binary),
        python_launcher=str(executable),
        python_launcher_hash=_launcher_identity_hash(executable),
        python_launcher_target=launcher_target,
        resolved_python_binary_hash=sha256_file(binary),
        pyvenv_cfg_path=str(cfg),
        pyvenv_cfg_hash=sha256_file(cfg),
        environment_path=str(runtime),
        worker_script=str(worker),
        worker_kind=worker_kind,
        runner_version=runner_version,
        runner_hash=runner_hash,
        status=RuntimeAdmissionStatus.APPROVED,
        evidence_reference="fixture://runtime-lock",
    )


def _isolated_candidate(
    tmp_path: Path,
    *,
    worker_kind: str = "qualification",
    dependencies: tuple[str, ...] = (),
    family: ModelFamily = ModelFamily.NAIVE,
    task: ModelTask = ModelTask.FORECAST,
    repeatability_policy: RepeatabilityPolicy = RepeatabilityPolicy.DETERMINISTIC_REQUIRED,
    repeatability_seed: int | None = None,
    python_constraint: str = ">=3.12,<3.13",
) -> CandidateSpec:
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    weights = cache / "weights.bin"
    weights.write_bytes(b"cached-fixture")
    checkpoint = CheckpointPin(
        model_family=family.value,
        repository=RepositoryPin(
            repository_id="fixture/model",
            revision="a" * 40,
            license="apache-2.0",
            artifacts=(ArtifactPin(relative_path="weights.bin", sha256=sha256_file(weights)),),
        ),
        cache_path=str(cache),
        license_admission=LicenseAdmission(
            status=LicenseAdmissionStatus.APPROVED,
            license_identifier="Apache-2.0",
            reviewed_at=datetime(2026, 8, 7, tzinfo=UTC),
            evidence_reference="fixture://model-license",
        ),
    )
    return CandidateSpec(
        name="isolated-fixture",
        family=family,
        task=task,
        external_checkpoint=checkpoint,
        requires_transformers=False,
        output_schema="sentiment(label,confidence)" if task == ModelTask.FINANCE_SENTIMENT else "forecast[1]",
        repeatability_policy=repeatability_policy,
        repeatability_seed=repeatability_seed,
        runtime_pin=_isolated_runtime(
            tmp_path,
            worker_kind=worker_kind,
            dependencies=dependencies,
            python_constraint=python_constraint,
        ),
    )


def _runner(family: str = ModelFamily.NAIVE.value, *, network: bool = False) -> FunctionalRunner:
    def load(_checkpoint, offline):
        assert offline is True
        return object()

    def infer(_model, _payload):
        if network:
            socket.create_connection(("example.invalid", 443))
        if (
            isinstance(_payload, (tuple, list))
            and _payload
            and isinstance(_payload[0], (tuple, list))
        ):
            return ((1.0,),) * len(_payload)
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
    assert by_name["finbert-family"].external_checkpoint.license_admission.status == LicenseAdmissionStatus.PENDING
    assert by_name["finbert-family"].runtime_pin.status.value == "pending"
    assert "flax_model.msgpack" not in {
        item.relative_path
        for item in by_name["finbert-family"].external_checkpoint.repository.all_artifacts
    }
    assert "training_args.bin" not in {
        item.relative_path
        for item in by_name["ttm-r2"].external_checkpoint.repository.all_artifacts
    }


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


def test_unexpected_loadable_artifact_is_rejected(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    artifact = cache / "weights.bin"
    artifact.write_bytes(b"model-fixture")
    (cache / "unexpected.h5").write_bytes(b"unreviewed")
    pin = _checkpoint(tmp_path, digest=sha256_file(artifact))
    with pytest.raises(CheckpointIntegrityError, match="unexpected loadable"):
        verify_checkpoint_artifacts(pin, cache_root=cache)


def test_cache_cannot_be_inside_repository(tmp_path):
    pin = _checkpoint(tmp_path / "repo")
    with pytest.raises(CheckpointPinError, match="inside"):
        pin.assert_cache_outside_repository(tmp_path / "repo")


def test_execution_runtime_environment_cannot_be_inside_repository(tmp_path):
    pin = RuntimePin(
        project="fixture",
        version_or_commit="1.0.0",
        python_constraint=">=3.12,<3.13",
        dependencies=("fixture==1.0",),
        lock_hash="a" * 64,
        environment_path=str(tmp_path / "repo" / "venv"),
        status="pending",
        evidence_reference="fixture://runtime",
    )
    with pytest.raises(Exception, match="outside"):
        pin.assert_environment_outside_repository(tmp_path / "repo")


def test_approved_runtime_requires_real_lock_and_worker_identity():
    with pytest.raises(ValueError, match="immutable worker identity"):
        RuntimePin(
            project="fixture",
            version_or_commit="1.0.0",
            python_constraint=">=3.12,<3.13",
            lock_hash="a" * 64,
            environment_path="/tmp/advisorai-fixture-runtime",
            status="approved",
            evidence_reference="fixture://runtime",
        )


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


def test_singleton_and_batch_output_contracts_are_separate():
    assert validate_model_batch_output(
        ModelTask.FINANCE_SENTIMENT,
        (
            {"label": "positive", "confidence": 0.9},
            {"label": "negative", "confidence": 0.8},
        ),
        expected_batch_size=2,
    ) == "batch[2]<sentiment(label,confidence)>"
    assert validate_model_batch_output(
        ModelTask.TSPULSE_FEATURES,
        ((0.1, 0.2), (0.3, 0.4)),
        expected_batch_size=2,
    ) == "batch[2]<features[2]>"
    with pytest.raises(Exception, match="cardinality"):
        validate_model_batch_output(
            ModelTask.FINANCE_SENTIMENT,
            ({"label": "positive", "confidence": 0.9},),
            expected_batch_size=2,
        )


def test_candidate_schema_is_enforced_for_batches():
    candidate = CandidateSpec(
        name="schema-fixture",
        family=ModelFamily.NAIVE,
        task=ModelTask.FORECAST,
        output_schema="forecast[1]",
    )
    assert validate_candidate_batch_output(candidate, ((1.0,),), expected_batch_size=1)
    with pytest.raises(Exception, match="horizon"):
        validate_candidate_batch_output(candidate, ((1.0, 2.0),), expected_batch_size=1)


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


def test_unapproved_license_cannot_become_measured():
    result = run_finbert_qualification(runner=_runner(ModelFamily.FINBERT.value))
    assert result.status == QualificationStatus.QUARANTINED
    assert "license admission" in result.failure_reason
    assert result.to_bakeoff_result().privacy_passed is None


def test_finbert_fixture_scores_labels_and_batch_throughput():
    dataset = BenchmarkDataset.finbert_fixture()
    candidate = CandidateSpec(
        name="finbert-fixture",
        family=ModelFamily.FINBERT,
        task=ModelTask.FINANCE_SENTIMENT,
        output_schema="sentiment(label,confidence)",
    )
    expected = tuple(label for _text, label in dataset.public_text_fixture)
    runner = FunctionalRunner(
        model_family=ModelFamily.FINBERT.value,
        infer_fn=lambda _model, payload: (
            [
                {"label": label, "score": 0.8}
                for label in expected
            ]
            if isinstance(payload, tuple)
            else {"label": expected[0], "score": 0.8}
        ),
    )
    result = run_runtime_qualification(
        candidate,
        runner=runner,
        dataset=dataset,
        sample_input=dataset.public_text_fixture[0][0],
        batch_input=tuple(text for text, _label in dataset.public_text_fixture),
    )
    assert result.status == QualificationStatus.MEASURED
    assert result.finbert_accuracy == 1
    assert result.finbert_mean_confidence == 0.8
    assert result.resource.batch_throughput_per_second > 0


def test_stochastic_repeatability_policy_does_not_require_equal_bytes():
    candidate = CandidateSpec(
        name="stochastic-fixture",
        family=ModelFamily.KRONOS_MINI,
        task=ModelTask.FORECAST,
        output_schema="forecast[1]",
        repeatability_policy="stochastic_characterized",
    )
    counter = {"value": 0}

    def infer(_model, payload):
        if isinstance(payload, tuple) and payload and isinstance(payload[0], tuple):
            return tuple((float(index),) for index, _item in enumerate(payload))
        counter["value"] += 1
        return (float(counter["value"]),)

    result = run_runtime_qualification(
        candidate,
        runner=FunctionalRunner(model_family=ModelFamily.KRONOS_MINI.value, infer_fn=infer),
        dataset=BenchmarkDataset.synthetic_forecast(),
        sample_input=(1.0, 2.0),
        batch_input=((1.0, 2.0), (3.0, 4.0)),
    )
    assert result.status == QualificationStatus.MEASURED
    assert result.repeated_outputs_equal is False
    assert result.stochastic_characterized is True
    assert result.stochastic_unique_output_count > 1
    assert result.stochastic_variation_observed is True


def test_stochastic_declaration_alone_does_not_pass_characterization():
    candidate = CandidateSpec(
        name="constant-stochastic-fixture",
        family=ModelFamily.KRONOS_MINI,
        task=ModelTask.FORECAST,
        output_schema="forecast[1]",
        repeatability_policy=RepeatabilityPolicy.STOCHASTIC_CHARACTERIZED,
    )
    result = run_runtime_qualification(
        candidate,
        runner=FunctionalRunner(
            model_family=ModelFamily.KRONOS_MINI.value,
            infer_fn=lambda _model, payload: (
                ((1.0,),) * len(payload)
                if isinstance(payload, tuple) and payload and isinstance(payload[0], tuple)
                else (1.0,)
            ),
        ),
        dataset=BenchmarkDataset.synthetic_forecast(),
        sample_input=(1.0, 2.0),
        batch_input=((1.0, 2.0), (3.0, 4.0)),
    )
    assert result.status == QualificationStatus.FAILED
    assert "characterization" in result.failure_reason


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


def test_offline_cached_inference_requires_isolated_runtime_and_is_repeatable(tmp_path):
    candidate = _isolated_candidate(tmp_path)
    dataset = BenchmarkDataset.synthetic_forecast()
    result = run_runtime_qualification(
        candidate,
        runner=None,
        dataset=dataset,
        sample_input=dataset.inputs[:4],
        batch_input=(dataset.inputs[:4], dataset.inputs[4:8]),
        repository_root=tmp_path / "repository",
    )
    assert result.status == QualificationStatus.MEASURED
    assert result.offline_cached_inference is True
    assert result.repeated_outputs_equal is True
    assert result.nan_inf_rejection_passed is True
    assert result.resource.unload_succeeded is True
    assert result.observed_artifacts[0].sha256 == candidate.external_checkpoint.repository.artifacts[0].sha256
    assert result.environment.sys_executable == candidate.runtime_pin.python_executable


def test_network_attempt_after_cache_fails_closed(tmp_path):
    candidate = _isolated_candidate(tmp_path, worker_kind="network_attempt")
    dataset = BenchmarkDataset.synthetic_forecast()
    result = run_runtime_qualification(
        candidate,
        runner=None,
        dataset=dataset,
        sample_input=dataset.inputs[:4],
        batch_input=(dataset.inputs[:4],),
        repository_root=tmp_path / "repository",
    )
    assert result.status == QualificationStatus.FAILED
    assert result.network_access_attempted is True
    assert result.to_bakeoff_result().privacy_passed is False


def test_uncaught_network_attempt_from_inference_emits_privacy_evidence(tmp_path):
    candidate = _isolated_candidate(tmp_path, worker_kind="fixture_network_inference")
    dataset = BenchmarkDataset.synthetic_forecast()
    result = run_runtime_qualification(
        candidate,
        runner=None,
        dataset=dataset,
        sample_input=dataset.inputs[:4],
        batch_input=(dataset.inputs[:4],),
        repository_root=tmp_path / "repository",
    )
    assert result.status == QualificationStatus.FAILED
    assert result.network_access_attempted is True
    assert result.to_bakeoff_result().privacy_passed is False


def test_malformed_success_envelope_fails_without_index_error(tmp_path):
    candidate = _isolated_candidate(tmp_path, worker_kind="malformed_success")
    dataset = BenchmarkDataset.synthetic_forecast()
    result = run_runtime_qualification(
        candidate,
        runner=None,
        dataset=dataset,
        sample_input=dataset.inputs[:4],
        batch_input=(dataset.inputs[:4],),
        repository_root=tmp_path / "repository",
    )
    assert result.status == QualificationStatus.FAILED
    assert "QualificationError" in result.failure_reason


def test_seeded_worker_reports_applied_seed_for_every_repeat(tmp_path):
    candidate = _isolated_candidate(
        tmp_path,
        repeatability_policy=RepeatabilityPolicy.SEEDED_REPRODUCIBLE,
        repeatability_seed=17,
    )
    dataset = BenchmarkDataset.synthetic_forecast()
    result = run_runtime_qualification(
        candidate,
        runner=None,
        dataset=dataset,
        sample_input=dataset.inputs[:4],
        batch_input=(dataset.inputs[:4],),
        repository_root=tmp_path / "repository",
    )
    assert result.status == QualificationStatus.MEASURED
    assert result.applied_seeds == (17, 17, 17)


def test_runtime_lock_hash_mismatch_is_quarantined_before_inference(tmp_path):
    candidate = _isolated_candidate(tmp_path)
    result = run_runtime_qualification(
        candidate,
        runner=None,
        dataset=BenchmarkDataset.synthetic_forecast(),
        sample_input=(1.0,),
        batch_input=((1.0,),),
        environment=runtime_environment(
            cache_path=str(tmp_path / "cache"),
            runner_version="fixture",
            runtime_lock_hash="d" * 64,
        ),
        repository_root=tmp_path / "repository",
    )
    assert result.status == QualificationStatus.QUARANTINED
    assert "lock hash" in result.failure_reason


def test_isolated_runtime_rejects_fake_lock_hash_and_wrong_executable(tmp_path):
    candidate = _isolated_candidate(tmp_path)
    pin = candidate.runtime_pin
    fake = pin.model_copy(update={"lock_hash": "f" * 64})
    with pytest.raises(Exception, match="lock artifact hash"):
        verify_runtime_pin(fake)
    wrong = pin.model_copy(update={"python_executable_hash": "e" * 64})
    with pytest.raises(Exception, match="executable hash"):
        verify_runtime_pin(wrong)
    missing_path = str(Path(pin.environment_path) / "bin" / "missing")
    missing = pin.model_copy(update={"python_launcher": missing_path, "python_executable": missing_path})
    with pytest.raises(Exception, match="missing"):
        verify_runtime_pin(missing)


def test_isolated_runtime_package_identity_mismatch_is_rejected(tmp_path):
    candidate = _isolated_candidate(tmp_path, dependencies=("fixture-package==9.9",))
    result = run_runtime_qualification(
        candidate,
        runner=None,
        dataset=BenchmarkDataset.synthetic_forecast(),
        sample_input=(1.0,),
        batch_input=((1.0,),),
        repository_root=tmp_path / "repository",
    )
    assert result.status == QualificationStatus.FAILED
    assert "isolated worker" in result.failure_reason


def test_isolated_worker_enforces_python_constraint_before_inference(tmp_path):
    candidate = _isolated_candidate(tmp_path, python_constraint="<3.0")
    dataset = BenchmarkDataset.synthetic_forecast()
    result = run_runtime_qualification(
        candidate,
        runner=None,
        dataset=dataset,
        sample_input=(1.0,),
        batch_input=((1.0,),),
        repository_root=tmp_path / "repository",
    )
    assert result.status == QualificationStatus.FAILED
    assert "isolated worker" in result.failure_reason


def test_installed_environment_attestation_is_immutable(tmp_path):
    candidate = _isolated_candidate(tmp_path)
    manifest = Path(candidate.runtime_pin.installed_environment_manifest_path)
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(Exception, match="installed-environment manifest hash"):
        verify_runtime_pin(candidate.runtime_pin)


def test_isolated_runtime_worker_identity_and_boundary(tmp_path):
    candidate = _isolated_candidate(tmp_path)
    dataset = BenchmarkDataset.synthetic_forecast()
    result = run_runtime_qualification(
        candidate,
        runner=None,
        dataset=dataset,
        sample_input=dataset.inputs[:4],
        batch_input=(dataset.inputs[:4], dataset.inputs[4:8]),
        repository_root=tmp_path / "repository",
    )
    assert result.status == QualificationStatus.MEASURED
    assert result.environment.sys_executable == candidate.runtime_pin.python_executable
    assert result.environment.sys_executable != sys.executable
    assert result.resource is not None
    assert result.resource.rss_peak_mib >= result.resource.rss_before_mib


def test_isolated_runtime_rejects_wrong_reported_sys_executable(tmp_path):
    candidate = _isolated_candidate(tmp_path)
    pin = candidate.runtime_pin
    worker = Path(pin.worker_script)
    wrong_worker = tmp_path / "wrong-worker.py"
    wrong_worker.write_text(
        worker.read_text(encoding="utf-8").replace(
            '"sys_executable": str(launcher),',
            '"sys_executable": "/wrong/python",',
        ),
        encoding="utf-8",
    )
    wrong_pin = pin.model_copy(
        update={
            "worker_script": str(wrong_worker),
            "runner_hash": _worker_runner_hash(wrong_worker, pin.runner_version),
        }
    )
    result = run_runtime_qualification(
        candidate.model_copy(update={"runtime_pin": wrong_pin}),
        runner=None,
        dataset=BenchmarkDataset.synthetic_forecast(),
        sample_input=(1.0,),
        batch_input=((1.0,),),
        repository_root=tmp_path / "repository",
    )
    assert result.status == QualificationStatus.FAILED
    assert "isolated worker" in result.failure_reason


def test_external_in_process_runner_is_rejected(tmp_path):
    candidate = _isolated_candidate(tmp_path)
    result = run_runtime_qualification(
        candidate,
        runner=_runner(),
        dataset=BenchmarkDataset.synthetic_forecast(),
        sample_input=(1.0,),
        batch_input=((1.0,),),
        repository_root=tmp_path / "repository",
    )
    assert result.status == QualificationStatus.QUARANTINED
    assert "in-process" in result.failure_reason


def test_tokenizer_license_pending_or_rejected_quarantines_before_worker(tmp_path):
    candidate = _isolated_candidate(tmp_path)
    tokenizer = RepositoryPin(
        repository_id="fixture/tokenizer",
        revision="b" * 40,
        license="not-declared",
        artifacts=(ArtifactPin(relative_path="tokenizer.json", sha256="c" * 64),),
    )
    for status in (LicenseAdmissionStatus.PENDING, LicenseAdmissionStatus.REJECTED):
        checkpoint = candidate.external_checkpoint.model_copy(
            update={"tokenizer": tokenizer, "tokenizer_license_admission": LicenseAdmission(status=status)}
        )
        candidate_with_tokenizer = candidate.model_copy(update={"external_checkpoint": checkpoint})
        result = run_runtime_qualification(
            candidate_with_tokenizer,
            runner=None,
            dataset=BenchmarkDataset.synthetic_forecast(),
            sample_input=(1.0,),
            batch_input=((1.0,),),
            repository_root=tmp_path / "repository",
        )
        assert result.status == QualificationStatus.QUARANTINED
        assert "tokenizer license" in result.failure_reason


@pytest.mark.parametrize("suffix", [".py", ".so", ".dll", ".sh"])
def test_executable_model_cache_artifacts_are_rejected(tmp_path, suffix):
    cache = tmp_path / "cache"
    cache.mkdir()
    weights = cache / "weights.bin"
    weights.write_bytes(b"model-fixture")
    (cache / f"unreviewed{suffix}").write_bytes(b"code")
    pin = _checkpoint(tmp_path, digest=sha256_file(weights), family=ModelFamily.NAIVE.value)
    with pytest.raises(CheckpointIntegrityError, match="unexpected loadable"):
        verify_checkpoint_artifacts(pin, cache_root=cache)


def test_missing_or_corrupt_checkpoint_is_quarantined_with_reason(tmp_path):
    dataset = BenchmarkDataset.synthetic_forecast()
    candidate = _isolated_candidate(tmp_path)
    missing_pin = candidate.external_checkpoint.model_copy(
        update={"cache_path": str(tmp_path / "missing")}
    )
    candidate = candidate.model_copy(update={"external_checkpoint": missing_pin})
    missing = run_runtime_qualification(
        candidate,
        runner=None,
        dataset=dataset,
        sample_input=dataset.inputs[:4],
        batch_input=(dataset.inputs[:4],),
        repository_root=tmp_path / "repository",
    )
    assert missing.status == QualificationStatus.QUARANTINED
    assert missing.missing_checkpoint_quarantined is True

    corrupt_candidate = _isolated_candidate(tmp_path / "corrupt")
    (Path(corrupt_candidate.external_checkpoint.cache_path) / "weights.bin").write_bytes(b"corrupt")
    corrupt_pin = corrupt_candidate.external_checkpoint.model_copy(
        update={
            "repository": corrupt_candidate.external_checkpoint.repository.model_copy(
                update={
                    "artifacts": (
                        ArtifactPin(relative_path="weights.bin", sha256="b" * 64),
                    )
                }
            )
        }
    )
    corrupt_candidate = corrupt_candidate.model_copy(update={"external_checkpoint": corrupt_pin})
    corrupt = run_runtime_qualification(
        corrupt_candidate,
        runner=None,
        dataset=dataset,
        sample_input=dataset.inputs[:4],
        batch_input=(dataset.inputs[:4],),
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


def test_resource_ceiling_failure_preserves_measurements():
    result = run_runtime_qualification(
        _forecast_candidate(),
        runner=_runner(),
        dataset=BenchmarkDataset.synthetic_forecast(),
        sample_input=(1.0,),
        batch_input=((1.0,),),
        ceiling=ResourceCeiling(max_rss_mib=1),
    )
    assert result.status == QualificationStatus.FAILED
    assert result.resource is not None
    assert result.resource.resource_limit_passed is False


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


def test_smoke_script_scopes_evidence_to_an_immutable_run(tmp_path):
    output = tmp_path / "evidence"
    subprocess.run(
        [
            sys.executable,
            "scripts/qualify_model_runtimes.py",
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    latest = json.loads((output / "latest.json").read_text(encoding="utf-8"))
    run_dir = output / latest["run_id"]
    assert run_dir.is_dir()
    assert (run_dir / "index.json").exists()
    assert (run_dir / "bakeoff-gate.json").exists()
    assert json.loads((output / "index.json").read_text(encoding="utf-8"))["run_id"] == latest["run_id"]


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
