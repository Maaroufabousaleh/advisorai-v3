#!/usr/bin/env python3
"""Create one actual-file, non-launching V3-Core long-run readiness report."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from advisorai.gates import GateDecision, PhaseGateRecord
from advisorai.models.forecasting import GpuModelLease
from advisorai.phase4.v3core_cadence import (
    V3_CORE_MARKET_DATA_PROVIDER,
    V3_CORE_MARKET_DATA_REST_ENDPOINT,
    V3CoreBar,
    V3CoreBarProvenance,
)
from advisorai.phase4.v3core_canary import sha256_file
from advisorai.phase4.v3core_chronos import (
    CHRONOS_CONTEXT_BARS,
    CHRONOS_MODEL,
    ChronosRuntimeIdentity,
    infer_chronos,
)
from advisorai.phase4.v3core_longrun import (
    QUALIFIED_CHRONOS_CHECKPOINT_SHA256,
    QUALIFIED_CHRONOS_MODEL,
    QUALIFIED_CHRONOS_REVISION,
    QUALIFIED_REQUIREMENTS_LOCK_SHA256,
)
from advisorai.phase4.v3core_longrun_runtime import (
    attest_long_run_identity,
    evaluate_long_run_readiness,
    load_long_run_preregistration,
    load_long_run_verification_results,
    long_run_component_files,
)


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _phase3_passed(path: Path) -> bool:
    gate = PhaseGateRecord.model_validate_json(path.read_text(encoding="utf-8"))
    return gate.phase == 3 and gate.decision is GateDecision.PASSED


def _runtime_passed(path: Path) -> bool:
    value = _load_json(path)
    resource = value.get("resource")
    candidate = value.get("candidate")
    checkpoint = candidate.get("external_checkpoint") if isinstance(candidate, dict) else None
    repository = checkpoint.get("repository") if isinstance(checkpoint, dict) else None
    runtime_pin = candidate.get("runtime_pin") if isinstance(candidate, dict) else None
    runtime_artifacts = (
        repository.get("runtime_artifacts") if isinstance(repository, dict) else None
    )
    checkpoint_hash = (
        next(
            (
                item.get("sha256")
                for item in runtime_artifacts
                if isinstance(item, dict) and item.get("relative_path") == "model.safetensors"
            ),
            None,
        )
        if isinstance(runtime_artifacts, list)
        else None
    )
    return (
        value.get("status") == "measured"
        and value.get("one_inference_completed") is True
        and value.get("output_schema_valid") is True
        and value.get("nan_inf_rejection_passed") is True
        and value.get("network_access_attempted") is False
        and value.get("offline_cached_inference") is True
        and isinstance(resource, dict)
        and resource.get("resource_limit_passed") is True
        and isinstance(repository, dict)
        and repository.get("repository_id") == QUALIFIED_CHRONOS_MODEL
        and repository.get("revision") == QUALIFIED_CHRONOS_REVISION
        and checkpoint_hash == QUALIFIED_CHRONOS_CHECKPOINT_SHA256
        and isinstance(runtime_pin, dict)
        and runtime_pin.get("lock_hash") == QUALIFIED_REQUIREMENTS_LOCK_SHA256
    )


def _git_tag_target(repository_root: Path, tag: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repository_root.resolve()),
            "rev-parse",
            f"refs/tags/{tag}^{{commit}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _gpu_lease_free() -> bool:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return False
    try:
        gpu = subprocess.run(
            [executable, "--query-gpu=name,driver_version", "--format=csv,noheader"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        applications = subprocess.run(
            [
                executable,
                "--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(gpu.stdout.strip()) and not applications.stdout.strip()


def _competing_component_exists() -> bool:
    try:
        import psutil
    except ImportError:
        return True
    names = {
        "collect_phase4_v3core_longrun.py",
        "run_phase4_v3core_longrun_chronos.py",
        "link_phase4_v3core_longrun_prediction_outcomes.py",
        "watch_phase4_v3core_longrun.py",
        "schedule_phase4_v3core_longrun.sh",
    }
    for process in psutil.process_iter(("pid", "cmdline")):
        if process.pid == os.getpid():
            continue
        try:
            command = [str(item) for item in (process.info.get("cmdline") or ())]
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except psutil.AccessDenied as exc:
            raise RuntimeError(
                "cannot inspect a process command line while proving run quiescence"
            ) from exc
        if any(Path(token).name in names for token in command):
            return True
    return False


def _candidate_worker_exists(runner_script: str) -> bool:
    """Return true when the exact offline Chronos worker is already running."""

    try:
        import psutil
    except ImportError:
        return True
    expected = str(Path(runner_script).expanduser().resolve(strict=False))
    for process in psutil.process_iter(("pid", "cmdline")):
        if process.pid == os.getpid():
            continue
        try:
            command = [str(item) for item in (process.info.get("cmdline") or ())]
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except psutil.AccessDenied:
            return True
        for token in command:
            if "/" not in token:
                continue
            try:
                resolved = str(Path(token).expanduser().resolve(strict=False))
            except OSError:
                continue
            if resolved == expected:
                return True
    return False


def _startup_smoke_context() -> tuple[V3CoreBar, ...]:
    """Build a non-prospective, validated 48-bar context for startup only."""

    snapshot_hash = "a" * 64
    start = datetime(2020, 1, 1, tzinfo=UTC)
    bars: list[V3CoreBar] = []
    for index in range(CHRONOS_CONTEXT_BARS):
        interval_end = start + timedelta(minutes=5 * index)
        collected_at = interval_end + timedelta(seconds=61)
        provenance = V3CoreBarProvenance(
            interval_end=interval_end,
            provider_available_at=collected_at - timedelta(seconds=1),
            collected_at=collected_at,
            provider_event_at=interval_end,
            availability_basis="forward_observed",
            evidence_class="forward_pit_admission",
            source_snapshot_hash=snapshot_hash,
            raw_record_hash="b" * 64,
            normalized_record_hash="c" * 64,
            source_health_state="HEALTHY",
        )
        close = Decimal(100 + index)
        bars.append(
            V3CoreBar(
                instrument="BTCUSDT",
                provenance=provenance,
                open=close,
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=Decimal("1"),
                source_id=V3_CORE_MARKET_DATA_PROVIDER,
                provider_identity=V3_CORE_MARKET_DATA_PROVIDER,
                endpoint=V3_CORE_MARKET_DATA_REST_ENDPOINT,
                source_snapshot_hash=snapshot_hash,
            )
        )
    return tuple(bars)


def _write_smoke_status(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _candidate_startup_smoke(
    *,
    repository_root: Path,
    admission_path: Path,
    qualification_evidence_path: Path,
    checkpoint_path: Path,
    timeout_seconds: float = 120.0,
) -> tuple[bool, str]:
    """Exercise the exact candidate identity and offline worker before launch.

    The smoke is deliberately not part of a generation evidence root.  It
    performs one synthetic, in-memory inference only to prove interpreter,
    checkpoint, CUDA, worker, and model-load readiness; it never writes a
    forecast, reads an outcome, or receives an authority-bearing environment.
    """

    with tempfile.TemporaryDirectory(prefix="advisorai-phase4-chronos-startup-smoke-") as root:
        smoke_root = Path(root)
        status_path = smoke_root / "candidate-startup-status.json"
        status: dict[str, object] = {
            "schema": "advisorai.phase4.v3-core.long-run.candidate-startup-smoke.v1",
            "state": "STARTING",
            "model": CHRONOS_MODEL,
            "model_loaded": False,
            "status_published": False,
            "prospective_prediction_written": False,
            "outcome_read": False,
            "network_access_attempted": False,
            "credentials_loaded": False,
            "order_writes_attempted": False,
            "execution_authority_present": False,
        }
        passed = False
        reason = "candidate startup smoke did not complete"
        identity: ChronosRuntimeIdentity | None = None
        try:
            if not _gpu_lease_free():
                raise RuntimeError("startup smoke requires a queryable idle CUDA device")
            identity = ChronosRuntimeIdentity.from_admission(
                admission_path.resolve(),
                qualification_evidence_path=qualification_evidence_path.resolve(),
                repository_root=repository_root.resolve(),
            )
            expected_checkpoint = (
                Path(identity.cache_path).expanduser().resolve(strict=True)
                / identity.cache_subdir
                / "model.safetensors"
            ).resolve(strict=True)
            actual_checkpoint = checkpoint_path.resolve(strict=True)
            if actual_checkpoint != expected_checkpoint:
                raise ValueError("startup smoke checkpoint path differs from admitted cache")
            if sha256_file(actual_checkpoint) != identity.checkpoint_hash:
                raise ValueError("startup smoke checkpoint hash differs from runtime identity")
            if identity.device.strip().lower() != "cuda":
                raise ValueError("startup smoke runtime is not bound to CUDA")
            if _candidate_worker_exists(identity.runner_script):
                raise RuntimeError("an existing Chronos worker prevents startup smoke")
            with GpuModelLease(CHRONOS_MODEL):
                result = infer_chronos(
                    identity=identity,
                    context=_startup_smoke_context(),
                    timeout_seconds=timeout_seconds,
                )
            if result.device.strip().lower() != "cuda":
                raise RuntimeError("startup smoke worker did not report CUDA")
            if len(result.forecast) < 12:
                raise RuntimeError("startup smoke worker returned fewer than 12 forecasts")
            if _candidate_worker_exists(identity.runner_script):
                raise RuntimeError("startup smoke worker remained alive after inference")
            passed = True
            reason = (
                "exact Chronos runtime identity, CUDA worker startup, and offline checkpoint "
                "load/readiness smoke passed"
            )
            status.update(
                {
                    "state": "READY",
                    "model_loaded": True,
                    "status_published": True,
                    "inference_completed": True,
                }
            )
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            reason = f"candidate startup smoke failed ({type(exc).__name__}): {exc}"
            status.update(
                {
                    "state": "FAILED",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
        finally:
            if identity is not None and _candidate_worker_exists(identity.runner_script):
                passed = False
                reason = f"{reason}; startup smoke worker remained alive after cleanup"
                status["state"] = "FAILED"
                status.setdefault("error_type", "RuntimeError")
                status.setdefault("error", reason)
            gpu_free_after = _gpu_lease_free()
            status["gpu_quiescent_after"] = gpu_free_after
            if not gpu_free_after:
                passed = False
                if status.get("state") == "READY":
                    reason = "candidate startup smoke failed: GPU was not quiescent after smoke"
                else:
                    reason = f"{reason}; GPU was not quiescent after smoke"
                status["state"] = "FAILED"
                status.setdefault("error_type", "RuntimeError")
                status.setdefault("error", reason)
            try:
                _write_smoke_status(status_path, status)
            except OSError as exc:
                passed = False
                reason = f"candidate startup smoke failed to publish status: {exc}"
        return passed, reason


def run(
    *,
    repository_root: Path,
    preregistration_path: Path,
    preregistration_sha256: str,
    verification_results_path: Path,
    requirements_lock_path: Path,
    checkpoint_path: Path,
    admission_path: Path,
    qualification_evidence_path: Path,
    phase3_gate_path: Path,
    model_runtime_qualification_path: Path,
    output_path: Path,
) -> dict[str, object]:
    repository_root = repository_root.resolve()
    preregistration = load_long_run_preregistration(
        preregistration_path.resolve(), expected_sha256=preregistration_sha256
    )
    if preregistration.verification_results_sha256 is None:
        raise ValueError("preregistration has no launch verification identity")
    if _git_tag_target(repository_root, preregistration.branch_or_tag) != (
        preregistration.repository_commit
    ):
        raise ValueError("frozen release tag differs from preregistration")
    checks = load_long_run_verification_results(
        verification_results_path.resolve(),
        expected_sha256=preregistration.verification_results_sha256,
    )
    if _competing_component_exists():
        checks["host_operation_contract"] = False
    attestation = attest_long_run_identity(
        repository_root=repository_root,
        expected_repository_commit=preregistration.repository_commit,
        component_files=long_run_component_files(repository_root),
        requirements_lock_path=requirements_lock_path.resolve(),
        checkpoint_path=checkpoint_path.resolve(),
        phase3_gate_path=phase3_gate_path.resolve(),
        model_runtime_qualification_path=model_runtime_qualification_path.resolve(),
    )
    if checks.get("runtime_attestation") is not True:
        startup_smoke_passed = False
        startup_smoke_reason = (
            "candidate startup smoke was not run because the static runtime attestation failed"
        )
    elif checks.get("host_operation_contract") is not True:
        startup_smoke_passed = False
        startup_smoke_reason = (
            "candidate startup smoke was not run because the host operation contract failed"
        )
    else:
        startup_smoke_passed, startup_smoke_reason = _candidate_startup_smoke(
            repository_root=repository_root,
            admission_path=admission_path,
            qualification_evidence_path=qualification_evidence_path,
            checkpoint_path=checkpoint_path,
        )
    checks["runtime_attestation"] = (
        checks.get("runtime_attestation") is True and startup_smoke_passed
    )
    report = evaluate_long_run_readiness(
        preregistration,
        attestation=attestation,
        phase3_gate_passed=_phase3_passed(phase3_gate_path.resolve()),
        model_runtime_passed=_runtime_passed(model_runtime_qualification_path.resolve()),
        preflight_checks=checks,
        credentials_loaded=False,
        order_writes_attempted=False,
        gpu_lease_free=_gpu_lease_free(),
        immutable_preregistration_created=True,
        preflight_check_reasons={"runtime_attestation": startup_smoke_reason},
    ).model_dump(mode="json")
    destination = output_path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    report["output_sha256"] = sha256_file(destination)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--preregistration-sha256", required=True)
    parser.add_argument("--verification-results", type=Path, required=True)
    parser.add_argument("--requirements-lock", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--qualification-evidence", type=Path, required=True)
    parser.add_argument("--phase3-gate", type=Path, required=True)
    parser.add_argument("--model-runtime-qualification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run(
            repository_root=args.repository_root,
            preregistration_path=args.preregistration,
            preregistration_sha256=args.preregistration_sha256,
            verification_results_path=args.verification_results,
            requirements_lock_path=args.requirements_lock,
            checkpoint_path=args.checkpoint,
            admission_path=args.admission,
            qualification_evidence_path=args.qualification_evidence,
            phase3_gate_path=args.phase3_gate,
            model_runtime_qualification_path=args.model_runtime_qualification,
            output_path=args.output,
        )
    except (OSError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        refusal = {
            "schema": "advisorai.phase4.v3-core.long-run.launch-preflight-refusal.v1",
            "decision": "LONG_RUN_REFUSED",
            "error_type": type(exc).__name__,
            "reason": str(exc),
            "credentials_loaded": False,
            "order_writes_attempted": False,
        }
        print(json.dumps(refusal, sort_keys=True, separators=(",", ":")))
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["decision"] == "LONG_RUN_READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
