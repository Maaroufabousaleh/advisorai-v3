"""Dedicated 24-hour stability evidence for the admitted Chronos runtime.

The historical :mod:`model_stability` contract intentionally remains limited
to its original three roles.  Chronos has a different runtime contract (a
GPU-backed forecasting worker, exact interpreter identity, and explicit
residual-resource limits), so it gets a separate append-only evidence schema
instead of widening the historical role set.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from advisorai.phase0.bakeoffs import ResourceSample, StabilityWindow, evaluate_stability

HEX = frozenset("0123456789abcdef")

CHRONOS_STABILITY_PROFILE = "chronos-2-small-24h-v1"
CHRONOS_STABILITY_SCHEMA = "advisorai.phase0.chronos-stability-config.v1"
CHRONOS_STABILITY_CANDIDATE = "chronos-2-small"
CHRONOS_STABILITY_MIN_HOURS = 24.0
CHRONOS_STABILITY_DEFAULT_INTERVAL_SECONDS = 300.0
CHRONOS_MAX_RSS_MIB = 4096.0
CHRONOS_MAX_VRAM_MIB = 6144.0
CHRONOS_MAX_RESIDUAL_RSS_MIB = 256.0
CHRONOS_MAX_RESIDUAL_VRAM_MIB = 256.0
CHRONOS_GPU_MODELS_AT_ONCE = 1


def canonical_bytes(value: object) -> bytes:
    """Return the canonical bytes used for immutable/hash-chain records."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def payload_hash(value: object) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in HEX for character in value)


def _is_git_sha(value: str) -> bool:
    return len(value) == 40 and all(character in HEX for character in value)


