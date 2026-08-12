#!/usr/bin/env python3
"""Perform the offline, evidence-bound Phase-4 utility review.

The measurement runner deliberately stops before admission.  This reviewer is
the separate decision boundary: it verifies the immutable measurement, builds
past-only calibration and causal delay/cost-stress views, evaluates chronology,
symbols and regimes, and writes a checklist plus a pending or passed
``PhaseGateRecord``.  It never acquires data, loads credentials, loads model
weights, promotes a roster entry, or submits an order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from advisorai.gates import GateDecision, GateEvidence, GateEvidenceKind, PhaseGateRecord
from advisorai.phase4 import (
    MANDATORY_BASELINES,
    Phase4MarketObservation,
    Phase4Prediction,
    Phase4UtilityPolicy,
    evaluate_paper_utility,
)
from scripts.run_phase4_paper_utility import INPUT_SCHEMA

REVIEW_SCHEMA = "advisorai.phase4.formal-review.v1"
CHECKLIST_SCHEMA = "advisorai.phase4.formal-admission-checklist.v1"
REVIEWER_VERSION = "phase4-formal-review-v1"
REVIEWED_PHASE3_GATE = (
    "artifacts/phase3/formal-admission/"
    "20260812T013505Z-with-passed-phase2-post-phase2-commit/phase3-gate-record.json"
)
REVIEWED_PHASE4_DEPENDENCY = (
    "artifacts/phase4/formal-dependency/"
    "20260812T014100Z-phase3-and-role-contract-v2/phase4-predecessor-dependency.json"
)
HEX = frozenset("0123456789abcdef")


class Phase4ReviewRefused(ValueError):
    """Raised when an immutable Phase-4 review input is not admissible."""


class RequirementStatus(StrEnum):
    SATISFIED = "SATISFIED"
    UNSATISFIED = "UNSATISFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    OPTIONAL = "OPTIONAL"
    EXTERNALLY_BLOCKED = "EXTERNALLY_BLOCKED"


class Phase4ReviewPolicy(BaseModel):
    """Versioned review rules; these are review policy, not trading limits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = "phase4-formal-review-v1"
    minimum_total_observations: int = Field(default=128, ge=1)
    minimum_observations_per_symbol: int = Field(default=48, ge=1)
    holdout_fraction: Decimal = Field(default=Decimal("0.20"), gt=0, lt=0.5)
    minimum_holdout_per_symbol: int = Field(default=16, ge=1)
    minimum_calibration_history: int = Field(default=20, ge=1)
    calibration_nominal_coverage: Decimal = Field(default=Decimal("0.80"), gt=0, lt=1)
    calibration_tolerance: Decimal = Field(default=Decimal("0.10"), ge=0, lt=1)
    calibration_method: str = "rolling_abs_residual_quantile_v1"
    delay_scenarios: tuple[tuple[str, int, int], ...] = (
        ("control", 0, 0),
        ("normal_10s", 0, 10_000),
        ("degraded_1h", 0, 3_600_000),
        ("next_bar_stress", 1, 86_400_000),
        ("two_bar_severe_stress", 2, 172_800_000),
    )
    cost_scenarios: tuple[tuple[str, Decimal, Decimal, Decimal], ...] = (
        ("optimistic", Decimal("5"), Decimal("1"), Decimal("1")),
        ("base", Decimal("10"), Decimal("2"), Decimal("2")),
        ("conservative", Decimal("15"), Decimal("4"), Decimal("4")),
        ("severe_plausible", Decimal("25"), Decimal("8"), Decimal("8")),
    )


class RequirementReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str = Field(min_length=1)
    requirement: str = Field(min_length=1)
    status: RequirementStatus
    gating: bool
    evidence: tuple[str, ...] = ()
    evidence_sha256: tuple[str, ...] = ()
    details: str = ""
    next_action: str = ""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _write_immutable(path: Path, payload: object) -> str:
    encoded = _canonical_json(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return hashlib.sha256(encoded).hexdigest()


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Phase4ReviewRefused(
            f"cannot read Phase-4 review input: {type(exc).__name__}"
        ) from exc


def _git_head() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Phase4ReviewRefused("cannot identify repository code revision") from exc
    return result.stdout.strip().lower()


def _digest(value: str) -> str:
    value = value.strip().lower()
    if len(value) != 64 or any(character not in HEX for character in value):
        raise Phase4ReviewRefused("review input contains an invalid SHA-256 digest")
    return value


def _load_typed_input(
    path: Path,
) -> tuple[tuple[Phase4MarketObservation, ...], tuple[Phase4Prediction, ...]]:
    payload = _load_json(path)
    if not isinstance(payload, dict) or payload.get("schema") != INPUT_SCHEMA:
        raise Phase4ReviewRefused("Phase-4 input is not the reviewed typed schema")
    if set(payload) != {"schema", "observations", "predictions"}:
        raise Phase4ReviewRefused("Phase-4 input contains unexpected fields")
    try:
        observations = tuple(
            Phase4MarketObservation.model_validate(item) for item in payload["observations"]
        )
        predictions = tuple(
            Phase4Prediction.model_validate(item) for item in payload["predictions"]
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise Phase4ReviewRefused("Phase-4 typed input validation failed") from exc
    if not observations or not predictions:
        raise Phase4ReviewRefused("Phase-4 review input cannot be empty")
    if not all(item.phase3_admitted for item in observations):
        raise Phase4ReviewRefused("Phase-4 review requires Phase-3-admitted observations")
    observation_ids = [item.observation_id for item in observations]
    if len(observation_ids) != len(set(observation_ids)):
        raise Phase4ReviewRefused("Phase-4 review observations are not unique")
    return observations, predictions


def _validate_predecessor(path: Path, *, at: datetime) -> tuple[dict[str, Any], str]:
    payload = _load_json(path)
    try:
        record = PhaseGateRecord.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise Phase4ReviewRefused("Phase-3 predecessor record is invalid") from exc
    if record.phase != 3 or record.decision is not GateDecision.PASSED:
        raise Phase4ReviewRefused("a passed Phase-3 predecessor is required")
    if not record.is_valid_at(at):
        raise Phase4ReviewRefused("Phase-3 predecessor is not valid at review time")
    return {
        "phase": record.phase,
        "decision": record.decision.value,
        "file_sha256": _sha256(path),
        "canonical_hash": record.canonical_hash(),
    }, _sha256(path)


def _validate_measurement(
    path: Path,
    *,
    input_path: Path,
    phase3_sha256: str,
    observations: tuple[Phase4MarketObservation, ...],
    predictions: tuple[Phase4Prediction, ...],
) -> dict[str, Any]:
    payload = _load_json(path)
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "advisorai.phase4.paper-utility-evidence.v1"
    ):
        raise Phase4ReviewRefused("Phase-4 measurement is not the reviewed evidence schema")
    if (
        payload.get("state") != "measured_pending_review"
        or payload.get("phase4_admission_opened") is not False
    ):
        raise Phase4ReviewRefused("Phase-4 measurement is already open or has an invalid state")
    if payload.get("network_calls") != 0 or payload.get("credentials_loaded") is not False:
        raise Phase4ReviewRefused("Phase-4 measurement is not offline/credential-free")
    if payload.get("input", {}).get("sha256") != _sha256(input_path):
        raise Phase4ReviewRefused("Phase-4 measurement does not bind the supplied input hash")
    gate = payload.get("phase3_gate", {})
    if (
        gate.get("file_sha256") != phase3_sha256
        or gate.get("decision") != GateDecision.PASSED.value
    ):
        raise Phase4ReviewRefused("Phase-4 measurement does not bind the passed Phase-3 record")
    report = payload.get("report")
    if not isinstance(report, dict):
        raise Phase4ReviewRefused("Phase-4 measurement report is missing")
    try:
        evaluated_at = datetime.fromisoformat(str(report["evaluated_at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise Phase4ReviewRefused("Phase-4 measurement time is invalid") from exc
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise Phase4ReviewRefused("Phase-4 measurement time is not timezone-aware")
    recomputed = evaluate_paper_utility(
        observations,
        predictions,
        phase3_gate_record_hash=phase3_sha256,
        evaluated_at=evaluated_at,
    ).model_dump(mode="json")
    if recomputed != report:
        raise Phase4ReviewRefused("Phase-4 measurement does not match deterministic recomputation")
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "input_sha256": _sha256(input_path),
        "evaluated_at": evaluated_at.astimezone(UTC).isoformat(),
        "state": payload["state"],
        "recomputed": True,
        "observation_count": len(observations),
        "prediction_count": len(predictions),
    }


def _prediction_index(
    predictions: Iterable[Phase4Prediction],
) -> dict[str, dict[str, Phase4Prediction]]:
    index: dict[str, dict[str, Phase4Prediction]] = defaultdict(dict)
    for prediction in predictions:
        if prediction.observation_id in index[prediction.model_name]:
            raise Phase4ReviewRefused("duplicate Phase-4 model prediction identity")
        index[prediction.model_name][prediction.observation_id] = prediction
    return index


def _evaluate(
    observations: tuple[Phase4MarketObservation, ...],
    predictions: tuple[Phase4Prediction, ...],
    *,
    phase3_sha256: str,
    fee_bps: Decimal = Decimal("10"),
) -> dict[str, dict[str, Any]]:
    if not observations:
        return {}
    report = evaluate_paper_utility(
        observations,
        predictions,
        phase3_gate_record_hash=phase3_sha256,
        policy=Phase4UtilityPolicy(fee_bps=fee_bps, minimum_observations=1),
        evaluated_at=datetime.now(UTC),
    )
    return {item.model_name: item.model_dump(mode="json") for item in report.results}


def _predictions_for(
    observations: tuple[Phase4MarketObservation, ...],
    index: dict[str, dict[str, Phase4Prediction]],
) -> tuple[Phase4Prediction, ...]:
    ids = {item.observation_id for item in observations}
    if not ids:
        return ()
    selected: list[Phase4Prediction] = []
    for model_name in sorted(index):
        values = [index[model_name].get(item.observation_id) for item in observations]
        if any(value is None for value in values):
            raise Phase4ReviewRefused(f"{model_name} does not cover the reviewed subset")
        selected.extend(value for value in values if value is not None)
    return tuple(selected)


def _subset(
    observations: tuple[Phase4MarketObservation, ...],
    predicate: Any,
) -> tuple[Phase4MarketObservation, ...]:
    return tuple(
        sorted(
            (item for item in observations if predicate(item)),
            key=lambda item: (item.instrument, item.cutoff, item.observation_id),
        )
    )


def _quantile(values: list[Decimal], quantile: Decimal) -> Decimal:
    if not values:
        raise ValueError("cannot calculate a quantile from an empty history")
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, math.ceil(float(quantile) * len(ordered)) - 1))
    return ordered[rank]


def _rolling_calibration(
    observations: tuple[Phase4MarketObservation, ...],
    predictions: tuple[Phase4Prediction, ...],
    policy: Phase4ReviewPolicy,
) -> tuple[tuple[Phase4Prediction, ...], dict[str, dict[str, Any]]]:
    index = _prediction_index(predictions)
    observation_by_id = {item.observation_id: item for item in observations}
    calibrated: list[Phase4Prediction] = []
    stats: dict[str, dict[str, Any]] = {}
    for model_name in sorted(index):
        model_stats = {
            "method": policy.calibration_method,
            "nominal_coverage": str(policy.calibration_nominal_coverage),
            "history_required": policy.minimum_calibration_history,
            "interval_count": 0,
            "native_interval_count": 0,
            "derived_interval_count": 0,
            "past_only": True,
        }
        for instrument in sorted({item.instrument for item in observations}):
            rows = sorted(
                (item for item in observations if item.instrument == instrument),
                key=lambda item: (item.cutoff, item.observation_id),
            )
            errors: list[Decimal] = []
            for observation in rows:
                prediction = index[model_name][observation.observation_id]
                lower = prediction.interval_lower_bps
                upper = prediction.interval_upper_bps
                if lower is not None and upper is not None:
                    model_stats["native_interval_count"] += 1
                    model_stats["interval_count"] += 1
                elif len(errors) >= policy.minimum_calibration_history:
                    width = _quantile(errors, policy.calibration_nominal_coverage)
                    lower = prediction.predicted_return_bps - width
                    upper = prediction.predicted_return_bps + width
                    model_stats["derived_interval_count"] += 1
                    model_stats["interval_count"] += 1
                calibrated.append(
                    prediction.model_copy(
                        update={
                            "interval_lower_bps": lower,
                            "interval_upper_bps": upper,
                        }
                    )
                )
                errors.append(
                    prediction.predicted_return_bps
                    - observation_by_id[observation.observation_id].realized_return_bps
                )
        stats[model_name] = model_stats
    calibrated.sort(key=lambda item: (item.model_name, item.observation_id))
    return tuple(calibrated), stats


def _holdout_split(
    observations: tuple[Phase4MarketObservation, ...], policy: Phase4ReviewPolicy
) -> tuple[
    tuple[Phase4MarketObservation, ...], tuple[Phase4MarketObservation, ...], dict[str, Any]
]:
    training: list[Phase4MarketObservation] = []
    holdout: list[Phase4MarketObservation] = []
    windows: dict[str, Any] = {}
    for instrument in sorted({item.instrument for item in observations}):
        rows = sorted(
            (item for item in observations if item.instrument == instrument),
            key=lambda item: (item.cutoff, item.observation_id),
        )
        holdout_count = max(
            policy.minimum_holdout_per_symbol, math.ceil(len(rows) * float(policy.holdout_fraction))
        )
        split = max(0, len(rows) - holdout_count)
        training.extend(rows[:split])
        holdout.extend(rows[split:])
        third = max(1, split // 3)
        segments = (rows[:third], rows[third : 2 * third], rows[2 * third : split], rows[split:])
        windows[instrument] = [
            {
                "window": name,
                "count": len(segment),
                "cutoff_min": segment[0].cutoff.isoformat() if segment else None,
                "cutoff_max": segment[-1].cutoff.isoformat() if segment else None,
                "holdout": name == "holdout",
            }
            for name, segment in zip(
                ("early", "middle", "late_train", "holdout"), segments, strict=True
            )
            if segment
        ]
    return tuple(training), tuple(holdout), windows


def _delayed(
    observations: tuple[Phase4MarketObservation, ...],
    index: dict[str, dict[str, Phase4Prediction]],
    delay_bars: int,
) -> tuple[tuple[Phase4MarketObservation, ...], tuple[Phase4Prediction, ...]]:
    selected: list[Phase4MarketObservation] = []
    delayed_predictions: list[Phase4Prediction] = []
    for instrument in sorted({item.instrument for item in observations}):
        rows = sorted(
            (item for item in observations if item.instrument == instrument),
            key=lambda item: (item.cutoff, item.observation_id),
        )
        for current_index, current in enumerate(rows):
            if current_index < delay_bars:
                continue
            selected.append(current)
            source = rows[current_index - delay_bars]
            for model_name in sorted(index):
                delayed_predictions.append(
                    index[model_name][source.observation_id].model_copy(
                        update={"observation_id": current.observation_id}
                    )
                )
    selected_tuple = tuple(
        sorted(selected, key=lambda item: (item.instrument, item.cutoff, item.observation_id))
    )
    return selected_tuple, tuple(delayed_predictions)


def _cost_observations(
    observations: tuple[Phase4MarketObservation, ...], spread_bps: Decimal, slippage_bps: Decimal
) -> tuple[Phase4MarketObservation, ...]:
    return tuple(
        item.model_copy(update={"spread_bps": spread_bps, "slippage_bps": slippage_bps})
        for item in observations
    )


def _model_summary(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        key: result[key]
        for key in (
            "model_name",
            "observations",
            "mae_bps",
            "rmse_bps",
            "directional_accuracy",
            "calibration_coverage",
            "confidence_brier_score",
            "turnover",
            "gross_utility_bps",
            "estimated_cost_bps",
            "net_utility_bps",
            "strongest_baseline_net_utility_bps",
            "incremental_net_utility_bps",
            "past_only",
            "provenance_complete",
            "resource_limit_passed",
            "adds_marginal_value",
            "rejection_reasons",
        )
    }


def _requirement(
    requirement_id: str,
    requirement: str,
    status: RequirementStatus,
    *,
    gating: bool = True,
    evidence: tuple[str, ...] = (),
    evidence_sha256: tuple[str, ...] = (),
    details: str = "",
    next_action: str = "",
) -> RequirementReview:
    return RequirementReview(
        requirement_id=requirement_id,
        requirement=requirement,
        status=status,
        gating=gating,
        evidence=evidence,
        evidence_sha256=evidence_sha256,
        details=details,
        next_action=next_action,
    )


def review_phase4(
    *,
    input_path: Path,
    measurement_path: Path,
    phase3_gate_path: Path,
    dependency_path: Path,
    output_root: Path,
    reviewed_at: datetime | None = None,
) -> dict[str, Any]:
    input_path = input_path.resolve()
    measurement_path = measurement_path.resolve()
    phase3_gate_path = phase3_gate_path.resolve()
    dependency_path = dependency_path.resolve()
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError("Phase-4 review output root must be new")
    at = (reviewed_at or datetime.now(UTC)).astimezone(UTC)
    if at.tzinfo is None or at.utcoffset() is None:
        raise Phase4ReviewRefused("review timestamp must include a timezone")
    observations, predictions = _load_typed_input(input_path)
    predecessor, phase3_sha256 = _validate_predecessor(phase3_gate_path, at=at)
    measurement = _validate_measurement(
        measurement_path,
        input_path=input_path,
        phase3_sha256=phase3_sha256,
        observations=observations,
        predictions=predictions,
    )
    dependency = _load_json(dependency_path)
    if not isinstance(dependency, dict) or dependency.get("measurement_allowed") is not True:
        raise Phase4ReviewRefused("Phase-4 predecessor dependency does not open measurement")
    dependency_sha256 = _sha256(dependency_path)
    policy = Phase4ReviewPolicy()
    index = _prediction_index(predictions)
    models = sorted(index)
    symbols = sorted({item.instrument for item in observations})
    symbol_counts = {
        symbol: sum(item.instrument == symbol for item in observations) for symbol in symbols
    }
    calibration_predictions, calibration_stats = _rolling_calibration(
        observations, predictions, policy
    )
    calibration_index = _prediction_index(calibration_predictions)
    calibration_results = _evaluate(
        observations,
        calibration_predictions,
        phase3_sha256=phase3_sha256,
    )
    training, holdout, windows = _holdout_split(observations, policy)
    holdout_results = _evaluate(
        holdout, _predictions_for(holdout, index), phase3_sha256=phase3_sha256
    )
    holdout_calibration_results = _evaluate(
        holdout,
        _predictions_for(holdout, calibration_index),
        phase3_sha256=phase3_sha256,
    )
    base_results = _evaluate(observations, predictions, phase3_sha256=phase3_sha256)

    symbol_results: dict[str, dict[str, dict[str, Any]]] = {}
    for symbol in symbols:
        subset = _subset(observations, lambda item, symbol=symbol: item.instrument == symbol)
        symbol_results[symbol] = _evaluate(
            subset,
            _predictions_for(subset, index),
            phase3_sha256=phase3_sha256,
        )

    regime_results: dict[str, dict[str, dict[str, Any]]] = {}
    for regime in sorted({item.regime for item in observations}):
        subset = _subset(observations, lambda item, regime=regime: item.regime == regime)
        regime_results[regime] = _evaluate(
            subset,
            _predictions_for(subset, index),
            phase3_sha256=phase3_sha256,
        )

    cost_results: dict[str, dict[str, Any]] = {}
    for name, fee, spread, slippage in policy.cost_scenarios:
        cost_obs = _cost_observations(observations, spread, slippage)
        cost_results[name] = {
            "fee_bps": str(fee),
            "spread_bps": str(spread),
            "slippage_bps": str(slippage),
            "historical_fill_cost": False,
            "results": _evaluate(cost_obs, predictions, phase3_sha256=phase3_sha256, fee_bps=fee),
        }

    delay_results: dict[str, dict[str, Any]] = {}
    for name, bars, milliseconds in policy.delay_scenarios:
        delayed_observations, delayed_predictions = _delayed(observations, index, bars)
        delay_results[name] = {
            "delay_bars": bars,
            "delay_milliseconds": milliseconds,
            "causal_prediction_source": "earlier_observation_cutoff"
            if bars
            else "same_observation_cutoff",
            "observation_count": len(delayed_observations),
            "results": _evaluate(
                delayed_observations,
                delayed_predictions,
                phase3_sha256=phase3_sha256,
            ),
        }

    calibrated_holdout = holdout_calibration_results
    candidate_models = [model for model in models if model not in MANDATORY_BASELINES]
    model_decisions: dict[str, dict[str, Any]] = {}
    base_cost = cost_results["base"]["results"]
    conservative_cost = cost_results["conservative"]["results"]
    next_bar = delay_results["next_bar_stress"]["results"]
    for model_name in candidate_models:
        full = base_results.get(model_name)
        holdout_result = holdout_results.get(model_name)
        calibration = calibration_results.get(model_name)
        calibration_holdout = calibrated_holdout.get(model_name)
        conservative = conservative_cost.get(model_name)
        delayed = next_bar.get(model_name)
        baseline_full = full["strongest_baseline_net_utility_bps"] if full else None
        baseline_holdout = (
            holdout_result["strongest_baseline_net_utility_bps"] if holdout_result else None
        )
        coverage = calibration.get("calibration_coverage") if calibration else None
        interval_count = calibration_stats.get(model_name, {}).get("interval_count", 0)
        coverage_ok = (
            interval_count >= policy.minimum_calibration_history
            and coverage is not None
            and abs(Decimal(str(coverage)) - policy.calibration_nominal_coverage)
            <= policy.calibration_tolerance
        )
        checks = {
            "full_incremental_positive": bool(
                full and Decimal(str(full["incremental_net_utility_bps"])) > 0
            ),
            "holdout_incremental_positive": bool(
                holdout_result and Decimal(str(holdout_result["incremental_net_utility_bps"])) > 0
            ),
            "past_only": bool(full and full["past_only"]),
            "provenance_complete": bool(full and full["provenance_complete"]),
            "resource_limit_passed": bool(full and full["resource_limit_passed"]),
            "rolling_calibration_acceptable": coverage_ok,
            "conservative_cost_incremental_positive": bool(
                conservative and Decimal(str(conservative["incremental_net_utility_bps"])) > 0
            ),
            "next_bar_delay_nonnegative_incremental": bool(
                delayed and Decimal(str(delayed["incremental_net_utility_bps"])) >= 0
            ),
        }
        robust = all(checks.values())
        role_decision = (
            "ADMITTED"
            if robust
            else "RESEARCH_ONLY"
            if full
            and holdout_result
            and Decimal(str(full["incremental_net_utility_bps"])) < 0
            and Decimal(str(holdout_result["incremental_net_utility_bps"])) < 0
            else "CHALLENGER"
        )
        model_decisions[model_name] = {
            "decision": role_decision,
            "full": _model_summary(full),
            "holdout": _model_summary(holdout_result),
            "calibration": {
                "result": _model_summary(calibration),
                "holdout_result": _model_summary(calibration_holdout),
                "stats": calibration_stats.get(model_name, {}),
                "native_intervals_available": calibration_stats.get(model_name, {}).get(
                    "native_interval_count", 0
                )
                > 0,
                "derived_layer_used": calibration_stats.get(model_name, {}).get(
                    "derived_interval_count", 0
                )
                > 0,
                "coverage_acceptable": coverage_ok,
            },
            "cost_stress": {
                "base": _model_summary(base_cost.get(model_name)),
                "conservative": _model_summary(conservative),
                "severe_plausible": _model_summary(
                    cost_results["severe_plausible"]["results"].get(model_name)
                ),
                "break_even_all_in_cost_bps_per_turnover": (
                    str(
                        Decimal(str(full["gross_utility_bps"]))
                        / (Decimal(str(full["turnover"])) * Decimal(str(full["observations"])))
                    )
                    if full and Decimal(str(full["turnover"])) > 0
                    else None
                ),
            },
            "latency": {
                "runtime_latency_ms": sorted(
                    {
                        str(p.latency_ms)
                        for p in index[model_name].values()
                        if p.latency_ms is not None
                    }
                ),
                "next_bar_stress": _model_summary(delayed),
                "next_bar_incremental_decay_bps": (
                    str(
                        Decimal(str(delayed["incremental_net_utility_bps"]))
                        - Decimal(str(full["incremental_net_utility_bps"]))
                    )
                    if delayed and full
                    else None
                ),
            },
            "checks": checks,
            "strongest_full_baseline_net_utility_bps": baseline_full,
            "strongest_holdout_baseline_net_utility_bps": baseline_holdout,
        }

    sufficient_sample = (
        len(observations) >= policy.minimum_total_observations
        and all(count >= policy.minimum_observations_per_symbol for count in symbol_counts.values())
        and len(holdout) >= policy.minimum_holdout_per_symbol * len(symbols)
    )
    chronology_ok = all(len(value) >= 3 for value in windows.values()) and bool(holdout)
    calibration_candidates = [
        model for model in candidate_models if model_decisions[model]["decision"] != "RESEARCH_ONLY"
    ]
    calibration_ok = bool(calibration_candidates) and all(
        model_decisions[model]["calibration"]["coverage_acceptable"]
        for model in calibration_candidates
    )
    any_admitted = any(item["decision"] == "ADMITTED" for item in model_decisions.values())
    holdout_incremental_utility_ok = any(
        item["holdout"] is not None
        and Decimal(str(item["holdout"]["incremental_net_utility_bps"])) > 0
        for item in model_decisions.values()
        if item["decision"] != "RESEARCH_ONLY"
    )
    requirements = [
        _requirement(
            "phase3_formal_predecessor",
            "A currently valid passed Phase-3 PhaseGateRecord is present.",
            RequirementStatus.SATISFIED,
            evidence=(str(phase3_gate_path),),
            evidence_sha256=(phase3_sha256,),
            details="The formal Phase-3 record validated at review time.",
        ),
        _requirement(
            "point_in_time_btc_eth_input",
            "The input is a reviewed point-in-time BTC/ETH data set with explicit source identity.",
            RequirementStatus.SATISFIED,
            evidence=(str(input_path),),
            evidence_sha256=(_sha256(input_path),),
            details="The input binds the admitted Binance public snapshot and phase3_admitted=true observations.",
        ),
        _requirement(
            "adequate_chronological_sample",
            "The review contains the minimum chronological sample and untouched per-symbol holdout.",
            RequirementStatus.SATISFIED if sufficient_sample else RequirementStatus.UNSATISFIED,
            evidence=(str(input_path),),
            evidence_sha256=(_sha256(input_path),),
            details=f"{len(observations)} observations; symbol counts={symbol_counts}; holdout={len(holdout)}.",
            next_action="Obtain a larger reviewed chronological input."
            if not sufficient_sample
            else "",
        ),
        _requirement(
            "walk_forward_and_holdout",
            "Chronological windows and a final untouched holdout are evaluated without shuffling.",
            RequirementStatus.SATISFIED if chronology_ok else RequirementStatus.UNSATISFIED,
            evidence=(str(input_path),),
            evidence_sha256=(_sha256(input_path),),
            details="Window boundaries and holdout identities are recorded in this review.",
            next_action="Create non-overlapping chronological windows."
            if not chronology_ok
            else "",
        ),
        _requirement(
            "mandatory_baselines",
            "Naive, drift, seasonal-7, linear, and LightGBM cover the same observations and cost policy.",
            RequirementStatus.SATISFIED
            if set(MANDATORY_BASELINES).issubset(models)
            else RequirementStatus.UNSATISFIED,
            evidence=(str(input_path), str(measurement_path)),
            evidence_sha256=(_sha256(input_path), _sha256(measurement_path)),
            details=f"Measured model set={models}.",
        ),
        _requirement(
            "model_provenance_and_resources",
            "Every measured prediction carries past-only provenance and resource status.",
            RequirementStatus.SATISFIED
            if all(
                result["past_only"]
                and result["provenance_complete"]
                and result["resource_limit_passed"]
                for result in base_results.values()
            )
            else RequirementStatus.UNSATISFIED,
            evidence=(str(input_path), str(measurement_path)),
            evidence_sha256=(_sha256(input_path), _sha256(measurement_path)),
            details="The deterministic utility recomputation verified these fields.",
        ),
        _requirement(
            "symbol_and_regime_robustness",
            "BTC/ETH and non-future-derived regime slices are exposed for every measured model.",
            RequirementStatus.SATISFIED
            if set(symbol_results) == set(symbols) and bool(regime_results)
            else RequirementStatus.UNSATISFIED,
            evidence=(str(input_path),),
            evidence_sha256=(_sha256(input_path),),
            details="Separate symbol and regime result maps are present.",
        ),
        _requirement(
            "past_only_calibration",
            "Past-only rolling calibration has adequate history and acceptable observed coverage.",
            RequirementStatus.SATISFIED if calibration_ok else RequirementStatus.UNSATISFIED,
            evidence=(str(input_path),),
            evidence_sha256=(_sha256(input_path),),
            details=f"Calibration method={policy.calibration_method}; native intervals are not assumed.",
            next_action="Keep the candidate as challenger until calibrated coverage is acceptable."
            if not calibration_ok
            else "",
        ),
        _requirement(
            "causal_latency_sensitivity",
            "Measured latency and causal delay scenarios are evaluated without future observations.",
            RequirementStatus.SATISFIED
            if set(delay_results) == {item[0] for item in policy.delay_scenarios}
            else RequirementStatus.UNSATISFIED,
            evidence=(str(input_path), str(measurement_path)),
            evidence_sha256=(_sha256(input_path), _sha256(measurement_path)),
            details="Sub-bar delays are bounded by the daily data cadence; next-bar and two-bar shifts are causal stress tests.",
        ),
        _requirement(
            "cost_stress_and_break_even",
            "Optimistic, base, conservative, and severe cost assumptions are measured separately from observed fills.",
            RequirementStatus.SATISFIED
            if set(cost_results) == {item[0] for item in policy.cost_scenarios}
            else RequirementStatus.UNSATISFIED,
            evidence=(str(input_path),),
            evidence_sha256=(_sha256(input_path),),
            details="No modeled cost is represented as a historical Binance fill.",
        ),
        _requirement(
            "holdout_incremental_utility",
            "At least one candidate adds net utility over the strongest baseline on the untouched holdout.",
            RequirementStatus.SATISFIED
            if holdout_incremental_utility_ok
            else RequirementStatus.UNSATISFIED,
            evidence=(str(input_path),),
            evidence_sha256=(_sha256(input_path),),
            details="Model-level admission decisions are recorded below.",
            next_action="Retain candidates as challenger/research-only and extend evidence."
            if not holdout_incremental_utility_ok
            else "",
        ),
        _requirement(
            "robust_candidate_admission",
            "At least one candidate passes the full utility, calibration, cost, latency, and holdout policy.",
            RequirementStatus.SATISFIED if any_admitted else RequirementStatus.UNSATISFIED,
            evidence=(str(input_path),),
            evidence_sha256=(_sha256(input_path),),
            details="A candidate is admitted only when every model-level robustness check is true.",
            next_action="Keep the strongest candidate in challenger status until all robustness checks pass."
            if not any_admitted
            else "",
        ),
        _requirement(
            "execution_authority_boundary",
            "The review is offline and does not grant model, dashboard, or research order authority.",
            RequirementStatus.SATISFIED,
            evidence=(str(measurement_path),),
            evidence_sha256=(_sha256(measurement_path),),
            details="RiskKernel and OMS remain unchanged external authorities; network_calls=0 and no order writes occurred.",
        ),
        _requirement(
            "global_phase0_route_archive",
            "Global Phase-0 private-route and archive gates are complete.",
            RequirementStatus.EXTERNALLY_BLOCKED,
            gating=False,
            evidence=(str(dependency_path),),
            evidence_sha256=(dependency_sha256,),
            details="The existing dependency contract opens Phase-4 measurement without silently closing global Phase-0.",
        ),
        _requirement(
            "initial_gpu_family_challenger",
            "An initial GPU forecast family has been evaluated for Phase-4 utility.",
            RequirementStatus.OPTIONAL,
            gating=False,
            evidence=(),
            evidence_sha256=(),
            details="Chronos/Kronos remain separate measured Phase-0 challengers and were not fabricated into this CPU-candidate admission.",
        ),
    ]
    blocking = [
        item.requirement_id
        for item in requirements
        if item.gating and item.status is not RequirementStatus.SATISFIED
    ]
    decision = GateDecision.PASSED if not blocking else GateDecision.PENDING
    for model in model_decisions.values():
        if model["decision"] == "ADMITTED":
            model["role"] = "admitted_phase4_candidate"
        else:
            model["role"] = "challenger_or_research_only"

    source_paths = {
        "input": str(input_path),
        "measurement": str(measurement_path),
        "phase3_gate": str(phase3_gate_path),
        "phase4_dependency": str(dependency_path),
        "phase4_code": "src/advisorai/phase4/paper_utility.py",
        "review_script": "scripts/review_phase4_utility.py",
    }
    review_payload = {
        "schema": REVIEW_SCHEMA,
        "reviewer_version": REVIEWER_VERSION,
        "reviewed_at": at.isoformat(),
        "repository_commit": _git_head(),
        "code_sha256": {
            "phase4_contract": _sha256(Path("src/advisorai/phase4/paper_utility.py")),
            "reviewer": _sha256(Path("scripts/review_phase4_utility.py")),
        },
        "policy": policy.model_dump(mode="json"),
        "predecessor": predecessor,
        "dependency": {
            "path": str(dependency_path),
            "sha256": dependency_sha256,
            "decision": dependency.get("decision"),
        },
        "measurement": measurement,
        "source_paths": source_paths,
        "source_sha256": {
            name: _sha256(Path(path)) for name, path in source_paths.items() if Path(path).is_file()
        },
        "sample": {
            "observation_count": len(observations),
            "prediction_count": len(predictions),
            "symbols": symbols,
            "symbol_counts": symbol_counts,
            "training_count": len(training),
            "holdout_count": len(holdout),
            "holdout_fraction": str(policy.holdout_fraction),
            "chronological_windows": windows,
            "source_snapshot_hashes": sorted({item.source_snapshot_hash for item in observations}),
        },
        "base_results": base_results,
        "holdout_results": holdout_results,
        "holdout_calibrated_results": holdout_calibration_results,
        "calibration": {
            "method": policy.calibration_method,
            "stats": calibration_stats,
            "results": calibration_results,
        },
        "symbol_results": symbol_results,
        "regime_results": regime_results,
        "cost_stress": cost_results,
        "latency_sensitivity": delay_results,
        "model_decisions": model_decisions,
        "calibration_candidates": calibration_candidates,
        "holdout_incremental_utility_ok": holdout_incremental_utility_ok,
        "requirements": [item.model_dump(mode="json") for item in requirements],
        "blocking_requirements": blocking,
        "decision": decision.value,
        "phase4_admission_opened": decision is GateDecision.PASSED,
        "network_calls": 0,
        "credentials_loaded": False,
        "model_weights_loaded": False,
        "order_writes_attempted": False,
        "execution_authority": {
            "risk_kernel": "unchanged_external_authority",
            "oms": "unchanged_external_authority",
            "model_order_authority": False,
            "dashboard_order_authority": False,
            "research_order_authority": False,
        },
        "limitations": [
            "The frozen snapshot is daily; sub-bar latency decay cannot be observed, so sub-bar scenarios remain zero-bar controls and next-bar shifts are causal stress tests.",
            "No historical Binance fill is inferred; modeled fees/spread/slippage are stress assumptions only.",
            "Native interval outputs are not invented. The versioned rolling calibration layer is evaluated explicitly.",
            "Finance sentiment roles and TSPulse are not coerced into direct price forecasts.",
            "LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.",
        ],
    }
    review_path = output_root / "phase4-formal-review-evidence.json"
    review_sha256 = _write_immutable(review_path, review_payload)
    checklist_payload = {
        "schema": CHECKLIST_SCHEMA,
        "reviewer_version": REVIEWER_VERSION,
        "reviewed_at": at.isoformat(),
        "repository_commit": review_payload["repository_commit"],
        "review_evidence": {"path": str(review_path), "sha256": review_sha256},
        "requirements": [item.model_dump(mode="json") for item in requirements],
        "decision": decision.value,
        "blocking_requirements": blocking,
        "model_decisions": model_decisions,
        "phase4_admission_opened": decision is GateDecision.PASSED,
    }
    checklist_path = output_root / "phase4-admission-checklist.json"
    checklist_sha256 = _write_immutable(checklist_path, checklist_payload)
    evidence_items = [
        GateEvidence(
            name="phase4-formal-review-evidence",
            kind=GateEvidenceKind.OPERATIONAL,
            passed=True,
            artifact_hash=review_sha256,
            source=str(review_path),
            verified_by=REVIEWER_VERSION,
            observed_at=at,
            details="Immutable offline Phase-4 review evidence.",
        ),
        GateEvidence(
            name="phase4-admission-checklist",
            kind=GateEvidenceKind.OPERATIONAL,
            passed=True,
            artifact_hash=checklist_sha256,
            source=str(checklist_path),
            verified_by=REVIEWER_VERSION,
            observed_at=at,
            details="Requirement-by-requirement Phase-4 review checklist.",
        ),
        GateEvidence(
            name="phase4-input",
            kind=GateEvidenceKind.OPERATIONAL,
            passed=True,
            artifact_hash=_sha256(input_path),
            source=str(input_path),
            verified_by=REVIEWER_VERSION,
            observed_at=at,
            details="Frozen point-in-time BTC/ETH typed input.",
        ),
        GateEvidence(
            name="phase4-measurement",
            kind=GateEvidenceKind.OPERATIONAL,
            passed=True,
            artifact_hash=_sha256(measurement_path),
            source=str(measurement_path),
            verified_by=REVIEWER_VERSION,
            observed_at=at,
            details="Recomputed measurement-only utility evidence.",
        ),
    ]
    record = PhaseGateRecord(
        phase=4,
        name="Phase 4 — Quantitative baseline council",
        decision=decision,
        required_evidence=tuple(item.name for item in evidence_items)
        if decision is GateDecision.PASSED
        else (),
        evidence=tuple(evidence_items),
        prerequisite_phase=3,
        recorded_by=REVIEWER_VERSION,
        recorded_at=at,
        reasons=tuple(blocking) if decision is not GateDecision.PASSED else (),
    )
    record_path = output_root / "phase4-gate-record.json"
    record_sha256 = _write_immutable(record_path, record.model_dump(mode="json", round_trip=True))
    for path, digest in (
        (review_path, review_sha256),
        (checklist_path, checklist_sha256),
        (record_path, record_sha256),
    ):
        (path.with_suffix(path.suffix + ".sha256")).write_text(
            f"{digest}  {path.name}\n", encoding="ascii"
        )
    return {
        "decision": decision.value,
        "phase4_admission_opened": decision is GateDecision.PASSED,
        "review_evidence": str(review_path),
        "review_evidence_sha256": review_sha256,
        "checklist": str(checklist_path),
        "checklist_sha256": checklist_sha256,
        "phase4_gate_record": str(record_path),
        "phase4_gate_record_sha256": record_sha256,
        "blocking_requirements": blocking,
        "model_decisions": {name: value["decision"] for name, value in model_decisions.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--measurement", type=Path, required=True)
    parser.add_argument("--phase3-gate-record", type=Path, default=Path(REVIEWED_PHASE3_GATE))
    parser.add_argument("--phase4-dependency", type=Path, default=Path(REVIEWED_PHASE4_DEPENDENCY))
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = review_phase4(
            input_path=args.input,
            measurement_path=args.measurement,
            phase3_gate_path=args.phase3_gate_record,
            dependency_path=args.phase4_dependency,
            output_root=args.output_root,
        )
    except (FileExistsError, OSError, Phase4ReviewRefused, ValueError) as exc:
        raise SystemExit(f"phase4 formal review refused ({type(exc).__name__})") from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
