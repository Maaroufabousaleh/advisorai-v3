"""Typed point-in-time contracts for the V3-Core Phase-4 cadence.

The existing Phase-4 review was intentionally built around a daily research
screen.  This module defines the separate operational contract that the next
review must satisfy: closed five-minute observations, four hours of context,
and a one-hour outcome for BTC and ETH.  It does not acquire data, run a model,
or open a Phase-4 gate.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CADENCE_CONTRACT_VERSION = "advisorai.phase4.v3-core-cadence.v1"
PREREGISTRATION_SCHEMA = "advisorai.phase4.v3-core-preregistration.v1"
EVALUATION_INPUT_SCHEMA = "advisorai.phase4.v3-core-cadence-input.v1"
V3_CORE_SYMBOLS = ("BTCUSDT", "ETHUSDT")
V3_CORE_BASELINES = ("naive", "drift", "seasonal-7", "linear", "lightgbm")
V3_CORE_CANDIDATES = ("ttm-r2", "chronos-2-small")
REGIME_POLICY_VERSION = "v3-core-context-regime-v1"
HEX = frozenset("0123456789abcdef")


def _digest(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(character not in HEX for character in normalized):
        raise ValueError(f"{field_name} must be a SHA-256 digest")
    return normalized


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _finite(value: Decimal, field_name: str) -> Decimal:
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return value


def _aligned(value: datetime, interval_seconds: int, field_name: str) -> datetime:
    normalized = _aware(value, field_name)
    if int(normalized.timestamp()) % interval_seconds:
        raise ValueError(f"{field_name} must align to the configured cadence")
    return normalized


class V3CoreCadencePolicy(BaseModel):
    """The reviewed operational cadence; values cannot be changed per run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: str = CADENCE_CONTRACT_VERSION
    observation_interval_seconds: int = Field(default=300, ge=60, le=3600)
    decision_horizon_seconds: int = Field(default=3600, ge=300, le=86_400)
    context_horizon_seconds: int = Field(default=14_400, ge=3600, le=86_400)
    universe: tuple[str, ...] = V3_CORE_SYMBOLS

    @field_validator("universe")
    @classmethod
    def normalize_universe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().upper() for item in value)
        if normalized != V3_CORE_SYMBOLS:
            raise ValueError("V3-Core cadence universe must remain BTCUSDT and ETHUSDT")
        return normalized

    @model_validator(mode="after")
    def validate_fixed_contract(self) -> V3CoreCadencePolicy:
        if self.contract_version != CADENCE_CONTRACT_VERSION:
            raise ValueError("unsupported V3-Core cadence contract version")
        if self.decision_horizon_seconds % self.observation_interval_seconds:
            raise ValueError("decision horizon must contain whole observation intervals")
        if self.context_horizon_seconds % self.observation_interval_seconds:
            raise ValueError("context horizon must contain whole observation intervals")
        if self.context_horizon_seconds < self.decision_horizon_seconds:
            raise ValueError("V3-Core context must cover at least one decision horizon")
        if self.observation_interval_seconds != 300 or self.decision_horizon_seconds != 3600:
            raise ValueError("V3-Core requires 5-minute observations and a 1-hour horizon")
        if self.context_horizon_seconds != 14_400:
            raise ValueError("V3-Core requires a 4-hour context horizon")
        return self

    @property
    def observations_per_decision(self) -> int:
        return self.decision_horizon_seconds // self.observation_interval_seconds

    @property
    def observations_per_context(self) -> int:
        return self.context_horizon_seconds // self.observation_interval_seconds


