"""Fail-closed Phase-4 paper-utility evaluation contracts.

This module is deliberately downstream of Phase 3.  It accepts only typed
observations whose source has already been admitted by a real Phase-3 gate and
keeps model predictions separate from execution.  The evaluator measures
utility; it never promotes a model, changes a roster, creates a gate record, or
submits an order.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

HEX = frozenset("0123456789abcdef")

# The names are bound to the current roster.  ``seasonal-7`` is intentional:
# the machine-readable roster uses the seven-period seasonal control even when
# plan prose abbreviates it to "seasonal".
MANDATORY_BASELINES = ("naive", "drift", "seasonal-7", "linear", "lightgbm")
CANDIDATE_MODELS = (
    "ttm-r2",
    "ttm-r3",
    "chronos-2-small",
    "kronos-mini",
    "kronos-small",
    "modern-finbert",
    "finance-deberta-v3",
    "finbert-minilm",
)


class Phase4PrerequisiteError(ValueError):
    """Raised when utility evaluation is attempted before Phase-3 admission."""


class Phase4EvaluationState(StrEnum):
    READY_FOR_ADMITTED_INPUT = "ready_for_admitted_input"
    MEASURED_PENDING_REVIEW = "measured_pending_review"


def _digest(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(character not in HEX for character in normalized):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return normalized


def _finite_decimal(value: Decimal, field_name: str) -> Decimal:
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return value


class Phase4MarketObservation(BaseModel):
    """One point-in-time observation paired with a later realized return."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: str = Field(min_length=1)
    instrument: str = Field(min_length=1)
    cutoff: datetime
    realized_at: datetime
    realized_return_bps: Decimal
    spread_bps: Decimal = Field(ge=0)
    slippage_bps: Decimal = Field(ge=0)
    regime: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    provider_identity: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    source_snapshot_hash: str
    phase3_admitted: bool = False

    @field_validator("cutoff", "realized_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Phase-4 observation timestamps must include a timezone")
        return value.astimezone(UTC)

    @field_validator("realized_return_bps")
    @classmethod
    def require_finite_return(cls, value: Decimal) -> Decimal:
        return _finite_decimal(value, "realized_return_bps")

    @field_validator("spread_bps", "slippage_bps")
    @classmethod
    def require_finite_cost(cls, value: Decimal) -> Decimal:
        return _finite_decimal(value, "observation cost")

    @field_validator("source_snapshot_hash")
    @classmethod
    def require_snapshot_hash(cls, value: str) -> str:
        return _digest(value, "source_snapshot_hash")

    @model_validator(mode="after")
    def validate_temporal_and_source_identity(self) -> Phase4MarketObservation:
        if self.realized_at <= self.cutoff:
            raise ValueError("realized observation must occur after its forecast cutoff")
        if not self.endpoint.startswith("https://"):
            raise ValueError("Phase-4 source endpoints must be reviewed HTTPS endpoints")
        if self.source_id != self.provider_identity:
            raise ValueError("source and provider identity must remain explicit and equal")
        return self


