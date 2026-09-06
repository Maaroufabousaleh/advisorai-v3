#!/usr/bin/env python3
"""Arm one OS-waiting gate for an immutable V3-Core long-run launch.

The gate has no scientific authority. It records its own append-only control
events, waits until the preregistered UTC launch window, and delegates the
actual identity attestation and component startup to the reviewed launcher.
It never changes the preregistration or slides the launch window.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from advisorai.phase4.v3core_canary import sha256_file
from advisorai.phase4.v3core_longrun_runtime import (
    LongRunLaunchReadinessReport,
    LongRunPreregistration,
    load_long_run_preregistration,
    process_command_identity,
    process_create_time,
    read_json_stable,
    write_json_atomic,
)

GATE_SCHEMA = "advisorai.phase4.v3-core.long-run.launch-gate.v1"
PRESTART_STATE = "PRESTART_WAITING_VALID_GATE"
PREFLIGHT_FAILED_STATE = "LONGRUN_NOT_LAUNCHED_PREFLIGHT_FAILED"
MISSED_WINDOW_STATE = "LONGRUN_NOT_LAUNCHED_MISSED_START_WINDOW"
LAUNCHED_STATE = "LONGRUN_LAUNCHED"


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _append_event(path: Path, payload: Mapping[str, object]) -> None:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _write_exclusive(path: Path, value: str) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _wait_until(
    target: datetime,
    *,
    now: Callable[[], datetime] = _utcnow,
    sleep: Callable[[float], None] = time.sleep,
) -> datetime:
    """Wait against UTC wall time and prove the action never runs early."""

    while True:
        observed = now().astimezone(UTC)
        remaining = (target - observed).total_seconds()
        if remaining <= 0:
            return observed
        sleep(min(60.0, remaining))


def _validate_launch_window(preregistration: LongRunPreregistration) -> tuple[datetime, datetime]:
    before = preregistration.launch_not_before_at
    after = preregistration.launch_not_after_at
    if before is None or after is None:
        raise ValueError("preregistration has no reviewed post-start launch window")
    if before != preregistration.start_at or after <= before:
        raise ValueError("preregistration launch window is inconsistent")
    return before, after


def _build_launch_command(
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
) -> list[str]:
    return [
        sys.executable,
        str(repository_root / "scripts/launch_phase4_v3core_longrun.py"),
        "--launch",
        "--repository-root",
        str(repository_root),
        "--preregistration",
        str(preregistration_path),
        "--preregistration-sha256",
        preregistration_sha256,
        "--readiness-report",
        str(readiness_report_path),
        "--evidence-root",
        str(evidence_root),
        "--admission",
        str(admission_path),
        "--qualification-evidence",
        str(qualification_evidence_path),
        "--requirements-lock",
        str(requirements_lock_path),
        "--checkpoint",
        str(checkpoint_path),
        "--phase3-gate",
        str(phase3_gate_path),
        "--model-runtime-qualification",
        str(model_runtime_qualification_path),
    ]


def _sanitized_environment(repository_root: Path) -> dict[str, str]:
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
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in allowed
        and not any(marker in key.lower() for marker in ("secret", "token", "password", "key"))
    }
    environment["PYTHONPATH"] = str(repository_root / "src")
    environment["ADVISORAI_CREDENTIALS_DISABLED"] = "1"
    environment["ADVISORAI_ORDER_CAPABILITY_DISABLED"] = "1"
    return environment


def _current_command() -> list[str]:
    try:
        import psutil
    except ImportError as exc:
        raise RuntimeError("launch-gate process identity requires psutil") from exc
    try:
        command = psutil.Process(os.getpid()).cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as exc:
        raise RuntimeError("launch-gate process identity is unavailable") from exc
    if not command:
        raise RuntimeError("launch-gate process command is empty")
    return command


def _gate_payload(
    *,
    state: str,
    detail: str,
    observed_at: datetime,
    preregistration: LongRunPreregistration,
    preregistration_sha256: str,
    launch_not_before_at: datetime,
    launch_not_after_at: datetime,
    command: Sequence[str],
) -> dict[str, object]:
    return {
        "schema": GATE_SCHEMA,
        "generation_id": preregistration.generation_id,
        "state": state,
        "detail": detail,
        "observed_at": _iso(observed_at),
        "preregistration_sha256": preregistration_sha256,
        "repository_commit": preregistration.repository_commit,
        "launch_not_before_at": _iso(launch_not_before_at),
        "launch_not_after_at": _iso(launch_not_after_at),
        "supervisor_pid": os.getpid(),
        "process_create_time": process_create_time(os.getpid()),
        "command": list(command),
        "command_identity": process_command_identity(command),
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "execution_authority_present": False,
    }


def arm(
    *,
    repository_root: Path,
    preregistration_path: Path,
    preregistration_sha256: str,
    readiness_report_path: Path,
    evidence_root: Path,
    gate_root: Path,
    admission_path: Path,
    qualification_evidence_path: Path,
    requirements_lock_path: Path,
    checkpoint_path: Path,
    phase3_gate_path: Path,
    model_runtime_qualification_path: Path,
    arm_requested: bool,
    now: Callable[[], datetime] = _utcnow,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    if not arm_requested:
        raise ValueError("launch gate requires explicit --arm")
    repository_root = repository_root.resolve()
    preregistration_path = preregistration_path.resolve()
    preregistration = load_long_run_preregistration(
        preregistration_path, expected_sha256=preregistration_sha256
    )
    launch_not_before, launch_not_after = _validate_launch_window(preregistration)
    expected_gate_hash = preregistration.launch_gate_code_sha256
    if expected_gate_hash is None or sha256_file(Path(__file__).resolve()) != expected_gate_hash:
        raise ValueError("launch-gate code identity differs from preregistration")
    readiness = LongRunLaunchReadinessReport.model_validate(
        read_json_stable(readiness_report_path.resolve())
    )
    if (
        readiness.decision != "LONG_RUN_READY"
        or not readiness.long_run_ready
        or readiness.preregistration_sha256 != preregistration_sha256
        or not all(check.passed for check in readiness.checks)
    ):
        raise ValueError("readiness report does not authorize this preregistration")
    observed = now().astimezone(UTC)
    if observed > launch_not_after:
        raise ValueError("launch start window was already missed")
    if evidence_root.resolve().exists() and any(evidence_root.resolve().iterdir()):
        raise ValueError("long-run evidence root is not empty")
    gate_root = gate_root.resolve()
    if gate_root.exists() and any(gate_root.iterdir()):
        raise ValueError("launch-gate root is not empty")
    gate_root.mkdir(parents=True, exist_ok=True)
    command = _current_command()
    events_path = gate_root / "events.jsonl"
    status_path = gate_root / "status.json"
    lock_path = gate_root / "gate.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another launch gate owns this root") from exc

        def publish(state: str, detail: str, at: datetime) -> dict[str, object]:
            payload = _gate_payload(
                state=state,
                detail=detail,
                observed_at=at,
                preregistration=preregistration,
                preregistration_sha256=preregistration_sha256,
                launch_not_before_at=launch_not_before,
                launch_not_after_at=launch_not_after,
                command=command,
            )
            _append_event(events_path, payload)
            write_json_atomic(status_path, payload)
            return payload

        publish(PRESTART_STATE, "waiting for the immutable UTC launch window", observed)
        launch_observed = _wait_until(launch_not_before, now=now, sleep=sleep)
        if launch_observed > launch_not_after:
            return publish(
                MISSED_WINDOW_STATE,
                "the immutable launch window expired before launch-time attestation",
                launch_observed,
            )
        publish("FINAL_LAUNCH_ATTESTATION", "delegating to the reviewed launcher", launch_observed)
        launch_command = _build_launch_command(
            repository_root=repository_root,
            preregistration_path=preregistration_path,
            preregistration_sha256=preregistration_sha256,
            readiness_report_path=readiness_report_path.resolve(),
            evidence_root=evidence_root.resolve(),
            admission_path=admission_path.resolve(),
            qualification_evidence_path=qualification_evidence_path.resolve(),
            requirements_lock_path=requirements_lock_path.resolve(),
            checkpoint_path=checkpoint_path.resolve(),
            phase3_gate_path=phase3_gate_path.resolve(),
            model_runtime_qualification_path=model_runtime_qualification_path.resolve(),
        )
        terminal_at = launch_observed
        try:
            completed = subprocess.run(
                launch_command,
                cwd=repository_root,
                env=_sanitized_environment(repository_root),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
            )
            _write_exclusive(gate_root / "launch.stdout.log", completed.stdout)
            _write_exclusive(gate_root / "launch.stderr.log", completed.stderr)
            terminal_at = now().astimezone(UTC)
            if completed.returncode != 0:
                try:
                    refusal = json.loads(completed.stdout)
                except json.JSONDecodeError:
                    refusal = None
                reason = (
                    refusal.get("reason")
                    if isinstance(refusal, dict) and isinstance(refusal.get("reason"), str)
                    else completed.stderr.strip() or "launcher returned no structured reason"
                )
                return publish(
                    PREFLIGHT_FAILED_STATE,
                    f"reviewed launcher refused with exit code {completed.returncode}: {reason}",
                    terminal_at,
                )
            launch_result = json.loads(completed.stdout)
            if not isinstance(launch_result, dict) or launch_result.get("state") != "RUNNING":
                raise RuntimeError("reviewed launcher did not return a running launch identity")
        except (OSError, RuntimeError, json.JSONDecodeError) as exc:
            return publish(
                PREFLIGHT_FAILED_STATE,
                f"reviewed launcher failed closed: {type(exc).__name__}: {exc}",
                terminal_at,
            )
        return publish(LAUNCHED_STATE, "reviewed launcher completed bounded startup", terminal_at)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="store_true")
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--preregistration-sha256", required=True)
    parser.add_argument("--readiness-report", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--qualification-evidence", type=Path, required=True)
    parser.add_argument("--requirements-lock", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--phase3-gate", type=Path, required=True)
    parser.add_argument("--model-runtime-qualification", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = arm(
            repository_root=args.repository_root,
            preregistration_path=args.preregistration,
            preregistration_sha256=args.preregistration_sha256,
            readiness_report_path=args.readiness_report,
            evidence_root=args.evidence_root,
            gate_root=args.gate_root,
            admission_path=args.admission,
            qualification_evidence_path=args.qualification_evidence,
            requirements_lock_path=args.requirements_lock,
            checkpoint_path=args.checkpoint,
            phase3_gate_path=args.phase3_gate,
            model_runtime_qualification_path=args.model_runtime_qualification,
            arm_requested=args.arm,
        )
    except (OSError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"long-run launch gate refused ({type(exc).__name__}): {exc}") from exc
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["state"] == LAUNCHED_STATE else 2


if __name__ == "__main__":
    raise SystemExit(main())
