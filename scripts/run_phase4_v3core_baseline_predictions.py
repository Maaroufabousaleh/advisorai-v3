#!/usr/bin/env python3
"""Generate pre-outcome V3-Core baseline predictions from a normalized spool.

The process is offline and read-only with respect to the acquisition root.  It
never opens a network client, loads credentials, reads a future outcome, or
submits an order.  A prediction is emitted only while its complete context is
available locally and before its cutoff; missed cutoffs are recorded rather
than backdated.  TTM-R2 and Chronos are reported as separate candidate states
and are not silently replaced by these baselines.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from advisorai.models.forecasting import (
    DriftForecaster,
    LinearForecaster,
    NaiveForecaster,
    SeasonalForecaster,
)
from advisorai.phase0.runtime_qualification import LightGBMBaseline, QualificationError
from advisorai.phase4 import (
    V3_CORE_BASELINES,
    V3_CORE_SYMBOLS,
    ForwardNormalizedBarSpool,
    ForwardPredictionLedger,
    ForwardPredictionRecord,
    ForwardRejectionSpool,
    V3CoreBar,
)

RUN_SCHEMA = "advisorai.phase4.v3-core-forward.baseline-predictions.v1"
MISSED_CUTOFF_SCHEMA = "advisorai.phase4.v3-core-forward.rejection.v1"
POLL_SECONDS = 5.0
INTERVAL = timedelta(minutes=5)
HORIZON_BARS = 12
CONTEXT_BARS = 48
MISSED_CUTOFF_REASONS = (
    "INSUFFICIENT_CONTEXT",
    "MISSING_BAR",
    "SOURCE_HEALTH_FAILURE",
    "WORKER_STARTED_TOO_LATE",
    "INFERENCE_RUNTIME_FAILURE",
    "SCHEDULER_DELAY",
)
RESUME_IDENTITY_FIELDS = (
    "schema",
    "source_root",
    "source_manifest_sha256",
    "source_snapshot_hash",
    "preregistration_sha256",
    "phase3_gate_record_sha256",
    "repository_commit",
    "forecasting_code_sha256",
    "lightgbm_code_sha256",
    "models",
    "model_identity_hashes",
    "missed_cutoff_schema",
    "context_bars",
    "horizon_bars",
)


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256(path.read_bytes())


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _source_snapshot_hash(manifest: dict[str, object]) -> str:
    value = manifest.get("source_snapshot_hash")
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError("source run manifest has no valid snapshot hash")
    return value


def _load_bars(source_root: Path) -> tuple[V3CoreBar, ...]:
    return ForwardNormalizedBarSpool(source_root / "normalized-bars.jsonl").read()


def _context_for_cutoff(
    bars: tuple[V3CoreBar, ...], *, symbol: str, cutoff: datetime, now: datetime
) -> tuple[V3CoreBar, ...] | None:
    if now > cutoff:
        return None
    by_end = {
        bar.interval_end: bar
        for bar in bars
        if bar.instrument == symbol
        and bar.collected_at <= cutoff
        and bar.provider_available_at <= cutoff
        and bar.evidence_class == "forward_pit_admission"
        and bar.provenance.source_health_state == "HEALTHY"
    }
    context_times = _context_times(cutoff)
    context = tuple(by_end.get(item) for item in context_times)
    if any(item is None for item in context):
        return None
    return tuple(item for item in context if item is not None)


def _context_times(cutoff: datetime) -> tuple[datetime, ...]:
    return tuple(cutoff - INTERVAL * (CONTEXT_BARS - index) for index in range(CONTEXT_BARS))


def _missed_cutoff_reason(
    bars: tuple[V3CoreBar, ...],
    *,
    symbol: str,
    cutoff: datetime,
    now: datetime,
    worker_started_at: datetime,
    inference_failed: bool = False,
) -> str:
    """Classify a cutoff once it can no longer receive a prospective record."""

    if inference_failed:
        return "INFERENCE_RUNTIME_FAILURE"
    all_by_end = {
        bar.interval_end: bar
        for bar in bars
        if bar.instrument == symbol and bar.collected_at <= cutoff
    }
    context_times = _context_times(cutoff)
    if len(all_by_end) < CONTEXT_BARS:
        return "INSUFFICIENT_CONTEXT"
    if any(item not in all_by_end for item in context_times):
        return "MISSING_BAR"
    context = tuple(all_by_end[item] for item in context_times)
    if any(
        bar.provider_available_at > cutoff
        or bar.evidence_class != "forward_pit_admission"
        or bar.provenance.source_health_state != "HEALTHY"
        for bar in context
    ):
        return "SOURCE_HEALTH_FAILURE"
    if worker_started_at > cutoff:
        return "WORKER_STARTED_TOO_LATE"
    if now > cutoff:
        return "SCHEDULER_DELAY"
    return "INSUFFICIENT_CONTEXT"


def _missed_cutoff_summary(rejections: ForwardRejectionSpool) -> dict[str, int]:
    counts = Counter(record.reason for record in rejections.records)
    unexpected = set(counts).difference(MISSED_CUTOFF_REASONS)
    if unexpected:
        raise RuntimeError("unknown missed-cutoff reason: " + ", ".join(sorted(unexpected)))
    return dict(sorted(counts.items()))


def _input_snapshot_hash(context: tuple[V3CoreBar, ...], cutoff: datetime) -> str:
    return _sha256(
        _canonical(
            {
                "schema": "advisorai.phase4.v3-core-forward.prediction-input.v1",
                "cutoff": cutoff.isoformat(),
                "context": [bar.model_dump(mode="json") for bar in context],
            }
        )
    )


def _identity_hash(
    *,
    model: str,
    repository_root: Path,
    forecasting_hash: str,
    lightgbm_hash: str,
) -> str:
    return _sha256(
        _canonical(
            {
                "schema": "advisorai.phase4.v3-core-forward.baseline-identity.v1",
                "model": model,
                "horizon_bars": HORIZON_BARS,
                "context_bars": CONTEXT_BARS,
                "forecasting_code_sha256": forecasting_hash,
                "lightgbm_code_sha256": lightgbm_hash,
                "repository_root": str(repository_root.resolve()),
            }
        )
    )


def _expected_manifest(
    *,
    source_root: Path,
    source_manifest: dict[str, object],
    repository_root: Path,
    preregistration_sha256: str,
    phase3_gate_sha256: str,
) -> dict[str, object]:
    """Return every immutable identity field required for a future resume."""

    forecasting_path = repository_root / "src/advisorai/models/forecasting.py"
    lightgbm_path = repository_root / "src/advisorai/phase0/runtime_qualification.py"
    forecasting_hash = _sha256_file(forecasting_path)
    lightgbm_hash = _sha256_file(lightgbm_path)
    return {
        "schema": RUN_SCHEMA,
        "source_root": str(source_root),
        "source_manifest_sha256": _sha256_file(source_root / "manifest.json"),
        "source_snapshot_hash": _source_snapshot_hash(source_manifest),
        "preregistration_sha256": preregistration_sha256,
        "phase3_gate_record_sha256": phase3_gate_sha256,
        "repository_commit": _git_head(repository_root),
        "forecasting_code_sha256": forecasting_hash,
        "lightgbm_code_sha256": lightgbm_hash,
        "models": list(V3_CORE_BASELINES),
        "model_identity_hashes": {
            model: _identity_hash(
                model=model,
                repository_root=repository_root,
                forecasting_hash=forecasting_hash,
                lightgbm_hash=lightgbm_hash,
            )
            for model in V3_CORE_BASELINES
        },
        "missed_cutoff_schema": MISSED_CUTOFF_SCHEMA,
        "context_bars": CONTEXT_BARS,
        "horizon_bars": HORIZON_BARS,
    }


def _validate_resume_manifest(manifest: dict[str, object], expected: dict[str, object]) -> None:
    mismatches = [
        field for field in RESUME_IDENTITY_FIELDS if manifest.get(field) != expected.get(field)
    ]
    if mismatches:
        raise ValueError("baseline prediction resume identity mismatch: " + ", ".join(mismatches))


def _predict_prices(model: str, values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    if model == "naive":
        forecaster = NaiveForecaster()
    elif model == "drift":
        forecaster = DriftForecaster()
    elif model == "seasonal-7":
        forecaster = SeasonalForecaster(7)
    elif model == "linear":
        forecaster = LinearForecaster()
    elif model == "lightgbm":
        forecaster = LightGBMBaseline()
    else:
        raise ValueError(f"unsupported baseline: {model}")
    try:
        return tuple(Decimal(str(value)) for value in forecaster.predict(values, HORIZON_BARS))
    except QualificationError:
        raise


def _prediction_id(*, symbol: str, cutoff: datetime, model: str) -> str:
    return f"{symbol}:{cutoff.isoformat()}:{model}"


def _pending_baselines(
    ledger: ForwardPredictionLedger, *, symbol: str, cutoff: datetime
) -> tuple[str, ...]:
    """Return only identities not already frozen in the append-only ledger."""

    return tuple(
        model
        for model in V3_CORE_BASELINES
        if _prediction_id(symbol=symbol, cutoff=cutoff, model=model) not in ledger.prediction_ids
    )


def _validate_existing_baseline_prediction(
    ledger: ForwardPredictionLedger,
    *,
    symbol: str,
    cutoff: datetime,
    model: str,
    context: tuple[V3CoreBar, ...],
    expected_model_identity_hash: str,
    expected_source_snapshot_hash: str,
) -> None:
    """Validate a frozen duplicate before allowing resume to skip inference.

    Runtime metadata is intentionally excluded: a restart must not require the
    old wall-clock or measured latency to be recreated.  Scientific identity is
    not excluded: a ledger entry for the same deterministic ID must still bind
    to the same instrument, cutoff, model identity, source snapshot, and input
    context.  Any mismatch fails closed without rerunning the model.
    """

    prediction_id = _prediction_id(symbol=symbol, cutoff=cutoff, model=model)
    existing = next(
        (
            entry.prediction
            for entry in ledger.records
            if entry.prediction.prediction_id == prediction_id
        ),
        None,
    )
    if existing is None:
        raise RuntimeError("baseline prediction identity disappeared during resume")
    expected = {
        "instrument": symbol,
        "model": model,
        "model_identity_hash": expected_model_identity_hash,
        "cutoff": cutoff,
        "input_snapshot_hash": _input_snapshot_hash(context, cutoff),
        "source_snapshot_hash": expected_source_snapshot_hash,
    }
    actual = {field: getattr(existing, field) for field in expected}
    if actual != expected:
        raise RuntimeError("existing baseline prediction has conflicting scientific identity")


def _prediction(
    *,
    model: str,
    symbol: str,
    cutoff: datetime,
    generated_at: datetime,
    context: tuple[V3CoreBar, ...],
    repository_root: Path,
    forecasting_hash: str,
    lightgbm_hash: str,
    source_snapshot_hash: str,
) -> ForwardPredictionRecord:
    started = time.perf_counter()
    values = tuple(bar.close for bar in context)
    prices = _predict_prices(model, values)
    last_close = values[-1]
    predicted_return_bps = (prices[-1] / last_close - Decimal("1")) * Decimal("10000")
    return ForwardPredictionRecord(
        prediction_id=_prediction_id(symbol=symbol, cutoff=cutoff, model=model),
        instrument=symbol,
        model=model,
        model_identity_hash=_identity_hash(
            model=model,
            repository_root=repository_root,
            forecasting_hash=forecasting_hash,
            lightgbm_hash=lightgbm_hash,
        ),
        cutoff=cutoff,
        input_snapshot_hash=_input_snapshot_hash(context, cutoff),
        source_snapshot_hash=source_snapshot_hash,
        predicted_return_bps=predicted_return_bps,
        generated_at=generated_at,
        runtime_latency_ms=Decimal(str((time.perf_counter() - started) * 1000)),
    )


def _write_atomic(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(json.dumps(payload, sort_keys=True, indent=2).encode() + b"\n")
    os.replace(temporary, path)


def run(
    *,
    source_root: Path,
    run_root: Path,
    repository_root: Path,
    preregistration_sha256: str,
    phase3_gate_sha256: str,
    until: datetime,
    poll_seconds: float = POLL_SECONDS,
) -> dict[str, object]:
    source_root = source_root.resolve()
    run_root = run_root.resolve()
    source_manifest = json.loads((source_root / "manifest.json").read_text(encoding="utf-8"))
    if source_manifest.get("credentials_loaded") or source_manifest.get("order_writes_attempted"):
        raise ValueError("source root is not credential-free and order-write-free")
    if source_manifest.get("preregistration_sha256") != preregistration_sha256:
        raise ValueError("source root is bound to a different preregistration")
    if source_manifest.get("phase3_gate_record_sha256") != phase3_gate_sha256:
        raise ValueError("source root is bound to a different Phase-3 gate")
    run_root.mkdir(parents=True, exist_ok=True)
    manifest_path = run_root / "manifest.json"
    expected_manifest = _expected_manifest(
        source_root=source_root,
        source_manifest=source_manifest,
        repository_root=repository_root,
        preregistration_sha256=preregistration_sha256,
        phase3_gate_sha256=phase3_gate_sha256,
    )
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _validate_resume_manifest(manifest, expected_manifest)
    else:
        manifest = {
            **expected_manifest,
            "started_at": datetime.now(UTC).isoformat(),
            "candidate_models": {
                "ttm-r2": "not_generated; runtime prediction worker is a separate admitted boundary",
                "chronos-2-small": "quarantined; preserved worker/runtime identity mismatch",
            },
            "network_calls": 0,
            "credentials_loaded": False,
            "order_writes_attempted": False,
        }
        _write_atomic(manifest_path, manifest)
    try:
        worker_started_at = datetime.fromisoformat(
            str(manifest["started_at"]).replace("Z", "+00:00")
        ).astimezone(UTC)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("baseline prediction manifest has no valid started_at") from exc
    ledger = ForwardPredictionLedger(run_root / "predictions.jsonl")
    missed_path = run_root / "missed-cutoffs.jsonl"
    rejections = ForwardRejectionSpool(missed_path)
    missed = {(record.instrument, record.cutoff.isoformat()) for record in rejections.records}
    inference_failures: set[tuple[str, str]] = set()
    stop = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    prior_term = signal.getsignal(signal.SIGTERM)
    prior_int = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        while not stop and datetime.now(UTC) < until:
            now = datetime.now(UTC)
            bars = _load_bars(source_root)
            last_by_symbol = {
                symbol: max(
                    (bar.interval_end for bar in bars if bar.instrument == symbol),
                    default=None,
                )
                for symbol in V3_CORE_SYMBOLS
            }
            for symbol, last_end in last_by_symbol.items():
                if last_end is None:
                    continue
                cutoff = last_end + INTERVAL
                if cutoff.minute % 60 != 0 or cutoff.second or cutoff.microsecond:
                    continue
                identity = (symbol, cutoff.isoformat())
                if now > cutoff:
                    if identity not in missed:
                        rejections.append(
                            instrument=symbol,
                            cutoff=cutoff,
                            reason=_missed_cutoff_reason(
                                bars,
                                symbol=symbol,
                                cutoff=cutoff,
                                now=now,
                                worker_started_at=worker_started_at,
                                inference_failed=identity in inference_failures,
                            ),
                        )
                        missed.add(identity)
                    continue
                context = _context_for_cutoff(bars, symbol=symbol, cutoff=cutoff, now=now)
                if context is None:
                    continue
                pending_models = _pending_baselines(ledger, symbol=symbol, cutoff=cutoff)
                for model in V3_CORE_BASELINES:
                    if model not in pending_models:
                        _validate_existing_baseline_prediction(
                            ledger,
                            symbol=symbol,
                            cutoff=cutoff,
                            model=model,
                            context=context,
                            expected_model_identity_hash=manifest["model_identity_hashes"][model],
                            expected_source_snapshot_hash=manifest["source_snapshot_hash"],
                        )
                failed_models = 0
                for model in pending_models:
                    try:
                        ledger.append(
                            _prediction(
                                model=model,
                                symbol=symbol,
                                cutoff=cutoff,
                                generated_at=now,
                                context=context,
                                repository_root=repository_root,
                                forecasting_hash=manifest["forecasting_code_sha256"],
                                lightgbm_hash=manifest["lightgbm_code_sha256"],
                                source_snapshot_hash=manifest["source_snapshot_hash"],
                            )
                        )
                    except QualificationError:
                        # Preserve the absence as a sanitized class; no model is
                        # replaced by another baseline.
                        failed_models += 1
                if pending_models and failed_models == len(pending_models):
                    inference_failures.add(identity)
                else:
                    inference_failures.discard(identity)
            _write_atomic(
                run_root / "status.json",
                {
                    "schema": f"{RUN_SCHEMA}.status",
                    "state": "running",
                    "updated_at": datetime.now(UTC).isoformat(),
                    "prediction_count": len(ledger.records),
                    "missed_cutoff_count": len(missed),
                    "missed_cutoff_reasons": _missed_cutoff_summary(rejections),
                    "models": list(V3_CORE_BASELINES),
                    "candidate_models": manifest["candidate_models"],
                    "network_calls": 0,
                    "credentials_loaded": False,
                    "order_writes_attempted": False,
                },
            )
            time.sleep(poll_seconds)
    finally:
        signal.signal(signal.SIGTERM, prior_term)
        signal.signal(signal.SIGINT, prior_int)
    status = {
        "schema": f"{RUN_SCHEMA}.status",
        "state": "stopped_with_evidence" if stop else "deadline_reached",
        "updated_at": datetime.now(UTC).isoformat(),
        "prediction_count": len(ledger.records),
        "missed_cutoff_count": len(missed),
        "missed_cutoff_reasons": _missed_cutoff_summary(rejections),
        "network_calls": 0,
        "credentials_loaded": False,
        "order_writes_attempted": False,
    }
    _write_atomic(run_root / "status.json", status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--preregistration-sha256", required=True)
    parser.add_argument("--phase3-gate-sha256", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    args = parser.parse_args()
    until = datetime.fromisoformat(args.until.replace("Z", "+00:00")).astimezone(UTC)
    if args.poll_seconds <= 0:
        raise SystemExit("--poll-seconds must be positive")
    try:
        result = run(
            source_root=args.source_root,
            run_root=args.run_root,
            repository_root=args.repository_root.resolve(),
            preregistration_sha256=args.preregistration_sha256,
            phase3_gate_sha256=args.phase3_gate_sha256,
            until=until,
            poll_seconds=args.poll_seconds,
        )
    except (OSError, KeyError, TypeError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"forward baseline prediction run refused ({type(exc).__name__})") from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
