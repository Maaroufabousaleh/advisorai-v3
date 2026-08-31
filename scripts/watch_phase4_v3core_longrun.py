#!/usr/bin/env python3
"""Monitor one long-run using append-only coordinator truth and stable snapshots."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from advisorai.phase4.v3core_canary import sha256_file
from advisorai.phase4.v3core_longrun_runtime import (
    LONG_RUN_EVIDENCE_CLASS,
    LongRunCaseState,
    LongRunCoordinator,
    LongRunFinalityTracker,
    LongRunIncident,
    LongRunOutcomeLinkLedger,
    LongRunPredictionLedger,
    LongRunPreregistration,
    LongRunRawSpool,
    LongRunState,
    collect_runtime_attestation,
    fresh_long_run_minimum_interval_end,
    load_long_run_preregistration,
    prediction_deadline,
    process_command_identity,
    process_create_time,
    process_identity_matches,
    read_json_stable,
    read_normalized_bars_stable,
    write_json_atomic,
)

RUN_SCHEMA = "advisorai.phase4.v3-core.long-run.watchdog.v1"
POLL_SECONDS = 30.0
STATUS_MAX_AGE_SECONDS = 120.0
TERMINAL_STATES = frozenset(
    {
        LongRunState.DEADLINE_REACHED.value,
        LongRunState.TERMINALIZING.value,
        LongRunState.GENERATION_FATAL.value,
        LongRunState.COMPLETED_PENDING_AUDIT.value,
        LongRunState.AUDITED.value,
    }
)


def _reason_incident(reason: str) -> LongRunIncident:
    if "watchdog" in reason:
        return LongRunIncident.WATCHDOG_PROCESS_DEATH
    if "post_admission" in reason:
        return LongRunIncident.POST_ADMISSION_REVISION
    if "credential" in reason:
        return LongRunIncident.CREDENTIAL_DETECTED
    if "order" in reason:
        return LongRunIncident.ORDER_WRITE_DETECTED
    if "cuda" in reason:
        return LongRunIncident.CUDA_FAILURE
    if "identity" in reason or "repository" in reason:
        return LongRunIncident.IDENTITY_MISMATCH
    if "ledger" in reason:
        return LongRunIncident.PREDICTION_LEDGER_CONFLICT
    if "mandatory_cutoff" in reason or "cutoff_unaccounted" in reason:
        return LongRunIncident.MISSED_MANDATORY_CUTOFF
    if "missing" in reason or "dead" in reason:
        return LongRunIncident.UNKNOWN
    return LongRunIncident.UNKNOWN


# A rejection is a case-level loss: the coordinator records the affected
# opportunity as EXCLUDED and the 80/64 feasibility calculation decides
# whether the generation can continue. Everything else is fail-closed unless
# it is explicitly listed here. This prevents a broad exception or an
# unreviewed warning from silently becoming a healthy continuation.
_CASE_LEVEL_WARNING_REASONS = frozenset(
    {
        "candidate_rejections_observed",
        "candidate_consecutive_failures_observed",
    }
)


def _is_generation_fatal_reason(reason: str) -> bool:
    """Classify monitor observations without downgrading unknown faults."""

    return reason not in _CASE_LEVEL_WARNING_REASONS


def _git_head(repository_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _check_process(
    status: dict[str, object], *, component: str, now: datetime, deadline: datetime
) -> list[str]:
    state = str(status.get("state"))
    if state in TERMINAL_STATES:
        # An ordinary terminal state is only valid at the frozen deadline.
        # Otherwise a premature exit with a terminal-looking status could be
        # mistaken for a healthy handoff merely because its PID is gone.
        if state != LongRunState.GENERATION_FATAL.value and now < deadline:
            return [f"{component}_terminated_before_deadline"]
        return []
    pid = status.get("pid")
    command = status.get("command")
    command_identity = status.get("command_identity")
    process_time = status.get("process_create_time")
    if not (
        isinstance(pid, int)
        and isinstance(command, list)
        and isinstance(command_identity, str)
        and isinstance(process_time, (int, float))
    ):
        return [f"{component}_missing_process_identity"]
    if now < deadline and not process_identity_matches(
        pid,
        command_identity,
        [str(item) for item in command],
        expected_process_create_time=float(process_time),
    ):
        return [f"{component}_process_identity_missing_or_mismatched"]
    return []


def _same_watchdog_process(status: dict[str, object]) -> bool:
    """Return true only when the status belongs to this exact watchdog PID."""

    pid = status.get("watchdog_pid")
    process_time = status.get("process_create_time")
    command = status.get("command")
    identity = status.get("command_identity")
    if not (
        isinstance(pid, int)
        and not isinstance(pid, bool)
        and pid == os.getpid()
        and isinstance(process_time, (int, float))
        and not isinstance(process_time, bool)
        and isinstance(command, list)
        and isinstance(identity, str)
    ):
        return False
    current_time = process_create_time(pid)
    current_command = [sys.executable, *sys.argv]
    return (
        current_time is not None
        and float(current_time) == float(process_time)
        and [str(item) for item in command] == current_command
        and identity == process_command_identity(current_command)
    )


def _load_source_snapshot(
    source_root: Path,
    *,
    preregistration: LongRunPreregistration,
    attempts: int = 3,
) -> tuple[LongRunRawSpool, tuple[object, ...], dict[str, object]]:
    """Read the raw and derived source projections as one validated observation.

    Raw receipts and the admitted-bar projection are separate append-only files,
    so a monitor can observe the short interval between their commits.  Retry
    only that bounded, identity-preserving race; a persistent mismatch is a
    scientific source-integrity failure.
    """

    source_root = source_root.resolve()
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            raw = LongRunRawSpool(
                source_root / "raw-receipts.jsonl",
                require_request_query=True,
                source_snapshot_hash=preregistration.source_snapshot_sha256,
            )
            normalized_path = source_root / "normalized-bars.jsonl"
            with tempfile.TemporaryDirectory(
                prefix="advisorai-longrun-watchdog-source-"
            ) as temporary:
                tracker = LongRunFinalityTracker(
                    Path(temporary) / "normalized-bars.jsonl",
                    Path(temporary) / "post-admission-revisions.jsonl",
                    minimum_interval_end=fresh_long_run_minimum_interval_end(
                        preregistration.start_at
                    ),
                    source_snapshot_hash=preregistration.source_snapshot_sha256,
                )
                tracker.replay(
                    raw.records,
                    preregistration.source_snapshot_sha256,
                    # The replay target is a disposable temporary spool; the
                    # watchdog never repairs the scientific projection on
                    # disk.  It needs the derived bars in this scratch spool
                    # to compare them with the persisted snapshot.
                    persist_missing=True,
                )
                replayed = tracker.normalized.read()
                if normalized_path.exists():
                    bars = read_normalized_bars_stable(normalized_path)
                elif replayed:
                    # Raw receipts legitimately precede the first admitted
                    # bar while finality is pending.  A missing projection is
                    # only a consistency failure once chronological replay
                    # proves that an admitted bar should exist.
                    raise FileNotFoundError(normalized_path)
                else:
                    bars = ()
                if replayed != bars:
                    raise RuntimeError("source raw/admitted projections changed during snapshot")
                metrics = tracker.metrics()
            return raw, bars, metrics
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.05)
    assert last_error is not None
    raise RuntimeError(f"source snapshot could not be validated: {last_error}") from last_error


def _load_candidate_snapshot(
    candidate_root: Path,
    *,
    preregistration: LongRunPreregistration,
    attempts: int = 3,
) -> tuple[LongRunPredictionLedger, LongRunOutcomeLinkLedger]:
    """Read both candidate ledgers and reject a persistent cross-file race."""

    candidate_root = candidate_root.resolve()
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            ledger = LongRunPredictionLedger(
                candidate_root / "predictions.jsonl",
                candidate_root / "prediction-durability.jsonl",
                preregistration.mandatory_cutoffs,
            )
            ledger.validate_against_preregistration(preregistration)
            links = LongRunOutcomeLinkLedger(
                candidate_root / "outcome-links.jsonl",
                preregistration.mandatory_cutoffs,
            )
            links.validate_against_preregistration(preregistration)
            if ledger.pending_durability_keys:
                raise RuntimeError("candidate durability attestation is pending")
            return ledger, links
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.05)
    assert last_error is not None
    raise RuntimeError(f"candidate snapshot could not be validated: {last_error}") from last_error


def _mandatory_coverage(
    preregistration: LongRunPreregistration,
    *,
    ledger: LongRunPredictionLedger,
    coordinator: LongRunCoordinator,
    now: datetime,
) -> dict[str, object]:
    """Return schedule coverage and fail-closed missing-deadline observations."""

    cases = coordinator.case_states()
    due_cutoffs = [
        ordinal
        for ordinal, cutoff in enumerate(preregistration.mandatory_cutoffs, 1)
        if now >= cutoff
    ]
    deadline_due = [
        ordinal
        for ordinal, cutoff in enumerate(preregistration.mandatory_cutoffs, 1)
        if now
        >= prediction_deadline(cutoff, lateness_seconds=preregistration.prediction_lateness_seconds)
        + timedelta(seconds=preregistration.case_accounting_grace_seconds)
    ]
    missing: list[str] = []
    predictions: dict[str, list[int]] = {symbol: [] for symbol in preregistration.symbols}
    excluded: dict[str, list[int]] = {symbol: [] for symbol in preregistration.symbols}
    for symbol in preregistration.symbols:
        for ordinal in deadline_due:
            cutoff = preregistration.mandatory_cutoffs[ordinal - 1]
            entry = ledger.for_cutoff(symbol, cutoff)
            case = cases.get((symbol, ordinal))
            if entry is not None:
                predictions[symbol].append(ordinal)
                if case is None:
                    missing.append(f"{symbol}:{ordinal}:prediction_not_accounted")
                elif case[1] != entry.prediction_id:
                    missing.append(f"{symbol}:{ordinal}:prediction_case_identity_mismatch")
            elif case is None:
                missing.append(f"{symbol}:{ordinal}:mandatory_cutoff_unaccounted")
            elif case[0].value == "EXCLUDED":
                excluded[symbol].append(ordinal)
            else:
                missing.append(f"{symbol}:{ordinal}:case_without_prediction")
    return {
        "expected_cutoffs": due_cutoffs,
        "deadline_due": deadline_due,
        "predictions": predictions,
        "excluded": excluded,
        "missing": missing,
    }


def _check_status_timestamp(
    status: dict[str, object], *, component: str, now: datetime, terminal: bool
) -> list[str]:
    if terminal:
        return []
    value = status.get("updated_at")
    if not isinstance(value, str):
        return [f"{component}_status_timestamp_missing"]
    try:
        updated_at = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (TypeError, ValueError):
        return [f"{component}_status_timestamp_malformed"]
    age = (now - updated_at).total_seconds()
    if age < -30.0 or age > STATUS_MAX_AGE_SECONDS:
        return [f"{component}_status_stale"]
    return []


def check_once(
    *,
    repository_root: Path,
    preregistration_path: Path,
    preregistration_sha256: str,
    source_root: Path,
    candidate_root: Path,
    outcome_root: Path | None = None,
    coordinator_root: Path,
    watchdog_root: Path,
) -> dict[str, object]:
    repository_root = repository_root.resolve()
    watchdog_root = watchdog_root.resolve()
    preregistration = load_long_run_preregistration(
        preregistration_path, expected_sha256=preregistration_sha256
    )
    source = read_json_stable(source_root.resolve() / "status.json")
    candidate = read_json_stable(candidate_root.resolve() / "status.json")
    outcome = (
        read_json_stable(outcome_root.resolve() / "outcome-status.json")
        if outcome_root is not None
        else None
    )
    coordinator = LongRunCoordinator(preregistration, coordinator_root.resolve() / "events.jsonl")
    raw, admitted_bars, source_metrics = _load_source_snapshot(
        source_root,
        preregistration=preregistration,
    )
    prediction_ledger, outcome_ledger = _load_candidate_snapshot(
        candidate_root,
        preregistration=preregistration,
    )
    now = datetime.now(UTC)
    reasons: list[str] = []
    prior_watchdog: dict[str, object] | None = None
    if (watchdog_root / "status.json").exists():
        prior_watchdog = read_json_stable(watchdog_root / "status.json")
        if prior_watchdog.get("generation_id") != preregistration.generation_id:
            reasons.append("watchdog_prior_generation_identity_mismatch")
        if prior_watchdog.get("preregistration_sha256") != preregistration_sha256:
            reasons.append("watchdog_prior_preregistration_identity_mismatch")
        if prior_watchdog.get("watchdog_terminal") is not True:
            prior_pid = prior_watchdog.get("watchdog_pid")
            prior_command = prior_watchdog.get("command")
            prior_identity = prior_watchdog.get("command_identity")
            prior_create_time = prior_watchdog.get("process_create_time")
            if not (
                isinstance(prior_pid, int)
                and isinstance(prior_command, list)
                and isinstance(prior_identity, str)
                and isinstance(prior_create_time, (int, float))
            ):
                reasons.append("watchdog_prior_process_identity_missing")
            elif _same_watchdog_process(prior_watchdog):
                pass
            elif not (
                process_identity_matches(
                    prior_pid,
                    prior_identity,
                    [str(item) for item in prior_command],
                    expected_process_create_time=float(prior_create_time),
                )
            ):
                # A new watchdog may not silently take over a live scientific
                # generation.  This is intentionally generation-fatal; the
                # prior append-only history remains authoritative.
                reasons.append("watchdog_process_death")
    if _git_head(repository_root) != preregistration.repository_commit:
        reasons.append("repository_identity_mismatch")
    if sha256_file(Path(__file__).resolve()) != preregistration.watchdog_code_sha256:
        reasons.append("watchdog_identity_mismatch")
    if collect_runtime_attestation().attestation_hash != preregistration.runtime_attestation_sha256:
        reasons.append("runtime_attestation_mismatch")
    statuses = [("source", source), ("candidate", candidate)]
    if outcome is not None:
        statuses.append(("outcome", outcome))
    for name, status in statuses:
        expected_common_identity = {
            "generation_id": preregistration.generation_id,
            "preregistration_sha256": preregistration_sha256,
            "runtime_attestation_sha256": preregistration.runtime_attestation_sha256,
            "source_snapshot_hash": preregistration.source_snapshot_sha256,
        }
        for key, expected in expected_common_identity.items():
            if status.get(key) != expected:
                reasons.append(f"{name}_{key}_identity_mismatch")
        if status.get("evidence_class") != LONG_RUN_EVIDENCE_CLASS:
            reasons.append(f"{name}_evidence_class_mismatch")
        if status.get("admission_eligible") is not False:
            reasons.append(f"{name}_admission_flag_mismatch")
        if status.get("credentials_loaded") is not False:
            reasons.append(f"{name}_credentials_loaded")
        if status.get("order_writes_attempted") is not False:
            reasons.append(f"{name}_order_write_attempted")
        if status.get("phase4_materialization_eligible") is not False:
            reasons.append(f"{name}_admission_materialization_flag_set")
        if status.get("repository_commit") != preregistration.repository_commit:
            reasons.append(f"{name}_repository_identity_mismatch")
        reasons.extend(
            _check_process(
                status,
                component=name,
                now=now,
                deadline=preregistration.terminal_deadline,
            )
        )
        reasons.extend(
            _check_status_timestamp(
                status,
                component=name,
                now=now,
                terminal=str(status.get("state")) in TERMINAL_STATES,
            )
        )
        if status.get("state") == LongRunState.GENERATION_FATAL.value:
            reasons.append(f"{name}_reported_generation_fatal")
        if name == "candidate":
            if status.get("ledger_health") is not True:
                reasons.append("candidate_ledger_unhealthy")
            for counter in (
                "schema_failures",
                "nan_inf_failures",
                "cuda_failures",
                "model_load_failures",
                "conflicting_ledger_writes",
            ):
                value = status.get(counter)
                if not isinstance(value, int) or value < 0:
                    reasons.append(f"candidate_{counter}_missing_or_invalid")
                elif value:
                    reasons.append(f"candidate_{counter}_nonzero")
    expected_source_identity = {
        "source_snapshot_hash": preregistration.source_snapshot_sha256,
        "collector_code_sha256": preregistration.collector_code_sha256,
        "finality_code_sha256": preregistration.finality_rule_sha256,
        "endpoint": preregistration.rest_endpoint,
        "interval": preregistration.interval,
    }
    for key, expected in expected_source_identity.items():
        if source.get(key) != expected:
            reasons.append(f"source_{key}_identity_mismatch")
    if outcome is not None:
        if outcome.get("outcome_linker_code_sha256") != preregistration.outcome_linker_code_sha256:
            reasons.append("outcome_linker_code_identity_mismatch")
    expected_source_counts = {
        "raw_receipt_count": len(raw.records),
        "admitted_final_bar_count": {
            symbol: sum(bar.instrument == symbol for bar in admitted_bars)
            for symbol in preregistration.symbols
        },
    }
    for key, expected in expected_source_counts.items():
        if source.get(key) != expected:
            reasons.append(f"source_{key}_snapshot_mismatch")
    source_finality = source.get("finality")
    if not isinstance(source_finality, dict):
        reasons.append("source_finality_metrics_missing")
    else:
        for key in (
            "raw_interval_identities",
            "admitted_final_intervals",
            "unresolved_intervals",
            "post_admission_revision_count",
        ):
            if source_finality.get(key) != source_metrics.get(key):
                reasons.append(f"source_finality_{key}_snapshot_mismatch")
    expected_prediction_counts = {
        symbol: sum(entry.symbol == symbol for entry in prediction_ledger.entries)
        for symbol in preregistration.symbols
    }
    if candidate.get("prediction_count") != len(prediction_ledger.entries):
        reasons.append("candidate_prediction_count_snapshot_mismatch")
    if candidate.get("prediction_counts") != expected_prediction_counts:
        reasons.append("candidate_prediction_counts_snapshot_mismatch")
    if candidate.get("outcome_link_count") is not None and candidate.get(
        "outcome_link_count"
    ) != len(outcome_ledger.records):
        reasons.append("candidate_outcome_count_snapshot_mismatch")
    excluded_total = sum(
        state == LongRunCaseState.EXCLUDED
        for state, _prediction_id, _outcome_id in coordinator.case_states().values()
    )
    rejection_count = candidate.get("rejection_count")
    if rejection_count != excluded_total:
        reasons.append("candidate_rejection_count_snapshot_mismatch")
    elif isinstance(rejection_count, int) and rejection_count > 0:
        reasons.append("candidate_rejections_observed")
    expected_candidate_identity = {
        "candidate_worker_code_sha256": preregistration.candidate_worker_code_sha256,
        "preprocessing_code_sha256": preregistration.preprocessing_sha256,
        "model_identity_hash": preregistration.model_identity_sha256,
        "checkpoint_hash": preregistration.checkpoint_sha256,
        "dependency_lock_hash": preregistration.requirements_lock_sha256,
    }
    for key, expected in expected_candidate_identity.items():
        if candidate.get(key) != expected:
            reasons.append(f"candidate_{key}_identity_mismatch")
    if outcome is not None and outcome.get("outcome_link_count") != len(outcome_ledger.records):
        reasons.append("outcome_link_count_snapshot_mismatch")
    if source_metrics.get("post_admission_revision_count", 0):
        reasons.append("source_post_admission_revision")
    coverage = _mandatory_coverage(
        preregistration,
        ledger=prediction_ledger,
        coordinator=coordinator,
        now=now,
    )
    reasons.extend(f"mandatory_cutoff_unaccounted:{item}" for item in coverage["missing"])
    if not all(coordinator.accounting().feasible.values()):
        reasons.append("generation_cannot_satisfy_phase4_admission")
    history_fatal = len(coordinator.fatal_events)
    if history_fatal:
        reasons.append("fatal_history_latched")
    unique_reasons = list(dict.fromkeys(reasons))
    fatal_reasons = [reason for reason in unique_reasons if _is_generation_fatal_reason(reason)]
    if fatal_reasons and not coordinator.fatal_latched:
        coordinator.fail(
            _reason_incident(fatal_reasons[0]),
            at=now,
            detail=fatal_reasons[0],
        )
    decision = (
        "GENERATION_FATAL"
        if coordinator.scientific_state == LongRunState.GENERATION_FATAL
        else "LONG_RUN_HEALTHY"
    )
    coordinator.record_watchdog_check(decision=decision, reasons=unique_reasons, at=now)
    accounting = coordinator.accounting().model_dump(mode="json")
    result: dict[str, object] = {
        "schema": RUN_SCHEMA,
        "generation_id": preregistration.generation_id,
        "preregistration_sha256": preregistration_sha256,
        "repository_commit": preregistration.repository_commit,
        "evidence_class": LONG_RUN_EVIDENCE_CLASS,
        "admission_eligible": False,
        "phase4_materialization_eligible": False,
        "decision": decision,
        "scientific_state": coordinator.scientific_state.value,
        "fatal_history_count": len(coordinator.fatal_events),
        "reasons": unique_reasons,
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "runtime_attestation_sha256": preregistration.runtime_attestation_sha256,
        "source_snapshot_hash": preregistration.source_snapshot_sha256,
        "source": source,
        "candidate": candidate,
        "outcome": outcome,
        "source_snapshot": {
            "raw_receipts": len(raw.records),
            "admitted_final_bars": {
                symbol: sum(bar.instrument == symbol for bar in admitted_bars)
                for symbol in preregistration.symbols
            },
            "finality": source_metrics,
        },
        "candidate_snapshot": {
            "predictions": expected_prediction_counts,
            "outcomes": {
                symbol: sum(link.symbol == symbol for link in outcome_ledger.records)
                for symbol in preregistration.symbols
            },
        },
        "coverage": coverage,
        "accounting": accounting,
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "execution_authority_present": False,
        "watchdog_pid": os.getpid(),
        "process_create_time": process_create_time(os.getpid()),
        "command": [sys.executable, *sys.argv],
        "command_identity": process_command_identity([sys.executable, *sys.argv]),
        "watchdog_code_sha256": sha256_file(Path(__file__).resolve()),
    }
    watchdog_root.mkdir(parents=True, exist_ok=True)
    result["watchdog_terminal"] = False
    write_json_atomic(watchdog_root / "status.json", result)
    return result


def _record_watchdog_input_failure(kwargs: dict[str, object], exc: Exception) -> dict[str, object]:
    """Latch an input/read failure and publish its exact operation/path.

    A watchdog exception must not degrade into a silent process exit: the
    scheduler and a later read-only checker need an append-only fatal signal.
    This helper never retries the experiment or changes any source/candidate
    evidence.
    """

    now = datetime.now(UTC)
    preregistration_path = Path(str(kwargs["preregistration"])).resolve()
    preregistration_sha256 = str(kwargs["preregistration_sha256"])
    watchdog_root = Path(str(kwargs["watchdog_root"])).resolve()
    reason = f"watchdog_input_error:{type(exc).__name__}:{exc}"
    fatal_count = 0
    scientific_state = LongRunState.GENERATION_FATAL.value
    try:
        preregistration = load_long_run_preregistration(
            preregistration_path,
            expected_sha256=preregistration_sha256,
        )
        coordinator = LongRunCoordinator(
            preregistration,
            Path(str(kwargs["coordinator_root"])).resolve() / "events.jsonl",
        )
        coordinator.fail(
            LongRunIncident.WATCHDOG_PROCESS_DEATH,
            at=now,
            detail=reason,
        )
        coordinator.record_watchdog_check(
            decision="GENERATION_FATAL",
            reasons=(reason,),
            at=now,
        )
        fatal_count = len(coordinator.fatal_events)
        scientific_state = coordinator.scientific_state.value
        generation_id = preregistration.generation_id
        repository_commit = preregistration.repository_commit
        code_hash = sha256_file(Path(__file__).resolve())
    except Exception:
        generation_id = "watchdog-input-failure"
        repository_commit = "0" * 40
        code_hash = sha256(b"watchdog-input-failure").hexdigest()
    result: dict[str, object] = {
        "schema": RUN_SCHEMA,
        "generation_id": generation_id,
        "evidence_class": LONG_RUN_EVIDENCE_CLASS,
        "admission_eligible": False,
        "phase4_materialization_eligible": False,
        "decision": "GENERATION_FATAL",
        "scientific_state": scientific_state,
        "fatal_history_count": fatal_count,
        "reasons": [reason],
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "source": None,
        "candidate": None,
        "outcome": None,
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "execution_authority_present": False,
        "watchdog_pid": os.getpid(),
        "process_create_time": process_create_time(os.getpid()),
        "command": [sys.executable, *sys.argv],
        "command_identity": process_command_identity([sys.executable, *sys.argv]),
        "watchdog_code_sha256": code_hash,
        "watchdog_terminal": False,
    }
    if generation_id != "watchdog-input-failure":
        result["preregistration_sha256"] = preregistration_sha256
        result["repository_commit"] = repository_commit
        result["runtime_attestation_sha256"] = preregistration.runtime_attestation_sha256
        result["source_snapshot_hash"] = preregistration.source_snapshot_sha256
    watchdog_root.mkdir(parents=True, exist_ok=True)
    write_json_atomic(watchdog_root / "status.json", result)
    return result


def _run_unlocked(**kwargs: object) -> dict[str, object]:
    poll_seconds = float(kwargs.pop("poll_seconds", POLL_SECONDS))
    once = bool(kwargs.pop("once", False))
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    result: dict[str, object] = {}
    while True:
        try:
            result = check_once(**kwargs)  # type: ignore[arg-type]
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
            json.JSONDecodeError,
        ) as exc:
            return _record_watchdog_input_failure(dict(kwargs), exc)
        if once or result["decision"] == "GENERATION_FATAL":
            return result
        preregistration = load_long_run_preregistration(
            Path(kwargs["preregistration"]),
            expected_sha256=str(kwargs["preregistration_sha256"]),
        )
        if datetime.now(UTC) >= preregistration.terminal_check_at:
            # Publish an explicit terminal marker only after the final
            # read-only observation has been durably written.  A later
            # watchdog process cannot mistake a normal handoff for a crash.
            result = dict(result)
            result["watchdog_terminal"] = True
            result["watchdog_terminal_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            write_json_atomic(Path(kwargs["watchdog_root"]).resolve() / "status.json", result)
            return result
        time.sleep(poll_seconds)


def run(**kwargs: object) -> dict[str, object]:
    """Run one watchdog instance for the scientific evidence root.

    A second watchdog would create a competing status writer and could make
    process-health observations non-reproducible.  The lock is an operational
    single-instance guard; fatal history remains in the coordinator event log.
    """

    watchdog_root = Path(str(kwargs["watchdog_root"])).resolve()
    watchdog_root.mkdir(parents=True, exist_ok=True)
    lock_path = watchdog_root / "watchdog.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another long-run watchdog owns this root") from exc
        return _run_unlocked(**kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--preregistration-sha256", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--outcome-root", type=Path)
    parser.add_argument("--coordinator-root", type=Path, required=True)
    parser.add_argument("--watchdog-root", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        result = run(
            repository_root=args.repository_root,
            preregistration=args.preregistration,
            preregistration_sha256=args.preregistration_sha256,
            source_root=args.source_root,
            candidate_root=args.candidate_root,
            outcome_root=args.outcome_root,
            coordinator_root=args.coordinator_root,
            watchdog_root=args.watchdog_root,
            poll_seconds=args.poll_seconds,
            once=args.once,
        )
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"long-run watchdog refused ({type(exc).__name__})") from exc
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["decision"] == "LONG_RUN_HEALTHY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
