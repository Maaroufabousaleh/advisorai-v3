#!/usr/bin/env python3
"""Regenerate mandatory V3-Core baselines from a sealed input, causally.

This command is an offline post-seal materialization boundary.  It reads the
typed case contexts only, writes a new immutable causal-baseline root, and
labels every record as retrospective.  It never creates prospective ledger
records, reads credentials, acquires data, or submits orders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from advisorai.phase4 import V3CoreEvaluationInput, regenerate_causal_baselines


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_new(path: Path, payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
    return hashlib.sha256(encoded).hexdigest()


def regenerate(
    *,
    input_path: Path,
    output_root: Path,
    repository_root: Path,
    repository_commit: str,
    materialized_at: datetime,
) -> dict[str, str | int | bool]:
    input_path = input_path.resolve()
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError("causal baseline output root must be new")
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("input"), dict):
        raise ValueError("sealed V3-Core input has no typed input object")
    typed = V3CoreEvaluationInput.model_validate(payload["input"])
    if not typed.meets_minimum():
        raise ValueError("sealed V3-Core input is below the 64-per-symbol minimum")
    report = regenerate_causal_baselines(
        typed.build.cases,
        repository_root=repository_root,
        repository_commit=repository_commit,
        materialized_at=materialized_at,
    )
    output_root.mkdir(parents=True)
    report_payload = report.model_dump(mode="json")
    report_hash = _write_new(output_root / "causal-baseline-regeneration.json", report_payload)
    manifest = {
        "schema": "advisorai.phase4.v3-core-forward.causal-baseline.manifest.v1",
        "generated_at": materialized_at.isoformat(),
        "input": {"path": str(input_path), "sha256": _sha256(input_path)},
        "report": {"path": "causal-baseline-regeneration.json", "sha256": report_hash},
        "case_counts": typed.case_counts(),
        "prediction_count": len(report.predictions),
        "evidence_class": "post_seal_causal_regeneration",
        "future_outcome_used": False,
        "network_calls": 0,
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "notes": [
            "Predictions use only context_bars at each cutoff.",
            "Records are retrospective and are not prospective admission ledger records.",
            "LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.",
        ],
    }
    manifest_hash = _write_new(output_root / "evidence-manifest.json", manifest)
    return {
        "report": str(output_root / "causal-baseline-regeneration.json"),
        "report_sha256": report_hash,
        "manifest": str(output_root / "evidence-manifest.json"),
        "manifest_sha256": manifest_hash,
        "prediction_count": len(report.predictions),
        "network_calls": False,
        "credentials_loaded": False,
        "order_writes_attempted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--repository-commit", required=True)
    parser.add_argument("--materialized-at", required=True)
    arguments = parser.parse_args()
    try:
        materialized_at = datetime.fromisoformat(arguments.materialized_at).astimezone(UTC)
        result = regenerate(
            input_path=arguments.input,
            output_root=arguments.output_root,
            repository_root=arguments.repository_root,
            repository_commit=arguments.repository_commit,
            materialized_at=materialized_at,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"causal baseline regeneration refused ({type(exc).__name__})") from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
