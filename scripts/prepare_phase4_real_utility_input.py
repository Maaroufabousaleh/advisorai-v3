#!/usr/bin/env python3
"""Build a real BTC/ETH Phase-4 utility input from frozen public data.

The command consumes an already acquired, point-in-time snapshot and executes
only the explicitly selected local forecast worker.  It writes immutable
predictions for the mandatory baselines and any successfully measured local
candidate.  Sentiment roles are recorded as context-only and are never coerced
into price forecasts.  No credentials, order transport, or network client is
loaded by this command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from advisorai.models.forecasting import (
    DriftForecaster,
    LinearForecaster,
    NaiveForecaster,
    SeasonalForecaster,
)
from advisorai.phase0 import ForecastBenchmarkSnapshot, build_walk_forward_cases
from advisorai.phase0.runtime_qualification import (
    BenchmarkDataset,
    LightGBMBaseline,
    LocalCandidateAdmission,
    QualificationError,
    QualificationStatus,
    apply_local_candidate_admission,
    default_runtime_candidates,
    run_runtime_qualification,
)
from advisorai.phase4 import Phase4MarketObservation, Phase4Prediction

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
INPUT_SCHEMA = "advisorai.phase4.paper-utility-input.v1"
GENERATION_SCHEMA = "advisorai.phase4.real-utility-input-generation.v1"
SOURCE_ID = "binance_spot_public_market_data"
SOURCE_ENDPOINT = "https://api.binance.com/api/v3/klines"
REQUIRED_SYMBOLS = ("BTCUSDT", "ETHUSDT")
DEFAULT_ADMISSION_ROOT = REPOSITORY_ROOT / (
    "artifacts/phase0/model-runtime-qualification/runtime-admission-post-cwd-fix-20260810"
)
DEFAULT_CANDIDATE_ADMISSION_ROOTS = {
    "ttm-r2": DEFAULT_ADMISSION_ROOT,
    "ttm-r3": REPOSITORY_ROOT
    / "artifacts/phase0/model-runtime-qualification/runtime-admission-phase4-ttm-r3-20260812",
}
# Preserve the original control-only default for callers that have not supplied
# a challenger admission root.  The Phase-4 work package requests TTM-R3
# explicitly when its current immutable runtime admission is available.
DEFAULT_FORECAST_CANDIDATES = ("ttm-r2",)
FORECAST_CODE = REPOSITORY_ROOT / "src/advisorai/models/forecasting.py"
LIGHTGBM_CODE = REPOSITORY_ROOT / "src/advisorai/phase0/runtime_qualification.py"
PHASE3_GATE = REPOSITORY_ROOT / (
    "artifacts/phase3/formal-admission/20260812T013505Z-with-passed-phase2-post-phase2-commit/"
    "phase3-gate-record.json"
)


class Phase4InputRefused(ValueError):
    """Raised when the frozen input cannot support a truthful measurement."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _write_immutable(path: Path, payload: object) -> str:
    encoded = _canonical(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return hashlib.sha256(encoded).hexdigest()


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().lower()


def _load_snapshot(path: Path, manifest_path: Path) -> ForecastBenchmarkSnapshot:
    try:
        snapshot = ForecastBenchmarkSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise Phase4InputRefused("frozen forecast snapshot or manifest is invalid") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != "advisorai.phase0.forecast-benchmark-evidence.v1"
    ):
        raise Phase4InputRefused("forecast snapshot manifest is not the reviewed evidence schema")
    manifest_snapshot = manifest.get("snapshot")
    if (
        not isinstance(manifest_snapshot, dict)
        or manifest_snapshot.get("content_hash") != snapshot.content_hash
    ):
        raise Phase4InputRefused("snapshot content hash does not match its immutable manifest")
    selected = {item.instrument for item in snapshot.series}
    if not set(REQUIRED_SYMBOLS).issubset(selected):
        raise Phase4InputRefused("frozen snapshot does not cover both required BTC/ETH symbols")
    for series in snapshot.series:
        if series.instrument in REQUIRED_SYMBOLS and series.source != SOURCE_ENDPOINT:
            raise Phase4InputRefused(
                "BTC/ETH snapshot source identity is not the reviewed Binance public endpoint"
            )
    return snapshot


