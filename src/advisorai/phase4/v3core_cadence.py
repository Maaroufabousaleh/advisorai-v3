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
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CADENCE_CONTRACT_VERSION = "advisorai.phase4.v3-core-cadence.v3"
PREREGISTRATION_SCHEMA = "advisorai.phase4.v3-core-preregistration.v3"
EVALUATION_INPUT_SCHEMA = "advisorai.phase4.v3-core-cadence-input.v3"
V3_CORE_SYMBOLS = ("BTCUSDT", "ETHUSDT")
V3_CORE_BASELINES = ("naive", "drift", "seasonal-7", "linear", "lightgbm")
V3_CORE_CANDIDATES = ("ttm-r2", "chronos-2-small")
REGIME_POLICY_VERSION = "v3-core-context-regime-v1"
V3_CORE_MARKET_DATA_PROVIDER = "binance_spot_public_market_data"
V3_CORE_MARKET_DATA_REST_BASE = "https://data-api.binance.vision"
V3_CORE_MARKET_DATA_REST_ENDPOINT = f"{V3_CORE_MARKET_DATA_REST_BASE}/api/v3/klines"
V3_CORE_MARKET_DATA_WS_ENDPOINT = "wss://data-stream.binance.vision/ws"
HEX = frozenset("0123456789abcdef")

V3CoreEvidenceClass = Literal["historical_development", "forward_pit_admission"]
V3CoreAvailabilityBasis = Literal[
    "forward_observed", "provider_close_semantics", "historical_backfill"
]
V3CoreSourceHealthState = Literal[
    "HEALTHY", "DEGRADED", "STALE", "DISCONNECTED", "RECOVERING", "QUARANTINED"
]


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


