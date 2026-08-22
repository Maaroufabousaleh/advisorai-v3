#!/usr/bin/env python3
"""Launch one preflight-approved bounded V3-Core canary.

This command launches only the canary collector, corrected Chronos worker, and
read-only watchdog.  It never launches the multi-day Phase-4 generation.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from advisorai.phase4 import (
    CANARY_EVIDENCE_CLASS,
    CanaryPreflightReport,
    load_canary_preregistration,
    sha256_file,
)


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write_new(path: Path, payload: object) -> None:
    encoded = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _wait_for(path: Path, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not path.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError(f"timed out waiting for {path}")
        time.sleep(0.25)


def _start(command: list[str], log_path: Path) -> subprocess.Popen[bytes]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=str(Path.cwd()),
            start_new_session=True,
        )
    finally:
        log.close()
    return process


def launch(
    *,
    preregistration: Path,
    preregistration_sha256: str,
    preflight: Path,
    source_root: Path,
    candidate_root: Path,
    watchdog_root: Path,
    admission: Path,
    qualification_evidence: Path,
    phase3_gate_sha256: str,
    repository_root: Path,
) -> dict[str, object]:
    prereg = load_canary_preregistration(preregistration, expected_sha256=preregistration_sha256)
    preflight_report = CanaryPreflightReport.model_validate(_load_json(preflight))
    if preflight_report.decision != "CANARY_READY":
        raise RuntimeError("canary preflight did not produce CANARY_READY")
    if preflight_report.canary_id != prereg.canary_id:
        raise RuntimeError("preflight and preregistration canary IDs differ")
    if (
        preflight_report.evidence_class != CANARY_EVIDENCE_CLASS
        or preflight_report.admission_eligible
    ):
        raise RuntimeError("canary launch requires explicit non-admission preflight flags")
    if sha256_file(preregistration) != preregistration_sha256:
        raise RuntimeError("canary preregistration changed before launch")
    for root in (source_root, candidate_root, watchdog_root):
        if root.exists() and any(root.iterdir()):
            raise RuntimeError(f"canary root is not fresh: {root}")
        root.mkdir(parents=True, exist_ok=True)
    launch_root = source_root.parent
    launch_record_path = launch_root / "launch.json"
    if launch_record_path.exists():
        raise RuntimeError("canary launch record already exists")

    scripts = repository_root / "scripts"
    source_command = [
        sys.executable,
        str(scripts / "collect_phase4_v3core_canary.py"),
        "--real",
        "--run-directory",
        str(source_root),
        "--repository-root",
        str(repository_root),
        "--preregistration",
        str(preregistration),
        "--phase3-gate-sha256",
        phase3_gate_sha256,
    ]
    collector = _start(source_command, source_root.parent / "collector.log")
    try:
        _wait_for(source_root / "manifest.json", 30.0)
        candidate_command = [
            sys.executable,
            str(scripts / "run_phase4_v3core_canary_chronos.py"),
            "--admission",
            str(admission),
            "--qualification-evidence",
            str(qualification_evidence),
            "--source-root",
            str(source_root),
            "--run-root",
            str(candidate_root),
            "--repository-root",
            str(repository_root),
            "--preregistration",
            str(preregistration),
            "--preregistration-sha256",
            preregistration_sha256,
            "--phase3-gate-sha256",
            phase3_gate_sha256,
        ]
        candidate = _start(candidate_command, candidate_root.parent / "candidate.log")
        _wait_for(candidate_root / "manifest.json", 30.0)
        watchdog_command = [
            sys.executable,
            str(scripts / "watch_phase4_v3core_canary.py"),
            "--preregistration",
            str(preregistration),
            "--preregistration-sha256",
            preregistration_sha256,
            "--source-root",
            str(source_root),
            "--candidate-root",
            str(candidate_root),
            "--output-root",
            str(watchdog_root),
        ]
        watchdog = _start(watchdog_command, watchdog_root.parent / "watchdog.log")
    except Exception:
        collector.terminate()
        collector.wait(timeout=10)
        raise

    record = {
        "schema": "advisorai.phase4.v3-core.prospective-canary.launch.v1",
        "canary_id": prereg.canary_id,
        "preregistration_sha256": preregistration_sha256,
        "preflight_report_sha256": sha256_file(preflight),
        "source_root": str(source_root.resolve()),
        "candidate_root": str(candidate_root.resolve()),
        "watchdog_root": str(watchdog_root.resolve()),
        "collector_pid": collector.pid,
        "candidate_pid": candidate.pid,
        "watchdog_pid": watchdog.pid,
        "collector_command": source_command,
        "candidate_command": candidate_command,
        "watchdog_command": watchdog_command,
        "target_end_at": prereg.target_end_at.isoformat(),
        "evidence_class": CANARY_EVIDENCE_CLASS,
        "admission_eligible": False,
        "credentials_loaded": False,
        "order_writes_attempted": False,
    }
    _write_new(launch_record_path, record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--preregistration-sha256", required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--watchdog-root", type=Path, required=True)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--qualification-evidence", type=Path, required=True)
    parser.add_argument("--phase3-gate-sha256", required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        result = launch(
            preregistration=args.preregistration.resolve(),
            preregistration_sha256=args.preregistration_sha256,
            preflight=args.preflight.resolve(),
            source_root=args.source_root.resolve(),
            candidate_root=args.candidate_root.resolve(),
            watchdog_root=args.watchdog_root.resolve(),
            admission=args.admission.resolve(),
            qualification_evidence=args.qualification_evidence.resolve(),
            phase3_gate_sha256=args.phase3_gate_sha256,
            repository_root=args.repository_root.resolve(),
        )
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise SystemExit(f"prospective canary launch refused ({type(exc).__name__})") from exc
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
