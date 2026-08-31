#!/usr/bin/env python3
"""Acquire the credential-free public source for the 80-opportunity run.

This entry point is deliberately long-run-specific.  It never reads a secret,
uses a trading endpoint, or creates a candidate prediction.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import sys
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode

from advisorai.integrations.http import HttpClientConfig, HttpTransportError, SafeHttpClient
from advisorai.phase4.v3core_cadence import V3_CORE_MARKET_DATA_REST_BASE, V3_CORE_SYMBOLS
from advisorai.phase4.v3core_canary import CanaryFinalityViolation, sha256_file
from advisorai.phase4.v3core_forward import ForwardNormalizedBarSpool, parse_binance_klines
from advisorai.phase4.v3core_longrun_runtime import (
    LONG_RUN_EVIDENCE_CLASS,
    LongRunCoordinator,
    LongRunFinalityTracker,
    LongRunIncident,
    LongRunPreregistration,
    LongRunRawSpool,
    LongRunTransportFailureSpool,
    collect_runtime_attestation,
    fresh_long_run_minimum_interval_end,
    load_long_run_preregistration,
    process_command_identity,
    process_create_time,
    process_identity_matches,
    read_json_stable,
    read_normalized_bars_stable,
    validate_utc_clock_progress,
    write_json_atomic,
)

RUN_SCHEMA = "advisorai.phase4.v3-core.long-run.source.v1"
POLL_SECONDS = 30.0


def _git_head(repository_root: Path) -> str:
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _request_url(symbol: str) -> str:
    return f"{V3_CORE_MARKET_DATA_REST_BASE}/api/v3/klines?{urlencode({'interval': '5m', 'limit': 2, 'symbol': symbol})}"


def _latest(normalized: ForwardNormalizedBarSpool, symbol: str) -> str | None:
    value = max(
        (bar.interval_end for bar in normalized.bars.values() if bar.instrument == symbol),
        default=None,
    )
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _status(
    *,
    preregistration: LongRunPreregistration,
    preregistration_sha256: str,
    repository_root: Path,
    run_root: Path,
    raw: LongRunRawSpool,
    normalized: ForwardNormalizedBarSpool,
    tracker: LongRunFinalityTracker,
    transport_failures: LongRunTransportFailureSpool,
    state: str,
    failure: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "schema": RUN_SCHEMA,
        "generation_id": preregistration.generation_id,
        "evidence_class": LONG_RUN_EVIDENCE_CLASS,
        "admission_eligible": False,
        "phase4_materialization_eligible": False,
        "state": state,
        "pid": os.getpid(),
        "process_create_time": process_create_time(os.getpid()),
        "command": [sys.executable, *sys.argv],
        "command_identity": process_command_identity([sys.executable, *sys.argv]),
        "repository_commit": _git_head(repository_root),
        "preregistration_sha256": preregistration_sha256,
        "runtime_attestation_sha256": preregistration.runtime_attestation_sha256,
        "source_snapshot_hash": preregistration.source_snapshot_sha256,
        "collector_code_sha256": sha256_file(Path(__file__).resolve()),
        "finality_code_sha256": preregistration.finality_rule_sha256,
        "raw_receipt_count": len(raw.records),
        "transport_failure_count": len(transport_failures.records),
        "admitted_final_bar_count": {
            symbol: sum(bar.instrument == symbol for bar in normalized.bars.values())
            for symbol in V3_CORE_SYMBOLS
        },
        "unresolved_interval_count": tracker.metrics()["unresolved_intervals"],
        "latest_admitted_interval_end": {
            symbol: _latest(normalized, symbol) for symbol in V3_CORE_SYMBOLS
        },
        "finality": tracker.metrics(),
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "execution_authority_present": False,
        "endpoint": preregistration.rest_endpoint,
        "interval": preregistration.interval,
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "deadline": preregistration.terminal_deadline.isoformat().replace("+00:00", "Z"),
    }
    if failure is not None:
        result["failure"] = failure
    return result


def _file_identity(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest() if path.is_file() else sha256(b"").hexdigest()


def _validate_or_record_resume(
    *,
    prior_status: dict[str, object] | None,
    resume: bool,
    preregistration: LongRunPreregistration,
    preregistration_sha256: str,
    run_root: Path,
    coordinator: LongRunCoordinator,
) -> None:
    if prior_status is None:
        return
    active_states = {"RUNNING_WARMUP", "RUNNING", "RECOVERING_COMPONENT"}
    status_state = str(prior_status.get("state"))
    if status_state == "GENERATION_FATAL":
        raise RuntimeError("a previously fatal long-run source cannot be restarted")
    if status_state not in active_states:
        return
    if not resume:
        raise RuntimeError(
            "existing active source requires explicit --resume; silent restart is forbidden"
        )
    old_pid = prior_status.get("pid")
    old_command = prior_status.get("command")
    old_identity = prior_status.get("command_identity")
    old_create_time = prior_status.get("process_create_time")
    if not (
        isinstance(old_pid, int)
        and isinstance(old_command, list)
        and isinstance(old_identity, str)
        and isinstance(old_create_time, (int, float))
    ):
        raise RuntimeError("source resume lacks the prior process identity")
    if process_identity_matches(
        old_pid,
        old_identity,
        [str(item) for item in old_command],
        expected_process_create_time=float(old_create_time),
    ):
        raise RuntimeError("source resume refused while the prior PID identity is still alive")
    expected = {
        "generation_id": preregistration.generation_id,
        "preregistration_sha256": preregistration_sha256,
        "repository_commit": preregistration.repository_commit,
        "source_snapshot_hash": preregistration.source_snapshot_sha256,
        "collector_code_sha256": sha256_file(Path(__file__).resolve()),
        "finality_code_sha256": preregistration.finality_rule_sha256,
        "endpoint": preregistration.rest_endpoint,
        "interval": preregistration.interval,
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "execution_authority_present": False,
    }
    if any(prior_status.get(key) != value for key, value in expected.items()):
        raise RuntimeError("source resume identity differs from the frozen contract")
    incident_id = sha256(
        json.dumps(
            {
                "component": "collector",
                "old_pid": old_pid,
                "old_command_identity": old_identity,
                "failure_timestamp": prior_status.get("updated_at"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    coordinator.record_recovery(
        incident_id=incident_id,
        component="collector",
        old_pid=old_pid,
        old_command_identity=old_identity,
        failure=str(prior_status.get("failure") or "source process interruption"),
        before_hashes={
            "raw": _file_identity(run_root / "raw-receipts.jsonl"),
            "normalized": _file_identity(run_root / "normalized-bars.jsonl"),
            "revisions": _file_identity(run_root / "post-admission-revisions.jsonl"),
        },
        new_pid=os.getpid(),
        new_command_identity=process_command_identity([sys.executable, *sys.argv]),
        resume_valid=True,
        cutoffs_impacted=(),
        at=datetime.now(UTC),
    )


def collect(
    *,
    repository_root: Path,
    preregistration_path: Path,
    preregistration_sha256: str,
    run_root: Path,
    coordinator_root: Path,
    poll_seconds: float,
    resume: bool,
    real: bool,
) -> dict[str, object]:
    if not real:
        raise ValueError("long-run source acquisition requires explicit --real")
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    repository_root = repository_root.resolve()
    preregistration_path = preregistration_path.resolve()
    preregistration = load_long_run_preregistration(
        preregistration_path, expected_sha256=preregistration_sha256
    )
    if _git_head(repository_root) != preregistration.repository_commit:
        raise ValueError("repository HEAD does not match the long-run preregistration")
    if collect_runtime_attestation().attestation_hash != preregistration.runtime_attestation_sha256:
        raise ValueError("collector runtime attestation differs from the preregistration")
    if sha256_file(Path(__file__).resolve()) != preregistration.collector_code_sha256:
        raise ValueError("collector code does not match the long-run preregistration")
    run_root = run_root.resolve()
    prior_status = (
        read_json_stable(run_root / "status.json") if (run_root / "status.json").exists() else None
    )
    if prior_status is None and any(
        (run_root / name).exists()
        for name in (
            "raw-receipts.jsonl",
            "normalized-bars.jsonl",
            "post-admission-revisions.jsonl",
            "transport-failures.jsonl",
            "manifest.json",
        )
    ):
        raise RuntimeError(
            "source append-only evidence exists without a status snapshot; resume is ambiguous"
        )
    if prior_status is not None and str(prior_status.get("state")) == "GENERATION_FATAL":
        raise RuntimeError("cannot restart a source whose scientific state is fatal")
    run_root.mkdir(parents=True, exist_ok=True)
    lock_path = run_root / "collector.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another long-run collector owns this root") from exc
        raw = LongRunRawSpool(
            run_root / "raw-receipts.jsonl",
            require_request_query=True,
            source_snapshot_hash=preregistration.source_snapshot_sha256,
        )
        transport_failures = LongRunTransportFailureSpool(
            run_root / "transport-failures.jsonl",
            source_snapshot_hash=preregistration.source_snapshot_sha256,
        )
        coordinator = LongRunCoordinator(
            preregistration, coordinator_root.resolve() / "events.jsonl"
        )
        _validate_or_record_resume(
            prior_status=prior_status,
            resume=resume,
            preregistration=preregistration,
            preregistration_sha256=preregistration_sha256,
            run_root=run_root,
            coordinator=coordinator,
        )
        terminal_states = {
            "DEADLINE_REACHED",
            "TERMINALIZING",
            "COMPLETED_PENDING_AUDIT",
            "AUDITED",
        }
        if prior_status is not None and str(prior_status.get("state")) in terminal_states:
            if prior_status.get("generation_id") != preregistration.generation_id:
                raise RuntimeError("terminal source status generation identity differs")
            if prior_status.get("preregistration_sha256") != preregistration_sha256:
                raise RuntimeError("terminal source status preregistration identity differs")
            if (
                prior_status.get("credentials_loaded") is not False
                or prior_status.get("order_writes_attempted") is not False
            ):
                raise RuntimeError("terminal source status has unsafe security flags")
            if not (run_root / "manifest.json").is_file():
                raise RuntimeError("terminal source has no preserved manifest")
            return prior_status
        tracker = LongRunFinalityTracker(
            run_root / "normalized-bars.jsonl",
            run_root / "post-admission-revisions.jsonl",
            minimum_interval_end=fresh_long_run_minimum_interval_end(preregistration.start_at),
            source_snapshot_hash=preregistration.source_snapshot_sha256,
        )
        # Validate the append-only projection without using the permissive
        # dictionary loader.  Identical duplicate lines would otherwise be
        # silently collapsed before the finality tracker sees them.
        normalized_path = run_root / "normalized-bars.jsonl"
        if normalized_path.exists():
            read_normalized_bars_stable(normalized_path)
        tracker.replay(raw.records, preregistration.source_snapshot_sha256)
        # The tracker owns the live derived projection; do not retain a
        # second pre-replay spool object for status accounting.
        normalized = tracker.normalized
        manifest = {
            "schema": RUN_SCHEMA,
            "generation_id": preregistration.generation_id,
            "evidence_class": LONG_RUN_EVIDENCE_CLASS,
            "admission_eligible": False,
            "repository_commit": preregistration.repository_commit,
            "preregistration_sha256": preregistration_sha256,
            "runtime_attestation_sha256": preregistration.runtime_attestation_sha256,
            "source_snapshot_hash": preregistration.source_snapshot_sha256,
            "collector_code_sha256": sha256_file(Path(__file__).resolve()),
            "finality_code_sha256": preregistration.finality_rule_sha256,
            "endpoint": preregistration.rest_endpoint,
            "interval": preregistration.interval,
            "symbols": list(preregistration.symbols),
            "credentials_loaded": False,
            "order_writes_attempted": False,
            "execution_authority_present": False,
        }
        manifest_path = run_root / "manifest.json"
        if manifest_path.exists():
            if read_json_stable(manifest_path) != manifest:
                raise RuntimeError("existing long-run source manifest identity differs")
        else:
            write_json_atomic(manifest_path, manifest)
        client = SafeHttpClient(
            HttpClientConfig(
                allowed_hosts=("data-api.binance.vision",),
                timeout_seconds=20,
                max_retries=1,
                requests_per_second=1,
                user_agent="advisorai-v3/phase4-long-run-public-source",
            ),
            base_url=V3_CORE_MARKET_DATA_REST_BASE,
        )
        stop_requested = False

        def request_stop(_signum: int, _frame: object) -> None:
            nonlocal stop_requested
            stop_requested = True

        old_term = signal.getsignal(signal.SIGTERM)
        old_int = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        state = "RUNNING_WARMUP"
        failure: str | None = None
        previous_clock: datetime | None = None
        write_json_atomic(
            run_root / "status.json",
            _status(
                preregistration=preregistration,
                preregistration_sha256=preregistration_sha256,
                repository_root=repository_root,
                run_root=run_root,
                raw=raw,
                normalized=normalized,
                tracker=tracker,
                transport_failures=transport_failures,
                state=state,
                failure=failure,
            ),
        )
        try:
            # The process may be started before the frozen UTC start so that
            # the launcher can validate a live status surface.  No public
            # receipt may be acquired before that instant.
            while not stop_requested:
                start_now = datetime.now(UTC)
                if start_now >= preregistration.start_at:
                    break
                time.sleep(
                    min(
                        poll_seconds,
                        max(0.0, (preregistration.start_at - start_now).total_seconds()),
                    )
                )
                if not stop_requested:
                    write_json_atomic(
                        run_root / "status.json",
                        _status(
                            preregistration=preregistration,
                            preregistration_sha256=preregistration_sha256,
                            repository_root=repository_root,
                            run_root=run_root,
                            raw=raw,
                            normalized=normalized,
                            tracker=tracker,
                            transport_failures=transport_failures,
                            state=state,
                            failure=failure,
                        ),
                    )
            while not stop_requested and datetime.now(UTC) < preregistration.terminal_deadline:
                try:
                    now = validate_utc_clock_progress(
                        previous_clock,
                        datetime.now(UTC),
                        component="collector",
                    )
                except RuntimeError as exc:
                    state = "GENERATION_FATAL"
                    failure = f"SOURCE_CLOCK_ERROR:{exc}"
                    coordinator.fail(
                        LongRunIncident.SOURCE_CLOCK_ERROR, at=datetime.now(UTC), detail=str(exc)
                    )
                    break
                previous_clock = now
                for symbol in V3_CORE_SYMBOLS:
                    if stop_requested:
                        break
                    url = _request_url(symbol)
                    try:
                        response = client.get(
                            url,
                            headers={
                                "Cache-Control": "no-cache",
                                "Pragma": "no-cache",
                            },
                            max_retries=1,
                            timeout_seconds=20,
                        )
                        attempt_id = f"{os.getpid()}:{client.request_count}:{symbol}:{response.fetched_at.isoformat()}"
                        receipt = raw.append(
                            response,
                            symbol=symbol,
                            request_url=url,
                            request_attempt_id=attempt_id,
                            source_snapshot_hash=preregistration.source_snapshot_sha256,
                        )
                        bars = parse_binance_klines(
                            response.body,
                            symbol=symbol,
                            collected_at=response.fetched_at,
                            source_snapshot_hash=preregistration.source_snapshot_sha256,
                        )
                        tracker.observe(receipt, bars)
                    except HttpTransportError as exc:
                        failure_at = datetime.now(UTC)
                        transport_failures.append(
                            symbol=symbol,
                            request_url=url,
                            request_attempt_id=(
                                f"{os.getpid()}:transport:{client.request_count}:{symbol}:"
                                f"{failure_at.isoformat()}"
                            ),
                            observed_at=failure_at,
                            source_snapshot_hash=preregistration.source_snapshot_sha256,
                            error_class=exc.error_type or type(exc).__name__,
                            status_code=exc.status_code,
                            retriable=exc.retriable,
                        )
                    except CanaryFinalityViolation as exc:
                        state = "GENERATION_FATAL"
                        failure = f"SOURCE_POST_ADMISSION_REVISION:{type(exc).__name__}"
                        coordinator.fail(
                            LongRunIncident.POST_ADMISSION_REVISION,
                            at=datetime.now(UTC),
                            detail=f"{type(exc).__name__}:{exc}",
                        )
                        break
                    except Exception as exc:
                        state = "GENERATION_FATAL"
                        failure = f"SOURCE_CONTRACT_ERROR:{type(exc).__name__}"
                        coordinator.fail(
                            LongRunIncident.UNKNOWN,
                            at=datetime.now(UTC),
                            detail=f"{type(exc).__name__}:{exc}",
                        )
                        break
                if state == "GENERATION_FATAL":
                    break
                state = (
                    "RUNNING"
                    if now >= preregistration.first_mandatory_cutoff_at
                    else "RUNNING_WARMUP"
                )
                write_json_atomic(
                    run_root / "status.json",
                    _status(
                        preregistration=preregistration,
                        preregistration_sha256=preregistration_sha256,
                        repository_root=repository_root,
                        run_root=run_root,
                        raw=raw,
                        normalized=normalized,
                        tracker=tracker,
                        transport_failures=transport_failures,
                        state=state,
                        failure=failure,
                    ),
                )
                if now < preregistration.terminal_deadline:
                    time.sleep(
                        min(
                            poll_seconds,
                            max(0.0, (preregistration.terminal_deadline - now).total_seconds()),
                        )
                    )
            if stop_requested and state != "GENERATION_FATAL":
                state = "GENERATION_FATAL"
                failure = "STOP_REQUESTED"
                coordinator.fail(LongRunIncident.UNKNOWN, at=datetime.now(UTC), detail=failure)
            elif state != "GENERATION_FATAL":
                state = "DEADLINE_REACHED"
        finally:
            signal.signal(signal.SIGTERM, old_term)
            signal.signal(signal.SIGINT, old_int)
        result = _status(
            preregistration=preregistration,
            preregistration_sha256=preregistration_sha256,
            repository_root=repository_root,
            run_root=run_root,
            raw=raw,
            normalized=normalized,
            tracker=tracker,
            transport_failures=transport_failures,
            state=state,
            failure=failure,
        )
        write_json_atomic(run_root / "status.json", result)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--preregistration-sha256", required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    parser.add_argument("--coordinator-root", type=Path, required=True)
    parser.add_argument(
        "--resume", action="store_true", help="resume only after explicit identity validation"
    )
    args = parser.parse_args()
    try:
        result = collect(
            repository_root=args.repository_root,
            preregistration_path=args.preregistration,
            preregistration_sha256=args.preregistration_sha256,
            run_root=args.run_root,
            coordinator_root=args.coordinator_root,
            poll_seconds=args.poll_seconds,
            resume=args.resume,
            real=args.real,
        )
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"long-run source collection refused ({type(exc).__name__})") from exc
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["state"] == "DEADLINE_REACHED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
