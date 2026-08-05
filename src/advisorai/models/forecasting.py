"""Small, auditable forecast roster for Phase 4.

Baselines are implemented locally and always available. Large model families are
optional adapters: absence or unsupported input quarantines them rather than
silently substituting a correlated model.
"""

from __future__ import annotations

import importlib.util
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from threading import Lock
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.contracts import Forecast, InstrumentIdentity


class ModelUnavailable(RuntimeError):
    pass


class SeriesPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    at: datetime
    value: Decimal

    @field_validator("at")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("series timestamps must include a timezone")
        return value.astimezone(UTC)

    @field_validator("value")
    @classmethod
    def require_finite_value(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("series values must be finite")
        return value


class ForecastSeries(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument: InstrumentIdentity
    target: str
    points: tuple[SeriesPoint, ...]
    snapshot_id: UUID
    training_cutoff: datetime

    @field_validator("training_cutoff")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("training cutoff must include a timezone")
        return value.astimezone(UTC)

    def values(self) -> tuple[Decimal, ...]:
        return tuple(point.value for point in self.points)

    @model_validator(mode="after")
    def require_ordered_points(self) -> ForecastSeries:
        if not self.target.strip():
            raise ValueError("forecast series target is required")
        if not self.points:
            raise ValueError("forecast series requires at least one point")
        timestamps = [point.at for point in self.points]
        if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
            raise ValueError("forecast series points must be strictly time ordered")
        if timestamps[-1] > self.training_cutoff:
            raise ValueError("forecast series cannot contain points after training cutoff")
        return self


class BaselineForecaster:
    """Mandatory naive/drift/seasonal/linear baselines."""

    name: str = "baseline"

    def predict(self, values: Sequence[Decimal], horizon: int = 1) -> tuple[Decimal, ...]:
        raise NotImplementedError


class NaiveForecaster(BaselineForecaster):
    name = "naive"

    def predict(self, values: Sequence[Decimal], horizon: int = 1) -> tuple[Decimal, ...]:
        if not values or horizon < 1:
            raise ValueError("values and positive horizon are required")
        return (values[-1],) * horizon


class DriftForecaster(BaselineForecaster):
    name = "drift"

    def predict(self, values: Sequence[Decimal], horizon: int = 1) -> tuple[Decimal, ...]:
        if len(values) < 2 or horizon < 1:
            raise ValueError("drift requires two values and positive horizon")
        step = (values[-1] - values[0]) / Decimal(len(values) - 1)
        return tuple(values[-1] + step * Decimal(index) for index in range(1, horizon + 1))


class SeasonalForecaster(BaselineForecaster):
    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("seasonal period must be positive")
        self.period = period

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"seasonal-{self.period}"

    def predict(self, values: Sequence[Decimal], horizon: int = 1) -> tuple[Decimal, ...]:
        if len(values) < self.period or horizon < 1:
            raise ValueError("seasonal history is shorter than its period")
        return tuple(
            values[-self.period + ((index - 1) % self.period)] for index in range(1, horizon + 1)
        )


class LinearForecaster(BaselineForecaster):
    name = "linear"

    def predict(self, values: Sequence[Decimal], horizon: int = 1) -> tuple[Decimal, ...]:
        if len(values) < 2 or horizon < 1:
            raise ValueError("linear baseline requires two values and positive horizon")
        n = Decimal(len(values))
        x_mean = (n - 1) / 2
        y_mean = sum(values) / n
        denominator = sum((Decimal(index) - x_mean) ** 2 for index in range(len(values)))
        slope = (
            sum((Decimal(index) - x_mean) * (value - y_mean) for index, value in enumerate(values))
            / denominator
        )
        intercept = y_mean - slope * x_mean
        return tuple(
            intercept + slope * Decimal(len(values) + index - 1) for index in range(1, horizon + 1)
        )


class OptionalForecastAdapter(BaselineForecaster):
    """Gate-controlled adapter for a named model family.

    The runner is injected so model-specific checkpoint loading never leaks into
    the deterministic contract.  A missing dependency or runner remains
    quarantined instead of silently falling back to another model.
    """

    def __init__(
        self,
        name: str,
        import_name: str,
        *,
        gpu: bool = False,
        runner: Callable[[Sequence[Decimal], int], Sequence[Decimal]] | None = None,
        checkpoint_hash: str | None = None,
    ) -> None:
        self.name = name
        self.import_name = import_name
        self.gpu = gpu
        self.runner = runner
        self.checkpoint_hash = checkpoint_hash
        self.available = importlib.util.find_spec(import_name) is not None

    def predict(self, values: Sequence[Decimal], horizon: int = 1) -> tuple[Decimal, ...]:
        if not self.available:
            raise ModelUnavailable(f"{self.name} dependency {self.import_name!r} is unavailable")
        if self.runner is None or not self.checkpoint_hash:
            raise ModelUnavailable(
                f"{self.name} requires a pinned checkpoint hash and injected model runner"
            )
        self._validate_checkpoint_hash()
        if not values or horizon < 1:
            raise ValueError("values and positive horizon are required")
        predictions = tuple(self.runner(values, horizon))
        if len(predictions) != horizon or any(
            not isinstance(item, Decimal) or not item.is_finite() for item in predictions
        ):
            raise ModelUnavailable(f"{self.name} runner returned an invalid forecast shape")
        return predictions

    def _validate_checkpoint_hash(self) -> None:
        if (
            self.checkpoint_hash is None
            or len(self.checkpoint_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.checkpoint_hash)
        ):
            raise ModelUnavailable(f"{self.name} checkpoint hash is not a SHA-256 digest")


class LightGBMForecastAdapter(OptionalForecastAdapter):
    """LightGBM baseline boundary; never substitutes another model silently."""

    def __init__(
        self,
        *,
        runner: Callable[[Sequence[Decimal], int], Sequence[Decimal]] | None = None,
        checkpoint_hash: str | None = None,
    ) -> None:
        super().__init__("lightgbm", "lightgbm", runner=runner, checkpoint_hash=checkpoint_hash)


class TTMR2ForecastAdapter(OptionalForecastAdapter):
    """Pinned IBM TTM-R2 CPU candidate boundary."""

    def __init__(
        self,
        *,
        runner: Callable[[Sequence[Decimal], int], Sequence[Decimal]] | None = None,
        checkpoint_hash: str | None = None,
    ) -> None:
        super().__init__("ttm-r2", "transformers", runner=runner, checkpoint_hash=checkpoint_hash)


class TSPulseAdapter(OptionalForecastAdapter):
    """TSPulse integrity/regime candidate; not promoted as a price forecaster."""

    def __init__(
        self,
        *,
        runner: Callable[[Sequence[Decimal], int], Sequence[Decimal]] | None = None,
        checkpoint_hash: str | None = None,
    ) -> None:
        super().__init__("tspulse", "transformers", runner=runner, checkpoint_hash=checkpoint_hash)

    def predict(self, values: Sequence[Decimal], horizon: int = 1) -> tuple[Decimal, ...]:
        raise ModelUnavailable(
            "TSPulse is an integrity/regime feature adapter and cannot forecast prices"
        )

    def extract_features(self, values: Sequence[Decimal]) -> tuple[Decimal, ...]:
        if not self.available:
            raise ModelUnavailable(f"{self.name} dependency {self.import_name!r} is unavailable")
        if self.runner is None or not self.checkpoint_hash:
            raise ModelUnavailable(
                f"{self.name} requires a pinned checkpoint hash and injected feature runner"
            )
        if not values or any(not value.is_finite() for value in values):
            raise ValueError("TSPulse features require non-empty finite values")
        self._validate_checkpoint_hash()
        features = tuple(self.runner(values, 1))
        if not features or any(
            not isinstance(item, Decimal) or not item.is_finite() for item in features
        ):
            raise ModelUnavailable(f"{self.name} runner returned invalid integrity features")
        return features


class Chronos2SmallAdapter(OptionalForecastAdapter):
    """Chronos-2-small GPU challenger boundary."""

    def __init__(
        self,
        *,
        runner: Callable[[Sequence[Decimal], int], Sequence[Decimal]] | None = None,
        checkpoint_hash: str | None = None,
    ) -> None:
        super().__init__(
            "chronos-2-small", "chronos", gpu=True, runner=runner, checkpoint_hash=checkpoint_hash
        )


class KronosMiniSmallAdapter(OptionalForecastAdapter):
    """Kronos-mini/small GPU challenger boundary."""

    def __init__(
        self,
        *,
        runner: Callable[[Sequence[Decimal], int], Sequence[Decimal]] | None = None,
        checkpoint_hash: str | None = None,
    ) -> None:
        super().__init__(
            "kronos-mini-small", "kronos", gpu=True, runner=runner, checkpoint_hash=checkpoint_hash
        )


class TabPFNTSAdapter(OptionalForecastAdapter):
    """TabPFN-TS Deep-mode challenger boundary."""

    def __init__(
        self,
        *,
        runner: Callable[[Sequence[Decimal], int], Sequence[Decimal]] | None = None,
        checkpoint_hash: str | None = None,
    ) -> None:
        super().__init__(
            "tabpfn-ts",
            "tabpfn_time_series",
            gpu=True,
            runner=runner,
            checkpoint_hash=checkpoint_hash,
        )


class RollingCalibrator:
    """Stores past-only absolute-error scale and computes empirical coverage."""

    def __init__(self, window: int = 100) -> None:
        if window < 2:
            raise ValueError("calibration window must be at least two")
        self.window = window
        self._errors: list[Decimal] = []

    def update(self, predicted: Decimal, actual: Decimal) -> None:
        if not predicted.is_finite() or not actual.is_finite():
            raise ValueError("calibration values must be finite")
        self._errors.append(abs(predicted - actual))
        del self._errors[: -self.window]

    @property
    def scale(self) -> Decimal:
        if not self._errors:
            return Decimal("0")
        return sum(self._errors) / Decimal(len(self._errors))

    def interval(
        self, point: Decimal, multiplier: Decimal = Decimal("2")
    ) -> tuple[Decimal, Decimal]:
        if not point.is_finite() or not multiplier.is_finite() or multiplier < 0:
            raise ValueError("calibration interval multiplier cannot be negative")
        width = self.scale * multiplier
        return point - width, point + width


@dataclass(frozen=True, slots=True)
class AbstentionPolicy:
    max_scale: Decimal
    min_history: int = 2

    def __post_init__(self) -> None:
        if not self.max_scale.is_finite() or self.max_scale < 0 or self.min_history < 1:
            raise ValueError("abstention policy bounds are invalid")

    def should_abstain(self, *, history_length: int, calibration_scale: Decimal) -> bool:
        if not calibration_scale.is_finite() or calibration_scale < 0 or history_length < 0:
            raise ValueError("abstention inputs must be finite and non-negative")
        return history_length < self.min_history or calibration_scale > self.max_scale


class ForecastEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_name: str
    observations: int = Field(ge=0)
    mae: Decimal = Field(ge=0)
    rmse: Decimal = Field(ge=0)
    directional_accuracy: Decimal = Field(ge=0, le=1)
    net_utility_after_costs: Decimal
    turnover: Decimal = Field(ge=0)
    drawdown: Decimal = Field(ge=0)
    calibration_coverage: Decimal = Field(ge=0, le=1)
    adds_marginal_value: bool
    baseline_name: str = "mandatory_baseline"
    past_only: bool = True
    brier_score: Decimal | None = None
    log_score: Decimal | None = None
    rank_ic: Decimal | None = None
    error_correlation: Decimal | None = None
    regime_failures: tuple[str, ...] = ()
    latency_ms: int = Field(default=0, ge=0)
    peak_ram_mib: int = Field(default=0, ge=0)
    peak_vram_mib: int = Field(default=0, ge=0)
    resource_limit_passed: bool = True

    @model_validator(mode="after")
    def require_evaluation_identity(self) -> ForecastEvaluation:
        if not self.model_name.strip():
            raise ValueError("forecast evaluation requires a model name")
        if not self.baseline_name.strip():
            raise ValueError("forecast evaluation requires a baseline identity")
        optional_metrics = (
            self.brier_score,
            self.log_score,
            self.rank_ic,
            self.error_correlation,
        )
        if any(value is not None and not value.is_finite() for value in optional_metrics):
            raise ValueError("forecast evaluation metrics must be finite")
        if self.brier_score is not None and not Decimal("0") <= self.brier_score <= Decimal("1"):
            raise ValueError("Brier score must be between zero and one")
        if self.error_correlation is not None and not Decimal(
            "-1"
        ) <= self.error_correlation <= Decimal("1"):
            raise ValueError("error correlation must be between negative one and one")
        if self.rank_ic is not None and not Decimal("-1") <= self.rank_ic <= Decimal("1"):
            raise ValueError("rank IC must be between negative one and one")
        if any(not item.strip() for item in self.regime_failures):
            raise ValueError("forecast regime failures cannot be blank")
        return self


def build_forecast(
    *,
    series: ForecastSeries,
    model: BaselineForecaster,
    horizon_seconds: int,
    calibration_version: str,
    code_version: str,
    training_cutoff: datetime | None = None,
    cutoff: datetime | None = None,
    abstention_policy: AbstentionPolicy | None = None,
    latency_ms: int = 0,
    peak_ram_mib: int = 0,
    peak_vram_mib: int = 0,
) -> Forecast:
    values = series.values()
    effective_training_cutoff = training_cutoff or series.training_cutoff
    if effective_training_cutoff.tzinfo is None or effective_training_cutoff.utcoffset() is None:
        raise ValueError("training cutoff must include a timezone")
    effective_training_cutoff = effective_training_cutoff.astimezone(UTC)
    effective_cutoff = cutoff or effective_training_cutoff
    if effective_cutoff.tzinfo is None or effective_cutoff.utcoffset() is None:
        raise ValueError("forecast cutoff must include a timezone")
    effective_cutoff = effective_cutoff.astimezone(UTC)
    if effective_training_cutoff > effective_cutoff:
        raise ValueError("training cutoff cannot be after forecast cutoff")
    latest_point = max(point.at for point in series.points)
    if latest_point > effective_training_cutoff:
        raise ValueError("training cutoff cannot precede the latest forecast input")
    calibration = RollingCalibrator()
    for left, right in zip(values, values[1:], strict=False):
        calibration.update(left, right)
    abstained = (
        abstention_policy.should_abstain(
            history_length=len(values), calibration_scale=calibration.scale
        )
        if abstention_policy
        else False
    )
    prediction = None if abstained else model.predict(values, 1)[0]
    data_hash = sha256(series.model_dump_json().encode()).hexdigest()
    code_hash = sha256(code_version.encode()).hexdigest()
    feature_hash = sha256(f"{model.name}:{series.target}".encode()).hexdigest()
    return Forecast(
        instrument=series.instrument,
        snapshot_id=series.snapshot_id,
        cutoff=effective_cutoff,
        horizon_seconds=horizon_seconds,
        target=series.target,
        point_forecast=prediction,
        confidence=Decimal("0.5") if not abstained else Decimal("0"),
        abstained=abstained,
        abstention_reason=("calibration_uncertainty" if abstained else None),
        model_version=model.name,
        data_hash=data_hash,
        feature_hash=feature_hash,
        code_hash=code_hash,
        calibration_version=calibration_version,
        training_cutoff=effective_training_cutoff,
        latency_ms=latency_ms,
        peak_ram_mib=peak_ram_mib,
        peak_vram_mib=peak_vram_mib,
    )


def evaluate_forecasts(
    *,
    model_name: str,
    predictions: Sequence[Decimal],
    actuals: Sequence[Decimal],
    baseline_utility: Decimal,
    cost_bps: Decimal = Decimal("10"),
    baseline_name: str = "mandatory_baseline",
    past_only: bool = True,
    brier_score: Decimal | None = None,
    log_score: Decimal | None = None,
    rank_ic: Decimal | None = None,
    error_correlation: Decimal | None = None,
    regime_failures: tuple[str, ...] = (),
    latency_ms: int = 0,
    peak_ram_mib: int = 0,
    peak_vram_mib: int = 0,
    resource_limit_passed: bool = True,
) -> ForecastEvaluation:
    if len(predictions) != len(actuals) or not predictions:
        raise ValueError("predictions and actuals must be non-empty and equal length")
    if (
        not model_name.strip()
        or any(not value.is_finite() for value in (*predictions, *actuals))
        or not baseline_utility.is_finite()
        or not cost_bps.is_finite()
    ):
        raise ValueError("forecast evaluation values and model name must be valid")
    if cost_bps < 0:
        raise ValueError("cost_bps cannot be negative")
    errors = [prediction - actual for prediction, actual in zip(predictions, actuals, strict=True)]
    mae = sum(abs(error) for error in errors) / Decimal(len(errors))
    rmse = Decimal(
        str(math.sqrt(float(sum(error * error for error in errors) / Decimal(len(errors)))))
    )
    directions = [
        (prediction >= 0) == (actual >= 0)
        for prediction, actual in zip(predictions, actuals, strict=True)
    ]
    directional_accuracy = Decimal(sum(directions)) / Decimal(len(directions))
    signals = [Decimal("1") if prediction > 0 else Decimal("-1") for prediction in predictions]
    gross_utility = sum(signal * actual for signal, actual in zip(signals, actuals, strict=True))
    turnover = sum(abs(signal) for signal in signals) / Decimal(len(signals))
    net_utility = gross_utility - turnover * cost_bps / Decimal("10000")
    running = Decimal("0")
    peak = Decimal("0")
    drawdown = Decimal("0")
    for signal, actual in zip(signals, actuals, strict=True):
        running += signal * actual
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
    coverage = Decimal(sum(abs(error) <= rmse * Decimal("2") for error in errors)) / Decimal(
        len(errors)
    )
    return ForecastEvaluation(
        model_name=model_name,
        observations=len(actuals),
        mae=mae,
        rmse=rmse,
        directional_accuracy=directional_accuracy,
        net_utility_after_costs=net_utility,
        turnover=turnover,
        drawdown=drawdown,
        calibration_coverage=coverage,
        adds_marginal_value=(
            net_utility > baseline_utility and past_only and resource_limit_passed
        ),
        baseline_name=baseline_name,
        past_only=past_only,
        brier_score=brier_score,
        log_score=log_score,
        rank_ic=rank_ic,
        error_correlation=error_correlation,
        regime_failures=regime_failures,
        latency_ms=latency_ms,
        peak_ram_mib=peak_ram_mib,
        peak_vram_mib=peak_vram_mib,
        resource_limit_passed=resource_limit_passed,
    )


class GpuModelLease:
    """One global model-family lease; checkpoints cannot be co-resident by accident."""

    _lock = Lock()
    _active_family: str | None = None

    def __init__(self, family: str) -> None:
        if not family.strip():
            raise ValueError("GPU model family is required")
        self.family = family
        self._acquired = False

    def __enter__(self) -> GpuModelLease:
        owner = type(self)
        if not owner._lock.acquire(blocking=False):
            raise RuntimeError("GPU lease already held")
        if owner._active_family is not None:
            owner._lock.release()
            raise RuntimeError(f"GPU lease held by family {owner._active_family}")
        owner._active_family = self.family
        self._acquired = True
        owner._lock.release()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        owner = type(self)
        with owner._lock:
            if self._acquired and owner._active_family == self.family:
                owner._active_family = None
                self._acquired = False
