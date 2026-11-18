"""Shadow comparison: a model-shaped forecast path vs the deterministic baseline.

Runs one existing forecaster (default: the deterministic ``linear`` baseline
from the roster's mandatory set, which needs no weights) through the typed
``Forecast`` evidence path on the same frozen month-end cutoffs, windows, and
costs as the DSL replay, and compares it against the buy-and-hold reference.
A missing local checkpoint quarantines instead of silently substituting: the
shadow path reports ``quarantined`` with the exact identity it required.

No promotion, no order path, no reference-adapter change. Shadow outputs land
in the research registry as experiments only.
"""

from __future__ import annotations

import hashlib
import resource
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from advisorai.contracts import AssetClass, Forecast, InstrumentIdentity
from advisorai.models.forecasting import (
    AbstentionPolicy,
    BaselineForecaster,
    ForecastSeries,
    LinearForecaster,
    ModelUnavailable,
    NaiveForecaster,
    SeriesPoint,
    build_forecast,
    evaluate_forecasts,
)
from advisorai.phase4.signal_policy import SignalCostScenario
from advisorai.research import ResearchRegistry
from advisorai.research import cards as research_cards
from advisorai.research.replay import (
    FORMATION_BARS,
    HOLDING_BARS,
    ExposureSpec,
    build_observations,
    evaluate_momentum,
    load_snapshot_bars,
)

SHADOW_SCHEMA_VERSION = "advisorai.research.shadow-compare.v1"
SHADOW_CODE_ID = "advisorai.research.shadow"

# Roster identity for the shadow path. The linear baseline is project code
# (no weights); a weight-bearing local model would pin repository_id/revision
# here and quarantine when either is unavailable.
SHADOW_MODEL_IDENTITY = {
    "candidate": "linear",
    "role": "mandatory_forecast_baseline",
    "runtime_class": "advisorai-deterministic",
    "repository_id": None,
    "revision": None,
    "device": "cpu",
}


@dataclass(frozen=True, slots=True)
class ShadowExposureResult:
    symbol: str
    model_name: str
    observations: int
    abstentions: int
    mae_bps: Decimal
    directional_accuracy: Decimal
    net_utility_bps: Decimal
    baseline_net_bps: Decimal
    adds_marginal_value: bool
    latency_p50_ms: int
    peak_ram_mib: int
    quarantined: bool
    quarantine_reason: str | None
    experiment_id: UUID | None


@dataclass(frozen=True, slots=True)
class ShadowReport:
    schema_version: str
    model_identity: dict[str, Any]
    snapshot_hash: str
    code_hash: str
    exposures: tuple[ShadowExposureResult, ...]


