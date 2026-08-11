"""Durable, restartable Phase-7 paper-soak evidence.

This module owns only the evidence and process-lifecycle boundary.  The caller
must provide the already-wired paper decision cycle; this runner never creates
orders, calls a venue, or changes RiskKernel/OMS authority.  A run is
evidence-for-review-only until the existing Phase-7 gate is evaluated by a
separate, supervised decision.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .controller import SoakSample

HEX = frozenset("0123456789abcdef")
_ZERO_HASH = "0" * 64


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _payload_hash(value: object) -> str:
    return sha256(_canonical_bytes(value)).hexdigest()


def _require_sha256(value: str, field_name: str) -> str:
    if len(value) != 64 or any(character not in HEX for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _write_immutable_json(path: Path, payload: object) -> None:
    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise FileExistsError(f"immutable evidence differs: {path}")
        return
    temporary = path.with_suffix(f"{path.suffix}.tmp-{os.getpid()}")
    try:
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_status(path: Path, payload: object) -> None:
    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp-{os.getpid()}")
    try:
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class SoakRunConfig(BaseModel):
    """Immutable identity and timing contract for one unattended soak root."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "advisorai.phase7.soak-run-config.v1"
    run_id: str = Field(min_length=1)
    started_at: datetime
    required_calendar_days: int = Field(default=60, ge=60)
    sample_interval_seconds: int = Field(default=300, ge=1)
    code_sha256: str
    configuration_sha256: str
    policy_sha256: str
    model_roster_sha256: str
    source_roster_sha256: str
    venue_identity: str = Field(min_length=1)
    venue_environment: str = "paper_testnet"
    command: str = Field(min_length=1)
    stop_procedure: str = "send SIGTERM; preserve the evidence root; inspect status"
    restart_procedure: str = "reuse the same root/config; never reset started_at"

    @field_validator("started_at")
    @classmethod
    def require_started_at(cls, value: datetime) -> datetime:
        return _aware(value, "soak start")

    @field_validator(
        "code_sha256",
        "configuration_sha256",
        "policy_sha256",
        "model_roster_sha256",
        "source_roster_sha256",
    )
    @classmethod
    def require_digest(cls, value: str, info) -> str:
        return _require_sha256(value, info.field_name)

    @field_validator("venue_environment")
    @classmethod
    def paper_only(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"paper", "testnet", "paper_testnet"}:
            raise ValueError("Phase-7 runner accepts only paper/testnet environments")
        return normalized

    @model_validator(mode="after")
    def validate_identity(self) -> SoakRunConfig:
        if not self.run_id.strip() or not self.venue_identity.strip():
            raise ValueError("soak identity fields cannot be blank")
        if (
            not self.command.strip()
            or not self.stop_procedure.strip()
            or not self.restart_procedure.strip()
        ):
            raise ValueError("soak process procedures cannot be blank")
        return self

    @property
    def target_end(self) -> datetime:
        return self.started_at + timedelta(days=self.required_calendar_days)


class SoakRecord(BaseModel):
    """One hash-chained interval observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "advisorai.phase7.soak-record.v1"
    run_id: str = Field(min_length=1)
    config_hash: str
    sequence: int = Field(ge=0)
    sampled_at: datetime
    sample: SoakSample
    previous_record_hash: str | None
    record_hash: str

    @field_validator("sampled_at")
    @classmethod
    def require_sampled_at(cls, value: datetime) -> datetime:
        return _aware(value, "soak record timestamp")

    @field_validator("config_hash", "record_hash", "previous_record_hash")
    @classmethod
    def require_record_digest(cls, value: str | None, info) -> str | None:
        return None if value is None else _require_sha256(value, info.field_name)

    @model_validator(mode="after")
    def validate_record(self) -> SoakRecord:
        if not self.run_id.strip():
            raise ValueError("soak record run ID cannot be blank")
        if self.sequence == 0 and self.previous_record_hash is not None:
            raise ValueError("first soak record cannot have a predecessor")
        if self.sequence > 0 and self.previous_record_hash is None:
            raise ValueError("later soak records require a predecessor")
        if self.sample.at > self.sampled_at:
            raise ValueError("soak sample cannot be recorded before its interval timestamp")
        expected = _payload_hash(self.model_dump(mode="json", exclude={"record_hash"}))
        if expected != self.record_hash:
            raise ValueError("soak record hash is inconsistent")
        return self


class SoakRunSummary(BaseModel):
    """Process result; deliberately cannot represent Phase-7 admission."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "advisorai.phase7.soak-run-summary.v1"
    run_id: str
    config_hash: str
    started_at: datetime
    ended_at: datetime
    elapsed_hours: float = Field(ge=0)
    record_count: int = Field(ge=1)
    terminal_sample_at: datetime | None
    status: str
    qualification_state: str = "evidence_for_review_only"
    phase7_admission: bool = False

    @field_validator("started_at", "ended_at", "terminal_sample_at")
    @classmethod
    def require_summary_time(cls, value: datetime | None, info) -> datetime | None:
        return None if value is None else _aware(value, info.field_name)

    @field_validator("config_hash")
    @classmethod
    def require_summary_hash(cls, value: str) -> str:
        return _require_sha256(value, "config_hash")

    @model_validator(mode="after")
    def validate_summary(self) -> SoakRunSummary:
        if self.ended_at < self.started_at:
            raise ValueError("soak summary cannot end before it starts")
        if self.status not in {"short_smoke_complete", "completed_60_calendar_days"}:
            raise ValueError("soak summary status is invalid")
        if self.qualification_state != "evidence_for_review_only":
            raise ValueError("durable runner summaries remain evidence-for-review-only")
        if self.phase7_admission:
            raise ValueError("durable runner cannot open Phase-7 admission")
        terminal = self.terminal_sample_at is not None
        if self.status == "completed_60_calendar_days" and (
            not terminal or self.elapsed_hours < 24 * 60
        ):
            raise ValueError("completed soak summary requires a real 60-day terminal sample")
        if self.status == "short_smoke_complete" and terminal:
            raise ValueError("short soak summary cannot contain a terminal sample")
        return self


