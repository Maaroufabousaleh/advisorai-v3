#!/usr/bin/env python3
"""Create the offline, pre-outcome V3-Core Phase-4 cadence contract.

This command reads only reviewed local configuration and immutable evidence. It
does not acquire data, load credentials or model weights, call a network, or
submit an order. When no eligible five-minute PIT case set is present it writes
that blocker explicitly instead of fabricating evaluation cases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from advisorai.phase4 import V3CorePhase4Preregistration

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PHASE3_GATE = REPOSITORY_ROOT / (
    "artifacts/phase3/formal-admission/20260812T013505Z-with-passed-phase2-post-phase2-commit/"
    "phase3-gate-record.json"
)
PHASE3_GATE_SHA256 = "4e00850787cc6dcd95cadcd6152f74d4875bf480d219d07736706dd47a11d232"
CONSUMED_DAILY_INPUT = REPOSITORY_ROOT / (
    "artifacts/phase4/real-utility-input/20260812T023000Z-btc-eth-daily-snapshot-ttm-r2-r3-v3/"
    "phase4-paper-utility-input.json"
)
R7_ROOT = REPOSITORY_ROOT / (
    "artifacts/phase3/public-market-data-durable/20260811T182252Z-four-hour-r7-validator-fix"
)
R7_SAMPLES = R7_ROOT / "samples.jsonl"
CHRONOS_QUARANTINE = REPOSITORY_ROOT / (
    "artifacts/phase0/model-runtime-qualification-first-run/chronos-2-small.json"
)
CHRONOS_MEASURED = REPOSITORY_ROOT / (
    "artifacts/phase0/model-runtime-qualification/20260807T214648.132422Z/chronos-2-small.json"
)
CHRONOS_WORKER = REPOSITORY_ROOT / "scripts/runtime_qualification_worker.py"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().lower()


def _canonical(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _write_new(path: Path, payload: object) -> str:
    encoded = _canonical(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return hashlib.sha256(encoded).hexdigest()


def _chronos_identity_audit() -> dict[str, Any]:
    if not CHRONOS_QUARANTINE.is_file() or not CHRONOS_MEASURED.is_file():
        return {
            "status": "QUARANTINED_EVIDENCE_MISSING",
            "quarantine_evidence": _relative(CHRONOS_QUARANTINE),
            "measured_evidence": _relative(CHRONOS_MEASURED),
        }
    quarantine = json.loads(CHRONOS_QUARANTINE.read_text(encoding="utf-8"))
    measured = json.loads(CHRONOS_MEASURED.read_text(encoding="utf-8"))
    measured_pin = measured["candidate"]["runtime_pin"]
    runner_version = str(measured_pin["runner_version"])
    current_worker_hash = hashlib.sha256(
        f"{runner_version}\n{hashlib.sha256(CHRONOS_WORKER.read_bytes()).hexdigest()}".encode()
    ).hexdigest()
    recorded_worker_hash = str(measured_pin["runner_hash"])
    return {
        "status": (
            "QUARANTINED_RUNTIME_IDENTITY_MISMATCH"
            if current_worker_hash != recorded_worker_hash
            else "MEASURED_IDENTITY_MATCHES_PRESERVED_ROOT"
        ),
        "quarantine_evidence": _relative(CHRONOS_QUARANTINE),
        "quarantine_evidence_sha256": _sha256(CHRONOS_QUARANTINE),
        "quarantine_status": quarantine.get("status"),
        "measured_evidence": _relative(CHRONOS_MEASURED),
        "measured_evidence_sha256": _sha256(CHRONOS_MEASURED),
        "measured_status": measured.get("status"),
        "runner_version": runner_version,
        "current_worker_hash": current_worker_hash,
        "recorded_worker_hash": recorded_worker_hash,
        "worker_match": current_worker_hash == recorded_worker_hash,
        "output_schema": measured["candidate"].get("output_schema"),
        "checkpoint_revision": measured["candidate"]["external_checkpoint"]["repository"][
            "revision"
        ],
        "runtime_lock_hash": measured_pin.get("lock_hash"),
        "decision": "do_not_enter_utility_comparison_until_identity_is_requalified",
    }


def _data_readiness() -> dict[str, Any]:
    samples = {
        "path": _relative(R7_SAMPLES),
        "exists": R7_SAMPLES.is_file(),
        "sha256": _sha256(R7_SAMPLES) if R7_SAMPLES.is_file() else None,
    }
    daily = {
        "path": _relative(CONSUMED_DAILY_INPUT),
        "exists": CONSUMED_DAILY_INPUT.is_file(),
        "sha256": _sha256(CONSUMED_DAILY_INPUT) if CONSUMED_DAILY_INPUT.is_file() else None,
        "reuse_status": "CONSUMED_AND_NOT_REUSED",
    }
    return {
        "status": "PENDING_FRESH_PIT_DATA",
        "eligible_input_present": False,
        "r7_qualification_telemetry": samples,
        "prior_daily_input": daily,
        "reasons": [
            "r7_samples_are_source_qualification_telemetry_not_5m_ohlcv_case_input",
            "existing_immutable_roots_do_not_contain_4h_context_plus_1h_outcomes_as_a_single_PIT_window",
            "the_consumed_daily_phase4_input_is_not_reused",
        ],
        "next_admissible_action": (
            "obtain_or_accumulate_a_reviewed_5m_PIT_window_with_4h_context_and_1h_outcomes;"
            "then_build_cases_with_build_phase4_v3core_cadence_input.py"
        ),
    }


def build_preregistration(*, generated_at: datetime | None = None) -> dict[str, Any]:
    timestamp = (generated_at or datetime.now(UTC)).astimezone(UTC)
    plan = V3CorePhase4Preregistration()
    source_files = (
        REPOSITORY_ROOT / "configs/v3_core.yaml",
        REPOSITORY_ROOT / "src/advisorai/phase4/v3core_cadence.py",
        REPOSITORY_ROOT / "src/advisorai/phase4/v3core_forward.py",
        REPOSITORY_ROOT / "src/advisorai/phase4/paper_utility.py",
        REPOSITORY_ROOT / "scripts/collect_phase4_v3core_forward.py",
        REPOSITORY_ROOT / "scripts/review_phase4_utility.py",
    )
    return {
        "schema": plan.schema_version,
        "generated_at": timestamp.isoformat(),
        "repository_commit": _git_head(),
        "plan": plan.model_dump(mode="json"),
        "source_code_sha256": {_relative(path): _sha256(path) for path in source_files},
        "phase3_gate": {
            "path": _relative(PHASE3_GATE),
            "sha256": _sha256(PHASE3_GATE) if PHASE3_GATE.is_file() else PHASE3_GATE_SHA256,
            "decision": "PASSED",
        },
        "data_readiness": _data_readiness(),
        "chronos_runtime_identity": _chronos_identity_audit(),
        "measurement_status": "PENDING_FRESH_PIT_DATA",
        "network_calls": 0,
        "credentials_loaded": False,
        "model_weights_loaded": False,
        "order_writes_attempted": False,
        "execution_authority": {
            "risk_kernel": "unchanged_external_authority",
            "oms": "unchanged_external_authority",
            "model_order_authority": False,
            "dashboard_order_authority": False,
        },
        "notes": [
            "The daily Phase-4 holdout and the 13-policy search remain frozen and are not reused.",
            "TTM-R2 remains a challenger; TTM-R3 remains research-only.",
            "Chronos-2-small remains quarantined until its current worker identity is requalified.",
            "This artifact pre-registers a review; it is not Phase-4 admission evidence.",
            "LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.",
        ],
    }


def write_preregistration(output_root: Path) -> dict[str, str]:
    if output_root.exists():
        raise FileExistsError(f"output root already exists: {output_root}")
    payload = build_preregistration()
    output_root.mkdir(parents=True)
    evidence_path = output_root / "phase4-v3core-cadence-preregistration.json"
    evidence_sha256 = _write_new(evidence_path, payload)
    digest_path = output_root / "phase4-v3core-cadence-preregistration.sha256"
    digest_path.write_text(f"{evidence_sha256}  {evidence_path.name}\n", encoding="ascii")
    manifest = {
        "schema": "advisorai.phase4.v3-core-preregistration-manifest.v1",
        "evidence": _relative(evidence_path),
        "evidence_sha256": evidence_sha256,
        "measurement_status": payload["measurement_status"],
        "network_calls": payload["network_calls"],
        "credentials_loaded": payload["credentials_loaded"],
        "order_writes_attempted": payload["order_writes_attempted"],
    }
    manifest_path = output_root / "evidence-manifest.json"
    manifest_sha256 = _write_new(manifest_path, manifest)
    return {
        "root": _relative(output_root),
        "evidence": _relative(evidence_path),
        "evidence_sha256": evidence_sha256,
        "manifest_sha256": manifest_sha256,
        "measurement_status": payload["measurement_status"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = write_preregistration(args.output_root.resolve())
    except (FileExistsError, OSError, KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"Phase-4 cadence preregistration refused ({type(exc).__name__})") from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
