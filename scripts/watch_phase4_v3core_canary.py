#!/usr/bin/env python3
"""Read-only watchdog for the bounded prospective V3-Core canary.

The watchdog never starts, stops, or restarts a process.  It writes a
fail-closed status marker so an operator can see a scientific failure promptly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from advisorai.phase4 import (
    CANARY_EVIDENCE_CLASS,
    load_canary_preregistration,
)

WATCHDOG_SCHEMA = "advisorai.phase4.v3-core.prospective-canary.watchdog.v1"
SNAPSHOT_READ_ATTEMPTS = 3
SNAPSHOT_RETRY_SECONDS = 0.01


class WatchdogInputError(RuntimeError):
    """A fail-closed input error with the exact affected path retained."""

    def __init__(self, *, path: Path, operation: str, detail: str) -> None:
        self.path = path.resolve()
        self.operation = operation
        self.detail = detail
        super().__init__(f"{operation}:{self.path}:{detail}")


def _write_atomic(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _read_stable_bytes(
    path: Path,
    *,
    attempts: int = SNAPSHOT_READ_ATTEMPTS,
    retry_seconds: float = SNAPSHOT_RETRY_SECONDS,
) -> bytes:
    """Read one immutable/atomic artifact without accepting a torn snapshot."""

    if attempts < 1:
        raise ValueError("snapshot read attempts must be positive")
    last_detail = "unavailable"
    for attempt in range(attempts):
        try:
            before = path.stat()
            payload = path.read_bytes()
            after = path.stat()
        except FileNotFoundError as exc:
            last_detail = "missing"
            if attempt + 1 < attempts:
                time.sleep(retry_seconds)
                continue
            raise WatchdogInputError(path=path, operation="read", detail=last_detail) from exc
        except OSError as exc:
            raise WatchdogInputError(
                path=path, operation="read", detail=f"{type(exc).__name__}"
            ) from exc
        if (
            before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            last_detail = "changed_during_read"
            if attempt + 1 < attempts:
                time.sleep(retry_seconds)
                continue
            raise WatchdogInputError(path=path, operation="read", detail=last_detail)
        return payload
    raise WatchdogInputError(path=path, operation="read", detail=last_detail)


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(_read_stable_bytes(path).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WatchdogInputError(path=path, operation="json", detail="malformed") from exc
    if not isinstance(value, dict):
        raise WatchdogInputError(path=path, operation="json", detail="not_object")
    return value


def _sha256_file_stable(path: Path) -> str:
    return hashlib.sha256(_read_stable_bytes(path)).hexdigest()


def _load_history_failure(history_root: Path) -> dict[str, object] | None:
    """Return the first durable fatal event/status for this canary, if any."""

    if not history_root.exists():
        raise WatchdogInputError(path=history_root, operation="history", detail="missing")
    status_path = history_root / "status.json"
    if status_path.exists():
        status = _load_json(status_path)
        if status.get("decision") == "CANARY_FAILED":
            return {
                "source": "status.json",
                "observed_at": status.get("observed_at"),
                "reasons": status.get("reasons", []),
            }
    events_path = history_root / "events.jsonl"
    if not events_path.exists():
        return None
    raw = _read_stable_bytes(events_path).decode("utf-8")
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise WatchdogInputError(
                path=events_path, operation=f"history_line_{line_number}", detail="malformed"
            ) from exc
        if isinstance(event, dict) and event.get("decision") == "CANARY_FAILED":
            return {
                "source": f"events.jsonl:{line_number}",
                "observed_at": event.get("observed_at"),
                "reasons": event.get("reasons", []),
            }
    return None


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
    history_root: Path | None = None,
) -> dict[str, object]:
    try:
        prereg = load_canary_preregistration(
            preregistration, expected_sha256=preregistration_sha256
        )
    except FileNotFoundError as exc:
        raise WatchdogInputError(
            path=preregistration, operation="preregistration", detail="missing"
        ) from exc
    history_root = (history_root or source_root.parent / "watchdog").resolve()
    historical_failure = _load_history_failure(history_root)
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
    if historical_failure is not None:
        reasons.append(
            "historical_watchdog_failure:"
            + str(historical_failure.get("observed_at"))
            + ":"
            + ",".join(str(item) for item in historical_failure.get("reasons", []))
        )
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
        "candidate_warmup_state": candidate_status.get("warmup_state"),
        "candidate_last_eligibility_status": candidate_status.get("last_eligibility_status"),
        "evidence_class": CANARY_EVIDENCE_CLASS,
        "admission_eligible": False,
        "preregistration_sha256": preregistration_sha256,
        "source_manifest_sha256": _sha256_file_stable(source_root / "manifest.json"),
        "candidate_manifest_sha256": _sha256_file_stable(candidate_root / "manifest.json"),
        "fatal_latched": historical_failure is not None or decision == "CANARY_FAILED",
        "fatal_history": historical_failure,
    }


def run_watchdog(
    *,
    preregistration: Path,
    preregistration_sha256: str,
    source_root: Path,
    candidate_root: Path,
    output_root: Path,
    history_root: Path | None,
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
                history_root=history_root,
            )
        except (
            WatchdogInputError,
            OSError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            if isinstance(exc, WatchdogInputError):
                reason = f"watchdog_input_error:{exc.operation}:{exc.path}:{exc.detail}"
            else:
                reason = f"watchdog_input_error:{type(exc).__name__}"
            report = {
                "schema": WATCHDOG_SCHEMA,
                "observed_at": datetime.now(UTC).isoformat(),
                "decision": "CANARY_FAILED",
                "reasons": [reason],
                "source_state": "UNKNOWN",
                "candidate_state": "UNKNOWN",
                "source_pid_alive": False,
                "candidate_pid_alive": False,
                "evidence_class": CANARY_EVIDENCE_CLASS,
                "admission_eligible": False,
                "fatal_latched": True,
            }
        with (output_root / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _write_atomic(output_root / "status.json", report)
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
    parser.add_argument("--history-root", type=Path)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    return run_watchdog(
        preregistration=args.preregistration.resolve(),
        preregistration_sha256=args.preregistration_sha256,
        source_root=args.source_root.resolve(),
        candidate_root=args.candidate_root.resolve(),
        output_root=args.output_root.resolve(),
        history_root=args.history_root.resolve() if args.history_root else None,
        poll_seconds=args.poll_seconds,
        once=args.once,
    )


if __name__ == "__main__":
    raise SystemExit(main())
