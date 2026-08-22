#!/usr/bin/env python3
"""Collect the credential-free, delayed-finality V3-Core canary source.

This collector is intentionally separate from the historical forward
collector.  It appends every public raw receipt first and only appends a
canonical bar after the frozen canary finality rule has been satisfied.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

from advisorai.integrations.http import HttpClientConfig, HttpTransportError, SafeHttpClient
from advisorai.phase4 import (
    CANARY_EVIDENCE_CLASS,
    V3_CORE_MARKET_DATA_PROVIDER,
    V3_CORE_MARKET_DATA_REST_BASE,
    V3_CORE_MARKET_DATA_REST_ENDPOINT,
    V3_CORE_SYMBOLS,
    CanaryFinalityTracker,
    CanaryFinalityViolation,
    ForwardFailureSpool,
    ForwardHealthLedger,
    ForwardNormalizedBarSpool,
    ForwardRawSpool,
    load_canary_preregistration,
    parse_binance_klines,
    sha256_file,
    source_snapshot_hash,
)

RUN_SCHEMA = "advisorai.phase4.v3-core.prospective-canary.source.v1"
DEFAULT_POLL_SECONDS = 30.0


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


def _request_url(symbol: str) -> str:
    query = urlencode({"interval": "5m", "limit": 2, "symbol": symbol})
    return f"{V3_CORE_MARKET_DATA_REST_ENDPOINT}?{query}"


def _failure_class(exc: Exception) -> tuple[str, int | None, bool]:
    if isinstance(exc, HttpTransportError):
        if exc.status_code is not None:
            return f"http_{exc.status_code}", exc.status_code, exc.retriable
        return exc.error_type or "transport", None, exc.retriable
    if isinstance(exc, (TimeoutError, OSError)):
        return type(exc).__name__.lower(), None, True
    return "schema_or_normalization", None, False


def _latest_admitted(normalized: ForwardNormalizedBarSpool, symbol: str) -> datetime | None:
    return max(
        (bar.interval_end for bar in normalized.bars.values() if bar.instrument == symbol),
        default=None,
    )


def _summary(
    *,
    run_directory: Path,
    preregistration_sha256: str,
    phase3_gate_sha256: str,
    source_snapshot: str,
    started_at: datetime,
    target_end_at: datetime,
    state: str,
    raw: ForwardRawSpool,
    normalized: ForwardNormalizedBarSpool,
    failures: ForwardFailureSpool,
    health: ForwardHealthLedger,
    tracker: CanaryFinalityTracker,
    client: SafeHttpClient,
    reason: str | None = None,
) -> dict[str, object]:
    metrics = tracker.metrics()
    result: dict[str, object] = {
        "schema": RUN_SCHEMA,
        "run_directory": str(run_directory.resolve()),
        "state": state,
        "started_at": started_at.isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "target_end_at": target_end_at.isoformat(),
        "provider_identity": V3_CORE_MARKET_DATA_PROVIDER,
        "endpoint": V3_CORE_MARKET_DATA_REST_ENDPOINT,
        "symbols": list(V3_CORE_SYMBOLS),
        "interval": "5m",
        "evidence_class": CANARY_EVIDENCE_CLASS,
        "admission_eligible": False,
        "phase4_materialization_eligible": False,
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "raw_response_count": len(raw.records),
        "admitted_final_bar_count": len(normalized.bars),
        "failure_count": len(failures.records),
        "health_transition_count": len(health.records),
        "network_call_count": client.request_count,
        "raw_last_record_hash": raw.last_record_hash,
        "source_snapshot_hash": source_snapshot,
        "preregistration_sha256": preregistration_sha256,
        "phase3_gate_record_sha256": phase3_gate_sha256,
        "latest_admitted_interval_end": {
            symbol: (
                value.isoformat()
                if (value := _latest_admitted(normalized, symbol)) is not None
                else None
            )
            for symbol in V3_CORE_SYMBOLS
        },
        "finality": metrics,
    }
    if reason is not None:
        result["failure_reason"] = reason
    return result


def run_collection(
    *,
    run_directory: Path,
    repository_root: Path,
    preregistration: Path,
    phase3_gate_sha256: str,
    poll_seconds: float,
    real: bool,
) -> dict[str, object]:
    if not real:
        raise ValueError("canary collection requires explicit --real opt-in")
    if poll_seconds <= 0:
        raise ValueError("poll interval must be positive")
    preregistration = preregistration.resolve()
    prereg = load_canary_preregistration(preregistration)
    preregistration_sha256 = sha256_file(preregistration)
    if prereg.phase3_gate_sha256 != phase3_gate_sha256:
        raise ValueError("canary preregistration and Phase-3 gate identity differ")
    repository_root = repository_root.resolve()
    if _git_commit(repository_root) != prereg.repository_commit:
        raise ValueError("canary repository commit does not match preregistration")
    if sha256_file(Path(__file__).resolve()) != prereg.collector_code_sha256:
        raise ValueError("canary collector code does not match preregistration")
    finality_path = repository_root / "src/advisorai/phase4/v3core_canary.py"
    if sha256_file(finality_path) != prereg.finality_code_sha256:
        raise ValueError("canary finality code does not match preregistration")

    run_directory = run_directory.resolve()
    run_directory.mkdir(parents=True, exist_ok=True)
    lock_path = run_directory / "collector.lock"
    lock = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock.close()
        raise RuntimeError("another canary collector owns this evidence root") from exc

    source_snapshot = source_snapshot_hash(
        preregistration_sha256=preregistration_sha256,
        phase3_gate_record_sha256=phase3_gate_sha256,
    )
    code_commit = _git_commit(repository_root)
    manifest_path = run_directory / "manifest.json"
    code_files = {
        "collector_code_sha256": sha256_file(Path(__file__).resolve()),
        "finality_code_sha256": sha256_file(finality_path),
        "forward_module_sha256": sha256_file(
            repository_root / "src/advisorai/phase4/v3core_forward.py"
        ),
    }
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = {
            "schema": RUN_SCHEMA,
            "canary_id": prereg.canary_id,
            "repository_commit": code_commit,
            "target_end_at": prereg.target_end_at.isoformat(),
            "poll_seconds": poll_seconds,
            "preregistration_sha256": preregistration_sha256,
            "phase3_gate_record_sha256": phase3_gate_sha256,
            "source_snapshot_hash": source_snapshot,
            "credentials_loaded": False,
            "order_writes_attempted": False,
            **code_files,
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise RuntimeError(
                "existing canary source root does not match its frozen configuration"
            )
        started_at = datetime.fromisoformat(str(manifest["started_at"])).astimezone(UTC)
    else:
        if any(path.name != "collector.lock" for path in run_directory.iterdir()):
            raise RuntimeError("canary source root is non-empty without a frozen manifest")
        started_at = datetime.now(UTC)
        manifest = {
            "schema": RUN_SCHEMA,
            "canary_id": prereg.canary_id,
            "started_at": started_at.isoformat(),
            "target_end_at": prereg.target_end_at.isoformat(),
            "poll_seconds": poll_seconds,
            "provider_identity": V3_CORE_MARKET_DATA_PROVIDER,
            "endpoint": V3_CORE_MARKET_DATA_REST_ENDPOINT,
            "symbols": list(V3_CORE_SYMBOLS),
            "interval": "5m",
            "evidence_class": CANARY_EVIDENCE_CLASS,
            "admission_eligible": False,
            "phase4_materialization_eligible": False,
            "preregistration_path": str(preregistration),
            "preregistration_sha256": preregistration_sha256,
            "phase3_gate_record_sha256": phase3_gate_sha256,
            "source_snapshot_hash": source_snapshot,
            "repository_commit": code_commit,
            "credentials_loaded": False,
            "order_writes_attempted": False,
            "market_data_only": True,
            "execution_capability": False,
            "finality_guard_seconds": prereg.finality_guard_seconds,
            "repeat_receipts": prereg.repeat_requirement,
            "distinct_receipts_required": prereg.distinct_receipts_required,
            "context_newest_lag_seconds": prereg.context_newest_lag_seconds,
            **code_files,
        }
        _write_atomic(manifest_path, manifest)

    raw = ForwardRawSpool(run_directory / "raw-responses.jsonl")
    normalized = ForwardNormalizedBarSpool(run_directory / "normalized-bars.jsonl")
    tracker = CanaryFinalityTracker(
        normalized,
        run_directory / "post-admission-revisions.jsonl",
        guard_seconds=prereg.finality_guard_seconds,
        repeat_receipts=prereg.repeat_requirement,
    )
    tracker.replay(raw.read(), source_snapshot)
    failures = ForwardFailureSpool(run_directory / "failures.jsonl")
    health = ForwardHealthLedger(run_directory / "source-health.jsonl")
    client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=("data-api.binance.vision",),
            timeout_seconds=20,
            max_retries=1,
            requests_per_second=1,
            user_agent="advisorai-v3/phase4-prospective-canary-market-data",
        ),
        base_url=V3_CORE_MARKET_DATA_REST_BASE,
    )
    stop_requested = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    prior_sigterm = signal.getsignal(signal.SIGTERM)
    prior_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    state = "running"
    failure_reason: str | None = None
    try:
        while not stop_requested:
            if datetime.now(UTC) >= prereg.target_end_at:
                state = "deadline_reached"
                break
            for symbol in V3_CORE_SYMBOLS:
                if stop_requested:
                    break
                request_url = _request_url(symbol)
                try:
                    response = client.get(request_url, max_retries=1, timeout_seconds=20)
                    raw_record = raw.append(response, symbol=symbol, request_url=request_url)
                    bars = parse_binance_klines(
                        response.body,
                        symbol=symbol,
                        collected_at=response.fetched_at,
                        source_snapshot_hash=source_snapshot,
                    )
                    tracker.observe(raw_record, bars)
                    health.append(
                        symbol=symbol,
                        observed_at=response.fetched_at,
                        to_state="HEALTHY",
                        reason=("admitted_final_bar" if bars else "valid_response_no_closed_bar"),
                        last_valid_interval_end=_latest_admitted(normalized, symbol),
                        last_collected_at=response.fetched_at,
                    )
                except CanaryFinalityViolation as exc:
                    state = "CANARY_FAILED"
                    failure_reason = str(exc)
                    break
                except Exception as exc:  # record class/status only; never provider bodies
                    failure_class, status_code, retriable = _failure_class(exc)
                    observed_at = datetime.now(UTC)
                    failures.append(
                        symbol=symbol,
                        observed_at=observed_at,
                        failure_class=failure_class,
                        status_code=status_code,
                        retriable=retriable,
                    )
                    health.append(
                        symbol=symbol,
                        observed_at=observed_at,
                        to_state=("DISCONNECTED" if retriable else "DEGRADED"),
                        reason=failure_class,
                        last_valid_interval_end=_latest_admitted(normalized, symbol),
                    )
            if state == "CANARY_FAILED":
                break
            summary = _summary(
                run_directory=run_directory,
                preregistration_sha256=preregistration_sha256,
                phase3_gate_sha256=phase3_gate_sha256,
                source_snapshot=source_snapshot,
                started_at=started_at,
                target_end_at=prereg.target_end_at,
                state=state,
                raw=raw,
                normalized=normalized,
                failures=failures,
                health=health,
                tracker=tracker,
                client=client,
            )
            _write_atomic(run_directory / "status.json", {**summary, "pid": os.getpid()})
            _write_atomic(
                run_directory / "heartbeat.json",
                {**summary, "heartbeat_at": datetime.now(UTC).isoformat()},
            )
            remaining = (prereg.target_end_at - datetime.now(UTC)).total_seconds()
            if remaining > 0:
                time.sleep(min(poll_seconds, remaining))
        if stop_requested and state == "running":
            state = "CANARY_FAILED"
            failure_reason = "STOP_REQUESTED"
    finally:
        signal.signal(signal.SIGTERM, prior_sigterm)
        signal.signal(signal.SIGINT, prior_sigint)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    summary = _summary(
        run_directory=run_directory,
        preregistration_sha256=preregistration_sha256,
        phase3_gate_sha256=phase3_gate_sha256,
        source_snapshot=source_snapshot,
        started_at=started_at,
        target_end_at=prereg.target_end_at,
        state=state,
        raw=raw,
        normalized=normalized,
        failures=failures,
        health=health,
        tracker=tracker,
        client=client,
        reason=failure_reason,
    )
    _write_atomic(run_directory / "summary.json", summary)
    _write_atomic(run_directory / "status.json", {**summary, "pid": os.getpid()})
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", help="allow public read-only network calls")
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--phase3-gate-sha256", required=True)
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    args = parser.parse_args()
    try:
        summary = run_collection(
            run_directory=args.run_directory,
            repository_root=args.repository_root.resolve(),
            preregistration=args.preregistration,
            phase3_gate_sha256=args.phase3_gate_sha256,
            poll_seconds=args.poll_seconds,
            real=args.real,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"prospective canary source collection refused ({type(exc).__name__})"
        ) from exc
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0 if summary["state"] == "deadline_reached" else 1


if __name__ == "__main__":
    raise SystemExit(main())
