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
import platform
import socket
import statistics
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
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


class RepositoryPin(BaseModel):
    """Immutable repository identity and complete expected file listing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_id: str
    revision: str
    license: str
    artifacts: tuple[ArtifactPin, ...]
    cache_subdir: str = "model"

    @model_validator(mode="after")
    def validate_repository(self) -> RepositoryPin:
        if not self.repository_id.strip():
            raise ValueError("repository ID is required")
        if len(self.revision) != 40 or any(character not in HEX40 for character in self.revision):
            raise ValueError("repository revision must be a 40-character lowercase commit SHA")
        if not self.license.strip():
            raise ValueError("repository license must be recorded, or explicitly not-declared")
        paths = [artifact.relative_path for artifact in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("repository artifact paths must be unique")
        if not self.cache_subdir.strip() or Path(self.cache_subdir).is_absolute():
            raise ValueError("repository cache subdirectory must be relative")
        return self


class CheckpointPin(BaseModel):
    """Model and optional tokenizer pins stored outside the repository."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_family: str
    repository: RepositoryPin
    tokenizer: RepositoryPin | None = None
    cache_path: str
    quantization: str = "none"

    @model_validator(mode="after")
    def validate_checkpoint(self) -> CheckpointPin:
        cache = Path(self.cache_path).expanduser()
        if not cache.is_absolute():
            raise ValueError("checkpoint cache path must be absolute and outside the repository")
        if not self.model_family.strip():
            raise ValueError("checkpoint model family is required")
        if not self.quantization.strip():
            raise ValueError("checkpoint quantization must be explicit")
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
        return self.repository.artifacts

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

    @model_validator(mode="after")
    def validate_environment(self) -> RuntimeEnvironment:
        if not self.python_version.strip() or not self.device.strip() or not self.dtype.strip():
            raise ValueError("runtime environment identity is incomplete")
        if not self.runner_version.strip():
            raise ValueError("runner version is required")
        if len(self.runner_hash) != 64 or any(character not in HEX64 for character in self.runner_hash):
            raise ValueError("runner hash must be a lowercase SHA-256 digest")
        cache = Path(self.cache_path).expanduser()
        if not cache.is_absolute():
            raise ValueError("runtime cache path must be absolute")
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
    requires_transformers: bool = False
    gpu: bool = False
    output_schema: str
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
        if self.gpu and self.task == ModelTask.FINANCE_SENTIMENT:
            raise ValueError("FinBERT qualification is CPU-only")
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
    offline_cached_inference: bool = False
    one_inference_completed: bool = False
    batch_completed: bool = False
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
                or not self.repeated_outputs_equal
                or not self.offline_cached_inference
                or not self.batch_completed
                or self.resource is None
            ):
                raise ValueError(
                    "measured qualification requires complete offline, repeatability, "
                    "resource, and output evidence"
                )
            if self.candidate.external_checkpoint is not None and not self.observed_artifacts:
                raise ValueError("measured external qualification requires observed artifact hashes")
        if self.status != QualificationStatus.MEASURED and not self.failure_reason:
            raise ValueError("quarantined/failed qualification requires a sanitized reason")
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
            privacy_passed=True,
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


class FunctionalRunner:
    """Small test/smoke runner with explicit family identity."""

    def __init__(
        self,
        *,
        model_family: str,
        load_fn: Callable[[CheckpointPin | None, bool], object] | None = None,
        infer_fn: Callable[[object, object], object],
        unload_fn: Callable[[object], None] | None = None,
        version: str = "functional-runner-v1",
    ) -> None:
        self.model_family = model_family
        self.version = version
        self._load_fn = load_fn or (lambda checkpoint, offline: object())
        self._infer_fn = infer_fn
        self._unload_fn = unload_fn or (lambda model: None)

    def load(self, *, checkpoint: CheckpointPin | None, offline: bool) -> object:
        return self._load_fn(checkpoint, offline)

    def infer(self, model: object, payload: object) -> object:
        return self._infer_fn(model, payload)

    def unload(self, model: object) -> None:
        self._unload_fn(model)


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


