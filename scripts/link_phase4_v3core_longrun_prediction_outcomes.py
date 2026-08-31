#!/usr/bin/env python3
"""Link genuine twelve-bar outcomes for long-run predictions."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from advisorai.phase4.v3core_canary import sha256_file
from advisorai.phase4.v3core_longrun_runtime import (
    LONG_RUN_EVIDENCE_CLASS,
    LongRunCaseState,
    LongRunCoordinator,
    LongRunOutcomeLinkLedger,
    LongRunPredictionLedger,
    LongRunPreregistration,
    LongRunState,
    collect_runtime_attestation,
    load_long_run_preregistration,
    prediction_deadline,
    process_command_identity,
    process_create_time,
    process_identity_matches,
    read_json_stable,
    read_long_run_normalized_bars_for_start,
    validate_utc_clock_progress,
    write_json_atomic,
)

RUN_SCHEMA = "advisorai.phase4.v3-core.long-run.outcomes.v1"
POLL_SECONDS = 30.0


def _bar_hash(bar: object) -> str:
    payload = bar.model_dump(mode="json")
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validate_source_status(
    source_status: dict[str, object],
    preregistration: LongRunPreregistration,
    preregistration_sha256: str,
) -> dict[str, object]:
    """Validate the current source snapshot before every linking pass."""

    if (
        source_status.get("generation_id") != preregistration.generation_id
        or source_status.get("preregistration_sha256") != preregistration_sha256
        or source_status.get("evidence_class") != LONG_RUN_EVIDENCE_CLASS
        or source_status.get("admission_eligible") is not False
        or source_status.get("phase4_materialization_eligible") is not False
        or source_status.get("source_snapshot_hash") != preregistration.source_snapshot_sha256
        or source_status.get("runtime_attestation_sha256")
        != preregistration.runtime_attestation_sha256
        or source_status.get("collector_code_sha256") != preregistration.collector_code_sha256
        or source_status.get("finality_code_sha256") != preregistration.finality_rule_sha256
        or source_status.get("endpoint") != preregistration.rest_endpoint
        or source_status.get("interval") != preregistration.interval
        or source_status.get("repository_commit") != preregistration.repository_commit
        or source_status.get("credentials_loaded") is not False
        or source_status.get("order_writes_attempted") is not False
        or source_status.get("execution_authority_present") is not False
    ):
        raise ValueError(
            "outcome linker source identity or security flags differ from the preregistration"
        )
    admitted_counts = source_status.get("admitted_final_bar_count")
    if not isinstance(admitted_counts, dict) or any(
        not isinstance(admitted_counts.get(symbol), int)
        or isinstance(admitted_counts.get(symbol), bool)
        or admitted_counts.get(symbol, -1) < 0
        for symbol in preregistration.symbols
    ):
        raise RuntimeError("source admitted-bar counts are missing or invalid")
    return admitted_counts


def _status(
    *,
    preregistration: LongRunPreregistration,
    preregistration_sha256: str,
    ledger: LongRunPredictionLedger,
    links: LongRunOutcomeLinkLedger,
    coordinator: LongRunCoordinator,
    repository_commit: str,
    state: str,
    failure: str | None = None,
) -> dict[str, object]:
    excluded_prediction_ids = {
        prediction_id
        for state, prediction_id, _outcome_id in coordinator.case_states().values()
        if state == LongRunCaseState.EXCLUDED and prediction_id is not None
    }
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
        "repository_commit": repository_commit,
        "preregistration_sha256": preregistration_sha256,
        "runtime_attestation_sha256": preregistration.runtime_attestation_sha256,
        "source_snapshot_hash": preregistration.source_snapshot_sha256,
        "outcome_linker_code_sha256": sha256_file(Path(__file__).resolve()),
        "prediction_count": len(ledger.entries),
        "pending_durability_count": len(ledger.pending_durability_keys),
        "outcome_link_count": len(links.records),
        "pending_outcome_count": sum(
            entry.prediction_id not in links.by_prediction
            and entry.prediction_id not in excluded_prediction_ids
            for entry in ledger.entries
        ),
        "scientific_state": coordinator.scientific_state.value,
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "execution_authority_present": False,
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    if failure is not None:
        result["failure"] = failure
    return result


def _link_unlocked(
    *,
    repository_root: Path,
    preregistration_path: Path,
    preregistration_sha256: str,
    source_root: Path,
    candidate_root: Path,
    coordinator_root: Path,
    poll_seconds: float = POLL_SECONDS,
) -> dict[str, object]:
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    preregistration = load_long_run_preregistration(
        preregistration_path, expected_sha256=preregistration_sha256
    )
    repository_root = repository_root.resolve()
    head = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if head != preregistration.repository_commit:
        raise ValueError("outcome linker repository identity differs from preregistration")
    if sha256_file(Path(__file__).resolve()) != preregistration.outcome_linker_code_sha256:
        raise ValueError("outcome linker code identity differs from preregistration")
    if collect_runtime_attestation().attestation_hash != preregistration.runtime_attestation_sha256:
        raise ValueError("outcome linker runtime attestation differs from the preregistration")
    source_root = source_root.resolve()
    candidate_root = candidate_root.resolve()
    prior_status = (
        read_json_stable(candidate_root / "outcome-status.json")
        if (candidate_root / "outcome-status.json").exists()
        else None
    )
    if prior_status is None and (candidate_root / "outcome-links.jsonl").exists():
        raise RuntimeError(
            "outcome append-only evidence exists without a status snapshot; resume is ambiguous"
        )
    if prior_status is not None and str(prior_status.get("state")) in {
        LongRunState.RUNNING.value,
        LongRunState.RUNNING_WARMUP.value,
    }:
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
            raise RuntimeError("existing active outcome linker lacks a process identity")
        if process_identity_matches(
            old_pid,
            old_identity,
            [str(item) for item in old_command],
            expected_process_create_time=float(old_create_time),
        ):
            raise RuntimeError(
                "existing active outcome linker is still alive; silent restart is forbidden"
            )
    coordinator = LongRunCoordinator(preregistration, coordinator_root.resolve() / "events.jsonl")
    source_status = read_json_stable(source_root / "status.json")
    _validate_source_status(source_status, preregistration, preregistration_sha256)
    terminal_states = {
        LongRunState.DEADLINE_REACHED.value,
        LongRunState.TERMINALIZING.value,
        LongRunState.COMPLETED_PENDING_AUDIT.value,
        LongRunState.AUDITED.value,
    }
    if (
        prior_status is not None
        and str(prior_status.get("state")) == LongRunState.GENERATION_FATAL.value
    ):
        raise RuntimeError("a previously fatal outcome linker cannot be restarted")
    if prior_status is not None and str(prior_status.get("state")) in terminal_states:
        return prior_status
    state = (
        LongRunState.RUNNING_WARMUP.value
        if coordinator.scientific_state == LongRunState.RUNNING_WARMUP
        else LongRunState.RUNNING.value
    )
    failure: str | None = None
    previous_clock: datetime | None = None
    while datetime.now(UTC) < preregistration.terminal_deadline:
        try:
            now = validate_utc_clock_progress(
                previous_clock,
                datetime.now(UTC),
                component="outcome_linker",
            )
        except RuntimeError as exc:
            coordinator.fail("UNKNOWN", at=datetime.now(UTC), detail=str(exc))
            failure = f"OUTCOME_CLOCK_ERROR:{exc}"
            state = LongRunState.GENERATION_FATAL.value
            break
        previous_clock = now
        if coordinator.scientific_state == LongRunState.GENERATION_FATAL:
            failure = "COORDINATOR_FATAL_LATCHED"
            state = LongRunState.GENERATION_FATAL.value
            break
        source_status = read_json_stable(source_root / "status.json")
        admitted_counts = _validate_source_status(
            source_status, preregistration, preregistration_sha256
        )
        # Candidate entries are appended by a separate process.  Rebuild both
        # projections on every pass so predictions created after this process
        # started cannot be missed, and so a crash between a link append and
        # its coordinator event is recoverable from append-only truth.
        ledger = LongRunPredictionLedger(
            candidate_root / "predictions.jsonl",
            candidate_root / "prediction-durability.jsonl",
            preregistration.mandatory_cutoffs,
        )
        ledger.validate_against_preregistration(preregistration)
        links = LongRunOutcomeLinkLedger(
            candidate_root / "outcome-links.jsonl", preregistration.mandatory_cutoffs
        )
        bars = read_long_run_normalized_bars_for_start(
            source_root,
            expected_admitted_final_bars=sum(
                int(admitted_counts[symbol]) for symbol in preregistration.symbols
            ),
        )
        by_key = {(bar.instrument, bar.interval_end): bar for bar in bars}
        for entry in ledger.entries:
            case = coordinator.case_states().get((entry.symbol, entry.cutoff_ordinal))
            durability = ledger.durability_for(entry)
            if durability is None:
                # The prediction entry is fsync'd before its follow-up
                # durability attestation.  A linker pass can therefore
                # legitimately observe the narrow crash-consistency window
                # between those two append-only writes.  Leave the entry
                # uncertified for this pass; terminal auditing still fails
                # closed if the attestation never appears, and the watchdog
                # observes a dead/failed candidate rather than accepting a
                # missing durability claim.
                continue
            if durability.durable_appended_at > prediction_deadline(entry.cutoff):
                # A crash can occur after the durability attestation but before
                # the candidate records its exclusion.  The outcome linker
                # must not turn that late record into a clean case.
                if case is None:
                    coordinator.record_case_excluded(
                        symbol=entry.symbol,
                        ordinal=entry.cutoff_ordinal,
                        cutoff=entry.cutoff,
                        reason="CASE_EXCLUDED_LATE_PREDICTION",
                        at=datetime.now(UTC),
                        prediction_id=entry.prediction_id,
                    )
                    case = coordinator.case_states().get((entry.symbol, entry.cutoff_ordinal))
                    if coordinator.scientific_state == LongRunState.GENERATION_FATAL:
                        failure = "COVERAGE_MATHEMATICALLY_IMPOSSIBLE"
                        state = LongRunState.GENERATION_FATAL.value
                        break
                if (
                    case is None
                    or case[0] != LongRunCaseState.EXCLUDED
                    or case[1] != entry.prediction_id
                    or entry.prediction_id in links.by_prediction
                ):
                    coordinator.fail(
                        "PREDICTION_LEDGER_CONFLICT",
                        at=datetime.now(UTC),
                        detail="late prediction is not consistently excluded",
                    )
                    failure = "LATE_PREDICTION_STATE_CONFLICT"
                    state = LongRunState.GENERATION_FATAL.value
                    break
                continue
            if case is not None and case[0] == LongRunCaseState.EXCLUDED:
                # A late or otherwise excluded prediction is retained as
                # immutable evidence of the opportunity, but can never become
                # a clean case and must not acquire a future outcome link.
                if case[1] != entry.prediction_id or case[2] is not None:
                    coordinator.fail(
                        "PREDICTION_LEDGER_CONFLICT",
                        at=datetime.now(UTC),
                        detail="excluded case identity conflicts with its prediction",
                    )
                    failure = "EXCLUDED_CASE_IDENTITY_CONFLICT"
                    state = LongRunState.GENERATION_FATAL.value
                    break
                continue
            existing_link = links.by_prediction.get(entry.prediction_id)
            if existing_link is not None:
                # A crash can occur after the outcome append and before the
                # coordinator projection is advanced.  Reconstruct the
                # pending case from the immutable prediction/link pair, then
                # finish the clean transition idempotently.
                try:
                    case = coordinator.case_states().get((entry.symbol, entry.cutoff_ordinal))
                    if case is None:
                        coordinator.record_case_pending(
                            symbol=entry.symbol,
                            ordinal=entry.cutoff_ordinal,
                            cutoff=entry.cutoff,
                            prediction_id=entry.prediction_id,
                            at=existing_link.linked_at,
                        )
                    coordinator.record_case_clean(
                        symbol=entry.symbol,
                        ordinal=entry.cutoff_ordinal,
                        cutoff=entry.cutoff,
                        prediction_id=entry.prediction_id,
                        outcome_case_id=existing_link.outcome_case_id,
                        at=existing_link.linked_at,
                    )
                except (RuntimeError, ValueError) as exc:
                    coordinator.fail(
                        "PREDICTION_LEDGER_CONFLICT", at=datetime.now(UTC), detail=str(exc)
                    )
                    failure = f"OUTCOME_STATE_RECONSTRUCTION_ERROR:{type(exc).__name__}"
                    state = LongRunState.GENERATION_FATAL.value
                    break
                continue
            times = tuple(
                entry.cutoff + timedelta(seconds=300 * (index + 1))
                for index in range(preregistration.outcome_bars)
            )
            future = tuple(by_key.get((entry.symbol, interval_end)) for interval_end in times)
            if any(bar is None for bar in future):
                continue
            resolved = tuple(bar for bar in future if bar is not None)
            if any(bar.evidence_class != "forward_pit_admission" for bar in resolved):
                failure = "OUTCOME_SOURCE_NOT_ADMITTED_FINAL"
                coordinator.fail("ADMITTED_IDENTITY_FAILURE", at=datetime.now(UTC), detail=failure)
                state = LongRunState.GENERATION_FATAL.value
                break
            hashes = tuple(_bar_hash(bar) for bar in resolved)
            outcome_case_id = sha256(
                json.dumps(
                    {"prediction_id": entry.prediction_id, "source_bar_hashes": hashes},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            try:
                case = coordinator.case_states().get((entry.symbol, entry.cutoff_ordinal))
                if case is None:
                    coordinator.record_case_pending(
                        symbol=entry.symbol,
                        ordinal=entry.cutoff_ordinal,
                        cutoff=entry.cutoff,
                        prediction_id=entry.prediction_id,
                        at=entry.inference_finished_at,
                    )
                elif case != (
                    LongRunCaseState.PENDING_OUTCOME,
                    entry.prediction_id,
                    None,
                ):
                    raise RuntimeError("outcome link does not match the existing pending case")
                linked_at = datetime.now(UTC)
                if linked_at > preregistration.terminal_deadline:
                    coordinator.record_case_excluded(
                        symbol=entry.symbol,
                        ordinal=entry.cutoff_ordinal,
                        cutoff=entry.cutoff,
                        reason="MISSING_OUTCOME_AT_DEADLINE",
                        at=linked_at,
                        prediction_id=entry.prediction_id,
                    )
                    continue
                links.append(
                    generation_id=preregistration.generation_id,
                    prediction_id=entry.prediction_id,
                    symbol=entry.symbol,
                    cutoff=entry.cutoff,
                    cutoff_ordinal=entry.cutoff_ordinal,
                    outcome_case_id=outcome_case_id,
                    source_interval_ends=times,
                    source_bar_hashes=hashes,
                    linked_at=linked_at,
                )
                coordinator.record_case_clean(
                    symbol=entry.symbol,
                    ordinal=entry.cutoff_ordinal,
                    cutoff=entry.cutoff,
                    prediction_id=entry.prediction_id,
                    outcome_case_id=outcome_case_id,
                    at=linked_at,
                )
            except (RuntimeError, ValueError) as exc:
                coordinator.fail(
                    "PREDICTION_LEDGER_CONFLICT", at=datetime.now(UTC), detail=str(exc)
                )
                failure = f"OUTCOME_LINK_CONTRACT_ERROR:{type(exc).__name__}"
                state = LongRunState.GENERATION_FATAL.value
                break
        write_json_atomic(
            candidate_root / "outcome-status.json",
            _status(
                preregistration=preregistration,
                preregistration_sha256=preregistration_sha256,
                ledger=ledger,
                links=links,
                coordinator=coordinator,
                repository_commit=head,
                state=state,
                failure=failure,
            ),
        )
        if failure is not None:
            break
        if coordinator.scientific_state == LongRunState.RUNNING:
            state = LongRunState.RUNNING.value
        time.sleep(poll_seconds)
    if failure is None:
        state = LongRunState.DEADLINE_REACHED.value
    ledger = LongRunPredictionLedger(
        candidate_root / "predictions.jsonl",
        candidate_root / "prediction-durability.jsonl",
        preregistration.mandatory_cutoffs,
    )
    links = LongRunOutcomeLinkLedger(
        candidate_root / "outcome-links.jsonl", preregistration.mandatory_cutoffs
    )
    result = _status(
        preregistration=preregistration,
        preregistration_sha256=preregistration_sha256,
        ledger=ledger,
        links=links,
        coordinator=coordinator,
        repository_commit=head,
        state=state,
        failure=failure,
    )
    write_json_atomic(candidate_root / "outcome-status.json", result)
    return result


def link(**kwargs: object) -> dict[str, object]:
    """Run the linker under a single-instance lock for its candidate root."""

    candidate_root = Path(str(kwargs["candidate_root"])).resolve()
    candidate_root.mkdir(parents=True, exist_ok=True)
    lock_path = candidate_root / "outcome.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another long-run outcome linker owns this root") from exc
        return _link_unlocked(**kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--preregistration-sha256", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--coordinator-root", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    args = parser.parse_args()
    try:
        result = link(
            preregistration_path=args.preregistration,
            repository_root=args.repository_root,
            preregistration_sha256=args.preregistration_sha256,
            source_root=args.source_root,
            candidate_root=args.candidate_root,
            coordinator_root=args.coordinator_root,
            poll_seconds=args.poll_seconds,
        )
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"long-run outcome linking refused ({type(exc).__name__})") from exc
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["state"] != LongRunState.GENERATION_FATAL.value else 1


if __name__ == "__main__":
    raise SystemExit(main())
