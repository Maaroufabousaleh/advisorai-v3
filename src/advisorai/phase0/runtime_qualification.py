"""Reproducible local-model runtime qualification for Phase 0.

This module is deliberately a qualification boundary, not a model-serving
boundary.  Runners are injected, checkpoints are immutable pins, and a
missing runner/cache is recorded as quarantine rather than replaced by a
different model family.  Successful records can be converted directly into
the existing :class:`~advisorai.phase0.bakeoffs.BakeoffResult` contract.

The default registry describes the exact public candidates selected for the
first qualification pass.  It does not download weights.  Caches belong under
the operator's external model cache (normally ``~/.cache/advisorai-v3``), and
all generated evidence is sanitized JSON under the ignored ``artifacts``
tree.
"""

from __future__ import annotations

import gc
import importlib.metadata
import importlib.util
import inspect
import json
import math
import os
import platform
import socket
import statistics
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol, cast

import psutil
from pydantic import BaseModel, ConfigDict, Field, model_validator

from advisorai.models.forecasting import (
    DriftForecaster,
    ForecastEvaluation,
    GpuModelLease,
    LinearForecaster,
    NaiveForecaster,
    SeasonalForecaster,
    evaluate_forecasts,
)
from advisorai.phase0.bakeoffs import BakeoffResult, ComponentKind

REQUIRED_TRANSFORMERS_VERSION = "5.5.4"
REQUIRED_HUGGINGFACE_HUB_VERSION = "1.26.1"
HEX40 = "0123456789abcdef"
HEX64 = "0123456789abcdef"


class QualificationStatus(StrEnum):
    MEASURED = "measured"
    QUARANTINED = "quarantined"
    FAILED = "failed"


class LicenseAdmissionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class RuntimeAdmissionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class RepeatabilityPolicy(StrEnum):
    DETERMINISTIC_REQUIRED = "deterministic_required"
    SEEDED_REPRODUCIBLE = "seeded_reproducible"
    STOCHASTIC_CHARACTERIZED = "stochastic_characterized"


class ModelTask(StrEnum):
    FORECAST = "forecast"
    FINANCE_SENTIMENT = "finance_sentiment"
    TSPULSE_FEATURES = "tspulse_features"


class ModelFamily(StrEnum):
    NAIVE = "naive"
    DRIFT = "drift"
    SEASONAL = "seasonal"
    LINEAR = "linear"
    LIGHTGBM = "lightgbm"
    FINBERT = "finbert-family"
    TTM_R2 = "ttm-r2"
    TSPULSE = "tspulse"
    CHRONOS_2_SMALL = "chronos-2-small"
    KRONOS_MINI = "kronos-mini"
    KRONOS_SMALL = "kronos-small"
    TABPFN_TS = "tabpfn-ts"


class QualificationError(RuntimeError):
    """Base error for a failed qualification operation."""


class CheckpointPinError(QualificationError):
    """An immutable checkpoint pin is malformed or incomplete."""


class CheckpointNotCachedError(QualificationError):
    """A pinned checkpoint is not present in the external cache."""


class CheckpointIntegrityError(QualificationError):
    """A cached checkpoint does not match its pinned digest."""


class CompatibilityError(QualificationError):
    """A runner does not match the reviewed dependency baseline."""


class InvalidModelOutputError(QualificationError):
    """A runner returned an output outside its typed task contract."""


class NoSilentFallbackError(QualificationError):
    """A runner identifies a different model family than the requested one."""


class ResourceLimitError(QualificationError):
    """A runtime exceeded the laptop qualification resource ceiling."""


class NetworkAccessAttemptError(QualificationError):
    """A cached qualification attempted to access a network socket."""


class ArtifactPin(BaseModel):
    """One repository file and its expected SHA-256, if already downloaded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relative_path: str
    sha256: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_artifact(self) -> ArtifactPin:
        path = Path(self.relative_path)
        if (
            not self.relative_path.strip()
            or path.is_absolute()
            or ".." in path.parts
            or str(path) in {".", ""}
        ):
            raise ValueError("artifact paths must be relative and traversal-free")
        if self.sha256 is not None and (
            len(self.sha256) != 64 or any(character not in HEX64 for character in self.sha256)
        ):
            raise ValueError("artifact SHA-256 must be a lowercase 64-character digest")
        return self


class LicenseAdmission(BaseModel):
    """Explicit operator decision for a model's license provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: LicenseAdmissionStatus = LicenseAdmissionStatus.PENDING
    license_identifier: str | None = None
    reviewed_at: datetime | None = None
    evidence_reference: str | None = None

    @model_validator(mode="after")
    def validate_admission(self) -> LicenseAdmission:
        if self.reviewed_at is not None and (
            self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None
        ):
            raise ValueError("license review timestamp must include a timezone")
        if self.status == LicenseAdmissionStatus.APPROVED:
            if not self.license_identifier or not self.evidence_reference or self.reviewed_at is None:
                raise ValueError("approved license admission requires identity, date, and evidence")
        return self


