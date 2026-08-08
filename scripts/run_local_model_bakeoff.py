#!/usr/bin/env python3
"""Run the real Phase-0 local-model bake-off from frozen public snapshots."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from advisorai.phase0 import (
    BenchmarkDataset,
    ForecastBenchmarkSnapshot,
    LocalCandidateAdmission,
    ModelFamily,
    QualificationStatus,
    SentimentBenchmarkSnapshot,
    apply_local_candidate_admission,
    build_walk_forward_cases,
    default_runtime_candidates,
    forecast_metrics,
    mandatory_baseline_metrics,
    run_runtime_qualification,
    sentiment_metrics,
    write_immutable_json,
    write_qualification_bundle,
)

FORECAST_CANDIDATES = (
    "ttm-r3",
    "ttm-r2",
    "chronos-2-small",
    "kronos-mini",
    "kronos-small",
)
SENTIMENT_CANDIDATES = (
    "modern-finbert",
    "finbert-minilm",
    "finsentiment-deberta-v3",
)


def _kronos_payload(case) -> dict[str, object]:
    return {
        "ohlcv": case.context_ohlcv,
        "timestamps": tuple(value.isoformat() for value in case.context_timestamps),
        "future_timestamps": tuple(value.isoformat() for value in case.future_timestamps),
    }


def _qualification_dataset(snapshot: ForecastBenchmarkSnapshot, cases) -> BenchmarkDataset:
    return BenchmarkDataset(
        dataset_id=snapshot.dataset_id,
        version=snapshot.version,
        task="forecast",
        source="snapshot://advisorai/phase0/public-daily-markets",
        snapshot_id=f"public-daily-{snapshot.content_hash[:16]}",
        training_cutoff=max(case.cutoff for case in cases),
        inputs=tuple(case.context[-1] for case in cases),
        targets=tuple(case.actual[0] for case in cases),
        content_hash=snapshot.content_hash,
    )


def _sentiment_subset(snapshot: SentimentBenchmarkSnapshot):
    by_label: dict[str, list[object]] = {"negative": [], "neutral": [], "positive": []}
    for example in snapshot.examples:
        if len(by_label[example.label]) < 60:
            by_label[example.label].append(example)
    if any(len(items) != 60 for items in by_label.values()):
        raise ValueError("sentiment snapshot does not contain the required balanced subset")
    return tuple(item for label in ("negative", "neutral", "positive") for item in by_label[label])


def _hash_payload(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forecast-snapshot", type=Path, required=True)
    parser.add_argument("--sentiment-snapshot", type=Path, required=True)
    parser.add_argument("--admission-root", type=Path, required=True)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("artifacts/phase0/model-runtime-qualification/local-bakeoff"),
    )
    args = parser.parse_args()
    forecast_snapshot = ForecastBenchmarkSnapshot.model_validate_json(
        args.forecast_snapshot.read_text(encoding="utf-8")
    )
    sentiment_snapshot = SentimentBenchmarkSnapshot.model_validate_json(
        args.sentiment_snapshot.read_text(encoding="utf-8")
    )
    cases = build_walk_forward_cases(forecast_snapshot, cases_per_series=4)
    candidates = {candidate.name: candidate for candidate in default_runtime_candidates()}
    admissions = {
        name: LocalCandidateAdmission.model_validate_json(
            (args.admission_root / name / "local-admission.json").read_text(encoding="utf-8")
        )
        for name in (*FORECAST_CANDIDATES, *SENTIMENT_CANDIDATES, "tspulse")
    }
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_directory = args.output_root / run_id
    qualification_results = []
    forecast_results = list(mandatory_baseline_metrics(cases))
    forecast_dataset = _qualification_dataset(forecast_snapshot, cases)
    for name in FORECAST_CANDIDATES:
        candidate = apply_local_candidate_admission(candidates[name], admissions[name])
        if candidate.family in {ModelFamily.KRONOS_MINI, ModelFamily.KRONOS_SMALL}:
            batch = tuple(_kronos_payload(case) for case in cases)
        else:
            batch = tuple(case.context for case in cases)
        result = run_runtime_qualification(
            candidate,
            runner=None,
            dataset=forecast_dataset,
            sample_input=batch[0],
            batch_input=batch,
            repeats=3,
            repository_root=Path.cwd(),
        )
        qualification_results.append(result)
        if result.status == QualificationStatus.MEASURED and result.resource is not None:
            forecast_results.append(
                forecast_metrics(
                    name,
                    cases,
                    result.forecast_batch_predictions,
                    latency_p50_ms=result.resource.warm_inference_p50_ms,
                    latency_p95_ms=result.resource.warm_inference_p95_ms,
                    cold_load_ms=result.resource.cold_load_ms,
                    peak_rss_mib=result.resource.rss_peak_mib,
                    peak_vram_mib=result.resource.vram_peak_mib or 0,
                    resource_limit_passed=result.resource.resource_limit_passed,
                    interval_lower=result.forecast_batch_lower,
                    interval_upper=result.forecast_batch_upper,
                )
            )
    subset = _sentiment_subset(sentiment_snapshot)
    subset_hash = _hash_payload([item.model_dump(mode="json") for item in subset])
    sentiment_dataset = BenchmarkDataset(
        dataset_id=sentiment_snapshot.dataset_id,
        version=f"{sentiment_snapshot.revision[:12]}-balanced-180",
        task="finance_sentiment",
        source=sentiment_snapshot.source,
        snapshot_id=f"phrasebank-{sentiment_snapshot.content_hash[:16]}",
        training_cutoff=sentiment_snapshot.acquired_at,
        public_text_fixture=tuple((item.text, item.label) for item in subset),
        content_hash=subset_hash,
    )
    sentiment_results = []
    for name in SENTIMENT_CANDIDATES:
        candidate = apply_local_candidate_admission(candidates[name], admissions[name])
        result = run_runtime_qualification(
            candidate,
            runner=None,
            dataset=sentiment_dataset,
            sample_input=subset[0].text,
            batch_input=tuple(item.text for item in subset),
            repeats=3,
            repository_root=Path.cwd(),
        )
        qualification_results.append(result)
        if result.status == QualificationStatus.MEASURED and result.resource is not None:
            sentiment_results.append(
                sentiment_metrics(
                    name,
                    tuple(item.label for item in subset),
                    result.sentiment_batch_predictions,
                    latency_p50_ms=result.resource.warm_inference_p50_ms,
                    latency_p95_ms=result.resource.warm_inference_p95_ms,
                    throughput_per_second=result.resource.batch_throughput_per_second,
                    peak_rss_mib=result.resource.rss_peak_mib,
                )
            )
    tspulse_candidate = apply_local_candidate_admission(
        candidates["tspulse"], admissions["tspulse"]
    )
    tspulse_dataset = BenchmarkDataset(
        dataset_id=f"{forecast_snapshot.dataset_id}-tspulse",
        version=forecast_snapshot.version,
        task="tspulse_features",
        source="snapshot://advisorai/phase0/public-daily-markets",
        snapshot_id=f"public-daily-{forecast_snapshot.content_hash[:16]}",
        training_cutoff=max(case.cutoff for case in cases),
        inputs=tuple(case.context[-1] for case in cases),
        content_hash=forecast_snapshot.content_hash,
    )
    tspulse_result = run_runtime_qualification(
        tspulse_candidate,
        runner=None,
        dataset=tspulse_dataset,
        sample_input=cases[0].context,
        batch_input=tuple(case.context for case in cases),
        repeats=3,
        repository_root=Path.cwd(),
    )
    qualification_results.append(tspulse_result)
    tspulse_characterization = None
    if (
        tspulse_result.status == QualificationStatus.MEASURED
        and tspulse_result.feature_batch_predictions
        and tspulse_result.resource is not None
    ):
        dimensions = tuple(zip(*tspulse_result.feature_batch_predictions, strict=True))
        tspulse_characterization = {
            "role": "anomaly_integrity_representation_regime_features_only",
            "price_forecast_prohibited": True,
            "cases": len(tspulse_result.feature_batch_predictions),
            "feature_dimension": len(dimensions),
            "feature_means": [sum(values) / len(values) for values in dimensions],
            "feature_minima": [min(values) for values in dimensions],
            "feature_maxima": [max(values) for values in dimensions],
            "latency_p50_ms": tspulse_result.resource.warm_inference_p50_ms,
            "latency_p95_ms": tspulse_result.resource.warm_inference_p95_ms,
            "peak_rss_mib": tspulse_result.resource.rss_peak_mib,
        }
    qualification_paths = write_qualification_bundle(
        tuple(qualification_results), run_directory / "qualification"
    )
    qualification_hashes = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))["manifest_hash"]
        for path in qualification_paths
        if path.name != "index.json"
    }
    measured_forecasters = [
        item for item in forecast_results if item.model_name in FORECAST_CANDIDATES
    ]
    baseline_forecasters = [
        item for item in forecast_results if item.model_name not in FORECAST_CANDIDATES
    ]
    best_baseline_mase = min(item.mase for item in baseline_forecasters)
    best_candidate = (
        min(measured_forecasters, key=lambda item: (item.mase, item.rmse))
        if measured_forecasters
        else None
    )
    forecast_winner = (
        best_candidate.model_name
        if best_candidate is not None and best_candidate.mase < best_baseline_mase
        else None
    )
    nlp_winner = (
        max(
            sentiment_results, key=lambda item: (item.macro_f1, -item.expected_calibration_error)
        ).model_name
        if sentiment_results
        else None
    )
    nlp_fast = (
        max(sentiment_results, key=lambda item: item.throughput_per_second).model_name
        if sentiment_results
        else None
    )
    report = {
        "schema": "advisorai.phase0.local-model-bakeoff.v1",
        "run_id": run_id,
        "measured_at": datetime.now(UTC).isoformat(),
        "forecast_snapshot": {
            "dataset_id": forecast_snapshot.dataset_id,
            "content_hash": forecast_snapshot.content_hash,
            "cases": len(cases),
            "instruments": [item.instrument for item in forecast_snapshot.series],
            "past_only": True,
        },
        "sentiment_snapshot": {
            "dataset_id": sentiment_snapshot.dataset_id,
            "revision": sentiment_snapshot.revision,
            "content_hash": sentiment_snapshot.content_hash,
            "evaluation_subset_hash": subset_hash,
            "observations": len(subset),
        },
        "forecast_metrics": [item.model_dump(mode="json") for item in forecast_results],
        "sentiment_metrics": [item.model_dump(mode="json") for item in sentiment_results],
        "tspulse_characterization": tspulse_characterization,
        "decisions": {
            "forecast_primary_pending_stability": forecast_winner,
            "forecast_no_winner_reason": (
                None
                if forecast_winner
                else "no external candidate beat the best mandatory baseline on MASE"
            ),
            "finance_sentiment_primary_pending_stability": nlp_winner,
            "finance_sentiment_fast_pending_stability": nlp_fast,
            "selection_policy": "quality-constrained Pareto roles; technical gates precede metrics",
        },
        "qualification_status": {
            item.candidate.name: item.status.value for item in qualification_results
        },
        "qualification_manifest_hashes": {
            item.candidate.name: qualification_hashes.get(item.candidate.name)
            for item in qualification_results
        },
    }
    report_hash = _hash_payload(report)
    report["report_hash"] = report_hash
    report_path = write_immutable_json(run_directory / "local-model-bakeoff.json", report)
    print(
        json.dumps(
            {
                "run_id": run_id,
                "report_path": str(report_path),
                "report_hash": report_hash,
                "forecast_winner_pending_stability": forecast_winner,
                "sentiment_primary_pending_stability": nlp_winner,
                "sentiment_fast_pending_stability": nlp_fast,
                "qualification_status": report["qualification_status"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