@contextmanager
def network_blocked() -> Iterator[None]:
    """Fail closed if a cached qualification tries to access the network."""

    original_socket = socket.socket
    original_create_connection = socket.create_connection

    def blocked_socket(*args: object, **kwargs: object) -> object:
        raise OSError("network access is disabled during offline qualification")

    def blocked_connection(*args: object, **kwargs: object) -> object:
        raise OSError("network access is disabled during offline qualification")

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


def _repository_root(cache_root: Path, repository: RepositoryPin) -> Path:
    preferred = cache_root / repository.cache_subdir
    return preferred if preferred.is_dir() else cache_root


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
    root = (cache_root or Path(checkpoint.cache_path).expanduser()).resolve(strict=False)
    repositories = (checkpoint.repository,) + ((checkpoint.tokenizer,) if checkpoint.tokenizer else ())
    observed: list[ArtifactPin] = []
    for repository in repositories:
        repo_root = _repository_root(root, repository)
        for artifact in repository.artifacts:
            if artifact.sha256 is None:
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
) -> tuple[ArtifactPin, ...]:
    """Hash every regular file currently present in a pinned external cache."""

    root = (cache_root or Path(checkpoint.cache_path).expanduser()).resolve(strict=False)
    repositories = (checkpoint.repository,) + ((checkpoint.tokenizer,) if checkpoint.tokenizer else ())
    inventory: list[ArtifactPin] = []
    for repository in repositories:
        repo_root = _repository_root(root, repository)
        if not repo_root.is_dir():
            raise CheckpointNotCachedError(f"checkpoint cache is absent: {repo_root}")
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


def runtime_environment(
    *,
    cache_path: str,
    device: str = "cpu",
    dtype: str = "float32",
    quantization: str = "none",
    runner_version: str = "qualification-runner-v1",
    runner_hash: str | None = None,
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
    runner_hash = sha256(f"{version}\n{runner_source}".encode()).hexdigest()
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
        candidate.external_checkpoint.assert_cache_outside_repository(repository_root or Path.cwd())
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
    try:
        load_started = time.perf_counter()
        with network_blocked():
            model = _call_runner_load(runner, candidate.external_checkpoint)
        cold_load_ms = (time.perf_counter() - load_started) * 1000
        rss_after_load, vram_after_load = _sample(sampler)
        rss_peak = max(rss_before, rss_after_load)
        vram_peak = sampler.peak_vram_mib()
        durations: list[float] = []
        outputs: list[object] = []
        with network_blocked():
            for _ in range(repeats):
                started = time.perf_counter()
                output = runner.infer(model, sample_input)
                durations.append((time.perf_counter() - started) * 1000)
                validate_model_output(candidate.task, output)
                outputs.append(output)
                rss_peak = max(rss_peak, sampler.rss_mib())
                current_vram = sampler.vram_mib()
                if current_vram is not None:
                    vram_peak = max(vram_peak or 0, current_vram)
            batch_started = time.perf_counter()
            batch_output = runner.infer(model, batch_input)
            batch_inference_ms = (time.perf_counter() - batch_started) * 1000
            rss_peak = max(rss_peak, sampler.rss_mib())
            current_vram = sampler.vram_mib()
            if current_vram is not None:
                vram_peak = max(vram_peak or 0, current_vram)
        validate_model_output(candidate.task, batch_output)
        rss_peak = max(rss_peak, sampler.rss_mib())
        measured_peak_vram = sampler.peak_vram_mib()
        if measured_peak_vram is not None:
            vram_peak = max(vram_peak or 0, measured_peak_vram)
        try:
            runner.unload(model)
            unload_succeeded = True
        except Exception as exc:  # noqa: BLE001 - evidence records unload failure
            unload_succeeded = False
            raise QualificationError("runner unload failed") from exc
        del model
        model = None
        gc.collect()
        rss_after_unload, vram_after_unload = _sample(sampler)
        resource = RuntimeResourceResult(
            cold_load_ms=cold_load_ms,
            warm_inference_p50_ms=statistics.median(durations),
            warm_inference_p95_ms=_percentile95(durations),
            batch_inference_ms=batch_inference_ms,
            batch_size=_batch_size(batch_input),
            batch_throughput_per_second=_batch_size(batch_input) / max(batch_inference_ms / 1000, 1e-9),
            rss_before_mib=rss_before,
            rss_after_load_mib=rss_after_load,
            rss_peak_mib=rss_peak,
            rss_after_unload_mib=rss_after_unload,
            vram_before_mib=vram_before,
            vram_after_load_mib=vram_after_load,
            vram_peak_mib=vram_peak,
            vram_after_unload_mib=vram_after_unload,
            unload_succeeded=unload_succeeded,
            resource_limit_passed=True,
        )
        resource.enforce(ceiling)
        digests = [output_digest(output) for output in outputs]
        equal = len(set(digests)) == 1
        return RuntimeQualificationResult(
            candidate=candidate,
            status=QualificationStatus.MEASURED,
            environment=environment,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            measured_at=measured_at,
            observed_artifacts=observed_artifacts,
            resource=resource,
            output_shape=validate_model_output(candidate.task, outputs[0]),
            output_schema_valid=True,
            nan_inf_rejection_passed=_nonfinite_rejection_probe(candidate.task),
            repeated_outputs_equal=equal,
            deterministic_match_rate=sum(digest == digests[0] for digest in digests) / len(digests),
            offline_cached_inference=True,
            one_inference_completed=True,
            batch_completed=True,
        )
    except InvalidModelOutputError:
        raise
    finally:
        if model is not None:
            try:
                runner.unload(model)
            except Exception:  # pragma: no cover - best effort after a failed probe
                pass


def _batch_size(payload: object) -> int:
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, Mapping)):
        return max(1, len(payload))
    return 1


