#!/usr/bin/env python3
"""Run the dedicated, supervised 24-hour Chronos runtime stability profile.

This runner is deliberately separate from ``run_model_stability.py``.  The
historical runner's three-role acceptance contract is frozen; this command
records only Chronos-2-small runtime evidence and never creates a forecast or
outcome record.

The command is intended to be launched by an independent supervisor, for
example with ``setsid``/``nohup``.  The run directory is a fresh evidence
root.  A terminal PASS is emitted only after a real sample at or after the
24-hour boundary and is independently checked by the companion validator.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import psutil

from advisorai.models.forecasting import GpuModelLease
from advisorai.phase0 import (
    BenchmarkDataset,
    LocalCandidateAdmission,
    QualificationStatus,
    ResourceCeiling,
    apply_local_candidate_admission,
    default_runtime_candidates,
    manifest_bytes,
    run_runtime_qualification,
    write_immutable_json,
)
from advisorai.phase0.chronos_stability import (
    CHRONOS_MAX_RESIDUAL_RSS_MIB,
    CHRONOS_MAX_RESIDUAL_VRAM_MIB,
    CHRONOS_MAX_RSS_MIB,
    CHRONOS_MAX_VRAM_MIB,
    CHRONOS_STABILITY_CANDIDATE,
    CHRONOS_STABILITY_DEFAULT_INTERVAL_SECONDS,
    CHRONOS_STABILITY_MIN_HOURS,
    ChronosStabilityConfig,
    ChronosStabilitySample,
    append_chronos_cycle,
    make_chronos_cycle,
    payload_hash,
    read_chronos_cycles,
    summarize_chronos_stability,
)
from advisorai.phase4.v3core_chronos import ChronosRuntimeIdentity

CONFIG_FILENAME = "config.json"
CYCLES_FILENAME = "cycles.jsonl"
STATUS_FILENAME = "status.json"
SUMMARY_FILENAME = "summary.json"
LOCK_FILENAME = "runner.lock"
STABILITY_RUNNER_SCHEMA = "advisorai.phase0.chronos-stability-runner.v1"


class IdentityDriftError(RuntimeError):
    """Raised when a pinned runtime/code identity changes during a run."""


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_value(repository_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository_root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repository_identity(repository_root: Path) -> tuple[str, bool]:
    commit = _git_value(repository_root, "rev-parse", "HEAD")
    clean = not bool(_git_value(repository_root, "status", "--porcelain"))
    return commit, clean


def _command_identity() -> str:
    return sha256(" ".join(sys.argv).encode()).hexdigest()


def _process_identity() -> dict[str, object]:
    process = psutil.Process(os.getpid())
    try:
        create_time = process.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        create_time = None
    return {
        "pid": os.getpid(),
        "process_create_time": create_time,
        "command": " ".join(sys.argv),
        "command_identity": _command_identity(),
    }


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _fresh_dataset() -> BenchmarkDataset:
    """Return the approved synthetic, non-prospective Chronos qualification input."""

    # Reuse the existing 512-context synthetic generator only as a data-shape
    # fixture. No TTM candidate or TTM inference is selected by this profile.
    base_fixture = BenchmarkDataset.ttm_runtime_fixture()
    dataset = BenchmarkDataset.model_validate(
        {
            **base_fixture.model_dump(mode="python"),
            "dataset_id": "advisorai-phase0-chronos-runtime-fixture",
            "source": "synthetic://advisorai/phase0/chronos-runtime-contract-v1",
            "snapshot_id": "chronos-runtime-contract-v1",
        }
    )
    if dataset.task.value != "forecast":
        raise ValueError("Chronos stability fixture must be a forecast dataset")
    return dataset


def _dataset_payload(dataset: BenchmarkDataset) -> tuple[object, object]:
    # Match the existing runtime qualification fixture shape.  These are
    # synthetic values, not Phase-4 market bars or prospective opportunities.
    sample = dataset.inputs[:512]
    batch = (dataset.inputs[:512], dataset.inputs[30:542])
    return sample, batch


def _current_identity(
    *,
    admission_path: Path,
    qualification_evidence_path: Path,
    repository_root: Path,
) -> tuple[ChronosRuntimeIdentity, dict[str, object]]:
    identity = ChronosRuntimeIdentity.from_admission(
        admission_path,
        qualification_evidence_path=qualification_evidence_path,
        repository_root=repository_root,
    )
    repository_commit, repository_clean = _repository_identity(repository_root)
    source_hashes = {
        "stability_runner_sha256": _sha256_file(Path(__file__).resolve()),
        "chronos_contract_sha256": _sha256_file(
            repository_root / "src/advisorai/phase4/v3core_chronos.py"
        ),
        "runtime_qualification_sha256": _sha256_file(
            repository_root / "src/advisorai/phase0/runtime_qualification.py"
        ),
    }
    return identity, {
        "repository_commit": repository_commit,
        "repository_clean": repository_clean,
        **source_hashes,
        "runtime_identity_sha256": payload_hash(identity.model_dump(mode="json")),
    }


def _config_from_current_identity(
    *,
    run_id: str,
    started_at: datetime,
    duration_hours: float,
    interval_seconds: float,
    admission_path: Path,
    qualification_evidence_path: Path,
    repository_root: Path,
    dataset: BenchmarkDataset,
    identity: ChronosRuntimeIdentity,
    binding: dict[str, object],
) -> ChronosStabilityConfig:
    if not binding["repository_clean"]:
        raise ValueError("Chronos stability requires a clean tracked worktree")
    if identity.pyvenv_cfg_hash is None:
        raise ValueError("Chronos stability requires an attested pyvenv identity")
    return ChronosStabilityConfig(
        run_id=run_id,
        started_at=started_at,
        duration_hours=duration_hours,
        interval_seconds=interval_seconds,
        admission_path=str(admission_path.resolve()),
        admission_sha256=identity.admission_sha256,
        qualification_evidence_path=str(qualification_evidence_path.resolve()),
        qualification_evidence_sha256=identity.qualification_evidence_sha256,
        repository_root=str(repository_root.resolve()),
        repository_commit=str(binding["repository_commit"]),
        stability_runner_sha256=str(binding["stability_runner_sha256"]),
        chronos_contract_sha256=str(binding["chronos_contract_sha256"]),
        runtime_qualification_sha256=str(binding["runtime_qualification_sha256"]),
        model_revision=identity.checkpoint_revision,
        checkpoint_sha256=identity.checkpoint_hash,
        config_sha256=identity.config_hash,
        python_launcher_hash=identity.python_launcher_hash,
        resolved_python_binary_hash=identity.resolved_python_binary_hash,
        pyvenv_cfg_hash=identity.pyvenv_cfg_hash,
        installed_environment_sha256=identity.installed_environment_sha256,
        runtime_lock_hash=identity.lock_hash,
        runner_hash=identity.runner_hash,
        worker_script_sha256=identity.runner_script_hash,
        environment_fingerprint=identity.environment_fingerprint,
        runtime_identity_sha256=str(binding["runtime_identity_sha256"]),
        dataset_id=dataset.dataset_id,
        dataset_hash=dataset.content_hash,
        allowed_residual_growth_mib=128.0,
        max_rss_mib=CHRONOS_MAX_RSS_MIB,
        max_vram_mib=CHRONOS_MAX_VRAM_MIB,
        max_residual_rss_mib=CHRONOS_MAX_RESIDUAL_RSS_MIB,
        max_residual_vram_mib=CHRONOS_MAX_RESIDUAL_VRAM_MIB,
        repeatability_policy="deterministic_required",
    )


def _assert_identity_unchanged(
    config: ChronosStabilityConfig,
    *,
    identity: ChronosRuntimeIdentity,
    binding: dict[str, object],
) -> None:
    expected = {
        "repository_commit": config.repository_commit,
        "stability_runner_sha256": config.stability_runner_sha256,
        "chronos_contract_sha256": config.chronos_contract_sha256,
        "runtime_qualification_sha256": config.runtime_qualification_sha256,
        "runtime_identity_sha256": config.runtime_identity_sha256,
    }
    for field_name, expected_value in expected.items():
        if binding.get(field_name) != expected_value:
            raise IdentityDriftError(f"{field_name}_changed")
    if not binding.get("repository_clean", False):
        raise IdentityDriftError("repository_became_dirty")
    identity_payload = identity.model_dump(mode="json")
    if payload_hash(identity_payload) != config.runtime_identity_sha256:
        raise IdentityDriftError("runtime_identity_changed")
    for field_name, expected_value in (
        ("candidate_name", CHRONOS_STABILITY_CANDIDATE),
        ("checkpoint_revision", config.model_revision),
        ("checkpoint_hash", config.checkpoint_sha256),
        ("config_hash", config.config_sha256),
        ("python_launcher_hash", config.python_launcher_hash),
        ("resolved_python_binary_hash", config.resolved_python_binary_hash),
        ("pyvenv_cfg_hash", config.pyvenv_cfg_hash),
        ("installed_environment_sha256", config.installed_environment_sha256),
        ("lock_hash", config.runtime_lock_hash),
        ("runner_hash", config.runner_hash),
        ("runner_script_hash", config.worker_script_sha256),
        ("environment_fingerprint", config.environment_fingerprint),
        ("admission_sha256", config.admission_sha256),
        ("qualification_evidence_sha256", config.qualification_evidence_sha256),
    ):
        if getattr(identity, field_name) != expected_value:
            raise IdentityDriftError(f"{field_name}_changed")


def _gpu_compute_quiescent() -> bool:
    """Require no external NVIDIA compute process before a cycle."""

    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return not any(line.strip() for line in result.stdout.splitlines())


def _failure_reason(result: object, default: str) -> str:
    value = getattr(result, "failure_reason", None)
    if value:
        safe = "".join(
            character for character in str(value) if character.isalnum() or character in " _-.:/"
        )
        return safe[:240] or default
    return default


def _sample_from_result(
    result: object,
    *,
    runtime_identity_sha256: str,
    sampled_at: datetime,
    gpu_lease_released: bool,
) -> ChronosStabilitySample:
    resource = getattr(result, "resource", None)
    status = getattr(getattr(result, "status", None), "value", "failed")
    measured = status == QualificationStatus.MEASURED.value
    rss_peak = float(resource.rss_peak_mib) if resource is not None else 0.0
    rss_after = float(resource.rss_after_unload_mib) if resource is not None else 0.0
    rss_residual = (
        float(resource.rss_residual_mib)
        if resource is not None and resource.rss_residual_mib is not None
        else None
    )
    vram_peak = (
        float(resource.vram_peak_mib)
        if resource is not None and resource.vram_peak_mib is not None
        else None
    )
    vram_after = (
        float(resource.vram_after_unload_mib)
        if resource is not None and resource.vram_after_unload_mib is not None
        else None
    )
    vram_residual = (
        float(resource.vram_residual_mib)
        if resource is not None and resource.vram_residual_mib is not None
        else None
    )
    resource_passed = bool(resource and resource.resource_limit_passed)
    worker_terminated = bool(resource and resource.worker_process_terminated)
    memory_released = bool(resource and resource.memory_released)
    result_candidate = getattr(getattr(result, "candidate", None), "name", None)
    exact_candidate = result_candidate == CHRONOS_STABILITY_CANDIDATE
    exact_output_schema = getattr(result, "output_shape", None) == "forecast[30]"
    residuals_passed = (
        rss_residual is not None
        and rss_residual <= CHRONOS_MAX_RESIDUAL_RSS_MIB
        and vram_residual is not None
        and vram_residual <= CHRONOS_MAX_RESIDUAL_VRAM_MIB
    )
    failure = None
    if not (
        measured
        and getattr(result, "offline_cached_inference", False)
        and not getattr(result, "network_access_attempted", True)
        and getattr(result, "one_inference_completed", False)
        and getattr(result, "batch_completed", False)
        and exact_candidate
        and getattr(result, "output_schema_valid", False)
        and exact_output_schema
        and getattr(result, "nan_inf_rejection_passed", False)
        and getattr(result, "repeated_outputs_equal", False)
        and resource_passed
        and worker_terminated
        and memory_released
        and gpu_lease_released
        and residuals_passed
    ):
        failure = _failure_reason(result, "chronos_stability_cycle_failed")
        if not residuals_passed:
            failure = "residual_resource_ceiling_failed"
        elif not worker_terminated:
            failure = "worker_cleanup_failed"
        elif not gpu_lease_released:
            failure = "gpu_lease_cleanup_failed"

    return ChronosStabilitySample(
        sampled_at=sampled_at,
        status=status if status in {"measured", "failed", "quarantined"} else "failed",
        runtime_identity_sha256=runtime_identity_sha256,
        qualification_manifest_hash=sha256(manifest_bytes(result)).hexdigest(),
        offline_cached_inference=bool(getattr(result, "offline_cached_inference", False)),
        network_access_attempted=bool(getattr(result, "network_access_attempted", True)),
        model_loaded=measured and getattr(result, "loaded_model_class", None) is not None,
        inference_completed=bool(getattr(result, "one_inference_completed", False)),
        batch_completed=bool(getattr(result, "batch_completed", False)),
        output_schema_valid=bool(
            exact_candidate
            and exact_output_schema
            and getattr(result, "output_schema_valid", False)
        ),
        finite_output_valid=bool(getattr(result, "nan_inf_rejection_passed", False)),
        repeated_outputs_equal=bool(getattr(result, "repeated_outputs_equal", False)),
        worker_process_terminated=worker_terminated,
        gpu_lease_released=gpu_lease_released,
        resource_limit_passed=resource_passed,
        memory_released=memory_released,
        rss_peak_mib=rss_peak,
        rss_after_unload_mib=rss_after,
        rss_residual_mib=rss_residual,
        vram_peak_mib=vram_peak,
        vram_after_unload_mib=vram_after,
        vram_residual_mib=vram_residual,
        failure_reason=failure,
    )


def _failed_sample(
    *, runtime_identity_sha256: str, sampled_at: datetime, reason: str
) -> ChronosStabilitySample:
    return ChronosStabilitySample(
        sampled_at=sampled_at,
        status="failed",
        runtime_identity_sha256=runtime_identity_sha256,
        qualification_manifest_hash="0" * 64,
        offline_cached_inference=False,
        network_access_attempted=False,
        model_loaded=False,
        inference_completed=False,
        batch_completed=False,
        output_schema_valid=False,
        finite_output_valid=False,
        repeated_outputs_equal=False,
        worker_process_terminated=False,
        gpu_lease_released=False,
        resource_limit_passed=False,
        memory_released=False,
        rss_peak_mib=0.0,
        rss_after_unload_mib=0.0,
        failure_reason=reason,
    )


def _run_cycle(
    *,
    config: ChronosStabilityConfig,
    admission_path: Path,
    qualification_evidence_path: Path,
    repository_root: Path,
    dataset: BenchmarkDataset,
) -> ChronosStabilitySample:
    sampled_at = datetime.now(UTC)
    try:
        identity, binding = _current_identity(
            admission_path=admission_path,
            qualification_evidence_path=qualification_evidence_path,
            repository_root=repository_root,
        )
        _assert_identity_unchanged(config, identity=identity, binding=binding)
        if not _gpu_compute_quiescent():
            return _failed_sample(
                runtime_identity_sha256=config.runtime_identity_sha256,
                sampled_at=sampled_at,
                reason="gpu_compute_conflict_or_unavailable",
            )
        candidates = {candidate.name: candidate for candidate in default_runtime_candidates()}
        candidate = candidates.get(CHRONOS_STABILITY_CANDIDATE)
        if candidate is None:
            raise RuntimeError("chronos_candidate_missing_from_roster")
        admission = LocalCandidateAdmission.model_validate(_load_json(admission_path))
        admitted = apply_local_candidate_admission(candidate, admission)
        sample_input, batch_input = _dataset_payload(dataset)
        result = run_runtime_qualification(
            admitted,
            runner=None,
            dataset=dataset,
            sample_input=sample_input,
            batch_input=batch_input,
            repeats=2,
            ceiling=ResourceCeiling(
                max_rss_mib=CHRONOS_MAX_RSS_MIB,
                max_vram_mib=CHRONOS_MAX_VRAM_MIB,
                max_residual_rss_mib=CHRONOS_MAX_RESIDUAL_RSS_MIB,
                max_residual_vram_mib=CHRONOS_MAX_RESIDUAL_VRAM_MIB,
                gpu_models_at_once=1,
            ),
            repository_root=repository_root,
        )
        after_identity, after_binding = _current_identity(
            admission_path=admission_path,
            qualification_evidence_path=qualification_evidence_path,
            repository_root=repository_root,
        )
        _assert_identity_unchanged(config, identity=after_identity, binding=after_binding)
        lease_released = GpuModelLease._active_family is None and _gpu_compute_quiescent()  # noqa: SLF001 - attestation
        return _sample_from_result(
            result,
            runtime_identity_sha256=config.runtime_identity_sha256,
            sampled_at=datetime.now(UTC),
            gpu_lease_released=lease_released,
        )
    except IdentityDriftError:
        raise
    except Exception as exc:  # sanitized into append-only evidence
        reason = "".join(
            character
            for character in type(exc).__name__
            if character.isalnum() or character in "_-"
        )
        return _failed_sample(
            runtime_identity_sha256=config.runtime_identity_sha256,
            sampled_at=sampled_at,
            reason=reason or "chronos_cycle_exception",
        )


def _write_status(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _status_payload(
    *,
    state: str,
    config: ChronosStabilityConfig,
    cycles: tuple[object, ...],
    process: dict[str, object],
    reason: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": STABILITY_RUNNER_SCHEMA,
        "run_id": config.run_id,
        "state": state,
        "updated_at": datetime.now(UTC).isoformat(),
        "process": process,
        "cycle_count": len(cycles),
        "last_record_hash": cycles[-1].record_hash if cycles else None,
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "execution_authority_present": False,
    }
    if reason is not None:
        payload["reason"] = reason
    return payload


def _config_or_create(
    *,
    run_directory: Path,
    admission_path: Path,
    qualification_evidence_path: Path,
    repository_root: Path,
    duration_hours: float,
    interval_seconds: float,
    dataset: BenchmarkDataset,
) -> ChronosStabilityConfig:
    config_path = run_directory / CONFIG_FILENAME
    if config_path.exists():
        raise ValueError(
            "Chronos stability evidence roots are single-use; existing config requires a new run"
        )
    if any(
        (run_directory / filename).exists()
        for filename in (CYCLES_FILENAME, STATUS_FILENAME, SUMMARY_FILENAME)
    ):
        raise ValueError("cannot initialize a Chronos stability run over existing evidence")
    identity, binding = _current_identity(
        admission_path=admission_path,
        qualification_evidence_path=qualification_evidence_path,
        repository_root=repository_root,
    )
    config = _config_from_current_identity(
        run_id=run_directory.name,
        started_at=datetime.now(UTC),
        duration_hours=duration_hours,
        interval_seconds=interval_seconds,
        admission_path=admission_path,
        qualification_evidence_path=qualification_evidence_path,
        repository_root=repository_root,
        dataset=dataset,
        identity=identity,
        binding=binding,
    )
    write_immutable_json(config_path, config.model_dump(mode="json"))
    return config


def run_stability(
    *,
    admission_path: Path,
    qualification_evidence_path: Path,
    repository_root: Path,
    run_directory: Path,
    duration_hours: float = CHRONOS_STABILITY_MIN_HOURS,
    interval_seconds: float = CHRONOS_STABILITY_DEFAULT_INTERVAL_SECONDS,
) -> int:
    if duration_hours < CHRONOS_STABILITY_MIN_HOURS:
        raise ValueError("Chronos stability duration must be at least 24 actual hours")
    if interval_seconds < 0:
        raise ValueError("Chronos stability cadence cannot be negative")
    repository_root = repository_root.resolve()
    run_directory = run_directory.resolve()
    run_directory.mkdir(parents=True, exist_ok=True)
    dataset = _fresh_dataset()
    config = _config_or_create(
        run_directory=run_directory,
        admission_path=admission_path.resolve(),
        qualification_evidence_path=qualification_evidence_path.resolve(),
        repository_root=repository_root,
        duration_hours=duration_hours,
        interval_seconds=interval_seconds,
        dataset=dataset,
    )
    lock_path = run_directory / LOCK_FILENAME
    with lock_path.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another Chronos stability supervisor owns this run") from exc
        process = _process_identity()
        cycles_path = run_directory / CYCLES_FILENAME
        status_path = run_directory / STATUS_FILENAME
        cycles = read_chronos_cycles(cycles_path)
        _write_status(
            status_path,
            _status_payload(state="running", config=config, cycles=cycles, process=process),
        )
        interrupted = False
        identity_failure_reason: str | None = None
        stop_requested = False

        def _request_stop(signum: int, _frame: object) -> None:
            nonlocal stop_requested
            stop_requested = True
            raise InterruptedError(f"signal_{signum}")

        previous_handlers = {
            signal.SIGTERM: signal.getsignal(signal.SIGTERM),
            signal.SIGINT: signal.getsignal(signal.SIGINT),
        }
        signal.signal(signal.SIGTERM, _request_stop)
        signal.signal(signal.SIGINT, _request_stop)
        try:
            target_end = config.started_at + timedelta(hours=config.duration_hours)
            while True:
                now = datetime.now(UTC)
                last_sampled_at = cycles[-1].sampled_at if cycles else None
                if (
                    now >= target_end
                    and last_sampled_at is not None
                    and last_sampled_at >= target_end
                ):
                    break
                sample = _run_cycle(
                    config=config,
                    admission_path=Path(config.admission_path),
                    qualification_evidence_path=Path(config.qualification_evidence_path),
                    repository_root=Path(config.repository_root),
                    dataset=dataset,
                )
                cycle = make_chronos_cycle(
                    config,
                    sample,
                    sequence=len(cycles),
                    previous_record_hash=cycles[-1].record_hash if cycles else None,
                )
                append_chronos_cycle(cycles_path, cycle)
                cycles = (*cycles, cycle)
                _write_status(
                    status_path,
                    _status_payload(
                        state="failed" if not sample.passed else "running",
                        config=config,
                        cycles=cycles,
                        process=process,
                        reason=sample.failure_reason if not sample.passed else None,
                    ),
                )
                if not sample.passed:
                    break
                if stop_requested:
                    interrupted = True
                    break
                now = datetime.now(UTC)
                if now < target_end:
                    time.sleep(interval_seconds)
        except (InterruptedError, KeyboardInterrupt):
            interrupted = True
        except IdentityDriftError as exc:
            identity_failure_reason = str(exc)
        finally:
            signal.signal(signal.SIGTERM, previous_handlers[signal.SIGTERM])
            signal.signal(signal.SIGINT, previous_handlers[signal.SIGINT])

        if interrupted:
            _write_status(
                status_path,
                _status_payload(
                    state="interrupted",
                    config=config,
                    cycles=cycles,
                    process=process,
                    reason="supervisor_interrupted",
                ),
            )
            return 2
        if identity_failure_reason is not None:
            _write_status(
                status_path,
                _status_payload(
                    state="failed_identity_drift",
                    config=config,
                    cycles=cycles,
                    process=process,
                    reason=identity_failure_reason,
                ),
            )
            return 1
        if cycles:
            summary = summarize_chronos_stability(config, cycles)
            write_immutable_json(
                run_directory / SUMMARY_FILENAME,
                summary.model_dump(mode="json"),
            )
            _write_status(
                status_path,
                _status_payload(
                    state=summary.status,
                    config=config,
                    cycles=cycles,
                    process=process,
                    reason=None
                    if summary.status == "passed"
                    else "cycle_or_terminal_requirement_failed",
                ),
            )
            return 0 if summary.status == "passed" else 1
        _write_status(
            status_path,
            _status_payload(
                state="failed",
                config=config,
                cycles=cycles,
                process=process,
                reason="no_cycle_recorded",
            ),
        )
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--qualification-evidence", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--duration-hours", type=float, default=CHRONOS_STABILITY_MIN_HOURS)
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=CHRONOS_STABILITY_DEFAULT_INTERVAL_SECONDS,
    )
    args = parser.parse_args()
    return run_stability(
        admission_path=args.admission,
        qualification_evidence_path=args.qualification_evidence,
        repository_root=args.repository_root,
        run_directory=args.run_directory,
        duration_hours=args.duration_hours,
        interval_seconds=args.interval_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
