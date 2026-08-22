#!/usr/bin/env python3
"""Run the read-only terminal audit for one completed prospective canary."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from advisorai.phase4 import (
    CANARY_CONTEXT_LAG_SECONDS,
    CANARY_EVIDENCE_CLASS,
    V3_CORE_SYMBOLS,
    CanaryFinalityTracker,
    CanaryPredictionLedger,
    CanaryRejectionLedger,
    ForwardNormalizedBarSpool,
    ForwardRawSpool,
    context_for_cutoff,
    load_canary_preregistration,
    require_canary_artifact,
    sha256_file,
)
from advisorai.phase4.v3core_chronos import _input_snapshot_hash

AUDIT_SCHEMA = "advisorai.phase4.v3-core.prospective-canary.terminal-audit.v1"


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _lock_free(path: Path) -> bool:
    if not path.exists():
        return True
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        finally:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            except OSError:
                pass
    return True


def _outcome_link_count(
    predictions: CanaryPredictionLedger, bars: tuple[object, ...]
) -> tuple[int, list[str]]:
    by_key = {(bar.instrument, bar.interval_end): bar for bar in bars}
    linked = 0
    missing: list[str] = []
    for entry in predictions.records:
        prediction = entry.prediction
        future = tuple(
            by_key.get(
                (
                    prediction.instrument,
                    prediction.cutoff + timedelta(seconds=300 * (index + 1)),
                )
            )
            for index in range(12)
        )
        if any(bar is None for bar in future):
            missing.append(prediction.prediction_id)
        else:
            linked += 1
    return linked, missing


def audit(
    *,
    preregistration: Path,
    preregistration_sha256: str,
    source_root: Path,
    candidate_root: Path,
    watchdog_root: Path,
    output_root: Path,
    phase3_gate_sha256: str,
    repository_root: Path,
) -> dict[str, object]:
    prereg = load_canary_preregistration(preregistration, expected_sha256=preregistration_sha256)
    if sha256_file(Path(__file__).resolve()) != prereg.terminal_audit_sha256:
        raise ValueError("terminal audit code does not match the canary preregistration")
    source_manifest = _load_json(source_root / "manifest.json")
    source_status = _load_json(source_root / "status.json")
    candidate_manifest = _load_json(candidate_root / "manifest.json")
    candidate_status = _load_json(candidate_root / "status.json")
    require_canary_artifact(source_manifest)
    require_canary_artifact(source_status)
    require_canary_artifact(candidate_manifest)
    require_canary_artifact(candidate_status)
    if source_status.get("state") != "deadline_reached":
        raise ValueError("canary source is not terminal")
    if candidate_status.get("state") != "deadline_reached":
        raise ValueError("canary candidate is not terminal")
    if source_manifest.get("preregistration_sha256") != preregistration_sha256:
        raise ValueError("source preregistration identity mismatch")
    if candidate_manifest.get("preregistration_sha256") != preregistration_sha256:
        raise ValueError("candidate preregistration identity mismatch")
    if source_manifest.get("phase3_gate_record_sha256") != phase3_gate_sha256:
        raise ValueError("source Phase-3 gate identity mismatch")
    if candidate_manifest.get("phase3_gate_record_sha256") != phase3_gate_sha256:
        raise ValueError("candidate Phase-3 gate identity mismatch")
    if source_manifest.get("repository_commit") != prereg.repository_commit:
        raise ValueError("source repository identity mismatch")
    if candidate_manifest.get("repository_commit") != prereg.repository_commit:
        raise ValueError("candidate repository identity mismatch")
    if any(
        not _lock_free(root / lock_name)
        for root, lock_name in ((source_root, "collector.lock"), (candidate_root, "candidate.lock"))
    ):
        raise ValueError("canary evidence lock is still active")
    temporary_root = Path(tempfile.mkdtemp(prefix="advisorai-canary-audit-"))
    try:
        source_bars_path = source_root / "normalized-bars.jsonl"
        copied_bars_path = temporary_root / "normalized-bars.jsonl"
        if source_bars_path.is_file():
            shutil.copyfile(source_bars_path, copied_bars_path)
        normalized = ForwardNormalizedBarSpool(copied_bars_path)
        raw = ForwardRawSpool(source_root / "raw-responses.jsonl")
        tracker = CanaryFinalityTracker(normalized, temporary_root / "revisions.jsonl")
        tracker.replay(raw.read(), str(source_manifest["source_snapshot_hash"]))
        admitted = normalized.read()
        predictions = CanaryPredictionLedger(candidate_root / "predictions.jsonl")
        rejections = CanaryRejectionLedger(candidate_root / "rejections.jsonl")
        linked, missing = _outcome_link_count(predictions, admitted)
        context_invalid: list[str] = []
        for entry in predictions.records:
            prediction = entry.prediction
            context = context_for_cutoff(
                admitted,
                instrument=prediction.instrument,
                cutoff=prediction.cutoff,
                now=prediction.cutoff,
                newest_context_lag_seconds=CANARY_CONTEXT_LAG_SECONDS,
            )
            if (
                context is None
                or _input_snapshot_hash(context, prediction.cutoff)
                != prediction.input_snapshot_hash
            ):
                context_invalid.append(prediction.prediction_id)
        counts = {
            symbol: sum(entry.prediction.instrument == symbol for entry in predictions.records)
            for symbol in V3_CORE_SYMBOLS
        }
        eligible = all(
            counts[symbol] >= prereg.minimum_cutoffs_per_symbol for symbol in V3_CORE_SYMBOLS
        )
        qualified = (
            eligible
            and not rejections.records
            and not tracker.revisions
            and not context_invalid
            and not missing
            and source_status.get("credentials_loaded") is False
            and source_status.get("order_writes_attempted") is False
            and candidate_status.get("credentials_loaded") is False
            and candidate_status.get("order_writes_attempted") is False
        )
        report: dict[str, object] = {
            "schema": AUDIT_SCHEMA,
            "canary_id": prereg.canary_id,
            "audited_at": datetime.now(UTC).isoformat(),
            "repository_commit": prereg.repository_commit,
            "preregistration_sha256": preregistration_sha256,
            "phase3_gate_sha256": phase3_gate_sha256,
            "evidence_class": CANARY_EVIDENCE_CLASS,
            "admission_eligible": False,
            "canary_qualified": qualified,
            "source": {
                "raw_receipts": len(raw.records),
                "admitted_final_bars": len(admitted),
                "metrics": tracker.metrics(),
                "manifest_sha256": sha256_file(source_root / "manifest.json"),
                "status_sha256": sha256_file(source_root / "status.json"),
            },
            "candidate": {
                "prediction_counts": counts,
                "predictions": len(predictions.records),
                "rejections": len(rejections.records),
                "rejection_reasons": [record.reason for record in rejections.records],
                "context_invalid": context_invalid,
                "outcome_links": linked,
                "outcome_missing_prediction_ids": missing,
                "manifest_sha256": sha256_file(candidate_root / "manifest.json"),
                "status_sha256": sha256_file(candidate_root / "status.json"),
            },
            "watchdog_status_sha256": (
                sha256_file(watchdog_root / "status.json")
                if (watchdog_root / "status.json").is_file()
                else None
            ),
            "notes": [
                "This is a canary qualification report, not Phase-4 admission evidence.",
                "No baseline, utility, materialization, or formal reviewer was run.",
                "The long multi-day generation remains separately blocked pending human review.",
            ],
        }
        output_root.mkdir(parents=True, exist_ok=True)
        report_path = output_root / "canary-terminal-audit.json"
        encoded = json.dumps(report, sort_keys=True, indent=2) + "\n"
        if report_path.exists():
            if report_path.read_text(encoding="utf-8") != encoded:
                raise RuntimeError("existing canary terminal audit conflicts")
        else:
            with report_path.open("x", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        digest = sha256_file(report_path)
        digest_path = output_root / "canary-terminal-audit.sha256"
        if not digest_path.exists():
            with digest_path.open("x", encoding="ascii") as handle:
                handle.write(f"{digest}  {report_path.name}\n")
        return {"report": str(report_path), "report_sha256": digest, "canary_qualified": qualified}
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--preregistration-sha256", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--watchdog-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--phase3-gate-sha256", required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        result = audit(
            preregistration=args.preregistration.resolve(),
            preregistration_sha256=args.preregistration_sha256,
            source_root=args.source_root.resolve(),
            candidate_root=args.candidate_root.resolve(),
            watchdog_root=args.watchdog_root.resolve(),
            output_root=args.output_root.resolve(),
            phase3_gate_sha256=args.phase3_gate_sha256,
            repository_root=args.repository_root.resolve(),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"prospective canary terminal audit refused ({type(exc).__name__})"
        ) from exc
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["canary_qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
