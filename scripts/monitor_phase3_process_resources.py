#!/usr/bin/env python3
"""Observe a Phase-3 qualification process without modifying its evidence root.

The qualification runner records source behavior.  This sidecar records
OS-observable resource and spool behavior in a separate evidence root so a
long run can be reviewed for RSS/CPU/file-descriptor/socket leaks and disk
growth without changing the runner or its append-only logs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from typing import Any

import psutil
from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA = "advisorai.phase3.resource-monitor.v2"


class ResourceObservation(BaseModel):
    """One sanitized OS observation for the monitored process."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_name: str = Field(default=SCHEMA, alias="schema")
    sampled_at: datetime
    pid: int = Field(gt=0)
    process_status: str
    process_start_ticks: int | None = Field(default=None, ge=0)
    command_sha256: str | None = None
    rss_mib: float | None = Field(default=None, ge=0)
    vms_mib: float | None = Field(default=None, ge=0)
    cpu_percent: float | None = Field(default=None, ge=0)
    thread_count: int | None = Field(default=None, ge=0)
    file_descriptor_count: int | None = Field(default=None, ge=0)
    inet_connection_count: int | None = Field(default=None, ge=0)
    target_root_file_count: int = Field(default=0, ge=0)
    target_root_bytes: int = Field(default=0, ge=0)
    resource_errors: tuple[str, ...] = ()
    previous_record_hash: str | None = None
    record_hash: str

    @field_validator("sampled_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("resource observations require an aware timestamp")
        return value.astimezone(UTC)

    @field_validator("command_sha256", "record_hash", "previous_record_hash")
    @classmethod
    def validate_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("resource evidence hashes must be lowercase SHA-256 digests")
        return value

    @field_validator("rss_mib", "vms_mib", "cpu_percent")
    @classmethod
    def validate_finite(cls, value: float | None) -> float | None:
        if value is not None and not isfinite(value):
            raise ValueError("resource measurements must be finite")
        return value


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=lambda item: (
            item.astimezone(UTC).isoformat().replace("+00:00", "Z")
            if isinstance(item, datetime)
            else str(item)
        ),
    ).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_bytes(_canonical(payload) + b"\n")
    temporary.replace(path)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--until must include a timezone")
    return parsed.astimezone(UTC)


def _command_hash(process: psutil.Process) -> str | None:
    try:
        return _sha256("\0".join(process.cmdline()).encode())
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        return None


def _process_start_ticks(pid: int) -> int:
    """Read the stable Linux process start-tick field without boot-time math."""

    stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    closing_parenthesis = stat.rfind(")")
    if closing_parenthesis < 0:
        raise RuntimeError("process stat has no command terminator")
    fields_after_command = stat[closing_parenthesis + 2 :].split()
    if len(fields_after_command) <= 19:
        raise RuntimeError("process stat has no start-tick field")
    return int(fields_after_command[19])


def _root_size(root: Path) -> tuple[int, int, tuple[str, ...]]:
    file_count = 0
    total_bytes = 0
    errors: list[str] = []
    if not root.is_dir():
        return 0, 0, ("target_root_missing",)
    try:
        paths = root.rglob("*")
    except OSError as exc:
        return 0, 0, (f"root_walk:{type(exc).__name__}",)
    for path in paths:
        try:
            if path.is_symlink() or not path.is_file():
                continue
            file_count += 1
            total_bytes += path.stat().st_size
        except (OSError, PermissionError) as exc:
            errors.append(f"root_stat:{type(exc).__name__}")
    return file_count, total_bytes, tuple(sorted(set(errors)))


