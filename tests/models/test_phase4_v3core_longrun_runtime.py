from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from advisorai.collectors.sources import HttpResponse
from advisorai.phase4.v3core_cadence import (
    V3_CORE_MARKET_DATA_REST_ENDPOINT,
    V3CoreBar,
    V3CoreBarProvenance,
)
from advisorai.phase4.v3core_forward import ForwardPredictionRecord
from advisorai.phase4.v3core_longrun import (
    LONG_RUN_TARGET_CASES_PER_SYMBOL,
    QUALIFIED_CHRONOS_CHECKPOINT_SHA256,
    QUALIFIED_PHASE3_GATE_SHA256,
    QUALIFIED_REQUIREMENTS_LOCK_SHA256,
    QUALIFIED_UV_LOCK_SHA256,
    LongRunCoverageSnapshot,
    LongRunIncidentDisposition,
    LongRunIncidentType,
    classify_long_run_incident,
    estimate_long_run_terminal_deadline,
    evaluate_long_run_coverage,
)
from advisorai.phase4.v3core_longrun_runtime import (
    LONG_RUN_CONTEXT_BARS,
    LONG_RUN_PREREGISTRATION_SCHEMA,
    LONG_RUN_PREREGISTRATION_SCHEMA_V1,
    LONG_RUN_REQUIRED_PREFLIGHT_CHECKS,
    LONG_RUN_VERIFICATION_SCHEMA,
    LongRunCoordinator,
    LongRunIncident,
    LongRunPredictionLedger,
    LongRunPreregistration,
    LongRunRawSpool,
    LongRunReadinessCheck,
    LongRunState,
    LongRunTransportFailureSpool,
    attest_long_run_identity,
    collect_runtime_attestation,
    derive_long_run_source_snapshot_sha256,
    evaluate_release_candidate_preflight,
    fresh_long_run_minimum_interval_end,
    load_long_run_verification_results,
    long_run_component_files,
    long_run_context_for_cutoff,
    long_run_preregistration_sha256,
    prediction_deadline,
    read_append_only_lines,
    read_long_run_normalized_bars_for_start,
    required_context_interval_ends,
    validate_utc_clock_progress,
)

HASH = "a" * 64
COMMIT = subprocess.run(
    ["git", "-C", str(Path(__file__).parents[2]), "rev-parse", "HEAD"],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
START = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)