class ChronosStabilityConfig(BaseModel):
    """Immutable binding for one supervised Chronos stability run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = CHRONOS_STABILITY_SCHEMA
    profile: str = CHRONOS_STABILITY_PROFILE
    run_id: str
    started_at: datetime
    duration_hours: float = Field(ge=CHRONOS_STABILITY_MIN_HOURS)
    interval_seconds: float = Field(ge=0)
    candidate: Literal["chronos-2-small"] = CHRONOS_STABILITY_CANDIDATE

    admission_path: str
    admission_sha256: str
    qualification_evidence_path: str
    qualification_evidence_sha256: str

    repository_root: str
    repository_commit: str
    stability_runner_sha256: str
    chronos_contract_sha256: str
    runtime_qualification_sha256: str

    model_revision: str
    checkpoint_sha256: str
    config_sha256: str
    python_launcher_hash: str
    resolved_python_binary_hash: str
    pyvenv_cfg_hash: str
    installed_environment_sha256: str
    runtime_lock_hash: str
    runner_hash: str
    worker_script_sha256: str
    environment_fingerprint: str
    runtime_identity_sha256: str

    dataset_id: str
    dataset_hash: str
    allowed_residual_growth_mib: float = Field(default=128.0, ge=0)

    max_rss_mib: float = Field(default=CHRONOS_MAX_RSS_MIB, ge=0)
    max_vram_mib: float = Field(default=CHRONOS_MAX_VRAM_MIB, ge=0)
    max_residual_rss_mib: float = Field(default=CHRONOS_MAX_RESIDUAL_RSS_MIB, ge=0)
    max_residual_vram_mib: float = Field(default=CHRONOS_MAX_RESIDUAL_VRAM_MIB, ge=0)
    gpu_models_at_once: int = Field(default=CHRONOS_GPU_MODELS_AT_ONCE, ge=1)

    repeatability_policy: Literal["deterministic_required"] = "deterministic_required"
    credentials_loaded: Literal[False] = False
    order_writes_attempted: Literal[False] = False
    execution_authority_present: Literal[False] = False

    @model_validator(mode="after")
    def validate_config(self) -> ChronosStabilityConfig:
        if self.schema_version != CHRONOS_STABILITY_SCHEMA:
            raise ValueError("unsupported Chronos stability config schema")
        if self.profile != CHRONOS_STABILITY_PROFILE:
            raise ValueError("unsupported Chronos stability profile")
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("Chronos stability start must include a timezone")
        if not self.run_id.strip() or Path(self.run_id).name != self.run_id:
            raise ValueError("Chronos stability run_id must be a simple directory identity")
        for path_value in (
            self.admission_path,
            self.qualification_evidence_path,
            self.repository_root,
        ):
            if not Path(path_value).is_absolute():
                raise ValueError("Chronos stability paths must be absolute")
        if not _is_git_sha(self.repository_commit):
            raise ValueError("Chronos stability repository identity must be a commit SHA")
        for digest in (
            self.admission_sha256,
            self.qualification_evidence_sha256,
            self.stability_runner_sha256,
            self.chronos_contract_sha256,
            self.runtime_qualification_sha256,
            self.checkpoint_sha256,
            self.config_sha256,
            self.python_launcher_hash,
            self.resolved_python_binary_hash,
            self.pyvenv_cfg_hash,
            self.installed_environment_sha256,
            self.runtime_lock_hash,
            self.runner_hash,
            self.worker_script_sha256,
            self.environment_fingerprint,
            self.runtime_identity_sha256,
            self.dataset_hash,
        ):
            if not _is_sha256(digest):
                raise ValueError("Chronos stability identity fields must be SHA-256")
        if not self.model_revision.strip() or not self.dataset_id.strip():
            raise ValueError("Chronos stability model and dataset identities are required")
        if (
            self.allowed_residual_growth_mib != 128.0
            or self.max_rss_mib != CHRONOS_MAX_RSS_MIB
            or self.max_vram_mib != CHRONOS_MAX_VRAM_MIB
            or self.max_residual_rss_mib != CHRONOS_MAX_RESIDUAL_RSS_MIB
            or self.max_residual_vram_mib != CHRONOS_MAX_RESIDUAL_VRAM_MIB
            or self.gpu_models_at_once != CHRONOS_GPU_MODELS_AT_ONCE
        ):
            raise ValueError("Chronos stability resource ceilings cannot be weakened")
        return self


class ChronosStabilitySample(BaseModel):
    """One complete model-load/inference/cleanup cycle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "advisorai.phase0.chronos-stability-sample.v1"
    candidate: Literal["chronos-2-small"] = CHRONOS_STABILITY_CANDIDATE
    sampled_at: datetime
    status: Literal["measured", "failed", "quarantined"]
    runtime_identity_sha256: str
    qualification_manifest_hash: str
    repeatability_policy: Literal["deterministic_required"] = "deterministic_required"

    offline_cached_inference: bool
    network_access_attempted: bool
    model_loaded: bool
    inference_completed: bool
    batch_completed: bool
    output_schema_valid: bool
    finite_output_valid: bool
    repeated_outputs_equal: bool
    worker_process_terminated: bool
    gpu_lease_released: bool
    resource_limit_passed: bool
    memory_released: bool

    rss_peak_mib: float = Field(ge=0)
    rss_after_unload_mib: float = Field(ge=0)
    rss_residual_mib: float | None = Field(default=None, ge=0)
    vram_peak_mib: float | None = Field(default=None, ge=0)
    vram_after_unload_mib: float | None = Field(default=None, ge=0)
    vram_residual_mib: float | None = Field(default=None, ge=0)
    failure_reason: str | None = None

    @model_validator(mode="after")
    def validate_sample(self) -> ChronosStabilitySample:
        if self.sampled_at.tzinfo is None or self.sampled_at.utcoffset() is None:
            raise ValueError("Chronos stability sample time must include a timezone")
        for digest in (self.runtime_identity_sha256, self.qualification_manifest_hash):
            if not _is_sha256(digest):
                raise ValueError("Chronos sample identity fields must be SHA-256")
        values = (
            self.rss_peak_mib,
            self.rss_after_unload_mib,
            self.rss_residual_mib,
            self.vram_peak_mib,
            self.vram_after_unload_mib,
            self.vram_residual_mib,
        )
        if any(value is not None and not math.isfinite(value) for value in values):
            raise ValueError("Chronos stability resources must be finite")
        if self.failure_reason is not None:
            if len(self.failure_reason) > 240 or any(
                character
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 _-.:/"
                for character in self.failure_reason
            ):
                raise ValueError("Chronos stability failure reason must be sanitized")
        if not self.passed and not self.failure_reason:
            raise ValueError("failed Chronos stability samples require a sanitized reason")
        if self.passed and self.failure_reason is not None:
            raise ValueError("passing Chronos stability samples cannot claim a failure")
        return self

    @property
    def passed(self) -> bool:
        return (
            self.status == "measured"
            and self.offline_cached_inference
            and not self.network_access_attempted
            and self.model_loaded
            and self.inference_completed
            and self.batch_completed
            and self.output_schema_valid
            and self.finite_output_valid
            and self.repeated_outputs_equal
            and self.worker_process_terminated
            and self.gpu_lease_released
            and self.resource_limit_passed
            and self.memory_released
            and self.rss_peak_mib <= CHRONOS_MAX_RSS_MIB
            and self.vram_peak_mib is not None
            and self.vram_peak_mib <= CHRONOS_MAX_VRAM_MIB
            and self.rss_residual_mib is not None
            and self.rss_residual_mib <= CHRONOS_MAX_RESIDUAL_RSS_MIB
            and self.vram_residual_mib is not None
            and self.vram_residual_mib <= CHRONOS_MAX_RESIDUAL_VRAM_MIB
        )


