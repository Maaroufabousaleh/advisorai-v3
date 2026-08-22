#!/usr/bin/env python3
"""Run the corrected Chronos candidate for the bounded non-admission canary."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from advisorai.phase4 import (
    CANARY_CONTEXT_LAG_SECONDS,
    CANARY_EVIDENCE_CLASS,
    V3_CORE_SYMBOLS,
    CanaryPredictionLedger,
    CanaryRejectionLedger,
    ChronosInferenceFailure,
    ChronosRuntimeIdentity,
    ForwardNormalizedBarSpool,
    ForwardPredictionRecord,
    build_chronos_prediction,
    load_canary_preregistration,
    sha256_file,
)
from advisorai.phase4.v3core_chronos import (
    CHRONOS_HORIZON_BARS,
    CHRONOS_MODEL,
    CHRONOS_PREPROCESSING_IDENTITY,
    _input_snapshot_hash,
    _prediction_id,
    context_for_cutoff,
    infer_chronos,
)

RUN_SCHEMA = "advisorai.phase4.v3-core.prospective-canary.chronos.v1"
POLL_SECONDS = 5.0
WORKER_TIMEOUT_SECONDS = 120.0


def _write_atomic(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _git_commit(repository_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _candidate_cutoffs(
    bars: Sequence[object], *, symbol: str, context_lag_seconds: int
) -> tuple[datetime, ...]:
    return tuple(
        sorted(
            {
                bar.interval_end + timedelta(seconds=context_lag_seconds)
                for bar in bars
                if bar.instrument == symbol
                and (bar.interval_end + timedelta(seconds=context_lag_seconds)).minute % 60 == 0
                and (bar.interval_end + timedelta(seconds=context_lag_seconds)).second == 0
                and (bar.interval_end + timedelta(seconds=context_lag_seconds)).microsecond == 0
            }
        )
    )


def _validate_existing(
    prediction: ForwardPredictionRecord,
    *,
    identity: ChronosRuntimeIdentity,
    context: Sequence[object],
    instrument: str,
    cutoff: datetime,
) -> None:
    if len(context) != 48:
        raise RuntimeError("existing canary prediction does not have a 48-bar context")
    source_hashes = {bar.source_snapshot_hash for bar in context}
    if len(source_hashes) != 1:
        raise RuntimeError("existing canary prediction has mixed source snapshots")
    expected = {
        "prediction_id": _prediction_id(instrument, cutoff),
        "instrument": instrument,
        "model": CHRONOS_MODEL,
        "model_identity_hash": identity.model_identity_hash,
        "cutoff": cutoff,
        "input_snapshot_hash": _input_snapshot_hash(context, cutoff),
        "source_snapshot_hash": next(iter(source_hashes)),
        "checkpoint_hash": identity.checkpoint_hash,
        "runner_hash": identity.runner_hash,
        "preprocessing_identity": CHRONOS_PREPROCESSING_IDENTITY,
        "preprocessing_hash": identity.preprocessing_hash,
        "dependency_lock_hash": identity.lock_hash,
        "runtime_environment_hash": identity.environment_fingerprint,
        "device": identity.device,
    }
    actual = {field: getattr(prediction, field) for field in expected}
    if actual != expected:
        raise RuntimeError(
            "existing canary prediction scientific identity mismatch: "
            + ", ".join(field for field in expected if actual[field] != expected[field])
        )


def _canary_prediction(
    *,
    identity: ChronosRuntimeIdentity,
    prediction: ForwardPredictionRecord,
) -> ForwardPredictionRecord:
    provenance = dict(prediction.provenance)
    provenance.update(
        {
            "experiment_evidence_class": CANARY_EVIDENCE_CLASS,
            "admission_eligible": "false",
            "context_newest_lag_seconds": str(CANARY_CONTEXT_LAG_SECONDS),
            "prediction_horizon_bars": str(CHRONOS_HORIZON_BARS),
            "model_identity_hash": identity.model_identity_hash,
        }
    )
    return prediction.model_copy(update={"provenance": tuple(sorted(provenance.items()))})


def _status(
    *,
    state: str,
    ledger: CanaryPredictionLedger,
    rejections: CanaryRejectionLedger,
    identity: ChronosRuntimeIdentity,
    target_end_at: datetime,
    failure_reason: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "schema": RUN_SCHEMA,
        "state": state,
        "updated_at": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
        "model": CHRONOS_MODEL,
        "model_identity_hash": identity.model_identity_hash,
        "prediction_count": len(ledger.records),
        "prediction_counts": {
            symbol: sum(record.prediction.instrument == symbol for record in ledger.records)
            for symbol in V3_CORE_SYMBOLS
        },
        "rejection_count": len(rejections.records),
        "rejection_reasons": [record.reason for record in rejections.records],
        "model_loaded": bool(ledger.records),
        "network_calls": 0,
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "evidence_class": CANARY_EVIDENCE_CLASS,
        "admission_eligible": False,
        "phase4_materialization_eligible": False,
        "target_end_at": target_end_at.isoformat(),
    }
    if failure_reason is not None:
        result["failure_reason"] = failure_reason
    return result


def run(
    *,
    admission_path: Path,
    qualification_evidence_path: Path,
    source_root: Path,
    run_root: Path,
    repository_root: Path,
    preregistration: Path,
    preregistration_sha256: str,
    phase3_gate_sha256: str,
    poll_seconds: float = POLL_SECONDS,
    worker_timeout_seconds: float = WORKER_TIMEOUT_SECONDS,
) -> dict[str, object]:
    if poll_seconds <= 0 or worker_timeout_seconds <= 0:
        raise ValueError("poll and worker timeout values must be positive")
    repository_root = repository_root.resolve()
    preregistration = preregistration.resolve()
    prereg = load_canary_preregistration(preregistration, expected_sha256=preregistration_sha256)
    if _git_commit(repository_root) != prereg.repository_commit:
        raise ValueError("canary Chronos repository commit differs from preregistration")
    worker_path = repository_root / "src/advisorai/phase4/v3core_chronos.py"
    if sha256_file(worker_path) != prereg.chronos_worker_code_sha256:
        raise ValueError("Chronos worker code differs from preregistration")
    if sha256_file(Path(__file__).resolve()) != prereg.chronos_runner_sha256:
        raise ValueError("canary Chronos runner differs from preregistration")
    source_root = source_root.resolve()
    source_manifest = _load_json(source_root / "manifest.json")
    if source_manifest.get("evidence_class") != CANARY_EVIDENCE_CLASS:
        raise ValueError("canary Chronos requires the canary source evidence class")
    if source_manifest.get("admission_eligible") is not False:
        raise ValueError("canary source must be explicitly non-admission evidence")
    if source_manifest.get("preregistration_sha256") != preregistration_sha256:
        raise ValueError("source and canary preregistration identities differ")
    if source_manifest.get("phase3_gate_record_sha256") != phase3_gate_sha256:
        raise ValueError("source and Phase-3 gate identities differ")
    if source_manifest.get("context_newest_lag_seconds") != CANARY_CONTEXT_LAG_SECONDS:
        raise ValueError("source context boundary is not the preregistered ten-minute lag")

    identity = ChronosRuntimeIdentity.from_admission(
        admission_path.resolve(),
        qualification_evidence_path=qualification_evidence_path.resolve(),
        repository_root=repository_root,
    )
    run_root = run_root.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    lock = (run_root / "candidate.lock").open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock.close()
        raise RuntimeError("another canary Chronos worker owns this root") from exc

    manifest_path = run_root / "manifest.json"
    expected_manifest = {
        "schema": RUN_SCHEMA,
        "canary_id": prereg.canary_id,
        "repository_commit": prereg.repository_commit,
        "source_root": str(source_root),
        "source_manifest_sha256": sha256_file(source_root / "manifest.json"),
        "source_snapshot_hash": source_manifest.get("source_snapshot_hash"),
        "preregistration_sha256": preregistration_sha256,
        "phase3_gate_record_sha256": phase3_gate_sha256,
        "chronos_worker_code_sha256": prereg.chronos_worker_code_sha256,
        "chronos_runner_sha256": prereg.chronos_runner_sha256,
        "model_identity_hash": identity.model_identity_hash,
        "checkpoint_hash": identity.checkpoint_hash,
        "runner_hash": identity.runner_hash,
        "preprocessing_identity": CHRONOS_PREPROCESSING_IDENTITY,
        "preprocessing_hash": identity.preprocessing_hash,
        "dependency_lock_hash": identity.lock_hash,
        "runtime_environment_hash": identity.environment_fingerprint,
        "context_bars": 48,
        "context_newest_lag_seconds": CANARY_CONTEXT_LAG_SECONDS,
        "horizon_bars": CHRONOS_HORIZON_BARS,
        "target_end_at": prereg.target_end_at.isoformat(),
        "evidence_class": CANARY_EVIDENCE_CLASS,
        "admission_eligible": False,
        "phase4_materialization_eligible": False,
        "credentials_loaded": False,
        "order_writes_attempted": False,
    }
    if manifest_path.exists():
        manifest = _load_json(manifest_path)
        if any(manifest.get(key) != value for key, value in expected_manifest.items()):
            raise RuntimeError("existing canary Chronos root does not match its frozen identity")
    else:
        if any(path.name != "candidate.lock" for path in run_root.iterdir()):
            raise RuntimeError("canary Chronos root is non-empty without a frozen manifest")
        _write_atomic(
            manifest_path, {**expected_manifest, "started_at": datetime.now(UTC).isoformat()}
        )

    ledger = CanaryPredictionLedger(run_root / "predictions.jsonl")
    rejections = CanaryRejectionLedger(run_root / "rejections.jsonl")
    if rejections.records:
        raise RuntimeError("canary Chronos cannot resume after a prior mandatory cutoff failure")
    stop = False
    state = "running"
    failure_reason: str | None = None

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    prior_term = signal.getsignal(signal.SIGTERM)
    prior_int = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        while not stop and datetime.now(UTC) < prereg.target_end_at:
            now = datetime.now(UTC)
            bars = ForwardNormalizedBarSpool(source_root / "normalized-bars.jsonl").read()
            source_status_path = source_root / "status.json"
            if source_status_path.is_file():
                source_status = _load_json(source_status_path)
                if source_status.get("state") == "CANARY_FAILED":
                    state = "CANARY_FAILED"
                    failure_reason = "SOURCE_CANARY_FAILED"
                    break
            for symbol in V3_CORE_SYMBOLS:
                if state == "CANARY_FAILED":
                    break
                completed_for_symbol = sum(
                    record.prediction.instrument == symbol for record in ledger.records
                )
                for cutoff in _candidate_cutoffs(
                    bars, symbol=symbol, context_lag_seconds=CANARY_CONTEXT_LAG_SECONDS
                ):
                    existing = ledger.for_cutoff(symbol, cutoff)
                    if (
                        existing is None
                        and completed_for_symbol >= prereg.maximum_cutoffs_per_symbol
                    ):
                        continue
                    context = context_for_cutoff(
                        bars,
                        instrument=symbol,
                        cutoff=cutoff,
                        now=min(now, cutoff),
                        newest_context_lag_seconds=CANARY_CONTEXT_LAG_SECONDS,
                    )
                    if existing is not None:
                        if context is None:
                            state = "CANARY_FAILED"
                            failure_reason = "EXISTING_PREDICTION_CONTEXT_UNAVAILABLE"
                            break
                        _validate_existing(
                            existing.prediction,
                            identity=identity,
                            context=context,
                            instrument=symbol,
                            cutoff=cutoff,
                        )
                        continue
                    if context is None:
                        if now >= cutoff:
                            rejections.append(
                                instrument=symbol,
                                cutoff=cutoff,
                                reason="MISSING_MANDATORY_48_BAR_CONTEXT",
                            )
                            state = "CANARY_FAILED"
                            failure_reason = "MISSING_MANDATORY_48_BAR_CONTEXT"
                            break
                        continue
                    if now > cutoff:
                        rejections.append(
                            instrument=symbol,
                            cutoff=cutoff,
                            reason="MISSED_MANDATORY_CUTOFF",
                        )
                        state = "CANARY_FAILED"
                        failure_reason = "MISSED_MANDATORY_CUTOFF"
                        break
                    try:
                        inference_started_at = datetime.now(UTC)
                        inference = infer_chronos(
                            identity=identity,
                            context=context,
                            timeout_seconds=worker_timeout_seconds,
                        )
                        inference_finished_at = datetime.now(UTC)
                        if inference_finished_at > cutoff:
                            rejections.append(
                                instrument=symbol,
                                cutoff=cutoff,
                                reason="INFERENCE_COMPLETED_AFTER_CUTOFF",
                            )
                            state = "CANARY_FAILED"
                            failure_reason = "INFERENCE_COMPLETED_AFTER_CUTOFF"
                            break
                        prediction = build_chronos_prediction(
                            identity=identity,
                            instrument=symbol,
                            cutoff=cutoff,
                            generated_at=inference_finished_at,
                            context=context,
                            result=inference,
                            inference_started_at=inference_started_at,
                            inference_finished_at=inference_finished_at,
                            ledger_persisted_at=datetime.now(UTC),
                        )
                        ledger.append(_canary_prediction(identity=identity, prediction=prediction))
                        completed_for_symbol += 1
                    except ChronosInferenceFailure as exc:
                        rejections.append(
                            instrument=symbol,
                            cutoff=cutoff,
                            reason=f"INFERENCE_{exc.error_class.upper()}",
                        )
                        state = "CANARY_FAILED"
                        failure_reason = f"INFERENCE_{exc.error_class.upper()}"
                        break
                    except (RuntimeError, ValueError) as exc:
                        rejections.append(
                            instrument=symbol,
                            cutoff=cutoff,
                            reason="OUTPUT_OR_LEDGER_CONTRACT_FAILURE",
                        )
                        state = "CANARY_FAILED"
                        failure_reason = f"OUTPUT_OR_LEDGER_CONTRACT_FAILURE:{type(exc).__name__}"
                        break
            _write_atomic(
                run_root / "status.json",
                _status(
                    state=state,
                    ledger=ledger,
                    rejections=rejections,
                    identity=identity,
                    target_end_at=prereg.target_end_at,
                    failure_reason=failure_reason,
                ),
            )
            if state == "CANARY_FAILED":
                break
            time.sleep(poll_seconds)
        if stop and state == "running":
            state = "CANARY_FAILED"
            failure_reason = "STOP_REQUESTED"
        if state == "running":
            counts = {
                symbol: sum(record.prediction.instrument == symbol for record in ledger.records)
                for symbol in V3_CORE_SYMBOLS
            }
            if any(
                counts[symbol] < prereg.minimum_cutoffs_per_symbol for symbol in V3_CORE_SYMBOLS
            ):
                state = "CANARY_FAILED"
                failure_reason = "MINIMUM_PROSPECTIVE_COVERAGE_NOT_REACHED"
            else:
                state = "deadline_reached"
    finally:
        signal.signal(signal.SIGTERM, prior_term)
        signal.signal(signal.SIGINT, prior_int)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    result = _status(
        state=state,
        ledger=ledger,
        rejections=rejections,
        identity=identity,
        target_end_at=prereg.target_end_at,
        failure_reason=failure_reason,
    )
    _write_atomic(run_root / "status.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--qualification-evidence", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--preregistration-sha256", required=True)
    parser.add_argument("--phase3-gate-sha256", required=True)
    parser.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    parser.add_argument("--worker-timeout-seconds", type=float, default=WORKER_TIMEOUT_SECONDS)
    args = parser.parse_args()
    try:
        result = run(
            admission_path=args.admission,
            qualification_evidence_path=args.qualification_evidence,
            source_root=args.source_root,
            run_root=args.run_root,
            repository_root=args.repository_root.resolve(),
            preregistration=args.preregistration,
            preregistration_sha256=args.preregistration_sha256,
            phase3_gate_sha256=args.phase3_gate_sha256,
            poll_seconds=args.poll_seconds,
            worker_timeout_seconds=args.worker_timeout_seconds,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"prospective canary Chronos run refused ({type(exc).__name__})") from exc
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["state"] == "deadline_reached" else 1


if __name__ == "__main__":
    raise SystemExit(main())