def _preregistration() -> LongRunPreregistration:
    from advisorai.phase4.v3core_longrun import (
        derive_first_long_run_cutoff,
        derive_long_run_cutoffs,
    )

    first = derive_first_long_run_cutoff(START)
    cutoffs = derive_long_run_cutoffs(START)
    source_snapshot = derive_long_run_source_snapshot_sha256(
        generation_id="synthetic-long-run",
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
        generation_id="synthetic-long-run",
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


def _bar(interval_end: datetime, *, symbol: str = "BTCUSDT") -> V3CoreBar:
    provenance = V3CoreBarProvenance(
        interval_end=interval_end,
        provider_available_at=interval_end + timedelta(seconds=60),
        collected_at=interval_end + timedelta(seconds=60),
        availability_basis="forward_observed",
        evidence_class="forward_pit_admission",
        source_snapshot_hash=HASH,
        raw_record_hash=HASH,
        normalized_record_hash=HASH,
        source_health_state="HEALTHY",
    )
    return V3CoreBar(
        instrument=symbol,
        provenance=provenance,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1"),
        source_id="binance_spot_public_market_data",
        provider_identity="binance_spot_public_market_data",
        endpoint=V3_CORE_MARKET_DATA_REST_ENDPOINT,
        source_snapshot_hash=HASH,
    )


def _prediction(symbol: str, cutoff: datetime, *, sequence: int = 1) -> ForwardPredictionRecord:
    started = cutoff + timedelta(seconds=1)
    finished = cutoff + timedelta(seconds=2)
    return ForwardPredictionRecord(
        prediction_id=f"{symbol}-{sequence}",
        instrument=symbol,
        model="autogluon/chronos-2-small",
        model_identity_hash=HASH,
        cutoff=cutoff,
        input_snapshot_hash=HASH,
        predicted_return_bps=Decimal("1"),
        generated_at=finished,
        runtime_latency_ms=Decimal("1"),
        inference_started_at=started,
        inference_finished_at=finished,
        generation_deadline_at=prediction_deadline(cutoff),
    )


def test_long_run_schedule_has_exactly_eighty_ordered_cutoffs() -> None:
    prereg = _preregistration()
    assert len(prereg.mandatory_cutoffs) == LONG_RUN_TARGET_CASES_PER_SYMBOL
    assert prereg.mandatory_cutoffs[0] == prereg.first_mandatory_cutoff_at
    assert all(
        right - left == timedelta(hours=1)
        for left, right in zip(prereg.mandatory_cutoffs, prereg.mandatory_cutoffs[1:], strict=False)
    )


def test_legacy_preregistration_hash_is_stable_and_cannot_gain_gate_authority() -> None:
    legacy = _preregistration()
    assert legacy.schema == LONG_RUN_PREREGISTRATION_SCHEMA_V1
    digest = long_run_preregistration_sha256(legacy)
    reloaded = LongRunPreregistration.model_validate(
        legacy.model_dump(mode="json", exclude_none=True)
    )
    assert long_run_preregistration_sha256(reloaded) == digest
    payload = legacy.model_dump(mode="json", exclude_none=True)
    payload.update(
        launch_not_before_at=START,
        launch_not_after_at=START + timedelta(seconds=60),
        launch_gate_code_sha256=HASH,
        launch_preflight_code_sha256=HASH,
        verification_results_sha256=HASH,
    )
    with pytest.raises(ValueError, match="legacy V1"):
        LongRunPreregistration.model_validate(payload)


def test_v2_preregistration_requires_exact_gate_hash_and_launch_window() -> None:
    payload = _preregistration().model_dump(mode="json", exclude_none=True)
    payload.update(
        schema=LONG_RUN_PREREGISTRATION_SCHEMA,
        launch_not_before_at=START,
        launch_not_after_at=START + timedelta(seconds=60),
        launch_gate_code_sha256=HASH,
        launch_preflight_code_sha256=HASH,
        verification_results_sha256=HASH,
    )
    preregistration = LongRunPreregistration.model_validate(payload)
    assert preregistration.launch_not_before_at == START
    assert preregistration.launch_not_after_at == START + timedelta(seconds=60)
    assert long_run_preregistration_sha256(preregistration) != long_run_preregistration_sha256(
        _preregistration()
    )
    for missing in (
        "launch_not_before_at",
        "launch_not_after_at",
        "launch_gate_code_sha256",
        "launch_preflight_code_sha256",
        "verification_results_sha256",
    ):
        invalid = dict(payload)
        invalid.pop(missing)
        with pytest.raises(ValueError, match="must bind"):
            LongRunPreregistration.model_validate(invalid)
    invalid = dict(payload)
    invalid["launch_not_after_at"] = START + timedelta(seconds=61)
    with pytest.raises(ValueError, match="60-second"):
        LongRunPreregistration.model_validate(invalid)


def test_launch_verification_results_are_complete_true_and_hash_bound(tmp_path) -> None:
    path = tmp_path / "verification.json"
    payload = {
        "schema": LONG_RUN_VERIFICATION_SCHEMA,
        "checks": {name: True for name in LONG_RUN_REQUIRED_PREFLIGHT_CHECKS},
    }
    encoded = json.dumps(payload, sort_keys=True).encode()
    path.write_bytes(encoded)
    digest = __import__("hashlib").sha256(encoded).hexdigest()
    assert load_long_run_verification_results(path, expected_sha256=digest) == payload["checks"]
    with pytest.raises(ValueError, match="hash mismatch"):
        load_long_run_verification_results(path, expected_sha256="b" * 64)


@pytest.mark.parametrize(
    "mutation",
    ["missing", "extra", "failed", "non_boolean", "schema", "top_level_extra"],
)
def test_launch_verification_results_fail_closed(mutation: str, tmp_path) -> None:
    path = tmp_path / f"{mutation}.json"
    checks = {name: True for name in LONG_RUN_REQUIRED_PREFLIGHT_CHECKS}
    payload: dict[str, object] = {"schema": LONG_RUN_VERIFICATION_SCHEMA, "checks": checks}
    if mutation == "missing":
        checks.pop(next(iter(checks)))
    elif mutation == "extra":
        checks["caller_invented_check"] = True
    elif mutation == "failed":
        checks[next(iter(checks))] = False
    elif mutation == "non_boolean":
        checks[next(iter(checks))] = 1
    elif mutation == "top_level_extra":
        payload["unreviewed"] = True
    else:
        payload["schema"] = "unreviewed"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_long_run_verification_results(path)


def test_readiness_check_passed_is_strictly_boolean() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LongRunReadinessCheck(name="security", passed=1, reason="forged")


def test_actual_identity_attestation_hash_round_trips_with_identity_schema(monkeypatch) -> None:
    import advisorai.phase4.v3core_longrun_runtime as runtime_module

    root = Path(__file__).parents[2]
    requirements_lock = Path(
        "/home/maaro/.local/share/advisorai-v3/runtime-admissions/chronos-2-small/requirements.lock"
    )
    checkpoint = Path(
        "/home/maaro/.cache/advisorai-v3/models/chronos-2-small/"
        "ddec01313e50b6bc58ebaa92ede81bc24a3d9f9a/model/model.safetensors"
    )
    phase3_gate = Path(
        "/mnt/c/projects/advisorai-v3/artifacts/phase3/formal-admission/"
        "20260812T013505Z-with-passed-phase2-post-phase2-commit/phase3-gate-record.json"
    )
    runtime_qualification = Path(
        "/mnt/c/projects/advisorai-v3/artifacts/phase0/model-runtime-qualification/"
        "chronos-v3core-r1/20260817T194802.642906Z/chronos-2-small.json"
    )
    expected_head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(runtime_module, "_worktree_clean", lambda _root: True)

    attestation = attest_long_run_identity(
        repository_root=root,
        expected_repository_commit=expected_head,
        component_files=long_run_component_files(root),
        requirements_lock_path=requirements_lock,
        checkpoint_path=checkpoint,
        phase3_gate_path=phase3_gate,
        model_runtime_qualification_path=runtime_qualification,
        runtime=collect_runtime_attestation(),
    )

    assert attestation.repository_head == expected_head
    assert attestation.attestation_hash


def test_release_candidate_preflight_materializes_all_readiness_checks(monkeypatch) -> None:
    import advisorai.phase4.v3core_longrun_runtime as runtime_module

    root = Path(__file__).parents[2]
    requirements_lock = Path(
        "/home/maaro/.local/share/advisorai-v3/runtime-admissions/chronos-2-small/requirements.lock"
    )
    checkpoint = Path(
        "/home/maaro/.cache/advisorai-v3/models/chronos-2-small/"
        "ddec01313e50b6bc58ebaa92ede81bc24a3d9f9a/model/model.safetensors"
    )
    phase3_gate = Path(
        "/mnt/c/projects/advisorai-v3/artifacts/phase3/formal-admission/"
        "20260812T013505Z-with-passed-phase2-post-phase2-commit/phase3-gate-record.json"
    )
    runtime_qualification = Path(
        "/mnt/c/projects/advisorai-v3/artifacts/phase0/model-runtime-qualification/"
        "chronos-v3core-r1/20260817T194802.642906Z/chronos-2-small.json"
    )
    expected_head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(runtime_module, "_worktree_clean", lambda _root: True)
    attestation = attest_long_run_identity(
        repository_root=root,
        expected_repository_commit=expected_head,
        component_files=long_run_component_files(root),
        requirements_lock_path=requirements_lock,
        checkpoint_path=checkpoint,
        phase3_gate_path=phase3_gate,
        model_runtime_qualification_path=runtime_qualification,
        runtime=collect_runtime_attestation(),
    )

    report = evaluate_release_candidate_preflight(
        _preregistration(),
        attestation=attestation,
        phase3_gate_passed=True,
        model_runtime_passed=True,
        preflight_checks={name: True for name in LONG_RUN_REQUIRED_PREFLIGHT_CHECKS},
        gpu_lease_free=True,
    )

    assert report.decision == "RELEASE_CANDIDATE_REFUSED"
    assert len(report.checks) == 6 + len(LONG_RUN_REQUIRED_PREFLIGHT_CHECKS)
    assert report.actual_identity_hash == attestation.attestation_hash


@pytest.mark.parametrize(
    ("btc_clean", "eth_clean", "btc_excluded", "eth_excluded", "expected"),
    [
        (80, 80, 0, 0, "COVERAGE_POSSIBLE"),
        (64, 64, 16, 16, "COVERAGE_POSSIBLE"),
        (63, 63, 17, 17, "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION"),
        (80, 63, 0, 17, "GENERATION_CANNOT_SATISFY_PHASE4_ADMISSION"),
        (64, 64, 0, 0, "COVERAGE_POSSIBLE"),
    ],
)
def test_target_and_minimum_are_independent_per_symbol(
    btc_clean: int,
    eth_clean: int,
    btc_excluded: int,
    eth_excluded: int,
    expected: str,
) -> None:
    report = evaluate_long_run_coverage(
        LongRunCoverageSnapshot(
            clean_cases={"BTCUSDT": btc_clean, "ETHUSDT": eth_clean},
            excluded_cases={"BTCUSDT": btc_excluded, "ETHUSDT": eth_excluded},
            remaining_opportunities={
                "BTCUSDT": 80 - btc_clean - btc_excluded,
                "ETHUSDT": 80 - eth_clean - eth_excluded,
            },
        )
    )
    assert report.status == expected


def test_coordinator_latches_mathematical_infeasibility_and_does_not_write_a_17th_case(
    tmp_path,
) -> None:
    coordinator = LongRunCoordinator(_preregistration(), tmp_path / "events.jsonl")
    coordinator.transition(LongRunState.PREFLIGHT_READY, event_type="PREFLIGHT_READY", at=START)
    coordinator.transition(LongRunState.RUNNING_WARMUP, event_type="CANDIDATE_STARTED", at=START)
    coordinator.transition(LongRunState.RUNNING, event_type="WARMUP_COMPLETE", at=START)
    for ordinal in range(1, 18):
        coordinator.record_case_excluded(
            symbol="BTCUSDT",
            ordinal=ordinal,
            cutoff=_preregistration().mandatory_cutoffs[ordinal - 1],
            reason="synthetic exclusion",
            at=START + timedelta(hours=ordinal),
        )
    assert coordinator.scientific_state == LongRunState.GENERATION_FATAL
    assert len(coordinator.case_states()) == 17
    assert len(coordinator.fatal_events) == 1
    with pytest.raises(RuntimeError, match="fatal"):
        coordinator.record_case_excluded(
            symbol="BTCUSDT",
            ordinal=18,
            cutoff=_preregistration().mandatory_cutoffs[17],
            reason="must not continue",
            at=START,
        )


def test_fatal_state_is_absorbing_across_reload_and_watchdog_checks(tmp_path) -> None:
    prereg = _preregistration()
    path = tmp_path / "events.jsonl"
    coordinator = LongRunCoordinator(prereg, path)
    coordinator.fail(LongRunIncident.POST_ADMISSION_REVISION, at=START, detail="fixture")
    reopened = LongRunCoordinator(prereg, path)
    assert reopened.scientific_state == LongRunState.GENERATION_FATAL
    with pytest.raises(RuntimeError, match="healthy"):
        reopened.record_watchdog_check(decision="LONG_RUN_HEALTHY", reasons=(), at=START)
    with pytest.raises(RuntimeError, match="absorbing"):
        reopened.transition(LongRunState.RUNNING, event_type="bad", at=START)


def test_coordinator_projections_refresh_across_processes(tmp_path) -> None:
    prereg = _preregistration()
    path = tmp_path / "events.jsonl"
    writer = LongRunCoordinator(prereg, path)
    reader = LongRunCoordinator(prereg, path)
    writer.transition(LongRunState.PREFLIGHT_READY, event_type="PREFLIGHT_READY", at=START)
    assert reader.scientific_state == LongRunState.PREFLIGHT_READY
    writer.transition(LongRunState.RUNNING_WARMUP, event_type="CANDIDATE_STARTED", at=START)
    writer.transition(LongRunState.RUNNING, event_type="WARMUP_COMPLETE", at=START)
    cutoff = prereg.mandatory_cutoffs[0]
    writer.record_case_excluded(
        symbol="BTCUSDT",
        ordinal=1,
        cutoff=cutoff,
        reason="synthetic source outage",
        at=START,
    )
    assert reader.case_states()[("BTCUSDT", 1)][0].value == "EXCLUDED"


def test_watchdog_observation_does_not_inflate_fatal_history(tmp_path) -> None:
    prereg = _preregistration()
    path = tmp_path / "events.jsonl"
    coordinator = LongRunCoordinator(prereg, path)
    coordinator.fail(LongRunIncident.POST_ADMISSION_REVISION, at=START, detail="fixture")
    coordinator.record_watchdog_check(
        decision="GENERATION_FATAL",
        reasons=("fatal_history_latched",),
        at=START + timedelta(seconds=1),
    )
    reopened = LongRunCoordinator(prereg, path)
    assert len(reopened.events) == 2
    assert len(reopened.fatal_events) == 1
    assert reopened.scientific_state == LongRunState.GENERATION_FATAL


def test_watchdog_history_cannot_claim_fatal_or_healthy_without_matching_state(tmp_path) -> None:
    prereg = _preregistration()
    coordinator = _running_coordinator(tmp_path)
    with pytest.raises(RuntimeError, match="latched fatal"):
        coordinator.record_watchdog_check(
            decision="GENERATION_FATAL",
            reasons=("tampered",),
            at=START,
        )
    coordinator.record_watchdog_check(decision="LONG_RUN_HEALTHY", reasons=(), at=START)
    record = json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    record["payload"] = {"decision": "GENERATION_FATAL", "reasons": ["tampered"]}
    record_without_hash = dict(record)
    record_without_hash.pop("record_hash")
    record["record_hash"] = (
        __import__("hashlib")
        .sha256(json.dumps(record_without_hash, sort_keys=True, separators=(",", ":")).encode())
        .hexdigest()
    )
    (tmp_path / "events.jsonl").write_text(
        "\n".join(
            [
                *((tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()[:-1]),
                json.dumps(record, sort_keys=True, separators=(",", ":")),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="latched fatal"):
        LongRunCoordinator(prereg, tmp_path / "events.jsonl")


def test_generation_fatal_event_requires_a_typed_incident(tmp_path) -> None:
    coordinator = _running_coordinator(tmp_path)
    coordinator.fail(LongRunIncident.POST_ADMISSION_REVISION, at=START, detail="fixture")
    path = tmp_path / "events.jsonl"
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    record["payload"]["incident"] = ""
    record_without_hash = dict(record)
    record_without_hash.pop("record_hash")
    record["record_hash"] = (
        __import__("hashlib")
        .sha256(json.dumps(record_without_hash, sort_keys=True, separators=(",", ":")).encode())
        .hexdigest()
    )
    path.write_text(
        "\n".join(
            [
                *path.read_text(encoding="utf-8").splitlines()[:-1],
                json.dumps(record, sort_keys=True, separators=(",", ":")),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="incident identity"):
        LongRunCoordinator(coordinator.preregistration, path)


def test_generation_fatal_event_rejects_unknown_incident_type(tmp_path) -> None:
    coordinator = _running_coordinator(tmp_path)
    coordinator.fail(LongRunIncident.POST_ADMISSION_REVISION, at=START, detail="fixture")
    path = tmp_path / "events.jsonl"
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    record["payload"]["incident"] = "NOT_A_LONG_RUN_INCIDENT"
    record_without_hash = dict(record)
    record_without_hash.pop("record_hash")
    record["record_hash"] = (
        __import__("hashlib")
        .sha256(json.dumps(record_without_hash, sort_keys=True, separators=(",", ":")).encode())
        .hexdigest()
    )
    path.write_text(
        "\n".join(
            [
                *path.read_text(encoding="utf-8").splitlines()[:-1],
                json.dumps(record, sort_keys=True, separators=(",", ":")),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="incident identity is unknown"):
        LongRunCoordinator(coordinator.preregistration, path)


def test_coordinator_replay_rejects_semantically_malformed_hashed_case_event(tmp_path) -> None:
    coordinator = _running_coordinator(tmp_path)
    cutoff = coordinator.preregistration.mandatory_cutoffs[0]
    coordinator.record_case_pending(
        symbol="BTCUSDT",
        ordinal=1,
        cutoff=cutoff,
        prediction_id="prediction-1",
        at=cutoff,
    )
    path = tmp_path / "events.jsonl"
    records = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(records[-1])
    record["payload"].pop("prediction_id")
    unsigned = dict(record)
    unsigned.pop("record_hash")
    record["record_hash"] = (
        __import__("hashlib")
        .sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode())
        .hexdigest()
    )
    path.write_text(
        "\n".join([*records[:-1], json.dumps(record, sort_keys=True)]) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="pending case requires"):
        LongRunCoordinator(coordinator.preregistration, path)


def test_coordinator_event_log_is_append_only_without_cross_component_time_order(
    tmp_path,
) -> None:
    coordinator = _running_coordinator(tmp_path)
    coordinator.record_watchdog_check(
        decision="LONG_RUN_HEALTHY",
        reasons=(),
        at=START + timedelta(seconds=1),
    )
    coordinator.record_watchdog_check(decision="LONG_RUN_HEALTHY", reasons=(), at=START)
    reopened = LongRunCoordinator(coordinator.preregistration, tmp_path / "events.jsonl")
    assert [event.sequence for event in reopened.events] == list(range(1, 6))


def test_recovery_is_bounded_before_any_second_event_is_written(tmp_path) -> None:
    prereg = _preregistration()
    path = tmp_path / "events.jsonl"
    coordinator = LongRunCoordinator(prereg, path)
    coordinator.transition(LongRunState.PREFLIGHT_READY, event_type="PREFLIGHT_READY", at=START)
    coordinator.transition(LongRunState.RUNNING_WARMUP, event_type="CANDIDATE_STARTED", at=START)
    coordinator.transition(LongRunState.RUNNING, event_type="WARMUP_COMPLETE", at=START)
    kwargs = {
        "incident_id": "incident-1",
        "component": "candidate",
        "old_pid": 1,
        "old_command_identity": HASH,
        "failure": "synthetic crash",
        "before_hashes": {"ledger": HASH},
        "new_pid": 2,
        "new_command_identity": HASH,
        "resume_valid": True,
        "at": START,
    }
    coordinator.record_recovery(**kwargs)
    with pytest.raises(RuntimeError, match="exceeded one attempt"):
        coordinator.record_recovery(**kwargs)
    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert sum(record["event_type"] == "RECOVERY_ATTEMPT" for record in records) == 1
    with pytest.raises(RuntimeError, match="watchdog"):
        coordinator.record_recovery(
            **{**kwargs, "component": "watchdog", "incident_id": "incident-2"}
        )
    with pytest.raises(RuntimeError, match="component-wide cap"):
        coordinator.record_recovery(**{**kwargs, "incident_id": "incident-2"})


def test_component_recovery_is_auditable_during_warmup(tmp_path) -> None:
    prereg = _preregistration()
    coordinator = LongRunCoordinator(prereg, tmp_path / "events.jsonl")
    coordinator.transition(LongRunState.PREFLIGHT_READY, event_type="PREFLIGHT_READY", at=START)
    coordinator.transition(
        LongRunState.RUNNING_WARMUP,
        event_type="CANDIDATE_STARTED",
        at=START,
    )
    event = coordinator.record_recovery(
        incident_id="warmup-source-incident",
        component="collector",
        old_pid=1,
        old_command_identity=HASH,
        failure="source process interruption",
        before_hashes={"raw": HASH},
        new_pid=2,
        new_command_identity=HASH,
        resume_valid=True,
        cutoffs_impacted=(),
        at=START + timedelta(seconds=1),
    )
    assert event.state == LongRunState.RUNNING_WARMUP


def test_pending_outcome_can_finish_during_terminalization(tmp_path) -> None:
    coordinator = _running_coordinator(tmp_path)
    cutoff = coordinator.preregistration.mandatory_cutoffs[0]
    coordinator.record_case_pending(
        symbol="BTCUSDT",
        ordinal=1,
        cutoff=cutoff,
        prediction_id="prediction-1",
        at=cutoff,
    )
    coordinator.transition(
        LongRunState.TERMINALIZING,
        event_type="DEADLINE_REACHED",
        at=cutoff + timedelta(hours=1),
    )
    coordinator.transition(
        LongRunState.DEADLINE_REACHED,
        event_type="CANDIDATE_TERMINAL",
        at=cutoff + timedelta(hours=1),
    )
    coordinator.record_case_clean(
        symbol="BTCUSDT",
        ordinal=1,
        cutoff=cutoff,
        prediction_id="prediction-1",
        outcome_case_id="outcome-1",
        at=cutoff + timedelta(hours=1, seconds=1),
    )
    reopened = LongRunCoordinator(coordinator.preregistration, tmp_path / "events.jsonl")
    assert reopened.case_states()[("BTCUSDT", 1)][0].value == "CLEAN"


def test_raw_spool_rejects_reuse_of_the_same_captured_http_response(tmp_path) -> None:
    fetched = START + timedelta(minutes=1)
    response = HttpResponse(
        status_code=200,
        body=b"[]",
        fetched_at=fetched,
        url=V3_CORE_MARKET_DATA_REST_ENDPOINT,
    )
    spool = LongRunRawSpool(tmp_path / "raw.jsonl", source_snapshot_hash=HASH)
    spool.append(
        response,
        symbol="BTCUSDT",
        request_url=V3_CORE_MARKET_DATA_REST_ENDPOINT,
        request_attempt_id="attempt-1",
        source_snapshot_hash=HASH,
    )
    with pytest.raises(RuntimeError, match="same captured"):
        spool.append(
            response,
            symbol="BTCUSDT",
            request_url=V3_CORE_MARKET_DATA_REST_ENDPOINT,
            request_attempt_id="attempt-2",
            source_snapshot_hash=HASH,
        )
    later = response.model_copy(update={"fetched_at": fetched + timedelta(seconds=1)})
    spool.append(
        later,
        symbol="BTCUSDT",
        request_url=V3_CORE_MARKET_DATA_REST_ENDPOINT,
        request_attempt_id="attempt-3",
        source_snapshot_hash=HASH,
    )
    assert len(spool.records) == 2


def test_raw_spool_rejects_reused_request_attempt_and_sensitive_or_cached_headers(tmp_path) -> None:
    fetched = START + timedelta(minutes=1)
    response = HttpResponse(
        status_code=200,
        body=b"[]",
        fetched_at=fetched,
        url=V3_CORE_MARKET_DATA_REST_ENDPOINT,
    )
    spool = LongRunRawSpool(tmp_path / "raw.jsonl", source_snapshot_hash=HASH)
    spool.append(
        response,
        symbol="BTCUSDT",
        request_url=V3_CORE_MARKET_DATA_REST_ENDPOINT,
        request_attempt_id="attempt-1",
        source_snapshot_hash=HASH,
    )
    with pytest.raises(RuntimeError, match="request attempt"):
        spool.append(
            response.model_copy(update={"fetched_at": fetched + timedelta(seconds=1)}),
            symbol="BTCUSDT",
            request_url=V3_CORE_MARKET_DATA_REST_ENDPOINT,
            request_attempt_id="attempt-1",
            source_snapshot_hash=HASH,
        )
    with pytest.raises(ValueError, match="authentication"):
        LongRunRawSpool(tmp_path / "sensitive.jsonl", source_snapshot_hash=HASH).append(
            response.model_copy(update={"headers": (("Authorization", "Bearer secret"),)}),
            symbol="BTCUSDT",
            request_url=V3_CORE_MARKET_DATA_REST_ENDPOINT,
            request_attempt_id="attempt-sensitive",
            source_snapshot_hash=HASH,
        )
    with pytest.raises(ValueError, match="cached"):
        LongRunRawSpool(tmp_path / "cached.jsonl", source_snapshot_hash=HASH).append(
            response.model_copy(update={"headers": (("Age", "3"),)}),
            symbol="BTCUSDT",
            request_url=V3_CORE_MARKET_DATA_REST_ENDPOINT,
            request_attempt_id="attempt-cached",
            source_snapshot_hash=HASH,
        )
    for header, value in (("CF-Cache-Status", "HIT"), ("X-Cache", "HIT"), ("X-Cache-Hits", "1")):
        with pytest.raises(ValueError, match="cached"):
            LongRunRawSpool(tmp_path / f"{header}.jsonl", source_snapshot_hash=HASH).append(
                response.model_copy(update={"headers": ((header, value),)}),
                symbol="BTCUSDT",
                request_url=V3_CORE_MARKET_DATA_REST_ENDPOINT,
                request_attempt_id=f"attempt-{header}",
                source_snapshot_hash=HASH,
            )


def test_raw_spool_allows_two_distinct_public_acquisitions(tmp_path) -> None:
    fetched = START + timedelta(minutes=1)
    spool = LongRunRawSpool(tmp_path / "raw.jsonl", source_snapshot_hash=HASH)
    for ordinal in (1, 2):
        spool.append(
            HttpResponse(
                status_code=200,
                body=b"[]",
                fetched_at=fetched + timedelta(seconds=ordinal),
                url=V3_CORE_MARKET_DATA_REST_ENDPOINT,
            ),
            symbol="ETHUSDT",
            request_url=V3_CORE_MARKET_DATA_REST_ENDPOINT,
            request_attempt_id=f"attempt-{ordinal}",
            source_snapshot_hash=HASH,
        )
    assert len(spool.records) == 2


def test_raw_receipts_cannot_cross_source_snapshot_boundaries(tmp_path) -> None:
    response = HttpResponse(
        status_code=200,
        body=b"[]",
        fetched_at=START + timedelta(minutes=1),
        url=V3_CORE_MARKET_DATA_REST_ENDPOINT,
    )
    spool = LongRunRawSpool(tmp_path / "raw.jsonl", source_snapshot_hash=HASH)
    with pytest.raises(ValueError, match="source snapshot"):
        spool.append(
            response,
            symbol="BTCUSDT",
            request_url=V3_CORE_MARKET_DATA_REST_ENDPOINT,
            request_attempt_id="attempt-cross-snapshot",
            source_snapshot_hash="b" * 64,
        )


def test_transport_failures_are_bound_to_the_frozen_source_snapshot(tmp_path) -> None:
    path = tmp_path / "transport-failures.jsonl"
    request_url = f"{V3_CORE_MARKET_DATA_REST_ENDPOINT}?interval=5m&limit=2&symbol=BTCUSDT"
    spool = LongRunTransportFailureSpool(path, source_snapshot_hash=HASH)
    spool.append(
        symbol="BTCUSDT",
        request_url=request_url,
        request_attempt_id="transport-attempt-1",
        observed_at=START + timedelta(minutes=1),
        source_snapshot_hash=HASH,
        error_class="Timeout",
        status_code=None,
        retriable=True,
    )
    with pytest.raises(ValueError, match="source snapshot"):
        spool.append(
            symbol="BTCUSDT",
            request_url=request_url,
            request_attempt_id="transport-attempt-2",
            observed_at=START + timedelta(minutes=2),
            source_snapshot_hash="b" * 64,
            error_class="Timeout",
            status_code=None,
            retriable=True,
        )
    foreign_path = tmp_path / "foreign-transport-failures.jsonl"
    foreign = LongRunTransportFailureSpool(foreign_path, source_snapshot_hash="b" * 64)
    foreign.append(
        symbol="BTCUSDT",
        request_url=request_url,
        request_attempt_id="foreign-attempt-1",
        observed_at=START + timedelta(minutes=1),
        source_snapshot_hash="b" * 64,
        error_class="Timeout",
        status_code=None,
        retriable=True,
    )
    with pytest.raises(RuntimeError, match="source snapshot"):
        LongRunTransportFailureSpool(foreign_path, source_snapshot_hash=HASH)


def test_append_only_reader_rejects_torn_jsonl_tail(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"partial":', encoding="utf-8")
    with pytest.raises(RuntimeError, match="unflushed"):
        read_append_only_lines(path, attempts=1)


def test_fresh_long_run_start_allows_projection_before_first_receipt(tmp_path) -> None:
    assert read_long_run_normalized_bars_for_start(tmp_path) == ()


def test_raw_receipts_before_first_finality_admission_are_valid_warmup(tmp_path) -> None:
    (tmp_path / "raw-receipts.jsonl").write_text("{}\n", encoding="utf-8")
    assert read_long_run_normalized_bars_for_start(tmp_path) == ()


def test_missing_projection_with_published_admission_fails_closed(tmp_path) -> None:
    (tmp_path / "raw-receipts.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="normalized-bars.jsonl"):
        read_long_run_normalized_bars_for_start(tmp_path, expected_admitted_final_bars=1)


def test_prediction_ledger_rejects_per_symbol_cutoff_order_inversion(tmp_path) -> None:
    prereg = _preregistration()
    ledger = LongRunPredictionLedger(tmp_path / "predictions.jsonl", tmp_path / "durability.jsonl")
    second_cutoff = prereg.mandatory_cutoffs[1]
    second = _prediction("BTCUSDT", second_cutoff, sequence=2)
    ledger.append(
        generation_id=prereg.generation_id,
        symbol="BTCUSDT",
        cutoff=second_cutoff,
        cutoff_ordinal=2,
        prediction=second,
        inference_started_at=second.inference_started_at,
        inference_finished_at=second.inference_finished_at,
        append_started_at=second_cutoff + timedelta(seconds=3),
        clock=lambda: second_cutoff + timedelta(seconds=4),
    )
    first_cutoff = prereg.mandatory_cutoffs[0]
    first = _prediction("BTCUSDT", first_cutoff, sequence=1)
    with pytest.raises(RuntimeError, match="cutoff order"):
        ledger.append(
            generation_id=prereg.generation_id,
            symbol="BTCUSDT",
            cutoff=first_cutoff,
            cutoff_ordinal=1,
            prediction=first,
            inference_started_at=first.inference_started_at,
            inference_finished_at=first.inference_finished_at,
            append_started_at=first_cutoff + timedelta(seconds=3),
            clock=lambda: first_cutoff + timedelta(seconds=4),
        )


def test_backwards_utc_clock_fails_closed_without_extending_run() -> None:
    with pytest.raises(RuntimeError, match="moved backwards"):
        validate_utc_clock_progress(
            START + timedelta(seconds=2),
            START + timedelta(seconds=1),
            component="synthetic",
        )


def _running_coordinator(tmp_path, name: str = "events.jsonl") -> LongRunCoordinator:
    prereg = _preregistration()
    coordinator = LongRunCoordinator(prereg, tmp_path / name)
    coordinator.transition(
        LongRunState.PREFLIGHT_READY,
        event_type="PREFLIGHT_READY",
        at=START,
    )
    coordinator.transition(
        LongRunState.RUNNING_WARMUP,
        event_type="RUNNING_WARMUP",
        at=START,
    )
    coordinator.transition(
        LongRunState.RUNNING,
        event_type="WARMUP_COMPLETE",
        at=START,
    )
    return coordinator


def _populate_cases(
    coordinator: LongRunCoordinator,
    *,
    symbol: str,
    excluded_ordinals: set[int],
    event_time_offset: timedelta = timedelta(0),
) -> None:
    prereg = coordinator.preregistration
    for ordinal, cutoff in enumerate(prereg.mandatory_cutoffs, 1):
        if ordinal in excluded_ordinals:
            coordinator.record_case_excluded(
                symbol=symbol,
                ordinal=ordinal,
                cutoff=cutoff,
                reason="synthetic CASE_EXCLUDED",
                at=cutoff + event_time_offset,
            )
            if coordinator.scientific_state == LongRunState.GENERATION_FATAL:
                return
            continue
        prediction_id = f"{symbol}-prediction-{ordinal}"
        outcome_id = f"{symbol}-outcome-{ordinal}"
        coordinator.record_case_pending(
            symbol=symbol,
            ordinal=ordinal,
            cutoff=cutoff,
            prediction_id=prediction_id,
            at=cutoff + event_time_offset,
        )
        coordinator.record_case_clean(
            symbol=symbol,
            ordinal=ordinal,
            cutoff=cutoff,
            prediction_id=prediction_id,
            outcome_case_id=outcome_id,
            at=cutoff + event_time_offset + timedelta(seconds=1),
        )


def test_synthetic_full_80_opportunity_lifecycle_covers_buffer_and_failure_matrix(tmp_path) -> None:
    scenarios = (
        ("perfect", set(), set(), 80, 80, LongRunState.RUNNING),
        ("sixteen_exclusions", set(range(1, 17)), set(range(1, 17)), 64, 64, LongRunState.RUNNING),
        ("asymmetric", set(), set(range(1, 17)), 80, 64, LongRunState.RUNNING),
    )
    for name, btc_excluded, eth_excluded, btc_clean, eth_clean, expected_state in scenarios:
        coordinator = _running_coordinator(tmp_path, f"{name}.jsonl")
        _populate_cases(coordinator, symbol="BTCUSDT", excluded_ordinals=btc_excluded)
        _populate_cases(
            coordinator,
            symbol="ETHUSDT",
            excluded_ordinals=eth_excluded,
            event_time_offset=timedelta(hours=100),
        )
        accounting = coordinator.accounting()
        assert accounting.clean_cases_provisional == {"BTCUSDT": btc_clean, "ETHUSDT": eth_clean}
        assert accounting.opportunities_remaining == {"BTCUSDT": 0, "ETHUSDT": 0}
        assert accounting.feasible == {"BTCUSDT": True, "ETHUSDT": True}
        assert coordinator.scientific_state == expected_state


def test_synthetic_seventeenth_exclusion_is_admission_impossible(tmp_path) -> None:
    coordinator = _running_coordinator(tmp_path)
    _populate_cases(coordinator, symbol="BTCUSDT", excluded_ordinals=set(range(1, 18)))
    assert coordinator.scientific_state == LongRunState.GENERATION_FATAL
    assert coordinator.accounting().feasible["BTCUSDT"] is False
    assert len(coordinator.fatal_events) == 1


def test_reaching_clean_minimum_does_not_end_the_80_opportunity_schedule(tmp_path) -> None:
    coordinator = _running_coordinator(tmp_path)
    _populate_cases(coordinator, symbol="BTCUSDT", excluded_ordinals=set(range(65, 81)))
    accounting = coordinator.accounting()
    assert accounting.clean_cases_provisional["BTCUSDT"] == 64
    assert accounting.opportunities_remaining["BTCUSDT"] == 0
    assert coordinator.scientific_state == LongRunState.RUNNING
    assert accounting.feasible["ETHUSDT"] is True


def test_context_requires_the_exact_forty_eight_admitted_bars_and_frozen_boundary() -> None:
    prereg = _preregistration()
    cutoff = prereg.first_mandatory_cutoff_at
    ends = required_context_interval_ends(cutoff)
    assert len(ends) == LONG_RUN_CONTEXT_BARS
    assert ends[-1] == cutoff - timedelta(minutes=10)
    context = tuple(_bar(item) for item in ends)
    assert (
        long_run_context_for_cutoff(
            context,
            instrument="BTCUSDT",
            cutoff=cutoff,
            available_at=cutoff,
        )
        == context
    )
    assert (
        long_run_context_for_cutoff(
            context[:-1],
            instrument="BTCUSDT",
            cutoff=cutoff,
            available_at=cutoff,
        )
        is None
    )


def test_fresh_run_context_rejects_prelaunch_interval_ends() -> None:
    prereg = _preregistration()
    early_cutoff = START + timedelta(hours=4)
    early_context = tuple(_bar(item) for item in required_context_interval_ends(early_cutoff))
    assert early_context[0].interval_end < START
    assert (
        long_run_context_for_cutoff(
            early_context,
            instrument="BTCUSDT",
            cutoff=early_cutoff,
            available_at=early_cutoff,
            minimum_interval_end=prereg.start_at,
        )
        is None
    )


def test_fresh_run_excludes_an_interval_ending_exactly_at_launch() -> None:
    assert fresh_long_run_minimum_interval_end(START) == START + timedelta(minutes=5)
    prereg = _preregistration()
    cutoff = prereg.first_mandatory_cutoff_at
    context = tuple(_bar(item) for item in required_context_interval_ends(cutoff))
    with_launch_boundary = tuple(context) + (_bar(START),)
    assert (
        long_run_context_for_cutoff(
            with_launch_boundary,
            instrument="BTCUSDT",
            cutoff=cutoff,
            available_at=cutoff,
            minimum_interval_end=fresh_long_run_minimum_interval_end(START),
        )
        == context
    )


def test_prediction_deadline_is_explicit_and_durable_timestamp_is_separate(tmp_path) -> None:
    prereg = _preregistration()
    cutoff = prereg.first_mandatory_cutoff_at
    prediction = _prediction("BTCUSDT", cutoff)
    ledger = LongRunPredictionLedger(tmp_path / "predictions.jsonl", tmp_path / "durability.jsonl")
    entry = ledger.append(
        generation_id=prereg.generation_id,
        symbol="BTCUSDT",
        cutoff=cutoff,
        cutoff_ordinal=1,
        prediction=prediction,
        inference_started_at=prediction.inference_started_at,
        inference_finished_at=prediction.inference_finished_at,
        append_started_at=cutoff + timedelta(seconds=3),
        clock=lambda: cutoff + timedelta(seconds=4),
    )
    assert prediction.generated_at > cutoff
    assert entry.prediction.ledger_persisted_at is None
    assert ledger.durability_for(entry).durable_appended_at == cutoff + timedelta(seconds=4)
    reopened = LongRunPredictionLedger(
        tmp_path / "predictions.jsonl", tmp_path / "durability.jsonl"
    )
    assert reopened.entries[0].model_dump(mode="json") == entry.model_dump(mode="json")
    assert reopened.durability_for(reopened.entries[0]).model_dump(
        mode="json"
    ) == ledger.durability_for(entry).model_dump(mode="json")


def test_prediction_entry_cannot_start_before_its_cutoff(tmp_path) -> None:
    prereg = _preregistration()
    cutoff = prereg.first_mandatory_cutoff_at
    started = cutoff - timedelta(seconds=1)
    finished = cutoff
    prediction = _prediction("BTCUSDT", cutoff).model_copy(
        update={
            "generated_at": finished,
            "inference_started_at": started,
            "inference_finished_at": finished,
        }
    )
    ledger = LongRunPredictionLedger(tmp_path / "predictions.jsonl", tmp_path / "durability.jsonl")
    with pytest.raises(ValueError, match="cannot start before"):
        ledger.append(
            generation_id=prereg.generation_id,
            symbol="BTCUSDT",
            cutoff=cutoff,
            cutoff_ordinal=1,
            prediction=prediction,
            inference_started_at=started,
            inference_finished_at=finished,
            append_started_at=cutoff + timedelta(seconds=1),
            clock=lambda: cutoff + timedelta(seconds=2),
        )


def test_prediction_ledger_conflicting_duplicate_fails_closed(tmp_path) -> None:
    prereg = _preregistration()
    cutoff = prereg.first_mandatory_cutoff_at
    path = tmp_path / "predictions.jsonl"
    durability = tmp_path / "durability.jsonl"
    ledger = LongRunPredictionLedger(path, durability)
    first = _prediction("ETHUSDT", cutoff)
    ledger.append(
        generation_id=prereg.generation_id,
        symbol="ETHUSDT",
        cutoff=cutoff,
        cutoff_ordinal=1,
        prediction=first,
        inference_started_at=first.inference_started_at,
        inference_finished_at=first.inference_finished_at,
        append_started_at=cutoff + timedelta(seconds=3),
        clock=lambda: cutoff + timedelta(seconds=4),
    )
    conflicting = first.model_copy(update={"predicted_return_bps": Decimal("2")})
    with pytest.raises(RuntimeError, match="conflicting"):
        ledger.append(
            generation_id=prereg.generation_id,
            symbol="ETHUSDT",
            cutoff=cutoff,
            cutoff_ordinal=1,
            prediction=conflicting,
            inference_started_at=conflicting.inference_started_at,
            inference_finished_at=conflicting.inference_finished_at,
            append_started_at=cutoff + timedelta(seconds=3),
            clock=lambda: cutoff + timedelta(seconds=4),
        )


def test_prediction_ledger_conflicting_duplicate_envelope_fails_closed(tmp_path) -> None:
    prereg = _preregistration()
    cutoff = prereg.first_mandatory_cutoff_at
    prediction = _prediction("BTCUSDT", cutoff)
    ledger = LongRunPredictionLedger(tmp_path / "predictions.jsonl", tmp_path / "durability.jsonl")
    append_started_at = cutoff + timedelta(seconds=3)
    ledger.append(
        generation_id=prereg.generation_id,
        symbol="BTCUSDT",
        cutoff=cutoff,
        cutoff_ordinal=1,
        prediction=prediction,
        inference_started_at=prediction.inference_started_at,
        inference_finished_at=prediction.inference_finished_at,
        append_started_at=append_started_at,
        clock=lambda: cutoff + timedelta(seconds=4),
    )
    with pytest.raises(RuntimeError, match="conflicting prediction"):
        ledger.append(
            generation_id=prereg.generation_id,
            symbol="BTCUSDT",
            cutoff=cutoff,
            cutoff_ordinal=1,
            prediction=prediction,
            inference_started_at=prediction.inference_started_at,
            inference_finished_at=prediction.inference_finished_at,
            append_started_at=append_started_at + timedelta(milliseconds=1),
            clock=lambda: cutoff + timedelta(seconds=4),
        )


def test_synthetic_collector_crash_resume_preserves_raw_receipt_chain(tmp_path) -> None:
    """A collector restart resumes append-only raw truth without duplication."""

    first_fetched = START + timedelta(minutes=1)
    raw_path = tmp_path / "raw-receipts.jsonl"
    first_response = HttpResponse(
        status_code=200,
        body=b"[]",
        fetched_at=first_fetched,
        url=V3_CORE_MARKET_DATA_REST_ENDPOINT,
    )
    spool = LongRunRawSpool(raw_path, source_snapshot_hash=HASH)
    first = spool.append(
        first_response,
        symbol="BTCUSDT",
        request_url=V3_CORE_MARKET_DATA_REST_ENDPOINT,
        request_attempt_id="collector-attempt-1",
        source_snapshot_hash=HASH,
    )

    # Simulate a process crash and reconstruct the collector projection.
    resumed = LongRunRawSpool(raw_path, source_snapshot_hash=HASH)
    second = resumed.append(
        first_response.model_copy(update={"fetched_at": first_fetched + timedelta(seconds=1)}),
        symbol="BTCUSDT",
        request_url=V3_CORE_MARKET_DATA_REST_ENDPOINT,
        request_attempt_id="collector-attempt-2",
        source_snapshot_hash=HASH,
    )

    assert [record.request_attempt_id for record in resumed.records] == [
        "collector-attempt-1",
        "collector-attempt-2",
    ]
    assert resumed.records[0].record_hash == first.record_hash
    assert resumed.records[1].record_hash == second.record_hash


def test_synthetic_candidate_crash_before_and_after_durable_append_is_idempotent(tmp_path) -> None:
    """A candidate restart neither loses nor duplicates an already durable cutoff."""

    prereg = _preregistration()
    cutoff = prereg.mandatory_cutoffs[0]
    prediction = _prediction("BTCUSDT", cutoff)
    predictions_path = tmp_path / "predictions.jsonl"
    durability_path = tmp_path / "durability.jsonl"

    # Crash before append: a fresh worker can append the one intended record.
    before_append = LongRunPredictionLedger(
        predictions_path,
        durability_path,
        cutoff_schedule=prereg.mandatory_cutoffs,
    )
    assert before_append.entries == []
    entry = before_append.append(
        generation_id=prereg.generation_id,
        symbol="BTCUSDT",
        cutoff=cutoff,
        cutoff_ordinal=1,
        prediction=prediction,
        inference_started_at=prediction.inference_started_at,
        inference_finished_at=prediction.inference_finished_at,
        append_started_at=cutoff + timedelta(seconds=3),
        clock=lambda: cutoff + timedelta(seconds=4),
    )

    # Crash after the prediction and its post-fsync attestation: exact replay
    # returns the existing entry instead of writing a second prediction.
    resumed = LongRunPredictionLedger(
        predictions_path,
        durability_path,
        cutoff_schedule=prereg.mandatory_cutoffs,
    )
    replayed = resumed.append(
        generation_id=prereg.generation_id,
        symbol="BTCUSDT",
        cutoff=cutoff,
        cutoff_ordinal=1,
        prediction=prediction,
        inference_started_at=prediction.inference_started_at,
        inference_finished_at=prediction.inference_finished_at,
        append_started_at=cutoff + timedelta(seconds=3),
        clock=lambda: cutoff + timedelta(seconds=4),
    )
    assert replayed.record_hash == entry.record_hash
    assert len(resumed.entries) == 1
    assert len(resumed.attestations) == 1


def test_synthetic_watchdog_death_is_fatal_and_cannot_become_healthy(tmp_path) -> None:
    coordinator = _running_coordinator(tmp_path)
    coordinator.fail(LongRunIncident.WATCHDOG_PROCESS_DEATH, at=START, detail="synthetic death")
    resumed = LongRunCoordinator(coordinator.preregistration, tmp_path / "events.jsonl")

    assert resumed.scientific_state == LongRunState.GENERATION_FATAL
    assert resumed.fatal_latched is True
    with pytest.raises(RuntimeError, match="healthy"):
        resumed.record_watchdog_check(
            decision="LONG_RUN_HEALTHY",
            reasons=(),
            at=START + timedelta(seconds=1),
        )


@pytest.mark.parametrize(
    "incident",
    (
        LongRunIncidentType.POST_ADMISSION_REVISION,
        LongRunIncidentType.IDENTITY_MISMATCH,
        LongRunIncidentType.RUNTIME_LOCK_DRIFT,
        LongRunIncidentType.FUTURE_LEAKAGE,
    ),
)
def test_synthetic_integrity_failure_matrix_is_generation_fatal(
    incident: LongRunIncidentType,
) -> None:
    assert (
        classify_long_run_incident(incident, clean_minimum_still_attainable=True)
        == LongRunIncidentDisposition.GENERATION_FATAL
    )


def test_synthetic_late_prediction_is_observed_after_frozen_deadline(tmp_path) -> None:
    prereg = _preregistration()
    cutoff = prereg.mandatory_cutoffs[0]
    prediction = _prediction("ETHUSDT", cutoff)
    ledger = LongRunPredictionLedger(
        tmp_path / "predictions.jsonl",
        tmp_path / "durability.jsonl",
        cutoff_schedule=prereg.mandatory_cutoffs,
    )
    entry = ledger.append(
        generation_id=prereg.generation_id,
        symbol="ETHUSDT",
        cutoff=cutoff,
        cutoff_ordinal=1,
        prediction=prediction,
        inference_started_at=prediction.inference_started_at,
        inference_finished_at=prediction.inference_finished_at,
        append_started_at=cutoff + timedelta(seconds=300),
        clock=lambda: cutoff + timedelta(seconds=301),
    )
    assert ledger.durability_for(entry).durable_appended_at > prediction_deadline(cutoff)


def test_synthetic_missing_outcome_remains_pending_and_not_clean(tmp_path) -> None:
    coordinator = _running_coordinator(tmp_path)
    cutoff = coordinator.preregistration.mandatory_cutoffs[0]
    coordinator.record_case_pending(
        symbol="BTCUSDT",
        ordinal=1,
        cutoff=cutoff,
        prediction_id="missing-outcome-prediction",
        at=cutoff,
    )
    accounting = coordinator.accounting()
    assert accounting.cases_pending_outcome["BTCUSDT"] == 1
    assert accounting.clean_cases_provisional["BTCUSDT"] == 1
    assert accounting.cases_terminally_certified["BTCUSDT"] == 0
    assert coordinator.case_states()[("BTCUSDT", 1)][0].value == "PENDING_OUTCOME"


def test_synthetic_host_suspension_excludes_cutoffs_without_extending_schedule(tmp_path) -> None:
    prereg = _preregistration()
    coordinator = _running_coordinator(tmp_path)
    original_cutoffs = prereg.mandatory_cutoffs
    original_deadline = prereg.terminal_deadline

    assert (
        classify_long_run_incident(
            LongRunIncidentType.MISSED_MANDATORY_CUTOFF,
            clean_minimum_still_attainable=True,
        )
        == LongRunIncidentDisposition.CASE_EXCLUDED
    )
    for ordinal in range(1, 4):
        coordinator.record_case_excluded(
            symbol="BTCUSDT",
            ordinal=ordinal,
            cutoff=original_cutoffs[ordinal - 1],
            reason="synthetic host suspension missed cutoff",
            at=original_cutoffs[ordinal - 1] + timedelta(hours=3),
        )

    assert coordinator.preregistration.mandatory_cutoffs == original_cutoffs
    assert coordinator.preregistration.terminal_deadline == original_deadline
    assert coordinator.accounting().opportunities_remaining["BTCUSDT"] == 77