def _regime(case: Any) -> str:
    closes = [Decimal(str(value)) for value in case.context]
    recent = closes[-20:]
    returns = tuple(
        (right / left - Decimal("1")) * Decimal("10000")
        for left, right in zip(recent, recent[1:], strict=False)
    )
    mean = sum(returns) / Decimal(len(returns))
    variance = sum((value - mean) ** 2 for value in returns) / Decimal(len(returns))
    volatility = variance.sqrt() if variance > 0 else Decimal("0")
    if mean > volatility * Decimal("0.25"):
        return "trend_up"
    if mean < -volatility * Decimal("0.25"):
        return "trend_down"
    return "range"


def _observations(
    cases: tuple[Any, ...], snapshot_hash: str, spread_bps: Decimal, slippage_bps: Decimal
) -> tuple[Phase4MarketObservation, ...]:
    observations = []
    for case in cases:
        cutoff_price = Decimal(str(case.context[-1]))
        realized_price = Decimal(str(case.actual[0]))
        observations.append(
            Phase4MarketObservation(
                observation_id=f"{case.instrument}:{case.cutoff.isoformat()}",
                instrument=case.instrument,
                cutoff=case.cutoff,
                realized_at=case.future_timestamps[0],
                realized_return_bps=(realized_price / cutoff_price - Decimal("1"))
                * Decimal("10000"),
                spread_bps=spread_bps,
                slippage_bps=slippage_bps,
                regime=_regime(case),
                source_id=SOURCE_ID,
                provider_identity=SOURCE_ID,
                endpoint=SOURCE_ENDPOINT,
                source_snapshot_hash=snapshot_hash,
                phase3_admitted=True,
            )
        )
    return tuple(observations)


def _return_prediction(
    observation: Phase4MarketObservation,
    predicted_price: Decimal,
    cutoff_price: Decimal,
    model_name: str,
    code_hash: str,
    artifact_hash: str,
    *,
    resource_limit_passed: bool = True,
    latency_ms: Decimal | None = None,
    interval_lower_bps: Decimal | None = None,
    interval_upper_bps: Decimal | None = None,
) -> Phase4Prediction:
    return Phase4Prediction(
        observation_id=observation.observation_id,
        model_name=model_name,
        predicted_return_bps=(predicted_price / cutoff_price - Decimal("1")) * Decimal("10000"),
        confidence=Decimal("0.5"),
        interval_lower_bps=interval_lower_bps,
        interval_upper_bps=interval_upper_bps,
        model_code_hash=code_hash,
        model_artifact_hash=artifact_hash,
        resource_limit_passed=resource_limit_passed,
        latency_ms=latency_ms,
    )


def _baseline_predictions(
    cases: tuple[Any, ...], observations: tuple[Phase4MarketObservation, ...]
) -> tuple[Phase4Prediction, ...]:
    forecasting_hash = _sha256(FORECAST_CODE)
    lightgbm_hash = _sha256(LIGHTGBM_CODE)
    models = (
        ("naive", NaiveForecaster(), forecasting_hash),
        ("drift", DriftForecaster(), forecasting_hash),
        ("seasonal-7", SeasonalForecaster(period=7), forecasting_hash),
        ("linear", LinearForecaster(), forecasting_hash),
        ("lightgbm", LightGBMBaseline(), lightgbm_hash),
    )
    predictions: list[Phase4Prediction] = []
    for name, model, implementation_hash in models:
        for case, observation in zip(cases, observations, strict=True):
            try:
                price = Decimal(
                    str(model.predict(tuple(Decimal(str(value)) for value in case.context), 1)[0])
                )
            except Exception as exc:  # noqa: BLE001 - baseline failures are quarantined by the caller
                raise Phase4InputRefused(
                    f"mandatory baseline {name} failed: {type(exc).__name__}"
                ) from exc
            predictions.append(
                _return_prediction(
                    observation,
                    price,
                    Decimal(str(case.context[-1])),
                    name,
                    implementation_hash,
                    implementation_hash,
                )
            )
    return tuple(predictions)


