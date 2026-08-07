#!/usr/bin/env python3
"""Run resumable append-only stability evidence for selected local models."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from advisorai.phase0 import (
    BenchmarkDataset,
    ForecastBenchmarkSnapshot,
    LocalCandidateAdmission,
    ModelTask,
    QualificationStatus,
    SentimentBenchmarkSnapshot,
    apply_local_candidate_admission,
    build_walk_forward_cases,
    default_runtime_candidates,
    manifest_bytes,
    run_runtime_qualification,
    write_immutable_json,
)
from advisorai.phase0.model_stability import (
    CandidateStabilitySample,
    ModelStabilityConfig,
    append_cycle,
    make_cycle,
    read_cycles,
    summarize_stability,
)

SELECTED_CANDIDATES = (
    "ttm-r2",
    "finsentiment-deberta-v3",
    "finbert-minilm",
)


def _hash_payload(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _verify_report(path: Path) -> dict[str, object]:
    report = json.loads(path.read_text(encoding="utf-8"))
    claimed = str(report.pop("report_hash"))
    if _hash_payload(report) != claimed:
        raise ValueError("local bake-off report hash is inconsistent")
    report["report_hash"] = claimed
    return report


def _sentiment_subset(snapshot: SentimentBenchmarkSnapshot) -> tuple[tuple[str, str], ...]:
    by_label: dict[str, list[tuple[str, str]]] = {
        "negative": [],
        "neutral": [],
        "positive": [],
    }
    for example in snapshot.examples:
        if len(by_label[example.label]) < 4:
            by_label[example.label].append((example.text, example.label))
    if any(len(items) != 4 for items in by_label.values()):
        raise ValueError("stability snapshot lacks the fixed balanced sentiment subset")
    return tuple(
        item
        for label in ("negative", "neutral", "positive")
        for item in by_label[label]
    )


def _sample_from_result(result) -> CandidateStabilitySample:
    resource = result.resource
    measured = result.status == QualificationStatus.MEASURED
    passed = (
        measured
        and result.offline_cached_inference
        and not result.network_access_attempted
        and resource is not None
        and resource.resource_limit_passed
        and resource.memory_released
    )
    reason = None if passed else (result.failure_reason or "stability qualification gate failed")
    return CandidateStabilitySample(
        candidate=result.candidate.name,
        status=result.status.value,
        qualification_manifest_hash=sha256(manifest_bytes(result)).hexdigest(),
        privacy_passed=(
            measured and result.offline_cached_inference and not result.network_access_attempted
        ),
        resource_limit_passed=bool(resource and resource.resource_limit_passed),
        memory_released=bool(resource and resource.memory_released),
        current_rss_after_unload_mib=resource.rss_after_unload_mib if resource else 0,
        peak_rss_mib=resource.rss_peak_mib if resource else 0,
        peak_vram_mib=(resource.vram_peak_mib or 0) if resource else 0,
        failure_reason=reason,
    )


def _run_cycle(
    *,
    admissions: dict[str, LocalCandidateAdmission],
    forecast_snapshot: ForecastBenchmarkSnapshot,
    sentiment_snapshot: SentimentBenchmarkSnapshot,
) -> tuple[CandidateStabilitySample, ...]:
    candidates = {candidate.name: candidate for candidate in default_runtime_candidates()}
    cases = build_walk_forward_cases(forecast_snapshot, cases_per_series=2)
    forecast_dataset = BenchmarkDataset(
        dataset_id=forecast_snapshot.dataset_id,
        version=forecast_snapshot.version,
        task=ModelTask.FORECAST,
        source="snapshot://advisorai/phase0/public-daily-markets",
        snapshot_id=f"public-daily-{forecast_snapshot.content_hash[:16]}",
        training_cutoff=max(case.cutoff for case in cases),
        inputs=tuple(case.context[-1] for case in cases),
        targets=tuple(case.actual[0] for case in cases),
        content_hash=forecast_snapshot.content_hash,
    )
    ttm = apply_local_candidate_admission(candidates["ttm-r2"], admissions["ttm-r2"])
    forecast_result = run_runtime_qualification(
        ttm,
        runner=None,
        dataset=forecast_dataset,
        sample_input=cases[0].context,
        batch_input=tuple(case.context for case in cases),
        repeats=2,
        repository_root=Path.cwd(),
    )

    labeled_texts = _sentiment_subset(sentiment_snapshot)
    texts = tuple(text for text, _label in labeled_texts)
    sentiment_dataset = BenchmarkDataset(
        dataset_id=sentiment_snapshot.dataset_id,
        version=f"{sentiment_snapshot.revision[:12]}-stability-balanced-12",
        task=ModelTask.FINANCE_SENTIMENT,
        source=sentiment_snapshot.source,
        snapshot_id=f"phrasebank-{sentiment_snapshot.content_hash[:16]}",
        training_cutoff=sentiment_snapshot.acquired_at,
        public_text_fixture=labeled_texts,
        content_hash=sha256(
            "\n".join(f"{label}\t{text}" for text, label in labeled_texts).encode()
        ).hexdigest(),
    )
    results = [forecast_result]
    for name in ("finsentiment-deberta-v3", "finbert-minilm"):
        candidate = apply_local_candidate_admission(candidates[name], admissions[name])
        results.append(
            run_runtime_qualification(
                candidate,
                runner=None,
                dataset=sentiment_dataset,
                sample_input=texts[0],
                batch_input=texts,
                repeats=2,
                repository_root=Path.cwd(),
            )
        )
    return tuple(_sample_from_result(result) for result in results)


def _write_status(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forecast-snapshot", type=Path, required=True)
    parser.add_argument("--sentiment-snapshot", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--admission-root", type=Path, required=True)
    parser.add_argument("--run-directory", type=Path)
    parser.add_argument("--duration-hours", type=float, default=24)
    parser.add_argument("--interval-seconds", type=float, default=300)
    parser.add_argument("--max-cycles", type=int)
    args = parser.parse_args()
    if args.max_cycles is not None and args.max_cycles < 1:
        raise ValueError("max cycles must be positive")

    report = _verify_report(args.report)
    forecast_snapshot = ForecastBenchmarkSnapshot.model_validate_json(
        args.forecast_snapshot.read_text(encoding="utf-8")
    )
    sentiment_snapshot = SentimentBenchmarkSnapshot.model_validate_json(
        args.sentiment_snapshot.read_text(encoding="utf-8")
    )
    if report["forecast_snapshot"]["content_hash"] != forecast_snapshot.content_hash:
        raise ValueError("forecast stability snapshot differs from the bake-off")
    if report["sentiment_snapshot"]["content_hash"] != sentiment_snapshot.content_hash:
        raise ValueError("sentiment stability snapshot differs from the bake-off")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_directory = args.run_directory or Path(
        f"artifacts/phase0/model-runtime-qualification/stability/{run_id}"
    )
    run_directory.mkdir(parents=True, exist_ok=True)
    config_path = run_directory / "config.json"
    if config_path.exists():
        config = ModelStabilityConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    else:
        config = ModelStabilityConfig(
            run_id=run_directory.name,
            started_at=datetime.now(UTC),
            duration_hours=args.duration_hours,
            interval_seconds=args.interval_seconds,
            candidates=SELECTED_CANDIDATES,
            forecast_dataset_hash=forecast_snapshot.content_hash,
            sentiment_dataset_hash=sentiment_snapshot.content_hash,
            benchmark_report_hash=report["report_hash"],
        )
        write_immutable_json(config_path, config.model_dump(mode="json"))

    admissions = {
        name: LocalCandidateAdmission.model_validate_json(
            (args.admission_root / name / "local-admission.json").read_text(encoding="utf-8")
        )
        for name in SELECTED_CANDIDATES
    }
    log_path = run_directory / "cycles.jsonl"
    lock_handle = (run_directory / "runner.lock").open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError("another stability runner already owns this run") from exc
    lock_handle.seek(0)
    lock_handle.truncate()
    lock_handle.write(str(os.getpid()))
    lock_handle.flush()

    cycles = read_cycles(log_path)
    target_end = config.started_at + timedelta(hours=config.duration_hours)
    try:
        while datetime.now(UTC) < target_end:
            if args.max_cycles is not None and len(cycles) >= args.max_cycles:
                break
            samples = _run_cycle(
                admissions=admissions,
                forecast_snapshot=forecast_snapshot,
                sentiment_snapshot=sentiment_snapshot,
            )
            cycle = make_cycle(
                config,
                samples,
                sequence=len(cycles),
                previous_record_hash=cycles[-1].record_hash if cycles else None,
            )
            append_cycle(log_path, cycle)
            cycles = (*cycles, cycle)
            _write_status(
                run_directory / "status.json",
                {
                    "run_id": config.run_id,
                    "pid": os.getpid(),
                    "state": "running",
                    "cycle_count": len(cycles),
                    "last_record_hash": cycle.record_hash,
                    "last_sampled_at": cycle.sampled_at.isoformat(),
                    "all_latest_samples_passed": all(sample.passed for sample in cycle.samples),
                },
            )
            remaining = (target_end - datetime.now(UTC)).total_seconds()
            if remaining <= 0 or (args.max_cycles is not None and len(cycles) >= args.max_cycles):
                break
            time.sleep(min(config.interval_seconds, remaining))
    finally:
        fcntl.flock(lock_handle, fcntl.LOCK_UN)
        lock_handle.close()

    summary = summarize_stability(config, cycles)
    write_immutable_json(run_directory / "summary.json", summary.model_dump(mode="json"))
    _write_status(
        run_directory / "status.json",
        {
            "run_id": config.run_id,
            "state": summary.status,
            "cycle_count": summary.cycle_count,
            "elapsed_hours": summary.elapsed_hours,
            "stability_24h_passed": summary.stability_24h_passed,
            "last_record_hash": cycles[-1].record_hash,
        },
    )
    print(json.dumps(summary.model_dump(mode="json"), sort_keys=True))
    return 0 if summary.all_cycles_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
