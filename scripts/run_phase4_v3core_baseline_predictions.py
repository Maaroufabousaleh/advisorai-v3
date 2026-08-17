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
    V3CoreBar,
)

RUN_SCHEMA = "advisorai.phase4.v3-core-forward.baseline-predictions.v1"
POLL_SECONDS = 5.0
INTERVAL = timedelta(minutes=5)
HORIZON_BARS = 12
CONTEXT_BARS = 48
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
        if bar.instrument == symbol and bar.collected_at <= cutoff
    }
    context_times = tuple(
        cutoff - INTERVAL * (CONTEXT_BARS - index) for index in range(CONTEXT_BARS)
    )
    context = tuple(by_end.get(item) for item in context_times)
    if any(item is None for item in context):
        return None
    return tuple(item for item in context if item is not None)


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
    return {
        "schema": RUN_SCHEMA,
        "source_root": str(source_root),
        "source_manifest_sha256": _sha256_file(source_root / "manifest.json"),
        "source_snapshot_hash": _source_snapshot_hash(source_manifest),
        "preregistration_sha256": preregistration_sha256,
        "phase3_gate_record_sha256": phase3_gate_sha256,
        "repository_commit": _git_head(repository_root),
        "forecasting_code_sha256": _sha256_file(forecasting_path),
        "lightgbm_code_sha256": _sha256_file(lightgbm_path),
        "models": list(V3_CORE_BASELINES),
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
) -> ForwardPredictionRecord:
    started = time.perf_counter()
    values = tuple(bar.close for bar in context)
    prices = _predict_prices(model, values)
    last_close = values[-1]
    predicted_return_bps = (prices[-1] / last_close - Decimal("1")) * Decimal("10000")
    return ForwardPredictionRecord(
        prediction_id=f"{symbol}:{cutoff.isoformat()}:{model}",
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
    ledger = ForwardPredictionLedger(run_root / "predictions.jsonl")
    missed_path = run_root / "missed-cutoffs.jsonl"
    missed: set[tuple[str, str]] = set()
    if missed_path.exists():
        for line in missed_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                missed.add((str(value["symbol"]), str(value["cutoff"])))
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
                        with missed_path.open("a", encoding="utf-8") as handle:
                            handle.write(
                                json.dumps({"symbol": symbol, "cutoff": cutoff.isoformat()}) + "\n"
                            )
                            handle.flush()
                            os.fsync(handle.fileno())
                        missed.add(identity)
                    continue
                context = _context_for_cutoff(bars, symbol=symbol, cutoff=cutoff, now=now)
                if context is None:
                    continue
                for model in V3_CORE_BASELINES:
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
                            )
                        )
                    except QualificationError:
                        # Preserve the absence as a sanitized class; no model is
                        # replaced by another baseline.
                        continue
            _write_atomic(
                run_root / "status.json",
                {
                    "schema": f"{RUN_SCHEMA}.status",
                    "state": "running",
                    "updated_at": datetime.now(UTC).isoformat(),
                    "prediction_count": len(ledger.records),
                    "missed_cutoff_count": len(missed),
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