def _forecast_predictions(
    cases: tuple[Any, ...],
    observations: tuple[Phase4MarketObservation, ...],
    snapshot: ForecastBenchmarkSnapshot,
    candidate_name: str,
    admission_path: Path,
) -> tuple[tuple[Phase4Prediction, ...], dict[str, Any]]:
    candidate = next(item for item in default_runtime_candidates() if item.name == candidate_name)
    try:
        admission = LocalCandidateAdmission.model_validate_json(
            admission_path.read_text(encoding="utf-8")
        )
        candidate = apply_local_candidate_admission(candidate, admission)
        dataset = BenchmarkDataset(
            dataset_id=snapshot.dataset_id,
            version=snapshot.version,
            task="forecast",
            source=SOURCE_ENDPOINT,
            snapshot_id=f"public-daily-{snapshot.content_hash[:16]}",
            training_cutoff=max(case.cutoff for case in cases),
            inputs=tuple(case.context[-1] for case in cases),
            targets=tuple(case.actual[0] for case in cases),
            content_hash=snapshot.content_hash,
        )
        result = run_runtime_qualification(
            candidate,
            runner=None,
            dataset=dataset,
            sample_input=cases[0].context,
            batch_input=tuple(case.context for case in cases),
            repeats=3,
            repository_root=REPOSITORY_ROOT,
        )
    except (OSError, ValueError, QualificationError) as exc:
        return (), {
            "candidate": candidate_name,
            "status": "quarantined",
            "failure_class": type(exc).__name__,
            "failure_reason": str(exc),
            "admission_path": _relative(admission_path),
        }
    report: dict[str, Any] = {
        "candidate": candidate_name,
        "status": result.status.value,
        "failure_class": type(result.failure_reason).__name__ if result.failure_reason else None,
        "failure_reason": str(result.failure_reason) if result.failure_reason else None,
        "failure_reason_present": result.failure_reason is not None,
        "network_access_attempted": result.network_access_attempted,
        "admission_path": _relative(admission_path),
        "admission_sha256": _sha256(admission_path),
        "resource": result.resource.model_dump(mode="json") if result.resource else None,
        "environment": result.environment.model_dump(mode="json"),
        "runtime_worker_hash": result.environment.runner_hash,
        "runtime_lock_hash": result.environment.runtime_lock_hash,
        "checkpoint_revision": candidate.external_checkpoint.repository.revision
        if candidate.external_checkpoint
        else None,
    }
    if result.status is not QualificationStatus.MEASURED:
        return (), report
    artifact_hash = next(
        item.sha256
        for item in admission.checkpoint.repository.runtime_artifacts
        if item.relative_path == "model.safetensors"
    )
    code_hash = result.environment.runner_hash
    if code_hash is None:
        raise Phase4InputRefused(f"measured {candidate_name} result has no worker identity hash")
    predictions = tuple(
        _return_prediction(
            observation,
            Decimal(str(forecast[0])),
            Decimal(str(case.context[-1])),
            candidate_name,
            code_hash,
            artifact_hash,
            resource_limit_passed=bool(result.resource and result.resource.resource_limit_passed),
            latency_ms=(
                Decimal(str(result.resource.warm_inference_p50_ms))
                if result.resource is not None
                else None
            ),
            interval_lower_bps=(
                (Decimal(str(interval_lower[0])) / Decimal(str(case.context[-1])) - Decimal("1"))
                * Decimal("10000")
                if interval_lower is not None
                else None
            ),
            interval_upper_bps=(
                (Decimal(str(interval_upper[0])) / Decimal(str(case.context[-1])) - Decimal("1"))
                * Decimal("10000")
                if interval_upper is not None
                else None
            ),
        )
        for case, observation, forecast, interval_lower, interval_upper in zip(
            cases,
            observations,
            result.forecast_batch_predictions,
            result.forecast_batch_lower or (None,) * len(cases),
            result.forecast_batch_upper or (None,) * len(cases),
            strict=True,
        )
    )
    return predictions, report


def _ttm_r2_predictions(
    cases: tuple[Any, ...],
    observations: tuple[Phase4MarketObservation, ...],
    snapshot: ForecastBenchmarkSnapshot,
    admission_root: Path,
) -> tuple[tuple[Phase4Prediction, ...], dict[str, Any]]:
    """Retain the original helper for callers that measure only the control."""

    return _forecast_predictions(
        cases,
        observations,
        snapshot,
        "ttm-r2",
        admission_root / "ttm-r2" / "local-admission.json",
    )