class Phase4Prediction(BaseModel):
    """One model output with the provenance needed for later review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    predicted_return_bps: Decimal
    confidence: Decimal = Field(ge=0, le=1)
    interval_lower_bps: Decimal | None = None
    interval_upper_bps: Decimal | None = None
    model_code_hash: str
    model_artifact_hash: str
    past_only: bool = True
    resource_limit_passed: bool = True
    latency_ms: Decimal | None = Field(default=None, ge=0)

    @field_validator("predicted_return_bps")
    @classmethod
    def require_finite_prediction(cls, value: Decimal) -> Decimal:
        return _finite_decimal(value, "predicted_return_bps")

    @field_validator("interval_lower_bps", "interval_upper_bps")
    @classmethod
    def require_finite_interval(cls, value: Decimal | None) -> Decimal | None:
        return _finite_decimal(value, "prediction interval") if value is not None else None

    @field_validator("model_code_hash", "model_artifact_hash")
    @classmethod
    def require_model_hash(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "model hash")
        return _digest(value, field_name)

    @field_validator("latency_ms")
    @classmethod
    def require_finite_latency(cls, value: Decimal | None) -> Decimal | None:
        return _finite_decimal(value, "prediction latency") if value is not None else None

    @model_validator(mode="after")
    def validate_interval(self) -> Phase4Prediction:
        if (self.interval_lower_bps is None) != (self.interval_upper_bps is None):
            raise ValueError("prediction interval bounds must be supplied together")
        if (
            self.interval_lower_bps is not None
            and self.interval_upper_bps is not None
            and self.interval_lower_bps > self.interval_upper_bps
        ):
            raise ValueError("prediction interval lower bound exceeds upper bound")
        return self


class Phase4UtilityPolicy(BaseModel):
    """Versioned conservative cost and promotion thresholds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(default="phase4-paper-utility-v1", min_length=1)
    fee_schedule_id: str = Field(default="binance-spot-testnet-conservative-v1", min_length=1)
    fee_bps: Decimal = Field(default=Decimal("10"), ge=0)
    minimum_incremental_utility_bps: Decimal = Field(default=Decimal("0"), ge=0)
    minimum_observations: int = Field(default=30, ge=1)

    @field_validator("fee_bps", "minimum_incremental_utility_bps")
    @classmethod
    def require_finite_policy_decimal(cls, value: Decimal) -> Decimal:
        return _finite_decimal(value, "Phase-4 policy value")


class Phase4RegimeSlice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    regime: str = Field(min_length=1)
    observations: int = Field(ge=1)
    trade_count: int = Field(ge=0)
    directional_accuracy: Decimal = Field(ge=0, le=1)
    net_utility_bps: Decimal


class Phase4UtilityResult(BaseModel):
    """Measured model utility; it is not a roster or admission decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_name: str = Field(min_length=1)
    mae_bps: Decimal = Field(ge=0)
    rmse_bps: Decimal = Field(ge=0)
    observations: int = Field(ge=1)
    trade_count: int = Field(ge=0)
    abstention_count: int = Field(ge=0)
    directional_accuracy: Decimal = Field(ge=0, le=1)
    calibration_coverage: Decimal | None = Field(default=None, ge=0, le=1)
    confidence_brier_score: Decimal | None = Field(default=None, ge=0, le=1)
    turnover: Decimal = Field(ge=0)
    gross_utility_bps: Decimal
    estimated_cost_bps: Decimal = Field(ge=0)
    net_utility_bps: Decimal
    drawdown_bps: Decimal = Field(ge=0)
    strongest_baseline_net_utility_bps: Decimal
    incremental_net_utility_bps: Decimal
    regime_slices: tuple[Phase4RegimeSlice, ...] = ()
    past_only: bool
    provenance_complete: bool
    resource_limit_passed: bool
    adds_marginal_value: bool
    rejection_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_counts(self) -> Phase4UtilityResult:
        if self.trade_count + self.abstention_count != self.observations:
            raise ValueError("Phase-4 result counts are inconsistent")
        if self.net_utility_bps != self.gross_utility_bps - self.estimated_cost_bps:
            raise ValueError("Phase-4 net utility must equal gross utility less estimated cost")
        if (
            self.incremental_net_utility_bps
            != self.net_utility_bps - self.strongest_baseline_net_utility_bps
        ):
            raise ValueError("Phase-4 incremental utility is inconsistent with the baseline")
        if self.adds_marginal_value and self.rejection_reasons:
            raise ValueError("a marginal-value result cannot carry rejection reasons")
        return self


class Phase4EvaluationReport(BaseModel):
    """Immutable-shaped report with an intentionally closed admission field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "advisorai.phase4.paper-utility.v1"
    evaluated_at: datetime
    state: Phase4EvaluationState
    phase3_admission_required: bool = True
    phase4_admission_opened: bool = False
    phase3_gate_record_hash: str | None = None
    source_snapshot_hashes: tuple[str, ...] = ()
    policy: Phase4UtilityPolicy
    baseline_models: tuple[str, ...] = MANDATORY_BASELINES
    candidate_models: tuple[str, ...] = CANDIDATE_MODELS
    results: tuple[Phase4UtilityResult, ...] = ()

    @field_validator("evaluated_at")
    @classmethod
    def require_aware_evaluated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Phase-4 evaluation time must include a timezone")
        return value.astimezone(UTC)

    @field_validator("phase3_gate_record_hash")
    @classmethod
    def validate_optional_gate_hash(cls, value: str | None) -> str | None:
        return _digest(value, "phase3_gate_record_hash") if value is not None else None

    @field_validator("source_snapshot_hashes")
    @classmethod
    def validate_snapshot_hashes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_digest(value, "source_snapshot_hash") for value in values)

    @model_validator(mode="after")
    def enforce_closed_admission(self) -> Phase4EvaluationReport:
        if self.phase4_admission_opened:
            raise ValueError("Phase-4 utility evaluation cannot open admission")
        if self.state is Phase4EvaluationState.MEASURED_PENDING_REVIEW:
            if self.phase3_gate_record_hash is None or not self.results:
                raise ValueError("measured Phase-4 report requires admitted input evidence")
        return self