def make_soak_record(
    config: SoakRunConfig,
    sample: SoakSample,
    *,
    sequence: int,
    sampled_at: datetime,
    previous_record_hash: str | None,
) -> SoakRecord:
    payload = {
        "schema_version": "advisorai.phase7.soak-record.v1",
        "run_id": config.run_id,
        "config_hash": _payload_hash(config.model_dump(mode="json")),
        "sequence": sequence,
        "sampled_at": _aware(sampled_at, "soak record timestamp"),
        "sample": sample,
        "previous_record_hash": previous_record_hash,
    }
    unsealed = SoakRecord.model_construct(**payload, record_hash=_ZERO_HASH)
    canonical = unsealed.model_dump(mode="json", exclude={"record_hash"})
    return SoakRecord.model_validate({**canonical, "record_hash": _payload_hash(canonical)})


def read_soak_records(path: Path) -> tuple[SoakRecord, ...]:
    if not path.exists():
        return ()
    records = tuple(
        SoakRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    for index, record in enumerate(records):
        if record.sequence != index:
            raise ValueError("soak record sequence contains a gap")
        expected_previous = records[index - 1].record_hash if index else None
        if record.previous_record_hash != expected_previous:
            raise ValueError("soak record hash chain is broken")
        if index and record.sampled_at <= records[index - 1].sampled_at:
            raise ValueError("soak records are not strictly time ordered")
    return records


def append_soak_record(path: Path, record: SoakRecord) -> None:
    existing = read_soak_records(path)
    if record.sequence != len(existing):
        raise ValueError("soak record append sequence is not contiguous")
    expected_previous = existing[-1].record_hash if existing else None
    if record.previous_record_hash != expected_previous:
        raise ValueError("soak record predecessor hash is inconsistent")
    if existing and record.sampled_at <= existing[-1].sampled_at:
        raise ValueError("soak records must be appended in time order")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(_canonical_bytes(record.model_dump(mode="json")) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


SampleFactory = Callable[[datetime], SoakSample]


class DurablePaperSoakRunner:
    """Run an already-wired paper cycle with durable, resumable evidence."""

    def __init__(
        self,
        *,
        config: SoakRunConfig,
        evidence_root: Path,
        sample_factory: SampleFactory,
    ) -> None:
        self.config = config
        self.evidence_root = evidence_root
        self.sample_factory = sample_factory
        self.config_path = evidence_root / "config.json"
        self.records_path = evidence_root / "samples.jsonl"
        self.status_path = evidence_root / "status.json"
        self.summary_path = evidence_root / "summary.json"
        self.lock_path = evidence_root / "runner.lock"
        self.evidence_root.mkdir(parents=True, exist_ok=True)
        _write_immutable_json(self.config_path, config.model_dump(mode="json"))

    @property
    def config_hash(self) -> str:
        return _payload_hash(self.config.model_dump(mode="json"))

    def run(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_samples: int | None = None,
    ) -> SoakRunSummary:
        """Resume the root; ``max_samples`` is for bounded non-admission tests."""

        if max_samples is not None and max_samples < 1:
            raise ValueError("max_samples must be positive")
        now_fn = clock or (lambda: datetime.now(UTC))
        lock_handle = self.lock_path.open("a+", encoding="utf-8")
        try:
            try:
                fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("another soak runner owns this evidence root") from exc
            lock_handle.seek(0)
            lock_handle.truncate()
            lock_handle.write(str(os.getpid()))
            lock_handle.flush()
            os.fsync(lock_handle.fileno())

            records = read_soak_records(self.records_path)
            self._validate_existing_records(records)
            target_end = self.config.target_end
            while True:
                if records and records[-1].sampled_at >= target_end:
                    break
                if max_samples is not None and len(records) >= max_samples:
                    break
                now = _aware(now_fn(), "soak clock")
                if now < self.config.started_at:
                    raise ValueError("soak clock precedes immutable start")
                if records and now <= records[-1].sampled_at:
                    raise ValueError("soak clock did not advance beyond the last record")
                try:
                    sample = self.sample_factory(now)
                    if sample.at < self.config.started_at:
                        raise ValueError("soak sample precedes immutable start")
                    record = make_soak_record(
                        self.config,
                        sample,
                        sequence=len(records),
                        sampled_at=now,
                        previous_record_hash=records[-1].record_hash if records else None,
                    )
                    append_soak_record(self.records_path, record)
                except Exception as exc:
                    self._write_failure_status(now, type(exc).__name__)
                    raise
                records = (*records, record)
                self._write_running_status(records, now)
                if record.sampled_at >= target_end:
                    break
                if max_samples is not None and len(records) >= max_samples:
                    break
                remaining = (target_end - now).total_seconds()
                sleep(min(float(self.config.sample_interval_seconds), max(0.0, remaining)))

            summary = self._summary(records)
            # A pre-terminal bounded run is resumable progress, not a final
            # qualification artifact.  Only the genuine terminal result gets
            # an immutable summary.json; status.json and samples.jsonl remain
            # the durable progress evidence before that point.
            if summary.status == "completed_60_calendar_days":
                _write_immutable_json(self.summary_path, summary.model_dump(mode="json"))
            self._write_terminal_status(summary)
            return summary
        finally:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)
            lock_handle.close()

    def _validate_existing_records(self, records: tuple[SoakRecord, ...]) -> None:
        for record in records:
            if record.run_id != self.config.run_id or record.config_hash != self.config_hash:
                raise ValueError("existing soak evidence is bound to another run/config")
            if record.sampled_at < self.config.started_at:
                raise ValueError("existing soak evidence precedes immutable start")

    def _write_running_status(self, records: tuple[SoakRecord, ...], now: datetime) -> None:
        last = records[-1]
        _write_status(
            self.status_path,
            {
                "run_id": self.config.run_id,
                "config_hash": self.config_hash,
                "pid": os.getpid(),
                "state": "running",
                "heartbeat_at": now.isoformat(),
                "sample_count": len(records),
                "last_sample_at": last.sample.at.isoformat(),
                "last_record_sampled_at": last.sampled_at.isoformat(),
                "last_record_hash": last.record_hash,
                "evidence_root": str(self.evidence_root),
            },
        )

    def _write_failure_status(self, now: datetime, failure_class: str) -> None:
        _write_status(
            self.status_path,
            {
                "run_id": self.config.run_id,
                "config_hash": self.config_hash,
                "pid": os.getpid(),
                "state": "failed",
                "heartbeat_at": now.isoformat(),
                "failure_class": failure_class,
                "evidence_root": str(self.evidence_root),
            },
        )

    def _write_terminal_status(self, summary: SoakRunSummary) -> None:
        summary_sha256 = (
            sha256(self.summary_path.read_bytes()).hexdigest()
            if self.summary_path.exists()
            else None
        )
        _write_status(
            self.status_path,
            {
                "run_id": summary.run_id,
                "config_hash": summary.config_hash,
                "pid": os.getpid(),
                "state": summary.status,
                "heartbeat_at": summary.ended_at.isoformat(),
                "sample_count": summary.record_count,
                "terminal_sample_at": (
                    summary.terminal_sample_at.isoformat()
                    if summary.terminal_sample_at is not None
                    else None
                ),
                "last_record_hash": read_soak_records(self.records_path)[-1].record_hash,
                "summary_sha256": summary_sha256,
                "evidence_root": str(self.evidence_root),
            },
        )

    def _summary(self, records: tuple[SoakRecord, ...]) -> SoakRunSummary:
        if not records:
            raise ValueError("soak summary requires at least one record")
        ended_at = records[-1].sampled_at
        elapsed_hours = max(0.0, (ended_at - self.config.started_at).total_seconds() / 3600)
        terminal_sample_at = ended_at if ended_at >= self.config.target_end else None
        return SoakRunSummary(
            run_id=self.config.run_id,
            config_hash=self.config_hash,
            started_at=self.config.started_at,
            ended_at=ended_at,
            elapsed_hours=elapsed_hours,
            record_count=len(records),
            terminal_sample_at=terminal_sample_at,
            status=(
                "completed_60_calendar_days"
                if terminal_sample_at is not None
                else "short_smoke_complete"
            ),
        )


__all__ = [
    "DurablePaperSoakRunner",
    "SoakRecord",
    "SoakRunConfig",
    "SoakRunSummary",
    "append_soak_record",
    "make_soak_record",
    "read_soak_records",
]
