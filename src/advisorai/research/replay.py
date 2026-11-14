"""Multi-asset research replay: DSL factor vs baselines on frozen PIT inputs.

Offline research boundary. Converts a versioned DSL time-series-momentum
factor into typed forecasts, routes them through the existing restricted
forecast-to-signal policies and the deterministic replay/accounting path, and
records one ``ExperimentArtifact`` per exposure plus a shared manifest. No
orders, no credentials, no admission changes. Crypto support is structural,
not special: any exposure is ``(symbol, bars, costs)``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from advisorai.contracts import (
    AssetClass,
    InstrumentIdentity,
    PointInTimeObservation,
    SourceGrade,
)
from advisorai.execution.events import MarketEvent, ReplayEngine
from advisorai.lake import PointInTimeViolation, SnapshotBuilder
from advisorai.phase4.paper_utility import Phase4MarketObservation, Phase4Prediction
from advisorai.phase4.signal_policy import (
    SignalCostScenario,
    SignalPolicyFamily,
    SignalPolicySpec,
    apply_signal_policy,
)
from advisorai.research import cards as research_cards
from advisorai.research import factors as dsl
from advisorai.research.costs import costs_for
from advisorai.research.registry import ResearchRegistry
from advisorai.research.validity import PurgedWalkForward

REPLAY_SCHEMA_VERSION = "advisorai.research.multi-asset-replay.v1"
CODE_ID = "advisorai.research.replay"
SOURCE_ID = "yahoo-finance-chart-v8"
SOURCE_ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart"
# Preregistered before seeing results: 252-bar momentum formation including
# the cutoff bar (a 12-0 span, not the paper's 12-1 skip-month variant),
# 21-bar holding, sign-only policy. Windows below are enforced by
# assign_window/require_window_embargo, not just documented.
FORMATION_BARS = 252
HOLDING_BARS = 21
MODEL_NAME = "tsmom-252b-sign-v1"

# Preregistered evaluation windows (cutoff calendar year). Screen is the only
# window the design may use; validation and holdout are reported separately.
SCREEN_YEARS = (2015, 2019)
VALIDATION_YEARS = (2020, 2022)
HOLDOUT_YEARS = (2023, 2025)
WINDOW_EMBARGO_DAYS = 21


def assign_window(cutoff_year: int) -> str:
    """Map a cutoff year to its preregistered window; unknown years fail closed."""
    if SCREEN_YEARS[0] <= cutoff_year <= SCREEN_YEARS[1]:
        return "screen"
    if VALIDATION_YEARS[0] <= cutoff_year <= VALIDATION_YEARS[1]:
        return "validation"
    if HOLDOUT_YEARS[0] <= cutoff_year <= HOLDOUT_YEARS[1]:
        return "holdout"
    raise ValueError(f"cutoff year {cutoff_year} is outside all preregistered windows")


def require_window_embargo(
    observations: tuple[Phase4MarketObservation, ...],
    *,
    require_all_windows: bool = True,
) -> dict[str, tuple[str, str, int]]:
    """Prove window boundaries and the 21-day embargo from actual cutoffs.

    Returns per-window (first_cutoff, last_cutoff, count). Every cutoff must
    fall inside a preregistered window, and consecutive non-empty windows must
    be separated by at least WINDOW_EMBARGO_DAYS. Full replays require all
    three windows; unit-sized inputs may cover a subset.
    """
    by_window: dict[str, list[str]] = {"screen": [], "validation": [], "holdout": []}
    for obs in observations:
        by_window[assign_window(obs.cutoff.year)].append(obs.cutoff.date().isoformat())
    if require_all_windows:
        for name, dates in by_window.items():
            if not dates:
                raise ValueError(f"preregistered window {name} has no cutoffs")
    for dates in by_window.values():
        dates.sort()
    present = [name for name in ("screen", "validation", "holdout") if by_window[name]]
    for earlier, later in zip(present, present[1:], strict=False):
        gap = (
            datetime.fromisoformat(by_window[later][0])
            - datetime.fromisoformat(by_window[earlier][-1])
        ).days
        if gap < WINDOW_EMBARGO_DAYS:
            raise ValueError(
                f"window embargo violated: {earlier}->{later} gap is {gap}d, "
                f"need {WINDOW_EMBARGO_DAYS}d"
            )
    return {name: (dates[0], dates[-1], len(dates)) for name, dates in by_window.items() if dates}


@dataclass(frozen=True, slots=True)
class ExposureSpec:
    symbol: str
    bars: tuple[dict[str, Any], ...] = ()
    fee_bps: Decimal | None = None
    spread_bps: Decimal | None = None
    slippage_bps: Decimal | None = None

    def resolved_costs(self) -> tuple[Decimal, Decimal, Decimal]:
        """Explicit per-exposure overrides win; otherwise the pinned config."""
        if (
            self.fee_bps is not None
            and self.spread_bps is not None
            and self.slippage_bps is not None
        ):
            return (self.fee_bps, self.spread_bps, self.slippage_bps)
        if self.fee_bps is None and self.spread_bps is None and self.slippage_bps is None:
            return costs_for(self.symbol)
        raise ValueError(f"{self.symbol}: partial cost overrides are not allowed")

    def cost_scenario(self) -> SignalCostScenario:
        fee, spread, slippage = self.resolved_costs()
        return SignalCostScenario(
            scenario_id=f"{self.symbol.lower()}-etf-conservative-v1",
            fee_bps=fee,
            spread_bps=spread,
            slippage_bps=slippage,
        )


@dataclass(frozen=True, slots=True)
class WindowResult:
    name: str
    observations: int
    trade_count: int
    net_bps: Decimal
    baseline_net_bps: Decimal


@dataclass(frozen=True, slots=True)
class ExposureResult:
    symbol: str
    observations: int
    trade_count: int
    gross_bps: Decimal
    costs_bps: Decimal
    net_bps: Decimal
    baseline_net_bps: Decimal
    directional_accuracy: Decimal | None
    max_drawdown_bps: Decimal
    experiment_id: UUID
    regime_slices: tuple[tuple[str, int, str], ...]
    windows: tuple[WindowResult, ...] = ()


@dataclass(frozen=True, slots=True)
class ReplayReport:
    schema_version: str
    factor_hash: str
    snapshot_hash: str
    code_hash: str
    policy_id: str
    exposures: tuple[ExposureResult, ...]
    failed: tuple[str, ...]


def load_snapshot_bars(snapshot_root: Path) -> tuple[dict[str, tuple[dict[str, Any], ...]], str]:
    """Load frozen bars; the hash covers the bars file exactly as read."""
    bars_path = snapshot_root / "bars.json"
    payload = bars_path.read_bytes()
    data = json.loads(payload.decode("utf-8"))
    bars = {
        symbol: tuple(
            {
                "date": row["date"],
                "open": Decimal(str(row["open"])),
                "high": Decimal(str(row["high"])),
                "low": Decimal(str(row["low"])),
                "adj_close": Decimal(str(row["adj_close"])),
                "volume": int(row["volume"]),
            }
            for row in rows
        )
        for symbol, rows in sorted(data.items())
    }
    return bars, hashlib.sha256(payload).hexdigest()


def momentum_dsl_source(*, formation_bars: int = FORMATION_BARS) -> str:
    """Time-series momentum: formation return over a caller-supplied lagged leg.

    The DSL's ``ts_lag`` is structural — the caller supplies the lagged value —
    so the versioned expression references an ``adj_close_lag_N`` field whose
    per-bar series is built past-only from closes at or before that bar. This
    keeps the expression inside the DSL depth/node budget while the formation
    length stays in the versioned field name and lookback record.
    """
    if formation_bars < 2:
        raise ValueError("momentum formation must span at least two bars")
    lagged = {"field": f"adj_close_lag_{formation_bars}"}
    return json.dumps(
        {
            "op": "div",
            "args": [
                {"op": "sub", "args": [{"field": "adj_close"}, dict(lagged)]},
                dict(lagged),
            ],
        },
        sort_keys=True,
    )


def evaluate_momentum(
    bars: tuple[dict[str, Any], ...], *, formation_bars: int = FORMATION_BARS
) -> tuple[Decimal, ...]:
    """Past-only formation return per bar; warmup bars read as zero.

    The DSL expression is the versioned identity: parsed, hashed, and actually
    evaluated over the two past-only field series (current close and the
    formation-start close at each bar). Index ``i`` reads closes ``i`` and
    ``i - formation + 1`` only.
    """
    expression = dsl.parse(momentum_dsl_source(formation_bars=formation_bars))
    closes = tuple(bar["adj_close"] for bar in bars)
    lag_name = f"adj_close_lag_{formation_bars}"
    lagged = tuple(
        closes[index - formation_bars + 1] if index >= formation_bars - 1 else closes[index]
        for index in range(len(closes))
    )
    evaluated = dsl.evaluate(expression, {"adj_close": closes, lag_name: lagged})
    assert evaluated.expression_hash == dsl.canonical_hash(expression)
    return evaluated.values


def _month_end_cutoffs(
    bars: tuple[dict[str, Any], ...], *, holding_bars: int = HOLDING_BARS
) -> list[int]:
    """Month-end formation cutoffs with a full holding window still in sample."""
    cutoffs: list[int] = []
    for index in range(FORMATION_BARS - 1, len(bars) - holding_bars):
        current = bars[index]["date"][:7]
        following = bars[index + 1]["date"][:7]
        if current != following:
            cutoffs.append(index)
    return cutoffs


def _realized_volatility(returns: list[Decimal], end: int, *, window: int = 63) -> Decimal:
    sample = returns[max(0, end - window) : end]
    if len(sample) < 2:
        return Decimal("0")
    mean = sum(sample, Decimal("0")) / Decimal(len(sample))
    variance = sum((item - mean) ** 2 for item in sample) / Decimal(len(sample))
    return variance.sqrt() if variance > 0 else Decimal("0")


def build_observations(
    symbol: str,
    bars: tuple[dict[str, Any], ...],
    momentum: tuple[Decimal, ...],
    *,
    factor_hash: str,
    snapshot_hash: str,
    holding_bars: int = HOLDING_BARS,
) -> tuple[tuple[Phase4MarketObservation, ...], tuple[Phase4Prediction, ...]]:
    """Month-end cutoffs: factor at close T, realized T+1..T+21, vol-tercile regime."""
    cutoffs = _month_end_cutoffs(bars, holding_bars=holding_bars)
    if not cutoffs:
        raise ValueError(f"{symbol}: no eligible month-end cutoffs in frozen bars")
    daily_returns = [Decimal("0")] * len(bars)
    for index in range(1, len(bars)):
        prior = bars[index - 1]["adj_close"]
        daily_returns[index] = (bars[index]["adj_close"] - prior) / prior if prior else Decimal("0")
    vols = {index: _realized_volatility(daily_returns, index) for index in cutoffs}
    ordered = sorted(vols.values())
    lo = ordered[len(ordered) // 3]
    hi = ordered[2 * len(ordered) // 3]

    model_code_hash = hashlib.sha256(f"{CODE_ID}:{factor_hash}:momentum-v1".encode()).hexdigest()
    observations: list[Phase4MarketObservation] = []
    predictions: list[Phase4Prediction] = []
    for position, index in enumerate(cutoffs):
        cutoff_date = datetime.fromisoformat(bars[index]["date"]).replace(tzinfo=UTC)
        realized_date = datetime.fromisoformat(bars[index + holding_bars]["date"]).replace(
            tzinfo=UTC
        )
        entry = bars[index + 1]["adj_close"]
        exit_ = bars[index + holding_bars]["adj_close"]
        realized = (exit_ - entry) / entry if entry else Decimal("0")
        vol = vols[index]
        regime = (
            "realized-vol-low"
            if vol <= lo
            else "realized-vol-high"
            if vol >= hi
            else "realized-vol-mid"
        )
        observation_id = f"{symbol}-tsmom-{cutoff_date.date().isoformat()}-{position:04d}"
        observations.append(
            Phase4MarketObservation(
                observation_id=observation_id,
                instrument=symbol,
                cutoff=cutoff_date,
                realized_at=realized_date,
                realized_return_bps=realized * Decimal("10000"),
                spread_bps=Decimal("0"),
                slippage_bps=Decimal("0"),
                regime=regime,
                source_id=SOURCE_ID,
                provider_identity=SOURCE_ID,
                endpoint=SOURCE_ENDPOINT,
                source_snapshot_hash=snapshot_hash,
                phase3_admitted=False,
            )
        )
        predictions.append(
            Phase4Prediction(
                observation_id=observation_id,
                model_name=MODEL_NAME,
                predicted_return_bps=momentum[index] * Decimal("10000"),
                confidence=Decimal("0.5"),
                model_code_hash=model_code_hash,
                model_artifact_hash=factor_hash,
                past_only=True,
                resource_limit_passed=True,
            )
        )
    # Point-in-time proof: every factor input was first available no later than
    # the cutoff that consumes it; a future-dated input fails closed here.
    pit_records = tuple(
        PointInTimeObservation(
            instrument=InstrumentIdentity(
                canonical_id=symbol, asset_class=AssetClass.EQUITY, venue="research"
            ),
            event_time=obs.cutoff,
            effective_time=obs.cutoff,
            first_available_at=obs.cutoff,
            ingested_at=obs.cutoff,
            raw_artifact_hash=snapshot_hash,
            parser_version=CODE_ID,
            source_family=SOURCE_ID,
            origin=SOURCE_ENDPOINT,
            quality_grade=SourceGrade.RESEARCH,
            intended_use="research_replay",
            value=obs.observation_id,
        )
        for obs in observations
    )
    latest_cutoff = max(obs.cutoff for obs in observations)
    try:
        SnapshotBuilder.assert_observations_available(pit_records, latest_cutoff)
    except PointInTimeViolation as exc:
        raise PointInTimeViolation(f"{symbol}: replay inputs violate PIT: {exc}") from exc
    return tuple(observations), tuple(predictions)


def _baseline_predictions(
    observations: tuple[Phase4MarketObservation, ...],
    *,
    model_code_hash: str,
    data_hash: str,
) -> tuple[Phase4Prediction, ...]:
    """Buy-and-hold baseline: always-long unit forecast on the same windows."""
    return tuple(
        Phase4Prediction(
            observation_id=obs.observation_id,
            model_name="baseline-buy-and-hold-v1",
            predicted_return_bps=Decimal("1"),
            confidence=Decimal("0.5"),
            model_code_hash=model_code_hash,
            model_artifact_hash=data_hash,
            past_only=True,
            resource_limit_passed=True,
        )
        for obs in observations
    )


def _run_path(
    exposure: ExposureSpec,
    observations: tuple[Phase4MarketObservation, ...],
    predictions: tuple[Phase4Prediction, ...],
    spec: SignalPolicySpec,
) -> dict[str, Any]:
    """Deterministic event-ordered accounting for one model path."""
    signals = apply_signal_policy(observations, predictions, spec)
    all_in = exposure.cost_scenario().all_in_cost_bps
    events = tuple(
        MarketEvent.from_raw(
            event_type="research-signal",
            instrument_id=obs.instrument,
            occurred_at=obs.cutoff,
            sequence=position,
            raw_payload=f"{obs.observation_id}:{signal.target_position}".encode(),
            price=Decimal("100"),
            quantity=Decimal("1"),
        )
        for position, (obs, signal) in enumerate(zip(observations, signals, strict=True))
    )
    by_sequence = {
        position: (obs, signal)
        for position, (obs, signal) in enumerate(zip(observations, signals, strict=True))
    }

    state: dict[str, Any] = {
        "gross": Decimal("0"),
        "costs": Decimal("0"),
        "trades": 0,
        "correct": 0,
        "active": 0,
        "peak": Decimal("0"),
        "drawdown": Decimal("0"),
        "running": Decimal("0"),
        "regime": {},
        "window": {},
        "seen": 0,
    }

    def handler(event: MarketEvent) -> None:
        obs, signal = by_sequence[event.sequence]
        if obs.cutoff != event.occurred_at or obs.instrument != event.instrument_id:
            raise ValueError("research replay event does not match its observation")
        position = Decimal(signal.target_position)
        pnl = position * obs.realized_return_bps
        state["gross"] += pnl
        if position != 0:
            state["active"] += 1
            state["correct"] += int(
                (signal.predicted_return_bps > 0) == (obs.realized_return_bps > 0)
            )
        change = abs(signal.target_position - signal.previous_position)
        if change:
            state["trades"] += 1
        cost = Decimal(change) * all_in
        state["costs"] += cost
        state["running"] += pnl - cost
        state["peak"] = max(state["peak"], state["running"])
        state["drawdown"] = max(state["drawdown"], state["peak"] - state["running"])
        bucket = state["regime"].setdefault(obs.regime, [0, Decimal("0")])
        bucket[0] += 1
        bucket[1] += pnl - cost
        wbucket = state["window"].setdefault(assign_window(obs.cutoff.year), [0, 0, Decimal("0")])
        wbucket[0] += 1
        wbucket[1] += 1 if change else 0
        wbucket[2] += pnl - cost
        state["seen"] += 1

    replayed = ReplayEngine().replay(events, handler)
    if replayed != len(observations) or state["seen"] != len(observations):
        raise ValueError("research replay dropped or duplicated events")
    net = state["gross"] - state["costs"]
    accuracy = Decimal(state["correct"]) / Decimal(state["active"]) if state["active"] else None
    return {
        "observations": len(observations),
        "trades": state["trades"],
        "gross_bps": state["gross"],
        "costs_bps": state["costs"],
        "net_bps": net,
        "accuracy": accuracy,
        "drawdown_bps": state["drawdown"],
        "regime": tuple(
            (name, count, f"{value:.2f}")
            for name, (count, value) in sorted(state["regime"].items())
        ),
        "window": tuple(
            (name, count, trades, value)
            for name, (count, trades, value) in sorted(state["window"].items())
        ),
    }


def run_exposure(
    registry: ResearchRegistry,
    exposure: ExposureSpec,
    *,
    spec: SignalPolicySpec,
    factor_id: UUID,
    factor_hash: str,
    snapshot_hash: str,
    code_hash: str,
    environment_hash: str,
    hypothesis_id: UUID | None = None,
    require_all_windows: bool = True,
) -> ExposureResult:
    """Replay one exposure; record factor, experiment, and validation artifacts."""
    momentum = evaluate_momentum(exposure.bars)
    observations, predictions = build_observations(
        exposure.symbol,
        exposure.bars,
        momentum,
        factor_hash=factor_hash,
        snapshot_hash=snapshot_hash,
    )
    window_proof = require_window_embargo(observations, require_all_windows=require_all_windows)
    candidate = _run_path(exposure, observations, predictions, spec)
    baseline = _run_path(
        exposure,
        observations,
        _baseline_predictions(
            observations,
            model_code_hash=hashlib.sha256(
                f"{CODE_ID}:baseline-buy-and-hold-v1".encode()
            ).hexdigest(),
            data_hash=snapshot_hash,
        ),
        SignalPolicySpec(policy_id="baseline-always-long-v1", family=SignalPolicyFamily.SIGN_ONLY),
    )
    exposure_factor_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"research-replay:{factor_hash}:{exposure.symbol}"
    )
    stored_factor = registry.get(exposure_factor_id)
    if stored_factor is None:
        stored_factor = registry.store(
            research_cards.CandidateFactorArtifact(
                artifact_id=exposure_factor_id,
                dsl_ast_hash=factor_hash,
                dsl_source=momentum_dsl_source(),
                lineage=(
                    "sourced:crossref:10.1016/j.jfineco.2011.11.003",
                    f"factor-family:{factor_id}",
                ),
                feature_versions=("adj_close-vendor-adjusted-v1",),
                lookbacks=(FORMATION_BARS,),
                universe=exposure.symbol,
                hypothesis_card_id=hypothesis_id,
            ),
            record_type="factor",
        )
    elif not isinstance(stored_factor, research_cards.CandidateFactorArtifact):
        raise ValueError(f"factor identity {exposure_factor_id} holds a non-factor record")
    fee, spread, slippage = exposure.resolved_costs()
    baseline_window = {name: value for name, _, _, value in baseline["window"]}
    window_results = tuple(
        WindowResult(
            name=name,
            observations=count,
            trade_count=trades,
            net_bps=value,
            baseline_net_bps=baseline_window[name],
        )
        for name, count, trades, value in candidate["window"]
    )
    experiment = research_cards.ExperimentArtifact(
        candidate_id=stored_factor.artifact_id,
        code_hash=code_hash,
        data_snapshot_hash=snapshot_hash,
        environment_hash=environment_hash,
        seeds=(20261114,),
        windows=tuple(
            f"{exposure.symbol}:{name}={first}..{last} ({count} obs)"
            for name, (first, last, count) in sorted(window_proof.items())
        ),
        metrics={
            "observations": str(candidate["observations"]),
            "units": "bps of notional; 100bps=1pct; net=sum(position*realized)-sum(change*all_in)",
            "net_bps": f"{candidate['net_bps']:.2f}",
            "baseline_net_bps": f"{baseline['net_bps']:.2f}",
            "gross_bps": f"{candidate['gross_bps']:.2f}",
            "costs_bps": f"{candidate['costs_bps']:.2f}",
            "directional_accuracy": (
                f"{candidate['accuracy']:.4f}" if candidate["accuracy"] is not None else "n/a"
            ),
            "max_drawdown_bps": f"{candidate['drawdown_bps']:.2f}",
            "trade_count": str(candidate["trades"]),
            **{f"{item.name}_net_bps": f"{item.net_bps:.2f}" for item in window_results},
            **{
                f"{item.name}_baseline_net_bps": f"{item.baseline_net_bps:.2f}"
                for item in window_results
            },
            **{f"{item.name}_obs": str(item.observations) for item in window_results},
        },
        costs={
            "fee_bps": str(fee),
            "spread_bps": str(spread),
            "slippage_bps": str(slippage),
            "scenario": exposure.cost_scenario().scenario_id,
        },
        exposures={exposure.symbol: "1.0"},
        regime_slices=tuple(name for name, _, _ in candidate["regime"]),
        trial_count=1,
    )
    stored = registry.store(experiment, record_type="experiment")
    registry.link(stored_factor.artifact_id, stored.artifact_id, "factor_tested_by_experiment")
    split = PurgedWalkForward.make(
        train_start=datetime(2015, 1, 1, tzinfo=UTC),
        train_end=datetime(2020, 1, 1, tzinfo=UTC),
        validation_start=datetime(2020, 1, 1, tzinfo=UTC),
        validation_end=datetime(2026, 1, 1, tzinfo=UTC),
        embargo_seconds=21 * 86400,
    )
    window_lines = "; ".join(
        f"{item.name}={item.observations}obs net={item.net_bps:.2f}bps "
        f"baseline={item.baseline_net_bps:.2f}bps"
        for item in window_results
    )
    stored_report = registry.store(
        research_cards.ValidationReport(
            experiment_id=stored.artifact_id,
            leakage_check="snapshot-builder PIT proof plus past-only formation; no future bars read",
            temporal_validation=(
                f"purged walk-forward passed={split.passed}; windows enforced "
                f"{window_proof}; embargo=21d gaps proven from actual cutoffs; "
                "holdout 2023-2025 untouched during design"
            ),
            multiple_testing=f"trial_count=1; single preregistered policy {spec.policy_id}",
            independent_replay="deterministic ReplayEngine ordering with conflict detection",
            stability="regime: "
            + ",".join(name for name, _, _ in candidate["regime"])
            + "; windows: "
            + window_lines,
            portfolio_incrementality=(
                f"candidate_net={candidate['net_bps']:.2f}bps vs "
                f"baseline_net={baseline['net_bps']:.2f}bps"
            ),
            approved=False,
            rationale="research measurement only; no promotion requested",
        ),
        record_type="validation",
    )
    registry.link(stored.artifact_id, stored_report.artifact_id, "experiment_validated_by")
    return ExposureResult(
        symbol=exposure.symbol,
        observations=candidate["observations"],
        trade_count=candidate["trades"],
        gross_bps=candidate["gross_bps"],
        costs_bps=candidate["costs_bps"],
        net_bps=candidate["net_bps"],
        baseline_net_bps=baseline["net_bps"],
        directional_accuracy=candidate["accuracy"],
        max_drawdown_bps=candidate["drawdown_bps"],
        experiment_id=stored.artifact_id,
        regime_slices=candidate["regime"],
        windows=window_results,
    )


def run_replay(
    *,
    registry: ResearchRegistry,
    snapshot_root: Path,
    exposures: tuple[ExposureSpec, ...],
    spec: SignalPolicySpec,
    hypothesis_id: UUID | None = None,
    environment_hash: str | None = None,
    require_all_windows: bool = True,
) -> ReplayReport:
    """Run every exposure independently; one failure never masks another."""
    bars, snapshot_hash = load_snapshot_bars(snapshot_root)
    missing = [item.symbol for item in exposures if item.symbol not in bars]
    if missing:
        raise ValueError(f"snapshot lacks exposures: {', '.join(missing)}")
    factor_hash = dsl.canonical_hash(dsl.parse(momentum_dsl_source()))
    code_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    environment = environment_hash or hashlib.sha256(b"research-replay-env-v1").hexdigest()
    results: list[ExposureResult] = []
    failed: list[str] = []
    factor_id = uuid.uuid5(uuid.NAMESPACE_URL, f"research-replay:{factor_hash}")
    for exposure in exposures:
        tailored = ExposureSpec(
            symbol=exposure.symbol,
            bars=bars[exposure.symbol],
            fee_bps=exposure.fee_bps,
            spread_bps=exposure.spread_bps,
            slippage_bps=exposure.slippage_bps,
        )
        try:
            results.append(
                run_exposure(
                    registry,
                    tailored,
                    spec=spec,
                    factor_id=factor_id,
                    factor_hash=factor_hash,
                    snapshot_hash=snapshot_hash,
                    code_hash=code_hash,
                    environment_hash=environment,
                    hypothesis_id=hypothesis_id,
                    require_all_windows=require_all_windows,
                )
            )
        except Exception as exc:  # noqa: BLE001 - per-exposure failure is evidence
            failed.append(f"{exposure.symbol}: {type(exc).__name__}: {exc}")
    return ReplayReport(
        schema_version=REPLAY_SCHEMA_VERSION,
        factor_hash=factor_hash,
        snapshot_hash=snapshot_hash,
        code_hash=code_hash,
        policy_id=spec.policy_id,
        exposures=tuple(results),
        failed=tuple(failed),
    )


__all__ = [
    "CODE_ID",
    "FORMATION_BARS",
    "HOLDING_BARS",
    "HOLDOUT_YEARS",
    "MODEL_NAME",
    "REPLAY_SCHEMA_VERSION",
    "SCREEN_YEARS",
    "VALIDATION_YEARS",
    "WINDOW_EMBARGO_DAYS",
    "ExposureResult",
    "ExposureSpec",
    "ReplayReport",
    "WindowResult",
    "assign_window",
    "build_observations",
    "evaluate_momentum",
    "load_snapshot_bars",
    "momentum_dsl_source",
    "require_window_embargo",
    "run_exposure",
    "run_replay",
]