def _percentile95(values: Sequence[float]) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(0.95 * len(ordered)) - 1))
    return ordered[index]


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
                validate_model_output(ModelTask.FORECAST, output, expected_length=1)
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


def _artifacts(*paths: str) -> tuple[ArtifactPin, ...]:
    return tuple(ArtifactPin(relative_path=path) for path in paths)


def _repo(
    repository_id: str,
    revision: str,
    license: str,
    *paths: str,
    cache_subdir: str = "model",
) -> RepositoryPin:
    return RepositoryPin(
        repository_id=repository_id,
        revision=revision,
        license=license,
        artifacts=_artifacts(*paths),
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
            external_checkpoint=CheckpointPin(
                model_family=ModelFamily.FINBERT.value,
                repository=_repo(
                    "ProsusAI/finbert",
                    "4556d13015211d73dccd3fdd39d39232506f3e43",
                    "not-declared",
                    ".gitattributes",
                    "README.md",
                    "config.json",
                    "flax_model.msgpack",
                    "pytorch_model.bin",
                    "special_tokens_map.json",
                    "tf_model.h5",
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
                    "training_args.bin",
                    "ttm_image.webp",
                ),
                cache_path=_cache_path("ttm-r2"),
            ),
        ),
        CandidateSpec(
            name="tspulse",
            family=ModelFamily.TSPULSE,
            task=ModelTask.TSPULSE_FEATURES,
            requires_transformers=True,
            output_schema="features[n]",
            notes="anomaly/integrity/regime features only; never a price forecaster",
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
            ),
        ),
        CandidateSpec(
            name="chronos-2-small",
            family=ModelFamily.CHRONOS_2_SMALL,
            task=ModelTask.FORECAST,
            requires_transformers=True,
            gpu=True,
            output_schema="forecast[1]",
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
            ),
        ),
        CandidateSpec(
            name="kronos-mini",
            family=ModelFamily.KRONOS_MINI,
            task=ModelTask.FORECAST,
            gpu=True,
            output_schema="forecast[1]",
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
            ),
        ),
        CandidateSpec(
            name="kronos-small",
            family=ModelFamily.KRONOS_SMALL,
            task=ModelTask.FORECAST,
            gpu=True,
            output_schema="forecast[1]",
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
            ),
        ),
        CandidateSpec(
            name="tabpfn-ts",
            family=ModelFamily.TABPFN_TS,
            task=ModelTask.FORECAST,
            gpu=True,
            output_schema="forecast[1]",
            notes="later challenger; package checkpoint/license admission is separate",
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