class V3CoreCostScenario(BaseModel):
    """Versioned modeled friction; this is not observed fill economics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(min_length=1)
    fee_bps: Decimal = Field(ge=0)
    spread_bps: Decimal = Field(ge=0)
    slippage_bps: Decimal = Field(ge=0)

    @field_validator("fee_bps", "spread_bps", "slippage_bps")
    @classmethod
    def finite_cost(cls, value: Decimal) -> Decimal:
        return _finite(value, "cost")

    @property
    def all_in_bps(self) -> Decimal:
        return self.fee_bps + self.spread_bps + self.slippage_bps


class V3CoreBar(BaseModel):
    """One closed, source-identified five-minute market bar."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument: str = Field(min_length=1)
    interval_end: datetime
    available_at: datetime
    provider_event_at: datetime | None = None
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)
    source_id: str = Field(min_length=1)
    provider_identity: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    source_snapshot_hash: str
    quality: str = "validated"

    @field_validator("instrument")
    @classmethod
    def normalize_instrument(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in V3_CORE_SYMBOLS:
            raise ValueError("V3-Core bars are restricted to BTCUSDT and ETHUSDT")
        return normalized

    @field_validator("interval_end", "available_at", "provider_event_at")
    @classmethod
    def aware_timestamp(cls, value: datetime | None, info: object) -> datetime | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "bar timestamp")
        return _aware(value, field_name)

    @field_validator("open", "high", "low", "close", "volume")
    @classmethod
    def finite_market_value(cls, value: Decimal, info: object) -> Decimal:
        return _finite(value, getattr(info, "field_name", "market value"))

    @field_validator("source_snapshot_hash")
    @classmethod
    def valid_snapshot_hash(cls, value: str) -> str:
        return _digest(value, "source_snapshot_hash")

    @model_validator(mode="after")
    def validate_bar(self) -> V3CoreBar:
        _aligned(self.interval_end, 300, "interval_end")
        if self.interval_end > self.available_at:
            raise ValueError("a closed bar cannot be available before its interval ends")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("bar OHLC bounds are inconsistent")
        if self.source_id != self.provider_identity:
            raise ValueError("source and provider identity must remain explicit and equal")
        if not self.endpoint.startswith("https://"):
            raise ValueError("V3-Core bar endpoint must be a reviewed HTTPS endpoint")
        if self.quality.lower() not in {"validated", "gold"}:
            raise ValueError("V3-Core bars require validated or gold quality")
        return self


class V3CoreForecastCase(BaseModel):
    """A causal 4-hour context and the later one-hour evaluation outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    instrument: str = Field(min_length=1)
    cutoff: datetime
    context_bars: tuple[V3CoreBar, ...]
    future_bars: tuple[V3CoreBar, ...]
    realized_at: datetime
    realized_return_bps: Decimal
    spread_bps: Decimal = Field(ge=0)
    slippage_bps: Decimal = Field(ge=0)
    source_id: str = Field(min_length=1)
    provider_identity: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    source_snapshot_hash: str
    regime: str = Field(min_length=1)
    regime_policy_version: str = REGIME_POLICY_VERSION
    phase3_admitted: bool = False
    contract_version: str = CADENCE_CONTRACT_VERSION
    observation_interval_seconds: int = 300
    decision_horizon_seconds: int = 3600
    context_horizon_seconds: int = 14_400

    @field_validator("instrument")
    @classmethod
    def normalize_case_instrument(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in V3_CORE_SYMBOLS:
            raise ValueError("V3-Core cases are restricted to BTCUSDT and ETHUSDT")
        return normalized

    @field_validator("cutoff", "realized_at")
    @classmethod
    def aware_case_timestamp(cls, value: datetime, info: object) -> datetime:
        return _aware(value, getattr(info, "field_name", "case timestamp"))

    @field_validator("realized_return_bps")
    @classmethod
    def finite_realized_return(cls, value: Decimal) -> Decimal:
        return _finite(value, "realized_return_bps")

    @field_validator("spread_bps", "slippage_bps")
    @classmethod
    def finite_case_cost(cls, value: Decimal) -> Decimal:
        return _finite(value, "case cost")

    @field_validator("source_snapshot_hash")
    @classmethod
    def valid_case_snapshot_hash(cls, value: str) -> str:
        return _digest(value, "source_snapshot_hash")

    @model_validator(mode="after")
    def validate_case(self) -> V3CoreForecastCase:
        policy = V3CoreCadencePolicy()
        _aligned(self.cutoff, policy.decision_horizon_seconds, "cutoff")
        if self.contract_version != CADENCE_CONTRACT_VERSION:
            raise ValueError("case cadence contract version is not supported")
        if self.regime_policy_version != REGIME_POLICY_VERSION:
            raise ValueError("case regime policy version is not supported")
        if (
            self.observation_interval_seconds != policy.observation_interval_seconds
            or self.decision_horizon_seconds != policy.decision_horizon_seconds
            or self.context_horizon_seconds != policy.context_horizon_seconds
        ):
            raise ValueError("case cadence values do not match V3-Core")
        if len(self.context_bars) != policy.observations_per_context:
            raise ValueError("case must contain exactly four hours of five-minute context")
        if len(self.future_bars) != policy.observations_per_decision:
            raise ValueError("case must contain exactly one hour of five-minute outcome bars")
        all_bars = (*self.context_bars, *self.future_bars)
        for bar in all_bars:
            if bar.instrument != self.instrument:
                raise ValueError("case bars must remain instrument-local")
            if (
                bar.source_id != self.source_id
                or bar.provider_identity != self.provider_identity
                or bar.endpoint != self.endpoint
                or bar.source_snapshot_hash != self.source_snapshot_hash
            ):
                raise ValueError("case cannot silently substitute source identity or snapshot")
        context_times = tuple(bar.interval_end for bar in self.context_bars)
        future_times = tuple(bar.interval_end for bar in self.future_bars)
        expected_context = tuple(
            self.cutoff
            - timedelta(seconds=policy.observation_interval_seconds * (len(context_times) - 1 - i))
            for i in range(len(context_times))
        )
        expected_future = tuple(
            self.cutoff + timedelta(seconds=policy.observation_interval_seconds * (i + 1))
            for i in range(len(future_times))
        )
        if context_times != expected_context or future_times != expected_future:
            raise ValueError("case bars must be contiguous around the decision cutoff")
        if any(bar.available_at > self.cutoff for bar in self.context_bars):
            raise ValueError("context contains a bar unavailable at the decision cutoff")
        if self.realized_at != self.future_bars[-1].interval_end:
            raise ValueError("realized_at must equal the end of the one-hour outcome")
        if self.realized_at <= self.cutoff:
            raise ValueError("one-hour outcome must occur after the decision cutoff")
        expected_return = (
            self.future_bars[-1].close / self.context_bars[-1].close - Decimal("1")
        ) * Decimal("10000")
        if self.realized_return_bps != expected_return:
            raise ValueError("realized return does not match the causal bar prices")
        return self


class V3CoreCaseRejection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument: str
    cutoff: datetime
    reason: str


class V3CoreCaseBuild(BaseModel):
    """Deterministic build result; rejected cutoffs are retained, not hidden."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = EVALUATION_INPUT_SCHEMA
    source_id: str
    provider_identity: str
    endpoint: str
    source_snapshot_hash: str
    bar_count: int = Field(ge=0)
    cases: tuple[V3CoreForecastCase, ...] = ()
    rejected_cutoffs: tuple[V3CoreCaseRejection, ...] = ()

    @field_validator("source_snapshot_hash")
    @classmethod
    def valid_build_hash(cls, value: str) -> str:
        return _digest(value, "source_snapshot_hash")