class RuntimePin(BaseModel):
    """Pinned executable runtime identity, separate from model weights."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project: str
    version_or_commit: str
    python_constraint: str
    dependencies: tuple[str, ...] = ()
    # A pending registry entry intentionally has no lock identity.  An
    # approved entry must point at an immutable, operator-created lock
    # artifact; hashing the desired dependency strings is not sufficient.
    lock_hash: str | None = None
    lock_artifact_path: str | None = None
    installed_environment_manifest_path: str | None = None
    installed_environment_sha256: str | None = None
    environment_fingerprint: str | None = None
    # ``python_executable``/``python_executable_hash`` remain accepted as a
    # compatibility spelling for the launcher/resolved-binary pair. New
    # admissions should use the explicit launcher fields below.
    python_executable: str | None = None
    python_executable_hash: str | None = None
    python_launcher: str | None = None
    python_launcher_hash: str | None = None
    python_launcher_target: str | None = None
    resolved_python_binary_hash: str | None = None
    pyvenv_cfg_path: str | None = None
    pyvenv_cfg_hash: str | None = None
    environment_path: str
    worker_script: str | None = None
    worker_kind: str = "qualification"
    runner_version: str | None = None
    runner_hash: str | None = None
    status: RuntimeAdmissionStatus = RuntimeAdmissionStatus.PENDING
    evidence_reference: str | None = None

    @model_validator(mode="after")
    def validate_runtime_pin(self) -> RuntimePin:
        if not self.project.strip() or not self.version_or_commit.strip():
            raise ValueError("runtime project and version/commit are required")
        if not self.python_constraint.strip():
            raise ValueError("runtime Python constraint is required")
        for field_name in (
            "lock_hash",
            "installed_environment_sha256",
            "environment_fingerprint",
            "python_executable_hash",
            "python_launcher_hash",
            "resolved_python_binary_hash",
            "pyvenv_cfg_hash",
            "runner_hash",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                len(value) != 64 or any(character not in HEX64 for character in value)
            ):
                raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
        if not Path(self.environment_path).expanduser().is_absolute():
            raise ValueError("runtime environment path must be absolute")
        if self.worker_kind.strip() == "":
            raise ValueError("runtime worker kind is required")
        if self.status == RuntimeAdmissionStatus.APPROVED and not self.evidence_reference:
            raise ValueError("approved runtime admission requires evidence")
        if self.status == RuntimeAdmissionStatus.APPROVED:
            if "pending" in self.version_or_commit.lower():
                raise ValueError("approved runtime admission cannot use a pending version")
            if any("==" not in dependency and "@" not in dependency for dependency in self.dependencies):
                raise ValueError("approved runtime dependencies must be exact versions or commits")
            required = {
                "lock_hash": self.lock_hash,
                "lock_artifact_path": self.lock_artifact_path,
                "installed_environment_manifest_path": self.installed_environment_manifest_path,
                "installed_environment_sha256": self.installed_environment_sha256,
                "environment_fingerprint": self.environment_fingerprint,
                "python_launcher": self.python_launcher or self.python_executable,
                "python_launcher_hash": self.python_launcher_hash or self.python_executable_hash,
                "resolved_python_binary_hash": self.resolved_python_binary_hash
                or self.python_executable_hash,
                "worker_script": self.worker_script,
                "runner_version": self.runner_version,
                "runner_hash": self.runner_hash,
            }
            missing = sorted(name for name, value in required.items() if not value)
            if missing:
                raise ValueError(
                    "approved runtime admission requires immutable worker identity: "
                    + ", ".join(missing)
                )
            for name, value in (
                ("lock_artifact_path", self.lock_artifact_path),
                ("installed_environment_manifest_path", self.installed_environment_manifest_path),
                ("python_launcher", self.python_launcher or self.python_executable),
                ("worker_script", self.worker_script),
            ):
                if not Path(str(value)).expanduser().is_absolute():
                    raise ValueError(f"approved runtime {name} must be an absolute path")
            if self.installed_environment_sha256 is None:
                raise ValueError("approved runtime requires installed environment attestation")
        return self

    def assert_environment_outside_repository(self, repository_root: Path) -> None:
        environment_raw = Path(self.environment_path).expanduser()
        if environment_raw.is_symlink():
            raise QualificationError("execution runtime environment must not be a symlink")
        environment = environment_raw.resolve(strict=False)
        root = repository_root.expanduser().resolve(strict=False)
        try:
            environment.relative_to(root)
        except ValueError:
            return
        raise QualificationError("execution runtime environment must be outside the repository")

    def resolve_python_launcher(self) -> Path:
        """Resolve the launcher inside the pinned environment.

        POSIX virtualenvs commonly make this path a symlink to the base
        interpreter. The launcher itself is therefore allowed to be a
        symlink; its target and resolved binary hash are attested separately.
        """

        environment_raw = Path(self.environment_path).expanduser()
        if environment_raw.is_symlink():
            raise QualificationError("execution runtime environment must not be a symlink")
        environment = environment_raw.resolve(strict=False)
        if self.python_launcher and self.python_executable:
            if Path(self.python_launcher).expanduser() != Path(self.python_executable).expanduser():
                raise QualificationError("conflicting pinned Python launcher paths")
        configured = (
            Path(self.python_launcher or self.python_executable).expanduser()
            if (self.python_launcher or self.python_executable)
            else environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )
        try:
            configured.relative_to(environment)
        except ValueError as exc:
            raise QualificationError("runtime Python launcher must be inside its pinned environment") from exc
        if configured.is_dir() or not configured.exists():
            raise QualificationError("pinned runtime Python launcher is missing")
        return configured

    def resolve_python_executable(self) -> Path:
        """Compatibility API returning the resolved interpreter binary."""

        return self.resolve_python_binary()

    def resolve_python_binary(self) -> Path:
        launcher = self.resolve_python_launcher()
        binary = launcher.resolve(strict=False)
        if not binary.is_file():
            raise QualificationError("resolved runtime Python binary is missing")
        return binary

    @property
    def installed_environment_hash(self) -> str | None:
        """Compatibility/readability alias for the installed manifest digest."""

        return self.installed_environment_sha256


class RepositoryPin(BaseModel):
    """Immutable repository identity and complete expected file listing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_id: str
    revision: str
    license: str
    # ``artifacts`` remains a backwards-compatible input alias. New pins must
    # use the explicit runtime/provenance closure fields.
    artifacts: tuple[ArtifactPin, ...] = ()
    runtime_artifacts: tuple[ArtifactPin, ...] = ()
    provenance_artifacts: tuple[ArtifactPin, ...] = ()
    cache_subdir: str = "model"

    @model_validator(mode="after")
    def validate_repository(self) -> RepositoryPin:
        if not self.repository_id.strip():
            raise ValueError("repository ID is required")
        if len(self.revision) != 40 or any(character not in HEX40 for character in self.revision):
            raise ValueError("repository revision must be a 40-character lowercase commit SHA")
        if not self.license.strip():
            raise ValueError("repository license must be recorded, or explicitly not-declared")
        if self.artifacts and (self.runtime_artifacts or self.provenance_artifacts):
            raise ValueError("use runtime_artifacts/provenance_artifacts instead of artifacts")
        paths = [artifact.relative_path for artifact in self.all_artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("repository artifact paths must be unique")
        if not self.cache_subdir.strip() or Path(self.cache_subdir).is_absolute():
            raise ValueError("repository cache subdirectory must be relative")
        return self

    @property
    def all_artifacts(self) -> tuple[ArtifactPin, ...]:
        runtime = self.runtime_artifacts or self.artifacts
        return (*runtime, *self.provenance_artifacts)


class CheckpointPin(BaseModel):
    """Model and optional tokenizer pins stored outside the repository."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_family: str
    repository: RepositoryPin
    tokenizer: RepositoryPin | None = None
    cache_path: str
    quantization: str = "none"
    license_admission: LicenseAdmission = Field(default_factory=LicenseAdmission)
    tokenizer_license_admission: LicenseAdmission | None = None

    @model_validator(mode="after")
    def validate_checkpoint(self) -> CheckpointPin:
        cache = Path(self.cache_path).expanduser()
        if not cache.is_absolute():
            raise ValueError("checkpoint cache path must be absolute and outside the repository")
        if not self.model_family.strip():
            raise ValueError("checkpoint model family is required")
        if not self.quantization.strip():
            raise ValueError("checkpoint quantization must be explicit")
        if self.tokenizer is not None and self.tokenizer_license_admission is None:
            raise ValueError("tokenizer repositories require a separate license admission")
        return self

    @property
    def repository_id(self) -> str:
        return self.repository.repository_id

    @property
    def revision(self) -> str:
        return self.repository.revision

    @property
    def license(self) -> str:
        return self.repository.license

    @property
    def artifacts(self) -> tuple[ArtifactPin, ...]:
        return self.repository.all_artifacts

    @property
    def runtime_artifacts(self) -> tuple[ArtifactPin, ...]:
        return self.repository.runtime_artifacts or self.repository.artifacts

    def assert_cache_outside_repository(self, repository_root: Path) -> None:
        cache = Path(self.cache_path).expanduser().resolve(strict=False)
        root = repository_root.expanduser().resolve(strict=False)
        try:
            cache.relative_to(root)
        except ValueError:
            return
        raise CheckpointPinError("model cache must not be inside the AdvisorAI repository")


class RuntimeEnvironment(BaseModel):
    """Dependency and hardware identity captured with every qualification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    python_version: str
    transformers_version: str | None = None
    huggingface_hub_version: str | None = None
    torch_version: str | None = None
    cuda_version: str | None = None
    device: str
    dtype: str
    quantization: str
    cache_path: str
    runner_version: str
    runner_hash: str
    runtime_lock_hash: str | None = None
    lock_artifact_path: str | None = None
    installed_environment_sha256: str | None = None
    environment_fingerprint: str | None = None
    sys_executable: str | None = None
    sys_prefix: str | None = None
    sys_base_prefix: str | None = None
    python_launcher_hash: str | None = None
    python_launcher_target: str | None = None
    resolved_python_binary_hash: str | None = None
    pyvenv_cfg_hash: str | None = None
    python_executable_hash: str | None = None

    @model_validator(mode="after")
    def validate_environment(self) -> RuntimeEnvironment:
        if not self.python_version.strip() or not self.device.strip() or not self.dtype.strip():
            raise ValueError("runtime environment identity is incomplete")
        if not self.runner_version.strip():
            raise ValueError("runner version is required")
        if len(self.runner_hash) != 64 or any(character not in HEX64 for character in self.runner_hash):
            raise ValueError("runner hash must be a lowercase SHA-256 digest")
        if self.runtime_lock_hash is not None and (
            len(self.runtime_lock_hash) != 64
            or any(character not in HEX64 for character in self.runtime_lock_hash)
        ):
            raise ValueError("runtime lock hash must be a lowercase SHA-256 digest")
        for field_name in (
            "installed_environment_sha256",
            "environment_fingerprint",
            "python_launcher_hash",
            "resolved_python_binary_hash",
            "pyvenv_cfg_hash",
            "python_executable_hash",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                len(value) != 64 or any(character not in HEX64 for character in value)
            ):
                raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
        cache = Path(self.cache_path).expanduser()
        if not cache.is_absolute():
            raise ValueError("runtime cache path must be absolute")
        if self.lock_artifact_path is not None and not Path(self.lock_artifact_path).expanduser().is_absolute():
            raise ValueError("runtime lock artifact path must be absolute")
        if self.sys_executable is not None and not Path(self.sys_executable).expanduser().is_absolute():
            raise ValueError("runtime sys.executable must be absolute")
        for field_name in ("sys_prefix", "sys_base_prefix"):
            value = getattr(self, field_name)
            if value is not None and not Path(value).expanduser().is_absolute():
                raise ValueError(f"runtime {field_name} must be absolute")
        return self

    def assert_transformers_baseline(self, *, requires_transformers: bool) -> None:
        if not requires_transformers:
            return
        if self.transformers_version != REQUIRED_TRANSFORMERS_VERSION:
            raise CompatibilityError(
                f"Transformers {REQUIRED_TRANSFORMERS_VERSION} is required; "
                f"observed {self.transformers_version!r}"
            )
        if self.huggingface_hub_version != REQUIRED_HUGGINGFACE_HUB_VERSION:
            raise CompatibilityError(
                f"huggingface-hub {REQUIRED_HUGGINGFACE_HUB_VERSION} is required; "
                f"observed {self.huggingface_hub_version!r}"
            )


class ResourceCeiling(BaseModel):
    """Laptop-safe upper bounds for one candidate process."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_rss_mib: float = Field(default=4096, gt=0)
    max_vram_mib: float = Field(default=6144, gt=0)
    max_residual_rss_mib: float = Field(default=256, ge=0)
    max_residual_vram_mib: float = Field(default=256, ge=0)
    gpu_models_at_once: int = Field(default=1, ge=1, le=1)


class RuntimeResourceResult(BaseModel):
    """Measured load/inference/unload resources and latency."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cold_load_ms: float = Field(ge=0)
    warm_inference_p50_ms: float = Field(ge=0)
    warm_inference_p95_ms: float = Field(ge=0)
    batch_inference_ms: float = Field(ge=0)
    batch_size: int = Field(ge=1)
    batch_throughput_per_second: float = Field(ge=0)
    rss_before_mib: float = Field(ge=0)
    rss_after_load_mib: float = Field(ge=0)
    rss_peak_mib: float = Field(ge=0)
    rss_after_unload_mib: float = Field(ge=0)
    vram_before_mib: float | None = Field(default=None, ge=0)
    vram_after_load_mib: float | None = Field(default=None, ge=0)
    vram_peak_mib: float | None = Field(default=None, ge=0)
    vram_after_unload_mib: float | None = Field(default=None, ge=0)
    unload_succeeded: bool
    memory_released: bool = True
    rss_residual_mib: float | None = Field(default=None, ge=0)
    vram_residual_mib: float | None = Field(default=None, ge=0)
    resource_limit_passed: bool

    @model_validator(mode="after")
    def validate_resources(self) -> RuntimeResourceResult:
        values = (
            self.cold_load_ms,
            self.warm_inference_p50_ms,
            self.warm_inference_p95_ms,
            self.batch_inference_ms,
            self.batch_throughput_per_second,
            self.rss_before_mib,
            self.rss_after_load_mib,
            self.rss_peak_mib,
            self.rss_after_unload_mib,
            self.vram_before_mib,
            self.vram_after_load_mib,
            self.vram_peak_mib,
            self.vram_after_unload_mib,
            self.rss_residual_mib,
            self.vram_residual_mib,
        )
        if any(value is not None and not math.isfinite(value) for value in values):
            raise ValueError("runtime resource values must be finite")
        if self.warm_inference_p95_ms < self.warm_inference_p50_ms:
            raise ValueError("warm p95 latency cannot be below p50")
        if self.rss_peak_mib < max(self.rss_before_mib, self.rss_after_load_mib):
            raise ValueError("RSS peak must include pre-load and post-load samples")
        if self.vram_peak_mib is not None and self.vram_after_load_mib is not None:
            if self.vram_peak_mib < max(self.vram_before_mib or 0, self.vram_after_load_mib):
                raise ValueError("VRAM peak must include pre-load and post-load samples")
        return self

    def enforce(self, ceiling: ResourceCeiling) -> None:
        if self.rss_peak_mib > ceiling.max_rss_mib:
            raise ResourceLimitError("RSS ceiling exceeded")
        if self.vram_peak_mib is not None and self.vram_peak_mib > ceiling.max_vram_mib:
            raise ResourceLimitError("VRAM ceiling exceeded")


class CandidateSpec(BaseModel):
    """A no-fallback model-family admission candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    family: ModelFamily
    task: ModelTask
    external_checkpoint: CheckpointPin | None = None
    runtime_pin: RuntimePin | None = None
    requires_transformers: bool = False
    gpu: bool = False
    output_schema: str
    repeatability_policy: RepeatabilityPolicy = RepeatabilityPolicy.DETERMINISTIC_REQUIRED
    repeatability_seed: int | None = None
    notes: str = ""

    @model_validator(mode="after")
    def validate_candidate(self) -> CandidateSpec:
        if not self.name.strip() or not self.output_schema.strip():
            raise ValueError("runtime candidates require names and output schemas")
        if self.family == ModelFamily.TSPULSE and self.task == ModelTask.FORECAST:
            raise ValueError("TSPulse is a feature/anomaly/regime model, never a price forecaster")
        if self.family == ModelFamily.TSPULSE and self.task != ModelTask.TSPULSE_FEATURES:
            raise ValueError("TSPulse is admitted only for feature/anomaly/regime tasks")
        if self.external_checkpoint is not None:
            if self.external_checkpoint.model_family != self.family.value:
                raise ValueError("checkpoint family must exactly match the candidate family")
            if self.runtime_pin is None:
                raise ValueError("external candidates require a separate execution runtime pin")
        if self.gpu and self.task == ModelTask.FINANCE_SENTIMENT:
            raise ValueError("FinBERT qualification is CPU-only")
        if (
            self.repeatability_policy == RepeatabilityPolicy.SEEDED_REPRODUCIBLE
            and self.repeatability_seed is None
        ):
            raise ValueError("seeded repeatability requires an explicit seed")
        return self


class BenchmarkDataset(BaseModel):
    """Versioned, point-in-time-safe benchmark input interface."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: str
    version: str
    task: ModelTask
    source: str
    snapshot_id: str
    training_cutoff: datetime
    inputs: tuple[float, ...] = ()
    targets: tuple[float, ...] = ()
    public_text_fixture: tuple[tuple[str, str], ...] = ()
    content_hash: str

    @model_validator(mode="after")
    def validate_dataset(self) -> BenchmarkDataset:
        if not self.dataset_id.strip() or not self.version.strip() or not self.snapshot_id.strip():
            raise ValueError("benchmark dataset identity is required")
        if not self.source.strip():
            raise ValueError("benchmark dataset source is required")
        if self.training_cutoff.tzinfo is None or self.training_cutoff.utcoffset() is None:
            raise ValueError("benchmark cutoff must include a timezone")
        if len(self.inputs) != len(self.targets) and self.task == ModelTask.FORECAST:
            raise ValueError("forecast benchmark inputs and targets must have equal length")
        if self.task == ModelTask.FINANCE_SENTIMENT and not self.public_text_fixture:
            raise ValueError("FinBERT benchmark requires a fixed public text fixture")
        if len(self.content_hash) != 64 or any(character not in HEX64 for character in self.content_hash):
            raise ValueError("benchmark content hash must be SHA-256")
        if any(not math.isfinite(value) for value in (*self.inputs, *self.targets)):
            raise ValueError("benchmark numeric values must be finite")
        return self

    @classmethod
    def synthetic_forecast(cls) -> BenchmarkDataset:
        inputs = tuple(100 + 0.7 * index + (index % 4) * 0.2 for index in range(48))
        targets = tuple(inputs[index] + 0.7 + ((index + 1) % 3) * 0.1 for index in range(48))
        payload = json.dumps({"inputs": inputs, "targets": targets}, separators=(",", ":"))
        return cls(
            dataset_id="advisorai-phase0-synthetic-forecast",
            version="1.0.0",
            task=ModelTask.FORECAST,
            source="synthetic://advisorai/phase0/forecast-v1",
            snapshot_id="synthetic-phase0-forecast-v1",
            training_cutoff=datetime(2026, 1, 1, tzinfo=UTC),
            inputs=inputs,
            targets=targets,
            content_hash=sha256(payload.encode()).hexdigest(),
        )

    @classmethod
    def finbert_fixture(cls) -> BenchmarkDataset:
        fixture = (
            ("The company beat quarterly earnings expectations and raised guidance.", "positive"),
            ("The issuer reported a fraud investigation and a sharp loss.", "negative"),
            ("The board announced a routine shareholder meeting next month.", "neutral"),
            ("Revenue growth accelerated after the product approval.", "positive"),
            ("The downgrade followed weak demand and declining margins.", "negative"),
        )
        payload = json.dumps(fixture, separators=(",", ":"))
        return cls(
            dataset_id="advisorai-phase0-finbert-public-fixture",
            version="1.0.0",
            task=ModelTask.FINANCE_SENTIMENT,
            source="fixture://public-finance-phrases-v1",
            snapshot_id="public-finance-phrases-v1",
            training_cutoff=datetime(2026, 1, 1, tzinfo=UTC),
            public_text_fixture=fixture,
            content_hash=sha256(payload.encode()).hexdigest(),
        )

    @classmethod
    def synthetic_features(cls) -> BenchmarkDataset:
        inputs = tuple(100 + (index % 5) * 0.25 for index in range(32))
        payload = json.dumps(inputs, separators=(",", ":"))
        return cls(
            dataset_id="advisorai-phase0-synthetic-integrity-features",
            version="1.0.0",
            task=ModelTask.TSPULSE_FEATURES,
            source="synthetic://advisorai/phase0/integrity-features-v1",
            snapshot_id="synthetic-phase0-integrity-v1",
            training_cutoff=datetime(2026, 1, 1, tzinfo=UTC),
            inputs=inputs,
            content_hash=sha256(payload.encode()).hexdigest(),
        )


class RuntimeQualificationResult(BaseModel):
    """Sanitized qualification evidence and its Phase-0 bake-off projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: CandidateSpec
    status: QualificationStatus
    environment: RuntimeEnvironment
    dataset_id: str
    dataset_version: str
    measured_at: datetime
    observed_artifacts: tuple[ArtifactPin, ...] = ()
    resource: RuntimeResourceResult | None = None
    output_shape: str | None = None
    output_schema_valid: bool = False
    nan_inf_rejection_passed: bool = False
    repeated_outputs_equal: bool = False
    deterministic_match_rate: float = Field(default=0, ge=0, le=1)
    seeded_repeatability_match_rate: float = Field(default=0, ge=0, le=1)
    repeatability_seed: int | None = None
    stochastic_characterized: bool = False
    stochastic_repeat_count: int = Field(default=0, ge=0)
    stochastic_unique_output_count: int = Field(default=0, ge=0)
    stochastic_deterministic_match_rate: float = Field(default=0, ge=0, le=1)
    stochastic_seeds: tuple[int, ...] = ()
    applied_seeds: tuple[int, ...] = ()
    stochastic_dispersion: float | None = Field(default=None, ge=0)
    stochastic_variation_observed: bool = False
    offline_cached_inference: bool = False
    one_inference_completed: bool = False
    batch_completed: bool = False
    network_access_attempted: bool = False
    finbert_accuracy: float | None = Field(default=None, ge=0, le=1)
    finbert_per_label_accuracy: tuple[tuple[str, float], ...] = ()
    finbert_mean_confidence: float | None = Field(default=None, ge=0, le=1)
    missing_checkpoint_quarantined: bool = False
    corrupt_checkpoint_quarantined: bool = False
    failure_reason: str | None = None
    warnings: tuple[str, ...] = ()
    forecast_evaluations: tuple[ForecastEvaluation, ...] = ()
    manifest_hash: str | None = None

    @model_validator(mode="after")
    def validate_result(self) -> RuntimeQualificationResult:
        if self.measured_at.tzinfo is None or self.measured_at.utcoffset() is None:
            raise ValueError("qualification timestamp must include a timezone")
        if self.status == QualificationStatus.MEASURED:
            if self.failure_reason is not None:
                raise ValueError("measured qualification cannot carry a failure reason")
            if (
                not self.one_inference_completed
                or not self.output_schema_valid
                or not self.nan_inf_rejection_passed
                or not self.offline_cached_inference
                or not self.batch_completed
                or self.resource is None
                or not self.resource.unload_succeeded
                or not self.resource.memory_released
                or not self.resource.resource_limit_passed
            ):
                raise ValueError(
                    "measured qualification requires complete offline, repeatability, "
                    "resource, and output evidence"
                )
            if self.candidate.external_checkpoint is not None and not self.observed_artifacts:
                raise ValueError("measured external qualification requires observed artifact hashes")
            if self.network_access_attempted:
                raise ValueError("measured qualification cannot include a network attempt")
            policy = self.candidate.repeatability_policy
            if policy == RepeatabilityPolicy.DETERMINISTIC_REQUIRED and not self.repeated_outputs_equal:
                raise ValueError("deterministic candidate outputs must repeat exactly")
            if policy == RepeatabilityPolicy.SEEDED_REPRODUCIBLE and self.seeded_repeatability_match_rate < 1:
                raise ValueError("seeded candidate must reproduce its declared seed")
            if policy == RepeatabilityPolicy.SEEDED_REPRODUCIBLE and self.repeatability_seed != self.candidate.repeatability_seed:
                raise ValueError("seeded repeatability evidence must record the candidate seed")
            if policy == RepeatabilityPolicy.STOCHASTIC_CHARACTERIZED and not self.stochastic_characterized:
                raise ValueError("stochastic candidate requires characterization evidence")
            if policy == RepeatabilityPolicy.STOCHASTIC_CHARACTERIZED:
                if self.stochastic_repeat_count < 2:
                    raise ValueError("stochastic candidate requires repeated characterization")
                if self.stochastic_unique_output_count < 2 or not self.stochastic_variation_observed:
                    raise ValueError("stochastic candidate requires observed output variation")
                if self.stochastic_dispersion is None or not math.isfinite(self.stochastic_dispersion):
                    raise ValueError("stochastic candidate requires finite dispersion evidence")
        if self.status != QualificationStatus.MEASURED and not self.failure_reason:
            raise ValueError("quarantined/failed qualification requires a sanitized reason")
        if any(
            not label.strip() or not 0 <= accuracy <= 1
            for label, accuracy in self.finbert_per_label_accuracy
        ):
            raise ValueError("FinBERT per-label accuracy must contain valid labels and values")
        if self.manifest_hash is not None and (
            len(self.manifest_hash) != 64
            or any(character not in HEX64 for character in self.manifest_hash)
        ):
            raise ValueError("manifest hash must be SHA-256")
        return self

    def to_bakeoff_result(self) -> BakeoffResult:
        """Project this record into the existing Phase-0 contract."""

        kind = {
            ModelTask.FINANCE_SENTIMENT: ComponentKind.FINANCE_NLP,
            ModelTask.TSPULSE_FEATURES: ComponentKind.FEATURE_COMPUTE,
            ModelTask.FORECAST: ComponentKind.FORECAST_MODEL,
        }[self.candidate.task]
        status = "measured" if self.status == QualificationStatus.MEASURED else self.status.value
        checkpoint = self.candidate.external_checkpoint
        route = (
            f"local/{checkpoint.repository_id}@{checkpoint.revision}"
            if checkpoint is not None
            else f"builtin/{self.candidate.family.value}"
        )
        notes = list(self.warnings)
        if self.failure_reason:
            notes.append(self.failure_reason)
        return BakeoffResult(
            candidate_name=self.candidate.name,
            kind=kind,
            status=status,
            version=self.environment.runner_version,
            route_identity=route,
            privacy_passed=(
                True
                if self.status == QualificationStatus.MEASURED
                and self.offline_cached_inference
                and not self.network_access_attempted
                else False
                if self.network_access_attempted
                else None
            ),
            failure_handling_passed=(
                True if self.status == QualificationStatus.MEASURED else None
            ),
            resource_samples=(),
            benchmark_hash=self.manifest_hash,
            notes=tuple(notes),
        )


class RuntimeRunner(Protocol):
    """Injected runner contract; it must never select a different family."""

    version: str
    model_family: str

    def load(self, *, checkpoint: CheckpointPin | None, offline: bool) -> object: ...

    def infer(self, model: object, payload: object) -> object: ...

    def unload(self, model: object) -> None: ...

    def set_seed(self, seed: int) -> None: ...


class FunctionalRunner:
    """Small test/smoke runner with explicit family identity."""

    def __init__(
        self,
        *,
        model_family: str,
        load_fn: Callable[[CheckpointPin | None, bool], object] | None = None,
        infer_fn: Callable[[object, object], object],
        unload_fn: Callable[[object], None] | None = None,
        seed_fn: Callable[[int], None] | None = None,
        version: str = "functional-runner-v1",
    ) -> None:
        self.model_family = model_family
        self.version = version
        self._load_fn = load_fn or (lambda checkpoint, offline: object())
        self._infer_fn = infer_fn
        self._unload_fn = unload_fn or (lambda model: None)
        self._seed_fn = seed_fn

    def load(self, *, checkpoint: CheckpointPin | None, offline: bool) -> object:
        return self._load_fn(checkpoint, offline)

    def infer(self, model: object, payload: object) -> object:
        return self._infer_fn(model, payload)

    def unload(self, model: object) -> None:
        self._unload_fn(model)

    def set_seed(self, seed: int) -> None:
        if self._seed_fn is None:
            raise QualificationError("runner does not expose seeded repeatability")
        self._seed_fn(seed)


class LightGBMBaseline:
    """Small deterministic LightGBM baseline; missing LightGBM is quarantine-worthy."""

    name = "lightgbm"

    def predict(self, values: Sequence[Any], horizon: int = 1) -> tuple[Any, ...]:
        if len(values) < 4 or horizon < 1:
            raise ValueError("LightGBM baseline requires four observations and a positive horizon")
        try:
            import lightgbm as lgb
            import numpy as np
        except ImportError as exc:
            raise QualificationError("LightGBM dependency is unavailable") from exc
        from decimal import Decimal

        features = np.asarray(tuple((float(index),) for index in range(len(values))), dtype=float)
        targets = [float(value) for value in values]
        training = lgb.Dataset(features, label=np.asarray(targets, dtype=float), free_raw_data=False)
        model = lgb.train(
            {
                "objective": "regression",
                "learning_rate": 0.05,
                "num_leaves": 7,
                "min_data_in_leaf": 2,
                "seed": 0,
                "deterministic": True,
                "force_col_wise": True,
                "verbosity": -1,
            },
            training,
            num_boost_round=16,
        )
        future = np.asarray(
            tuple((float(len(values) + index),) for index in range(horizon)), dtype=float
        )
        return tuple(Decimal(str(value)) for value in model.predict(future))


class _ResourceSampler:
    def __init__(self) -> None:
        self.process = psutil.Process()

    def rss_mib(self) -> float:
        return self.process.memory_info().rss / (1024**2)

    def vram_mib(self) -> float | None:
        try:
            import torch
        except ImportError:
            return None
        if not torch.cuda.is_available():
            return None
        return float(torch.cuda.memory_allocated() / (1024**2))

    def peak_vram_mib(self) -> float | None:
        try:
            import torch
        except ImportError:
            return None
        if not torch.cuda.is_available():
            return None
        return float(torch.cuda.max_memory_allocated() / (1024**2))

    def reset_peak(self) -> None:
        try:
            import torch
        except ImportError:
            return
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()


class _ResourceMonitor:
    """Background sampler that captures transient process and VRAM peaks."""

    def __init__(self, sampler: _ResourceSampler, interval_seconds: float = 0.01) -> None:
        self.sampler = sampler
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak_rss = sampler.rss_mib()
        self.peak_vram = sampler.vram_mib()

    def _sample(self) -> None:
        rss, vram = _sample(self.sampler)
        self.peak_rss = max(self.peak_rss, rss)
        if vram is not None:
            self.peak_vram = max(self.peak_vram or 0, vram)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._sample()

    def start(self) -> None:
        self._sample()
        self._thread = threading.Thread(target=self._run, name="advisorai-runtime-resource-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._sample()


class RuntimeWorkerIdentity(BaseModel):
    """Sanitized identity returned by an external runtime worker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sys_executable: str
    model_family: str
    sys_prefix: str
    sys_base_prefix: str
    python_launcher_hash: str
    python_launcher_target: str | None = None
    resolved_python_binary_hash: str
    pyvenv_cfg_hash: str | None = None
    python_version: str
    package_versions: tuple[tuple[str, str | None], ...] = ()
    torch_version: str | None = None
    cuda_version: str | None = None
    runtime_lock_hash: str
    lock_artifact_hash: str
    python_executable_hash: str
    installed_environment_sha256: str
    environment_fingerprint: str
    runner_version: str
    runner_hash: str

    @model_validator(mode="after")
    def validate_identity(self) -> RuntimeWorkerIdentity:
        if not self.model_family.strip() or not self.runner_version.strip():
            raise ValueError("worker model and runner identity are incomplete")
        if not Path(self.sys_executable).expanduser().is_absolute():
            raise ValueError("worker sys.executable must be absolute")
        if not Path(self.sys_prefix).expanduser().is_absolute():
            raise ValueError("worker sys.prefix must be absolute")
        if not Path(self.sys_base_prefix).expanduser().is_absolute():
            raise ValueError("worker sys.base_prefix must be absolute")
        for field_name in (
            "runtime_lock_hash",
            "lock_artifact_hash",
            "python_executable_hash",
            "python_launcher_hash",
            "resolved_python_binary_hash",
            "installed_environment_sha256",
            "environment_fingerprint",
            "runner_hash",
        ):
            value = getattr(self, field_name)
            if len(value) != 64 or any(character not in HEX64 for character in value):
                raise ValueError(f"worker {field_name} must be a SHA-256 digest")
        return self


class RuntimeWorkerResponse(BaseModel):
    """Credential-free JSON response exchanged with an isolated worker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: int = 1
    identity: RuntimeWorkerIdentity
    outputs: tuple[object, ...] = ()
    batch_output: object | None = None
    cold_load_ms: float = Field(default=0, ge=0)
    warm_durations_ms: tuple[float, ...] = ()
    batch_inference_ms: float = Field(default=0, ge=0)
    batch_size: int = Field(default=1, ge=1)
    rss_after_load_mib: float = Field(default=0, ge=0)
    rss_after_unload_mib: float = Field(default=0, ge=0)
    vram_after_load_mib: float | None = Field(default=None, ge=0)
    vram_peak_mib: float | None = Field(default=None, ge=0)
    vram_after_unload_mib: float | None = Field(default=None, ge=0)
    unload_succeeded: bool = False
    offline_cached_inference: bool = False
    network_access_attempted: bool = False
    error_class: str | None = None
    stochastic_repeat_count: int = Field(default=0, ge=0)
    stochastic_unique_output_count: int = Field(default=0, ge=0)
    stochastic_deterministic_match_rate: float = Field(default=0, ge=0, le=1)
    stochastic_seeds: tuple[int, ...] = ()
    applied_seeds: tuple[int, ...] = ()
    stochastic_dispersion: float | None = Field(default=None, ge=0)
    stochastic_variation_observed: bool = False

    @model_validator(mode="after")
    def validate_worker_response(self) -> RuntimeWorkerResponse:
        if self.protocol_version != 1:
            raise ValueError("unsupported runtime worker protocol")
        if self.warm_durations_ms and any(value < 0 or not math.isfinite(value) for value in self.warm_durations_ms):
            raise ValueError("worker warm timings must be finite and non-negative")
        if self.error_class and self.outputs:
            raise ValueError("failed worker response cannot include model outputs")
        return self


class _ProcessTreeMonitor:
    """Sample an isolated worker and its descendants, not the parent process."""

    def __init__(self, pid: int, interval_seconds: float = 0.01) -> None:
        self.pid = pid
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.rss_before_mib = 0.0
        self.rss_peak_mib = 0.0
        self.samples = 0

    def _processes(self) -> tuple[psutil.Process, ...]:
        try:
            root = psutil.Process(self.pid)
            return (root, *root.children(recursive=True))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return ()

    def _sample(self) -> None:
        total = 0.0
        for process in self._processes():
            try:
                total += process.memory_info().rss / (1024**2)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if self.samples == 0:
            self.rss_before_mib = total
        self.rss_peak_mib = max(self.rss_peak_mib, total)
        self.samples += 1

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._sample()

    def start(self) -> None:
        self._sample()
        self._thread = threading.Thread(
            target=self._run,
            name="advisorai-runtime-worker-resource-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._sample()


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    try:
        root = psutil.Process(process.pid)
        children = root.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        children = []
    for child in reversed(children):
        try:
            child.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    try:
        process.kill()
    except ProcessLookupError:
        pass


@contextmanager
def network_blocked() -> Iterator[None]:
    """Fail closed if a cached qualification tries to access the network."""

    original_socket = socket.socket
    original_create_connection = socket.create_connection

    def blocked_socket(*args: object, **kwargs: object) -> object:
        raise NetworkAccessAttemptError("network access is disabled during offline qualification")

    def blocked_connection(*args: object, **kwargs: object) -> object:
        raise NetworkAccessAttemptError("network access is disabled during offline qualification")

    socket.socket = cast(Any, blocked_socket)
    socket.create_connection = cast(Any, blocked_connection)
    try:
        yield
    finally:
        socket.socket = original_socket
        socket.create_connection = original_create_connection


def sha256_file(path: Path) -> str:
    """Hash a file in bounded chunks without loading model weights into RAM."""

    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _launcher_identity_hash(path: Path) -> str:
    """Hash launcher bytes or the symlink target string without following it."""

    if path.is_symlink():
        return sha256(os.readlink(path).encode()).hexdigest()
    return sha256_file(path)


def _installed_environment_inventory() -> tuple[str, ...]:
    """Canonical complete distribution inventory for an interpreter."""

    entries = {
        f"{distribution.metadata['Name'] or distribution.name}=={distribution.version}"
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name") or distribution.name
    }
    return tuple(sorted(entries, key=str.casefold))


def _installed_environment_bytes(entries: Sequence[str]) -> bytes:
    return json.dumps(tuple(entries), separators=(",", ":"), ensure_ascii=True).encode()


def installed_environment_sha256(entries: Sequence[str] | None = None) -> str:
    """Hash the complete normalized installed-distribution inventory."""

    return sha256(_installed_environment_bytes(entries or _installed_environment_inventory())).hexdigest()


def _python_constraint_satisfied(constraint: str, version: str) -> bool:
    """Evaluate the simple comma-separated Python constraints used by pins."""

    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    try:
        return Version(version) in SpecifierSet(constraint)
    except Exception as exc:  # pragma: no cover - malformed pins are rejected at admission
        raise QualificationError("invalid runtime Python constraint") from exc


def _repository_root(cache_root: Path, repository: RepositoryPin) -> Path:
    preferred = cache_root / repository.cache_subdir
    return preferred if preferred.is_dir() else cache_root


def _assert_runtime_cache_closure(repo_root: Path, repository: RepositoryPin) -> None:
    """Require every cached regular file to be part of the reviewed closure.

    The previous suffix-based check protected executable/loadable formats but
    allowed an unpinned ``config.json`` or tokenizer/processor file to alter
    model behavior.  Configuration and provenance are part of the immutable
    closure too, so the comparison is deliberately extension-agnostic.
    """

    expected = {artifact.relative_path for artifact in repository.all_artifacts}
    for path in repo_root.rglob("*"):
        if path.is_symlink():
            raise CheckpointIntegrityError(f"symlink in model cache is not allowed: {path.name}")
        if not path.is_file():
            continue
        relative = str(path.relative_to(repo_root))
        if relative not in expected:
            raise CheckpointIntegrityError(
                f"unexpected cached artifact is outside the reviewed runtime closure: {relative}"
            )


def verify_checkpoint_artifacts(
    checkpoint: CheckpointPin,
    *,
    cache_root: Path | None = None,
    repository_root: Path | None = None,
) -> tuple[ArtifactPin, ...]:
    """Verify every pinned file and return the observed digest inventory.

    A pin with no expected digest is intentionally rejected.  This prevents a
    downloaded file from being treated as immutable evidence merely because it
    has the expected filename.
    """

    if repository_root is not None:
        checkpoint.assert_cache_outside_repository(repository_root)
    if checkpoint.tokenizer is not None and (
        checkpoint.tokenizer_license_admission is None
        or checkpoint.tokenizer_license_admission.status != LicenseAdmissionStatus.APPROVED
    ):
        raise CheckpointPinError("tokenizer license admission is not approved")
    root = (cache_root or Path(checkpoint.cache_path).expanduser()).resolve(strict=False)
    repositories = (checkpoint.repository,) + ((checkpoint.tokenizer,) if checkpoint.tokenizer else ())
    observed: list[ArtifactPin] = []
    for repository in repositories:
        repo_root = _repository_root(root, repository)
        if repo_root.is_dir():
            _assert_runtime_cache_closure(repo_root, repository)
        required_artifacts = repository.runtime_artifacts or repository.artifacts
        optional_provenance = repository.provenance_artifacts
        for artifact in (*required_artifacts, *optional_provenance):
            if artifact.sha256 is None:
                if artifact in optional_provenance and not (repo_root / artifact.relative_path).exists():
                    continue
                raise CheckpointPinError(
                    f"artifact {repository.repository_id}/{artifact.relative_path} has no pinned hash"
                )
            path = repo_root / artifact.relative_path
            if not path.is_file() or path.is_symlink():
                raise CheckpointNotCachedError(
                    f"pinned artifact is not cached: {repository.repository_id}/{artifact.relative_path}"
                )
            if artifact.size_bytes is not None and path.stat().st_size != artifact.size_bytes:
                raise CheckpointIntegrityError(f"artifact size mismatch: {artifact.relative_path}")
            actual = sha256_file(path)
            if actual != artifact.sha256:
                raise CheckpointIntegrityError(f"artifact hash mismatch: {artifact.relative_path}")
            observed.append(ArtifactPin(relative_path=artifact.relative_path, sha256=actual, size_bytes=path.stat().st_size))
    return tuple(observed)


def cached_artifact_inventory(
    checkpoint: CheckpointPin,
    *,
    cache_root: Path | None = None,
    repository_root: Path | None = None,
) -> tuple[ArtifactPin, ...]:
    """Hash every regular file currently present in a pinned external cache."""

    root = (cache_root or Path(checkpoint.cache_path).expanduser()).resolve(strict=False)
    if repository_root is not None:
        checkpoint.assert_cache_outside_repository(repository_root)
    if checkpoint.tokenizer is not None and (
        checkpoint.tokenizer_license_admission is None
        or checkpoint.tokenizer_license_admission.status != LicenseAdmissionStatus.APPROVED
    ):
        raise CheckpointPinError("tokenizer license admission is not approved")
    repositories = (checkpoint.repository,) + ((checkpoint.tokenizer,) if checkpoint.tokenizer else ())
    inventory: list[ArtifactPin] = []
    for repository in repositories:
        repo_root = _repository_root(root, repository)
        if not repo_root.is_dir():
            raise CheckpointNotCachedError(f"checkpoint cache is absent: {repo_root}")
        _assert_runtime_cache_closure(repo_root, repository)
        for path in sorted(repo_root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                inventory.append(
                    ArtifactPin(
                        relative_path=str(path.relative_to(repo_root)),
                        sha256=sha256_file(path),
                        size_bytes=path.stat().st_size,
                    )
                )
    if not inventory:
        raise CheckpointNotCachedError("checkpoint cache contains no regular artifacts")
    return tuple(inventory)


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _environment_fingerprint(
    *,
    sys_executable: str,
    python_version: str,
    package_versions: Mapping[str, str | None],
    torch_version: str | None,
    cuda_version: str | None,
    runtime_lock_hash: str,
    installed_environment_sha256: str,
    sys_prefix: str,
    sys_base_prefix: str,
) -> str:
    """Create the stable identity reported by an isolated worker."""

    material = {
        "sys_executable": str(Path(sys_executable).expanduser().resolve(strict=False)),
        "python_version": python_version,
        "package_versions": {
            str(name): value
            for name, value in sorted(package_versions.items(), key=lambda item: str(item[0]))
        },
        "torch_version": torch_version,
        "cuda_version": cuda_version,
        "runtime_lock_hash": runtime_lock_hash,
        "installed_environment_sha256": installed_environment_sha256,
        "sys_prefix": str(Path(sys_prefix).expanduser().resolve(strict=False)),
        "sys_base_prefix": str(Path(sys_base_prefix).expanduser().resolve(strict=False)),
    }
    payload = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


def _worker_runner_hash(worker_script: Path, runner_version: str) -> str:
    return sha256(f"{runner_version}\n{sha256_file(worker_script)}".encode()).hexdigest()


def verify_runtime_pin(pin: RuntimePin, *, repository_root: Path | None = None) -> Path:
    """Verify the immutable lock/interpreter/worker identity before dispatch."""

    if pin.status != RuntimeAdmissionStatus.APPROVED:
        raise QualificationError("execution runtime admission is not approved")
    effective_root = repository_root or Path.cwd()
    pin.assert_environment_outside_repository(effective_root)
    if not pin.lock_hash or not pin.lock_artifact_path:
        raise QualificationError("approved runtime is missing an immutable lock artifact")
    lock_path_raw = Path(pin.lock_artifact_path).expanduser()
    if lock_path_raw.is_symlink():
        raise QualificationError("pinned runtime lock artifact must not be a symlink")
    lock_path = lock_path_raw.resolve(strict=False)
    root = effective_root.expanduser().resolve(strict=False)
    try:
        lock_path.relative_to(root)
    except ValueError:
        pass
    else:
        raise QualificationError("runtime lock artifact must be outside the repository")
    if not lock_path.is_file():
        raise QualificationError("pinned runtime lock artifact is missing")
    if sha256_file(lock_path) != pin.lock_hash:
        raise QualificationError("pinned runtime lock artifact hash mismatch")
    launcher = pin.resolve_python_launcher()
    expected_launcher_hash = pin.python_launcher_hash or pin.python_executable_hash
    if not expected_launcher_hash or _launcher_identity_hash(launcher) != expected_launcher_hash:
        raise QualificationError("pinned runtime Python launcher hash mismatch")
    binary = pin.resolve_python_binary()
    expected_binary_hash = pin.resolved_python_binary_hash or pin.python_executable_hash
    if not expected_binary_hash or sha256_file(binary) != expected_binary_hash:
        raise QualificationError("pinned runtime resolved Python binary hash mismatch")
    if pin.python_executable_hash and sha256_file(binary) != pin.python_executable_hash:
        raise QualificationError("pinned runtime executable hash mismatch")
    launcher_target = str(launcher.resolve(strict=False)) if launcher.is_symlink() else None
    if pin.python_launcher_target != launcher_target:
        raise QualificationError("pinned runtime launcher target mismatch")
    cfg_path = Path(pin.pyvenv_cfg_path).expanduser() if pin.pyvenv_cfg_path else Path(pin.environment_path).expanduser() / "pyvenv.cfg"
    try:
        cfg_path.resolve(strict=False).relative_to(Path(pin.environment_path).expanduser().resolve(strict=False))
    except ValueError as exc:
        raise QualificationError("pyvenv.cfg must be inside the pinned environment") from exc
    if cfg_path.exists():
        if cfg_path.is_symlink() or not pin.pyvenv_cfg_hash or sha256_file(cfg_path) != pin.pyvenv_cfg_hash:
            raise QualificationError("pinned pyvenv.cfg hash mismatch")
    elif pin.pyvenv_cfg_hash:
        raise QualificationError("pinned pyvenv.cfg is missing")
    if not pin.installed_environment_manifest_path or not pin.installed_environment_sha256:
        raise QualificationError("approved runtime is missing installed-environment attestation")
    manifest_path = Path(pin.installed_environment_manifest_path).expanduser()
    try:
        manifest_path.resolve(strict=False).relative_to(root)
    except ValueError:
        pass
    else:
        raise QualificationError("installed-environment manifest must be outside the repository")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise QualificationError("installed-environment manifest is missing")
    if sha256_file(manifest_path) != pin.installed_environment_sha256:
        raise QualificationError("installed-environment manifest hash mismatch")
    if not pin.worker_script:
        raise QualificationError("approved runtime worker script is missing")
    worker_script = Path(pin.worker_script).expanduser().resolve(strict=False)
    if worker_script.is_symlink() or not worker_script.is_file():
        raise QualificationError("pinned runtime worker script is missing")
    if not pin.runner_version or not pin.runner_hash:
        raise QualificationError("approved runtime runner identity is incomplete")
    if _worker_runner_hash(worker_script, pin.runner_version) != pin.runner_hash:
        raise QualificationError("pinned runtime worker/runner hash mismatch")
    if not pin.environment_fingerprint:
        raise QualificationError("approved runtime environment fingerprint is missing")
    return launcher


def runtime_environment(
    *,
    cache_path: str,
    device: str = "cpu",
    dtype: str = "float32",
    quantization: str = "none",
    runner_version: str = "qualification-runner-v1",
    runner_hash: str | None = None,
    runtime_lock_hash: str | None = None,
) -> RuntimeEnvironment:
    """Capture the dependency/hardware identity without importing model code."""

    version_material = runner_hash or f"{__name__}:{runner_version}"
    cuda_version: str | None = None
    try:
        import torch

        cuda_version = torch.version.cuda
    except ImportError:
        pass
    return RuntimeEnvironment(
        python_version=platform.python_version(),
        transformers_version=_distribution_version("transformers"),
        huggingface_hub_version=_distribution_version("huggingface-hub"),
        torch_version=_distribution_version("torch"),
        cuda_version=cuda_version,
        device=device,
        dtype=dtype,
        quantization=quantization,
        cache_path=str(Path(cache_path).expanduser()),
        runner_version=runner_version,
        runner_hash=sha256(version_material.encode()).hexdigest(),
        runtime_lock_hash=runtime_lock_hash,
    )


def _canonical(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if hasattr(value, "item") and callable(value.item):
        try:
            return _canonical(value.item())
        except Exception:  # pragma: no cover - defensive conversion for third-party tensors
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def output_digest(output: object) -> str:
    payload = json.dumps(_canonical(output), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(payload.encode()).hexdigest()


def _numeric_values(value: object) -> tuple[float, ...]:
    """Flatten finite numeric output for small stochastic dispersion evidence."""

    if isinstance(value, bool):
        return ()
    if isinstance(value, Mapping):
        values: list[float] = []
        for item in value.values():
            values.extend(_numeric_values(item))
        return tuple(values)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        values = []
        for item in value:
            values.extend(_numeric_values(item))
        return tuple(values)
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return ()
    return (number,) if math.isfinite(number) else ()


def _stochastic_evidence(
    outputs: Sequence[object],
    *,
    seed: int | None = None,
) -> dict[str, object]:
    digests = [output_digest(output) for output in outputs]
    flattened = [number for output in outputs for number in _numeric_values(output)]
    dispersion = statistics.pstdev(flattened) if len(flattened) >= 2 else 0.0
    return {
        "stochastic_repeat_count": len(outputs),
        "stochastic_unique_output_count": len(set(digests)),
        "stochastic_deterministic_match_rate": sum(digest == digests[0] for digest in digests) / len(digests),
        "stochastic_seeds": (seed,) if seed is not None else (),
        "stochastic_dispersion": dispersion,
        "stochastic_variation_observed": len(set(digests)) > 1 and dispersion > 0,
    }


def _finite_number(value: object) -> float:
    if isinstance(value, bool):
        raise InvalidModelOutputError("boolean is not a numeric model output")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as exc:
        raise InvalidModelOutputError("model output contains a non-numeric value") from exc
    if not math.isfinite(number):
        raise InvalidModelOutputError("model output contains NaN or infinity")
    return number


def validate_model_output(
    task: ModelTask,
    output: object,
    *,
    expected_length: int | None = None,
) -> str:
    """Validate and return a stable output-shape label."""

    if task == ModelTask.FINANCE_SENTIMENT:
        if not isinstance(output, Mapping):
            raise InvalidModelOutputError("FinBERT output must be a mapping")
        label = str(output.get("label", ""))
        if label not in {"positive", "negative", "neutral"}:
            raise InvalidModelOutputError("FinBERT label is outside the reviewed label set")
        confidence = _finite_number(output.get("confidence", output.get("score")))
        if not 0 <= confidence <= 1:
            raise InvalidModelOutputError("FinBERT confidence must be between zero and one")
        return "sentiment(label,confidence)"
    if task == ModelTask.TSPULSE_FEATURES:
        if isinstance(output, (str, bytes, Mapping)) or not isinstance(output, Sequence):
            raise InvalidModelOutputError("TSPulse feature output must be a numeric sequence")
        values = tuple(_finite_number(item) for item in output)
        if not values:
            raise InvalidModelOutputError("TSPulse feature output cannot be empty")
        return f"features[{len(values)}]"
    if isinstance(output, (str, bytes, Mapping)) or not isinstance(output, Sequence):
        raise InvalidModelOutputError("forecast output must be a numeric sequence")
    values = tuple(_finite_number(item) for item in output)
    if not values:
        raise InvalidModelOutputError("forecast output cannot be empty")
    if expected_length is not None and len(values) != expected_length:
        raise InvalidModelOutputError("forecast output length does not match the requested horizon")
    return f"forecast[{len(values)}]"


def validate_model_batch_output(
    task: ModelTask,
    output: object,
    *,
    expected_batch_size: int,
    expected_forecast_horizon: int | None = None,
) -> str:
    """Validate a batch envelope and every task-specific singleton result."""

    if expected_batch_size < 1:
        raise ValueError("expected batch size must be positive")
    if isinstance(output, (str, bytes, Mapping)) or not isinstance(output, Sequence):
        raise InvalidModelOutputError("model batch output must be a sequence")
    if len(output) != expected_batch_size:
        raise InvalidModelOutputError("model batch output cardinality does not match the input batch")
    shapes = [
        validate_model_output(task, item, expected_length=expected_forecast_horizon)
        for item in output
    ]
    return f"batch[{expected_batch_size}]<{shapes[0]}>"


def _forecast_horizon_from_schema(output_schema: str) -> int | None:
    prefix, separator, suffix = output_schema.partition("forecast[")
    if prefix or not separator or not suffix.endswith("]"):
        return None
    try:
        horizon = int(suffix[:-1])
    except ValueError:
        return None
    return horizon if horizon > 0 else None


def validate_candidate_output(candidate: CandidateSpec, output: object) -> str:
    """Validate a singleton output against the candidate's declared schema."""

    shape = validate_model_output(
        candidate.task,
        output,
        expected_length=_forecast_horizon_from_schema(candidate.output_schema),
    )
    if candidate.task == ModelTask.FINANCE_SENTIMENT and candidate.output_schema != shape:
        raise InvalidModelOutputError("output does not match the candidate schema")
    if candidate.task == ModelTask.TSPULSE_FEATURES and not candidate.output_schema.startswith("features["):
        raise InvalidModelOutputError("TSPulse candidate schema must be features[n]")
    if candidate.task == ModelTask.FORECAST and shape != candidate.output_schema:
        raise InvalidModelOutputError("forecast output does not match the candidate schema")
    return shape


def validate_candidate_batch_output(
    candidate: CandidateSpec,
    output: object,
    *,
    expected_batch_size: int,
) -> str:
    """Validate a candidate's complete batch output contract."""

    shape = validate_model_batch_output(
        candidate.task,
        output,
        expected_batch_size=expected_batch_size,
        expected_forecast_horizon=_forecast_horizon_from_schema(candidate.output_schema),
    )
    if candidate.task == ModelTask.FINANCE_SENTIMENT and not shape.endswith(
        "<sentiment(label,confidence)>"
    ):
        raise InvalidModelOutputError("FinBERT batch output does not match the candidate schema")
    if candidate.task == ModelTask.TSPULSE_FEATURES and not candidate.output_schema.startswith("features["):
        raise InvalidModelOutputError("TSPulse candidate schema must be features[n]")
    if candidate.task == ModelTask.FORECAST:
        expected = _forecast_horizon_from_schema(candidate.output_schema)
        if expected is None or not shape.endswith(f"<forecast[{expected}]>"):
            raise InvalidModelOutputError("forecast batch output does not match the candidate schema")
    return shape


def _nonfinite_rejection_probe(task: ModelTask) -> bool:
    try:
        invalid: object = (
            {"label": "positive", "confidence": float("nan")}
            if task == ModelTask.FINANCE_SENTIMENT
            else (float("nan"),)
        )
        validate_model_output(task, invalid)
    except InvalidModelOutputError:
        return True
    return False


def _runner_identity(runner: RuntimeRunner, candidate: CandidateSpec) -> tuple[str, str]:
    family = str(getattr(runner, "model_family", ""))
    if family != candidate.family.value:
        raise NoSilentFallbackError(
            f"runner family {family!r} does not match requested {candidate.family.value!r}"
        )
    version = str(getattr(runner, "version", ""))
    if not version.strip():
        raise QualificationError("runner version is required")
    try:
        runner_source = inspect.getsource(type(runner))
    except (OSError, TypeError):
        runner_source = f"{type(runner).__module__}.{type(runner).__qualname__}"
    callable_material: list[str] = []
    for attribute in ("_load_fn", "_infer_fn", "_unload_fn", "_seed_fn"):
        function = getattr(runner, attribute, None)
        if function is None:
            continue
        try:
            source = inspect.getsource(function)
        except (OSError, TypeError):
            code = getattr(function, "__code__", None)
            source = repr(getattr(code, "co_code", function))
        callable_material.append(f"{attribute}:{source}")
    callable_source = "\n".join(callable_material)
    runner_hash = sha256(f"{version}\n{runner_source}\n{callable_source}".encode()).hexdigest()
    return version, runner_hash


def _call_runner_load(runner: RuntimeRunner, checkpoint: CheckpointPin | None) -> object:
    signature = inspect.signature(runner.load)
    if "offline" not in signature.parameters:
        raise QualificationError("runner.load must explicitly accept offline=True")
    return runner.load(checkpoint=checkpoint, offline=True)


def _sample(sampler: _ResourceSampler) -> tuple[float, float | None]:
    return sampler.rss_mib(), sampler.vram_mib()


def _quarantined_result(
    candidate: CandidateSpec,
    *,
    dataset: BenchmarkDataset,
    environment: RuntimeEnvironment,
    reason: str,
    missing_checkpoint: bool = False,
    corrupt_checkpoint: bool = False,
    network_access_attempted: bool = False,
) -> RuntimeQualificationResult:
    return RuntimeQualificationResult(
        candidate=candidate,
        status=QualificationStatus.QUARANTINED,
        environment=environment,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        measured_at=datetime.now(UTC),
        failure_reason=reason,
        missing_checkpoint_quarantined=missing_checkpoint,
        corrupt_checkpoint_quarantined=corrupt_checkpoint,
        network_access_attempted=network_access_attempted,
    )


def _worker_environment(
    identity: RuntimeWorkerIdentity,
    *,
    candidate: CandidateSpec,
    cache_path: str,
    lock_artifact_path: str | None = None,
) -> RuntimeEnvironment:
    packages = {name.lower().replace("_", "-"): value for name, value in identity.package_versions}
    return RuntimeEnvironment(
        python_version=identity.python_version,
        transformers_version=packages.get("transformers"),
        huggingface_hub_version=packages.get("huggingface-hub"),
        torch_version=identity.torch_version,
        cuda_version=identity.cuda_version,
        device="cuda" if candidate.gpu else "cpu",
        dtype="float32",
        quantization=candidate.external_checkpoint.quantization if candidate.external_checkpoint else "none",
        cache_path=cache_path,
        runner_version=identity.runner_version,
        runner_hash=identity.runner_hash,
        runtime_lock_hash=identity.runtime_lock_hash,
        lock_artifact_path=lock_artifact_path,
        installed_environment_sha256=identity.installed_environment_sha256,
        environment_fingerprint=identity.environment_fingerprint,
        sys_executable=identity.sys_executable,
        sys_prefix=identity.sys_prefix,
        sys_base_prefix=identity.sys_base_prefix,
        python_launcher_hash=identity.python_launcher_hash,
        python_launcher_target=identity.python_launcher_target,
        resolved_python_binary_hash=identity.resolved_python_binary_hash,
        pyvenv_cfg_hash=identity.pyvenv_cfg_hash,
        python_executable_hash=identity.resolved_python_binary_hash,
    )


def _validate_worker_identity(
    identity: RuntimeWorkerIdentity,
    *,
    pin: RuntimePin,
    candidate: CandidateSpec,
    executable: Path,
) -> None:
    if identity.model_family != candidate.family.value:
        raise NoSilentFallbackError("isolated worker family does not match the requested candidate")
    # The worker reports ``sys.executable`` as the launcher path, not the
    # resolved base interpreter.  A POSIX venv is allowed to make this path a
    # symlink, so compare the pinned launcher identity separately below.
    expected_launcher = str(executable)
    if Path(identity.sys_executable).expanduser().resolve(strict=False) != Path(expected_launcher).resolve(strict=False):
        raise QualificationError("isolated worker reported an unexpected sys.executable")
    expected_environment = str(Path(pin.environment_path).expanduser().resolve(strict=False))
    if str(Path(identity.sys_prefix).expanduser().resolve(strict=False)) != expected_environment:
        raise QualificationError("isolated worker sys.prefix does not identify the pinned environment")
    if str(Path(identity.sys_base_prefix).expanduser().resolve(strict=False)) == expected_environment:
        raise QualificationError("isolated worker sys.base_prefix must identify the base interpreter")
    if identity.runtime_lock_hash != pin.lock_hash or identity.lock_artifact_hash != pin.lock_hash:
        raise QualificationError("isolated worker runtime lock identity does not match the pin")
    launcher_target = str(executable.resolve(strict=False)) if executable.is_symlink() else None
    expected_launcher_hash = pin.python_launcher_hash or pin.python_executable_hash
    if identity.python_launcher_hash != expected_launcher_hash:
        raise QualificationError("isolated worker launcher identity does not match the pin")
    if identity.python_launcher_target != launcher_target:
        raise QualificationError("isolated worker launcher target does not match the pin")
    expected_binary_hash = pin.resolved_python_binary_hash or pin.python_executable_hash
    if identity.resolved_python_binary_hash != expected_binary_hash:
        raise QualificationError("isolated worker resolved interpreter identity does not match the pin")
    if identity.python_executable_hash != identity.resolved_python_binary_hash:
        raise QualificationError("isolated worker legacy executable identity is inconsistent")
    if identity.pyvenv_cfg_hash != pin.pyvenv_cfg_hash:
        raise QualificationError("isolated worker pyvenv.cfg identity does not match the pin")
    if identity.installed_environment_sha256 != pin.installed_environment_sha256:
        raise QualificationError("isolated worker installed-environment identity does not match the pin")
    if identity.environment_fingerprint != pin.environment_fingerprint:
        raise QualificationError("isolated worker environment fingerprint does not match the pin")
    if identity.runner_version != pin.runner_version or identity.runner_hash != pin.runner_hash:
        raise QualificationError("isolated worker runner identity does not match the pin")
    if not _python_constraint_satisfied(pin.python_constraint, identity.python_version):
        raise CompatibilityError("isolated worker Python version does not satisfy the runtime pin")
    observed = {
        name.lower().replace("_", "-"): value for name, value in identity.package_versions
    }
    for dependency in pin.dependencies:
        name, separator, expected = dependency.partition("==")
        if not separator:
            name = dependency.split("@", 1)[0]
            expected = ""
        actual = observed.get(name.strip().lower().replace("_", "-"))
        if actual is None or (expected and actual != expected.strip()):
            raise QualificationError(f"isolated worker package identity mismatch for {name.strip()}")


def _validate_worker_success_envelope(
    response: RuntimeWorkerResponse,
    *,
    repeats: int,
    expected_batch_size: int,
) -> None:
    if response.error_class is not None:
        raise QualificationError("successful worker response contains an error class")
    if not response.offline_cached_inference:
        raise QualificationError("successful worker response did not prove offline inference")
    if len(response.outputs) != repeats:
        raise QualificationError("worker output repeat count does not match the request")
    if len(response.warm_durations_ms) != repeats:
        raise QualificationError("worker timing repeat count does not match the request")
    if response.batch_size != expected_batch_size:
        raise QualificationError("worker batch size does not match the request")
    if response.batch_output is None:
        raise QualificationError("worker success response is missing batch output")


def _worker_request(
    candidate: CandidateSpec,
    *,
    pin: RuntimePin,
    dataset: BenchmarkDataset,
    sample_input: object,
    batch_input: object,
    repeats: int,
) -> dict[str, object]:
    labels = [label for _text, label in dataset.public_text_fixture]
    return {
        "protocol_version": 1,
        "worker_kind": pin.worker_kind,
        "candidate": candidate.name,
        "family": candidate.family.value,
        "task": candidate.task.value,
        "output_schema": candidate.output_schema,
        "repeatability_policy": candidate.repeatability_policy.value,
        "repeatability_seed": candidate.repeatability_seed,
        "python_constraint": pin.python_constraint,
        "trust_remote_code": False,
        "sample_input": _canonical(sample_input),
        "batch_input": _canonical(batch_input),
        "cache_path": candidate.external_checkpoint.cache_path if candidate.external_checkpoint else None,
        "local_files_only": True,
        "repeats": repeats,
        "expected_batch_size": _batch_size(batch_input),
        "expected_labels": labels,
        "dependencies": list(pin.dependencies),
        "lock_artifact_path": pin.lock_artifact_path,
        "lock_hash": pin.lock_hash,
        "installed_environment_manifest_path": pin.installed_environment_manifest_path,
        "installed_environment_sha256": pin.installed_environment_sha256,
        "python_launcher": pin.python_launcher or pin.python_executable,
        "python_launcher_hash": pin.python_launcher_hash or pin.python_executable_hash,
        "python_launcher_target": pin.python_launcher_target,
        "resolved_python_binary_hash": pin.resolved_python_binary_hash or pin.python_executable_hash,
        "pyvenv_cfg_path": pin.pyvenv_cfg_path,
        "pyvenv_cfg_hash": pin.pyvenv_cfg_hash,
        "python_executable_hash": pin.resolved_python_binary_hash or pin.python_executable_hash,
        "environment_fingerprint": pin.environment_fingerprint,
        "runner_version": pin.runner_version,
        "runner_hash": pin.runner_hash,
    }


def _run_isolated_runtime_qualification(
    candidate: CandidateSpec,
    *,
    dataset: BenchmarkDataset,
    sample_input: object,
    batch_input: object,
    repeats: int,
    ceiling: ResourceCeiling,
    environment: RuntimeEnvironment,
    repository_root: Path | None,
) -> RuntimeQualificationResult:
    """Run an external candidate only through its pinned Python worker."""

    checkpoint = candidate.external_checkpoint
    pin = candidate.runtime_pin
    assert checkpoint is not None and pin is not None
    observed_artifacts: tuple[ArtifactPin, ...] = ()
    try:
        observed_artifacts = verify_checkpoint_artifacts(checkpoint, repository_root=repository_root)
        executable = verify_runtime_pin(pin, repository_root=repository_root)
    except (CheckpointPinError, CheckpointNotCachedError, CheckpointIntegrityError, QualificationError) as exc:
        return _quarantined_result(
            candidate,
            dataset=dataset,
            environment=environment,
            reason=str(exc),
            missing_checkpoint=isinstance(exc, CheckpointNotCachedError),
            corrupt_checkpoint=isinstance(exc, CheckpointIntegrityError),
        )
    request = _worker_request(
        candidate,
        pin=pin,
        dataset=dataset,
        sample_input=sample_input,
        batch_input=batch_input,
        repeats=repeats,
    )
    worker_script = Path(str(pin.worker_script)).expanduser().resolve(strict=False)
    if _launcher_identity_hash(executable) != (pin.python_launcher_hash or pin.python_executable_hash):
        return _quarantined_result(
            candidate,
            dataset=dataset,
            environment=environment,
            reason="pinned runtime Python executable changed after verification",
        )
    binary = pin.resolve_python_binary()
    if sha256_file(binary) != (pin.resolved_python_binary_hash or pin.python_executable_hash):
        return _quarantined_result(
            candidate,
            dataset=dataset,
            environment=environment,
            reason="pinned runtime Python binary changed after verification",
        )
    if _worker_runner_hash(worker_script, str(pin.runner_version)) != pin.runner_hash:
        return _quarantined_result(
            candidate,
            dataset=dataset,
            environment=environment,
            reason="pinned runtime worker changed after verification",
        )
    request_json = json.dumps(request, sort_keys=True, separators=(",", ":"))
    # Never inherit the process's secret inventory or arbitrary connector
    # configuration into a third-party model runtime.  The worker receives a
    # deliberately tiny environment and only the offline controls below.
    worker_environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT"}
    }
    worker_environment.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "PYTHONNOUSERSITE": "1",
        }
    )
    monitor: _ProcessTreeMonitor | None = None
    process: subprocess.Popen[str] | None = None
    decoded: Mapping[str, object] | None = None
    try:
        process = subprocess.Popen(
            [str(executable), "-I", str(worker_script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=worker_environment,
            cwd=str(worker_script.parent),
        )
        monitor = _ProcessTreeMonitor(process.pid)
        monitor.start()
        stdout, _stderr = process.communicate(request_json + "\n", timeout=120)
        lines = [line for line in stdout.splitlines() if line.strip()]
        if lines:
            parsed = json.loads(lines[-1])
            if isinstance(parsed, Mapping):
                decoded = parsed
        if process.returncode not in (0, None) and decoded is None:
            raise QualificationError("isolated runtime worker exited without a sanitized response")
        if decoded is None:
            raise QualificationError("isolated runtime worker returned no response")
        response = RuntimeWorkerResponse.model_validate(decoded)
    except subprocess.TimeoutExpired:
        if process is not None:
            _terminate_process_tree(process)
            process.communicate()
        return RuntimeQualificationResult(
            candidate=candidate,
            status=QualificationStatus.FAILED,
            environment=environment,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            measured_at=datetime.now(UTC),
            failure_reason="isolated runtime worker timeout",
            resource=None,
        )
    except (json.JSONDecodeError, ValueError, QualificationError) as exc:
        return RuntimeQualificationResult(
            candidate=candidate,
            status=QualificationStatus.FAILED,
            environment=environment,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            measured_at=datetime.now(UTC),
            failure_reason=f"isolated worker failure: {type(exc).__name__}",
        )
    finally:
        if monitor is not None:
            monitor.stop()

    assert response is not None
    worker_resource = _isolated_worker_resource(
        response,
        monitor=monitor,
        ceiling=ceiling,
        batch_size=_batch_size(batch_input),
    )
    # Treat the worker's explicit privacy signal before any identity or output
    # validation.  A network attempt is a fail-closed result even if a
    # candidate worker cannot complete its normal identity envelope.
    if response.network_access_attempted:
        return RuntimeQualificationResult(
            candidate=candidate,
            status=QualificationStatus.FAILED,
            environment=environment,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            measured_at=datetime.now(UTC),
            failure_reason="network access attempted during offline qualification",
            resource=worker_resource,
            network_access_attempted=True,
        )
    try:
        _validate_worker_identity(response.identity, pin=pin, candidate=candidate, executable=executable)
        worker_environment_result = _worker_environment(
            response.identity,
            candidate=candidate,
            cache_path=checkpoint.cache_path,
            lock_artifact_path=pin.lock_artifact_path,
        )
        worker_environment_result.assert_transformers_baseline(requires_transformers=candidate.requires_transformers)
        _validate_worker_success_envelope(
            response,
            repeats=repeats,
            expected_batch_size=_batch_size(batch_input),
        )
        if candidate.repeatability_policy == RepeatabilityPolicy.SEEDED_REPRODUCIBLE:
            expected_seed = candidate.repeatability_seed
            if expected_seed is None or response.applied_seeds != (expected_seed,) * repeats:
                raise QualificationError("worker did not prove the requested seed was applied every repetition")
        output_shape = validate_candidate_output(candidate, response.outputs[0])
        batch_size = _batch_size(batch_input)
        validate_candidate_batch_output(candidate, response.batch_output, expected_batch_size=batch_size)
        finbert_metrics = _finbert_metrics(dataset, response.batch_output) if candidate.task == ModelTask.FINANCE_SENTIMENT else None
        repeat_digests = [output_digest(output) for output in response.outputs]
        equal = len(set(repeat_digests)) == 1
        stochastic_evidence = (
            _stochastic_evidence(response.outputs, seed=candidate.repeatability_seed)
            if candidate.repeatability_policy == RepeatabilityPolicy.STOCHASTIC_CHARACTERIZED
            else {
                "stochastic_repeat_count": 0,
                "stochastic_unique_output_count": 0,
                "stochastic_deterministic_match_rate": 0,
                "stochastic_seeds": (),
                "stochastic_dispersion": None,
                "stochastic_variation_observed": False,
            }
        )
        if candidate.repeatability_policy == RepeatabilityPolicy.STOCHASTIC_CHARACTERIZED and not stochastic_evidence["stochastic_variation_observed"]:
            raise QualificationError("stochastic characterization observed no output variation")
        resource = worker_resource
        common = {
            "candidate": candidate,
            "environment": worker_environment_result,
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.version,
            "measured_at": datetime.now(UTC),
            "observed_artifacts": observed_artifacts,
            "resource": resource,
            "output_shape": output_shape,
            "output_schema_valid": True,
            "nan_inf_rejection_passed": _nonfinite_rejection_probe(candidate.task),
            "repeated_outputs_equal": equal,
            "deterministic_match_rate": sum(digest == repeat_digests[0] for digest in repeat_digests) / len(repeat_digests),
            "seeded_repeatability_match_rate": sum(digest == repeat_digests[0] for digest in repeat_digests) / len(repeat_digests),
            "repeatability_seed": candidate.repeatability_seed,
            "stochastic_characterized": bool(stochastic_evidence["stochastic_variation_observed"]),
            **stochastic_evidence,
            "offline_cached_inference": True,
            "one_inference_completed": True,
            "batch_completed": True,
            "applied_seeds": response.applied_seeds,
            "finbert_accuracy": finbert_metrics[0] if finbert_metrics else None,
            "finbert_per_label_accuracy": finbert_metrics[1] if finbert_metrics else (),
            "finbert_mean_confidence": finbert_metrics[2] if finbert_metrics else None,
        }
        if candidate.repeatability_policy == RepeatabilityPolicy.SEEDED_REPRODUCIBLE:
            common["stochastic_seeds"] = response.applied_seeds
        repeatability_failure = (
            candidate.repeatability_policy == RepeatabilityPolicy.DETERMINISTIC_REQUIRED and not equal
        ) or (
            candidate.repeatability_policy == RepeatabilityPolicy.SEEDED_REPRODUCIBLE
            and common["seeded_repeatability_match_rate"] < 1
        )
        if not resource.resource_limit_passed or not resource.memory_released or repeatability_failure:
            reason = "resource/recovery/repeatability policy failed"
            return RuntimeQualificationResult(**common, status=QualificationStatus.FAILED, failure_reason=reason)
        return RuntimeQualificationResult(**common, status=QualificationStatus.MEASURED)
    except NetworkAccessAttemptError:
        return RuntimeQualificationResult(
            candidate=candidate,
            status=QualificationStatus.FAILED,
            environment=environment,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            measured_at=datetime.now(UTC),
            failure_reason="network access attempted during offline qualification",
            resource=worker_resource,
            network_access_attempted=True,
        )
    except (InvalidModelOutputError, QualificationError, ValueError) as exc:
        return RuntimeQualificationResult(
            candidate=candidate,
            status=QualificationStatus.FAILED,
            environment=environment,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            measured_at=datetime.now(UTC),
            failure_reason=f"isolated worker result rejected: {type(exc).__name__}",
            resource=worker_resource,
        )


def run_runtime_qualification(
    candidate: CandidateSpec,
    *,
    runner: RuntimeRunner | None,
    dataset: BenchmarkDataset,
    sample_input: object,
    batch_input: object,
    repeats: int = 3,
    ceiling: ResourceCeiling | None = None,
    environment: RuntimeEnvironment | None = None,
    repository_root: Path | None = None,
) -> RuntimeQualificationResult:
    """Run a short, offline, no-fallback qualification for one candidate."""

    if repeats < 2:
        raise ValueError("runtime qualification requires at least two repeated inferences")
    if dataset.task != candidate.task:
        raise ValueError("candidate task and benchmark dataset task must match")
    ceiling = ceiling or ResourceCeiling()
    cache = candidate.external_checkpoint.cache_path if candidate.external_checkpoint else str(
        Path("~/.cache/advisorai-v3/models").expanduser()
    )
    if environment is None:
        environment = runtime_environment(cache_path=cache, device="cuda" if candidate.gpu else "cpu")
    if candidate.external_checkpoint is not None:
        checkpoint = candidate.external_checkpoint
        try:
            checkpoint.assert_cache_outside_repository(repository_root or Path.cwd())
            if candidate.runtime_pin is not None:
                candidate.runtime_pin.assert_environment_outside_repository(repository_root or Path.cwd())
        except (CheckpointPinError, QualificationError) as exc:
            return _quarantined_result(candidate, dataset=dataset, environment=environment, reason=str(exc))
        if checkpoint.license_admission.status != LicenseAdmissionStatus.APPROVED:
            return _quarantined_result(
                candidate,
                dataset=dataset,
                environment=environment,
                reason="license admission is not approved",
            )
        if checkpoint.tokenizer is not None and (
            checkpoint.tokenizer_license_admission is None
            or checkpoint.tokenizer_license_admission.status != LicenseAdmissionStatus.APPROVED
        ):
            return _quarantined_result(
                candidate,
                dataset=dataset,
                environment=environment,
                reason="tokenizer license admission is not approved",
            )
        if candidate.runtime_pin is None:
            return _quarantined_result(
                candidate,
                dataset=dataset,
                environment=environment,
                reason="execution runtime pin is missing",
            )
        if candidate.runtime_pin.status != RuntimeAdmissionStatus.APPROVED:
            return _quarantined_result(
                candidate,
                dataset=dataset,
                environment=environment,
                reason="execution runtime admission is not approved",
            )
        if runner is not None:
            return _quarantined_result(
                candidate,
                dataset=dataset,
                environment=environment,
                reason="external candidates cannot execute an in-process runner",
            )
        if environment.runtime_lock_hash not in (None, candidate.runtime_pin.lock_hash):
            return _quarantined_result(
                candidate,
                dataset=dataset,
                environment=environment,
                reason="supplied runtime lock hash identity does not match the candidate pin",
            )
        with (GpuModelLease(candidate.family.value) if candidate.gpu else nullcontext()):
            return _run_isolated_runtime_qualification(
                candidate,
                dataset=dataset,
                sample_input=sample_input,
                batch_input=batch_input,
                repeats=repeats,
                ceiling=ceiling,
                environment=environment,
                repository_root=repository_root or Path.cwd(),
            )
    if candidate.requires_transformers:
        try:
            environment.assert_transformers_baseline(requires_transformers=True)
        except CompatibilityError as exc:
            return _quarantined_result(candidate, dataset=dataset, environment=environment, reason=str(exc))
    if runner is None:
        return _quarantined_result(
            candidate,
            dataset=dataset,
            environment=environment,
            reason="runner/checkpoint is not supplied; candidate remains quarantined",
            missing_checkpoint=candidate.external_checkpoint is not None,
        )
    try:
        runner_version, runner_hash = _runner_identity(runner, candidate)
        environment = environment.model_copy(update={"runner_version": runner_version, "runner_hash": runner_hash})
        # model_copy does not revalidate in Pydantic; rebuild to retain the frozen contract.
        environment = RuntimeEnvironment.model_validate(environment.model_dump())
        observed_artifacts: tuple[ArtifactPin, ...] = ()
        if candidate.external_checkpoint is not None:
            observed_artifacts = verify_checkpoint_artifacts(
                candidate.external_checkpoint,
                repository_root=repository_root,
            )
    except (CheckpointPinError, CheckpointNotCachedError) as exc:
        return _quarantined_result(
            candidate,
            dataset=dataset,
            environment=environment,
            reason=str(exc),
            missing_checkpoint=isinstance(exc, CheckpointNotCachedError),
            corrupt_checkpoint=False,
        )
    except CheckpointIntegrityError as exc:
        return _quarantined_result(
            candidate,
            dataset=dataset,
            environment=environment,
            reason=str(exc),
            corrupt_checkpoint=True,
        )
    except (NoSilentFallbackError, QualificationError) as exc:
        return _quarantined_result(candidate, dataset=dataset, environment=environment, reason=str(exc))

    sampler = _ResourceSampler()
    rss_before, vram_before = _sample(sampler)
    if candidate.gpu:
        sampler.reset_peak()
    model: object | None = None
    try:
        if candidate.gpu:
            with GpuModelLease(candidate.family.value):
                return _run_loaded_qualification(
                    candidate,
                    runner=runner,
                    dataset=dataset,
                    sample_input=sample_input,
                    batch_input=batch_input,
                    repeats=repeats,
                    ceiling=ceiling,
                    environment=environment,
                    sampler=sampler,
                    rss_before=rss_before,
                    vram_before=vram_before,
                    observed_artifacts=observed_artifacts,
                )
        return _run_loaded_qualification(
            candidate,
            runner=runner,
            dataset=dataset,
            sample_input=sample_input,
            batch_input=batch_input,
            repeats=repeats,
            ceiling=ceiling,
            environment=environment,
            sampler=sampler,
            rss_before=rss_before,
            vram_before=vram_before,
            observed_artifacts=observed_artifacts,
        )
    except NetworkAccessAttemptError:
        return RuntimeQualificationResult(
            candidate=candidate,
            status=QualificationStatus.FAILED,
            environment=environment,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            measured_at=datetime.now(UTC),
            failure_reason="network access attempted during offline qualification",
            network_access_attempted=True,
        )
    except (InvalidModelOutputError, ResourceLimitError, QualificationError) as exc:
        return RuntimeQualificationResult(
            candidate=candidate,
            status=QualificationStatus.FAILED,
            environment=environment,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            measured_at=datetime.now(UTC),
            failure_reason=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 - third-party runner failures become sanitized evidence
        return RuntimeQualificationResult(
            candidate=candidate,
            status=QualificationStatus.FAILED,
            environment=environment,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            measured_at=datetime.now(UTC),
            failure_reason=f"runner failure: {type(exc).__name__}",
        )
    finally:
        del model
        gc.collect()


def _run_loaded_qualification(
    candidate: CandidateSpec,
    *,
    runner: RuntimeRunner,
    dataset: BenchmarkDataset,
    sample_input: object,
    batch_input: object,
    repeats: int,
    ceiling: ResourceCeiling,
    environment: RuntimeEnvironment,
    sampler: _ResourceSampler,
    rss_before: float,
    vram_before: float | None,
    observed_artifacts: tuple[ArtifactPin, ...],
) -> RuntimeQualificationResult:
    model: object | None = None
    measured_at = datetime.now(UTC)
    monitor = _ResourceMonitor(sampler)
    monitor.start()
    try:
        load_started = time.perf_counter()
        with network_blocked():
            model = _call_runner_load(runner, candidate.external_checkpoint)
        cold_load_ms = (time.perf_counter() - load_started) * 1000
        rss_after_load, vram_after_load = _sample(sampler)
        durations: list[float] = []
        outputs: list[object] = []
        applied_seeds: list[int] = []
        with network_blocked():
            for _ in range(repeats):
                if candidate.repeatability_policy == RepeatabilityPolicy.SEEDED_REPRODUCIBLE:
                    seed_setter = getattr(runner, "set_seed", None)
                    if not callable(seed_setter) or candidate.repeatability_seed is None:
                        raise QualificationError("seeded candidate runner must expose set_seed")
                    seed_setter(candidate.repeatability_seed)
                    applied_seeds.append(candidate.repeatability_seed)
                started = time.perf_counter()
                output = runner.infer(model, sample_input)
                durations.append((time.perf_counter() - started) * 1000)
                validate_candidate_output(candidate, output)
                outputs.append(output)
            batch_started = time.perf_counter()
            batch_output = runner.infer(model, batch_input)
            batch_inference_ms = (time.perf_counter() - batch_started) * 1000
        batch_size = _batch_size(batch_input)
        validate_candidate_batch_output(candidate, batch_output, expected_batch_size=batch_size)
        finbert_metrics = _finbert_metrics(dataset, batch_output) if candidate.task == ModelTask.FINANCE_SENTIMENT else None
        try:
            runner.unload(model)
            unload_succeeded = True
        except Exception:  # noqa: BLE001 - evidence records unload failure
            unload_succeeded = False
        del model
        model = None
        gc.collect()
        monitor.stop()
        rss_after_unload, vram_after_unload = _sample(sampler)
        rss_peak = max(rss_before, rss_after_load, monitor.peak_rss, rss_after_unload)
        vram_peak = monitor.peak_vram
        measured_peak_vram = sampler.peak_vram_mib()
        if measured_peak_vram is not None:
            vram_peak = max(vram_peak or 0, measured_peak_vram)
        rss_residual = max(0, rss_after_unload - rss_before)
        vram_residual = (
            max(0, vram_after_unload - (vram_before or 0))
            if vram_after_unload is not None
            else None
        )
        memory_released = unload_succeeded and rss_residual <= ceiling.max_residual_rss_mib
        if vram_residual is not None:
            memory_released = memory_released and vram_residual <= ceiling.max_residual_vram_mib
        resource_limit_passed = (
            rss_peak <= ceiling.max_rss_mib
            and (vram_peak is None or vram_peak <= ceiling.max_vram_mib)
        )
        resource = RuntimeResourceResult(
            cold_load_ms=cold_load_ms,
            warm_inference_p50_ms=statistics.median(durations),
            warm_inference_p95_ms=_percentile95(durations),
            batch_inference_ms=batch_inference_ms,
            batch_size=batch_size,
            batch_throughput_per_second=batch_size / max(batch_inference_ms / 1000, 1e-9),
            rss_before_mib=rss_before,
            rss_after_load_mib=rss_after_load,
            rss_peak_mib=rss_peak,
            rss_after_unload_mib=rss_after_unload,
            vram_before_mib=vram_before,
            vram_after_load_mib=vram_after_load,
            vram_peak_mib=vram_peak,
            vram_after_unload_mib=vram_after_unload,
            unload_succeeded=unload_succeeded,
            memory_released=memory_released,
            rss_residual_mib=rss_residual,
            vram_residual_mib=vram_residual,
            resource_limit_passed=resource_limit_passed,
        )
        digests = [output_digest(output) for output in outputs]
        equal = len(set(digests)) == 1
        stochastic_evidence = (
            _stochastic_evidence(outputs, seed=candidate.repeatability_seed)
            if candidate.repeatability_policy == RepeatabilityPolicy.STOCHASTIC_CHARACTERIZED
            else {
                "stochastic_repeat_count": 0,
                "stochastic_unique_output_count": 0,
                "stochastic_deterministic_match_rate": 0,
                "stochastic_seeds": (),
                "stochastic_dispersion": None,
                "stochastic_variation_observed": False,
            }
        )
        stochastic_characterized = bool(stochastic_evidence["stochastic_variation_observed"])
        common = {
            "candidate": candidate,
            "environment": environment,
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.version,
            "measured_at": measured_at,
            "observed_artifacts": observed_artifacts,
            "resource": resource,
            "output_shape": validate_candidate_output(candidate, outputs[0]),
            "output_schema_valid": True,
            "nan_inf_rejection_passed": _nonfinite_rejection_probe(candidate.task),
            "repeated_outputs_equal": equal,
            "deterministic_match_rate": sum(digest == digests[0] for digest in digests) / len(digests),
            "seeded_repeatability_match_rate": sum(digest == digests[0] for digest in digests) / len(digests),
            "repeatability_seed": candidate.repeatability_seed,
            "stochastic_characterized": stochastic_characterized,
            **stochastic_evidence,
            "offline_cached_inference": True,
            "one_inference_completed": True,
            "batch_completed": True,
            "applied_seeds": tuple(applied_seeds),
            "finbert_accuracy": finbert_metrics[0] if finbert_metrics else None,
            "finbert_per_label_accuracy": finbert_metrics[1] if finbert_metrics else (),
            "finbert_mean_confidence": finbert_metrics[2] if finbert_metrics else None,
        }
        repeatability_failure = (
            candidate.repeatability_policy == RepeatabilityPolicy.DETERMINISTIC_REQUIRED and not equal
        ) or (
            candidate.repeatability_policy == RepeatabilityPolicy.SEEDED_REPRODUCIBLE
            and common["seeded_repeatability_match_rate"] < 1
        )
        if candidate.repeatability_policy == RepeatabilityPolicy.STOCHASTIC_CHARACTERIZED and not stochastic_characterized:
            repeatability_failure = True
        if not resource_limit_passed or not memory_released or repeatability_failure:
            reasons = []
            if not resource_limit_passed:
                reasons.append("resource ceiling exceeded")
            if not memory_released:
                reasons.append("post-unload memory recovery failed")
            if not unload_succeeded:
                reasons.append("runner unload failed")
            if repeatability_failure:
                reasons.append("repeatability/characterization policy failed")
            return RuntimeQualificationResult(
                **common,
                status=QualificationStatus.FAILED,
                failure_reason="; ".join(reasons),
            )
        return RuntimeQualificationResult(
            **common,
            status=QualificationStatus.MEASURED,
        )
    except InvalidModelOutputError:
        raise
    finally:
        if model is not None:
            try:
                runner.unload(model)
            except Exception:  # pragma: no cover - best effort after a failed probe
                pass
        monitor.stop()


def _batch_size(payload: object) -> int:
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, Mapping)):
        return max(1, len(payload))
    return 1


def _finbert_metrics(
    dataset: BenchmarkDataset,
    batch_output: object,
) -> tuple[float, tuple[tuple[str, float], ...], float]:
    if not isinstance(batch_output, Sequence) or isinstance(batch_output, (str, bytes, Mapping)):
        raise InvalidModelOutputError("FinBERT metrics require a typed batch sequence")
    expected = tuple(label for _text, label in dataset.public_text_fixture)
    if len(batch_output) != len(expected):
        raise InvalidModelOutputError("FinBERT batch does not cover the fixed fixture")
    labels = [str(cast(Mapping[str, object], item).get("label", "")) for item in batch_output]
    correct = [label == expected_label for label, expected_label in zip(labels, expected, strict=True)]
    per_label: list[tuple[str, float]] = []
    for label in sorted(set(expected)):
        indices = [index for index, expected_label in enumerate(expected) if expected_label == label]
        per_label.append(
            (label, sum(correct[index] for index in indices) / len(indices))
        )
    confidences = [
        _finite_number(
            (item_mapping := cast(Mapping[str, object], item)).get(
                "confidence", item_mapping.get("score")
            )
        )
        for item in batch_output
    ]
    return sum(correct) / len(correct), tuple(per_label), sum(confidences) / len(confidences)


def _percentile95(values: Sequence[float]) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(0.95 * len(ordered)) - 1))
    return ordered[index]


