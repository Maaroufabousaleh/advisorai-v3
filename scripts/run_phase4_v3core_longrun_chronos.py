#!/usr/bin/env python3
"""Run the pinned Chronos candidate for an 80-cutoff long-run contract."""

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

from advisorai.phase4.v3core_canary import sha256_file
from advisorai.phase4.v3core_chronos import (
    ChronosInferenceFailure,
    ChronosRuntimeIdentity,
    build_chronos_prediction,
    infer_chronos,
)
from advisorai.phase4.v3core_longrun_runtime import (
    LONG_RUN_EVIDENCE_CLASS,
    LongRunCaseState,
    LongRunCoordinator,
    LongRunIncident,
    LongRunPredictionLedger,
    LongRunPreregistration,
    LongRunState,
    collect_runtime_attestation,
    fresh_long_run_minimum_interval_end,
    load_long_run_preregistration,
    long_run_context_for_cutoff,
    prediction_deadline,
    process_command_identity,
    process_create_time,
    process_identity_matches,
    read_json_stable,
    read_long_run_normalized_bars_for_start,
    validate_utc_clock_progress,
    write_json_atomic,
)

RUN_SCHEMA = "advisorai.phase4.v3-core.long-run.candidate.v1"
POLL_SECONDS = 2.0
WORKER_TIMEOUT_SECONDS = 120.0
CASE_EXCLUDABLE_INFERENCE_ERRORS = frozenset({"WorkerTimeout"})


def _git_head(repository_root: Path) -> str:
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _validate_source_status(
    source_status: dict[str, object],
    preregistration: LongRunPreregistration,
    preregistration_sha256: str,
) -> None:
    """Require every candidate pass to use the current frozen source view."""

    if (
        source_status.get("generation_id") != preregistration.generation_id
        or source_status.get("preregistration_sha256") != preregistration_sha256
        or source_status.get("evidence_class") != LONG_RUN_EVIDENCE_CLASS
        or source_status.get("admission_eligible") is not False
        or source_status.get("phase4_materialization_eligible") is not False
        or source_status.get("runtime_attestation_sha256")
        != preregistration.runtime_attestation_sha256
        or source_status.get("source_snapshot_hash") != preregistration.source_snapshot_sha256
        or source_status.get("collector_code_sha256") != preregistration.collector_code_sha256
        or source_status.get("finality_code_sha256") != preregistration.finality_rule_sha256
        or source_status.get("endpoint") != preregistration.rest_endpoint
        or source_status.get("interval") != preregistration.interval
        or source_status.get("repository_commit") != preregistration.repository_commit
        or source_status.get("credentials_loaded") is not False
        or source_status.get("order_writes_attempted") is not False
        or source_status.get("execution_authority_present") is not False
    ):
        raise RuntimeError(
            "candidate source identity or security flags differ from the preregistration"
        )


def _status(
    *,
    preregistration: LongRunPreregistration,
    preregistration_sha256: str,
    repository_root: Path,
    ledger: LongRunPredictionLedger,
    coordinator: LongRunCoordinator,
    identity: ChronosRuntimeIdentity,
    state: str,
    last_cutoff: str | None,
    rejection_count: int,
    schema_failures: int = 0,
    nan_inf_failures: int = 0,
    model_load_failures: int = 0,
    conflicting_ledger_writes: int = 0,
    cuda_failures: int = 0,
    model_loaded: bool = False,
    ledger_health: bool = True,
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
        "model_loaded": model_loaded,
        "model_repository": identity.checkpoint_repository,
        "model_revision": identity.checkpoint_revision,
        "checkpoint_hash": identity.checkpoint_hash,
        "model_identity_hash": identity.model_identity_hash,
        "preprocessing_code_sha256": sha256_file(
            repository_root / "src/advisorai/phase4/v3core_chronos.py"
        ),
        "preprocessing_hash": identity.preprocessing_hash,
        "runner_hash": identity.runner_hash,
        "dependency_lock_hash": identity.lock_hash,
        "runtime_environment_hash": identity.environment_fingerprint,
        "prediction_count": len(ledger.entries),
        "prediction_counts": {
            symbol: sum(entry.symbol == symbol for entry in ledger.entries)
            for symbol in preregistration.symbols
        },
        "rejection_count": rejection_count,
        "schema_failures": schema_failures,
        "nan_inf_failures": nan_inf_failures,
        "model_load_failures": model_load_failures,
        "conflicting_ledger_writes": conflicting_ledger_writes,
        "latest_successful_cutoff": last_cutoff,
        "fatal_history_count": len(coordinator.fatal_events),
        "scientific_state": coordinator.scientific_state.value,
        "ledger_health": ledger_health,
        "cuda_failures": cuda_failures,
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "execution_authority_present": False,
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "prediction_lateness_seconds": preregistration.prediction_lateness_seconds,
    }
    if failure is not None:
        result["failure"] = failure
    return result