def build_input(
    *,
    forecast_snapshot: Path,
    snapshot_manifest: Path,
    admission_root: Path = DEFAULT_ADMISSION_ROOT,
    cases_per_series: int = 32,
    spread_bps: Decimal = Decimal("2"),
    slippage_bps: Decimal = Decimal("2"),
    phase3_gate_path: Path = PHASE3_GATE,
    forecast_candidates: tuple[str, ...] = DEFAULT_FORECAST_CANDIDATES,
    candidate_admission_roots: Mapping[str, Path] | None = None,
    generated_at: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if cases_per_series < 16:
        raise Phase4InputRefused("real Phase-4 preparation requires at least 16 cases per symbol")
    snapshot = _load_snapshot(forecast_snapshot.resolve(), snapshot_manifest.resolve())
    cases = tuple(
        case
        for case in build_walk_forward_cases(snapshot, cases_per_series=cases_per_series)
        if case.instrument in REQUIRED_SYMBOLS
    )
    if len(cases) < 30:
        raise Phase4InputRefused(
            "real Phase-4 preparation requires at least 30 BTC/ETH observations"
        )
    observations = _observations(cases, snapshot.content_hash, spread_bps, slippage_bps)
    predictions = list(_baseline_predictions(cases, observations))
    requested_candidates = tuple(dict.fromkeys(forecast_candidates))
    if not requested_candidates:
        raise Phase4InputRefused("at least one Phase-4 forecast candidate is required")
    known_forecast_candidates = {
        item.name
        for item in default_runtime_candidates()
        if item.task.value == "forecast" and item.external_checkpoint is not None
    }
    unknown_candidates = set(requested_candidates) - known_forecast_candidates
    if unknown_candidates:
        raise Phase4InputRefused(
            f"unsupported Phase-4 forecast candidates: {sorted(unknown_candidates)}"
        )
    roots = dict(DEFAULT_CANDIDATE_ADMISSION_ROOTS)
    roots["ttm-r2"] = admission_root.resolve()
    if candidate_admission_roots:
        roots.update({name: path.resolve() for name, path in candidate_admission_roots.items()})
    candidate_reports: dict[str, dict[str, Any]] = {}
    measured_candidates: list[str] = []
    for candidate_name in requested_candidates:
        candidate_root = roots.get(candidate_name)
        if candidate_root is None:
            raise Phase4InputRefused(f"no admission root configured for {candidate_name}")
        candidate_predictions, candidate_report = _forecast_predictions(
            cases,
            observations,
            snapshot,
            candidate_name,
            candidate_root / candidate_name / "local-admission.json",
        )
        candidate_reports[candidate_name] = candidate_report
        predictions.extend(candidate_predictions)
        if candidate_predictions:
            measured_candidates.append(candidate_name)
        elif candidate_name == "ttm-r2":
            raise Phase4InputRefused("qualified TTM-R2 did not produce a measured price forecast")
    timestamp = (generated_at or datetime.now(UTC)).astimezone(UTC)
    input_payload = {
        "schema": INPUT_SCHEMA,
        "observations": [item.model_dump(mode="json") for item in observations],
        "predictions": [item.model_dump(mode="json") for item in predictions],
    }
    generation_payload = {
        "schema": GENERATION_SCHEMA,
        "generated_at": timestamp.isoformat(),
        "repository_commit": _git_head(),
        "source": {
            "snapshot_path": _relative(forecast_snapshot),
            "manifest_path": _relative(snapshot_manifest),
            "snapshot_sha256": _sha256(forecast_snapshot),
            "manifest_sha256": _sha256(snapshot_manifest),
            "content_hash": snapshot.content_hash,
            "provider_identity": SOURCE_ID,
            "endpoint": SOURCE_ENDPOINT,
            "symbols": list(REQUIRED_SYMBOLS),
            "point_in_time": True,
            "market_data_only": True,
        },
        "phase3_gate": {
            "path": _relative(phase3_gate_path),
            "sha256": _sha256(phase3_gate_path),
        },
        "cases": {
            "cases_per_series": cases_per_series,
            "observation_count": len(observations),
            "cutoff_min": min(item.cutoff for item in observations).isoformat(),
            "cutoff_max": max(item.cutoff for item in observations).isoformat(),
            "realized_horizon": "next_daily_bar",
        },
        "cost_scenario": {
            "fee_schedule_id": "binance-spot-testnet-conservative-v1",
            "fee_bps": "10",
            "spread_bps": str(spread_bps),
            "slippage_bps": str(slippage_bps),
            "status": "conservative_scenario_assumption_not_historical_tick_measurement",
        },
        "models": {
            "mandatory_baselines": ["naive", "drift", "seasonal-7", "linear", "lightgbm"],
            "requested_candidates": list(requested_candidates),
            "measured_candidates": measured_candidates,
            "candidate_reports": candidate_reports,
            "context_roles_not_coerced_to_price_forecast": [
                "finsentiment-deberta-v3",
                "finbert-minilm",
                "tspulse",
            ],
        },
        "input": {
            "prediction_count": len(predictions),
            "model_names": sorted({item.model_name for item in predictions}),
        },
        "network_calls": 0,
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "model_weights_loaded": True,
        "execution_authority": {
            "risk_kernel": "unchanged_external_authority",
            "oms": "unchanged_external_authority",
            "model_order_authority": False,
            "dashboard_order_authority": False,
        },
        "admission_statement": "real_measurement_input_only;_phase4_admission_remains_closed",
        "notes": [
            "The public Binance endpoint is used only as a read-only historical market-data source; the execution transport remains Binance Spot Testnet.",
            "Finance sentiment models are not price forecasters and were not assigned fabricated predictions.",
            "No new Phase-3 durability root or Binance order was created.",
            "LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.",
        ],
    }
    return input_payload, generation_payload


def write_evidence(
    output_root: Path, input_payload: dict[str, Any], generation_payload: dict[str, Any]
) -> dict[str, str]:
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError(f"Phase-4 input root already exists: {output_root}")
    output_root.mkdir(parents=True)
    input_path = output_root / "phase4-paper-utility-input.json"
    generation_path = output_root / "phase4-input-generation.json"
    input_hash = _write_immutable(input_path, input_payload)
    generation_payload = {
        **generation_payload,
        "input": {**generation_payload["input"], "path": input_path.name, "sha256": input_hash},
    }
    generation_hash = _write_immutable(generation_path, generation_payload)
    manifest = {
        "schema": "advisorai.phase4.real-utility-input-generation.v1.manifest",
        "input": input_path.name,
        "input_sha256": input_hash,
        "generation": generation_path.name,
        "generation_sha256": generation_hash,
    }
    manifest_hash = _write_immutable(output_root / "evidence-manifest.json", manifest)
    return {
        "input": str(input_path),
        "input_sha256": input_hash,
        "generation": str(generation_path),
        "generation_sha256": generation_hash,
        "manifest_sha256": manifest_hash,
    }


def _parse_candidate_admission_roots(values: list[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name.strip() or not raw_path.strip():
            raise Phase4InputRefused("candidate admission roots must use NAME=PATH")
        if name in roots:
            raise Phase4InputRefused(f"duplicate candidate admission root: {name}")
        roots[name] = Path(raw_path)
    return roots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forecast-snapshot", type=Path, required=True)
    parser.add_argument("--snapshot-manifest", type=Path, required=True)
    parser.add_argument("--admission-root", type=Path, default=DEFAULT_ADMISSION_ROOT)
    parser.add_argument("--phase3-gate-record", type=Path, default=PHASE3_GATE)
    parser.add_argument("--cases-per-series", type=int, default=32)
    parser.add_argument("--spread-bps", type=Decimal, default=Decimal("2"))
    parser.add_argument("--slippage-bps", type=Decimal, default=Decimal("2"))
    parser.add_argument(
        "--forecast-candidate",
        action="append",
        dest="forecast_candidates",
        default=None,
        help="price-forecast candidate to measure (repeatable; defaults to TTM-R2)",
    )
    parser.add_argument(
        "--candidate-admission-root",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="override a candidate's local-admission root (repeatable)",
    )
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        input_payload, generation_payload = build_input(
            forecast_snapshot=args.forecast_snapshot,
            snapshot_manifest=args.snapshot_manifest,
            admission_root=args.admission_root,
            cases_per_series=args.cases_per_series,
            spread_bps=args.spread_bps,
            slippage_bps=args.slippage_bps,
            phase3_gate_path=args.phase3_gate_record.resolve(),
            forecast_candidates=tuple(args.forecast_candidates or DEFAULT_FORECAST_CANDIDATES),
            candidate_admission_roots=_parse_candidate_admission_roots(
                args.candidate_admission_root
            ),
        )
        print(
            json.dumps(
                write_evidence(args.output_root, input_payload, generation_payload), sort_keys=True
            )
        )
        return 0
    except (FileExistsError, OSError, Phase4InputRefused, ValueError) as exc:
        raise SystemExit(f"phase4 input preparation refused ({type(exc).__name__})") from exc


if __name__ == "__main__":
    raise SystemExit(main())