class V3CoreEvaluationInput(BaseModel):
    """Immutable-shaped input consumed by a later offline Phase-4 evaluator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = EVALUATION_INPUT_SCHEMA
    plan_id: str = Field(min_length=1)
    phase3_gate_record_sha256: str
    build: V3CoreCaseBuild

    @field_validator("phase3_gate_record_sha256")
    @classmethod
    def valid_gate_hash(cls, value: str) -> str:
        return _digest(value, "phase3_gate_record_sha256")

    @model_validator(mode="after")
    def validate_input(self) -> V3CoreEvaluationInput:
        if self.schema_version != EVALUATION_INPUT_SCHEMA:
            raise ValueError("unsupported V3-Core evaluation input schema")
        if self.build.schema_version != EVALUATION_INPUT_SCHEMA:
            raise ValueError("case build schema does not match evaluation input")
        for case in self.build.cases:
            if not case.phase3_admitted:
                raise ValueError("evaluation cases must carry Phase-3 admission")
        return self

    def case_counts(self) -> dict[str, int]:
        return {
            symbol: sum(case.instrument == symbol for case in self.build.cases)
            for symbol in V3_CORE_SYMBOLS
        }

    def meets_minimum(self, *, total: int = 128, per_symbol: int = 64) -> bool:
        counts = self.case_counts()
        return len(self.build.cases) >= total and all(
            counts[symbol] >= per_symbol for symbol in V3_CORE_SYMBOLS
        )


class V3CorePhase4Prereadiness(StrEnum):
    READY_FOR_MEASUREMENT = "READY_FOR_MEASUREMENT"
    PENDING_FRESH_PIT_DATA = "PENDING_FRESH_PIT_DATA"


class V3CorePhase4Preregistration(BaseModel):
    """Pre-registered 1-hour review rules, before final outcomes are read."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = PREREGISTRATION_SCHEMA
    plan_id: str = "phase4-v3-core-1h-5m-v1"
    cadence: V3CoreCadencePolicy = V3CoreCadencePolicy()
    market_data_provider_identity: str = "binance_spot_public_market_data"
    market_data_rest_endpoint: str = "https://api.binance.com/api/v3/klines"
    market_data_websocket_endpoint: str = "wss://stream.binance.com:9443/ws"
    execution_venue: str = "binance_spot_testnet"
    candidate_models: tuple[str, ...] = V3_CORE_CANDIDATES
    mandatory_baselines: tuple[str, ...] = V3_CORE_BASELINES
    calibration_method: str = "rolling_abs_residual_quantile_v1"
    calibration_nominal_coverage: Decimal = Decimal("0.80")
    calibration_tolerance: Decimal = Decimal("0.10")
    latency_delays_seconds: tuple[int, ...] = (0, 300, 600, 900, 1800, 3600)
    cost_scenarios: tuple[V3CoreCostScenario, ...] = (
        V3CoreCostScenario(scenario_id="optimistic", fee_bps=5, spread_bps=1, slippage_bps=1),
        V3CoreCostScenario(scenario_id="base", fee_bps=10, spread_bps=2, slippage_bps=2),
        V3CoreCostScenario(scenario_id="conservative", fee_bps=15, spread_bps=4, slippage_bps=4),
        V3CoreCostScenario(
            scenario_id="severe_plausible", fee_bps=25, spread_bps=8, slippage_bps=8
        ),
    )
    minimum_total_cases: int = Field(default=128, ge=1)
    minimum_cases_per_symbol: int = Field(default=64, ge=1)
    final_holdout_per_symbol: int = Field(default=16, ge=1)
    policy_search_status: str = "CLOSED_FOR_CONSUMED_DAILY_DATASET"
    selection_policy: str = "RAW_FORECAST_FIRST_NO_SIMULTANEOUS_SIGNAL_TUNING"
    final_evaluation_policy: str = "FRESH_PIT_WINDOW_SINGLE_PASS_UNTOUCHED"
    network_calls: bool = False
    credentials_loaded: bool = False
    order_writes_attempted: bool = False

    @field_validator("calibration_nominal_coverage", "calibration_tolerance")
    @classmethod
    def finite_calibration_value(cls, value: Decimal) -> Decimal:
        return _finite(value, "calibration policy value")

    @model_validator(mode="after")
    def validate_preregistration(self) -> V3CorePhase4Preregistration:
        if self.schema_version != PREREGISTRATION_SCHEMA:
            raise ValueError("unsupported Phase-4 preregistration schema")
        if self.market_data_provider_identity == self.execution_venue:
            raise ValueError("market-data and execution identities must remain explicit")
        if not self.market_data_rest_endpoint.startswith("https://"):
            raise ValueError("market-data REST endpoint must be HTTPS")
        if not self.market_data_websocket_endpoint.startswith("wss://"):
            raise ValueError("market-data WebSocket endpoint must be WSS")
        if self.candidate_models != V3_CORE_CANDIDATES:
            raise ValueError("the first independent challenger set is fixed to TTM-R2 and Chronos")
        if self.mandatory_baselines != V3_CORE_BASELINES:
            raise ValueError("all mandatory baselines must remain first-class")
        if self.latency_delays_seconds != (0, 300, 600, 900, 1800, 3600):
            raise ValueError("latency scenarios must remain the pre-registered 5m-to-1h set")
        if not (
            self.minimum_total_cases >= 2 * self.minimum_cases_per_symbol
            and self.final_holdout_per_symbol < self.minimum_cases_per_symbol
        ):
            raise ValueError("case minimums must leave an untouched per-symbol holdout")
        if self.network_calls or self.credentials_loaded or self.order_writes_attempted:
            raise ValueError("Phase-4 preregistration must remain offline and write-free")
        return self