def _isolated_worker_resource(
    response: RuntimeWorkerResponse,
    *,
    monitor: _ProcessTreeMonitor | None,
    ceiling: ResourceCeiling,
    batch_size: int,
) -> RuntimeResourceResult:
    rss_before = monitor.rss_before_mib if monitor is not None else 0.0
    rss_peak = max(
        rss_before,
        response.rss_after_load_mib,
        monitor.rss_peak_mib if monitor is not None else 0,
    )
    rss_after_unload = response.rss_after_unload_mib
    rss_residual = max(0.0, rss_after_unload - rss_before)
    resource_limit_passed = rss_peak <= ceiling.max_rss_mib
    memory_released = response.unload_succeeded and rss_residual <= ceiling.max_residual_rss_mib
    durations = response.warm_durations_ms or (0.0,)
    return RuntimeResourceResult(
        cold_load_ms=response.cold_load_ms,
        warm_inference_p50_ms=statistics.median(durations),
        warm_inference_p95_ms=_percentile95(durations),
        batch_inference_ms=response.batch_inference_ms,
        batch_size=batch_size,
        batch_throughput_per_second=batch_size / max(response.batch_inference_ms / 1000, 1e-9),
        rss_before_mib=rss_before,
        rss_after_load_mib=response.rss_after_load_mib,
        rss_peak_mib=rss_peak,
        rss_after_unload_mib=rss_after_unload,
        vram_before_mib=0 if response.vram_after_load_mib is not None else None,
        vram_after_load_mib=response.vram_after_load_mib,
        vram_peak_mib=response.vram_peak_mib or response.vram_after_load_mib,
        vram_after_unload_mib=response.vram_after_unload_mib,
        unload_succeeded=response.unload_succeeded,
        memory_released=memory_released,
        rss_residual_mib=rss_residual,
        vram_residual_mib=response.vram_after_unload_mib,
        resource_limit_passed=resource_limit_passed,
    )


