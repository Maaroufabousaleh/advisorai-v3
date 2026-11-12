"""Non-blocking TimesFM 3 qualification matrix and evidence interfaces."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from advisorai.models.forecasting import ForecastEvaluation, evaluate_forecasts

from .governance import TIMESFM3_CHECKPOINT


class TimesFM3Ablation(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


class TimesFM3AblationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ablation: TimesFM3Ablation
    targets: tuple[str, ...]
    variant: str
    past_only_covariates: tuple[str, ...] = ()
    known_future_covariates: tuple[str, ...] = ()
    marginal_comparison: str

    @model_validator(mode="after")
    def validate_ablation(self) -> TimesFM3AblationSpec:
        expected = {
            TimesFM3Ablation.A: (("BTCUSDT",), "univariate", (), (), "baseline → A"),
            TimesFM3Ablation.B: (
                ("BTCUSDT", "ETHUSDT"),
                "multivariate",
                (),
                (),
                "A → B",
            ),
            TimesFM3Ablation.C: (
                ("BTCUSDT", "ETHUSDT"),
                "multivariate",
                ("realized_volatility", "volume"),
                (),
                "B → C",
            ),
            TimesFM3Ablation.D: (
                ("BTCUSDT", "ETHUSDT"),
                "multivariate",
                ("realized_volatility", "volume", "funding", "open_interest", "basis"),
                (),
                "C → D",
            ),
            TimesFM3Ablation.E: (
                ("BTCUSDT", "ETHUSDT"),
                "multivariate",
                ("realized_volatility", "volume", "funding", "open_interest", "basis"),
                ("calendar",),
                "D → E",
            ),
        }[self.ablation]
        if (
            self.targets,
            self.variant,
            self.past_only_covariates,
            self.known_future_covariates,
            self.marginal_comparison,
        ) != expected:
            raise ValueError(
                f"TimesFM3 ablation {self.ablation.value} does not match its preregistered design"
            )
        return self


TIMESFM3_RESEARCH_TARGETS = (
    "return",
    "direction_calibration",
    "realized_volatility",
    "future_range",
    "volume_liquidity_state",
    "probabilistic_tail",
)

TIMESFM3_COMPARISON_SET = (
    "naive",
    "drift",
    "seasonal",
    "linear",
    "lightgbm",
    "ttm-r2",
    "ttm-r3",
    "chronos-2-small",
    "timesfm-3-univariate",
    "timesfm-3-multivariate",
)


def build_timesfm3_qualification_matrix() -> tuple[TimesFM3AblationSpec, ...]:
    """Return the fixed A→E design without running a model or downloading data."""

    return (
        TimesFM3AblationSpec(
            ablation=TimesFM3Ablation.A,
            targets=("BTCUSDT",),
            variant="univariate",
            marginal_comparison="baseline → A",
        ),
        TimesFM3AblationSpec(
            ablation=TimesFM3Ablation.B,
            targets=("BTCUSDT", "ETHUSDT"),
            variant="multivariate",
            marginal_comparison="A → B",
        ),
        TimesFM3AblationSpec(
            ablation=TimesFM3Ablation.C,
            targets=("BTCUSDT", "ETHUSDT"),
            variant="multivariate",
            past_only_covariates=("realized_volatility", "volume"),
            marginal_comparison="B → C",
        ),
        TimesFM3AblationSpec(
            ablation=TimesFM3Ablation.D,
            targets=("BTCUSDT", "ETHUSDT"),
            variant="multivariate",
            past_only_covariates=(
                "realized_volatility",
                "volume",
                "funding",
                "open_interest",
                "basis",
            ),
            marginal_comparison="C → D",
        ),
        TimesFM3AblationSpec(
            ablation=TimesFM3Ablation.E,
            targets=("BTCUSDT", "ETHUSDT"),
            variant="multivariate",
            past_only_covariates=(
                "realized_volatility",
                "volume",
                "funding",
                "open_interest",
                "basis",
            ),
            known_future_covariates=("calendar",),
            marginal_comparison="D → E",
        ),
    )


class TimesFM3EvaluationRequest(BaseModel):
    """Inputs required before TimesFM 3 evidence can be compared or reviewed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_variant: str
    ablation: TimesFM3Ablation
    predictions: tuple[Decimal, ...] = Field(min_length=1)
    actuals: tuple[Decimal, ...] = Field(min_length=1)
    baseline_utility: Decimal
    baseline_name: str = "mandatory_baseline"
    past_only: bool = True
    calibrated: bool = False
    regime_robust: bool = False
    realistic_costs_applied: bool = False
    latency_ms: int = Field(default=0, ge=0)
    peak_ram_mib: int = Field(default=0, ge=0)
    peak_vram_mib: int = Field(default=0, ge=0)
    resource_limit_passed: bool = False
    disagreement_score: Decimal | None = None

    @model_validator(mode="after")
    def validate_request(self) -> TimesFM3EvaluationRequest:
        if not self.model_variant.strip():
            raise ValueError("TimesFM3 evaluation requires a model variant")
        if len(self.predictions) != len(self.actuals):
            raise ValueError("TimesFM3 predictions and actuals must have equal lengths")
        if any(
            not value.is_finite()
            for value in (*self.predictions, *self.actuals, self.baseline_utility)
        ):
            raise ValueError("TimesFM3 evaluation values must be finite")
        if self.disagreement_score is not None and not self.disagreement_score.is_finite():
            raise ValueError("TimesFM3 disagreement score must be finite")
        return self


class TimesFM3QualificationEvidence(BaseModel):
    """Evidence record; never an admission or promotion decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = TIMESFM3_CHECKPOINT
    model_variant: str
    ablation: TimesFM3Ablation
    comparison_set: tuple[str, ...] = TIMESFM3_COMPARISON_SET
    research_targets: tuple[str, ...] = TIMESFM3_RESEARCH_TARGETS
    evaluation: ForecastEvaluation
    calibrated: bool
    regime_robust: bool
    realistic_costs_applied: bool
    disagreement_score: Decimal | None = None
    eligible_for_external_review: bool
    automatic_promotion: bool = False
    production_eligible: bool = False
    execution_eligible: bool = False


def evaluate_timesfm3(request: TimesFM3EvaluationRequest) -> TimesFM3QualificationEvidence:
    """Evaluate one controlled slice against a baseline without promoting it."""

    evaluation = evaluate_forecasts(
        model_name=request.model_variant,
        predictions=request.predictions,
        actuals=request.actuals,
        baseline_utility=request.baseline_utility,
        baseline_name=request.baseline_name,
        past_only=request.past_only,
        latency_ms=request.latency_ms,
        peak_ram_mib=request.peak_ram_mib,
        peak_vram_mib=request.peak_vram_mib,
        resource_limit_passed=request.resource_limit_passed,
    )
    eligible = all(
        (
            request.past_only,
            request.calibrated,
            request.regime_robust,
            request.realistic_costs_applied,
            request.resource_limit_passed,
            evaluation.adds_marginal_value,
        )
    )
    return TimesFM3QualificationEvidence(
        model_variant=request.model_variant,
        ablation=request.ablation,
        evaluation=evaluation,
        calibrated=request.calibrated,
        regime_robust=request.regime_robust,
        realistic_costs_applied=request.realistic_costs_applied,
        disagreement_score=request.disagreement_score,
        eligible_for_external_review=eligible,
    )
