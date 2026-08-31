from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode

from advisorai.collectors.sources import HttpResponse
from advisorai.phase4.v3core_cadence import (
    V3_CORE_MARKET_DATA_REST_BASE,
    V3_CORE_MARKET_DATA_REST_ENDPOINT,
    V3_CORE_SYMBOLS,
)
from advisorai.phase4.v3core_chronos import CHRONOS_PREPROCESSING_IDENTITY, _input_snapshot_hash
from advisorai.phase4.v3core_forward import ForwardPredictionRecord
from advisorai.phase4.v3core_longrun import (
    QUALIFIED_CHRONOS_CHECKPOINT_SHA256,
    QUALIFIED_PHASE3_GATE_SHA256,
    QUALIFIED_REQUIREMENTS_LOCK_SHA256,
    QUALIFIED_UV_LOCK_SHA256,
    derive_first_long_run_cutoff,
    derive_long_run_cutoffs,
    estimate_long_run_terminal_deadline,
)
from advisorai.phase4.v3core_longrun_runtime import (
    LONG_RUN_EVIDENCE_CLASS,
    LongRunCoordinator,
    LongRunFinalityTracker,
    LongRunOutcomeLinkLedger,
    LongRunPredictionLedger,
    LongRunPreregistration,
    LongRunRawReceipt,
    LongRunRawSpool,
    LongRunState,
    _source_bar_hash,
    audit_long_run,
    derive_long_run_source_snapshot_sha256,
    fresh_long_run_minimum_interval_end,
    long_run_audit_report_sha256,
    long_run_context_for_cutoff,
    long_run_preregistration_sha256,
    prediction_deadline,
    process_command_identity,
    write_json_atomic,
)