def _position(value: Decimal) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _model_result(
    model_name: str,
    observations: Sequence[Phase4MarketObservation],
    predictions: Sequence[Phase4Prediction],
    *,
    policy: Phase4UtilityPolicy,
    strongest_baseline_net_utility_bps: Decimal | None,
) -> Phase4UtilityResult:
    by_id = {item.observation_id: item for item in observations}
    ordered = sorted(
        zip(observations, predictions, strict=True),
        key=lambda pair: (pair[0].instrument, pair[0].cutoff, pair[0].observation_id),
    )
    previous: dict[str, int] = defaultdict(int)
    gross = Decimal("0")
    costs = Decimal("0")
    turnover = Decimal("0")
    running = Decimal("0")
    peak = Decimal("0")
    drawdown = Decimal("0")
    active_correct = 0
    active_count = 0
    confidence_brier_total = Decimal("0")
    forecast_errors: list[Decimal] = []
    calibration_hits = 0
    calibration_count = 0
    trade_count = 0
    abstention_count = 0
    regime_values: dict[str, list[tuple[int, Decimal, bool, Decimal]]] = defaultdict(list)
    for observation, prediction in ordered:
        if prediction.observation_id not in by_id:
            raise ValueError("prediction refers to an unknown observation")
        forecast_errors.append(prediction.predicted_return_bps - observation.realized_return_bps)
        position = _position(prediction.predicted_return_bps)
        if position == 0:
            abstention_count += 1
        else:
            trade_count += 1
            active_count += 1
            correct = int(
                (prediction.predicted_return_bps > 0) == (observation.realized_return_bps > 0)
            )
            active_correct += correct
            confidence_brier_total += (prediction.confidence - Decimal(correct)) ** 2
        change = abs(position - previous[observation.instrument])
        turnover += Decimal(change)
        previous[observation.instrument] = position
        trade_cost = Decimal(change) * (
            policy.fee_bps + observation.spread_bps + observation.slippage_bps
        )
        contribution = Decimal(position) * observation.realized_return_bps
        gross += contribution
        costs += trade_cost
        running += contribution - trade_cost
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
        if prediction.interval_lower_bps is not None and prediction.interval_upper_bps is not None:
            calibration_count += 1
            calibration_hits += int(
                prediction.interval_lower_bps
                <= observation.realized_return_bps
                <= prediction.interval_upper_bps
            )
        regime_values[observation.regime].append(
            (
                position,
                observation.realized_return_bps * Decimal(position) - trade_cost,
                position != 0,
                observation.realized_return_bps,
            )
        )

    observations_count = len(ordered)
    strongest = (
        strongest_baseline_net_utility_bps
        if strongest_baseline_net_utility_bps is not None
        else Decimal("0")
    )
    net = gross - costs
    incremental = net - strongest
    reasons: list[str] = []
    if observations_count < policy.minimum_observations:
        reasons.append("insufficient_observations")
    if not all(item.past_only for item in predictions):
        reasons.append("non_past_only_prediction")
    if not all(item.resource_limit_passed for item in predictions):
        reasons.append("resource_limit_failed")
    provenance_complete = all(
        item.model_code_hash and item.model_artifact_hash for item in predictions
    )
    if not provenance_complete:
        reasons.append("incomplete_model_provenance")
    adds = (
        model_name not in MANDATORY_BASELINES
        and not reasons
        and incremental > policy.minimum_incremental_utility_bps
    )
    if (
        not adds
        and model_name not in MANDATORY_BASELINES
        and "no_incremental_net_utility" not in reasons
    ):
        reasons.append("no_incremental_net_utility")

    slices: list[Phase4RegimeSlice] = []
    for regime, values in sorted(regime_values.items()):
        active = [item for item in values if item[2]]
        slices.append(
            Phase4RegimeSlice(
                regime=regime,
                observations=len(values),
                trade_count=len(active),
                directional_accuracy=(
                    Decimal(sum((item[0] > 0) == (item[3] > 0) for item in active))
                    / Decimal(len(active))
                    if active
                    else Decimal("0")
                ),
                net_utility_bps=sum(item[1] for item in values),
            )
        )
    return Phase4UtilityResult(
        model_name=model_name,
        mae_bps=sum(abs(value) for value in forecast_errors) / Decimal(observations_count),
        rmse_bps=(
            sum(value * value for value in forecast_errors) / Decimal(observations_count)
        ).sqrt(),
        observations=observations_count,
        trade_count=trade_count,
        abstention_count=abstention_count,
        directional_accuracy=(
            Decimal(active_correct) / Decimal(active_count) if active_count else Decimal("0")
        ),
        calibration_coverage=(
            Decimal(calibration_hits) / Decimal(calibration_count) if calibration_count else None
        ),
        confidence_brier_score=(
            confidence_brier_total / Decimal(active_count) if active_count else None
        ),
        turnover=turnover / Decimal(observations_count),
        gross_utility_bps=gross,
        estimated_cost_bps=costs,
        net_utility_bps=net,
        drawdown_bps=drawdown,
        strongest_baseline_net_utility_bps=strongest,
        incremental_net_utility_bps=incremental,
        regime_slices=tuple(slices),
        past_only=not any("non_past_only_prediction" == reason for reason in reasons),
        provenance_complete=provenance_complete,
        resource_limit_passed=not any("resource_limit_failed" == reason for reason in reasons),
        adds_marginal_value=adds,
        rejection_reasons=tuple(reasons),
    )


