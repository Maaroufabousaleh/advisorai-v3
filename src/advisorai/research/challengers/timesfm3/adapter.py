"""Lazy, scoped TimesFM 3 adapter for research qualification only."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.contracts import Forecast
from advisorai.models.forecasting import GpuModelLease

from .governance import (
    TIMESFM3_CHECKPOINT,
    TimesFM3ActivationScope,
    TimesFM3Governance,
    assert_timesfm3_activation_allowed,
    load_timesfm3_governance,
)
from .pit import (
    CovariateRole,
    TimesFM3ResearchInput,
    validate_timesfm3_point_in_time_input,
)

TIMESFM3_QUANTILE_LEVELS = (
    Decimal("0.1"),
    Decimal("0.2"),
    Decimal("0.3"),
    Decimal("0.4"),
    Decimal("0.5"),
    Decimal("0.6"),
    Decimal("0.7"),
    Decimal("0.8"),
    Decimal("0.9"),
)
TIMESFM3_ADAPTER_CODE_ID = "advisorai-timesfm3-research-adapter-v1"


class TimesFM3ModelUnavailable(RuntimeError):
    """Raised when the explicit research runtime/checkpoint is not available."""


class TimesFM3RuntimeConfig(BaseModel):
    """Configuration for an isolated optional TimesFM 3 environment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    checkpoint: Literal[TIMESFM3_CHECKPOINT] = TIMESFM3_CHECKPOINT
    local_checkpoint_path: Path | None = None
    allow_model_download: bool = False
    cache_dir: Path | None = None
    device: str = "cuda"
    per_core_batch_size: int = Field(default=1, gt=0)
    require_gpu_lease: bool = True

    @field_validator("device")
    @classmethod
    def require_device(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("TimesFM3 runtime device is required")
        return value.strip()


class TimesFM3RawOutput(BaseModel):
    """Backend-neutral output used by tests and the optional native runner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    point_forecasts: tuple[tuple[Decimal, ...], ...]
    quantile_forecasts: tuple[tuple[tuple[Decimal, ...], ...], ...] = ()

    @field_validator("point_forecasts")
    @classmethod
    def require_points(
        cls, value: tuple[tuple[Decimal, ...], ...]
    ) -> tuple[tuple[Decimal, ...], ...]:
        if not value or any(not row for row in value):
            raise ValueError("TimesFM3 output must contain one non-empty forecast per variate")
        if any(not item.is_finite() for row in value for item in row):
            raise ValueError("TimesFM3 point forecasts must be finite")
        lengths = {len(row) for row in value}
        if len(lengths) != 1:
            raise ValueError("TimesFM3 variate forecasts must have the same horizon")
        return value

    @model_validator(mode="after")
    def validate_quantiles(self) -> TimesFM3RawOutput:
        if self.quantile_forecasts:
            if len(self.quantile_forecasts) != len(self.point_forecasts):
                raise ValueError("TimesFM3 quantiles must match the variate count")
            horizon = len(self.point_forecasts[0])
            for variate in self.quantile_forecasts:
                if len(variate) != horizon or any(
                    len(step) != len(TIMESFM3_QUANTILE_LEVELS) for step in variate
                ):
                    raise ValueError("TimesFM3 quantile shape does not match the forecast")
                if any(not item.is_finite() for step in variate for item in step):
                    raise ValueError("TimesFM3 quantiles must be finite")
        return self


class TimesFM3ForecastArtifact(BaseModel):
    """Forecast-artifact-compatible research evidence with explicit isolation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    forecasts: tuple[Forecast, ...]
    full_point_forecasts: tuple[tuple[Decimal, ...], ...]
    full_quantile_forecasts: tuple[tuple[tuple[Decimal, ...], ...], ...] = ()
    input_snapshot_id: str
    cutoff: datetime
    target_variates: tuple[str, ...]
    covariate_roles: tuple[CovariateRole, ...]
    pit_contract_hash: str = Field(min_length=64, max_length=64)
    model: Literal[TIMESFM3_CHECKPOINT] = TIMESFM3_CHECKPOINT
    license_class: Literal["research_only_nonproduction"] = "research_only_nonproduction"
    production_eligible: Literal[False] = False
    execution_eligible: Literal[False] = False
    automatic_promotion_allowed: Literal[False] = False

    @field_validator("cutoff")
    @classmethod
    def require_aware_cutoff(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("TimesFM3 forecast cutoff must include a timezone")
        return value.astimezone(UTC)

    @field_validator("pit_contract_hash")
    @classmethod
    def require_lower_hash(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("TimesFM3 PIT contract hash must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def validate_artifact(self) -> TimesFM3ForecastArtifact:
        if len(self.forecasts) != len(self.full_point_forecasts) != len(self.target_variates):
            raise ValueError("TimesFM3 forecast artifact variate counts must agree")
        return self


TimesFM3ResearchRunner = Callable[[TimesFM3ResearchInput], TimesFM3RawOutput]


class TimesFM3ResearchAdapter:
    """Explicit TimesFM 3 adapter that can only emit research evidence.

    The optional ``timesfm3`` package is imported only by :meth:`load`, and a
    native model is unloaded after every :meth:`forecast` call.  Tests and
    offline qualification use an injected runner, so they never download a
    checkpoint or import PyTorch.
    """

    name = TIMESFM3_CHECKPOINT
    research_only = True

    def __init__(
        self,
        *,
        runner: TimesFM3ResearchRunner | None = None,
        runtime: TimesFM3RuntimeConfig | None = None,
        governance: TimesFM3Governance | None = None,
    ) -> None:
        self.runtime = runtime or TimesFM3RuntimeConfig()
        self.governance = governance or load_timesfm3_governance()
        self._runner = runner
        self._injected_runner = runner is not None
        self._native_model: Any | None = None
        self._resident = False

    @property
    def model_resident(self) -> bool:
        return self._resident

    @property
    def execution_authority_present(self) -> bool:
        return False

    @property
    def order_authority_present(self) -> bool:
        return False

    @property
    def self_promotion_allowed(self) -> bool:
        return False

    def activate(
        self, scope: TimesFM3ActivationScope | str = TimesFM3ActivationScope.RESEARCH
    ) -> None:
        """Validate scope; no import, download, or model residency occurs here."""

        assert_timesfm3_activation_allowed(scope, governance=self.governance)

    def acquire_checkpoint(self, *, cache_dir: Path | None = None) -> Path:
        """Explicitly opt into checkpoint acquisition in the research environment."""

        self.activate()
        if not self.runtime.allow_model_download:
            raise TimesFM3ModelUnavailable(
                "TimesFM3 checkpoint acquisition is disabled; set allow_model_download explicitly"
            )
        try:
            hub = importlib.import_module("huggingface_hub")
        except ImportError as exc:
            raise TimesFM3ModelUnavailable(
                "huggingface_hub is available only in the isolated TimesFM3 environment"
            ) from exc
        destination = cache_dir or self.runtime.cache_dir
        downloaded = hub.snapshot_download(
            repo_id=TIMESFM3_CHECKPOINT,
            cache_dir=str(destination) if destination else None,
        )
        return Path(downloaded)

    def load(self) -> None:
        """Load a local checkpoint or explicitly acquired checkpoint on demand."""

        self.activate()
        if self._runner is not None:
            return
        checkpoint = self.runtime.local_checkpoint_path
        if checkpoint is None:
            if not self.runtime.allow_model_download:
                raise TimesFM3ModelUnavailable(
                    "TimesFM3 requires an explicit local checkpoint or opt-in acquisition"
                )
            checkpoint = self.acquire_checkpoint()
        checkpoint = checkpoint.expanduser().resolve(strict=False)
        if not checkpoint.exists():
            raise TimesFM3ModelUnavailable(f"TimesFM3 local checkpoint is missing: {checkpoint}")
        try:
            module = importlib.import_module("timesfm3")
        except ImportError as exc:
            raise TimesFM3ModelUnavailable(
                "the optional timesfm3 package is not installed in the research environment"
            ) from exc
        try:
            forecaster = module.TimesFM3Forecaster.from_pretrained(str(checkpoint))
        except Exception as exc:
            raise TimesFM3ModelUnavailable("TimesFM3 local checkpoint could not be loaded") from exc
        self._native_model = forecaster
        self._runner = _TimesFM3LibraryRunner(forecaster)
        self._resident = True

    def forecast(
        self,
        research_input: TimesFM3ResearchInput,
    ) -> TimesFM3ForecastArtifact:
        """Run one bounded research call and release any model/GPU resources."""

        validated = validate_timesfm3_point_in_time_input(research_input)
        self.activate()
        lease = GpuModelLease("timesfm3") if self.runtime.require_gpu_lease else nullcontext()
        try:
            with lease:
                if self._runner is None:
                    self.load()
                assert self._runner is not None
                self._resident = True
                raw = self._runner(validated)
                raw_payload = raw.model_dump(mode="python") if isinstance(raw, BaseModel) else raw
                raw = TimesFM3RawOutput.model_validate(raw_payload)
                return self._build_artifact(validated, raw)
        finally:
            if not self._injected_runner:
                self.close()
            else:
                self._resident = False

    def close(self) -> None:
        """Release the native model and clear the optional CUDA cache if present."""

        model = self._native_model
        self._native_model = None
        if model is not None:
            close = getattr(model, "close", None)
            if callable(close):
                close()
        if not self._injected_runner:
            self._runner = None
        self._resident = False
        if model is None:
            return
        try:
            torch = importlib.import_module("torch")
        except ImportError:
            return
        cuda = getattr(torch, "cuda", None)
        empty_cache = getattr(cuda, "empty_cache", None)
        if callable(empty_cache):
            empty_cache()

    def _build_artifact(
        self,
        research_input: TimesFM3ResearchInput,
        raw: TimesFM3RawOutput,
    ) -> TimesFM3ForecastArtifact:
        data_hash = research_input.canonical_pit_hash()
        feature_hash = sha256(
            "|".join(sorted(series.role.value for series in research_input.covariates)).encode()
        ).hexdigest()
        code_hash = sha256(TIMESFM3_ADAPTER_CODE_ID.encode()).hexdigest()
        forecasts: list[Forecast] = []
        for index, target in enumerate(research_input.target_variates):
            first_step_quantiles = ()
            if raw.quantile_forecasts:
                first_step_quantiles = tuple(
                    (level, raw.quantile_forecasts[index][0][level_index])
                    for level_index, level in enumerate(TIMESFM3_QUANTILE_LEVELS)
                )
            forecasts.append(
                Forecast(
                    instrument=target.instrument,
                    snapshot_id=research_input.snapshot_id,
                    cutoff=research_input.cutoff,
                    horizon_seconds=research_input.interval_seconds * research_input.horizon_bars,
                    target=target.name,
                    point_forecast=raw.point_forecasts[index][0],
                    quantiles=first_step_quantiles,
                    confidence=Decimal("0.5"),
                    model_version=TIMESFM3_CHECKPOINT,
                    data_hash=data_hash,
                    feature_hash=feature_hash,
                    code_hash=code_hash,
                    calibration_version=research_input.calibration_version,
                    training_cutoff=research_input.cutoff,
                    known_support_limits=("research_only", "no_execution", "no_self_promotion"),
                    latency_ms=0,
                    peak_ram_mib=0,
                    peak_vram_mib=0,
                )
            )
        return TimesFM3ForecastArtifact(
            forecasts=tuple(forecasts),
            full_point_forecasts=raw.point_forecasts,
            full_quantile_forecasts=raw.quantile_forecasts,
            input_snapshot_id=str(research_input.snapshot_id),
            cutoff=research_input.cutoff,
            target_variates=tuple(series.name for series in research_input.target_variates),
            covariate_roles=tuple(series.role for series in research_input.covariates),
            pit_contract_hash=data_hash,
        )


class _TimesFM3LibraryRunner:
    """Translate the isolated native library output into the testable contract."""

    def __init__(self, forecaster: Any) -> None:
        self.forecaster = forecaster

    def __call__(self, research_input: TimesFM3ResearchInput) -> TimesFM3RawOutput:
        try:
            numpy = importlib.import_module("numpy")
        except ImportError as exc:
            raise TimesFM3ModelUnavailable(
                "numpy is required only by the isolated TimesFM3 runtime"
            ) from exc

        targets = [
            [float(point.value) for point in series.points]
            for series in research_input.target_variates
        ]
        context_length = len(targets[0])
        if any(len(values) != context_length for values in targets):
            raise TimesFM3ModelUnavailable("TimesFM3 target context lengths must agree")
        target_array = numpy.asarray(
            targets[0] if len(targets) == 1 else targets, dtype=numpy.float32
        )
        past_only = self._covariate_array(
            research_input,
            CovariateRole.PAST_ONLY_COVARIATE,
            expected_length=context_length,
            numpy=numpy,
        )
        past_future = self._covariate_array(
            research_input,
            CovariateRole.KNOWN_FUTURE_COVARIATE,
            expected_length=context_length + research_input.horizon_bars,
            numpy=numpy,
        )
        try:
            output = self.forecaster.predict(
                target_array,
                horizon=research_input.horizon_bars,
                past_only_covariates=past_only,
                past_future_covariates=past_future,
                return_quantiles=True,
            )
        except Exception as exc:
            raise TimesFM3ModelUnavailable("TimesFM3 native forecast failed") from exc
        points = self._normalise_points(
            output.forecast, len(targets), research_input.horizon_bars, numpy
        )
        quantiles = self._normalise_quantiles(
            getattr(output, "quantiles", None), len(targets), research_input.horizon_bars, numpy
        )
        return TimesFM3RawOutput(point_forecasts=points, quantile_forecasts=quantiles)

    @staticmethod
    def _covariate_array(
        research_input: TimesFM3ResearchInput,
        role: CovariateRole,
        *,
        expected_length: int,
        numpy: Any,
    ) -> Any | None:
        series = [item for item in research_input.covariates if item.role is role]
        if not series:
            return None
        arrays = [[float(point.value) for point in item.points] for item in series]
        if any(len(values) != expected_length for values in arrays):
            raise TimesFM3ModelUnavailable(
                f"TimesFM3 {role.value} channels must have {expected_length} aligned points"
            )
        return numpy.asarray(arrays, dtype=numpy.float32)

    @staticmethod
    def _normalise_points(
        output: Any, variates: int, horizon: int, numpy: Any
    ) -> tuple[tuple[Decimal, ...], ...]:
        array = numpy.asarray(output)
        if array.ndim == 1:
            array = array[None, :]
        if tuple(array.shape) != (variates, horizon):
            raise TimesFM3ModelUnavailable("TimesFM3 native point forecast shape is invalid")
        return tuple(tuple(Decimal(str(value)) for value in row) for row in array.tolist())

    @staticmethod
    def _normalise_quantiles(
        output: Any,
        variates: int,
        horizon: int,
        numpy: Any,
    ) -> tuple[tuple[tuple[Decimal, ...], ...], ...]:
        if output is None:
            return ()
        array = numpy.asarray(output)
        if array.ndim == 2:
            array = array[None, :, :]
        expected = (variates, horizon, len(TIMESFM3_QUANTILE_LEVELS))
        if tuple(array.shape) != expected:
            raise TimesFM3ModelUnavailable("TimesFM3 native quantile forecast shape is invalid")
        return tuple(
            tuple(tuple(Decimal(str(value)) for value in step) for step in variate)
            for variate in array.tolist()
        )
