#!/usr/bin/env python3
"""Acquire one exact Phase-0 model closure into the external clean cache."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from advisorai.config import CredentialResolver, CredentialScope
from advisorai.phase0 import (
    GatedTermsAcceptanceRequired,
    acquire_candidate_artifacts,
    checkpoint_pin_payload,
    default_runtime_candidates,
    write_acquisition_manifest,
)


def _model_registry_token(path: Path | None) -> tuple[str | None, tuple[str, ...]]:
    if path is None or not path.exists():
        return None, ()
    resolver = CredentialResolver.from_env_file(path)
    names = resolver.available_names(CredentialScope.MODEL_REGISTRY)
    token = resolver.get(CredentialScope.MODEL_REGISTRY, "HF_TOKEN")
    if token is None:
        token = resolver.get(CredentialScope.MODEL_REGISTRY, "HUGGINGFACE_HUB_TOKEN")
    return token, names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", help="exact active CandidateSpec name")
    parser.add_argument(
        "--staging-root",
        type=Path,
        default=Path("~/.cache/advisorai-v3/staging").expanduser(),
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path("~/.cache/advisorai-v3/models").expanduser(),
    )
    parser.add_argument(
        "--secrets-file",
        type=Path,
        default=Path(os.getenv("ADVISORAI_SECRETS_FILE", "secrets.env")),
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=Path("artifacts/phase0/model-runtime-qualification"),
    )
    args = parser.parse_args()
    by_name = {candidate.name: candidate for candidate in default_runtime_candidates()}
    candidate = by_name.get(args.candidate)
    if candidate is None or candidate.external_checkpoint is None:
        raise SystemExit("candidate is not an active external-model registry entry")
    token, credential_names = _model_registry_token(args.secrets_file)
    run_id = datetime.now(UTC).strftime("acquisition-%Y%m%dT%H%M%S.%fZ")
    run_directory = args.evidence_root / run_id / candidate.name
    try:
        pin, result = acquire_candidate_artifacts(
            candidate,
            staging_root=args.staging_root,
            cache_root=args.cache_root,
            repository_root=Path.cwd(),
            token=token,
        )
    except GatedTermsAcceptanceRequired:
        failure_path = run_directory / "acquisition-failure.json"
        failure_payload = (
            json.dumps(
                {
                    "schema": "advisorai.phase0.model-acquisition-failure.v1",
                    "candidate": candidate.name,
                    "repository_id": candidate.external_checkpoint.repository_id,
                    "revision": candidate.external_checkpoint.revision,
                    "status": "waiting_for_user_acceptance",
                    "error_class": "GatedTermsAcceptanceRequired",
                    "credential_names_available": credential_names,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode()
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.write_bytes(failure_payload)
        print(
            json.dumps(
                {
                    "candidate": candidate.name,
                    "status": "waiting_for_user_acceptance",
                    "failure_path": str(failure_path),
                },
                sort_keys=True,
            )
        )
        return 0
    manifest_path = write_acquisition_manifest(result, run_directory / "acquisition.json")
    pin_path = run_directory / "checkpoint-pin.json"
    pin_payload = (
        json.dumps(checkpoint_pin_payload(pin), sort_keys=True, indent=2) + "\n"
    ).encode()
    if pin_path.exists() and pin_path.read_bytes() != pin_payload:
        raise FileExistsError(f"immutable checkpoint evidence differs: {pin_path}")
    pin_path.parent.mkdir(parents=True, exist_ok=True)
    pin_path.write_bytes(pin_payload)
    print(
        json.dumps(
            {
                "candidate": candidate.name,
                "revision": pin.revision,
                "cache_path": pin.cache_path,
                "anonymous": result.anonymous,
                "credential_names_available": credential_names,
                "manifest_path": str(manifest_path),
                "manifest_hash": result.manifest_hash,
                "checkpoint_pin_path": str(pin_path),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
