"""Restricted, deterministic forecast-to-signal research contracts.

This module is deliberately upstream of portfolio construction, the
RiskKernel, the OMS, and execution.  It converts a typed forecast into a
bounded target-direction signal for offline Phase-4 research only.  It has no
network, credential, model-loading, or order-submission boundary.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .paper_utility import Phase4MarketObservation, Phase4Prediction


class SignalPolicyFamily(StrEnum):
    """Small, reviewed policy families permitted by this research boundary."""

    SIGN_ONLY = "sign_only"
    MAGNITUDE_THRESHOLD = "magnitude_threshold"
    EDGE_OVER_COST = "edge_over_cost"
    HYSTERESIS = "hysteresis"
    COOLDOWN = "cooldown"
    CONFIDENCE_THRESHOLD = "confidence_threshold"


class SignalPolicySpec(BaseModel):
    """A frozen, finite policy configuration with no executable code fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "advisorai.phase4.signal-policy.v1"
    policy_id: str = Field(min_length=1)
    family: SignalPolicyFamily
    threshold_bps: Decimal = Field(default=Decimal("0"), ge=0)
    flip_threshold_bps: Decimal = Field(default=Decimal("0"), ge=0)
    expected_all_in_cost_bps: Decimal = Field(default=Decimal("0"), ge=0)
    uncertainty_buffer_bps: Decimal = Field(default=Decimal("0"), ge=0)
    min_hold_observations: int = Field(default=0, ge=0)
    confidence_threshold: Decimal = Field(default=Decimal("0"), ge=0, le=1)

    @field_validator(
        "threshold_bps",
        "flip_threshold_bps",
        "expected_all_in_cost_bps",
        "uncertainty_buffer_bps",
        "confidence_threshold",
    )
    @classmethod
    def require_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("signal-policy decimal values must be finite")
        return value

    @model_validator(mode="after")
    def validate_family_parameters(self) -> SignalPolicySpec:
        if self.family is SignalPolicyFamily.HYSTERESIS:
            if self.threshold_bps <= 0 or self.flip_threshold_bps < self.threshold_bps:
                raise ValueError(
                    "hysteresis requires positive entry and non-decreasing flip thresholds"
                )
        elif self.family is SignalPolicyFamily.MAGNITUDE_THRESHOLD:
            if self.threshold_bps <= 0:
                raise ValueError("magnitude threshold must be positive")
        elif self.family is SignalPolicyFamily.EDGE_OVER_COST:
            if self.expected_all_in_cost_bps < 0 or self.uncertainty_buffer_bps < 0:
                raise ValueError("edge-over-cost thresholds must be non-negative")
        elif self.family is SignalPolicyFamily.COOLDOWN:
            if self.min_hold_observations < 1:
                raise ValueError("cooldown requires at least one holding observation")
        elif self.family is SignalPolicyFamily.CONFIDENCE_THRESHOLD:
            if self.confidence_threshold <= 0:
                raise ValueError("confidence threshold must be positive")
        return self