def _record_exclusion(
    coordinator: LongRunCoordinator,
    *,
    symbol: str,
    ordinal: int,
    cutoff: datetime,
    reason: str,
    prediction_id: str | None = None,
) -> bool:
    event = coordinator.record_case_excluded(
        symbol=symbol,
        ordinal=ordinal,
        cutoff=cutoff,
        reason=reason,
        at=datetime.now(UTC),
        prediction_id=prediction_id,
    )
    return event is not None


def _excluded_count(coordinator: LongRunCoordinator) -> int:
    return sum(
        state == LongRunCaseState.EXCLUDED
        for state, _prediction_id, _outcome_id in coordinator.case_states().values()
    )


def _file_identity(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest() if path.is_file() else sha256(b"").hexdigest()


def _validate_or_record_resume(
    *,
    prior_status: dict[str, object] | None,
    resume: bool,
    preregistration: LongRunPreregistration,
    preregistration_sha256: str,
    repository_root: Path,
    run_root: Path,
    coordinator: LongRunCoordinator,
    identity: ChronosRuntimeIdentity,
) -> None:
    if prior_status is None:
        return
    active_states = {
        LongRunState.RUNNING_WARMUP.value,
        LongRunState.RUNNING.value,
        LongRunState.RECOVERING_COMPONENT.value,
    }
    status_state = str(prior_status.get("state"))
    if status_state == LongRunState.GENERATION_FATAL.value:
        raise RuntimeError("a previously fatal long-run candidate cannot be restarted")
    if status_state not in active_states:
        return
    if not resume:
        raise RuntimeError(
            "existing active candidate requires explicit --resume; silent restart is forbidden"
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
        raise RuntimeError("candidate resume lacks the prior process identity")
    if process_identity_matches(
        old_pid,
        old_identity,
        [str(item) for item in old_command],
        expected_process_create_time=float(old_create_time),
    ):
        raise RuntimeError("candidate resume refused while the prior PID identity is still alive")
    expected = {
        "generation_id": preregistration.generation_id,
        "preregistration_sha256": preregistration_sha256,
        "repository_commit": preregistration.repository_commit,
        "candidate_worker_code_sha256": sha256_file(Path(__file__).resolve()),
        "preprocessing_code_sha256": preregistration.preprocessing_sha256,
        "model_repository": identity.checkpoint_repository,
        "model_revision": identity.checkpoint_revision,
        "checkpoint_hash": identity.checkpoint_hash,
        "model_identity_hash": identity.model_identity_hash,
        "preprocessing_hash": identity.preprocessing_hash,
        "runner_hash": identity.runner_hash,
        "dependency_lock_hash": identity.lock_hash,
        "runtime_environment_hash": identity.environment_fingerprint,
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "execution_authority_present": False,
    }
    if any(prior_status.get(key) != value for key, value in expected.items()):
        raise RuntimeError("candidate resume identity differs from the frozen contract")
    incident_id = sha256(
        json.dumps(
            {
                "component": "candidate",
                "old_pid": old_pid,
                "old_command_identity": old_identity,
                "failure_timestamp": prior_status.get("updated_at"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    before_hashes = {
        "predictions": _file_identity(run_root / "predictions.jsonl"),
        "durability": _file_identity(run_root / "prediction-durability.jsonl"),
        "events": _file_identity(coordinator.events_path),
    }
    coordinator.record_recovery(
        incident_id=incident_id,
        component="candidate",
        old_pid=old_pid,
        old_command_identity=old_identity,
        failure=str(prior_status.get("failure") or "candidate process interruption"),
        before_hashes=before_hashes,
        new_pid=os.getpid(),
        new_command_identity=process_command_identity([sys.executable, *sys.argv]),
        resume_valid=True,
        cutoffs_impacted=(),
        at=datetime.now(UTC),
    )


def run(
    *,
    repository_root: Path,
    preregistration_path: Path,
    preregistration_sha256: str,
    admission_path: Path,
    qualification_evidence_path: Path,
    source_root: Path,
    run_root: Path,
    coordinator_root: Path,
    poll_seconds: float = POLL_SECONDS,
    worker_timeout_seconds: float = WORKER_TIMEOUT_SECONDS,
    resume: bool = False,
    real: bool = False,
) -> dict[str, object]:
    if not real:
        raise ValueError("long-run Chronos requires explicit --real opt-in")
    if poll_seconds <= 0 or worker_timeout_seconds <= 0:
        raise ValueError("poll and worker timeout values must be positive")
    repository_root = repository_root.resolve()
    preregistration_path = preregistration_path.resolve()
    preregistration = load_long_run_preregistration(
        preregistration_path, expected_sha256=preregistration_sha256
    )
    if _git_head(repository_root) != preregistration.repository_commit:
        raise ValueError("candidate repository identity differs from preregistration")
    if sha256_file(Path(__file__).resolve()) != preregistration.candidate_worker_code_sha256:
        raise ValueError("candidate worker identity differs from preregistration")
    if (
        sha256_file(repository_root / "src/advisorai/phase4/v3core_chronos.py")
        != preregistration.preprocessing_sha256
    ):
        raise ValueError("candidate preprocessing code identity differs from preregistration")
    source_root = source_root.resolve()
    source_status = read_json_stable(source_root / "status.json")
    _validate_source_status(source_status, preregistration, preregistration_sha256)
    identity = ChronosRuntimeIdentity.from_admission(
        admission_path.resolve(),
        qualification_evidence_path=qualification_evidence_path.resolve(),
        repository_root=repository_root,
    )
    if collect_runtime_attestation().attestation_hash != preregistration.runtime_attestation_sha256:
        raise ValueError("candidate runtime attestation differs from the preregistration")
    run_root = run_root.resolve()
    prior_status = (
        read_json_stable(run_root / "status.json") if (run_root / "status.json").exists() else None
    )
    if prior_status is None and any(
        (run_root / name).exists()
        for name in (
            "predictions.jsonl",
            "prediction-durability.jsonl",
            "manifest.json",
        )
    ):
        raise RuntimeError(
            "candidate append-only evidence exists without a status snapshot; resume is ambiguous"
        )
    run_root.mkdir(parents=True, exist_ok=True)
    lock_path = run_root / "candidate.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another long-run candidate owns this root") from exc
        ledger = LongRunPredictionLedger(
            run_root / "predictions.jsonl",
            run_root / "prediction-durability.jsonl",
            preregistration.mandatory_cutoffs,
        )
        ledger.validate_against_preregistration(preregistration)
        coordinator = LongRunCoordinator(preregistration, coordinator_root / "events.jsonl")
        _validate_or_record_resume(
            prior_status=prior_status,
            resume=resume,
            preregistration=preregistration,
            preregistration_sha256=preregistration_sha256,
            repository_root=repository_root,
            run_root=run_root,
            coordinator=coordinator,
            identity=identity,
        )
        recovered_durability = ledger.recover_pending_durability()
        for recovered_id in recovered_durability:
            recovered_entry = next(
                entry for entry in ledger.entries if entry.prediction_id == recovered_id
            )
            recovered_attestation = ledger.durability_for(recovered_entry)
            if recovered_attestation is None:
                raise RuntimeError("pending prediction durability has no recovery attestation")
        if coordinator.state == LongRunState.PREREGISTERED:
            coordinator.transition(
                LongRunState.PREFLIGHT_READY,
                event_type="PREFLIGHT_READY",
                at=datetime.now(UTC),
            )
            coordinator.transition(
                LongRunState.RUNNING_WARMUP,
                event_type="CANDIDATE_STARTED",
                at=datetime.now(UTC),
            )
        if coordinator.scientific_state == LongRunState.GENERATION_FATAL:
            raise RuntimeError("long-run coordinator is already fatally latched")
        manifest = {
            "schema": RUN_SCHEMA,
            "generation_id": preregistration.generation_id,
            "evidence_class": LONG_RUN_EVIDENCE_CLASS,
            "admission_eligible": False,
            "repository_commit": preregistration.repository_commit,
            "preregistration_sha256": preregistration_sha256,
            "runtime_attestation_sha256": preregistration.runtime_attestation_sha256,
            "candidate_worker_code_sha256": sha256_file(Path(__file__).resolve()),
            "model_identity_hash": identity.model_identity_hash,
            "checkpoint_hash": identity.checkpoint_hash,
            "runner_hash": identity.runner_hash,
            "preprocessing_hash": identity.preprocessing_hash,
            "preprocessing_code_sha256": preregistration.preprocessing_sha256,
            "dependency_lock_hash": identity.lock_hash,
            "runtime_environment_hash": identity.environment_fingerprint,
            "context_bars": preregistration.context_bars,
            "context_newest_lag_seconds": preregistration.context_newest_lag_seconds,
            "credentials_loaded": False,
            "order_writes_attempted": False,
            "execution_authority_present": False,
        }
        manifest_path = run_root / "manifest.json"
        if manifest_path.exists():
            if read_json_stable(manifest_path) != manifest:
                raise RuntimeError("existing candidate manifest identity differs")
        else:
            write_json_atomic(manifest_path, manifest)
        if coordinator.state in {
            LongRunState.DEADLINE_REACHED,
            LongRunState.COMPLETED_PENDING_AUDIT,
            LongRunState.AUDITED,
        }:
            # A terminal projection is immutable.  Reopening a completed
            # candidate may validate it, but must not publish a replacement
            # snapshot with reset counters or timestamps.
            if prior_status is None:
                raise RuntimeError("terminal candidate has no preserved terminal status")
            return prior_status
        stop_requested = False

        def request_stop(_signum: int, _frame: object) -> None:
            nonlocal stop_requested
            stop_requested = True

        old_term = signal.getsignal(signal.SIGTERM)
        old_int = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        last_cutoff: str | None = None
        rejection_count = _excluded_count(coordinator)

        def prior_counter(name: str) -> int:
            value = prior_status.get(name, 0) if prior_status is not None else 0
            if not isinstance(value, int) or value < 0:
                raise RuntimeError(f"candidate resume counter is invalid: {name}")
            return value

        schema_failures = prior_counter("schema_failures")
        nan_inf_failures = prior_counter("nan_inf_failures")
        model_load_failures = prior_counter("model_load_failures")
        conflicting_ledger_writes = prior_counter("conflicting_ledger_writes")
        cuda_failures = prior_counter("cuda_failures")
        if prior_status is not None and not isinstance(prior_status.get("model_loaded"), bool):
            raise RuntimeError("candidate resume model_loaded flag is invalid")
        model_loaded = bool(prior_status.get("model_loaded")) if prior_status is not None else False
        if prior_status is not None and not isinstance(prior_status.get("ledger_health"), bool):
            raise RuntimeError("candidate resume ledger health is invalid")
        ledger_health = (
            bool(prior_status.get("ledger_health", True)) if prior_status is not None else True
        )
        failure: str | None = None
        state = str(prior_status.get("state")) if prior_status is not None else "RUNNING_WARMUP"
        if state not in {
            LongRunState.RUNNING_WARMUP.value,
            LongRunState.RUNNING.value,
            LongRunState.RECOVERING_COMPONENT.value,
        }:
            raise RuntimeError("candidate resume state is not active")
        if coordinator.scientific_state.value != state:
            raise RuntimeError("candidate status state differs from append-only coordinator state")
        previous_clock: datetime | None = None
        if ledger.entries:
            latest_cutoff = max(entry.cutoff for entry in ledger.entries)
            last_cutoff = latest_cutoff.isoformat().replace("+00:00", "Z")
        try:
            while not stop_requested and datetime.now(UTC) < preregistration.terminal_deadline:
                now = validate_utc_clock_progress(
                    previous_clock,
                    datetime.now(UTC),
                    component="candidate",
                )
                previous_clock = now
                if (
                    now >= preregistration.first_mandatory_cutoff_at
                    and coordinator.state == LongRunState.RUNNING_WARMUP
                ):
                    coordinator.transition(
                        LongRunState.RUNNING, event_type="WARMUP_COMPLETE", at=now
                    )
                source_status = read_json_stable(source_root / "status.json")
                _validate_source_status(source_status, preregistration, preregistration_sha256)
                if source_status.get("credentials_loaded") is not False:
                    coordinator.fail(
                        LongRunIncident.CREDENTIAL_DETECTED,
                        at=now,
                        detail="source status reported credentials_loaded",
                    )
                    failure = "SOURCE_CREDENTIAL_DETECTED"
                    break
                if (
                    source_status.get("order_writes_attempted") is not False
                    or source_status.get("execution_authority_present") is not False
                ):
                    coordinator.fail(
                        LongRunIncident.ORDER_WRITE_DETECTED,
                        at=now,
                        detail="source status reported execution authority or order writes",
                    )
                    failure = "SOURCE_EXECUTION_AUTHORITY_DETECTED"
                    break
                admitted_counts = source_status.get("admitted_final_bar_count")
                if not isinstance(admitted_counts, dict) or any(
                    not isinstance(admitted_counts.get(symbol), int)
                    or isinstance(admitted_counts.get(symbol), bool)
                    or admitted_counts.get(symbol, -1) < 0
                    for symbol in preregistration.symbols
                ):
                    raise RuntimeError("source admitted-bar counts are missing or invalid")
                bars = read_long_run_normalized_bars_for_start(
                    source_root,
                    expected_admitted_final_bars=sum(
                        int(admitted_counts[symbol]) for symbol in preregistration.symbols
                    ),
                )
                if source_status.get("state") == LongRunState.GENERATION_FATAL.value:
                    coordinator.fail(
                        "SOURCE_PROCESS_DEATH", at=now, detail="source reported fatal state"
                    )
                    failure = "SOURCE_REPORTED_FATAL"
                    break
                source_pid = source_status.get("pid")
                source_command = source_status.get("command")
                source_identity = source_status.get("command_identity")
                source_create_time = source_status.get("process_create_time")
                source_terminal = source_status.get("state") in {
                    "DEADLINE_REACHED",
                    "TERMINALIZING",
                    "COMPLETED_PENDING_AUDIT",
                    "AUDITED",
                }
                if not source_terminal and (
                    not isinstance(source_pid, int)
                    or not isinstance(source_command, list)
                    or not isinstance(source_identity, str)
                    or not isinstance(source_create_time, (int, float))
                    or not process_identity_matches(
                        source_pid,
                        source_identity,
                        [str(item) for item in source_command],
                        expected_process_create_time=float(source_create_time),
                    )
                ):
                    coordinator.fail(
                        "SOURCE_PROCESS_DEATH",
                        at=now,
                        detail="source process identity is absent or mismatched",
                    )
                    failure = "SOURCE_PROCESS_IDENTITY_FAILURE"
                    break
                if source_terminal and now < preregistration.terminal_deadline:
                    coordinator.fail(
                        "SOURCE_PROCESS_DEATH",
                        at=now,
                        detail="source terminated before the fixed long-run deadline",
                    )
                    failure = "SOURCE_TERMINATED_EARLY"
                    break
                for ordinal, cutoff in enumerate(preregistration.mandatory_cutoffs, 1):
                    if (
                        stop_requested
                        or coordinator.scientific_state == LongRunState.GENERATION_FATAL
                    ):
                        break
                    for symbol in preregistration.symbols:
                        existing = ledger.for_cutoff(symbol, cutoff)
                        case = coordinator.case_states().get((symbol, ordinal))
                        if existing is not None:
                            durable = ledger.durability_for(existing)
                            if durable is None:
                                raise RuntimeError(
                                    "existing prediction has no durability attestation"
                                )
                            if case is None and durable.durable_appended_at > prediction_deadline(
                                cutoff
                            ):
                                _record_exclusion(
                                    coordinator,
                                    symbol=symbol,
                                    ordinal=ordinal,
                                    cutoff=cutoff,
                                    reason="CASE_EXCLUDED_LATE_PREDICTION",
                                    prediction_id=existing.prediction_id,
                                )
                            elif case is None:
                                coordinator.record_case_pending(
                                    symbol=symbol,
                                    ordinal=ordinal,
                                    cutoff=cutoff,
                                    prediction_id=existing.prediction_id,
                                    at=now,
                                )
                            elif case[1] != existing.prediction_id:
                                raise RuntimeError(
                                    "existing prediction conflicts with case accounting"
                                )
                            continue
                        if case is not None:
                            if case[0] == LongRunCaseState.EXCLUDED:
                                # An excluded opportunity is terminal.  It is
                                # never retried on a later poll, and therefore
                                # cannot generate a second attempt or consume
                                # a different prediction identity.
                                continue
                            coordinator.fail(
                                "PREDICTION_LEDGER_CONFLICT",
                                at=now,
                                detail="active case accounting has no matching prediction ledger entry",
                            )
                            failure = "CASE_ACCOUNTING_WITHOUT_PREDICTION"
                            break
                        if now < cutoff:
                            continue
                        context = long_run_context_for_cutoff(
                            bars,
                            instrument=symbol,
                            cutoff=cutoff,
                            available_at=now,
                            minimum_interval_end=fresh_long_run_minimum_interval_end(
                                preregistration.start_at
                            ),
                            source_snapshot_hash=preregistration.source_snapshot_sha256,
                        )
                        if now >= prediction_deadline(cutoff):
                            if _record_exclusion(
                                coordinator,
                                symbol=symbol,
                                ordinal=ordinal,
                                cutoff=cutoff,
                                reason=(
                                    "SOURCE_CONTEXT_UNAVAILABLE"
                                    if context is None
                                    else "CASE_EXCLUDED_LATE_PREDICTION"
                                ),
                            ):
                                rejection_count += 1
                            continue
                        if context is None:
                            continue
                        try:
                            inference_started_at = datetime.now(UTC)
                            inference = infer_chronos(
                                identity=identity,
                                context=context,
                                timeout_seconds=worker_timeout_seconds,
                            )
                            inference_finished_at = datetime.now(UTC)
                            model_loaded = True
                            if inference_finished_at > prediction_deadline(cutoff):
                                if _record_exclusion(
                                    coordinator,
                                    symbol=symbol,
                                    ordinal=ordinal,
                                    cutoff=cutoff,
                                    reason="CASE_EXCLUDED_LATE_PREDICTION",
                                ):
                                    rejection_count += 1
                                continue
                            prediction = build_chronos_prediction(
                                identity=identity,
                                instrument=symbol,
                                cutoff=cutoff,
                                generated_at=inference_finished_at,
                                context=context,
                                result=inference,
                                inference_started_at=inference_started_at,
                                inference_finished_at=inference_finished_at,
                                ledger_persisted_at=None,
                                generation_deadline_at=prediction_deadline(cutoff),
                            )
                            entry = ledger.append(
                                generation_id=preregistration.generation_id,
                                symbol=symbol,
                                cutoff=cutoff,
                                cutoff_ordinal=ordinal,
                                prediction=prediction,
                                inference_started_at=inference_started_at,
                                inference_finished_at=inference_finished_at,
                                append_started_at=datetime.now(UTC),
                            )
                            durable = ledger.durability_for(entry)
                            if durable is None:
                                coordinator.fail(
                                    "PREDICTION_LEDGER_CONFLICT",
                                    at=now,
                                    detail="prediction has no durability attestation",
                                )
                                failure = "MISSING_DURABILITY_ATTESTATION"
                                break
                            if durable.durable_appended_at > prediction_deadline(cutoff):
                                _record_exclusion(
                                    coordinator,
                                    symbol=symbol,
                                    ordinal=ordinal,
                                    cutoff=cutoff,
                                    reason="CASE_EXCLUDED_LATE_PREDICTION",
                                    prediction_id=entry.prediction_id,
                                )
                            else:
                                coordinator.record_case_pending(
                                    symbol=symbol,
                                    ordinal=ordinal,
                                    cutoff=cutoff,
                                    prediction_id=entry.prediction_id,
                                    at=durable.durable_appended_at,
                                )
                            last_cutoff = cutoff.isoformat().replace("+00:00", "Z")
                        except ChronosInferenceFailure as exc:
                            if exc.error_class in CASE_EXCLUDABLE_INFERENCE_ERRORS:
                                if _record_exclusion(
                                    coordinator,
                                    symbol=symbol,
                                    ordinal=ordinal,
                                    cutoff=cutoff,
                                    reason=f"CANDIDATE_INFERENCE_{exc.error_class.upper()}",
                                ):
                                    rejection_count += 1
                            else:
                                if "NaN" in exc.error_class or "Inf" in exc.error_class:
                                    nan_inf_failures += 1
                                elif "CUDA" in exc.error_class.upper():
                                    cuda_failures += 1
                                elif "Load" in exc.error_class or "Model" in exc.error_class:
                                    model_load_failures += 1
                                else:
                                    schema_failures += 1
                                coordinator.fail(
                                    "CANDIDATE_SCHEMA_FAILURE"
                                    if "Malformed" in exc.error_class
                                    or "Mismatch" in exc.error_class
                                    else "CANDIDATE_RUNTIME_FAILURE",
                                    at=datetime.now(UTC),
                                    detail=exc.error_class,
                                )
                                failure = f"CANDIDATE_FATAL:{exc.error_class}"
                                break
                        except (RuntimeError, ValueError) as exc:
                            conflicting_ledger_writes += 1
                            ledger_health = False
                            coordinator.fail(
                                "PREDICTION_LEDGER_CONFLICT", at=datetime.now(UTC), detail=str(exc)
                            )
                            failure = f"CANDIDATE_CONTRACT_ERROR:{type(exc).__name__}"
                            break
                state = coordinator.scientific_state.value
                rejection_count = _excluded_count(coordinator)
                if (
                    state != LongRunState.GENERATION_FATAL.value
                    and now >= preregistration.first_mandatory_cutoff_at
                ):
                    state = "RUNNING"
                write_json_atomic(
                    run_root / "status.json",
                    _status(
                        preregistration=preregistration,
                        preregistration_sha256=preregistration_sha256,
                        repository_root=repository_root,
                        ledger=ledger,
                        coordinator=coordinator,
                        identity=identity,
                        state=state,
                        last_cutoff=last_cutoff,
                        rejection_count=rejection_count,
                        schema_failures=schema_failures,
                        nan_inf_failures=nan_inf_failures,
                        model_load_failures=model_load_failures,
                        conflicting_ledger_writes=conflicting_ledger_writes,
                        cuda_failures=cuda_failures,
                        model_loaded=model_loaded,
                        ledger_health=ledger_health,
                        failure=failure,
                    ),
                )
                if (
                    failure is not None
                    or coordinator.scientific_state == LongRunState.GENERATION_FATAL
                ):
                    break
                time.sleep(poll_seconds)
            if stop_requested and coordinator.scientific_state != LongRunState.GENERATION_FATAL:
                coordinator.fail("UNKNOWN", at=datetime.now(UTC), detail="candidate stop requested")
                state = LongRunState.GENERATION_FATAL.value
            elif coordinator.scientific_state != LongRunState.GENERATION_FATAL:
                terminal_at = validate_utc_clock_progress(
                    previous_clock,
                    datetime.now(UTC),
                    component="candidate",
                )
                if coordinator.state == LongRunState.TERMINALIZING:
                    coordinator.transition(
                        LongRunState.DEADLINE_REACHED,
                        event_type="CANDIDATE_TERMINAL",
                        at=terminal_at,
                    )
                else:
                    coordinator.transition(
                        LongRunState.TERMINALIZING,
                        event_type="DEADLINE_REACHED",
                        at=terminal_at,
                    )
                    coordinator.transition(
                        LongRunState.DEADLINE_REACHED,
                        event_type="CANDIDATE_TERMINAL",
                        at=terminal_at,
                    )
                state = LongRunState.DEADLINE_REACHED.value
        finally:
            signal.signal(signal.SIGTERM, old_term)
            signal.signal(signal.SIGINT, old_int)
        result = _status(
            preregistration=preregistration,
            preregistration_sha256=preregistration_sha256,
            repository_root=repository_root,
            ledger=ledger,
            coordinator=coordinator,
            identity=identity,
            state=state,
            last_cutoff=last_cutoff,
            rejection_count=rejection_count,
            schema_failures=schema_failures,
            nan_inf_failures=nan_inf_failures,
            model_load_failures=model_load_failures,
            conflicting_ledger_writes=conflicting_ledger_writes,
            cuda_failures=cuda_failures,
            model_loaded=model_loaded,
            ledger_health=ledger_health,
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
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--qualification-evidence", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--coordinator-root", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    parser.add_argument("--worker-timeout-seconds", type=float, default=WORKER_TIMEOUT_SECONDS)
    parser.add_argument(
        "--resume", action="store_true", help="resume only after explicit identity validation"
    )
    args = parser.parse_args()
    try:
        result = run(
            real=args.real,
            repository_root=args.repository_root,
            preregistration_path=args.preregistration,
            preregistration_sha256=args.preregistration_sha256,
            admission_path=args.admission,
            qualification_evidence_path=args.qualification_evidence,
            source_root=args.source_root,
            run_root=args.run_root,
            coordinator_root=args.coordinator_root,
            poll_seconds=args.poll_seconds,
            worker_timeout_seconds=args.worker_timeout_seconds,
            resume=args.resume,
        )
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise SystemExit(f"long-run Chronos run refused ({type(exc).__name__})") from exc
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["state"] == LongRunState.DEADLINE_REACHED.value else 1


if __name__ == "__main__":
    raise SystemExit(main())
