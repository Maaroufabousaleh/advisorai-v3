#!/usr/bin/env python3
"""Run a bounded, offline Phase-4 signal-policy research study.

The study explains TTM-R2 turnover/cost sensitivity and searches a small,
pre-registered deterministic forecast-to-signal policy set.  Policy selection
uses only the first 48 chronological observations per instrument: 32 tuning
observations followed by 16 validation observations.  The final 16
observations per instrument are the consumed Phase-4 holdout and are never
used for selection or policy scoring.

This command does not acquire data, load credentials or model weights, call a
network, submit orders, alter the RiskKernel/OMS, or create a PhaseGateRecord.
It produces research evidence only; an independent future/PIT evaluation is
required before this work can support Phase-4 admission.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from advisorai.phase4 import (
    MANDATORY_BASELINES,
    Phase4MarketObservation,
    Phase4Prediction,
    PolicyUtilityMetrics,
    SignalCostScenario,
    apply_signal_policy,
    candidate_policy_specs,
    compare_policy_paths,
    evaluate_policy_signals,
    summarize_prediction_distribution,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
INPUT_SCHEMA = "advisorai.phase4.paper-utility-input.v1"
MEASUREMENT_SCHEMA = "advisorai.phase4.paper-utility-evidence.v1"
EVIDENCE_SCHEMA = "advisorai.phase4.signal-policy-research.v1"
POLICY_PARTITION_VERSION = "phase4-policy-dev-validation-v1"
POLICY_SELECTION_OBJECTIVE = (
    "maximize validation conservative-cost incremental net utility versus the "
    "strongest mandatory baseline; tie-break lower turnover, then higher net utility, "
    "without reading the consumed holdout"
)
SCENARIOS = (
    SignalCostScenario(
        scenario_id="optimistic",
        fee_bps=Decimal("5"),
        spread_bps=Decimal("1"),
        slippage_bps=Decimal("1"),
    ),
    SignalCostScenario(
        scenario_id="base",
        fee_bps=Decimal("10"),
        spread_bps=Decimal("2"),
        slippage_bps=Decimal("2"),
    ),
    SignalCostScenario(
        scenario_id="conservative",
        fee_bps=Decimal("15"),
        spread_bps=Decimal("4"),
        slippage_bps=Decimal("4"),
    ),
    SignalCostScenario(
        scenario_id="severe_plausible",
        fee_bps=Decimal("25"),
        spread_bps=Decimal("8"),
        slippage_bps=Decimal("8"),
    ),
)
ANALYSIS_MODELS = (*MANDATORY_BASELINES, "ttm-r2")
HEX = frozenset("0123456789abcdef")


class SignalPolicyResearchRefused(ValueError):
    """Raised when the frozen research inputs are not admissible."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _code_hashes() -> dict[str, str]:
    return {
        "src/advisorai/phase4/signal_policy.py": _sha256(
            REPOSITORY_ROOT / "src/advisorai/phase4/signal_policy.py"
        ),
        "scripts/evaluate_phase4_signal_policies.py": _sha256(Path(__file__).resolve()),
    }


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


def _write_immutable_bytes(path: Path, encoded: bytes) -> str:
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
        raise SignalPolicyResearchRefused(
            f"cannot read frozen Phase-4 artifact ({type(exc).__name__})"
        ) from exc