class ChronosStabilityCycle(BaseModel):
    """Hash-chained append-only cycle record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "advisorai.phase0.chronos-stability-cycle.v1"
    run_id: str
    config_hash: str
    sequence: int = Field(ge=0)
    sampled_at: datetime
    previous_record_hash: str | None
    sample: ChronosStabilitySample
    record_hash: str

    @model_validator(mode="after")
    def validate_cycle(self) -> ChronosStabilityCycle:
        if self.sampled_at.tzinfo is None or self.sampled_at.utcoffset() is None:
            raise ValueError("Chronos cycle time must include a timezone")
        if self.sampled_at != self.sample.sampled_at:
            raise ValueError("Chronos cycle and sample timestamps must agree")
        if self.sequence == 0 and self.previous_record_hash is not None:
            raise ValueError("first Chronos cycle cannot have a predecessor")
        if self.sequence > 0 and self.previous_record_hash is None:
            raise ValueError("later Chronos cycles require a predecessor")
        for digest in (self.config_hash, self.previous_record_hash, self.record_hash):
            if digest is not None and not _is_sha256(digest):
                raise ValueError("Chronos cycle hashes must be SHA-256")
        expected = payload_hash(self.model_dump(mode="json", exclude={"record_hash"}))
        if expected != self.record_hash:
            raise ValueError("Chronos cycle hash is inconsistent")
        return self


class ChronosStabilitySummary(BaseModel):
    """Immutable summary; only ``passed`` is a terminal qualification result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "advisorai.phase0.chronos-stability-summary.v1"
    run_id: str
    started_at: datetime
    ended_at: datetime
    elapsed_hours: float = Field(ge=0)
    cycle_count: int = Field(ge=1)
    terminal_sample: bool
    all_cycles_passed: bool
    stability_24h_passed: bool
    window: StabilityWindow | None
    status: Literal["passed", "failed", "short_smoke_complete"]

    @model_validator(mode="after")
    def validate_summary(self) -> ChronosStabilitySummary:
        if self.ended_at < self.started_at:
            raise ValueError("Chronos stability cannot end before it starts")
        if self.stability_24h_passed and (
            self.elapsed_hours < CHRONOS_STABILITY_MIN_HOURS
            or not self.terminal_sample
            or not self.all_cycles_passed
            or self.window is None
            or not self.window.passed
        ):
            raise ValueError("Chronos 24-hour stability lacks complete passing evidence")
        expected = (
            "passed"
            if self.stability_24h_passed
            else "failed"
            if not self.all_cycles_passed or self.terminal_sample
            else "short_smoke_complete"
        )
        if self.status != expected:
            raise ValueError("Chronos stability summary status is inconsistent")
        return self


def make_chronos_cycle(
    config: ChronosStabilityConfig,
    sample: ChronosStabilitySample,
    *,
    sequence: int,
    previous_record_hash: str | None = None,
) -> ChronosStabilityCycle:
    payload = {
        "schema_version": "advisorai.phase0.chronos-stability-cycle.v1",
        "run_id": config.run_id,
        "config_hash": payload_hash(config.model_dump(mode="json")),
        "sequence": sequence,
        "sampled_at": sample.sampled_at,
        "previous_record_hash": previous_record_hash,
        "sample": sample,
    }
    unsealed = ChronosStabilityCycle.model_construct(**payload, record_hash="0" * 64)
    canonical = unsealed.model_dump(mode="json", exclude={"record_hash"})
    return ChronosStabilityCycle.model_validate(
        {**canonical, "record_hash": payload_hash(canonical)}
    )


