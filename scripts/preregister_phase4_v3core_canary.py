#!/usr/bin/env python3
"""Create one immutable, non-admission prospective canary preregistration."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from advisorai.phase4 import (
    CANARY_EVIDENCE_CLASS,
    CANARY_MIN_CUTOFFS_PER_SYMBOL,
    CanaryPreregistration,
    ChronosRuntimeIdentity,
    sha256_file,
    write_canary_preregistration,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _git_head() -> str:
    result = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().lower()


def _write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def build_preregistration(
    *,
    admission: Path,
    qualification_evidence: Path,
    phase3_gate_sha256: str,
    canary_id: str,
    start_at: datetime,
    target_end_at: datetime,
) -> CanaryPreregistration:
    identity = ChronosRuntimeIdentity.from_admission(
        admission.resolve(),
        qualification_evidence_path=qualification_evidence.resolve(),
        repository_root=REPOSITORY_ROOT,
    )
    collector = REPOSITORY_ROOT / "scripts/collect_phase4_v3core_canary.py"
    finality = REPOSITORY_ROOT / "src/advisorai/phase4/v3core_canary.py"
    worker = REPOSITORY_ROOT / "src/advisorai/phase4/v3core_chronos.py"
    runner = REPOSITORY_ROOT / "scripts/run_phase4_v3core_canary_chronos.py"
    return CanaryPreregistration(
        canary_id=canary_id,
        created_at=datetime.now(UTC),
        start_at=start_at,
        target_end_at=target_end_at,
        repository_commit=_git_head(),
        collector_code_sha256=sha256_file(collector),
        finality_code_sha256=sha256_file(finality),
        chronos_worker_code_sha256=sha256_file(worker),
        chronos_runner_sha256=sha256_file(runner),
        model_identity=(
            f"{identity.checkpoint_repository}@{identity.checkpoint_revision}"
            f":model_identity={identity.model_identity_hash}"
        ),
        checkpoint_sha256=identity.checkpoint_hash,
        preprocessing_identity="v3core-raw-close-48-direct-chronos-v1",
        preprocessing_sha256=identity.preprocessing_hash,
        dependency_lock_sha256=identity.lock_hash,
        phase3_gate_sha256=phase3_gate_sha256,
        fail_fast_policy_id="v3core-prospective-canary-fail-fast-v1",
        fail_fast_policy_sha256=sha256_file(finality),
        watchdog_identity="v3core-prospective-canary-watchdog-v1",
        watchdog_sha256=sha256_file(REPOSITORY_ROOT / "scripts/watch_phase4_v3core_canary.py"),
        terminal_audit_sha256=sha256_file(
            REPOSITORY_ROOT / "scripts/audit_phase4_v3core_canary.py"
        ),
        start_rule="start collector and corrected Chronos before the first eligible hourly cutoff",
        terminal_rule="fixed target_end_at; no extension after observing failures",
        acceptance_criteria=(
            "four complete eligible hourly cutoffs for BTCUSDT",
            "four complete eligible hourly cutoffs for ETHUSDT",
            "zero post-admission revisions",
            "zero candidate schema, CUDA, NaN/Inf, or conflicting ledger failures",
            "all canary artifacts remain PROSPECTIVE_CANARY_ONLY and admission_eligible=false",
            "credentials_loaded=false and order_writes_attempted=false",
        ),
        minimum_cutoffs_per_symbol=CANARY_MIN_CUTOFFS_PER_SYMBOL,
    )


def write_preregistration(
    *,
    output_root: Path,
    admission: Path,
    qualification_evidence: Path,
    phase3_gate_sha256: str,
    canary_id: str,
    start_at: datetime,
    target_end_at: datetime,
) -> dict[str, str]:
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / "canary-preregistration.json"
    preregistration = build_preregistration(
        admission=admission,
        qualification_evidence=qualification_evidence,
        phase3_gate_sha256=phase3_gate_sha256,
        canary_id=canary_id,
        start_at=start_at,
        target_end_at=target_end_at,
    )
    digest = write_canary_preregistration(path, preregistration)
    digest_path = output_root / "canary-preregistration.sha256"
    if not digest_path.exists():
        _write_new(digest_path, f"{digest}  {path.name}\n")
    elif digest_path.read_text(encoding="utf-8") != f"{digest}  {path.name}\n":
        raise RuntimeError("canary preregistration digest sidecar conflicts")
    return {
        "canary_id": canary_id,
        "preregistration": str(path),
        "preregistration_sha256": digest,
        "start_at": start_at.isoformat(),
        "target_end_at": target_end_at.isoformat(),
        "evidence_class": CANARY_EVIDENCE_CLASS,
        "admission_eligible": "false",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--qualification-evidence", type=Path, required=True)
    parser.add_argument("--phase3-gate-sha256", required=True)
    parser.add_argument("--canary-id", required=True)
    parser.add_argument("--start-at", required=True)
    parser.add_argument("--target-end-at", required=True)
    args = parser.parse_args()
    try:
        start_at = datetime.fromisoformat(args.start_at.replace("Z", "+00:00")).astimezone(UTC)
        target_end_at = datetime.fromisoformat(
            args.target_end_at.replace("Z", "+00:00")
        ).astimezone(UTC)
        result = write_preregistration(
            output_root=args.output_root,
            admission=args.admission,
            qualification_evidence=args.qualification_evidence,
            phase3_gate_sha256=args.phase3_gate_sha256,
            canary_id=args.canary_id,
            start_at=start_at,
            target_end_at=target_end_at,
        )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise SystemExit(
            f"prospective canary preregistration refused ({type(exc).__name__})"
        ) from exc
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
