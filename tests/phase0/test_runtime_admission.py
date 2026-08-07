from __future__ import annotations

import json
import socket
import venv
from datetime import UTC, datetime
from pathlib import Path

import pytest

from advisorai.phase0.runtime_qualification import (
    ArtifactPin,
    CandidateSpec,
    CheckpointPin,
    InvalidModelOutputError,
    LocalCandidateAdmission,
    ModelFamily,
    ModelTask,
    NetworkAccessAttemptError,
    QualificationError,
    RepositoryPin,
    RuntimeAdmissionStatus,
    apply_local_candidate_admission,
    default_runtime_candidates,
    freeze_runtime_pin,
    network_blocked,
    sha256_file,
    validate_candidate_output,
    verify_runtime_pin,
)


def _runtime(tmp_path: Path):
    environment = tmp_path / "runtime"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    lock = tmp_path / "runtime.lock"
    lock.write_text("fixture==1.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    evidence = tmp_path / "evidence"
    pin = freeze_runtime_pin(
        project="fixture",
        version_or_commit="1.0.0",
        python_constraint=">=3.12,<3.13",
        dependencies=(),
        environment_path=environment,
        lock_artifact_path=lock,
        worker_script=Path("scripts/runtime_qualification_worker.py").resolve(),
        worker_kind="qualification",
        runner_version="fixture-v1",
        admission_directory=evidence,
        repository_root=Path.cwd(),
    )
    return pin, evidence


def test_freeze_runtime_pin_attests_real_venv_and_complete_inventory(tmp_path):
    pin, evidence = _runtime(tmp_path)

    assert pin.status == RuntimeAdmissionStatus.APPROVED
    assert verify_runtime_pin(pin, repository_root=Path.cwd()) == Path(pin.python_launcher)
    inventory = json.loads((evidence / "installed-environment.json").read_text(encoding="utf-8"))
    assert isinstance(inventory, list)
    assert pin.installed_environment_sha256 == sha256_file(evidence / "installed-environment.json")
    assert Path(pin.python_launcher).is_symlink()
    assert pin.python_launcher_target == str(Path(pin.python_launcher).resolve())


def test_freeze_runtime_pin_rejects_core_repository_evidence(tmp_path):
    environment = tmp_path / "runtime"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    lock = tmp_path / "runtime.lock"
    lock.write_text("fixture\n", encoding="utf-8")

    with pytest.raises(QualificationError, match="outside the repository"):
        freeze_runtime_pin(
            project="fixture",
            version_or_commit="1.0.0",
            python_constraint=">=3.12,<3.13",
            dependencies=(),
            environment_path=environment,
            lock_artifact_path=lock,
            worker_script=Path("scripts/runtime_qualification_worker.py").resolve(),
            worker_kind="qualification",
            runner_version="fixture-v1",
            admission_directory=Path("runtime-evidence").resolve(),
            repository_root=Path.cwd(),
        )


def test_local_admission_cannot_replace_registered_checkpoint_family_or_revision(tmp_path):
    runtime, _evidence = _runtime(tmp_path)
    registered = next(item for item in default_runtime_candidates() if item.name == "ttm-r3")
    assert registered.external_checkpoint is not None
    wrong_checkpoint = CheckpointPin(
        model_family=ModelFamily.TTM_R3.value,
        repository=RepositoryPin(
            repository_id=registered.external_checkpoint.repository_id,
            revision="f" * 40,
            license="unknown",
            runtime_artifacts=(ArtifactPin(relative_path="model.safetensors", sha256="a" * 64),),
        ),
        cache_path=str(tmp_path / "cache"),
    )
    admission = LocalCandidateAdmission(
        candidate_name="ttm-r3",
        checkpoint=wrong_checkpoint,
        runtime_pin=runtime,
        created_at=datetime.now(UTC),
    )

    with pytest.raises(QualificationError, match="not the registered candidate"):
        apply_local_candidate_admission(registered, admission)


def test_network_guard_preserves_ssl_import_but_blocks_connections():
    with network_blocked():
        import ssl

        assert ssl.SSLSocket is not None
        with pytest.raises(NetworkAccessAttemptError, match="network access is disabled"):
            socket.create_connection(("example.invalid", 443))


def test_ttm_r3_portable_contract_uses_native_horizon_and_runtime_commit():
    candidate: CandidateSpec = next(
        item for item in default_runtime_candidates() if item.name == "ttm-r3"
    )
    assert candidate.task == ModelTask.FORECAST
    assert candidate.output_schema == "forecast[30]"
    assert candidate.runtime_pin is not None
    assert candidate.runtime_pin.project == "granite-tsfm"
    assert "d473fc3d800c400230a3d8f5192fbdc6255a02f5" in candidate.runtime_pin.version_or_commit
    assert "granite-tsfm==0.3.8" in candidate.runtime_pin.dependencies


def test_external_cpu_roster_has_exact_native_contracts_and_current_runtime_packages():
    roster = {candidate.name: candidate for candidate in default_runtime_candidates()}

    assert roster["ttm-r2"].output_schema == "forecast[96]"
    assert roster["tspulse"].output_schema == "features[6]"
    assert roster["tspulse"].repeatability_seed == 0
    for name in ("ttm-r2", "ttm-r3", "tspulse"):
        assert "granite-tsfm==0.3.8" in roster[name].runtime_pin.dependencies
    for name in ("modern-finbert", "finbert-minilm", "finsentiment-deberta-v3"):
        dependencies = roster[name].runtime_pin.dependencies
        assert "transformers==5.5.4" in dependencies
        assert "torch==2.10.0+cpu" in dependencies
        assert "tokenizers==0.22.2" in dependencies


def test_tspulse_schema_rejects_feature_dimension_drift():
    candidate = next(item for item in default_runtime_candidates() if item.name == "tspulse")

    assert validate_candidate_output(candidate, [0.0] * 6) == "features[6]"
    with pytest.raises(InvalidModelOutputError, match="does not match"):
        validate_candidate_output(candidate, [0.0] * 5)