class V3CoreMarketDataSurface(BaseModel):
    """The exact credential-free Binance market-data-only surface."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_identity: str = V3_CORE_MARKET_DATA_PROVIDER
    rest_base_url: str = V3_CORE_MARKET_DATA_REST_BASE
    klines_path: str = "/api/v3/klines"
    websocket_url: str = V3_CORE_MARKET_DATA_WS_ENDPOINT
    symbols: tuple[str, ...] = V3_CORE_SYMBOLS
    credentials_required: bool = False
    write_capability: bool = False
    market_data_only: bool = True

    @field_validator("provider_identity", "rest_base_url", "klines_path", "websocket_url")
    @classmethod
    def nonblank(cls, value: str) -> str:
        return value.strip()

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().upper() for item in value)
        if normalized != V3_CORE_SYMBOLS:
            raise ValueError("V3-Core market-data symbols must remain BTCUSDT and ETHUSDT")
        return normalized

    @model_validator(mode="after")
    def validate_fixed_surface(self) -> V3CoreMarketDataSurface:
        if self.provider_identity != V3_CORE_MARKET_DATA_PROVIDER:
            raise ValueError("unsupported V3-Core market-data provider identity")
        if self.rest_base_url != V3_CORE_MARKET_DATA_REST_BASE:
            raise ValueError("V3-Core REST must use the reviewed market-data-only host")
        if self.klines_path != "/api/v3/klines":
            raise ValueError("V3-Core REST path must remain the public klines path")
        if self.websocket_url != V3_CORE_MARKET_DATA_WS_ENDPOINT:
            raise ValueError("V3-Core WebSocket must use the reviewed market-data-only host")
        if self.credentials_required or self.write_capability or not self.market_data_only:
            raise ValueError(
                "V3-Core market-data surface must remain credential-free and read-only"
            )
        return self

    @property
    def klines_url(self) -> str:
        return f"{self.rest_base_url}{self.klines_path}"


class V3CoreBarProvenance(BaseModel):
    """Timestamp and evidence provenance for one closed market bar."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    interval_end: datetime
    provider_available_at: datetime
    collected_at: datetime
    provider_event_at: datetime | None = None
    availability_basis: V3CoreAvailabilityBasis
    evidence_class: V3CoreEvidenceClass
    source_snapshot_hash: str
    raw_record_hash: str
    normalized_record_hash: str
    source_health_state: V3CoreSourceHealthState
    historical_availability_contract_id: str | None = None
    historical_availability_contract_sha256: str | None = None

    @field_validator("interval_end", "provider_available_at", "collected_at", "provider_event_at")
    @classmethod
    def aware_timestamp(cls, value: datetime | None, info: object) -> datetime | None:
        if value is None:
            return None
        return _aware(value, getattr(info, "field_name", "provenance timestamp"))

    @field_validator(
        "source_snapshot_hash",
        "raw_record_hash",
        "normalized_record_hash",
        "historical_availability_contract_sha256",
    )
    @classmethod
    def valid_hash(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _digest(value, getattr(info, "field_name", "hash"))

    @model_validator(mode="after")
    def validate_provenance(self) -> V3CoreBarProvenance:
        _aligned(self.interval_end, 300, "interval_end")
        if self.provider_available_at < self.interval_end:
            raise ValueError("provider availability cannot precede closed interval end")
        if self.evidence_class == "forward_pit_admission":
            if self.availability_basis != "forward_observed":
                raise ValueError("forward PIT bars require forward_observed availability")
            if self.provider_available_at > self.collected_at:
                raise ValueError("forward collection cannot precede provider availability")
            if (
                self.historical_availability_contract_id is not None
                or self.historical_availability_contract_sha256 is not None
            ):
                raise ValueError("forward PIT bars cannot carry historical availability claims")
        else:
            if self.availability_basis != "historical_backfill":
                raise ValueError("historical development bars require historical_backfill")
            if not self.historical_availability_contract_id:
                raise ValueError("historical bars require a reviewed availability contract")
            if not self.historical_availability_contract_sha256:
                raise ValueError("historical bars require an availability contract hash")
        return self


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
    provenance: V3CoreBarProvenance
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
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("bar OHLC bounds are inconsistent")
        if self.source_id != self.provider_identity:
            raise ValueError("source and provider identity must remain explicit and equal")
        if self.source_snapshot_hash != self.provenance.source_snapshot_hash:
            raise ValueError("bar and provenance snapshot hashes must match")
        if self.provider_identity == V3_CORE_MARKET_DATA_PROVIDER:
            if self.endpoint not in {
                V3_CORE_MARKET_DATA_REST_ENDPOINT,
                V3_CORE_MARKET_DATA_WS_ENDPOINT,
            }:
                raise ValueError(
                    "Binance V3-Core bars must use a reviewed market-data-only endpoint"
                )
        elif not self.endpoint.startswith("https://"):
            raise ValueError("V3-Core bar endpoint must be a reviewed HTTPS endpoint")
        if self.quality.lower() not in {"validated", "gold"}:
            raise ValueError("V3-Core bars require validated or gold quality")
        return self

    @property
    def interval_end(self) -> datetime:
        return self.provenance.interval_end

    @property
    def provider_available_at(self) -> datetime:
        return self.provenance.provider_available_at

    @property
    def collected_at(self) -> datetime:
        return self.provenance.collected_at

    @property
    def evidence_class(self) -> V3CoreEvidenceClass:
        return self.provenance.evidence_class


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
    evidence_class: V3CoreEvidenceClass
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
                or bar.evidence_class != self.evidence_class
            ):
                raise ValueError("case cannot silently substitute source identity or snapshot")
        context_times = tuple(bar.interval_end for bar in self.context_bars)
        future_times = tuple(bar.interval_end for bar in self.future_bars)
        # A forward-observed bar ending exactly at the cutoff is not locally
        # available at that cutoff: its provider close and local receipt occur
        # after the interval closes.  The causal context therefore ends one
        # observation before the decision cutoff.  The one-hour outcome begins
        # after the cutoff and remains a separate, future-only segment.
        expected_context = tuple(
            self.cutoff
            - timedelta(seconds=policy.observation_interval_seconds * (len(context_times) - i))
            for i in range(len(context_times))
        )
        expected_future = tuple(
            self.cutoff + timedelta(seconds=policy.observation_interval_seconds * (i + 1))
            for i in range(len(future_times))
        )
        if context_times != expected_context or future_times != expected_future:
            raise ValueError("case bars must be contiguous around the decision cutoff")
        if self.evidence_class == "forward_pit_admission":
            if any(bar.collected_at > self.cutoff for bar in self.context_bars):
                raise ValueError("forward context was collected after the decision cutoff")
        elif any(bar.provider_available_at > self.cutoff for bar in self.context_bars):
            raise ValueError("historical context was unavailable under the provider contract")
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
    evidence_class: V3CoreEvidenceClass
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

    @model_validator(mode="after")
    def validate_case_classes(self) -> V3CoreCaseBuild:
        if any(case.evidence_class != self.evidence_class for case in self.cases):
            raise ValueError("case evidence class must match the build evidence class")
        return self


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
            if case.evidence_class != self.build.evidence_class:
                raise ValueError("evaluation case evidence class does not match its build")
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

    def is_forward_admission_input(self) -> bool:
        return self.build.evidence_class == "forward_pit_admission"


