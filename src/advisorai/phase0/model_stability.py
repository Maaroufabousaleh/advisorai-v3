"""Append-only, resumable Phase-0 stability evidence for selected local models."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from advisorai.phase0.bakeoffs import ResourceSample, StabilityWindow, evaluate_stability

HEX = frozenset("0123456789abcdef")


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def payload_hash(value: object) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


class ModelStabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "advisorai.phase0.model-stability-config.v1"
    run_id: str
    started_at: datetime
    duration_hours: float = Field(gt=0)
    interval_seconds: float = Field(ge=0)
    candidates: tuple[str, ...] = Field(min_length=1)
    forecast_dataset_hash: str
    sentiment_dataset_hash: str
    benchmark_report_hash: str
    allowed_residual_growth_mib: float = Field(default=128, ge=0)

    @model_validator(mode="after")
    def validate_config(self) -> ModelStabilityConfig:
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("stability start must include a timezone")
        if not self.run_id.strip() or len(self.candidates) != len(set(self.candidates)):
            raise ValueError("stability run and candidate identities must be unique and non-blank")
        if any(not candidate.strip() for candidate in self.candidates):
            raise ValueError("stability candidate identities cannot be blank")
        for digest in (
            self.forecast_dataset_hash,
            self.sentiment_dataset_hash,
            self.benchmark_report_hash,
        ):
            if len(digest) != 64 or any(character not in HEX for character in digest):
                raise ValueError("stability evidence hashes must be SHA-256")
        return self


class CandidateStabilitySample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: str
    status: str
    qualification_manifest_hash: str
    privacy_passed: bool
    resource_limit_passed: bool
    memory_released: bool
    current_rss_after_unload_mib: float = Field(ge=0)
    peak_rss_mib: float = Field(ge=0)
    peak_vram_mib: float = Field(ge=0)
    failure_reason: str | None = None

    @model_validator(mode="after")
    def validate_sample(self) -> CandidateStabilitySample:
        if not self.candidate.strip() or self.status not in {"measured", "failed", "quarantined"}:
            raise ValueError("stability samples require a valid candidate and status")
        if len(self.qualification_manifest_hash) != 64 or any(
            character not in HEX for character in self.qualification_manifest_hash
        ):
            raise ValueError("qualification manifest identity must be SHA-256")
        if any(
            not math.isfinite(value)
            for value in (
                self.current_rss_after_unload_mib,
                self.peak_rss_mib,
                self.peak_vram_mib,
            )
        ):
            raise ValueError("stability resource values must be finite")
        passed = (
            self.status == "measured"
            and self.privacy_passed
            and self.resource_limit_passed
            and self.memory_released
        )
        if not passed and not self.failure_reason:
            raise ValueError("failed stability samples require a sanitized reason")
        if passed and self.failure_reason is not None:
            raise ValueError("passing stability samples cannot claim a failure")
        return self

    @property
    def passed(self) -> bool:
        return (
            self.status == "measured"
            and self.privacy_passed
            and self.resource_limit_passed
            and self.memory_released
        )


class ModelStabilityCycle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "advisorai.phase0.model-stability-cycle.v1"
    run_id: str
    config_hash: str
    sequence: int = Field(ge=0)
    sampled_at: datetime
    previous_record_hash: str | None
    samples: tuple[CandidateStabilitySample, ...] = Field(min_length=1)
    record_hash: str

    @model_validator(mode="after")
    def validate_cycle(self) -> ModelStabilityCycle:
        if self.sampled_at.tzinfo is None or self.sampled_at.utcoffset() is None:
            raise ValueError("stability sample time must include a timezone")
        if self.sequence == 0 and self.previous_record_hash is not None:
            raise ValueError("first stability record cannot have a predecessor")
        if self.sequence > 0 and self.previous_record_hash is None:
            raise ValueError("later stability records require a predecessor")
        for digest in (self.config_hash, self.previous_record_hash, self.record_hash):
            if digest is not None and (
                len(digest) != 64 or any(character not in HEX for character in digest)
            ):
                raise ValueError("stability chain hashes must be SHA-256")
        if len({sample.candidate for sample in self.samples}) != len(self.samples):
            raise ValueError("a cycle cannot contain duplicate candidates")
        expected = payload_hash(self.model_dump(mode="json", exclude={"record_hash"}))
        if expected != self.record_hash:
            raise ValueError("stability record hash is inconsistent")
        return self


class ModelStabilitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "advisorai.phase0.model-stability-summary.v1"
    run_id: str
    started_at: datetime
    ended_at: datetime
    elapsed_hours: float = Field(ge=0)
    cycle_count: int = Field(ge=1)
    candidate_windows: dict[str, StabilityWindow | None]
    all_cycles_passed: bool
    stability_24h_passed: bool
    status: str

    @model_validator(mode="after")
    def validate_summary(self) -> ModelStabilitySummary:
        if self.ended_at < self.started_at:
            raise ValueError("stability summary cannot end before it starts")
        if self.stability_24h_passed and (
            self.elapsed_hours < 24
            or not self.all_cycles_passed
            or any(
                window is None or not window.passed for window in self.candidate_windows.values()
            )
        ):
            raise ValueError("24-hour stability cannot pass without complete passing evidence")
        expected_status = (
            "passed"
            if self.stability_24h_passed
            else "failed"
            if self.elapsed_hours >= 24
            else "short_smoke_complete"
        )
        if self.status != expected_status:
            raise ValueError("stability summary status is inconsistent")
        return self


def make_cycle(
    config: ModelStabilityConfig,
    samples: tuple[CandidateStabilitySample, ...],
    *,
    sequence: int,
    sampled_at: datetime | None = None,
    previous_record_hash: str | None = None,
) -> ModelStabilityCycle:
    payload = {
        "schema_version": "advisorai.phase0.model-stability-cycle.v1",
        "run_id": config.run_id,
        "config_hash": payload_hash(config.model_dump(mode="json")),
        "sequence": sequence,
        "sampled_at": sampled_at or datetime.now(UTC),
        "previous_record_hash": previous_record_hash,
        "samples": samples,
    }
    unsealed = ModelStabilityCycle.model_construct(**payload, record_hash="0" * 64)
    canonical = unsealed.model_dump(mode="json", exclude={"record_hash"})
    return ModelStabilityCycle.model_validate({**canonical, "record_hash": payload_hash(canonical)})


def append_cycle(path: Path, cycle: ModelStabilityCycle) -> None:
    """Append one fsync'd JSONL cycle after verifying the existing chain."""

    existing = read_cycles(path)
    if cycle.sequence != len(existing):
        raise ValueError("stability sequence is not append-only")
    expected_previous = existing[-1].record_hash if existing else None
    if cycle.previous_record_hash != expected_previous:
        raise ValueError("stability predecessor hash is inconsistent")
    if existing and cycle.sampled_at <= existing[-1].sampled_at:
        raise ValueError("stability samples must be appended in time order")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(canonical_bytes(cycle.model_dump(mode="json")) + b"\n")
        handle.flush()
        __import__("os").fsync(handle.fileno())


