#!/usr/bin/env python3
"""Validate or explicitly create one immutable V3-Core long-run preregistration.

The command is intentionally inert unless ``--create`` is supplied.  It never
starts a worker, acquires market data, loads credentials, or grants execution
authority.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from advisorai.phase4.v3core_canary import sha256_file
from advisorai.phase4.v3core_chronos import CHRONOS_PREPROCESSING_IDENTITY
from advisorai.phase4.v3core_longrun_runtime import (
    LONG_RUN_LAUNCH_WINDOW_SECONDS,
    LONG_RUN_PREREGISTRATION_SCHEMA,
    LongRunPreregistration,
    collect_runtime_attestation,
    derive_first_long_run_cutoff,
    derive_long_run_cutoffs,
    derive_long_run_source_snapshot_sha256,
    load_long_run_verification_results,
    long_run_component_files,
    long_run_preregistration_sha256,
    write_immutable_long_run_preregistration,
)


def _parse_utc(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset")
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("timestamps must be UTC")
    return parsed.astimezone(UTC)


def build_preregistration(
    *,
    repository_root: Path,
    generation_id: str,
    branch_or_tag: str,
    start_at: datetime,
    created_at: datetime,
    terminal_deadline: datetime,
    terminal_check_at: datetime,
    model_runtime_qualification_path: Path,
    phase3_gate_path: Path,
    verification_results_path: Path,
) -> LongRunPreregistration:
    load_long_run_verification_results(verification_results_path)
    files = long_run_component_files(repository_root)
    missing = [
        str(path)
        for path in (
            *files.values(),
            model_runtime_qualification_path,
            phase3_gate_path,
            verification_results_path,
        )
        if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError("missing preregistration identity files: " + ", ".join(missing))
    finality_path = files["finality_rule_sha256"]
    context_path = files["context_rule_sha256"]
    preprocessing_path = files["preprocessing_sha256"]
    code_hashes = {
        name: sha256_file(path)
        for name, path in files.items()
        if name not in {"finality_rule_sha256", "context_rule_sha256", "preprocessing_sha256"}
    }
    first_cutoff = derive_first_long_run_cutoff(start_at)
    mandatory_cutoffs = derive_long_run_cutoffs(start_at)
    repository_commit = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    finality_hash = sha256_file(finality_path)
    context_hash = sha256_file(context_path)
    source_snapshot_hash = derive_long_run_source_snapshot_sha256(
        generation_id=generation_id,
        repository_commit=repository_commit,
        start_at=start_at,
        first_mandatory_cutoff_at=first_cutoff,
        mandatory_cutoffs=mandatory_cutoffs,
        source_provider="binance_spot_public_market_data",
        rest_endpoint="https://data-api.binance.vision/api/v3/klines",
        interval="5m",
        finality_rule_id="v3core-admitted-final-60s-two-distinct-receipts-v1",
        finality_rule_sha256=finality_hash,
        context_rule_id="v3core-48-admitted-final-newest-minus-10m-v1",
        context_rule_sha256=context_hash,
    )
    preregistration = LongRunPreregistration(
        schema=LONG_RUN_PREREGISTRATION_SCHEMA,
        generation_id=generation_id,
        branch_or_tag=branch_or_tag,
        repository_commit=repository_commit,
        created_at=created_at,
        start_at=start_at,
        launch_not_before_at=start_at,
        launch_not_after_at=start_at + timedelta(seconds=LONG_RUN_LAUNCH_WINDOW_SECONDS),
        first_mandatory_cutoff_at=first_cutoff,
        mandatory_cutoffs=mandatory_cutoffs,
        finality_rule_sha256=finality_hash,
        context_rule_sha256=context_hash,
        preprocessing_sha256=sha256_file(preprocessing_path),
        **code_hashes,
        verification_results_sha256=sha256_file(verification_results_path),
        model_runtime_qualification_sha256=sha256_file(model_runtime_qualification_path),
        source_snapshot_sha256=source_snapshot_hash,
        runtime_attestation_sha256=collect_runtime_attestation().attestation_hash,
        phase3_gate_sha256=sha256_file(phase3_gate_path),
        terminal_deadline=terminal_deadline,
        terminal_check_at=terminal_check_at,
    )
    if preregistration.preprocessing_identity != CHRONOS_PREPROCESSING_IDENTITY:
        raise ValueError("unexpected Chronos preprocessing identity")
    return preregistration


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--start-at", required=True, help="UTC ISO-8601 timestamp")
    parser.add_argument("--terminal-deadline", required=True)
    parser.add_argument("--terminal-check-at", required=True)
    parser.add_argument("--model-runtime-qualification", type=Path, required=True)
    parser.add_argument("--phase3-gate", type=Path, required=True)
    parser.add_argument("--verification-results", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--branch-or-tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--create", action="store_true", help="perform the one-time immutable write"
    )
    args = parser.parse_args()
    root = args.repository_root.resolve()
    preregistration = build_preregistration(
        repository_root=root,
        generation_id=args.generation_id,
        branch_or_tag=args.branch_or_tag,
        start_at=_parse_utc(args.start_at),
        created_at=datetime.now(UTC),
        terminal_deadline=_parse_utc(args.terminal_deadline),
        terminal_check_at=_parse_utc(args.terminal_check_at),
        model_runtime_qualification_path=args.model_runtime_qualification.resolve(),
        phase3_gate_path=args.phase3_gate.resolve(),
        verification_results_path=args.verification_results.resolve(),
    )
    digest = long_run_preregistration_sha256(preregistration)
    result = {
        "generation_id": preregistration.generation_id,
        "preregistration_sha256": digest,
        "created": False,
        "output": str(args.output.resolve()),
        "evidence_class": preregistration.evidence_class,
        "admission_eligible": preregistration.admission_eligible,
    }
    if args.create:
        written_hash = write_immutable_long_run_preregistration(args.output, preregistration)
        if written_hash != digest:
            raise RuntimeError("immutable preregistration canonical hash changed after write")
        result["created"] = True
        result["file_sha256"] = sha256_file(args.output)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