def _candidate_by_family(family: ModelFamily) -> CandidateSpec:
    return next(candidate for candidate in default_runtime_candidates() if candidate.family == family)


def run_finbert_qualification(
    *,
    runner: RuntimeRunner | None,
    environment: RuntimeEnvironment | None = None,
    ceiling: ResourceCeiling | None = None,
    repository_root: Path | None = None,
) -> RuntimeQualificationResult:
    """Qualify FinBERT against the fixed public finance-text fixture."""

    dataset = BenchmarkDataset.finbert_fixture()
    candidate = _candidate_by_family(ModelFamily.FINBERT)
    texts = tuple(text for text, _label in dataset.public_text_fixture)
    return run_runtime_qualification(
        candidate,
        runner=runner,
        dataset=dataset,
        sample_input=texts[0],
        batch_input=texts,
        environment=environment,
        ceiling=ceiling,
        repository_root=repository_root,
    )


def run_tspulse_qualification(
    *,
    runner: RuntimeRunner | None,
    environment: RuntimeEnvironment | None = None,
    ceiling: ResourceCeiling | None = None,
    repository_root: Path | None = None,
) -> RuntimeQualificationResult:
    """Qualify TSPulse only for integrity/anomaly/regime features."""

    dataset = BenchmarkDataset.synthetic_features()
    candidate = _candidate_by_family(ModelFamily.TSPULSE)
    return run_runtime_qualification(
        candidate,
        runner=runner,
        dataset=dataset,
        sample_input=dataset.inputs[:16],
        batch_input=(dataset.inputs[:16], dataset.inputs[16:]),
        environment=environment,
        ceiling=ceiling,
        repository_root=repository_root,
    )


