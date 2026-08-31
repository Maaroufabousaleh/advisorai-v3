#!/usr/bin/env python3
"""Run actual-file long-run release preflight without creating a preregistration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from advisorai.gates import GateDecision, PhaseGateRecord
from advisorai.phase4.v3core_canary import sha256_file
from advisorai.phase4.v3core_longrun_runtime import (
    attest_long_run_identity,
    evaluate_release_candidate_preflight,
    load_long_run_preregistration,
    long_run_component_files,
)


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _phase3_passed(path: Path) -> bool:
    gate = PhaseGateRecord.model_validate_json(path.read_text(encoding="utf-8"))
    return gate.phase == 3 and gate.decision is GateDecision.PASSED


def _runtime_passed(path: Path) -> bool:
    value = _load_json(path)
    resource = value.get("resource")
    candidate = value.get("candidate")
    checkpoint = candidate.get("external_checkpoint") if isinstance(candidate, dict) else None
    repository = checkpoint.get("repository") if isinstance(checkpoint, dict) else None
    runtime_pin = candidate.get("runtime_pin") if isinstance(candidate, dict) else None
    runtime_artifacts = (
        repository.get("runtime_artifacts") if isinstance(repository, dict) else None
    )
    checkpoint_hash = (
        next(
            (
                item.get("sha256")
                for item in runtime_artifacts
                if isinstance(item, dict) and item.get("relative_path") == "model.safetensors"
            ),
            None,
        )
        if isinstance(runtime_artifacts, list)
        else None
    )
    return (
        value.get("status") == "measured"
        and value.get("one_inference_completed") is True
        and value.get("output_schema_valid") is True
        and value.get("nan_inf_rejection_passed") is True
        and value.get("network_access_attempted") is False
        and value.get("offline_cached_inference") is True
        and isinstance(resource, dict)
        and resource.get("resource_limit_passed") is True
        and isinstance(repository, dict)
        and repository.get("repository_id") == "autogluon/chronos-2-small"
        and repository.get("revision") == "ddec01313e50b6bc58ebaa92ede81bc24a3d9f9a"
        and checkpoint_hash == "492290ae82bb89f9769e3479ce90b3179de1f33e600c34daa0352531538b23cd"
        and isinstance(runtime_pin, dict)
        and runtime_pin.get("lock_hash")
        == "260b47a47432d58c80ec1f850563782d8302ecf8748950c2b85200152e1dfaec"
    )


def run(
    *,
    repository_root: Path,
    contract_path: Path,
    requirements_lock_path: Path,
    checkpoint_path: Path,
    phase3_gate_path: Path,
    model_runtime_qualification_path: Path,
    check_results_path: Path,
    output_path: Path | None,
    gpu_lease_free: bool,
) -> dict[str, object]:
    repository_root = repository_root.resolve()
    contract = load_long_run_preregistration(contract_path.resolve())
    check_results = _load_json(check_results_path.resolve())
    component_files = long_run_component_files(repository_root)
    attestation = attest_long_run_identity(
        repository_root=repository_root,
        expected_repository_commit=contract.repository_commit,
        component_files=component_files,
        requirements_lock_path=requirements_lock_path.resolve(),
        checkpoint_path=checkpoint_path.resolve(),
        phase3_gate_path=phase3_gate_path.resolve(),
        model_runtime_qualification_path=model_runtime_qualification_path.resolve(),
    )
    report = evaluate_release_candidate_preflight(
        contract,
        attestation=attestation,
        phase3_gate_passed=_phase3_passed(phase3_gate_path.resolve()),
        model_runtime_passed=_runtime_passed(model_runtime_qualification_path.resolve()),
        preflight_checks={key: value is True for key, value in check_results.items()},
        gpu_lease_free=gpu_lease_free,
    ).model_dump(mode="json")
    if output_path is not None:
        destination = output_path.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
        with destination.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            import os

            os.fsync(handle.fileno())
        report["output_sha256"] = sha256_file(destination)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--requirements-lock", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--phase3-gate", type=Path, required=True)
    parser.add_argument("--model-runtime-qualification", type=Path, required=True)
    parser.add_argument("--check-results", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--gpu-lease-free", action="store_true")
    args = parser.parse_args()
    try:
        report = run(
            repository_root=args.repository_root,
            contract_path=args.contract,
            requirements_lock_path=args.requirements_lock,
            checkpoint_path=args.checkpoint,
            phase3_gate_path=args.phase3_gate,
            model_runtime_qualification_path=args.model_runtime_qualification,
            check_results_path=args.check_results,
            output_path=args.output,
            gpu_lease_free=args.gpu_lease_free,
        )
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"long-run release preflight refused ({type(exc).__name__}): {exc}"
        ) from exc
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["decision"] == "RELEASE_CANDIDATE_READY_FOR_FINAL_HUMAN_REVIEW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
