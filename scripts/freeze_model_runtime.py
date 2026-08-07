#!/usr/bin/env python3
"""Freeze a machine-specific isolated model-runtime admission bundle."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from advisorai.phase0.runtime_qualification import (
    CheckpointPin,
    LocalCandidateAdmission,
    default_runtime_candidates,
    freeze_runtime_pin,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate")
    parser.add_argument("--checkpoint-pin", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--admission-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    candidates = {candidate.name: candidate for candidate in default_runtime_candidates()}
    if args.candidate not in candidates:
        raise SystemExit(f"unknown runtime candidate: {args.candidate}")
    candidate = candidates[args.candidate]
    if candidate.runtime_pin is None or candidate.external_checkpoint is None:
        raise SystemExit("candidate does not use an external isolated runtime")
    checkpoint = CheckpointPin.model_validate_json(args.checkpoint_pin.read_text(encoding="utf-8"))
    runtime = freeze_runtime_pin(
        project=candidate.runtime_pin.project,
        version_or_commit=candidate.runtime_pin.version_or_commit,
        python_constraint=candidate.runtime_pin.python_constraint,
        dependencies=candidate.runtime_pin.dependencies,
        environment_path=args.environment,
        lock_artifact_path=args.lock,
        worker_script=Path("scripts/runtime_qualification_worker.py").resolve(),
        worker_kind=candidate.family.value,
        runner_version=f"advisorai-{candidate.name}-worker-v1",
        admission_directory=args.admission_directory,
        repository_root=Path.cwd(),
    )
    admission = LocalCandidateAdmission(
        candidate_name=candidate.name,
        checkpoint=checkpoint,
        runtime_pin=runtime,
        created_at=datetime.now(UTC),
    )
    payload = (json.dumps(admission.model_dump(mode="json"), sort_keys=True, indent=2) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists() and args.output.read_bytes() != payload:
        raise SystemExit(f"immutable runtime admission already differs: {args.output}")
    args.output.write_bytes(payload)
    print(
        json.dumps(
            {
                "candidate": candidate.name,
                "output": str(args.output),
                "runtime_lock_sha256": runtime.lock_hash,
                "installed_environment_sha256": runtime.installed_environment_sha256,
                "worker_kind": runtime.worker_kind,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