class SignalCostScenario(BaseModel):
    """A modeled friction scenario; it is not a claim about observed fills."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(min_length=1)
    fee_bps: Decimal = Field(ge=0)
    spread_bps: Decimal = Field(ge=0)
    slippage_bps: Decimal = Field(ge=0)
    historical_fill_cost: bool = False

    @field_validator("fee_bps", "spread_bps", "slippage_bps")
    @classmethod
    def require_finite_cost(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("signal-policy costs must be finite")
        return value

    @property
    def all_in_cost_bps(self) -> Decimal:
        return self.fee_bps + self.spread_bps + self.slippage_bps


class PolicySignal(BaseModel):
    """A target-direction signal, intentionally not an order or portfolio."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "advisorai.phase4.policy-signal.v1"
    policy_id: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    observation_id: str = Field(min_length=1)
    instrument: str = Field(min_length=1)
    cutoff: datetime
    predicted_return_bps: Decimal
    confidence: Decimal = Field(ge=0, le=1)
    raw_position: int = Field(ge=-1, le=1)
    previous_position: int = Field(ge=-1, le=1)
    target_position: int = Field(ge=-1, le=1)
    changed: bool

    @field_validator("cutoff")
    @classmethod
    def require_aware_cutoff(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("policy signal cutoffs must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("predicted_return_bps")
    @classmethod
    def require_finite_prediction(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("policy signal forecasts must be finite")
        return value

    @model_validator(mode="after")
    def validate_changed_flag(self) -> PolicySignal:
        if self.changed != (self.previous_position != self.target_position):
            raise ValueError("policy signal changed flag is inconsistent")
        return self


class PolicySliceMetric(BaseModel):
    """Deterministic utility accounting for one instrument or regime slice."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1)
    observations: int = Field(ge=1)
    active_observations: int = Field(ge=0)
    signal_change_count: int = Field(ge=0)
    turnover_units: Decimal = Field(ge=0)
    gross_utility_bps: Decimal
    estimated_cost_bps: Decimal = Field(ge=0)
    net_utility_bps: Decimal
    directional_accuracy: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_accounting(self) -> PolicySliceMetric:
        if self.active_observations > self.observations:
            raise ValueError("active observations exceed slice observations")
        if self.net_utility_bps != self.gross_utility_bps - self.estimated_cost_bps:
            raise ValueError("slice net utility is inconsistent")
        return self


class PolicyUtilityMetrics(BaseModel):
    """Cost, turnover, and utility metrics for one frozen signal path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "advisorai.phase4.policy-utility-metrics.v1"
    policy_id: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    observations: int = Field(ge=1)
    active_observations: int = Field(ge=0)
    abstention_count: int = Field(ge=0)
    signal_change_count: int = Field(ge=0)
    turnover_units: Decimal = Field(ge=0)
    turnover: Decimal = Field(ge=0)
    gross_utility_bps: Decimal
    fee_cost_bps: Decimal = Field(ge=0)
    spread_cost_bps: Decimal = Field(ge=0)
    slippage_cost_bps: Decimal = Field(ge=0)
    estimated_cost_bps: Decimal = Field(ge=0)
    net_utility_bps: Decimal
    utility_per_signal_change_bps: Decimal | None = None
    directional_accuracy: Decimal = Field(ge=0, le=1)
    max_drawdown_bps: Decimal = Field(ge=0)
    instrument_metrics: tuple[PolicySliceMetric, ...] = ()
    regime_metrics: tuple[PolicySliceMetric, ...] = ()

    @model_validator(mode="after")
    def validate_accounting(self) -> PolicyUtilityMetrics:
        if self.active_observations + self.abstention_count != self.observations:
            raise ValueError("active and abstention counts do not cover observations")
        if self.turnover != self.turnover_units / Decimal(self.observations):
            raise ValueError("turnover is inconsistent with turnover units")
        if self.estimated_cost_bps != (
            self.fee_cost_bps + self.spread_cost_bps + self.slippage_cost_bps
        ):
            raise ValueError("cost components do not equal estimated cost")
        if self.net_utility_bps != self.gross_utility_bps - self.estimated_cost_bps:
            raise ValueError("net utility is inconsistent with cost accounting")
        if self.signal_change_count and self.utility_per_signal_change_bps is None:
            raise ValueError("utility per signal change is required when changes exist")
        if not self.signal_change_count and self.utility_per_signal_change_bps is not None:
            raise ValueError("utility per signal change must be empty without changes")
        return self


class PolicyPathComparison(BaseModel):
    """Comparison of a primary forecast path with a baseline path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    primary_model: str = Field(min_length=1)
    baseline_model: str = Field(min_length=1)
    observations: int = Field(ge=1)
    raw_direction_disagreement_count: int = Field(ge=0)
    primary_active_when_baseline_flat_count: int = Field(ge=0)
    primary_changed_when_baseline_unchanged_count: int = Field(ge=0)
    baseline_changed_when_primary_unchanged_count: int = Field(ge=0)
    primary_only_turnover_units: Decimal = Field(ge=0)
    primary_only_gross_utility_bps: Decimal
    primary_only_estimated_cost_bps: Decimal = Field(ge=0)
    primary_only_net_utility_bps: Decimal

    @model_validator(mode="after")
    def validate_net(self) -> PolicyPathComparison:
        if self.primary_only_net_utility_bps != (
            self.primary_only_gross_utility_bps - self.primary_only_estimated_cost_bps
        ):
            raise ValueError("primary-only utility is inconsistent")
        return self


class PredictionDistributionSummary(BaseModel):
    """Distribution diagnostics used to explain cost sensitivity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_name: str = Field(min_length=1)
    instrument: str = Field(min_length=1)
    observations: int = Field(ge=1)
    absolute_prediction_min_bps: Decimal = Field(ge=0)
    absolute_prediction_p25_bps: Decimal = Field(ge=0)
    absolute_prediction_p50_bps: Decimal = Field(ge=0)
    absolute_prediction_p75_bps: Decimal = Field(ge=0)
    absolute_prediction_p90_bps: Decimal = Field(ge=0)
    absolute_prediction_p95_bps: Decimal = Field(ge=0)
    absolute_prediction_max_bps: Decimal = Field(ge=0)
    absolute_residual_p50_bps: Decimal = Field(ge=0)
    absolute_residual_p90_bps: Decimal = Field(ge=0)
    signed_residual_mean_bps: Decimal
    positive_prediction_count: int = Field(ge=0)
    negative_prediction_count: int = Field(ge=0)
    zero_prediction_count: int = Field(ge=0)


def candidate_policy_specs() -> tuple[SignalPolicySpec, ...]:
    """Return the intentionally small, pre-registered policy search space."""

    return (
        SignalPolicySpec(policy_id="sign-only-v1", family=SignalPolicyFamily.SIGN_ONLY),
        *tuple(
            SignalPolicySpec(
                policy_id=f"magnitude-{threshold}-bps-v1",
                family=SignalPolicyFamily.MAGNITUDE_THRESHOLD,
                threshold_bps=Decimal(str(threshold)),
            )
            for threshold in (50, 100, 150)
        ),
        *tuple(
            SignalPolicySpec(
                policy_id=f"edge-cost-{cost}-bps-v1",
                family=SignalPolicyFamily.EDGE_OVER_COST,
                expected_all_in_cost_bps=Decimal(str(cost)),
            )
            for cost in (23, 33, 43)
        ),
        *tuple(
            SignalPolicySpec(
                policy_id=f"hysteresis-{entry}-{flip}-bps-v1",
                family=SignalPolicyFamily.HYSTERESIS,
                threshold_bps=Decimal(str(entry)),
                flip_threshold_bps=Decimal(str(flip)),
            )
            for entry, flip in ((50, 75), (75, 100), (100, 125))
        ),
        *tuple(
            SignalPolicySpec(
                policy_id=f"cooldown-{period}-observations-v1",
                family=SignalPolicyFamily.COOLDOWN,
                min_hold_observations=period,
            )
            for period in (2, 3)
        ),
        SignalPolicySpec(
            policy_id="confidence-0.6-v1",
            family=SignalPolicyFamily.CONFIDENCE_THRESHOLD,
            confidence_threshold=Decimal("0.6"),
        ),
    )


def _position(value: Decimal) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _target_for_policy(
    spec: SignalPolicySpec,
    *,
    raw_position: int,
    prediction: Phase4Prediction,
    previous_position: int,
    instrument_index: int,
    last_change_index: int,
) -> int:
    magnitude = abs(prediction.predicted_return_bps)
    if spec.family is SignalPolicyFamily.SIGN_ONLY:
        return raw_position
    if spec.family is SignalPolicyFamily.MAGNITUDE_THRESHOLD:
        return (
            raw_position if raw_position and magnitude >= spec.threshold_bps else previous_position
        )
    if spec.family is SignalPolicyFamily.EDGE_OVER_COST:
        threshold = spec.expected_all_in_cost_bps + spec.uncertainty_buffer_bps
        return raw_position if raw_position and magnitude > threshold else previous_position
    if spec.family is SignalPolicyFamily.HYSTERESIS:
        if raw_position == 0:
            return previous_position
        if previous_position == 0:
            return raw_position if magnitude >= spec.threshold_bps else 0
        if raw_position == previous_position:
            return previous_position
        return raw_position if magnitude >= spec.flip_threshold_bps else previous_position
    if spec.family is SignalPolicyFamily.COOLDOWN:
        if raw_position == previous_position:
            return previous_position
        if (
            last_change_index < 0
            or instrument_index - last_change_index >= spec.min_hold_observations
        ):
            return raw_position
        return previous_position
    if spec.family is SignalPolicyFamily.CONFIDENCE_THRESHOLD:
        return (
            raw_position
            if raw_position and prediction.confidence >= spec.confidence_threshold
            else previous_position
        )
    raise ValueError(f"unsupported signal-policy family: {spec.family}")


def apply_signal_policy(
    observations: Sequence[Phase4MarketObservation],
    predictions: Sequence[Phase4Prediction],
    spec: SignalPolicySpec,
) -> tuple[PolicySignal, ...]:
    """Apply one policy causally; outcomes are never read by this function."""

    if not observations:
        raise ValueError("signal policy requires observations")
    by_observation = {item.observation_id: item for item in observations}
    if len(by_observation) != len(observations):
        raise ValueError("signal policy observations must be unique")
    if len({item.observation_id for item in predictions}) != len(predictions):
        raise ValueError("signal policy predictions must be unique")
    if {item.observation_id for item in predictions} != set(by_observation):
        raise ValueError("signal policy predictions must cover observations exactly")
    model_names = {item.model_name for item in predictions}
    if len(model_names) != 1:
        raise ValueError("signal policy accepts exactly one model path")
    model_name = next(iter(model_names))
    prediction_by_id = {item.observation_id: item for item in predictions}
    ordered = sorted(
        observations,
        key=lambda item: (item.instrument, item.cutoff, item.observation_id),
    )
    previous: dict[str, int] = defaultdict(int)
    last_change: dict[str, int] = defaultdict(lambda: -1)
    instrument_index: dict[str, int] = defaultdict(int)
    signals: list[PolicySignal] = []
    for observation in ordered:
        prediction = prediction_by_id[observation.observation_id]
        raw_position = _position(prediction.predicted_return_bps)
        prior = previous[observation.instrument]
        index = instrument_index[observation.instrument]
        target = _target_for_policy(
            spec,
            raw_position=raw_position,
            prediction=prediction,
            previous_position=prior,
            instrument_index=index,
            last_change_index=last_change[observation.instrument],
        )
        changed = target != prior
        if changed:
            last_change[observation.instrument] = index
        previous[observation.instrument] = target
        instrument_index[observation.instrument] = index + 1
        signals.append(
            PolicySignal(
                policy_id=spec.policy_id,
                model_name=model_name,
                observation_id=observation.observation_id,
                instrument=observation.instrument,
                cutoff=observation.cutoff,
                predicted_return_bps=prediction.predicted_return_bps,
                confidence=prediction.confidence,
                raw_position=raw_position,
                previous_position=prior,
                target_position=target,
                changed=changed,
            )
        )
    return tuple(signals)


def _quantile(values: Sequence[Decimal], fraction: Decimal) -> Decimal:
    if not values:
        raise ValueError("cannot calculate a quantile of an empty sequence")
    ordered = sorted(values)
    index = int((Decimal(len(ordered) - 1) * fraction).to_integral_value())
    return ordered[index]


def summarize_prediction_distribution(
    observations: Sequence[Phase4MarketObservation],
    predictions: Sequence[Phase4Prediction],
) -> PredictionDistributionSummary:
    """Summarize forecasts and residuals without using the result for policy choice."""

    if not observations or len(observations) != len(predictions):
        raise ValueError("distribution summary requires paired observations and predictions")
    if {item.observation_id for item in observations} != {
        item.observation_id for item in predictions
    }:
        raise ValueError("distribution summary identities do not match")
    model_names = {item.model_name for item in predictions}
    instruments = {item.instrument for item in observations}
    if len(model_names) != 1 or len(instruments) != 1:
        raise ValueError("distribution summary requires one model and one instrument")
    observation_by_id = {item.observation_id: item for item in observations}
    magnitudes = [abs(item.predicted_return_bps) for item in predictions]
    residuals = [
        item.predicted_return_bps - observation_by_id[item.observation_id].realized_return_bps
        for item in predictions
    ]
    absolute_residuals = [abs(item) for item in residuals]
    return PredictionDistributionSummary(
        model_name=next(iter(model_names)),
        instrument=next(iter(instruments)),
        observations=len(predictions),
        absolute_prediction_min_bps=min(magnitudes),
        absolute_prediction_p25_bps=_quantile(magnitudes, Decimal("0.25")),
        absolute_prediction_p50_bps=_quantile(magnitudes, Decimal("0.50")),
        absolute_prediction_p75_bps=_quantile(magnitudes, Decimal("0.75")),
        absolute_prediction_p90_bps=_quantile(magnitudes, Decimal("0.90")),
        absolute_prediction_p95_bps=_quantile(magnitudes, Decimal("0.95")),
        absolute_prediction_max_bps=max(magnitudes),
        absolute_residual_p50_bps=_quantile(absolute_residuals, Decimal("0.50")),
        absolute_residual_p90_bps=_quantile(absolute_residuals, Decimal("0.90")),
        signed_residual_mean_bps=sum(residuals, Decimal("0")) / Decimal(len(residuals)),
        positive_prediction_count=sum(
            item > 0 for item in (p.predicted_return_bps for p in predictions)
        ),
        negative_prediction_count=sum(
            item < 0 for item in (p.predicted_return_bps for p in predictions)
        ),
        zero_prediction_count=sum(
            item == 0 for item in (p.predicted_return_bps for p in predictions)
        ),
    )


def _slice_metric(
    label: str,
    rows: Sequence[
        tuple[PolicySignal, Phase4MarketObservation, Decimal, Decimal, Decimal, Decimal]
    ],
) -> PolicySliceMetric:
    active = [row for row in rows if row[0].target_position != 0]
    gross = sum((row[3] for row in rows), Decimal("0"))
    cost = sum((row[4] for row in rows), Decimal("0"))
    return PolicySliceMetric(
        label=label,
        observations=len(rows),
        active_observations=len(active),
        signal_change_count=sum(row[0].changed for row in rows),
        turnover_units=sum((row[2] for row in rows), Decimal("0")),
        gross_utility_bps=gross,
        estimated_cost_bps=cost,
        net_utility_bps=gross - cost,
        directional_accuracy=(
            Decimal(
                sum(
                    row[0].target_position != 0
                    and ((row[0].target_position > 0) == (row[1].realized_return_bps > 0))
                    for row in active
                )
            )
            / Decimal(len(active))
            if active
            else Decimal("0")
        ),
    )


def evaluate_policy_signals(
    observations: Sequence[Phase4MarketObservation],
    signals: Sequence[PolicySignal],
    scenario: SignalCostScenario,
) -> PolicyUtilityMetrics:
    """Evaluate one already-generated path under one modeled cost scenario."""

    if not observations or not signals:
        raise ValueError("policy utility requires observations and signals")
    observation_by_id = {item.observation_id: item for item in observations}
    if len(observation_by_id) != len(observations):
        raise ValueError("policy utility observations must be unique")
    signal_by_id = {item.observation_id: item for item in signals}
    if len(signal_by_id) != len(signals) or set(signal_by_id) != set(observation_by_id):
        raise ValueError("policy signals must cover observations exactly")
    ordered = sorted(signals, key=lambda item: (item.instrument, item.cutoff, item.observation_id))
    rows: list[
        tuple[PolicySignal, Phase4MarketObservation, Decimal, Decimal, Decimal, Decimal]
    ] = []
    running = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    total_gross = Decimal("0")
    fee_cost = Decimal("0")
    spread_cost = Decimal("0")
    slippage_cost = Decimal("0")
    active_correct = 0
    active_count = 0
    turnover_units = Decimal("0")
    for signal in ordered:
        observation = observation_by_id[signal.observation_id]
        units = Decimal(abs(signal.target_position - signal.previous_position))
        gross = Decimal(signal.target_position) * observation.realized_return_bps
        fee = units * scenario.fee_bps
        spread = units * scenario.spread_bps
        slippage = units * scenario.slippage_bps
        cost = fee + spread + slippage
        total_gross += gross
        fee_cost += fee
        spread_cost += spread
        slippage_cost += slippage
        turnover_units += units
        if signal.target_position != 0:
            active_count += 1
            active_correct += int(
                (signal.target_position > 0) == (observation.realized_return_bps > 0)
            )
        running += gross - cost
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)
        rows.append((signal, observation, units, gross, cost, gross - cost))
    by_instrument: dict[
        str, list[tuple[PolicySignal, Phase4MarketObservation, Decimal, Decimal, Decimal, Decimal]]
    ] = defaultdict(list)
    by_regime: dict[
        str, list[tuple[PolicySignal, Phase4MarketObservation, Decimal, Decimal, Decimal, Decimal]]
    ] = defaultdict(list)
    for row in rows:
        by_instrument[row[1].instrument].append(row)
        by_regime[row[1].regime].append(row)
    changes = sum(signal.changed for signal in ordered)
    net = total_gross - fee_cost - spread_cost - slippage_cost
    return PolicyUtilityMetrics(
        policy_id=ordered[0].policy_id,
        model_name=ordered[0].model_name,
        scenario_id=scenario.scenario_id,
        observations=len(rows),
        active_observations=active_count,
        abstention_count=len(rows) - active_count,
        signal_change_count=changes,
        turnover_units=turnover_units,
        turnover=turnover_units / Decimal(len(rows)),
        gross_utility_bps=total_gross,
        fee_cost_bps=fee_cost,
        spread_cost_bps=spread_cost,
        slippage_cost_bps=slippage_cost,
        estimated_cost_bps=fee_cost + spread_cost + slippage_cost,
        net_utility_bps=net,
        utility_per_signal_change_bps=net / Decimal(changes) if changes else None,
        directional_accuracy=(
            Decimal(active_correct) / Decimal(active_count) if active_count else Decimal("0")
        ),
        max_drawdown_bps=max_drawdown,
        instrument_metrics=tuple(
            _slice_metric(label, slice_rows) for label, slice_rows in sorted(by_instrument.items())
        ),
        regime_metrics=tuple(
            _slice_metric(label, slice_rows) for label, slice_rows in sorted(by_regime.items())
        ),
    )


def compare_policy_paths(
    observations: Sequence[Phase4MarketObservation],
    primary_signals: Sequence[PolicySignal],
    baseline_signals: Sequence[PolicySignal],
    scenario: SignalCostScenario,
) -> PolicyPathComparison:
    """Explain primary-only changes without changing either path."""

    observation_by_id = {item.observation_id: item for item in observations}
    primary = {item.observation_id: item for item in primary_signals}
    baseline = {item.observation_id: item for item in baseline_signals}
    if set(primary) != set(observation_by_id) or set(baseline) != set(observation_by_id):
        raise ValueError("path comparison requires matching observations")
    disagreement = 0
    primary_active_baseline_flat = 0
    primary_changed_baseline_unchanged = 0
    baseline_changed_primary_unchanged = 0
    primary_only_units = Decimal("0")
    primary_only_gross = Decimal("0")
    primary_only_cost = Decimal("0")
    for observation_id, observation in observation_by_id.items():
        left = primary[observation_id]
        right = baseline[observation_id]
        if left.raw_position != right.raw_position:
            disagreement += 1
        if left.raw_position != 0 and right.raw_position == 0:
            primary_active_baseline_flat += 1
        if left.changed and not right.changed:
            primary_changed_baseline_unchanged += 1
            units = Decimal(abs(left.target_position - left.previous_position))
            primary_only_units += units
            primary_only_gross += Decimal(left.target_position) * observation.realized_return_bps
            primary_only_cost += units * scenario.all_in_cost_bps
        if right.changed and not left.changed:
            baseline_changed_primary_unchanged += 1
    return PolicyPathComparison(
        primary_model=primary_signals[0].model_name,
        baseline_model=baseline_signals[0].model_name,
        observations=len(observation_by_id),
        raw_direction_disagreement_count=disagreement,
        primary_active_when_baseline_flat_count=primary_active_baseline_flat,
        primary_changed_when_baseline_unchanged_count=primary_changed_baseline_unchanged,
        baseline_changed_when_primary_unchanged_count=baseline_changed_primary_unchanged,
        primary_only_turnover_units=primary_only_units,
        primary_only_gross_utility_bps=primary_only_gross,
        primary_only_estimated_cost_bps=primary_only_cost,
        primary_only_net_utility_bps=primary_only_gross - primary_only_cost,
    )


__all__ = [
    "PolicyPathComparison",
    "PolicySignal",
    "PolicySliceMetric",
    "PolicyUtilityMetrics",
    "PredictionDistributionSummary",
    "SignalCostScenario",
    "SignalPolicyFamily",
    "SignalPolicySpec",
    "apply_signal_policy",
    "candidate_policy_specs",
    "compare_policy_paths",
    "evaluate_policy_signals",
    "summarize_prediction_distribution",
]