def _peak_ram_mib() -> int:
    usage_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(usage_kb // 1024)


def _require_local_checkpoint(
    *, candidate: str, repository_id: str | None, revision: str | None
) -> None:
    """Fail closed when a weight-bearing model lacks a pinned identity."""
    if repository_id is None or revision is None:
        raise ModelUnavailable(
            f"{candidate} has no pinned repository_id/revision in this checkout; "
            "shadow stays quarantined instead of substituting another model"
        )


def shadow_forecasts(
    symbol: str,
    bars: tuple[dict[str, Any], ...],
    *,
    model: BaselineForecaster,
    abstention_policy: AbstentionPolicy | None = None,
    warmup_bars: int = FORMATION_BARS,
) -> tuple[tuple[Forecast, ...], dict[str, Any]]:
    """Typed forecasts at the same month-end cutoffs as the DSL replay."""
    momentum = evaluate_momentum(bars)
    observations, _ = build_observations(
        symbol, bars, momentum, factor_hash="0" * 64, snapshot_hash="0" * 64
    )
    closes = [bar["adj_close"] for bar in bars]
    dates = [bar["date"] for bar in bars]
    forecasts: list[Forecast] = []
    latencies: list[int] = []
    cutoff_index = {
        obs.cutoff.date().isoformat(): position for position, obs in enumerate(observations)
    }
    instrument = InstrumentIdentity(
        canonical_id=symbol, asset_class=AssetClass.EQUITY, venue="research"
    )
    for bar_index, bar in enumerate(bars):
        key = bar["date"]
        if key not in cutoff_index:
            continue
        history = closes[max(0, bar_index - warmup_bars + 1) : bar_index + 1]
        points = tuple(
            SeriesPoint(at=datetime.fromisoformat(dates[i]).replace(tzinfo=UTC), value=closes[i])
            for i in range(max(0, bar_index - len(history) + 1), bar_index + 1)
        )
        cutoff = datetime.fromisoformat(key).replace(tzinfo=UTC)
        series = ForecastSeries(
            instrument=instrument,
            snapshot_id=uuid4(),
            target=f"{symbol}:adj_close",
            training_cutoff=cutoff,
            points=points,
        )
        started = time.perf_counter()
        try:
            forecast = build_forecast(
                series=series,
                model=model,
                horizon_seconds=HOLDING_BARS * 86400,
                calibration_version="rolling-v1",
                code_version=f"{SHADOW_CODE_ID}:{model.name}",
                cutoff=cutoff,
                abstention_policy=abstention_policy,
            )
        except ModelUnavailable as exc:
            # Per-cutoff quarantine: the model cannot train or infer here
            # (e.g. insufficient past-only rows early in sample). Recorded as
            # an abstention with the reason, never a silent skip.
            forecast = Forecast(
                instrument=instrument,
                snapshot_id=series.snapshot_id,
                cutoff=cutoff,
                horizon_seconds=HOLDING_BARS * 86400,
                target=series.target,
                point_forecast=None,
                confidence=Decimal("0"),
                abstained=True,
                abstention_reason=f"model_unavailable:{exc}",
                model_version=model.name,
                data_hash=hashlib.sha256(series.model_dump_json().encode()).hexdigest(),
                feature_hash=hashlib.sha256(f"{model.name}:{series.target}".encode()).hexdigest(),
                code_hash=hashlib.sha256(f"{SHADOW_CODE_ID}:{model.name}".encode()).hexdigest(),
                calibration_version="rolling-v1",
                training_cutoff=cutoff,
                latency_ms=0,
                peak_ram_mib=0,
                peak_vram_mib=0,
            )
        latencies.append(int((time.perf_counter() - started) * 1000))
        forecasts.append(forecast)
    latencies.sort()
    stats = {
        "latency_p50_ms": latencies[len(latencies) // 2] if latencies else 0,
        "peak_ram_mib": _peak_ram_mib(),
    }
    return tuple(forecasts), stats


def run_shadow_exposure(
    registry: ResearchRegistry,
    exposure: ExposureSpec,
    *,
    model: BaselineForecaster,
    model_identity: dict[str, Any],
    factor_id: UUID,
    snapshot_hash: str,
    code_hash: str,
    environment_hash: str,
    hypothesis_id: UUID | None = None,
    abstention_policy: AbstentionPolicy | None = None,
    cost_scenario: SignalCostScenario | None = None,
) -> ShadowExposureResult:
    """Compare one model path against buy-and-hold on identical windows/costs."""
    scenario = cost_scenario or exposure.cost_scenario()
    all_in = scenario.all_in_cost_bps
    momentum = evaluate_momentum(exposure.bars)
    observations, _ = build_observations(
        exposure.symbol,
        exposure.bars,
        momentum,
        factor_hash="0" * 64,
        snapshot_hash=snapshot_hash,
    )
    forecasts, stats = shadow_forecasts(
        exposure.symbol,
        exposure.bars,
        model=model,
        abstention_policy=abstention_policy,
    )
    predicted: list[Decimal] = []
    actuals: list[Decimal] = []
    abstentions = 0
    cutoff_close_by_date = {bar["date"]: bar["adj_close"] for bar in exposure.bars}
    for forecast, obs in zip(forecasts, observations, strict=True):
        if forecast.abstained or forecast.point_forecast is None:
            abstentions += 1
            predicted.append(Decimal("0"))
        else:
            # Forecast target is next-holding price; convert to return vs cutoff close.
            cutoff_close = cutoff_close_by_date[obs.cutoff.date().isoformat()]
            predicted.append(
                (forecast.point_forecast - cutoff_close) / cutoff_close * Decimal("10000")
                if cutoff_close
                else Decimal("0")
            )
        actuals.append(obs.realized_return_bps)
    baseline_net = sum(actuals, Decimal("0")) - all_in  # one entry, held throughout
    evaluation = evaluate_forecasts(
        model_name=model.name,
        predictions=tuple(predicted),
        actuals=tuple(actuals),
        baseline_utility=baseline_net,
        cost_bps=all_in,
        baseline_name="baseline-buy-and-hold-v1",
        past_only=True,
        latency_ms=stats["latency_p50_ms"],
        peak_ram_mib=stats["peak_ram_mib"],
    )
    experiment_id: UUID | None = None
    if not evaluation.adds_marginal_value or True:
        # Recorded either way: a losing shadow is valid evidence.
        experiment = research_cards.ExperimentArtifact(
            candidate_id=factor_id,
            code_hash=code_hash,
            data_snapshot_hash=snapshot_hash,
            environment_hash=environment_hash,
            seeds=(20261114,),
            windows=(f"{exposure.symbol}:shadow=2015-2025",),
            metrics={
                "model": model.name,
                "model_candidate": str(model_identity.get("candidate", model.name)),
                "model_runtime": str(model_identity.get("runtime_class", "unknown")),
                "model_revision": str(model_identity.get("revision", "none")),
                "model_checkpoint": str(model_identity.get("checkpoint_hash", "none")),
                "observations": str(evaluation.observations),
                "abstentions": str(abstentions),
                "mae_bps": f"{evaluation.mae:.2f}",
                "directional_accuracy": f"{evaluation.directional_accuracy:.4f}",
                "net_utility_bps": f"{evaluation.net_utility_after_costs:.2f}",
                "baseline_net_bps": f"{baseline_net:.2f}",
                "adds_marginal_value": str(evaluation.adds_marginal_value),
                "latency_p50_ms": str(stats["latency_p50_ms"]),
                "peak_ram_mib": str(stats["peak_ram_mib"]),
            },
            costs={
                "fee_bps": str(scenario.fee_bps),
                "spread_bps": str(scenario.spread_bps),
                "slippage_bps": str(scenario.slippage_bps),
                "scenario": scenario.scenario_id,
            },
            exposures={exposure.symbol: "1.0"},
            regime_slices=(),
            trial_count=1,
        )
        stored = registry.store(experiment, record_type="experiment")
        experiment_id = stored.artifact_id
    _ = hypothesis_id
    return ShadowExposureResult(
        symbol=exposure.symbol,
        model_name=model.name,
        observations=evaluation.observations,
        abstentions=abstentions,
        mae_bps=evaluation.mae,
        directional_accuracy=evaluation.directional_accuracy,
        net_utility_bps=evaluation.net_utility_after_costs,
        baseline_net_bps=baseline_net,
        adds_marginal_value=evaluation.adds_marginal_value,
        latency_p50_ms=stats["latency_p50_ms"],
        peak_ram_mib=stats["peak_ram_mib"],
        quarantined=False,
        quarantine_reason=None,
        experiment_id=experiment_id,
    )


def quarantined_result(symbol: str, *, reason: str) -> ShadowExposureResult:
    return ShadowExposureResult(
        symbol=symbol,
        model_name=str(SHADOW_MODEL_IDENTITY["candidate"]),
        observations=0,
        abstentions=0,
        mae_bps=Decimal("0"),
        directional_accuracy=Decimal("0"),
        net_utility_bps=Decimal("0"),
        baseline_net_bps=Decimal("0"),
        adds_marginal_value=False,
        latency_p50_ms=0,
        peak_ram_mib=0,
        quarantined=True,
        quarantine_reason=reason,
        experiment_id=None,
    )


def run_shadow(
    *,
    registry: ResearchRegistry,
    snapshot_root: Path,
    symbols: tuple[str, ...],
    model: BaselineForecaster | None = None,
    model_identity: dict[str, Any] | None = None,
    model_factory: Any = None,
    hypothesis_id: UUID | None = None,
    environment_hash: str | None = None,
    abstention_policy: AbstentionPolicy | None = None,
    require_checkpoint: bool = False,
) -> ShadowReport:
    """Shadow one model across exposures; quarantine beats silent substitution.

    ``model_factory``, when given, builds a (model, identity) pair per symbol
    from that exposure's bars — required for trained runners that close over
    one exposure's history. Factory failures quarantine that exposure only.
    """
    bars, snapshot_hash = load_snapshot_bars(snapshot_root)
    code_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    environment = environment_hash or hashlib.sha256(b"research-shadow-env-v1").hexdigest()
    default_model = model or LinearForecaster()
    default_identity = (
        dict(model_identity) if model_identity is not None else dict(SHADOW_MODEL_IDENTITY)
    )
    results: list[ShadowExposureResult] = []
    report_identity = default_identity
    for symbol in symbols:
        if symbol not in bars:
            results.append(quarantined_result(symbol, reason=f"snapshot lacks exposure {symbol}"))
            continue
        chosen = default_model
        identity = default_identity
        if model_factory is not None:
            try:
                built_model, built_identity = model_factory(symbol, bars[symbol])
            except ModelUnavailable as exc:
                results.append(quarantined_result(symbol, reason=str(exc)))
                continue
            chosen, identity = built_model, dict(built_identity)
            report_identity = identity
        if require_checkpoint:
            try:
                _require_local_checkpoint(
                    candidate=identity["candidate"],
                    repository_id=identity["repository_id"],
                    revision=identity["revision"],
                )
            except ModelUnavailable as exc:
                results.append(quarantined_result(symbol, reason=str(exc)))
                continue
        exposure = ExposureSpec(symbol=symbol, bars=bars[symbol])
        factor_id = uuid4()
        try:
            results.append(
                run_shadow_exposure(
                    registry,
                    exposure,
                    model=chosen,
                    model_identity=identity,
                    factor_id=factor_id,
                    snapshot_hash=snapshot_hash,
                    code_hash=code_hash,
                    environment_hash=environment,
                    hypothesis_id=hypothesis_id,
                    abstention_policy=abstention_policy,
                )
            )
        except ModelUnavailable as exc:
            results.append(quarantined_result(symbol, reason=str(exc)))
    return ShadowReport(
        schema_version=SHADOW_SCHEMA_VERSION,
        model_identity=report_identity,
        snapshot_hash=snapshot_hash,
        code_hash=code_hash,
        exposures=tuple(results),
    )


def reference_baseline_model() -> NaiveForecaster:
    """The deterministic reference adapter for shadow comparison."""
    return NaiveForecaster()


__all__ = [
    "SHADOW_CODE_ID",
    "SHADOW_MODEL_IDENTITY",
    "SHADOW_SCHEMA_VERSION",
    "ShadowExposureResult",
    "ShadowReport",
    "quarantined_result",
    "reference_baseline_model",
    "run_shadow",
    "run_shadow_exposure",
    "shadow_forecasts",
]