def run_forecast_candidate_benchmark(
    candidate: CandidateSpec,
    *,
    runner: RuntimeRunner | None,
    dataset: BenchmarkDataset,
    ceiling: ResourceCeiling | None = None,
    environment: RuntimeEnvironment | None = None,
    repository_root: Path | None = None,
) -> RuntimeQualificationResult:
    """Qualify a forecast runner and score it beside mandatory baselines.

    The candidate score is recorded as a measurement only.  The single
    dataset guard forces ``adds_marginal_value`` false so a smoke series cannot
    be interpreted as model superiority or admission evidence.
    """

    if candidate.task != ModelTask.FORECAST or dataset.task != ModelTask.FORECAST:
        raise ValueError("forecast candidate benchmark requires forecast task inputs")
    sample = dataset.inputs[:16]
    batch = (dataset.inputs[:16], dataset.inputs[16:32])
    result = run_runtime_qualification(
        candidate,
        runner=runner,
        dataset=dataset,
        sample_input=sample,
        batch_input=batch,
        ceiling=ceiling,
        environment=environment,
        repository_root=repository_root,
    )
    if result.status != QualificationStatus.MEASURED or runner is None:
        return result
    predictions: list[Any] = []
    actuals = list(dataset.targets[8:])
    model: object | None = None
    try:
        with network_blocked():
            model = _call_runner_load(runner, candidate.external_checkpoint)
            for index in range(8, len(dataset.inputs)):
                output = runner.infer(model, dataset.inputs[:index])
                validate_candidate_output(candidate, output)
                predictions.append(tuple(output)[0])  # type: ignore[arg-type]
    finally:
        if model is not None:
            runner.unload(model)
    from decimal import Decimal

    baseline_evaluations = run_forecast_baseline_benchmark(dataset)
    baseline_utility = min(item.net_utility_after_costs for item in baseline_evaluations)
    candidate_evaluation = evaluate_forecasts(
        model_name=candidate.name,
        predictions=tuple(Decimal(str(item)) for item in predictions),
        actuals=tuple(Decimal(str(item)) for item in actuals),
        baseline_utility=baseline_utility,
        baseline_name="mandatory-baseline-set",
        latency_ms=round(result.resource.warm_inference_p50_ms if result.resource else 0),
        peak_ram_mib=round(result.resource.rss_peak_mib if result.resource else 0),
        peak_vram_mib=round(result.resource.vram_peak_mib or 0 if result.resource else 0),
        regime_failures=("single_dataset_no_admission",),
    )
    candidate_evaluation = ForecastEvaluation.model_validate(
        {**candidate_evaluation.model_dump(), "adds_marginal_value": False}
    )
    return RuntimeQualificationResult.model_validate(
        {**result.model_dump(), "forecast_evaluations": (candidate_evaluation, *baseline_evaluations)}
    )


