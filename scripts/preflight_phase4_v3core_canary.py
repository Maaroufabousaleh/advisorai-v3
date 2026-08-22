#!/usr/bin/env python3
"""Run the fail-closed, offline preflight for one prospective canary."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from advisorai.phase4 import (
    CANARY_CONTEXT_BARS,
    CANARY_CONTEXT_LAG_SECONDS,
    CANARY_EVIDENCE_CLASS,
    CANARY_FINALITY_GUARD_SECONDS,
    CANARY_REPEAT_RECEIPTS,
    CanaryPreflightCheck,
    CanaryPreflightReport,
    ChronosRuntimeIdentity,
    load_canary_preregistration,
    sha256_file,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _git_head() -> str:
    result = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().lower()


def _nvidia_snapshot() -> tuple[bool, str]:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return False, "nvidia-smi is unavailable"
    try:
        gpu = subprocess.run(
            [executable, "--query-gpu=name,driver_version", "--format=csv,noheader"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        apps = subprocess.run(
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
        return False, f"nvidia-smi read-only query failed: {type(exc).__name__}"
    resident = [line.strip() for line in apps.stdout.splitlines() if line.strip()]
    if resident:
        return False, "GPU compute applications are already resident: " + "; ".join(resident)
    return True, f"GPU available: {gpu.stdout.strip()}"


def _windows_power_snapshot() -> tuple[bool, str]:
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if powershell is None:
        return False, "Windows power/sleep state could not be inspected from this environment"
    command = (
        "$b=Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue; "
        "$s=powercfg /getactivescheme; "
        "if($b){$b.BatteryStatus}else{'NO_BATTERY_OBJECT'}; $s"
    )
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Windows power state query failed: {type(exc).__name__}"
    output = result.stdout.strip().replace("\r", "")
    # A missing battery object is common on a desktop/VM; it is not proof of
    # AC power, so the operator must confirm it manually.
    if "NO_BATTERY_OBJECT" in output:
        return False, "AC power could not be proven automatically; operator confirmation required"
    return True, f"read-only Windows power snapshot: {output}"


def _write_immutable(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise RuntimeError("existing canary preflight report conflicts")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()


def run_preflight(
    *,
    preregistration: Path,
    preregistration_sha256: str,
    source_root: Path,
    candidate_root: Path,
    output_path: Path,
    admission: Path,
    qualification_evidence: Path,
    phase3_gate_sha256: str,
) -> CanaryPreflightReport:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prereg = load_canary_preregistration(preregistration, expected_sha256=preregistration_sha256)
    checks: list[CanaryPreflightCheck] = []

    def add(name: str, passed: bool, reason: str) -> None:
        checks.append(CanaryPreflightCheck(name=name, passed=passed, reason=reason))

    head = _git_head()
    add("repository_identity", head == prereg.repository_commit, f"head={head}")
    add(
        "preregistration_identity",
        sha256_file(preregistration) == preregistration_sha256,
        "immutable preregistration hash matches",
    )
    add(
        "phase3_gate_identity",
        phase3_gate_sha256 == prereg.phase3_gate_sha256,
        "Phase-3 gate hash matches",
    )
    add(
        "source_root_fresh",
        not source_root.exists() or not any(source_root.iterdir()),
        "source root is absent or empty before first canary receipt",
    )
    add(
        "candidate_root_fresh",
        not candidate_root.exists() or not any(candidate_root.iterdir()),
        "candidate root is absent or empty before first canary receipt",
    )
    add(
        "canary_evidence_class",
        prereg.evidence_class == CANARY_EVIDENCE_CLASS,
        "explicit non-admission class",
    )
    add("admission_disabled", prereg.admission_eligible is False, "admission_eligible=false")
    add(
        "materialization_disabled",
        prereg.phase4_materialization_eligible is False,
        "phase4_materialization_eligible=false",
    )
    add(
        "source_contract",
        (
            prereg.context_bars == CANARY_CONTEXT_BARS
            and prereg.context_newest_lag_seconds == CANARY_CONTEXT_LAG_SECONDS
            and prereg.finality_guard_seconds == CANARY_FINALITY_GUARD_SECONDS
            and prereg.repeat_requirement == CANARY_REPEAT_RECEIPTS
            and prereg.distinct_receipts_required
        ),
        "60-second guard, two distinct receipts, and cutoff-minus-10-minute context are frozen",
    )
    add("credentials_prohibited", prereg.credentials_prohibited is True, "credentials prohibited")
    add("orders_prohibited", prereg.orders_prohibited is True, "orders prohibited")

    try:
        identity = ChronosRuntimeIdentity.from_admission(
            admission.resolve(),
            qualification_evidence_path=qualification_evidence.resolve(),
            repository_root=REPOSITORY_ROOT,
        )
        runtime_ok = (
            identity.checkpoint_hash == prereg.checkpoint_sha256
            and identity.preprocessing_hash == prereg.preprocessing_sha256
            and identity.lock_hash == prereg.dependency_lock_sha256
            and identity.device == "cuda"
            and identity.output_bars == 30
        )
        runtime_reason = "qualified local Chronos-2-small identity matches preregistration"
    except (OSError, KeyError, TypeError, ValueError) as exc:
        identity = None
        runtime_ok = False
        runtime_reason = f"qualified local Chronos identity refused: {type(exc).__name__}"
    add("chronos_runtime_identity", runtime_ok, runtime_reason)

    files = {
        "collector_code": REPOSITORY_ROOT / "scripts/collect_phase4_v3core_canary.py",
        "finality_code": REPOSITORY_ROOT / "src/advisorai/phase4/v3core_canary.py",
        "chronos_worker": REPOSITORY_ROOT / "src/advisorai/phase4/v3core_chronos.py",
        "chronos_runner": REPOSITORY_ROOT / "scripts/run_phase4_v3core_canary_chronos.py",
        "watchdog": REPOSITORY_ROOT / "scripts/watch_phase4_v3core_canary.py",
        "terminal_audit": REPOSITORY_ROOT / "scripts/audit_phase4_v3core_canary.py",
    }
    expected_hashes = {
        "collector_code": prereg.collector_code_sha256,
        "finality_code": prereg.finality_code_sha256,
        "chronos_worker": prereg.chronos_worker_code_sha256,
        "chronos_runner": prereg.chronos_runner_sha256,
        "watchdog": prereg.watchdog_sha256,
        "terminal_audit": prereg.terminal_audit_sha256,
    }
    for name, path in files.items():
        add(
            name + "_identity",
            path.is_file() and sha256_file(path) == expected_hashes[name],
            str(path),
        )

    add(
        "finality_constants",
        prereg.fail_fast_policy_sha256 == prereg.finality_code_sha256,
        "fail-fast identity is bound to the reviewed finality module",
    )
    add(
        "clock_window",
        datetime.now(UTC) <= prereg.target_end_at,
        "wall clock is before fixed canary deadline",
    )
    add(
        "disk_space",
        shutil.disk_usage(output_path.parent.resolve()).free >= 1_000_000_000,
        "at least 1 GB free at the canary artifact location",
    )
    gpu_ok, gpu_reason = _nvidia_snapshot()
    add("gpu_lease_free", gpu_ok, gpu_reason)
    power_ok, power_reason = _windows_power_snapshot()
    add("machine_power_sleep_check", power_ok, power_reason)
    add(
        "no_network_preflight",
        True,
        "preflight performed no market-data or provider call",
    )
    add(
        "no_credentials_or_orders",
        True,
        "preflight loaded no credentials and has no order operation",
    )

    refusal_reasons = tuple(check.name + ":" + check.reason for check in checks if not check.passed)
    decision = "CANARY_READY" if not refusal_reasons else "REFUSE_CANARY"
    unsigned = {
        "schema": "advisorai.phase4.v3-core.prospective-canary.preflight.v1",
        "decision": decision,
        "canary_id": prereg.canary_id,
        "evidence_class": CANARY_EVIDENCE_CLASS,
        "admission_eligible": False,
        "checks": [check.model_dump(mode="json") for check in checks],
        "refusal_reasons": list(refusal_reasons),
    }
    from advisorai.phase4.v3core_cadence import sha256_json

    report = CanaryPreflightReport(**unsigned, report_hash=sha256_json(unsigned))
    _write_immutable(output_path, report.model_dump(mode="json"))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--preregistration-sha256", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--qualification-evidence", type=Path, required=True)
    parser.add_argument("--phase3-gate-sha256", required=True)
    args = parser.parse_args()
    try:
        report = run_preflight(
            preregistration=args.preregistration.resolve(),
            preregistration_sha256=args.preregistration_sha256,
            source_root=args.source_root.resolve(),
            candidate_root=args.candidate_root.resolve(),
            output_path=args.output.resolve(),
            admission=args.admission,
            qualification_evidence=args.qualification_evidence,
            phase3_gate_sha256=args.phase3_gate_sha256,
        )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"prospective canary preflight refused ({type(exc).__name__})") from exc
    print(report.model_dump_json())
    return 0 if report.decision == "CANARY_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
