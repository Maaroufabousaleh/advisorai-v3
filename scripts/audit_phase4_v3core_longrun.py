#!/usr/bin/env python3
"""Run exactly one canonical terminal audit for a completed long-run."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from advisorai.phase4.v3core_canary import sha256_file
from advisorai.phase4.v3core_longrun_runtime import (
    audit_long_run,
    load_long_run_preregistration,
    long_run_audit_report_sha256,
)


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root.resolve()), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def run(
    *,
    repository_root: Path,
    preregistration_path: Path,
    preregistration_sha256: str,
    source_root: Path,
    candidate_root: Path,
    coordinator_root: Path,
    watchdog_root: Path,
    scheduler_root: Path,
    audit_root: Path,
    terminal_observed_at: datetime,
) -> dict[str, object]:
    repository_root = repository_root.resolve()
    preregistration_path = preregistration_path.resolve()
    preregistration = load_long_run_preregistration(
        preregistration_path, expected_sha256=preregistration_sha256
    )
    if _git_head(repository_root) != preregistration.repository_commit:
        raise ValueError("audit checkout differs from preregistered repository")
    if sha256_file(Path(__file__).resolve()) != preregistration.auditor_code_sha256:
        raise ValueError("long-run auditor code differs from preregistration")
    audit_root = audit_root.resolve()
    audit_root.mkdir(parents=True, exist_ok=True)
    report_path = audit_root / "terminal-report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if (
            report.get("generation_id") != preregistration.generation_id
            or report.get("preregistration_sha256") != preregistration_sha256
        ):
            raise ValueError("existing terminal report has a different identity")
        if report.get("report_hash") != long_run_audit_report_sha256(report):
            raise ValueError("existing terminal report internal hash is invalid")
        return {
            "path": str(report_path),
            "sha256": sha256_file(report_path),
            "report_hash": report.get("report_hash"),
            "reused": True,
            "phase4_result": report.get("phase4_result"),
        }
    report = audit_long_run(
        preregistration=preregistration,
        preregistration_sha256=preregistration_sha256,
        source_root=source_root.resolve(),
        candidate_root=candidate_root.resolve(),
        coordinator_root=coordinator_root.resolve(),
        watchdog_root=watchdog_root.resolve(),
        scheduler_root=scheduler_root.resolve(),
        repository_root=repository_root,
        terminal_observed_at=terminal_observed_at.astimezone(UTC),
    )
    encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    with report_path.open("x", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        import os

        os.fsync(handle.fileno())
    return {
        "path": str(report_path),
        "sha256": sha256_file(report_path),
        "report_hash": report["report_hash"],
        "reused": False,
        "phase4_result": report["phase4_result"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--preregistration-sha256", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--coordinator-root", type=Path, required=True)
    parser.add_argument("--watchdog-root", type=Path, required=True)
    parser.add_argument("--scheduler-root", type=Path, required=True)
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--terminal-observed-at", required=True)
    args = parser.parse_args()
    try:
        observed = datetime.fromisoformat(args.terminal_observed_at.replace("Z", "+00:00"))
        result = run(
            repository_root=args.repository_root,
            preregistration_path=args.preregistration,
            preregistration_sha256=args.preregistration_sha256,
            source_root=args.source_root,
            candidate_root=args.candidate_root,
            coordinator_root=args.coordinator_root,
            watchdog_root=args.watchdog_root,
            scheduler_root=args.scheduler_root,
            audit_root=args.audit_root,
            terminal_observed_at=observed,
        )
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"long-run terminal audit refused ({type(exc).__name__})") from exc
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["phase4_result"] == "PHASE4_LONGRUN_CERTIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