def _cache_path(name: str) -> str:
    return str((Path("~/.cache/advisorai-v3/models") / name).expanduser())


def _runtime_path(name: str) -> str:
    return str(
        (Path("~/.local/share/advisorai-v3/venvs/model-qualification") / name / "pending").expanduser()
    )


def _artifacts(*paths: str) -> tuple[ArtifactPin, ...]:
    return tuple(ArtifactPin(relative_path=path) for path in paths)


def _runtime_pin(
    *,
    candidate: str,
    project: str,
    version_or_commit: str,
    dependencies: tuple[str, ...],
) -> RuntimePin:
    # Registry candidates are deliberately pending until an operator creates
    # an isolated environment and records its real lock artifact and worker
    # identity.  A hash of this declaration is not an environment lock hash.
    return RuntimePin(
        project=project,
        version_or_commit=version_or_commit,
        python_constraint=">=3.12,<3.13",
        dependencies=dependencies,
        lock_hash=None,
        environment_path=_runtime_path(candidate),
        evidence_reference=None,
    )


def _approved_license(identifier: str, reference: str) -> LicenseAdmission:
    return LicenseAdmission(
        status=LicenseAdmissionStatus.APPROVED,
        license_identifier=identifier,
        reviewed_at=datetime(2026, 8, 7, tzinfo=UTC),
        evidence_reference=reference,
    )