def evaluate_paper_utility(
    observations: Sequence[Phase4MarketObservation],
    predictions: Sequence[Phase4Prediction],
    *,
    policy: Phase4UtilityPolicy | None = None,
    phase3_gate_record_hash: str | None = None,
    evaluated_at: datetime | None = None,
) -> Phase4EvaluationReport:
    """Measure models on admitted paper observations without promoting them."""

    policy = policy or Phase4UtilityPolicy()
    if not observations:
        raise ValueError("Phase-4 utility evaluation requires observations")
    if not all(item.phase3_admitted for item in observations):
        raise Phase4PrerequisiteError(
            "Phase-3 admission is required before paper utility can be measured"
        )
    if phase3_gate_record_hash is None:
        raise Phase4PrerequisiteError("an immutable Phase-3 gate record hash is required")
    phase3_gate_record_hash = _digest(phase3_gate_record_hash, "phase3_gate_record_hash")
    observation_ids = [item.observation_id for item in observations]
    if len(observation_ids) != len(set(observation_ids)):
        raise ValueError("Phase-4 observation identities must be unique")
    by_model: dict[str, dict[str, Phase4Prediction]] = defaultdict(dict)
    for prediction in predictions:
        if prediction.observation_id in by_model[prediction.model_name]:
            raise ValueError("duplicate model prediction for one observation")
        by_model[prediction.model_name][prediction.observation_id] = prediction
    missing_baselines = set(MANDATORY_BASELINES).difference(by_model)
    if missing_baselines:
        raise ValueError(f"mandatory baseline predictions are missing: {sorted(missing_baselines)}")
    expected_ids = set(observation_ids)
    for model_name, values in by_model.items():
        if set(values) != expected_ids:
            raise ValueError(f"{model_name} predictions do not cover the complete observation set")
    baseline_results: list[Phase4UtilityResult] = []
    for model_name in MANDATORY_BASELINES:
        baseline_results.append(
            _model_result(
                model_name,
                observations,
                tuple(by_model[model_name][item.observation_id] for item in observations),
                policy=policy,
                strongest_baseline_net_utility_bps=None,
            )
        )
    strongest = max(item.net_utility_bps for item in baseline_results)
    results = list(baseline_results)
    for model_name in sorted(set(by_model).difference(MANDATORY_BASELINES)):
        results.append(
            _model_result(
                model_name,
                observations,
                tuple(by_model[model_name][item.observation_id] for item in observations),
                policy=policy,
                strongest_baseline_net_utility_bps=strongest,
            )
        )
    timestamp = evaluated_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Phase-4 evaluation timestamp must include a timezone")
    return Phase4EvaluationReport(
        evaluated_at=timestamp.astimezone(UTC),
        state=Phase4EvaluationState.MEASURED_PENDING_REVIEW,
        phase3_gate_record_hash=phase3_gate_record_hash,
        source_snapshot_hashes=tuple(sorted({item.source_snapshot_hash for item in observations})),
        policy=policy,
        results=tuple(results),
    )