HASH = "a" * 64
REPOSITORY_ROOT = Path(__file__).parents[2]
COMMIT = subprocess.run(
    ["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
START = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)


def _preregistration() -> LongRunPreregistration:
    first = derive_first_long_run_cutoff(START)
    cutoffs = derive_long_run_cutoffs(START)
    source_snapshot = derive_long_run_source_snapshot_sha256(
        generation_id="synthetic-terminal-audit",
        repository_commit=COMMIT,
        start_at=START,
        first_mandatory_cutoff_at=first,
        mandatory_cutoffs=cutoffs,
        source_provider="binance_spot_public_market_data",
        rest_endpoint=V3_CORE_MARKET_DATA_REST_ENDPOINT,
        interval="5m",
        finality_rule_id="v3core-admitted-final-60s-two-distinct-receipts-v1",
        finality_rule_sha256=HASH,
        context_rule_id="v3core-48-admitted-final-newest-minus-10m-v1",
        context_rule_sha256=HASH,
    )
    return LongRunPreregistration(
        generation_id="synthetic-terminal-audit",
        branch_or_tag="test",
        repository_commit=COMMIT,
        created_at=START - timedelta(hours=1),
        start_at=START,
        first_mandatory_cutoff_at=first,
        mandatory_cutoffs=cutoffs,
        finality_rule_sha256=HASH,
        context_rule_sha256=HASH,
        preprocessing_sha256=HASH,
        collector_code_sha256=HASH,
        candidate_worker_code_sha256=HASH,
        outcome_linker_code_sha256=HASH,
        watchdog_code_sha256=HASH,
        auditor_code_sha256=HASH,
        scheduler_code_sha256=HASH,
        coordinator_code_sha256=HASH,
        launcher_code_sha256=HASH,
        long_run_contract_code_sha256=HASH,
        forward_contract_code_sha256=HASH,
        cadence_contract_code_sha256=HASH,
        model_runtime_qualification_sha256=HASH,
        source_snapshot_sha256=source_snapshot,
        runtime_attestation_sha256=HASH,
        checkpoint_sha256=QUALIFIED_CHRONOS_CHECKPOINT_SHA256,
        phase3_gate_sha256=QUALIFIED_PHASE3_GATE_SHA256,
        uv_lock_sha256=QUALIFIED_UV_LOCK_SHA256,
        requirements_lock_sha256=QUALIFIED_REQUIREMENTS_LOCK_SHA256,
        terminal_deadline=estimate_long_run_terminal_deadline(START),
        terminal_check_at=estimate_long_run_terminal_deadline(START) + timedelta(minutes=5),
    )


def _request_url(symbol: str) -> str:
    query = urlencode({"interval": "5m", "limit": 1, "symbol": symbol})
    return f"{V3_CORE_MARKET_DATA_REST_BASE}/api/v3/klines?{query}"


def _payload(interval_end: datetime, close: str = "100") -> bytes:
    start_ms = int((interval_end - timedelta(minutes=5)).timestamp()) * 1000
    close_ms = int(interval_end.timestamp()) * 1000 - 1
    low = "98" if close != "100" else "99"
    row = [
        start_ms,
        "100",
        "101",
        low,
        close,
        "1",
        close_ms,
        "100",
        1,
        "0",
        "0",
        "0",
    ]
    return json.dumps([row], separators=(",", ":")).encode()


def _build_raw_fixture(root: Path, preregistration: LongRunPreregistration) -> LongRunRawSpool:
    """Build a complete 80-opportunity raw fixture without bypassing receipt validation."""

    path = root / "raw-receipts.jsonl"
    records: list[LongRunRawReceipt] = []
    previous: str | None = None
    sequence = 0
    bars_needed = 1020  # 5m bars from launch+5m through the final 1h outcome bar.
    for index in range(bars_needed):
        interval_end = preregistration.start_at + timedelta(minutes=5 * (index + 1))
        for symbol in V3_CORE_SYMBOLS:
            contents = (
                ("99", "100", "100") if symbol == "ETHUSDT" and index == 20 else ("100", "100")
            )
            for receipt_index, close in enumerate(contents):
                sequence += 1
                collected_at = interval_end + timedelta(
                    seconds=61 + receipt_index + (2 if symbol == "ETHUSDT" else 0)
                )
                url = _request_url(symbol)
                response = HttpResponse(
                    status_code=200,
                    body=_payload(interval_end, close),
                    fetched_at=collected_at,
                    url=url,
                )
                record = LongRunRawReceipt.from_response(
                    response,
                    sequence=sequence,
                    symbol=symbol,
                    request_url=url,
                    request_attempt_id=f"synthetic-attempt-{sequence}",
                    source_snapshot_hash=preregistration.source_snapshot_sha256,
                    previous_record_hash=previous,
                )
                records.append(record)
                previous = record.record_hash
    path.write_text(
        "".join(record.model_dump_json() + "\n" for record in records), encoding="utf-8"
    )
    (root / "post-admission-revisions.jsonl").write_text("", encoding="utf-8")
    return LongRunRawSpool(
        path,
        require_request_query=True,
        source_snapshot_hash=preregistration.source_snapshot_sha256,
    )


def _prediction(
    preregistration: LongRunPreregistration,
    symbol: str,
    cutoff: datetime,
    ordinal: int,
    context: tuple[object, ...],
) -> ForwardPredictionRecord:
    started = cutoff + timedelta(seconds=1)
    finished = cutoff + timedelta(seconds=2)
    return ForwardPredictionRecord(
        prediction_id=f"synthetic:{symbol}:{ordinal}",
        instrument=symbol,
        model="autogluon/chronos-2-small",
        model_identity_hash=preregistration.model_identity_sha256,
        cutoff=cutoff,
        input_snapshot_hash=_input_snapshot_hash(context, cutoff),
        predicted_return_bps=Decimal("1"),
        generated_at=finished,
        runtime_latency_ms=Decimal("1"),
        inference_started_at=started,
        inference_finished_at=finished,
        generation_deadline_at=prediction_deadline(cutoff),
        source_snapshot_hash=preregistration.source_snapshot_sha256,
        checkpoint_hash=preregistration.checkpoint_sha256,
        runner_hash=HASH,
        preprocessing_identity=CHRONOS_PREPROCESSING_IDENTITY,
        preprocessing_hash=HASH,
        dependency_lock_hash=preregistration.requirements_lock_sha256,
        runtime_environment_hash=HASH,
        device="cuda",
        native_interval_lower_bps=Decimal("-1"),
        native_interval_upper_bps=Decimal("1"),
        resource_peak_rss_mib=Decimal("1"),
        resource_peak_cpu_percent=Decimal("1"),
        resource_sample_count=1,
        provenance=(
            ("evidence_class", "forward_pit_admission"),
            ("source_snapshot_hash", preregistration.source_snapshot_sha256),
        ),
    )


def _write_terminal_fixture(root: Path, preregistration: LongRunPreregistration) -> None:
    source_root = root / "source"
    candidate_root = root / "candidate"
    coordinator_root = root / "coordinator"
    watchdog_root = root / "watchdog"
    scheduler_root = root / "scheduler"
    for path in (source_root, candidate_root, coordinator_root, watchdog_root, scheduler_root):
        path.mkdir(parents=True, exist_ok=True)

    raw = _build_raw_fixture(source_root, preregistration)
    tracker = LongRunFinalityTracker(
        source_root / "normalized-bars.jsonl",
        source_root / "post-admission-revisions.jsonl",
        minimum_interval_end=fresh_long_run_minimum_interval_end(preregistration.start_at),
    )
    tracker.replay(raw.records, preregistration.source_snapshot_sha256)
    admitted = tracker.normalized.read()
    by_key = {(bar.instrument, bar.interval_end): bar for bar in admitted}

    ledger = LongRunPredictionLedger(
        candidate_root / "predictions.jsonl",
        candidate_root / "prediction-durability.jsonl",
        preregistration.mandatory_cutoffs,
    )
    links = LongRunOutcomeLinkLedger(
        candidate_root / "outcome-links.jsonl", preregistration.mandatory_cutoffs
    )
    coordinator = LongRunCoordinator(preregistration, coordinator_root / "events.jsonl")
    coordinator.transition(LongRunState.PREFLIGHT_READY, event_type="PREFLIGHT_READY", at=START)
    coordinator.transition(LongRunState.RUNNING_WARMUP, event_type="CANDIDATE_STARTED", at=START)
    coordinator.transition(
        LongRunState.RUNNING,
        event_type="WARMUP_COMPLETE",
        at=preregistration.first_mandatory_cutoff_at,
    )

    for ordinal, cutoff in enumerate(preregistration.mandatory_cutoffs, 1):
        for symbol in V3_CORE_SYMBOLS:
            context = long_run_context_for_cutoff(
                admitted,
                instrument=symbol,
                cutoff=cutoff,
                available_at=cutoff,
                minimum_interval_end=fresh_long_run_minimum_interval_end(preregistration.start_at),
                source_snapshot_hash=preregistration.source_snapshot_sha256,
            )
            assert context is not None
            prediction = _prediction(preregistration, symbol, cutoff, ordinal, context)
            entry = ledger.append(
                generation_id=preregistration.generation_id,
                symbol=symbol,
                cutoff=cutoff,
                cutoff_ordinal=ordinal,
                prediction=prediction,
                inference_started_at=prediction.inference_started_at,
                inference_finished_at=prediction.inference_finished_at,
                append_started_at=cutoff + timedelta(seconds=3),
                clock=lambda cutoff=cutoff: cutoff + timedelta(seconds=4),
            )
            coordinator.record_case_pending(
                symbol=symbol,
                ordinal=ordinal,
                cutoff=cutoff,
                prediction_id=entry.prediction_id,
                at=cutoff + timedelta(seconds=4),
            )
            outcome_times = tuple(cutoff + timedelta(minutes=5 * index) for index in range(1, 13))
            outcome_hashes = tuple(
                _source_bar_hash(by_key[(symbol, item)]) for item in outcome_times
            )
            outcome_id = sha256(
                json.dumps(
                    {"prediction_id": entry.prediction_id, "source_bar_hashes": outcome_hashes},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            linked_at = max(
                by_key[(symbol, item)].collected_at for item in outcome_times
            ) + timedelta(seconds=1)
            links.append(
                generation_id=preregistration.generation_id,
                prediction_id=entry.prediction_id,
                symbol=symbol,
                cutoff=cutoff,
                cutoff_ordinal=ordinal,
                outcome_case_id=outcome_id,
                source_interval_ends=outcome_times,
                source_bar_hashes=outcome_hashes,
                linked_at=linked_at,
            )
            coordinator.record_case_clean(
                symbol=symbol,
                ordinal=ordinal,
                cutoff=cutoff,
                prediction_id=entry.prediction_id,
                outcome_case_id=outcome_id,
                at=linked_at,
            )

    terminal = preregistration.terminal_deadline
    coordinator.transition(LongRunState.TERMINALIZING, event_type="DEADLINE_REACHED", at=terminal)
    coordinator.transition(
        LongRunState.DEADLINE_REACHED, event_type="CANDIDATE_TERMINAL", at=terminal
    )
    coordinator.record_watchdog_check(
        decision="LONG_RUN_HEALTHY", reasons=(), at=preregistration.terminal_check_at
    )

    metrics = tracker.metrics()
    common = {
        "generation_id": preregistration.generation_id,
        "preregistration_sha256": long_run_preregistration_sha256(preregistration),
        "repository_commit": preregistration.repository_commit,
        "evidence_class": LONG_RUN_EVIDENCE_CLASS,
        "admission_eligible": False,
        "phase4_materialization_eligible": False,
        "runtime_attestation_sha256": preregistration.runtime_attestation_sha256,
        "source_snapshot_hash": preregistration.source_snapshot_sha256,
        "credentials_loaded": False,
        "order_writes_attempted": False,
        "execution_authority_present": False,
    }
    source_status = {
        **common,
        "state": LongRunState.DEADLINE_REACHED.value,
        "source_snapshot_hash": preregistration.source_snapshot_sha256,
        "collector_code_sha256": HASH,
        "finality_code_sha256": HASH,
        "endpoint": preregistration.rest_endpoint,
        "interval": preregistration.interval,
        "raw_receipt_count": len(raw.records),
        "transport_failure_count": 0,
        "admitted_final_bar_count": {
            symbol: sum(bar.instrument == symbol for bar in admitted) for symbol in V3_CORE_SYMBOLS
        },
        "unresolved_interval_count": metrics["unresolved_intervals"],
        "finality": metrics,
    }
    candidate_status = {
        **common,
        "state": LongRunState.DEADLINE_REACHED.value,
        "candidate_worker_code_sha256": HASH,
        "preprocessing_code_sha256": HASH,
        "model_identity_hash": preregistration.model_identity_sha256,
        "model_repository": preregistration.model_repository,
        "model_revision": preregistration.model_revision,
        "checkpoint_hash": preregistration.checkpoint_sha256,
        "dependency_lock_hash": preregistration.requirements_lock_sha256,
        "model_loaded": True,
        "prediction_count": len(ledger.entries),
        "prediction_counts": {symbol: 80 for symbol in V3_CORE_SYMBOLS},
        "rejection_count": 0,
        "schema_failures": 0,
        "nan_inf_failures": 0,
        "cuda_failures": 0,
        "model_load_failures": 0,
        "conflicting_ledger_writes": 0,
        "latest_successful_cutoff": preregistration.mandatory_cutoffs[-1]
        .isoformat()
        .replace("+00:00", "Z"),
        "fatal_history_count": 0,
        "scientific_state": coordinator.scientific_state.value,
        "ledger_health": True,
        "outcome_link_count": len(links.records),
    }
    outcome_status = {
        **common,
        "state": LongRunState.DEADLINE_REACHED.value,
        "outcome_linker_code_sha256": HASH,
        "prediction_count": len(ledger.entries),
        "pending_durability_count": 0,
        "outcome_link_count": len(links.records),
        "scientific_state": coordinator.scientific_state.value,
    }
    for path, payload in (
        (source_root / "status.json", source_status),
        (candidate_root / "status.json", candidate_status),
        (candidate_root / "outcome-status.json", outcome_status),
    ):
        write_json_atomic(path, payload)
    write_json_atomic(
        source_root / "manifest.json",
        {
            **common,
            "source_snapshot_hash": preregistration.source_snapshot_sha256,
            "collector_code_sha256": HASH,
            "finality_code_sha256": HASH,
            "endpoint": preregistration.rest_endpoint,
            "interval": preregistration.interval,
            "symbols": list(preregistration.symbols),
        },
    )
    write_json_atomic(
        candidate_root / "manifest.json",
        {
            **common,
            "candidate_worker_code_sha256": HASH,
            "model_identity_hash": preregistration.model_identity_sha256,
            "checkpoint_hash": preregistration.checkpoint_sha256,
            "preprocessing_code_sha256": HASH,
            "preprocessing_hash": HASH,
            "runner_hash": HASH,
            "dependency_lock_hash": preregistration.requirements_lock_sha256,
            "runtime_environment_hash": HASH,
            "context_bars": preregistration.context_bars,
            "context_newest_lag_seconds": preregistration.context_newest_lag_seconds,
        },
    )
    command = ["synthetic-scheduler"]
    write_json_atomic(
        watchdog_root / "status.json",
        {
            **common,
            "decision": "LONG_RUN_HEALTHY",
            "scientific_state": coordinator.scientific_state.value,
            "fatal_history_count": 0,
            "watchdog_code_sha256": HASH,
            "watchdog_terminal": True,
            "reasons": [],
            "healthy_event_count": 1,
            "warning_event_count": 0,
            "fatal_event_count": 0,
        },
    )
    write_json_atomic(
        scheduler_root / "status.json",
        {
            **common,
            "state": "AUDIT_COMPLETE",
            "scheduler_code_sha256": HASH,
            "scheduler_pid": 1,
            "process_create_time": 1.0,
            "command": command,
            "command_identity": process_command_identity(command),
        },
    )
    write_json_atomic(
        root / "launch.json",
        {
            **common,
            "state": "RUNNING",
            "code_hashes": {
                "collector": HASH,
                "candidate": HASH,
                "outcomes": HASH,
                "watchdog": HASH,
                "auditor": HASH,
                "scheduler": HASH,
                "coordinator": HASH,
                "launcher": HASH,
                "long_run_contract": HASH,
                "forward_contract": HASH,
                "cadence_contract": HASH,
            },
        },
    )


def test_terminal_auditor_certifies_an_independent_perfect_80_opportunity_run(
    tmp_path: Path,
) -> None:
    preregistration = _preregistration()
    preregistration_hash = long_run_preregistration_sha256(preregistration)
    root = tmp_path / "run"
    _write_terminal_fixture(root, preregistration)
    evidence_hashes_before = {
        path: sha256((root / path).read_bytes()).hexdigest()
        for path in (
            "source/normalized-bars.jsonl",
            "source/post-admission-revisions.jsonl",
        )
    }
    report = audit_long_run(
        preregistration=preregistration,
        preregistration_sha256=preregistration_hash,
        source_root=root / "source",
        candidate_root=root / "candidate",
        coordinator_root=root / "coordinator",
        watchdog_root=root / "watchdog",
        scheduler_root=root / "scheduler",
        repository_root=REPOSITORY_ROOT,
        terminal_observed_at=preregistration.terminal_check_at,
    )
    assert report["phase4_result"] == "PHASE4_LONGRUN_CERTIFIED"
    assert report["source"]["raw_hash_integrity"] is True
    assert report["source"]["admitted_identity_integrity"] is True
    assert report["source"]["finality_metrics"]["pre_admission_variation_intervals"] == 1
    assert report["source"]["post_admission_revision_count"] == 0
    assert report["candidate"]["predictions"] == {"BTCUSDT": 80, "ETHUSDT": 80}
    assert report["candidate"]["outcome_links"] == {"BTCUSDT": 80, "ETHUSDT": 80}
    assert report["coverage"]["clean"] == {"BTCUSDT": 80, "ETHUSDT": 80}
    assert report["issues"] == []
    assert long_run_audit_report_sha256(report) == report["report_hash"]
    assert {
        path: sha256((root / path).read_bytes()).hexdigest() for path in evidence_hashes_before
    } == evidence_hashes_before
