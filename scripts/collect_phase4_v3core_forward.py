#!/usr/bin/env python3
"""Collect forward, credential-free V3-Core Binance 5-minute PIT evidence.

The process is intentionally REST-only and market-data-only.  It never reads
``secrets.env`` and has no execution, account, user-data, transfer, or
withdrawal operation.  A raw response is fsync'd before its closed rows are
normalized.  The same run directory can be resumed only with the original
frozen configuration.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode

from advisorai.integrations.http import HttpClientConfig, HttpTransportError, SafeHttpClient
from advisorai.phase4 import (
    FORWARD_INTERVAL,
    V3_CORE_MARKET_DATA_PROVIDER,
    V3_CORE_MARKET_DATA_REST_BASE,
    V3_CORE_MARKET_DATA_REST_ENDPOINT,
    V3_CORE_SYMBOLS,
    ForwardCaseSpool,
    ForwardFailureSpool,
    ForwardHealthLedger,
    ForwardNormalizedBarSpool,
    ForwardRawSpool,
    ForwardRejectionSpool,
    build_forward_cases,
    parse_binance_klines,
    source_snapshot_hash,
)

RUN_SCHEMA = "advisorai.phase4.v3-core-forward.run.v1"
DEFAULT_POLL_SECONDS = 30.0
DEFAULT_MAX_DURATION_HOURS = 120.0
DEFAULT_TARGET_CASES_PER_SYMBOL = 64


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_atomic(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _git_commit(repository_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("cannot bind forward evidence to the repository commit") from exc
    commit = result.stdout.strip()
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise RuntimeError("repository commit identity is malformed")
    return commit


def _load_preregistration_hash(path: Path) -> str:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"preregistration artifact is missing: {resolved}")
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("measurement_status") != "PENDING_FRESH_PIT_DATA"
    ):
        raise ValueError("forward collection requires the frozen pending preregistration artifact")
    return _sha256_file(resolved)


def _request_url(symbol: str) -> str:
    query = urlencode({"interval": FORWARD_INTERVAL, "limit": 2, "symbol": symbol})
    return f"{V3_CORE_MARKET_DATA_REST_ENDPOINT}?{query}"


def _failure_class(exc: Exception) -> tuple[str, int | None, bool]:
    if isinstance(exc, HttpTransportError):
        if exc.status_code is not None:
            return f"http_{exc.status_code}", exc.status_code, exc.retriable
        return exc.error_type or "transport", None, exc.retriable
    if isinstance(exc, (TimeoutError, OSError)):
        return type(exc).__name__.lower(), None, True
    return "schema_or_normalization", None, False


def _summary(
    *,
    run_directory: Path,
    started_at: datetime,
    state: str,
    target_cases_per_symbol: int,
    target_end: datetime,
    raw: ForwardRawSpool,
    normalized: ForwardNormalizedBarSpool,
    cases: ForwardCaseSpool,
    failures: ForwardFailureSpool,
    health: ForwardHealthLedger,
    rejections: ForwardRejectionSpool,
    client: SafeHttpClient,
    source_snapshot: str,
    phase3_gate_sha256: str,
    preregistration_sha256: str,
) -> dict[str, object]:
    counts = cases.count_by_symbol()
    return {
        "schema": RUN_SCHEMA,
        "state": state,
        "run_directory": str(run_directory.resolve()),
        "started_at": started_at.isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "target_end_at": target_end.isoformat(),
        "target_cases_per_symbol": target_cases_per_symbol,
        "case_counts": counts,
        "minimum_reached": all(
            counts[symbol] >= target_cases_per_symbol for symbol in V3_CORE_SYMBOLS
        ),
        "provider_identity": V3_CORE_MARKET_DATA_PROVIDER,
        "endpoint": V3_CORE_MARKET_DATA_REST_ENDPOINT,
        "websocket_reviewed_endpoint": "wss://data-stream.binance.vision/ws",
        "symbols": list(V3_CORE_SYMBOLS),
        "interval": FORWARD_INTERVAL,
        "evidence_class": "forward_pit_admission",
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "raw_response_count": len(raw.records),
        "normalized_bar_count": len(normalized.bars),
        "completed_case_count": len(cases.cases),
        "failure_count": len(failures.records),
        "health_transition_count": len(health.records),
        "rejection_count": len(rejections.records),
        "network_call_count": client.request_count,
        "raw_last_record_hash": raw.last_record_hash,
        "source_snapshot_hash": source_snapshot,
        "preregistration_sha256": preregistration_sha256,
        "phase3_gate_record_sha256": phase3_gate_sha256,
    }


def _refresh_cases(
    *,
    normalized: ForwardNormalizedBarSpool,
    cases: ForwardCaseSpool,
    rejections: ForwardRejectionSpool,
    source_snapshot: str,
    phase3_gate_sha256: str,
) -> None:
    build = build_forward_cases(
        normalized.read(),
        source_snapshot_hash=source_snapshot,
        phase3_gate_record_sha256=phase3_gate_sha256,
    )
    for rejection in build.rejected_cutoffs:
        rejections.append(
            instrument=rejection.instrument,
            cutoff=rejection.cutoff,
            reason=rejection.reason,
        )
    for case in build.cases:
        cases.append(case)


def run_collection(
    *,
    run_directory: Path,
    repository_root: Path,
    preregistration: Path,
    phase3_gate_sha256: str,
    target_cases_per_symbol: int,
    max_duration_hours: float,
    poll_seconds: float,
    real: bool,
) -> dict[str, object]:
    if not real:
        raise ValueError("forward collection requires explicit --real opt-in")
    if target_cases_per_symbol < 1:
        raise ValueError("target cases per symbol must be positive")
    if max_duration_hours <= 0 or poll_seconds <= 0:
        raise ValueError("duration and polling interval must be positive")
    run_directory = run_directory.resolve()
    run_directory.mkdir(parents=True, exist_ok=True)
    lock_path = run_directory / "collector.lock"
    lock = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock.close()
        raise RuntimeError("another forward collector already owns this evidence root") from exc

    preregistration_sha256 = _load_preregistration_hash(preregistration)
    source_snapshot = source_snapshot_hash(
        preregistration_sha256=preregistration_sha256,
        phase3_gate_record_sha256=phase3_gate_sha256,
    )
    code_files = {
        "collector_script_sha256": _sha256_file(Path(__file__).resolve()),
        "forward_module_sha256": _sha256_file(
            repository_root / "src/advisorai/phase4/v3core_forward.py"
        ),
    }
    code_commit = _git_commit(repository_root)
    manifest_path = run_directory / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = {
            "preregistration_sha256": preregistration_sha256,
            "phase3_gate_record_sha256": phase3_gate_sha256,
            "source_snapshot_hash": source_snapshot,
            "target_cases_per_symbol": target_cases_per_symbol,
            "poll_seconds": poll_seconds,
            "provider_identity": V3_CORE_MARKET_DATA_PROVIDER,
            "endpoint": V3_CORE_MARKET_DATA_REST_ENDPOINT,
            "credentials_loaded": False,
            "order_writes_attempted": False,
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise RuntimeError(
                "existing forward evidence root does not match its frozen configuration"
            )
        started_at = datetime.fromisoformat(str(manifest["started_at"])).astimezone(UTC)
        target_end = datetime.fromisoformat(str(manifest["target_end_at"])).astimezone(UTC)
    else:
        started_at = datetime.now(UTC)
        target_end = started_at + timedelta(hours=max_duration_hours)
        manifest = {
            "schema": RUN_SCHEMA,
            "run_id": run_directory.name,
            "started_at": started_at.isoformat(),
            "target_end_at": target_end.isoformat(),
            "target_cases_per_symbol": target_cases_per_symbol,
            "poll_seconds": poll_seconds,
            "provider_identity": V3_CORE_MARKET_DATA_PROVIDER,
            "endpoint": V3_CORE_MARKET_DATA_REST_ENDPOINT,
            "websocket_reviewed_endpoint": "wss://data-stream.binance.vision/ws",
            "symbols": list(V3_CORE_SYMBOLS),
            "interval": FORWARD_INTERVAL,
            "evidence_class": "forward_pit_admission",
            "preregistration_path": str(preregistration.resolve()),
            "preregistration_sha256": preregistration_sha256,
            "phase3_gate_record_sha256": phase3_gate_sha256,
            "source_snapshot_hash": source_snapshot,
            "code_commit": code_commit,
            **code_files,
            "credentials_loaded": False,
            "order_writes_attempted": False,
            "market_data_only": True,
            "execution_venue": "binance_spot_testnet",
        }
        _write_atomic(manifest_path, manifest)

    raw = ForwardRawSpool(run_directory / "raw-responses.jsonl")
    normalized = ForwardNormalizedBarSpool(run_directory / "normalized-bars.jsonl")
    cases = ForwardCaseSpool(run_directory / "completed-cases.jsonl")
    failures = ForwardFailureSpool(run_directory / "failures.jsonl")
    health = ForwardHealthLedger(run_directory / "source-health.jsonl")
    rejections = ForwardRejectionSpool(run_directory / "case-rejections.jsonl")
    client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=("data-api.binance.vision",),
            timeout_seconds=20,
            max_retries=1,
            requests_per_second=1,
            user_agent="advisorai-v3/phase4-forward-pit-market-data",
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
    try:
        while not stop_requested:
            now = datetime.now(UTC)
            if now >= target_end:
                state = "deadline_reached"
                break
            for symbol in V3_CORE_SYMBOLS:
                if stop_requested:
                    break
                request_url = _request_url(symbol)
                try:
                    response = client.get(request_url, max_retries=1, timeout_seconds=20)
                    raw.append(response, symbol=symbol, request_url=request_url)
                    bars = parse_binance_klines(
                        response.body,
                        symbol=symbol,
                        collected_at=response.fetched_at,
                        source_snapshot_hash=source_snapshot,
                    )
                    for bar in bars:
                        normalized.append(bar)
                    health.append(
                        symbol=symbol,
                        observed_at=response.fetched_at,
                        to_state="HEALTHY",
                        reason=(
                            "closed_bar_received" if bars else "valid_response_no_new_closed_bar"
                        ),
                        last_valid_interval_end=(
                            max(
                                (
                                    bar.interval_end
                                    for bar in normalized.bars.values()
                                    if bar.instrument == symbol
                                ),
                                default=None,
                            )
                        ),
                        last_collected_at=response.fetched_at,
                    )
                except Exception as exc:  # class/status only; never print provider bodies
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
                        last_valid_interval_end=max(
                            (
                                bar.interval_end
                                for bar in normalized.bars.values()
                                if bar.instrument == symbol
                            ),
                            default=None,
                        ),
                    )
            _refresh_cases(
                normalized=normalized,
                cases=cases,
                rejections=rejections,
                source_snapshot=source_snapshot,
                phase3_gate_sha256=phase3_gate_sha256,
            )
            summary = _summary(
                run_directory=run_directory,
                started_at=started_at,
                state=state,
                target_cases_per_symbol=target_cases_per_symbol,
                target_end=target_end,
                raw=raw,
                normalized=normalized,
                cases=cases,
                failures=failures,
                health=health,
                rejections=rejections,
                client=client,
                source_snapshot=source_snapshot,
                phase3_gate_sha256=phase3_gate_sha256,
                preregistration_sha256=preregistration_sha256,
            )
            if all(
                int(summary["case_counts"][symbol]) >= target_cases_per_symbol
                for symbol in V3_CORE_SYMBOLS
            ):
                state = "target_reached"
                summary["state"] = state
                _write_atomic(run_directory / "summary.json", summary)
                _write_atomic(run_directory / "status.json", {**summary, "pid": os.getpid()})
                break
            _write_atomic(
                run_directory / "heartbeat.json",
                {
                    "schema": f"{RUN_SCHEMA}.heartbeat",
                    "pid": os.getpid(),
                    "heartbeat_at": datetime.now(UTC).isoformat(),
                    "state": "running",
                    "case_counts": cases.count_by_symbol(),
                    "raw_response_count": len(raw.records),
                    "normalized_bar_count": len(normalized.bars),
                    "network_call_count": client.request_count,
                },
            )
            _write_atomic(run_directory / "status.json", {**summary, "pid": os.getpid()})
            remaining = (target_end - datetime.now(UTC)).total_seconds()
            if remaining > 0:
                time.sleep(min(poll_seconds, remaining))
        if stop_requested:
            state = "stopped_with_evidence"
    finally:
        signal.signal(signal.SIGTERM, prior_sigterm)
        signal.signal(signal.SIGINT, prior_sigint)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    summary = _summary(
        run_directory=run_directory,
        started_at=started_at,
        state=state,
        target_cases_per_symbol=target_cases_per_symbol,
        target_end=target_end,
        raw=raw,
        normalized=normalized,
        cases=cases,
        failures=failures,
        health=health,
        rejections=rejections,
        client=client,
        source_snapshot=source_snapshot,
        phase3_gate_sha256=phase3_gate_sha256,
        preregistration_sha256=preregistration_sha256,
    )
    _write_atomic(run_directory / "summary.json", summary)
    _write_atomic(run_directory / "status.json", {**summary, "pid": os.getpid()})
    _write_atomic(
        run_directory / "heartbeat.json",
        {
            "schema": f"{RUN_SCHEMA}.heartbeat",
            "pid": os.getpid(),
            "heartbeat_at": datetime.now(UTC).isoformat(),
            "state": state,
            "case_counts": cases.count_by_symbol(),
            "raw_response_count": len(raw.records),
            "normalized_bar_count": len(normalized.bars),
            "network_call_count": client.request_count,
        },
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", help="allow public read-only network calls")
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--phase3-gate-sha256", required=True)
    parser.add_argument(
        "--target-cases-per-symbol", type=int, default=DEFAULT_TARGET_CASES_PER_SYMBOL
    )
    parser.add_argument("--max-duration-hours", type=float, default=DEFAULT_MAX_DURATION_HOURS)
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    args = parser.parse_args()
    summary = run_collection(
        run_directory=args.run_directory,
        repository_root=args.repository_root.resolve(),
        preregistration=args.preregistration,
        phase3_gate_sha256=args.phase3_gate_sha256,
        target_cases_per_symbol=args.target_cases_per_symbol,
        max_duration_hours=args.max_duration_hours,
        poll_seconds=args.poll_seconds,
        real=args.real,
    )
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0 if summary["state"] in {"target_reached", "deadline_reached"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