def build_preparation_manifest(
    *, policy: Phase4UtilityPolicy | None = None, generated_at: datetime | None = None
) -> Mapping[str, object]:
    """Describe the input contract without claiming real data or admission."""

    policy = policy or Phase4UtilityPolicy()
    timestamp = generated_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("preparation timestamp must include a timezone")
    return {
        "schema": "advisorai.phase4.paper-utility-preparation.v1",
        "generated_at": timestamp.astimezone(UTC).isoformat(),
        "state": Phase4EvaluationState.READY_FOR_ADMITTED_INPUT.value,
        "phase3_admission_required": True,
        "phase4_admission_opened": False,
        "execution_authority": {
            "risk_kernel": "unchanged_external_authority",
            "oms": "unchanged_external_authority",
            "model_order_authority": False,
            "dashboard_order_authority": False,
        },
        "mandatory_baselines": list(MANDATORY_BASELINES),
        "candidate_models": list(CANDIDATE_MODELS),
        "policy": policy.model_dump(mode="json"),
        "required_input_fields": [
            "Phase4MarketObservation with phase3_admitted=true",
            "Phase4Prediction for every baseline and candidate observation",
            "immutable Phase-3 gate record SHA-256",
            "model code and artifact SHA-256 values",
            "spread/slippage and fee schedule identity",
        ],
        "admission_statement": "measurement_only_until_separately_reviewed",
    }


def preparation_hash(payload: Mapping[str, object]) -> str:
    """Return the digest used by the preparation script's immutable manifest."""

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return sha256(encoded).hexdigest()


__all__ = [
    "CANDIDATE_MODELS",
    "MANDATORY_BASELINES",
    "Phase4EvaluationReport",
    "Phase4EvaluationState",
    "Phase4MarketObservation",
    "Phase4Prediction",
    "Phase4PrerequisiteError",
    "Phase4RegimeSlice",
    "Phase4UtilityPolicy",
    "Phase4UtilityResult",
    "build_preparation_manifest",
    "evaluate_paper_utility",
    "preparation_hash",
]