def _load_input(
    path: Path,
) -> tuple[tuple[Phase4MarketObservation, ...], tuple[Phase4Prediction, ...]]:
    payload = _load_json(path)
    if not isinstance(payload, dict) or payload.get("schema") != INPUT_SCHEMA:
        raise SignalPolicyResearchRefused("input is not the reviewed Phase-4 typed schema")
    if set(payload) != {"schema", "observations", "predictions"}:
        raise SignalPolicyResearchRefused("input contains unexpected fields")
    try:
        observations = tuple(
            Phase4MarketObservation.model_validate(item) for item in payload["observations"]
        )
        predictions = tuple(
            Phase4Prediction.model_validate(item) for item in payload["predictions"]
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise SignalPolicyResearchRefused("frozen input typed validation failed") from exc
    if not observations or not predictions:
        raise SignalPolicyResearchRefused("frozen input cannot be empty")
    if not all(item.phase3_admitted for item in observations):
        raise SignalPolicyResearchRefused("all observations must be Phase-3 admitted")
    if len({item.observation_id for item in observations}) != len(observations):
        raise SignalPolicyResearchRefused("observation identities must be unique")
    return observations, predictions


def _validate_measurement(path: Path, *, input_sha256: str) -> dict[str, Any]:
    payload = _load_json(path)
    if not isinstance(payload, dict) or payload.get("schema") != MEASUREMENT_SCHEMA:
        raise SignalPolicyResearchRefused("measurement is not the reviewed Phase-4 schema")
    if payload.get("phase4_admission_opened") is not False:
        raise SignalPolicyResearchRefused("measurement cannot have opened Phase-4 admission")
    if payload.get("state") != "measured_pending_review":
        raise SignalPolicyResearchRefused("measurement is not pending review")
    input_record = payload.get("input")
    if not isinstance(input_record, dict) or input_record.get("sha256") != input_sha256:
        raise SignalPolicyResearchRefused("measurement is not bound to the frozen input")
    return {
        "path": str(path.relative_to(REPOSITORY_ROOT)),
        "sha256": _sha256(path),
        "state": payload["state"],
        "input_sha256": input_sha256,
    }


def _validate_review(path: Path, *, input_sha256: str) -> dict[str, Any]:
    payload = _load_json(path)
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "advisorai.phase4.formal-review.v2"
    ):
        raise SignalPolicyResearchRefused("formal review is not the corrected Phase-4 schema")
    source_sha256 = payload.get("source_sha256")
    if not isinstance(source_sha256, dict) or source_sha256.get("input") != input_sha256:
        raise SignalPolicyResearchRefused("formal review is not bound to the frozen input")
    if payload.get("decision") != "pending":
        raise SignalPolicyResearchRefused("signal research must start from the pending review")
    blockers = payload.get("blocking_requirements")
    if blockers != ["robust_candidate_admission"]:
        raise SignalPolicyResearchRefused("formal review blocker set is not the current one")
    return {
        "path": str(path.relative_to(REPOSITORY_ROOT)),
        "sha256": _sha256(path),
        "decision": payload["decision"],
        "blocking_requirements": blockers,
        "reviewer_version": payload.get("reviewer_version"),
    }


def _prediction_index(
    predictions: Sequence[Phase4Prediction],
) -> dict[str, dict[str, Phase4Prediction]]:
    indexed: dict[str, dict[str, Phase4Prediction]] = {}
    for prediction in predictions:
        by_id = indexed.setdefault(prediction.model_name, {})
        if prediction.observation_id in by_id:
            raise SignalPolicyResearchRefused("duplicate model prediction identity")
        by_id[prediction.observation_id] = prediction
    expected = {item.observation_id for item in predictions}
    if not expected:
        raise SignalPolicyResearchRefused("prediction input cannot be empty")
    return indexed


def _partition(
    observations: Sequence[Phase4MarketObservation],
) -> dict[str, tuple[Phase4MarketObservation, ...]]:
    grouped: dict[str, list[Phase4MarketObservation]] = {}
    for observation in observations:
        grouped.setdefault(observation.instrument, []).append(observation)
    if set(grouped) != {"BTCUSDT", "ETHUSDT"}:
        raise SignalPolicyResearchRefused("policy study requires exactly BTCUSDT and ETHUSDT")
    partitions: dict[str, list[Phase4MarketObservation]] = {
        "tuning": [],
        "validation": [],
        "holdout_consumed": [],
    }
    for _instrument, values in sorted(grouped.items()):
        ordered = sorted(values, key=lambda item: (item.cutoff, item.observation_id))
        if len(ordered) != 64:
            raise SignalPolicyResearchRefused("each instrument must contain 64 frozen observations")
        partitions["tuning"].extend(ordered[:32])
        partitions["validation"].extend(ordered[32:48])
        partitions["holdout_consumed"].extend(ordered[48:])
    return {key: tuple(value) for key, value in partitions.items()}


