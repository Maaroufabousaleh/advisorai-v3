"""Append-only contracts for a bounded remote-route stability window."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

SCHEMA_VERSION = "phase0-remote-route-stability-v2"


def _validate_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be a SHA-256 hex digest") from exc
    return value


def canonical_hash(value: object) -> str:
    """Hash a JSON-compatible value using the repository canonical form."""

    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def make_record(
    *,
    run_id: str,
    sequence: int,
    sampled_at: datetime,
    identity_key: str,
    passed: bool,
    probe: Mapping[str, object],
    previous_record_hash: str | None,
    config_sha256: str,
) -> dict[str, object]:
    """Build one immutable hash-chained route sample."""

    if not run_id.strip() or sequence < 0 or not identity_key.strip():
        raise ValueError("remote stability records require run, sequence, and identity")
    if sampled_at.tzinfo is None or sampled_at.utcoffset() is None:
        raise ValueError("remote stability sample time must be timezone-aware")
    if previous_record_hash is not None and len(previous_record_hash) != 64:
        raise ValueError("previous record hash must be SHA-256")
    _validate_sha256(config_sha256, field="config_sha256")
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "sequence": sequence,
        "sampled_at": sampled_at.astimezone(UTC).isoformat(),
        "identity_key": identity_key,
        "passed": bool(passed),
        "probe": dict(probe),
        "previous_record_hash": previous_record_hash,
        "config_sha256": config_sha256,
    }
    payload["record_hash"] = canonical_hash(payload)
    return payload


def _verify_record(
    record: Mapping[str, object], *, expected_sequence: int, previous: str | None
) -> None:
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("remote stability schema version changed")
    if record.get("sequence") != expected_sequence:
        raise ValueError("remote stability sequence is not contiguous")
    if record.get("previous_record_hash") != previous:
        raise ValueError("remote stability hash chain is broken")
    claimed = record.get("record_hash")
    if not isinstance(claimed, str) or len(claimed) != 64:
        raise ValueError("remote stability record hash is missing")
    unsigned = dict(record)
    unsigned.pop("record_hash", None)
    if canonical_hash(unsigned) != claimed:
        raise ValueError("remote stability record hash is inconsistent")
    _validate_sha256(record.get("config_sha256"), field="config_sha256")
    sampled_at = record.get("sampled_at")
    if not isinstance(sampled_at, str):
        raise ValueError("remote stability sample timestamp is missing")
    timestamp = datetime.fromisoformat(sampled_at)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("remote stability sample timestamp is not timezone-aware")


def read_records(path: Path) -> tuple[dict[str, object], ...]:
    """Read and verify a complete append-only route sample log."""

    if not path.exists():
        return ()
    records: list[dict[str, object]] = []
    previous: str | None = None
    previous_time: datetime | None = None
    config_sha256: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("remote stability records must be JSON objects")
        _verify_record(value, expected_sequence=len(records), previous=previous)
        record_config = str(value["config_sha256"])
        if config_sha256 is None:
            config_sha256 = record_config
        elif record_config != config_sha256:
            raise ValueError("remote stability configuration hash changed")
        timestamp = datetime.fromisoformat(str(value["sampled_at"]))
        if previous_time is not None and timestamp <= previous_time:
            raise ValueError("remote stability samples must be strictly time ordered")
        records.append(value)
        previous = str(value["record_hash"])
        previous_time = timestamp
    return tuple(records)


def append_record(path: Path, record: Mapping[str, object]) -> None:
    """Append one record only when it extends the existing verified chain."""

    existing = read_records(path)
    previous = str(existing[-1]["record_hash"]) if existing else None
    if existing and record.get("config_sha256") != existing[0].get("config_sha256"):
        raise ValueError("remote stability configuration hash changed")
    _verify_record(record, expected_sequence=len(existing), previous=previous)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(dict(record), sort_keys=True, separators=(",", ":")) + "\n").encode()
    with path.open("ab") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def summarize_records(
    *,
    run_id: str,
    started_at: datetime,
    duration_hours: float,
    records: tuple[Mapping[str, object], ...],
    now: datetime,
) -> dict[str, object]:
    """Return a truthful short-window or complete-window gate summary."""

    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise ValueError("remote stability start time must be timezone-aware")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("remote stability current time must be timezone-aware")
    elapsed_hours = max(0.0, (now - started_at).total_seconds() / 3600)
    all_passed = bool(records) and all(bool(record.get("passed")) for record in records)
    identities = {str(record.get("identity_key")) for record in records}
    identity_stable = len(identities) == 1
    duration_passed = elapsed_hours >= duration_hours
    if all_passed and identity_stable and duration_passed:
        status = "passed"
    elif all_passed and identity_stable:
        status = "short_smoke_complete"
    else:
        status = "failed" if records else "not_started"
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        "cycle_count": len(records),
        "elapsed_hours": elapsed_hours,
        "all_cycles_passed": all_passed,
        "identity_stable": identity_stable,
        "duration_hours_required": duration_hours,
        "duration_gate_passed": duration_passed,
        "last_record_hash": records[-1].get("record_hash") if records else None,
    }