class V3CorePhase4Prereadiness(StrEnum):
    READY_FOR_MEASUREMENT = "READY_FOR_MEASUREMENT"
    PENDING_FRESH_PIT_DATA = "PENDING_FRESH_PIT_DATA"


class V3CorePhase4Preregistration(BaseModel):
    """Pre-registered 1-hour review rules, before final outcomes are read."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = PREREGISTRATION_SCHEMA
    plan_id: str = "phase4-v3-core-1h-5m-v3"
    cadence: V3CoreCadencePolicy = V3CoreCadencePolicy()
    market_data_surface: V3CoreMarketDataSurface = V3CoreMarketDataSurface()
    market_data_provider_identity: str = V3_CORE_MARKET_DATA_PROVIDER
    market_data_rest_endpoint: str = V3_CORE_MARKET_DATA_REST_ENDPOINT
    market_data_websocket_endpoint: str = V3_CORE_MARKET_DATA_WS_ENDPOINT
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
        if self.market_data_provider_identity != self.market_data_surface.provider_identity:
            raise ValueError("market-data provider identity must match the reviewed surface")
        if self.market_data_rest_endpoint != self.market_data_surface.klines_url:
            raise ValueError("market-data REST endpoint must match the reviewed klines surface")
        if self.market_data_websocket_endpoint != self.market_data_surface.websocket_url:
            raise ValueError("market-data WebSocket endpoint must match the reviewed surface")
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
    evidence_class: V3CoreEvidenceClass,
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
            evidence_class=evidence_class,
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
        or bar.evidence_class != evidence_class
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
                cutoff - interval * (cadence.observations_per_context - index)
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
            if evidence_class == "forward_pit_admission":
                unavailable = any(bar.collected_at > cutoff for bar in context)
                rejection_reason = "context_not_collected_at_cutoff"
            else:
                unavailable = any(bar.provider_available_at > cutoff for bar in context)
                rejection_reason = "context_not_available_under_provider_contract"
            if unavailable:
                rejected.append(
                    V3CoreCaseRejection(
                        instrument=instrument,
                        cutoff=cutoff,
                        reason=rejection_reason,
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
                evidence_class=evidence_class,
                regime=derive_regime_from_context(tuple(bar.close for bar in context)),
                phase3_admitted=phase3_admitted,
            )
            cases.append(case)
    return V3CoreCaseBuild(
        evidence_class=evidence_class,
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
    "V3CoreAvailabilityBasis",
    "V3CoreEvidenceClass",
    "V3CoreSourceHealthState",
    "V3_CORE_BASELINES",
    "V3_CORE_CANDIDATES",
    "V3_CORE_MARKET_DATA_PROVIDER",
    "V3_CORE_MARKET_DATA_REST_BASE",
    "V3_CORE_MARKET_DATA_REST_ENDPOINT",
    "V3_CORE_MARKET_DATA_WS_ENDPOINT",
    "V3_CORE_SYMBOLS",
    "V3CoreBar",
    "V3CoreBarProvenance",
    "V3CoreCadencePolicy",
    "V3CoreCaseBuild",
    "V3CoreCaseRejection",
    "V3CoreCostScenario",
    "V3CoreEvaluationInput",
    "V3CoreForecastCase",
    "V3CorePhase4Prereadiness",
    "V3CorePhase4Preregistration",
    "V3CoreMarketDataSurface",
    "build_v3core_cases",
    "derive_regime_from_context",
    "sha256_json",
]