def _partition_summary(values: Sequence[Phase4MarketObservation]) -> dict[str, Any]:
    by_instrument: dict[str, list[Phase4MarketObservation]] = {}
    for item in values:
        by_instrument.setdefault(item.instrument, []).append(item)
    return {
        "observations": len(values),
        "by_instrument": {
            instrument: {
                "count": len(items),
                "first_cutoff": min(item.cutoff for item in items).isoformat(),
                "last_cutoff": max(item.cutoff for item in items).isoformat(),
            }
            for instrument, items in sorted(by_instrument.items())
        },
    }


def _model_predictions(
    indexed: dict[str, dict[str, Phase4Prediction]],
    model_name: str,
    observations: Sequence[Phase4MarketObservation],
) -> tuple[Phase4Prediction, ...]:
    try:
        by_id = indexed[model_name]
        return tuple(by_id[item.observation_id] for item in observations)
    except KeyError as exc:
        raise SignalPolicyResearchRefused(f"missing prediction path: {model_name}") from exc


def _metrics_dump(metric: PolicyUtilityMetrics) -> dict[str, Any]:
    return metric.model_dump(mode="json")


def _break_even_all_in_cost(metrics: dict[str, Any]) -> str | None:
    turnover_units = Decimal(metrics["turnover_units"])
    if turnover_units <= 0:
        return None
    return str(Decimal(metrics["gross_utility_bps"]) / turnover_units)


def _validation_attempt(
    spec: Any,
    partitions: dict[str, tuple[Phase4MarketObservation, ...]],
    indexed: dict[str, dict[str, Phase4Prediction]],
) -> dict[str, Any]:
    development = partitions["tuning"] + partitions["validation"]
    metrics: dict[str, dict[str, dict[str, Any]]] = {}
    for model_name in (*MANDATORY_BASELINES, "ttm-r2"):
        predictions = _model_predictions(indexed, model_name, development)
        signals = apply_signal_policy(development, predictions, spec)
        validation_ids = {item.observation_id for item in partitions["validation"]}
        validation_observations = tuple(
            item for item in development if item.observation_id in validation_ids
        )
        validation_signals = tuple(
            signal for signal in signals if signal.observation_id in validation_ids
        )
        metrics[model_name] = {
            scenario.scenario_id: _metrics_dump(
                evaluate_policy_signals(validation_observations, validation_signals, scenario)
            )
            for scenario in SCENARIOS
        }
    conservative = metrics["ttm-r2"]["conservative"]
    baseline_metrics = {
        model_name: metrics[model_name]["conservative"]["net_utility_bps"]
        for model_name in MANDATORY_BASELINES
    }
    strongest_model, strongest_net = max(
        baseline_metrics.items(), key=lambda item: (Decimal(item[1]), item[0])
    )
    ttm_net = Decimal(conservative["net_utility_bps"])
    ttm_incremental = ttm_net - Decimal(str(strongest_net))
    return {
        "policy": spec.model_dump(mode="json"),
        "tuning_partition": _partition_summary(partitions["tuning"]),
        "validation_partition": _partition_summary(partitions["validation"]),
        "validation_metrics": metrics,
        "selection_metrics": {
            "scenario_id": "conservative",
            "ttm_r2_net_utility_bps": str(ttm_net),
            "strongest_baseline_model": strongest_model,
            "strongest_baseline_net_utility_bps": str(strongest_net),
            "incremental_net_utility_bps": str(ttm_incremental),
            "ttm_r2_turnover": conservative["turnover"],
            "ttm_r2_signal_change_count": conservative["signal_change_count"],
            "ttm_r2_break_even_all_in_cost_bps": _break_even_all_in_cost(conservative),
        },
        "holdout_used_for_selection": False,
    }