def read_cycles(path: Path) -> tuple[ModelStabilityCycle, ...]:
    if not path.exists():
        return ()
    cycles = tuple(
        ModelStabilityCycle.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    for index, cycle in enumerate(cycles):
        if cycle.sequence != index:
            raise ValueError("stability sequence contains a gap")
        expected = cycles[index - 1].record_hash if index else None
        if cycle.previous_record_hash != expected:
            raise ValueError("stability hash chain is broken")
        if index and cycle.sampled_at <= cycles[index - 1].sampled_at:
            raise ValueError("stability samples are not strictly time ordered")
    return cycles


def summarize_stability(
    config: ModelStabilityConfig,
    cycles: tuple[ModelStabilityCycle, ...],
) -> ModelStabilitySummary:
    if not cycles:
        raise ValueError("stability summary requires at least one cycle")
    if any(cycle.run_id != config.run_id for cycle in cycles):
        raise ValueError("stability cycles belong to another run")
    config_hash = payload_hash(config.model_dump(mode="json"))
    if any(cycle.config_hash != config_hash for cycle in cycles):
        raise ValueError("stability cycles do not bind the immutable run config")
    ended_at = cycles[-1].sampled_at
    elapsed = max(0.0, (ended_at - config.started_at).total_seconds() / 3600)
    windows: dict[str, StabilityWindow | None] = {}
    for candidate in config.candidates:
        samples = tuple(
            ResourceSample(
                rss_mib=sample.current_rss_after_unload_mib,
                vms_mib=0,
                cpu_percent=0,
                sampled_at=cycle.sampled_at,
            )
            for cycle in cycles
            for sample in cycle.samples
            if sample.candidate == candidate
        )
        windows[candidate] = (
            evaluate_stability(
                started_at=config.started_at,
                ended_at=ended_at,
                samples=samples,
                allowed_growth_mib=config.allowed_residual_growth_mib,
            )
            if elapsed >= 24 and samples
            else None
        )
    all_passed = all(
        set(sample.candidate for sample in cycle.samples) == set(config.candidates)
        and all(sample.passed for sample in cycle.samples)
        for cycle in cycles
    )
    passed = (
        elapsed >= 24
        and all_passed
        and all(window is not None and window.passed for window in windows.values())
    )
    return ModelStabilitySummary(
        run_id=config.run_id,
        started_at=config.started_at,
        ended_at=ended_at,
        elapsed_hours=elapsed,
        cycle_count=len(cycles),
        candidate_windows=windows,
        all_cycles_passed=all_passed,
        stability_24h_passed=passed,
        status="passed" if passed else "failed" if elapsed >= 24 else "short_smoke_complete",
    )
