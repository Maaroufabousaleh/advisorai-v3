#!/usr/bin/env python3
"""Launch the long-run components only after a final readiness report passes.

This script has an explicit --launch interlock. It does not create a
preregistration and it never activates credentials or order capability.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from advisorai.phase4.v3core_longrun_runtime import (
    LONG_RUN_EVIDENCE_CLASS,
    LongRunLaunchReadinessReport,
    attest_long_run_identity,
    identity_matches_preregistration,
    load_long_run_preregistration,
    long_run_component_files,
    process_command_identity,
    process_create_time,
    process_identity_matches,
    read_json_stable,
    write_json_atomic,
)


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root.resolve()), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _git_tag_target(root: Path, tag: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root.resolve()), "rev-parse", f"refs/tags/{tag}^{{commit}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _require_no_long_run_component_processes() -> None:
    """Refuse stale/competing scientific components using full command identity."""

    try:
        import psutil
    except ImportError as exc:
        raise RuntimeError("launch process inspection requires psutil") from exc
    component_names = {
        "collect_phase4_v3core_longrun.py",
        "run_phase4_v3core_longrun_chronos.py",
        "link_phase4_v3core_longrun_prediction_outcomes.py",
        "watch_phase4_v3core_longrun.py",
        "schedule_phase4_v3core_longrun.sh",
    }
    conflicts: list[str] = []
    for process in psutil.process_iter(("pid", "cmdline")):
        if process.pid == os.getpid():
            continue
        try:
            command = [str(item) for item in (process.info.get("cmdline") or ())]
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        if any(Path(token).name in component_names for token in command):
            conflicts.append(f"pid={process.pid} command={command!r}")
    if conflicts:
        raise RuntimeError("competing long-run component exists: " + "; ".join(conflicts))


def _require_gpu_lease_free() -> None:
    """Require a queryable CUDA device with no resident compute application."""

    executable = shutil.which("nvidia-smi")
    if executable is None:
        raise RuntimeError("nvidia-smi is unavailable at launch time")
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
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("launch-time CUDA/GPU attestation failed") from exc
    if not gpu.stdout.strip():
        raise RuntimeError("launch-time CUDA/GPU attestation returned no device")
    resident = [line.strip() for line in applications.stdout.splitlines() if line.strip()]
    if resident:
        raise RuntimeError("GPU lease is not free: " + "; ".join(resident))


def _safe_environment(repository_root: Path) -> dict[str, str]:
    allowed = {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "VIRTUAL_ENV",
        "HF_HOME",
        "HF_HUB_CACHE",
        "TRANSFORMERS_CACHE",
        "TORCH_HOME",
        "CUDA_VISIBLE_DEVICES",
    }
    result = {
        key: value
        for key, value in os.environ.items()
        if key in allowed
        and not any(token in key.lower() for token in ("secret", "token", "password", "key"))
    }
    result["PYTHONPATH"] = str(repository_root / "src")
    result["ADVISORAI_CREDENTIALS_DISABLED"] = "1"
    result["ADVISORAI_ORDER_CAPABILITY_DISABLED"] = "1"
    return result


def _require_launch_window(
    now: datetime,
    *,
    launch_not_before_at: datetime | None,
    launch_not_after_at: datetime | None,
) -> None:
    """Require the explicit post-start window frozen in the preregistration."""

    if launch_not_before_at is None or launch_not_after_at is None:
        raise ValueError("preregistration has no post-start launch window")
    if now < launch_not_before_at:
        raise ValueError("launch window has not opened; early component start is forbidden")
    if now > launch_not_after_at:
        raise ValueError("launch start window was missed; do not slide a long-run start")


def _component_commands(
    *,
    repository_root: Path,
    preregistration_path: Path,
    preregistration_sha256: str,
    source_root: Path,
    candidate_root: Path,
    outcome_root: Path,
    coordinator_root: Path,
    watchdog_root: Path,
    scheduler_root: Path,
    audit_root: Path,
    admission_path: Path,
    qualification_evidence_path: Path,
) -> dict[str, list[str]]:
    """Build the complete, reviewable command set for one launch.

    Keeping this construction separate from process creation makes it possible
    to test that every child receives the required authority-free roots before
    any process is started.  In particular, the scheduler must receive the
    same outcome root used by the outcome linker and watchdog.
    """

    common = [
        "--repository-root",
        str(repository_root),
        "--preregistration",
        str(preregistration_path),
        "--preregistration-sha256",
        preregistration_sha256,
    ]
    return {
        "collector": [
            sys.executable,
            str(repository_root / "scripts/collect_phase4_v3core_longrun.py"),
            "--real",
            *common,
            "--run-root",
            str(source_root),
            "--coordinator-root",
            str(coordinator_root),
        ],
        "candidate": [
            sys.executable,
            str(repository_root / "scripts/run_phase4_v3core_longrun_chronos.py"),
            "--real",
            *common,
            "--admission",
            str(admission_path),
            "--qualification-evidence",
            str(qualification_evidence_path),
            "--source-root",
            str(source_root),
            "--run-root",
            str(candidate_root),
            "--coordinator-root",
            str(coordinator_root),
        ],
        "outcomes": [
            sys.executable,
            str(repository_root / "scripts/link_phase4_v3core_longrun_prediction_outcomes.py"),
            *common,
            "--source-root",
            str(source_root),
            "--candidate-root",
            str(candidate_root),
            "--outcome-root",
            str(outcome_root),
            "--coordinator-root",
            str(coordinator_root),
        ],
        "watchdog": [
            sys.executable,
            str(repository_root / "scripts/watch_phase4_v3core_longrun.py"),
            *common,
            "--source-root",
            str(source_root),
            "--candidate-root",
            str(candidate_root),
            "--outcome-root",
            str(outcome_root),
            "--coordinator-root",
            str(coordinator_root),
            "--watchdog-root",
            str(watchdog_root),
        ],
        "scheduler": [
            "bash",
            str(repository_root / "scripts/schedule_phase4_v3core_longrun.sh"),
            "--preregistration",
            str(preregistration_path),
            "--preregistration-sha256",
            preregistration_sha256,
            "--source-root",
            str(source_root),
            "--candidate-root",
            str(candidate_root),
            "--outcome-root",
            str(outcome_root),
            "--coordinator-root",
            str(coordinator_root),
            "--watchdog-root",
            str(watchdog_root),
            "--audit-root",
            str(audit_root),
            "--scheduler-root",
            str(scheduler_root),
            "--repository-root",
            str(repository_root),
            "--admission",
            str(admission_path),
            "--qualification-evidence",
            str(qualification_evidence_path),
        ],
    }


def launch(
    *,
    repository_root: Path,
    preregistration_path: Path,
    preregistration_sha256: str,
    readiness_report_path: Path,
    evidence_root: Path,
    admission_path: Path,
    qualification_evidence_path: Path,
    requirements_lock_path: Path,
    checkpoint_path: Path,
    phase3_gate_path: Path,
    model_runtime_qualification_path: Path,
    launch: bool,
) -> dict[str, object]:
    if not launch:
        raise ValueError("launch requires explicit --launch")
    repository_root = repository_root.resolve()
    preregistration = load_long_run_preregistration(
        preregistration_path.resolve(), expected_sha256=preregistration_sha256
    )
    readiness = LongRunLaunchReadinessReport.model_validate(
        read_json_stable(readiness_report_path.resolve())
    )
    if readiness.decision != "LONG_RUN_READY" or readiness.long_run_ready is not True:
        raise ValueError("final readiness report does not authorize long-run launch")
    if readiness.preregistration_sha256 != preregistration_sha256:
        raise ValueError("readiness report is bound to a different preregistration")
    if not all(check.passed for check in readiness.checks):
        raise ValueError("readiness report contains a failed launch check")
    if _git_head(repository_root) != preregistration.repository_commit:
        raise ValueError("launch checkout differs from preregistration")
    if _git_tag_target(repository_root, preregistration.branch_or_tag) != (
        preregistration.repository_commit
    ):
        raise ValueError("frozen release tag differs from preregistration")
    actual_identity = attest_long_run_identity(
        repository_root=repository_root,
        expected_repository_commit=preregistration.repository_commit,
        component_files=long_run_component_files(repository_root),
        requirements_lock_path=requirements_lock_path.resolve(),
        checkpoint_path=checkpoint_path.resolve(),
        phase3_gate_path=phase3_gate_path.resolve(),
        model_runtime_qualification_path=model_runtime_qualification_path.resolve(),
    )
    if not identity_matches_preregistration(preregistration, actual_identity):
        raise ValueError("actual launch checkout identity does not match the preregistration")
    if actual_identity.attestation_hash != readiness.actual_identity_hash:
        raise ValueError("launch readiness attestation is stale for the current checkout")
    now = datetime.now(UTC)
    _require_launch_window(
        now,
        launch_not_before_at=preregistration.launch_not_before_at,
        launch_not_after_at=preregistration.launch_not_after_at,
    )
    _require_no_long_run_component_processes()
    _require_gpu_lease_free()
    # Host checks consume part of the frozen launch window. Recheck immediately
    # before creating evidence or starting a child so a slow check cannot turn
    # into an unreviewed late launch.
    now = datetime.now(UTC)
    _require_launch_window(
        now,
        launch_not_before_at=preregistration.launch_not_before_at,
        launch_not_after_at=preregistration.launch_not_after_at,
    )
    evidence_root = evidence_root.resolve()
    if evidence_root.exists() and any(evidence_root.iterdir()):
        raise ValueError("long-run evidence root must be empty before launch")
    source_root = evidence_root / "source"
    candidate_root = evidence_root / "candidate"
    coordinator_root = evidence_root / "coordinator"
    watchdog_root = evidence_root / "watchdog"
    scheduler_root = evidence_root / "scheduler"
    audit_root = evidence_root / "audit"
    for path in (
        source_root,
        candidate_root,
        coordinator_root,
        watchdog_root,
        scheduler_root,
        audit_root,
    ):
        path.mkdir(parents=True, exist_ok=True)
    commands = _component_commands(
        repository_root=repository_root,
        preregistration_path=preregistration_path.resolve(),
        preregistration_sha256=preregistration_sha256,
        source_root=source_root,
        candidate_root=candidate_root,
        outcome_root=candidate_root,
        coordinator_root=coordinator_root,
        watchdog_root=watchdog_root,
        scheduler_root=scheduler_root,
        audit_root=audit_root,
        admission_path=admission_path.resolve(),
        qualification_evidence_path=qualification_evidence_path.resolve(),
    )
    environment = _safe_environment(repository_root)
    processes: dict[str, dict[str, object]] = {}
    launch_path = evidence_root / "launch.json"
    launch_metadata = {
        "schema": "advisorai.phase4.v3-core.long-run.launch.v1",
        "state": "STARTING",
        "generation_id": preregistration.generation_id,
        "evidence_class": LONG_RUN_EVIDENCE_CLASS,
        "admission_eligible": False,
        "repository_commit": preregistration.repository_commit,
        "preregistration_sha256": preregistration_sha256,
        "code_hashes": {
            "collector": preregistration.collector_code_sha256,
            "candidate": preregistration.candidate_worker_code_sha256,
            "outcomes": preregistration.outcome_linker_code_sha256,
            "watchdog": preregistration.watchdog_code_sha256,
            "auditor": preregistration.auditor_code_sha256,
            "scheduler": preregistration.scheduler_code_sha256,
            "coordinator": preregistration.coordinator_code_sha256,
            "launcher": preregistration.launcher_code_sha256,
            "launch_gate": preregistration.launch_gate_code_sha256,
            "long_run_contract": preregistration.long_run_contract_code_sha256,
            "forward_contract": preregistration.forward_contract_code_sha256,
            "cadence_contract": preregistration.cadence_contract_code_sha256,
        },
        "started_at": now.isoformat().replace("+00:00", "Z"),
        "launch_not_before_at": preregistration.launch_not_before_at.isoformat().replace(
            "+00:00", "Z"
        ),
        "launch_not_after_at": preregistration.launch_not_after_at.isoformat().replace(
            "+00:00", "Z"
        ),
        "commands": commands,
        "processes": processes,
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "execution_authority_present": False,
    }
    # Publish a durable launch intent before the first child is spawned.  A
    # launcher crash must leave an auditable STARTING record rather than
    # orphaning processes with no authoritative launch metadata.
    write_json_atomic(launch_path, launch_metadata)

    def start(name: str) -> subprocess.Popen[str]:
        command = commands[name]
        log = (evidence_root / f"{name}.stdout.log").open("x", encoding="utf-8")
        process = subprocess.Popen(
            command,
            cwd=repository_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log.close()
        processes[name] = {
            "pid": process.pid,
            "process_create_time": process_create_time(process.pid),
            "command": command,
            "command_identity": process_command_identity(command),
            "log": str(evidence_root / f"{name}.stdout.log"),
        }
        return process

    def wait_for_status(path: Path, process: subprocess.Popen[str], label: str) -> None:
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            if path.is_file():
                read_json_stable(path)
                return
            if process.poll() is not None:
                raise RuntimeError(f"{label} exited before publishing its status")
            time.sleep(0.25)
        raise RuntimeError(f"{label} did not publish its status within the startup bound")

    def cleanup_startup_failure() -> None:
        """Do not orphan a partially started generation after a launch error."""

        for process in reversed(tuple(processes.values())):
            pid = process.get("pid")
            if not isinstance(pid, int):
                continue
            try:
                os.killpg(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                continue

    def validate_initial_status(
        path: Path,
        label: str,
        *,
        process_key: str | None = None,
        watchdog: bool = False,
    ) -> dict[str, object]:
        status = read_json_stable(path)
        process_key = process_key or ("watchdog" if watchdog else label)
        expected_process = processes.get(process_key)
        if not isinstance(expected_process, dict):
            raise RuntimeError(f"{label} launch process identity was not recorded")
        required = {
            "generation_id": preregistration.generation_id,
            "preregistration_sha256": preregistration_sha256,
            "repository_commit": preregistration.repository_commit,
            "evidence_class": LONG_RUN_EVIDENCE_CLASS,
            "admission_eligible": False,
            "phase4_materialization_eligible": False,
            "runtime_attestation_sha256": preregistration.runtime_attestation_sha256,
            "credentials_loaded": False,
            "order_writes_attempted": False,
            "execution_authority_present": False,
        }
        if any(status.get(key) != value for key, value in required.items()):
            raise RuntimeError(f"{label} initial identity/security state failed")
        pid_field = (
            "watchdog_pid" if watchdog else "scheduler_pid" if process_key == "scheduler" else "pid"
        )
        status_pid = status.get(pid_field)
        expected_pid = expected_process.get("pid")
        expected_create_time = expected_process.get("process_create_time")
        status_create_time = status.get("process_create_time")
        if (
            not isinstance(status_pid, int)
            or status_pid != expected_pid
            or not isinstance(status_create_time, (int, float))
            or not isinstance(expected_create_time, (int, float))
            or float(status_create_time) != float(expected_create_time)
        ):
            raise RuntimeError(f"{label} status process identity differs from its launched process")
        status_command = status.get("command")
        status_identity = status.get("command_identity")
        if (
            not isinstance(status_command, list)
            or not status_command
            or not isinstance(status_identity, str)
            or status_identity != process_command_identity([str(item) for item in status_command])
            or not process_identity_matches(
                status_pid,
                status_identity,
                [str(item) for item in status_command],
                expected_process_create_time=float(status_create_time),
            )
        ):
            raise RuntimeError(f"{label} status command identity is not live and self-consistent")
        state_field = "scientific_state" if watchdog else "state"
        state = str(status.get(state_field))
        allowed_states = {"RUNNING_WARMUP", "RUNNING"}
        if process_key == "scheduler":
            state = str(status.get("state"))
            allowed_states = {"WAITING_FOR_FIRST_CUTOFF"}
        if state not in allowed_states:
            raise RuntimeError(f"{label} did not start in an active {state_field}")
        if watchdog:
            if status.get("decision") != "LONG_RUN_HEALTHY":
                raise RuntimeError("watchdog initial scientific decision was not healthy")
            if status.get("fatal_history_count") != 0:
                raise RuntimeError("watchdog initial fatal history is not empty")
        return status

    try:
        collector_process = start("collector")
        wait_for_status(source_root / "status.json", collector_process, "collector")
        candidate_process = start("candidate")
        wait_for_status(candidate_root / "status.json", candidate_process, "candidate")
        outcomes_process = start("outcomes")
        wait_for_status(candidate_root / "outcome-status.json", outcomes_process, "outcome linker")
        watchdog_process = start("watchdog")
        wait_for_status(watchdog_root / "status.json", watchdog_process, "watchdog")
        validate_initial_status(source_root / "status.json", "collector")
        candidate_initial = validate_initial_status(candidate_root / "status.json", "candidate")
        validate_initial_status(
            candidate_root / "outcome-status.json", "outcome linker", process_key="outcomes"
        )
        validate_initial_status(watchdog_root / "status.json", "watchdog", watchdog=True)
        if (
            candidate_initial.get("prediction_count") != 0
            or candidate_initial.get("rejection_count") != 0
        ):
            raise RuntimeError("candidate initial counters are not empty")
        scheduler_process = start("scheduler")
        wait_for_status(scheduler_root / "status.json", scheduler_process, "scheduler")
        validate_initial_status(
            scheduler_root / "status.json", "scheduler", process_key="scheduler"
        )
    except Exception:
        cleanup_startup_failure()
        raise
    try:
        launch_metadata["state"] = "RUNNING"
        launch_metadata["processes"] = processes
        write_json_atomic(evidence_root / "launch.json", launch_metadata)
    except Exception:
        cleanup_startup_failure()
        raise
    return launch_metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--preregistration-sha256", required=True)
    parser.add_argument("--readiness-report", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--qualification-evidence", type=Path, required=True)
    parser.add_argument("--requirements-lock", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--phase3-gate", type=Path, required=True)
    parser.add_argument("--model-runtime-qualification", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = launch(
            launch=args.launch,
            repository_root=args.repository_root,
            preregistration_path=args.preregistration,
            preregistration_sha256=args.preregistration_sha256,
            readiness_report_path=args.readiness_report,
            evidence_root=args.evidence_root,
            admission_path=args.admission,
            qualification_evidence_path=args.qualification_evidence,
            requirements_lock_path=args.requirements_lock,
            checkpoint_path=args.checkpoint,
            phase3_gate_path=args.phase3_gate,
            model_runtime_qualification_path=args.model_runtime_qualification,
        )
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        refusal = {
            "schema": "advisorai.phase4.v3-core.long-run.launch-refusal.v1",
            "state": "LONGRUN_NOT_LAUNCHED_PREFLIGHT_FAILED",
            "error_type": type(exc).__name__,
            "reason": str(exc),
            "credentials_loaded": False,
            "order_writes_attempted": False,
            "execution_authority_present": False,
        }
        print(json.dumps(refusal, sort_keys=True, separators=(",", ":")))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