def _select_attempt(attempts: Sequence[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        attempts,
        key=lambda attempt: (
            Decimal(attempt["selection_metrics"]["incremental_net_utility_bps"]),
            -Decimal(attempt["selection_metrics"]["ttm_r2_turnover"]),
            Decimal(attempt["selection_metrics"]["ttm_r2_net_utility_bps"]),
            attempt["policy"]["policy_id"],
        ),
        reverse=True,
    )
    best = ordered[0]
    incremental = Decimal(best["selection_metrics"]["incremental_net_utility_bps"])
    return {
        "status": (
            "DEVELOPMENT_POLICY_HAS_POSITIVE_INCREMENTAL_VALUE"
            if incremental > 0
            else "NO_DEVELOPMENT_POLICY_HAS_POSITIVE_INCREMENTAL_VALUE"
        ),
        "selected_policy_id": best["policy"]["policy_id"] if incremental > 0 else None,
        "selected_policy_frozen": incremental > 0,
        "selection_objective": POLICY_SELECTION_OBJECTIVE,
        "best_validation_incremental_net_utility_bps": str(incremental),
        "ranked_policy_ids": [attempt["policy"]["policy_id"] for attempt in ordered],
        "holdout_used": False,
        "selection_note": (
            "A positive development result is research selection only; it is not "
            "Phase-4 admission and requires an independent future/PIT evaluation."
            if incremental > 0
            else "No bounded policy adds positive conservative-cost value on development validation."
        ),
    }


def _full_diagnostics(
    observations: Sequence[Phase4MarketObservation],
    indexed: dict[str, dict[str, Phase4Prediction]],
) -> dict[str, Any]:
    model_names = ("ttm-r2", "lightgbm", "drift")
    sign_policy = next(
        spec for spec in candidate_policy_specs() if spec.policy_id == "sign-only-v1"
    )
    all_metrics: dict[str, dict[str, dict[str, Any]]] = {}
    signals: dict[str, tuple[Any, ...]] = {}
    for model_name in model_names:
        predictions = _model_predictions(indexed, model_name, observations)
        signals[model_name] = apply_signal_policy(observations, predictions, sign_policy)
        all_metrics[model_name] = {
            scenario.scenario_id: _metrics_dump(
                evaluate_policy_signals(observations, signals[model_name], scenario)
            )
            for scenario in SCENARIOS
        }
    conservative = next(
        scenario for scenario in SCENARIOS if scenario.scenario_id == "conservative"
    )
    comparisons = {
        baseline: compare_policy_paths(
            observations,
            signals["ttm-r2"],
            signals[baseline],
            conservative,
        ).model_dump(mode="json")
        for baseline in ("lightgbm", "drift")
    }
    distributions: list[dict[str, Any]] = []
    for instrument in ("BTCUSDT", "ETHUSDT"):
        instrument_observations = tuple(
            item for item in observations if item.instrument == instrument
        )
        for model_name in ("ttm-r2", "lightgbm"):
            instrument_predictions = tuple(
                item for item in _model_predictions(indexed, model_name, instrument_observations)
            )
            distributions.append(
                summarize_prediction_distribution(
                    instrument_observations, instrument_predictions
                ).model_dump(mode="json")
            )
    confidence_counts = Counter(str(item.confidence) for item in indexed["ttm-r2"].values())
    break_even = {
        model_name: {
            scenario_id: _break_even_all_in_cost(metrics)
            for scenario_id, metrics in model_metrics.items()
        }
        for model_name, model_metrics in all_metrics.items()
    }
    return {
        "scope": "diagnostic_only_frozen_full_input",
        "holdout_outcomes_used_for_policy_selection": False,
        "full_input_includes_consumed_holdout": True,
        "sign_only_policy": sign_policy.model_dump(mode="json"),
        "metrics": all_metrics,
        "break_even_all_in_cost_bps": break_even,
        "path_comparisons": comparisons,
        "prediction_distributions": distributions,
        "ttm_r2_confidence_values": {
            "counts": dict(sorted(confidence_counts.items())),
            "policy_implication": "confidence threshold candidates cannot differentiate the frozen 0.5 confidence path",
        },
        "diagnostic_findings": {
            "weak_forecasts": "not_isolated_by_this_decomposition; raw TTM-R2 gross utility is positive in the frozen diagnostic",
            "excessive_trading_frequency": "supported_if_TTM_R2_turnover_exceeds_comparator; see path_comparisons",
            "low_conviction_predictions": "tested_by_magnitude_and_edge_policy_families_on_development_only",
            "regime_specific_degradation": "inspect regime_metrics; negative slices are preserved, not averaged away",
            "cost_sensitive_marginal_trades": "supported when conservative incremental utility falls below base; modeled costs are not observed fills",
            "forecast_horizon_policy_mismatch": "existing formal review reports negative next-bar stress; this study does not retune that holdout",
        },
    }


def _challenger_inventory() -> list[dict[str, Any]]:
    candidates = (
        (
            "chronos-2-small",
            Path("artifacts/phase0/model-runtime-qualification-first-run/chronos-2-small.json"),
        ),
        (
            "kronos-mini",
            Path("artifacts/phase0/model-runtime-qualification-first-run/kronos-mini.json"),
        ),
        (
            "kronos-small",
            Path("artifacts/phase0/model-runtime-qualification-first-run/kronos-small.json"),
        ),
    )
    result: list[dict[str, Any]] = []
    for name, relative_path in candidates:
        path = REPOSITORY_ROOT / relative_path
        if not path.is_file():
            result.append(
                {
                    "name": name,
                    "status": "EXTERNALLY_BLOCKED",
                    "reason": "no immutable runtime qualification report is available",
                    "evidence": [],
                }
            )
            continue
        payload = _load_json(path)
        result.append(
            {
                "name": name,
                "status": str(payload.get("status", "UNKNOWN")).upper(),
                "reason": payload.get("failure_reason") or "runtime admission did not pass",
                "worker_hash": payload.get("environment", {}).get("runner_hash"),
                "evidence": [
                    {
                        "path": str(relative_path),
                        "sha256": _sha256(path),
                    }
                ],
                "evaluated": False,
                "selection_note": "preserved quarantine; no worker/checkpoint mismatch bypass",
            }
        )
    return result


def _git_head() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SignalPolicyResearchRefused("cannot identify research code revision") from exc
    return result.stdout.strip().lower()


def run_research(
    input_path: Path,
    measurement_path: Path,
    review_path: Path,
    output_root: Path,
    *,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    """Run one immutable offline policy study."""

    input_path = input_path.resolve()
    measurement_path = measurement_path.resolve()
    review_path = review_path.resolve()
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError("signal-policy research output root must be new")
    timestamp = (evaluated_at or datetime.now(UTC)).astimezone(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise SignalPolicyResearchRefused("evaluation timestamp must be timezone-aware")
    observations, predictions = _load_input(input_path)
    input_sha256 = _sha256(input_path)
    measurement = _validate_measurement(measurement_path, input_sha256=input_sha256)
    review = _validate_review(review_path, input_sha256=input_sha256)
    partitions = _partition(observations)
    indexed = _prediction_index(predictions)
    required_models = set((*MANDATORY_BASELINES, "ttm-r2", "ttm-r3"))
    if not required_models.issubset(indexed):
        raise SignalPolicyResearchRefused("frozen input is missing the required model paths")
    attempts = tuple(
        _validation_attempt(spec, partitions, indexed) for spec in candidate_policy_specs()
    )
    selection = _select_attempt(attempts)
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "generated_at": timestamp.isoformat(),
        "repository_commit": _git_head(),
        "working_tree_code_sha256": _code_hashes(),
        "network_calls": 0,
        "credentials_loaded": False,
        "model_weights_loaded": False,
        "order_writes_attempted": False,
        "data_acquisition": False,
        "phase4_admission_opened": False,
        "input": {
            "path": str(input_path.relative_to(REPOSITORY_ROOT)),
            "sha256": input_sha256,
            "observation_count": len(observations),
            "prediction_count": len(predictions),
            "instruments": sorted({item.instrument for item in observations}),
            "source_snapshot_hashes": sorted({item.source_snapshot_hash for item in observations}),
        },
        "measurement": measurement,
        "formal_review": review,
        "partition_policy": {
            "version": POLICY_PARTITION_VERSION,
            "tuning": _partition_summary(partitions["tuning"]),
            "validation": _partition_summary(partitions["validation"]),
            "holdout_consumed": _partition_summary(partitions["holdout_consumed"]),
            "holdout_used_for_selection": False,
            "holdout_scored": False,
            "holdout_status": "CONSUMED_AND_NOT_REUSED",
        },
        "policy_search": {
            "candidate_count": len(attempts),
            "candidate_policy_ids": [attempt["policy"]["policy_id"] for attempt in attempts],
            "selection_objective": POLICY_SELECTION_OBJECTIVE,
            "multiple_testing_control": "bounded_pre_registered_search_space; no holdout selection",
            "attempts": list(attempts),
            "selection": selection,
        },
        "frozen_holdout_policy": {
            "status": "NOT_EVALUATED_CONSUMED",
            "reason": "the existing final holdout already influenced the formal Phase-4 review and cannot select or score a new policy",
            "next_admissible_evidence": "future accumulating paper observations or a genuinely independent PIT historical window",
        },
        "economic_decomposition": _full_diagnostics(observations, indexed),
        "additional_challengers": _challenger_inventory(),
        "ensemble": {
            "status": "NOT_EVALUATED",
            "reason": "no independently runtime-admitted Chronos/Kronos candidate is available; no ensemble justification exists",
            "required_before_evaluation": [
                "exact checkpoint identity",
                "exact worker/code hash",
                "runtime qualification",
                "same point-in-time input and baselines",
                "same formal reviewer",
            ],
        },
        "formal_admission": {
            "status": "NOT_ELIGIBLE_FROM_THIS_STUDY",
            "phase4_remains": "PENDING",
            "blocker": "robust_candidate_admission",
            "reason": "policy selection has no clean final holdout and no independent future/PIT evaluation; a development result cannot admit a candidate",
            "ttm_r2_status": "CHALLENGER",
            "ttm_r3_status": "RESEARCH_ONLY",
        },
        "execution_authority": {
            "risk_kernel": "unchanged_external_authority",
            "oms": "unchanged_external_authority",
            "signal_policy_order_authority": False,
            "model_order_authority": False,
            "dashboard_order_authority": False,
        },
    }
    report_path = output_root / "phase4-signal-policy-research.json"
    evidence_sha256 = _write_immutable(report_path, evidence)
    _write_immutable_bytes(
        output_root / "phase4-signal-policy-research.sha256",
        f"{evidence_sha256}  {report_path.name}\n".encode("ascii"),
    )
    manifest = {
        "schema": "advisorai.phase4.signal-policy-research-manifest.v1",
        "evidence": {
            "path": str(report_path.relative_to(REPOSITORY_ROOT)),
            "sha256": evidence_sha256,
        },
        "input": {"path": str(input_path.relative_to(REPOSITORY_ROOT)), "sha256": input_sha256},
        "measurement": measurement,
        "formal_review": review,
        "immutable": True,
    }
    _write_immutable(output_root / "evidence-manifest.json", manifest)
    return {
        "state": "research_only_pending_independent_evidence",
        "evidence": str(report_path),
        "sha256": evidence_sha256,
        "selected_policy_id": selection["selected_policy_id"],
        "selection_status": selection["status"],
        "holdout_used": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--measurement", type=Path, required=True)
    parser.add_argument("--formal-review", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--evaluated-at", type=str)
    args = parser.parse_args()
    evaluated_at = datetime.fromisoformat(args.evaluated_at) if args.evaluated_at else None
    try:
        result = run_research(
            args.input,
            args.measurement,
            args.formal_review,
            args.output_root,
            evaluated_at=evaluated_at,
        )
    except (FileExistsError, OSError, SignalPolicyResearchRefused, ValueError) as exc:
        raise SystemExit(f"phase4 signal-policy research refused ({type(exc).__name__})") from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
