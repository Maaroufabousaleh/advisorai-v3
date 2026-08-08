#!/usr/bin/env python3
"""Run short, offline Phase-0 model-runtime smoke qualification.

This command never downloads checkpoints.  It qualifies built-in baselines
when their local dependencies are available and writes explicit quarantine
manifests for external candidates whose pinned cache/runner is not present.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from advisorai.phase0.runtime_qualification import (
    BenchmarkDataset,
    CandidateSpec,
    FunctionalRunner,
    LocalCandidateAdmission,
    ModelFamily,
    ModelTask,
    RuntimeQualificationResult,
    apply_local_candidate_admission,
    default_runtime_candidates,
    forecast_baseline_models,
    project_bakeoff_gate,
    run_forecast_baseline_benchmark,
    run_runtime_qualification,
    write_qualification_bundle,
)


def _forecast_payload(payload: object) -> tuple[Decimal, ...]:
    if isinstance(payload, (tuple, list)) and payload and isinstance(payload[0], (tuple, list)):
        payload = payload[0]
    if not isinstance(payload, (tuple, list)):
        raise TypeError("forecast smoke payload must be a sequence")
    return tuple(Decimal(str(value)) for value in payload)


def _baseline_runner(candidate: CandidateSpec) -> FunctionalRunner:
    model = forecast_baseline_models()[candidate.family.value]

    def infer(loaded: object, payload: object) -> tuple[Decimal, ...]:
        if isinstance(payload, (tuple, list)) and payload and isinstance(payload[0], (tuple, list)):
            return tuple(
                tuple(loaded.predict(_forecast_payload(history), 1))  # type: ignore[attr-defined]
                for history in payload
            )
        history = _forecast_payload(payload)
        return tuple(loaded.predict(history, 1))  # type: ignore[attr-defined]

    return FunctionalRunner(
        model_family=candidate.family.value,
        load_fn=lambda checkpoint, offline: model,
        infer_fn=infer,
        version=f"builtin-{candidate.family.value}-v1",
    )


def _dataset_for(candidate: CandidateSpec) -> BenchmarkDataset:
    if candidate.task == ModelTask.FINANCE_SENTIMENT:
        return BenchmarkDataset.finbert_fixture()
    if candidate.task == ModelTask.TSPULSE_FEATURES:
        return BenchmarkDataset.tspulse_runtime_fixture()
    if candidate.family in {
        ModelFamily.CHRONOS_2_SMALL,
        ModelFamily.KRONOS_MINI,
        ModelFamily.KRONOS_SMALL,
        ModelFamily.TTM_R2,
        ModelFamily.TTM_R3,
    }:
        return BenchmarkDataset.ttm_runtime_fixture()
    return BenchmarkDataset.synthetic_forecast()


def _payloads(dataset: BenchmarkDataset) -> tuple[object, object]:
    if dataset.task == ModelTask.FINANCE_SENTIMENT:
        texts = tuple(text for text, _label in dataset.public_text_fixture)
        return texts[0], texts
    if dataset.task == ModelTask.TSPULSE_FEATURES:
        return dataset.inputs[:512], (dataset.inputs[:512], dataset.inputs[512:1024])
    if dataset.dataset_id == "advisorai-phase0-ttm-runtime-fixture":
        return dataset.inputs[:512], (dataset.inputs[:512], dataset.inputs[30:542])
    return dataset.inputs[:16], (dataset.inputs[:16], dataset.inputs[16:])


def qualify(
    output_dir: Path,
    *,
    admission_paths: tuple[Path, ...] = (),
    selected_candidates: frozenset[str] | None = None,
) -> tuple[RuntimeQualificationResult, ...]:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id_base = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_id = run_id_base
    suffix = 1
    while (output_dir / run_id).exists():
        suffix += 1
        run_id = f"{run_id_base}-{suffix}"
    run_dir = output_dir / run_id
    results: list[RuntimeQualificationResult] = []
    forecast_dataset = BenchmarkDataset.synthetic_forecast()
    forecast_evaluations = run_forecast_baseline_benchmark(forecast_dataset)
    evaluations_by_name = {item.model_name: item for item in forecast_evaluations}
    admissions = {
        admission.candidate_name: admission
        for path in admission_paths
        for admission in (
            LocalCandidateAdmission.model_validate_json(path.read_text(encoding="utf-8")),
        )
    }
    roster = default_runtime_candidates()
    if selected_candidates is not None:
        unknown = selected_candidates - {candidate.name for candidate in roster}
        if unknown:
            raise ValueError(f"unknown selected candidates: {sorted(unknown)}")
        roster = tuple(candidate for candidate in roster if candidate.name in selected_candidates)
    for candidate in roster:
        if candidate.name in admissions:
            candidate = apply_local_candidate_admission(candidate, admissions[candidate.name])
        dataset = _dataset_for(candidate)
        sample, batch = _payloads(dataset)
        runner = _baseline_runner(candidate) if candidate.external_checkpoint is None else None
        result = run_runtime_qualification(
            candidate,
            runner=runner,
            dataset=dataset,
            sample_input=sample,
            batch_input=batch,
            repeats=3,
            repository_root=Path.cwd(),
        )
        if result.status.value == "measured" and result.resource is not None:
            evaluation = evaluations_by_name.get(candidate.family.value)
            if evaluation is not None:
                evaluation = evaluation.model_copy(
                    update={
                        "latency_ms": max(1, round(result.resource.warm_inference_p50_ms)),
                        "peak_ram_mib": round(result.resource.rss_peak_mib),
                        "peak_vram_mib": round(result.resource.vram_peak_mib or 0),
                        "resource_limit_passed": result.resource.resource_limit_passed,
                    }
                )
                evaluation = type(evaluation).model_validate(evaluation.model_dump())
                result = RuntimeQualificationResult.model_validate(
                    {
                        **result.model_dump(),
                        "forecast_evaluations": (evaluation,),
                    }
                )
        results.append(result)
    paths = write_qualification_bundle(results, run_dir)
    manifest_hashes = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))["manifest_hash"]
        for path in paths
        if path.name != "index.json"
    }
    results = tuple(
        RuntimeQualificationResult.model_validate(
            {**result.model_dump(), "manifest_hash": manifest_hashes.get(result.candidate.name)}
        )
        for result in results
    )
    benchmark_evaluations = [
        evaluation for result in results for evaluation in result.forecast_evaluations
    ]
    benchmark_path = run_dir / "forecast-baseline-benchmark.json"
    benchmark_payload = (
        json.dumps(
            {
                "schema": "advisorai.phase0.forecast-baseline-benchmark.v1",
                "dataset": forecast_dataset.model_dump(mode="json"),
                "do_not_claim_superiority_from_one_series": True,
                "evaluations": [item.model_dump(mode="json") for item in benchmark_evaluations],
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode()
    if benchmark_path.exists() and benchmark_path.read_bytes() != benchmark_payload:
        raise FileExistsError(
            f"immutable evidence already exists with different content: {benchmark_path}"
        )
    benchmark_path.write_bytes(benchmark_payload)
    gate_path = run_dir / "bakeoff-gate.json"
    gate_payload = (
        json.dumps(project_bakeoff_gate(results).model_dump(mode="json"), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    if gate_path.exists() and gate_path.read_bytes() != gate_payload:
        raise FileExistsError(
            f"immutable evidence already exists with different content: {gate_path}"
        )
    gate_path.write_bytes(gate_payload)
    pointer = {
        "schema": "advisorai.phase0.model-runtime-qualification.latest.v1",
        "run_id": run_id,
        "run_path": run_id,
        "index_sha256": sha256((run_dir / "index.json").read_bytes()).hexdigest(),
    }
    pointer_payload = (json.dumps(pointer, sort_keys=True, indent=2) + "\n").encode()
    for pointer_name in ("latest.json", "index.json"):
        (output_dir / pointer_name).write_bytes(pointer_payload)
    return tuple(results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/phase0/model-runtime-qualification"),
    )
    parser.add_argument(
        "--admission",
        action="append",
        type=Path,
        default=[],
        help="machine-specific local candidate admission JSON (repeatable)",
    )
    parser.add_argument(
        "--candidate",
        action="append",
        default=[],
        help="qualify only this candidate (repeatable)",
    )
    args = parser.parse_args()
    results = qualify(
        args.output,
        admission_paths=tuple(args.admission),
        selected_candidates=frozenset(args.candidate) if args.candidate else None,
    )
    latest = json.loads((args.output / "latest.json").read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "output": str(args.output),
                "run_id": latest["run_id"],
                "candidates": {
                    result.candidate.name: {
                        "status": result.status.value,
                        "reason": result.failure_reason,
                        "manifest_hash": result.manifest_hash,
                    }
                    for result in results
                },
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