def _process_sample(
    process: psutil.Process,
    *,
    pid: int,
    expected_start_ticks: int,
    expected_command_sha256: str,
    target_root: Path,
    previous_record_hash: str | None,
) -> ResourceObservation:
    sampled_at = datetime.now(UTC)
    errors: list[str] = []
    file_count, root_bytes, root_errors = _root_size(target_root)
    errors.extend(root_errors)
    try:
        start_ticks = _process_start_ticks(pid)
        command_hash = _command_hash(process)
        identity_matches = (
            start_ticks == expected_start_ticks and command_hash == expected_command_sha256
        )
        if not identity_matches:
            return _observation(
                sampled_at=sampled_at,
                pid=pid,
                process_status="identity_mismatch",
                process_start_ticks=start_ticks,
                command_sha256=command_hash,
                target_root_file_count=file_count,
                target_root_bytes=root_bytes,
                resource_errors=("process_identity_mismatch", *errors),
                previous_record_hash=previous_record_hash,
            )
        memory = process.memory_info()
        try:
            cpu = process.cpu_percent(interval=None)
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError) as exc:
            cpu = None
            errors.append(f"cpu:{type(exc).__name__}")
        try:
            file_descriptors = process.num_fds()
        except (AttributeError, psutil.AccessDenied, psutil.NoSuchProcess, OSError) as exc:
            file_descriptors = None
            errors.append(f"file_descriptors:{type(exc).__name__}")
        try:
            connections = len(process.net_connections(kind="inet"))
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError) as exc:
            connections = None
            errors.append(f"inet_connections:{type(exc).__name__}")
        return _observation(
            sampled_at=sampled_at,
            pid=pid,
            process_status="running",
            process_start_ticks=start_ticks,
            command_sha256=command_hash,
            rss_mib=memory.rss / (1024**2),
            vms_mib=memory.vms / (1024**2),
            cpu_percent=max(0.0, cpu) if cpu is not None else None,
            thread_count=process.num_threads(),
            file_descriptor_count=file_descriptors,
            inet_connection_count=connections,
            target_root_file_count=file_count,
            target_root_bytes=root_bytes,
            resource_errors=tuple(sorted(set(errors))),
            previous_record_hash=previous_record_hash,
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
        return _observation(
            sampled_at=sampled_at,
            pid=pid,
            process_status="exited",
            target_root_file_count=file_count,
            target_root_bytes=root_bytes,
            resource_errors=(f"process:{type(exc).__name__}", *errors),
            previous_record_hash=previous_record_hash,
        )


def _observation(**values: Any) -> ResourceObservation:
    without_hash = dict(values)
    without_hash["schema"] = SCHEMA
    without_hash["record_hash"] = "0" * 64
    digest_payload = without_hash.copy()
    digest_payload.pop("record_hash")
    digest = _sha256(_canonical(digest_payload))
    without_hash["record_hash"] = digest
    return ResourceObservation.model_validate(without_hash)


def _append(path: Path, record: ResourceObservation) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write(_canonical(record.model_dump(mode="json", by_alias=True)) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _summarize(
    records: list[ResourceObservation], *, state: str, target_root: Path
) -> dict[str, Any]:
    running = [record for record in records if record.process_status == "running"]
    return {
        "schema": f"{SCHEMA}.summary",
        "state": state,
        "completed_at": datetime.now(UTC).isoformat(),
        "sample_count": len(records),
        "running_sample_count": len(running),
        "target_root": str(target_root),
        "first_sampled_at": records[0].sampled_at.isoformat() if records else None,
        "last_sampled_at": records[-1].sampled_at.isoformat() if records else None,
        "max_rss_mib": max((record.rss_mib or 0 for record in running), default=0),
        "max_vms_mib": max((record.vms_mib or 0 for record in running), default=0),
        "max_cpu_percent": max((record.cpu_percent or 0 for record in running), default=0),
        "max_thread_count": max((record.thread_count or 0 for record in running), default=0),
        "max_file_descriptor_count": max(
            (record.file_descriptor_count or 0 for record in running), default=0
        ),
        "max_inet_connection_count": max(
            (record.inet_connection_count or 0 for record in running), default=0
        ),
        "max_target_root_file_count": max(
            (record.target_root_file_count for record in records), default=0
        ),
        "max_target_root_bytes": max((record.target_root_bytes for record in records), default=0),
        "resource_errors": sorted(
            {error for record in records for error in record.resource_errors}
        ),
        "last_record_hash": records[-1].record_hash if records else None,
    }


def monitor(args: argparse.Namespace) -> int:
    evidence_dir = args.evidence_dir.resolve()
    target_root = args.target_root.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=False)
    process = psutil.Process(args.pid)
    process_start_ticks = _process_start_ticks(args.pid)
    command_hash = _command_hash(process)
    if (
        command_hash != args.expected_command_sha256
        or process_start_ticks != args.expected_start_ticks
    ):
        raise SystemExit("target process identity does not match supplied expectations")
    config = {
        "schema": f"{SCHEMA}.config",
        "run_id": evidence_dir.name,
        "pid": args.pid,
        "expected_process_start_ticks": args.expected_start_ticks,
        "expected_command_sha256": args.expected_command_sha256,
        "target_root": str(target_root),
        "interval_seconds": args.interval_seconds,
        "until": args.until.isoformat(),
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "monitor_code_sha256": _sha256(Path(__file__).read_bytes()),
    }
    _write_atomic(evidence_dir / "config.json", config)
    observations = evidence_dir / "observations.jsonl"
    records: list[ResourceObservation] = []
    state = "running"
    while True:
        if datetime.now(UTC) >= args.until:
            state = "deadline_reached"
            break
        if not process.is_running():
            state = "target_exited"
            break
        record = _process_sample(
            process,
            pid=args.pid,
            expected_start_ticks=args.expected_start_ticks,
            expected_command_sha256=args.expected_command_sha256,
            target_root=target_root,
            previous_record_hash=records[-1].record_hash if records else None,
        )
        records.append(record)
        _append(observations, record)
        _write_atomic(
            evidence_dir / "heartbeat.json",
            {
                "schema": f"{SCHEMA}.heartbeat",
                "sampled_at": record.sampled_at.isoformat(),
                "state": record.process_status,
                "sample_count": len(records),
                "last_record_hash": record.record_hash,
            },
        )
        if record.process_status != "running":
            state = record.process_status
            break
        time.sleep(args.interval_seconds)
    summary = _summarize(records, state=state, target_root=target_root)
    _write_atomic(evidence_dir / "summary.json", summary)
    _write_atomic(
        evidence_dir / "status.json",
        {
            "schema": f"{SCHEMA}.status",
            "state": state,
            "completed_at": summary["completed_at"],
            "summary_sha256": _sha256((evidence_dir / "summary.json").read_bytes()),
            "last_record_hash": summary["last_record_hash"],
        },
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--expected-start-ticks", type=int, required=True)
    parser.add_argument("--expected-command-sha256", required=True)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--until", type=_parse_timestamp, required=True)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    return parser


if __name__ == "__main__":
    parsed = build_parser().parse_args()
    if parsed.interval_seconds < 1:
        raise SystemExit("--interval-seconds must be at least one second")
    raise SystemExit(monitor(parsed))
