#!/usr/bin/env python3
"""Validate a sealed Phase-4 resource sidecar without changing it.

The validator checks the sidecar's append-only hash chain, process identity
continuity, summary/status binding, monotonic sample time, resource errors, and
OS-observable growth.  It does not invent percentile estimates, acquire data,
load credentials, or make execution calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "advisorai.phase4.v3-core.resource-audit.v1"
RESOURCE_SCHEMA = "advisorai.phase3.resource-monitor.v2"
TERMINAL_STATES = {"deadline_reached", "target_exited", "identity_mismatch"}
PROCESS_STATES = {"running", "exited", "identity_mismatch"}


def _canonical(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=lambda value: (
            value.astimezone(UTC).isoformat().replace("+00:00", "Z")
            if isinstance(value, datetime)
            else str(value)
        ),
    ).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256(path.read_bytes())


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("resource timestamp is not a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("resource timestamp is not timezone-aware")
    return parsed.astimezone(UTC)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _validate_digest(value: object, *, field: str, allow_none: bool = False) -> None:
    if value is None and allow_none:
        return
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} is not a lowercase SHA-256 digest")


def audit(resource_root: Path) -> dict[str, object]:
    resource_root = resource_root.resolve()
    status = _load_json(resource_root / "status.json")
    if status.get("state") == "running":
        raise ValueError("resource sidecar is still running")
    if status.get("schema") != f"{RESOURCE_SCHEMA}.status":
        raise ValueError("resource status has an unsupported schema")
    if status.get("state") not in TERMINAL_STATES:
        raise ValueError("resource status has an unsupported terminal state")
    config = _load_json(resource_root / "config.json")
    if config.get("schema") != f"{RESOURCE_SCHEMA}.config":
        raise ValueError("resource config has an unsupported schema")
    rows: list[dict[str, Any]] = []
    previous: str | None = None
    for line_number, line in enumerate(
        (resource_root / "observations.jsonl").read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"resource observation {line_number} is not an object")
        if row.get("schema") != RESOURCE_SCHEMA:
            raise ValueError(f"resource observation {line_number} has an unsupported schema")
        _validate_digest(row.get("record_hash"), field="record_hash")
        _validate_digest(
            row.get("previous_record_hash"), field="previous_record_hash", allow_none=True
        )
        if (
            not isinstance(row.get("pid"), int)
            or isinstance(row.get("pid"), bool)
            or row["pid"] <= 0
        ):
            raise ValueError(f"resource observation {line_number} has an invalid pid")
        if row.get("process_status") not in PROCESS_STATES:
            raise ValueError(f"resource observation {line_number} has an invalid process status")
        if row.get("process_start_ticks") is not None and (
            not isinstance(row["process_start_ticks"], int)
            or isinstance(row["process_start_ticks"], bool)
            or row["process_start_ticks"] < 0
        ):
            raise ValueError(f"resource observation {line_number} has invalid process start ticks")
        _validate_digest(row.get("command_sha256"), field="command_sha256", allow_none=True)
        if row.get("previous_record_hash") != previous:
            raise ValueError(f"resource observation {line_number} has a broken predecessor")
        if row.get("record_hash") != _sha256(
            _canonical({key: value for key, value in row.items() if key != "record_hash"})
        ):
            raise ValueError(f"resource observation {line_number} has an invalid hash")
        rows.append(row)
        previous = str(row["record_hash"])
    if not rows:
        raise ValueError("resource sidecar has no observations")

    times = [_timestamp(row["sampled_at"]) for row in rows]
    issues: list[str] = []
    if times != sorted(times):
        issues.append("sample_times_not_monotonic")
    pids = {row.get("pid") for row in rows}
    starts = {
        row.get("process_start_ticks") for row in rows if row.get("process_start_ticks") is not None
    }
    commands = {row.get("command_sha256") for row in rows if row.get("command_sha256") is not None}
    if len(pids) != 1:
        issues.append("process_pid_changed")
    if len(starts) > 1:
        issues.append("process_start_ticks_changed")
    if len(commands) > 1:
        issues.append("process_command_hash_changed")
    for row in rows:
        for field in (
            "rss_mib",
            "vms_mib",
            "cpu_percent",
            "thread_count",
            "file_descriptor_count",
            "inet_connection_count",
            "target_root_file_count",
            "target_root_bytes",
        ):
            value = row.get(field)
            if value is not None and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value < 0
                or (isinstance(value, float) and not math.isfinite(value))
            ):
                issues.append(f"invalid_{field}")
        if row.get("resource_errors"):
            issues.append("resource_errors_present")
        if not isinstance(row.get("resource_errors", []), list):
            issues.append("invalid_resource_errors")

    running = [row for row in rows if row.get("process_status") == "running"]

    def _maximum(field: str) -> float | int:
        values = [row[field] for row in running if row.get(field) is not None]
        return max(values) if values else 0

    def _growth(field: str) -> float | int | None:
        values = [row[field] for row in rows if row.get(field) is not None]
        return values[-1] - values[0] if values else None

    summary = _load_json(resource_root / "summary.json")
    summary_sha256 = _sha256_file(resource_root / "summary.json")
    if summary.get("schema") != f"{RESOURCE_SCHEMA}.summary":
        issues.append("summary_schema_mismatch")
    if status.get("summary_sha256") != summary_sha256:
        issues.append("summary_hash_mismatch")
    if status.get("last_record_hash") != previous:
        issues.append("status_last_record_hash_mismatch")
    if summary.get("state") != status.get("state"):
        issues.append("summary_status_state_mismatch")
    if summary.get("sample_count") != len(rows):
        issues.append("summary_sample_count_mismatch")
    if summary.get("running_sample_count") != len(running):
        issues.append("summary_running_sample_count_mismatch")
    if summary.get("last_record_hash") != previous:
        issues.append("summary_last_record_hash_mismatch")
    if summary.get("target_root") != config.get("target_root"):
        issues.append("summary_target_root_mismatch")
    if (
        summary.get("first_sampled_at") is not None
        and _timestamp(summary["first_sampled_at"]) != times[0]
    ):
        issues.append("summary_first_sample_mismatch")
    if (
        summary.get("last_sampled_at") is not None
        and _timestamp(summary["last_sampled_at"]) != times[-1]
    ):
        issues.append("summary_last_sample_mismatch")
    expected_summary_metrics = {
        "max_rss_mib": _maximum("rss_mib"),
        "max_vms_mib": _maximum("vms_mib"),
        "max_cpu_percent": _maximum("cpu_percent"),
        "max_thread_count": _maximum("thread_count"),
        "max_file_descriptor_count": _maximum("file_descriptor_count"),
        "max_inet_connection_count": _maximum("inet_connection_count"),
        "max_target_root_file_count": max(
            (row["target_root_file_count"] for row in rows), default=0
        ),
        "max_target_root_bytes": max((row["target_root_bytes"] for row in rows), default=0),
        "resource_errors": sorted(
            {error for row in rows for error in row.get("resource_errors", [])}
        ),
    }
    for field, expected in expected_summary_metrics.items():
        if summary.get(field) != expected:
            issues.append(f"summary_{field}_mismatch")
    if config.get("credentials_loaded") is not False:
        issues.append("config_credentials_invariant_failed")
    if config.get("order_writes_attempted") is not False:
        issues.append("config_order_write_invariant_failed")

    resource = {
        "sample_count": len(rows),
        "running_sample_count": len(running),
        "first_sampled_at": times[0].isoformat().replace("+00:00", "Z"),
        "last_sampled_at": times[-1].isoformat().replace("+00:00", "Z"),
        "max_rss_mib": _maximum("rss_mib"),
        "max_vms_mib": _maximum("vms_mib"),
        "max_cpu_percent": _maximum("cpu_percent"),
        "max_thread_count": _maximum("thread_count"),
        "max_file_descriptor_count": _maximum("file_descriptor_count"),
        "max_inet_connection_count": _maximum("inet_connection_count"),
        "target_root_file_count_growth": _growth("target_root_file_count"),
        "target_root_bytes_growth": _growth("target_root_bytes"),
        "rss_mib_growth": _growth("rss_mib"),
        "resource_errors": sorted(
            {error for row in rows for error in row.get("resource_errors", [])}
        ),
    }
    report: dict[str, object] = {
        "schema": SCHEMA,
        "resource_root": str(resource_root),
        "config_sha256": _sha256_file(resource_root / "config.json"),
        "status_sha256": _sha256_file(resource_root / "status.json"),
        "summary_sha256": summary_sha256,
        "observations_sha256": _sha256_file(resource_root / "observations.jsonl"),
        "last_record_hash": previous,
        "state": "PASS_FOR_REVIEW" if not issues else "FAIL",
        "issues": sorted(set(issues)),
        "resource": resource,
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "network_calls": 0,
    }
    report["audit_fingerprint"] = _sha256(
        _canonical({key: value for key, value in report.items() if key != "audit_fingerprint"})
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = audit(args.resource_root)
        output = args.output.resolve()
        if output.exists():
            raise ValueError(f"immutable output already exists: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
        with output.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
        print(
            json.dumps(
                {
                    "output": str(output),
                    "artifact_sha256": _sha256(encoded),
                    "audit_fingerprint": report["audit_fingerprint"],
                    "state": report["state"],
                },
                sort_keys=True,
            )
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"resource audit refused ({type(exc).__name__})") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