def _repo(
    repository_id: str,
    revision: str,
    license: str,
    *paths: str,
    cache_subdir: str = "model",
) -> RepositoryPin:
    provenance_names = {
        ".gitattributes",
        "README.md",
        "LICENSE",
        "LICENSE.md",
        "pyproject.toml",
    }
    runtime_paths = tuple(
        path for path in paths if Path(path).name not in provenance_names and Path(path).suffix.lower() not in {".webp", ".png", ".jpg", ".jpeg"}
    )
    provenance_paths = tuple(path for path in paths if path not in runtime_paths)
    return RepositoryPin(
        repository_id=repository_id,
        revision=revision,
        license=license,
        runtime_artifacts=_artifacts(*runtime_paths),
        provenance_artifacts=_artifacts(*provenance_paths),
        cache_subdir=cache_subdir,
    )


def default_runtime_candidates() -> tuple[CandidateSpec, ...]:
    """Return the versioned initial roster; no weights are downloaded."""

    return (
        CandidateSpec(name="naive", family=ModelFamily.NAIVE, task=ModelTask.FORECAST, output_schema="forecast[1]"),
        CandidateSpec(name="drift", family=ModelFamily.DRIFT, task=ModelTask.FORECAST, output_schema="forecast[1]"),
        CandidateSpec(name="seasonal", family=ModelFamily.SEASONAL, task=ModelTask.FORECAST, output_schema="forecast[1]"),
        CandidateSpec(name="linear", family=ModelFamily.LINEAR, task=ModelTask.FORECAST, output_schema="forecast[1]"),
        CandidateSpec(name="lightgbm", family=ModelFamily.LIGHTGBM, task=ModelTask.FORECAST, output_schema="forecast[1]"),
        CandidateSpec(
            name="finbert-family",
            family=ModelFamily.FINBERT,
            task=ModelTask.FINANCE_SENTIMENT,
            requires_transformers=True,
            output_schema="sentiment(label,confidence)",
            runtime_pin=_runtime_pin(
                candidate="finbert-family",
                project="transformers",
                version_or_commit="5.5.4",
                dependencies=("huggingface-hub==1.26.1", "torch=operator-selected"),
            ),
            external_checkpoint=CheckpointPin(
                model_family=ModelFamily.FINBERT.value,
                repository=_repo(
                    "ProsusAI/finbert",
                    "4556d13015211d73dccd3fdd39d39232506f3e43",
                    "not-declared",
                    ".gitattributes",
                    "README.md",
                    "config.json",
                    "pytorch_model.bin",
                    "special_tokens_map.json",
                    "tokenizer_config.json",
                    "vocab.txt",
                ),
                cache_path=_cache_path("finbert-family"),
            ),
        ),
        CandidateSpec(
            name="ttm-r2",
            family=ModelFamily.TTM_R2,
            task=ModelTask.FORECAST,
            requires_transformers=True,
            output_schema="forecast[1]",
            runtime_pin=_runtime_pin(
                candidate="ttm-r2",
                project="granite-tsfm",
                version_or_commit="0.3.6",
                dependencies=("transformers==5.5.4", "huggingface-hub==1.26.1"),
            ),
            external_checkpoint=CheckpointPin(
                model_family=ModelFamily.TTM_R2.value,
                repository=_repo(
                    "ibm-granite/granite-timeseries-ttm-r2",
                    "d6a79570cac0f33d526601cd3a0fc7c80a8f9a2f",
                    "apache-2.0",
                    ".gitattributes",
                    "README.md",
                    "benchmarks.webp",
                    "config.json",
                    "generation_config.json",
                    "model.safetensors",
                    "ttm_image.webp",
                ),
                cache_path=_cache_path("ttm-r2"),
                license_admission=_approved_license(
                    "Apache-2.0",
                    "https://huggingface.co/ibm-granite/granite-timeseries-ttm-r2",
                ),
            ),
        ),
        CandidateSpec(
            name="tspulse",
            family=ModelFamily.TSPULSE,
            task=ModelTask.TSPULSE_FEATURES,
            requires_transformers=True,
            output_schema="features[n]",
            notes="anomaly/integrity/regime features only; never a price forecaster",
            runtime_pin=_runtime_pin(
                candidate="tspulse",
                project="granite-tsfm",
                version_or_commit="0.3.6",
                dependencies=("transformers==5.5.4", "huggingface-hub==1.26.1"),
            ),
            external_checkpoint=CheckpointPin(
                model_family=ModelFamily.TSPULSE.value,
                repository=_repo(
                    "ibm-granite/granite-timeseries-tspulse-r1",
                    "2e64fcdc2a06d3565dfadaf0065c0ab5055f80f2",
                    "apache-2.0",
                    ".gitattributes",
                    "README.md",
                    "config.json",
                    "model.safetensors",
                    "tspulse_overview.webp",
                ),
                cache_path=_cache_path("tspulse"),
                license_admission=_approved_license(
                    "Apache-2.0",
                    "https://huggingface.co/ibm-granite/granite-timeseries-tspulse-r1",
                ),
            ),
        ),
        CandidateSpec(
            name="chronos-2-small",
            family=ModelFamily.CHRONOS_2_SMALL,
            task=ModelTask.FORECAST,
            requires_transformers=True,
            gpu=True,
            output_schema="forecast[1]",
            runtime_pin=_runtime_pin(
                candidate="chronos-2-small",
                project="chronos-forecasting",
                version_or_commit="2.3.1@7dc4435706a4454feb79df44ca9f33631f3027bf",
                dependencies=("transformers==5.5.4", "huggingface-hub==1.26.1"),
            ),
            external_checkpoint=CheckpointPin(
                model_family=ModelFamily.CHRONOS_2_SMALL.value,
                repository=_repo(
                    "autogluon/chronos-2-small",
                    "ddec01313e50b6bc58ebaa92ede81bc24a3d9f9a",
                    "apache-2.0",
                    ".gitattributes",
                    "README.md",
                    "config.json",
                    "model.safetensors",
                ),
                cache_path=_cache_path("chronos-2-small"),
                license_admission=_approved_license(
                    "Apache-2.0",
                    "https://huggingface.co/autogluon/chronos-2-small",
                ),
            ),
        ),
        CandidateSpec(
            name="kronos-mini",
            family=ModelFamily.KRONOS_MINI,
            task=ModelTask.FORECAST,
            gpu=True,
            output_schema="forecast[1]",
            repeatability_policy=RepeatabilityPolicy.STOCHASTIC_CHARACTERIZED,
            runtime_pin=_runtime_pin(
                candidate="kronos-mini",
                project="NeoQuasar/Kronos",
                version_or_commit="67b630e67f6a18c9e9be918d9b4337c960db1e9a",
                dependencies=(
                    "torch>=2.0.0",
                    "einops==0.8.1",
                    "huggingface-hub==0.33.1",
                    "pandas==2.2.2",
                    "safetensors==0.6.2",
                ),
            ),
            external_checkpoint=CheckpointPin(
                model_family=ModelFamily.KRONOS_MINI.value,
                repository=_repo(
                    "NeoQuasar/Kronos-mini",
                    "f4e68697d9d5aed55cef5c96aabc3376bcad9f81",
                    "mit",
                    ".gitattributes",
                    "README.md",
                    "config.json",
                    "model.safetensors",
                ),
                tokenizer=_repo(
                    "NeoQuasar/Kronos-Tokenizer-2k",
                    "26966d0035065a0cae0ebad7af8ece35bc1fb51c",
                    "mit",
                    ".gitattributes",
                    "README.md",
                    "config.json",
                    "model.safetensors",
                    cache_subdir="tokenizer",
                ),
                cache_path=_cache_path("kronos-mini"),
                license_admission=_approved_license(
                    "MIT",
                    "https://huggingface.co/NeoQuasar/Kronos-mini",
                ),
                tokenizer_license_admission=_approved_license(
                    "MIT",
                    "https://huggingface.co/NeoQuasar/Kronos-Tokenizer-2k",
                ),
            ),
        ),
        CandidateSpec(
            name="kronos-small",
            family=ModelFamily.KRONOS_SMALL,
            task=ModelTask.FORECAST,
            gpu=True,
            output_schema="forecast[1]",
            repeatability_policy=RepeatabilityPolicy.STOCHASTIC_CHARACTERIZED,
            runtime_pin=_runtime_pin(
                candidate="kronos-small",
                project="NeoQuasar/Kronos",
                version_or_commit="67b630e67f6a18c9e9be918d9b4337c960db1e9a",
                dependencies=(
                    "torch>=2.0.0",
                    "einops==0.8.1",
                    "huggingface-hub==0.33.1",
                    "pandas==2.2.2",
                    "safetensors==0.6.2",
                ),
            ),
            external_checkpoint=CheckpointPin(
                model_family=ModelFamily.KRONOS_SMALL.value,
                repository=_repo(
                    "NeoQuasar/Kronos-small",
                    "901c26c1332695a2a8f243eb2f37243a37bea320",
                    "mit",
                    ".gitattributes",
                    "README.md",
                    "config.json",
                    "model.safetensors",
                ),
                tokenizer=_repo(
                    "NeoQuasar/Kronos-Tokenizer-base",
                    "0e0117387f39004a9016484a186a908917e22426",
                    "mit",
                    ".gitattributes",
                    "README.md",
                    "config.json",
                    "model.safetensors",
                    cache_subdir="tokenizer",
                ),
                cache_path=_cache_path("kronos-small"),
                license_admission=_approved_license(
                    "MIT",
                    "https://huggingface.co/NeoQuasar/Kronos-small",
                ),
                tokenizer_license_admission=_approved_license(
                    "MIT",
                    "https://huggingface.co/NeoQuasar/Kronos-Tokenizer-base",
                ),
            ),
        ),
        CandidateSpec(
            name="tabpfn-ts",
            family=ModelFamily.TABPFN_TS,
            task=ModelTask.FORECAST,
            gpu=True,
            output_schema="forecast[1]",
            notes="later challenger; package checkpoint/license admission is separate",
            runtime_pin=_runtime_pin(
                candidate="tabpfn-ts",
                project="tabpfn-time-series",
                version_or_commit="a756ae3fb3af82c903c39e1cd71864ff5252bc4d",
                dependencies=(
                    "tabpfn-time-series==1.2.0",
                    "tabpfn==8.2.0",
                    "tabpfn-client==0.3.3",
                    "tabpfn-common-utils==0.2.23",
                ),
            ),
            external_checkpoint=CheckpointPin(
                model_family=ModelFamily.TABPFN_TS.value,
                repository=_repo(
                    "PriorLabs/tabpfn-time-series",
                    "a756ae3fb3af82c903c39e1cd71864ff5252bc4d",
                    "apache-2.0",
                    "README.md",
                    "pyproject.toml",
                ),
                cache_path=_cache_path("tabpfn-ts"),
                license_admission=_approved_license(
                    "Apache-2.0",
                    "https://github.com/PriorLabs/tabpfn-time-series",
                ),
            ),
        ),
    )