def append_chronos_cycle(path: Path, cycle: ChronosStabilityCycle) -> None:
    """Append one fsync'd cycle after validating the complete prior chain."""

    existing = read_chronos_cycles(path)
    if cycle.sequence != len(existing):
        raise ValueError("Chronos stability sequence is not append-only")
    expected_previous = existing[-1].record_hash if existing else None
    if cycle.previous_record_hash != expected_previous:
        raise ValueError("Chronos stability predecessor hash is inconsistent")
    if existing and cycle.sampled_at <= existing[-1].sampled_at:
        raise ValueError("Chronos stability samples must be appended in time order")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(canonical_bytes(cycle.model_dump(mode="json")) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_chronos_cycles(path: Path) -> tuple[ChronosStabilityCycle, ...]:
    if not path.exists():
        return ()
    cycles = tuple(
        ChronosStabilityCycle.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    for index, cycle in enumerate(cycles):
        if cycle.sequence != index:
            raise ValueError("Chronos stability sequence contains a gap")
        expected = cycles[index - 1].record_hash if index else None
        if cycle.previous_record_hash != expected:
            raise ValueError("Chronos stability hash chain is broken")
        if index and cycle.sampled_at <= cycles[index - 1].sampled_at:
            raise ValueError("Chronos stability samples are not strictly time ordered")
    return cycles


def summarize_chronos_stability(
    config: ChronosStabilityConfig,
    cycles: tuple[ChronosStabilityCycle, ...],
) -> ChronosStabilitySummary:
    if not cycles:
        raise ValueError("Chronos stability summary requires at least one cycle")
    config_hash = payload_hash(config.model_dump(mode="json"))
    if any(
        cycle.run_id != config.run_id
        or cycle.config_hash != config_hash
        or cycle.sample.candidate != config.candidate
        or cycle.sample.runtime_identity_sha256 != config.runtime_identity_sha256
        for cycle in cycles
    ):
        raise ValueError("Chronos stability cycles do not bind the immutable identity config")
    ended_at = cycles[-1].sampled_at
    elapsed_hours = max(0.0, (ended_at - config.started_at).total_seconds() / 3600)
    terminal_sample = elapsed_hours >= config.duration_hours
    all_cycles_passed = all(cycle.sample.passed for cycle in cycles)
    window: StabilityWindow | None = None
    if terminal_sample:
        resource_samples = tuple(
            ResourceSample(
                rss_mib=cycle.sample.rss_after_unload_mib,
                vms_mib=0.0,
                cpu_percent=0.0,
                sampled_at=cycle.sample.sampled_at,
            )
            for cycle in cycles
        )
        window = evaluate_stability(
            started_at=config.started_at,
            ended_at=ended_at,
            samples=resource_samples,
            allowed_growth_mib=config.allowed_residual_growth_mib,
        )
    stability_passed = (
        terminal_sample and all_cycles_passed and window is not None and window.passed
    )
    status = (
        "passed"
        if stability_passed
        else "failed"
        if not all_cycles_passed or terminal_sample
        else "short_smoke_complete"
    )
    return ChronosStabilitySummary(
        run_id=config.run_id,
        started_at=config.started_at,
        ended_at=ended_at,
        elapsed_hours=elapsed_hours,
        cycle_count=len(cycles),
        terminal_sample=terminal_sample,
        all_cycles_passed=all_cycles_passed,
        stability_24h_passed=stability_passed,
        window=window,
        status=status,
    )


__all__ = [
    "CHRONOS_MAX_RESIDUAL_RSS_MIB",
    "CHRONOS_MAX_RESIDUAL_VRAM_MIB",
    "CHRONOS_MAX_RSS_MIB",
    "CHRONOS_MAX_VRAM_MIB",
    "CHRONOS_STABILITY_CANDIDATE",
    "CHRONOS_STABILITY_DEFAULT_INTERVAL_SECONDS",
    "CHRONOS_STABILITY_MIN_HOURS",
    "CHRONOS_STABILITY_PROFILE",
    "CHRONOS_STABILITY_SCHEMA",
    "ChronosStabilityConfig",
    "ChronosStabilityCycle",
    "ChronosStabilitySample",
    "ChronosStabilitySummary",
    "append_chronos_cycle",
    "canonical_bytes",
    "make_chronos_cycle",
    "payload_hash",
    "read_chronos_cycles",
    "summarize_chronos_stability",
]