def derive_regime_from_context(closes: Sequence[Decimal]) -> str:
    """Derive a coarse regime from context only, with no future observations."""

    if len(closes) < 2 or any(not value.is_finite() or value <= 0 for value in closes):
        raise ValueError("regime context requires at least two positive finite closes")
    returns = tuple(
        (right / left - Decimal("1")) * Decimal("10000")
        for left, right in zip(closes, closes[1:], strict=False)
    )
    mean = sum(returns) / Decimal(len(returns))
    variance = sum((value - mean) ** 2 for value in returns) / Decimal(len(returns))
    volatility = variance.sqrt() if variance > 0 else Decimal("0")
    if volatility >= Decimal("150"):
        return "high_volatility"
    threshold = max(Decimal("1"), volatility * Decimal("0.25"))
    if mean > threshold:
        return "trend_up"
    if mean < -threshold:
        return "trend_down"
    return "range_bound"


def build_v3core_cases(
    bars: Sequence[V3CoreBar],
    *,
    source_id: str,
    provider_identity: str,
    endpoint: str,
    source_snapshot_hash: str,
    spread_bps: Decimal = Decimal("2"),
    slippage_bps: Decimal = Decimal("2"),
    phase3_admitted: bool = False,
    policy: V3CoreCadencePolicy | None = None,
) -> V3CoreCaseBuild:
    """Build causal cases without filling gaps or switching providers silently."""

    cadence = policy or V3CoreCadencePolicy()
    normalized_hash = _digest(source_snapshot_hash, "source_snapshot_hash")
    if not bars:
        return V3CoreCaseBuild(
            source_id=source_id,
            provider_identity=provider_identity,
            endpoint=endpoint,
            source_snapshot_hash=normalized_hash,
            bar_count=0,
        )
    if any(
        bar.source_id != source_id
        or bar.provider_identity != provider_identity
        or bar.endpoint != endpoint
        or bar.source_snapshot_hash != normalized_hash
        for bar in bars
    ):
        raise ValueError("case input contains a source identity or snapshot substitution")
    by_instrument: dict[str, dict[datetime, V3CoreBar]] = {symbol: {} for symbol in V3_CORE_SYMBOLS}
    for bar in bars:
        if bar.interval_end in by_instrument[bar.instrument]:
            raise ValueError("case input contains duplicate instrument/time bar identities")
        by_instrument[bar.instrument][bar.interval_end] = bar
    cases: list[V3CoreForecastCase] = []
    rejected: list[V3CoreCaseRejection] = []
    interval = timedelta(seconds=cadence.observation_interval_seconds)
    for instrument in V3_CORE_SYMBOLS:
        timeline = by_instrument[instrument]
        for cutoff in sorted(timeline):
            if int(cutoff.timestamp()) % cadence.decision_horizon_seconds:
                continue
            context_times = tuple(
                cutoff - interval * (cadence.observations_per_context - 1 - index)
                for index in range(cadence.observations_per_context)
            )
            future_times = tuple(
                cutoff + interval * (index + 1)
                for index in range(cadence.observations_per_decision)
            )
            missing_context = [item for item in context_times if item not in timeline]
            missing_future = [item for item in future_times if item not in timeline]
            if missing_context or missing_future:
                rejected.append(
                    V3CoreCaseRejection(
                        instrument=instrument,
                        cutoff=cutoff,
                        reason=(
                            "missing_context_bars"
                            if missing_context
                            else "missing_one_hour_outcome_bars"
                        ),
                    )
                )
                continue
            context = tuple(timeline[item] for item in context_times)
            future = tuple(timeline[item] for item in future_times)
            if any(bar.available_at > cutoff for bar in context):
                rejected.append(
                    V3CoreCaseRejection(
                        instrument=instrument,
                        cutoff=cutoff,
                        reason="context_not_available_at_cutoff",
                    )
                )
                continue
            case = V3CoreForecastCase(
                case_id=f"{instrument}:{cutoff.isoformat()}:{source_id}",
                instrument=instrument,
                cutoff=cutoff,
                context_bars=context,
                future_bars=future,
                realized_at=future[-1].interval_end,
                realized_return_bps=(future[-1].close / context[-1].close - Decimal("1"))
                * Decimal("10000"),
                spread_bps=spread_bps,
                slippage_bps=slippage_bps,
                source_id=source_id,
                provider_identity=provider_identity,
                endpoint=endpoint,
                source_snapshot_hash=normalized_hash,
                regime=derive_regime_from_context(tuple(bar.close for bar in context)),
                phase3_admitted=phase3_admitted,
            )
            cases.append(case)
    return V3CoreCaseBuild(
        source_id=source_id,
        provider_identity=provider_identity,
        endpoint=endpoint,
        source_snapshot_hash=normalized_hash,
        bar_count=len(bars),
        cases=tuple(cases),
        rejected_cutoffs=tuple(rejected),
    )


def sha256_json(payload: object) -> str:
    """Return the canonical digest used by cadence artifacts."""

    import json

    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)).encode()
    return sha256(encoded).hexdigest()


__all__ = [
    "CADENCE_CONTRACT_VERSION",
    "EVALUATION_INPUT_SCHEMA",
    "PREREGISTRATION_SCHEMA",
    "REGIME_POLICY_VERSION",
    "V3_CORE_BASELINES",
    "V3_CORE_CANDIDATES",
    "V3_CORE_SYMBOLS",
    "V3CoreBar",
    "V3CoreCadencePolicy",
    "V3CoreCaseBuild",
    "V3CoreCaseRejection",
    "V3CoreCostScenario",
    "V3CoreEvaluationInput",
    "V3CoreForecastCase",
    "V3CorePhase4Prereadiness",
    "V3CorePhase4Preregistration",
    "build_v3core_cases",
    "derive_regime_from_context",
    "sha256_json",
]
