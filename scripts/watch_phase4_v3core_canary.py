#!/usr/bin/env python3
"""Read-only watchdog for the bounded prospective V3-Core canary.

The watchdog never starts, stops, or restarts a process.  It writes a
fail-closed status marker so an operator can see a scientific failure promptly.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from advisorai.phase4 import (
    CANARY_EVIDENCE_CLASS,
    load_canary_preregistration,
    sha256_file,
)

WATCHDOG_SCHEMA = "advisorai.phase4.v3-core.prospective-canary.watchdog.v1"


def _write_atomic(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _pid_exists(pid: object) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def evaluate_once(
    *,
    preregistration: Path,
    preregistration_sha256: str,
    source_root: Path,
    candidate_root: Path,
) -> dict[str, object]:
    prereg = load_canary_preregistration(preregistration, expected_sha256=preregistration_sha256)
    source_status = _load_json(source_root / "status.json")
    candidate_status = _load_json(candidate_root / "status.json")
    reasons: list[str] = []
    now = datetime.now(UTC)
    source_state = str(source_status.get("state"))
    candidate_state = str(candidate_status.get("state"))
    if source_status.get("evidence_class") != CANARY_EVIDENCE_CLASS:
        reasons.append("source_evidence_class_mismatch")
    if source_status.get("admission_eligible") is not False:
        reasons.append("source_admission_flag_mismatch")
    if candidate_status.get("evidence_class") != CANARY_EVIDENCE_CLASS:
        reasons.append("candidate_evidence_class_mismatch")
    if candidate_status.get("admission_eligible") is not False:
        reasons.append("candidate_admission_flag_mismatch")
    if source_status.get("credentials_loaded") is not False:
        reasons.append("unexpected_source_credentials")
    if source_status.get("order_writes_attempted") is not False:
        reasons.append("unexpected_source_order_writes")
    if candidate_status.get("credentials_loaded") is not False:
        reasons.append("unexpected_candidate_credentials")
    if candidate_status.get("order_writes_attempted") is not False:
        reasons.append("unexpected_candidate_order_writes")
    if int(candidate_status.get("rejection_count", 0)):
        reasons.append("candidate_rejection_observed")
    if int(source_status.get("finality", {}).get("post_admission_revision_count", 0)):
        reasons.append("post_admission_revision_observed")
    if source_state == "CANARY_FAILED":
        reasons.append("source_canary_failed")
    if candidate_state == "CANARY_FAILED":
        reasons.append("candidate_canary_failed")
    source_pid_alive = _pid_exists(source_status.get("pid"))
    candidate_pid_alive = _pid_exists(candidate_status.get("pid"))
    before_deadline = now < prereg.target_end_at
    if before_deadline and source_state == "running" and not source_pid_alive:
        reasons.append("source_process_missing_before_deadline")
    if before_deadline and candidate_state == "running" and not candidate_pid_alive:
        reasons.append("candidate_process_missing_before_deadline")
    if not before_deadline and source_state == "running":
        reasons.append("source_not_terminal_after_deadline")
    if not before_deadline and candidate_state == "running":
        reasons.append("candidate_not_terminal_after_deadline")
    if reasons:
        decision = "CANARY_FAILED"
    elif (
        not before_deadline
        and source_state == "deadline_reached"
        and candidate_state == "deadline_reached"
    ):
        decision = "CANARY_COMPLETE_PENDING_AUDIT"
    else:
        decision = "CANARY_HEALTHY"
    return {
        "schema": WATCHDOG_SCHEMA,
        "canary_id": prereg.canary_id,
        "observed_at": now.isoformat(),
        "target_end_at": prereg.target_end_at.isoformat(),
        "decision": decision,
        "reasons": list(dict.fromkeys(reasons)),
        "source_state": source_state,
        "candidate_state": candidate_state,
        "source_pid_alive": source_pid_alive,
        "candidate_pid_alive": candidate_pid_alive,
        "source_raw_receipts": source_status.get("raw_response_count"),
        "source_admitted_final_bars": source_status.get("admitted_final_bar_count"),
        "candidate_prediction_counts": candidate_status.get("prediction_counts"),
        "candidate_rejection_count": candidate_status.get("rejection_count"),
        "evidence_class": CANARY_EVIDENCE_CLASS,
        "admission_eligible": False,
        "preregistration_sha256": preregistration_sha256,
        "source_manifest_sha256": sha256_file(source_root / "manifest.json"),
        "candidate_manifest_sha256": sha256_file(candidate_root / "manifest.json"),
    }


def run_watchdog(
    *,
    preregistration: Path,
    preregistration_sha256: str,
    source_root: Path,
    candidate_root: Path,
    output_root: Path,
    poll_seconds: float,
    once: bool,
) -> int:
    if poll_seconds <= 0:
        raise ValueError("watchdog poll interval must be positive")
    output_root.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            report = evaluate_once(
                preregistration=preregistration,
                preregistration_sha256=preregistration_sha256,
                source_root=source_root,
                candidate_root=candidate_root,
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            report = {
                "schema": WATCHDOG_SCHEMA,
                "observed_at": datetime.now(UTC).isoformat(),
                "decision": "CANARY_FAILED",
                "reasons": [f"watchdog_input_error:{type(exc).__name__}"],
                "evidence_class": CANARY_EVIDENCE_CLASS,
                "admission_eligible": False,
            }
        _write_atomic(output_root / "status.json", report)
        with (output_root / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if once or report.get("decision") in {"CANARY_FAILED", "CANARY_COMPLETE_PENDING_AUDIT"}:
            return 0 if report.get("decision") != "CANARY_FAILED" else 1
        time.sleep(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--preregistration-sha256", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    return run_watchdog(
        preregistration=args.preregistration.resolve(),
        preregistration_sha256=args.preregistration_sha256,
        source_root=args.source_root.resolve(),
        candidate_root=args.candidate_root.resolve(),
        output_root=args.output_root.resolve(),
        poll_seconds=args.poll_seconds,
        once=args.once,
    )


if __name__ == "__main__":
    raise SystemExit(main())