def forecast_baseline_models() -> dict[str, object]:
    """Construct mandatory baselines without touching any model runner."""

    return {
        "naive": NaiveForecaster(),
        "drift": DriftForecaster(),
        "seasonal": SeasonalForecaster(period=4),
        "linear": LinearForecaster(),
        "lightgbm": LightGBMBaseline(),
    }


def run_forecast_baseline_benchmark(
    dataset: BenchmarkDataset,
    *,
    horizon: int = 1,
) -> tuple[ForecastEvaluation, ...]:
    """Evaluate mandatory deterministic baselines on one synthetic/data interface.

    The returned records are measurements only; callers must not interpret a
    single dataset as model superiority or admission evidence.
    """

    if dataset.task != ModelTask.FORECAST:
        raise ValueError("forecast benchmark requires a forecast dataset")
    values = tuple(float(value) for value in dataset.inputs)
    actuals = tuple(float(value) for value in dataset.targets)
    if len(values) < 8:
        raise ValueError("forecast benchmark requires at least eight observations")
    predictions_by_model: dict[str, list[float]] = {name: [] for name in forecast_baseline_models()}
    actual_eval: list[float] = []
    models = forecast_baseline_models()
    for index in range(8, len(values)):
        history = tuple(__import__("decimal").Decimal(str(item)) for item in values[:index])
        for name, model in models.items():
            predictions_by_model[name].append(float(model.predict(history, horizon)[0]))  # type: ignore[attr-defined]
        actual_eval.append(actuals[index])
    baseline_utility = __import__("decimal").Decimal("-Infinity")
    # The deterministic evaluation contract requires a finite utility baseline;
    # use the worst measured utility as the comparison floor.
    evaluations: list[ForecastEvaluation] = []
    temporary: list[ForecastEvaluation] = []
    for name, predictions in predictions_by_model.items():
        temporary.append(
            evaluate_forecasts(
                model_name=name,
                predictions=tuple(__import__("decimal").Decimal(str(item)) for item in predictions),
                actuals=tuple(__import__("decimal").Decimal(str(item)) for item in actual_eval),
                baseline_utility=__import__("decimal").Decimal("0"),
                baseline_name="mandatory-baseline-set",
            )
        )
    baseline_utility = min(item.net_utility_after_costs for item in temporary)
    for item in temporary:
        evaluations.append(item.model_copy(update={"adds_marginal_value": False}))
    del baseline_utility
    return tuple(ForecastEvaluation.model_validate(item.model_dump()) for item in evaluations)


def manifest_bytes(result: RuntimeQualificationResult) -> bytes:
    """Canonical sanitized bytes used for immutable evidence and hashing."""

    data = result.model_dump(mode="json", exclude={"manifest_hash"})
    return (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write_qualification_manifest(result: RuntimeQualificationResult, output_dir: Path) -> Path:
    """Write one immutable candidate manifest; refuse to overwrite changed evidence."""

    digest = sha256(manifest_bytes(result)).hexdigest()
    completed = RuntimeQualificationResult.model_validate(
        {**result.model_dump(), "manifest_hash": digest}
    )
    filename = "".join(character if character.isalnum() or character in "-_" else "_" for character in result.candidate.name)
    path = output_dir / f"{filename}.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(completed.model_dump(mode="json"), sort_keys=True, indent=2) + "\n").encode()
    if path.exists() and path.read_bytes() != payload:
        raise FileExistsError(f"immutable evidence already exists with different content: {path}")
    path.write_bytes(payload)
    return path


def write_qualification_bundle(
    results: Sequence[RuntimeQualificationResult],
    output_dir: Path,
) -> tuple[Path, ...]:
    """Write candidate manifests and a sanitized index without model weights."""

    paths = tuple(write_qualification_manifest(result, output_dir) for result in results)
    generated_at = max(
        (result.measured_at for result in results),
        default=datetime.now(UTC),
    ).isoformat()
    index = {
        "schema": "advisorai.phase0.model-runtime-qualification.v1",
        "generated_at": generated_at,
        "candidate_manifests": [
            {"candidate": result.candidate.name, "path": path.name, "sha256": sha256(path.read_bytes()).hexdigest()}
            for result, path in zip(results, paths, strict=True)
        ],
        "no_model_weights_committed": True,
        "stability_24h_run": False,
    }
    index_path = output_dir / "index.json"
    index_payload = (json.dumps(index, sort_keys=True, indent=2) + "\n").encode()
    if index_path.exists() and index_path.read_bytes() != index_payload:
        raise FileExistsError(f"immutable evidence already exists with different content: {index_path}")
    index_path.write_bytes(index_payload)
    return (*paths, index_path)


def project_bakeoff_gate(results: Sequence[RuntimeQualificationResult]):
    """Keep short qualification evidence pending in the existing Phase-0 gate."""

    from advisorai.phase0.bakeoffs import BakeoffGate

    projected = tuple(result.to_bakeoff_result() for result in results)
    return BakeoffGate(
        selected_components=(),
        results=projected,
        exact_versions_reproducible=all(
            result.status == QualificationStatus.MEASURED for result in results
        ),
        unexplained_memory_growth=False,
        decision="pending",
    )
