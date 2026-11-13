"""Research registry, factor DSL, and Alpha Team job tests."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from advisorai.research import (
    CandidateFactorArtifact,
    ExperimentArtifact,
    HypothesisCard,
    JobState,
    PaperCard,
    PromotionDecision,
    ResearchCoordinator,
    ResearchRegistry,
    ResearchRole,
    ValidationReport,
)
from advisorai.research import factors as dsl


def _paper() -> PaperCard:
    return PaperCard(
        source_url="https://arxiv.org/abs/2401.00001",
        authors=("Doe, J.",),
        claimed_result="momentum persists",
        limitations=("single venue",),
        relevance="BTC intraday",
    )


def test_registry_stores_and_links_full_chain(tmp_path):
    registry = ResearchRegistry(tmp_path / "research.sqlite3")
    paper = registry.store(_paper(), record_type="paper")
    hypothesis = registry.store(
        HypothesisCard(
            economic_rationale="inventory mean reversion",
            universe="BTCUSDT",
            horizon="1h",
            baseline="naive",
            preregistered_plan="plan-v1",
            owner="research-lead",
            expires_at=datetime(2026, 9, 1, tzinfo=UTC),
            paper_card_ids=(paper.artifact_id,),
        ),
        record_type="hypothesis",
    )
    factor = registry.store(
        CandidateFactorArtifact(
            dsl_ast_hash="a" * 64,
            dsl_source='{"op": "ts_mean", "args": [{"field": "close"}]}',
            universe="BTCUSDT",
            hypothesis_card_id=hypothesis.artifact_id,
        ),
        record_type="factor",
    )
    experiment = registry.store(
        ExperimentArtifact(
            candidate_id=factor.artifact_id,
            code_hash="b" * 64,
            data_snapshot_hash="c" * 64,
            environment_hash="d" * 64,
            trial_count=5,
        ),
        record_type="experiment",
    )
    report = registry.store(
        ValidationReport(
            experiment_id=experiment.artifact_id,
            leakage_check="passed",
            temporal_validation="purged walk-forward",
            multiple_testing="holm",
            independent_replay="reproduced",
            stability="stable",
            portfolio_incrementality="positive",
            approved=True,
            rationale="all gates passed",
        ),
        record_type="validation",
    )
    decision = registry.store(
        PromotionDecision(
            experiment_id=experiment.artifact_id,
            validation_id=report.artifact_id,
            policy_version="policy-v1",
            scope="paper-shadow",
            expiry=datetime(2026, 10, 1, tzinfo=UTC),
            rollback_condition="utility turns negative",
            approver="owner",
            approved=True,
        ),
        record_type="promotion",
    )
    registry.link(paper.artifact_id, hypothesis.artifact_id, "paper_supports_hypothesis")
    registry.link(hypothesis.artifact_id, factor.artifact_id, "hypothesis_proposes_factor")
    registry.link(factor.artifact_id, experiment.artifact_id, "factor_tested_by_experiment")
    registry.link(experiment.artifact_id, report.artifact_id, "experiment_validated_by")
    registry.link(report.artifact_id, decision.artifact_id, "validation_decided_by")
    assert registry.get(paper.artifact_id).artifact_id == paper.artifact_id
    chain = registry.chain(decision.artifact_id)
    types = {item[0] for item in chain}
    assert {"paper", "hypothesis", "factor", "experiment", "validation", "promotion"} <= types
    lineage = registry.lineage(experiment.artifact_id)
    assert len(lineage) >= 2


def test_registry_rejects_mutation(tmp_path):
    registry = ResearchRegistry(tmp_path / "research.sqlite3")
    paper = registry.store(_paper(), record_type="paper")
    with pytest.raises((ValueError, TypeError)):
        registry.store(paper.model_copy(update={"claimed_result": "changed"}), record_type="paper")
    with pytest.raises(KeyError):
        registry.retire(uuid4(), reason="nope")
    registry.retire(paper.artifact_id, reason="superseded")


def test_factor_dsl_rejects_forbidden_ops():
    with pytest.raises(dsl.FactorDslError):
        dsl.parse('{"op": "eval", "args": [{"field": "close"}]}')
    with pytest.raises(dsl.FactorDslError):
        dsl.parse('{"field": ""}')
    with pytest.raises(dsl.FactorDslError):
        dsl.parse('{"op": "add", "args": []}')
    expression = dsl.parse('{"op": "add", "args": [{"field": "close"}, {"const": 1}]}')
    assert len(dsl.canonical_hash(expression)) == 64
    assert dsl.duplicates(expression, {dsl.canonical_hash(expression)}) is True


def test_factor_dsl_evaluates_past_only():
    expression = dsl.parse('{"op": "ts_delta", "args": [{"field": "close"}, {"field": "open"}]}')
    result = dsl.evaluate(
        expression,
        {"close": (Decimal("10"), Decimal("12")), "open": (Decimal("9"), Decimal("11"))},
    )
    assert result.values == (Decimal("1"), Decimal("1"))
    assert result.expression_hash == dsl.canonical_hash(expression)


def test_red_team_rejects_leaky_factor():
    """A factor using future information must fail validation, not silently pass."""
    expression = dsl.parse(
        '{"op": "add", "args": [{"field": "close"}, {"field": "future_return"}]}'
    )
    with pytest.raises(dsl.FactorDslError, match="unknown factor field"):
        dsl.evaluate(expression, {"close": (Decimal("10"),)})


def test_coordinator_runs_cancels_and_checkpoints(tmp_path):
    from advisorai.ledger import SqliteLedgers

    ledgers = SqliteLedgers(tmp_path / "jobs.sqlite3")
    coordinator = ResearchCoordinator(ledgers, max_workers=2)
    job = coordinator.submit(ResearchRole.SCOUT, detail="arxiv scan")
    done = coordinator.run(job.job_id, lambda running, cancelled: "scanned 3 papers")
    assert done.state is JobState.COMPLETED
    assert done.detail == "scanned 3 papers"

    job2 = coordinator.submit(ResearchRole.FACTOR_MINER)

    def slow(running, cancelled):
        coordinator.checkpoint(running.job_id, detail="halfway")
        coordinator.cancel(running.job_id)
        return "should not complete"

    done2 = coordinator.run(job2.job_id, slow)
    assert done2.state is JobState.CANCELLED
    assert coordinator.jobs(JobState.COMPLETED) != ()


def test_coordinator_enforces_parallel_worker_cap(tmp_path):
    from advisorai.ledger import SqliteLedgers

    coordinator = ResearchCoordinator(
        SqliteLedgers(tmp_path / "bounded-jobs.sqlite3"), max_workers=2
    )
    jobs = [coordinator.submit(ResearchRole.SCOUT) for _ in range(3)]
    lock = threading.Lock()
    active = 0
    peak = 0
    two_started = threading.Event()
    release = threading.Event()
    finished: list[JobState] = []

    def worker(_job, cancelled):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                two_started.set()
        while not release.wait(0.01):
            if cancelled():
                break
        with lock:
            active -= 1
        return "bounded"

    threads = [
        threading.Thread(
            target=lambda job=job: finished.append(coordinator.run(job.job_id, worker).state)
        )
        for job in jobs
    ]
    for thread in threads:
        thread.start()
    assert two_started.wait(timeout=3)
    time.sleep(0.1)
    assert active == 2
    assert coordinator.active_workers == 2
    assert coordinator.peak_workers == 2
    release.set()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()
    assert peak == 2
    assert finished == [JobState.COMPLETED] * 3
    assert coordinator.active_workers == 0


def test_concurrent_submissions_respect_durable_queue_capacity(tmp_path):
    from advisorai.ledger import LedgerNamespace, SqliteLedgers

    ledgers = SqliteLedgers(tmp_path / "concurrent-submissions.sqlite3")
    coordinator = ResearchCoordinator(ledgers, max_queued_jobs=4)
    barrier = threading.Barrier(12)
    lock = threading.Lock()
    accepted = []
    refused = 0

    def submit_one() -> None:
        nonlocal refused
        barrier.wait(timeout=3)
        try:
            job = coordinator.submit(ResearchRole.SCOUT)
        except RuntimeError as exc:
            assert "queue is full" in str(exc)
            with lock:
                refused += 1
        else:
            with lock:
                accepted.append(job)

    threads = [threading.Thread(target=submit_one) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()

    submissions = [
        event
        for event in ledgers.events(LedgerNamespace.MISSION)
        if event.event_type == "research_job_submitted"
    ]
    assert len(accepted) == 4
    assert refused == 8
    assert len(submissions) == 4
    assert len(coordinator.jobs(JobState.QUEUED)) == 4


def test_external_cancellation_is_durable_across_restart(tmp_path):
    from advisorai.ledger import SqliteLedgers

    ledger_path = tmp_path / "external-cancel.sqlite3"
    ledgers = SqliteLedgers(ledger_path)
    coordinator = ResearchCoordinator(ledgers)
    job = coordinator.submit(ResearchRole.SCOUT)
    started = threading.Event()
    result = []

    def worker(_running, cancelled):
        started.set()
        while not cancelled():
            time.sleep(0.005)
        return "cancelled before acceptance"

    thread = threading.Thread(target=lambda: result.append(coordinator.run(job.job_id, worker)))
    thread.start()
    assert started.wait(timeout=3)
    assert coordinator.cancel(job.job_id).state is JobState.RUNNING
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert result[0].state is JobState.CANCELLED

    restarted = ResearchCoordinator(SqliteLedgers(ledger_path))
    hydrated = next(item for item in restarted.jobs() if item.job_id == job.job_id)
    assert hydrated.state is JobState.CANCELLED
    assert restarted.active_workers == 0


def test_execution_budget_is_deadline_aware_and_over_budget_results_fail(tmp_path):
    from advisorai.ledger import SqliteLedgers

    coordinator = ResearchCoordinator(
        SqliteLedgers(tmp_path / "budget-jobs.sqlite3"),
        default_execution_budget_seconds=0.05,
        max_execution_budget_seconds=1,
    )
    job = coordinator.submit(ResearchRole.FACTOR_MINER, execution_budget_seconds=0.05)

    def cooperative(_running, cancelled):
        while not cancelled():
            time.sleep(0.005)
        return "late result is not accepted"

    completed = coordinator.run(job.job_id, cooperative)
    assert completed.state is JobState.INTERRUPTED
    assert completed.detail == "execution_budget_exceeded"
    assert completed.budget_consumed_seconds == 0.05


def test_checkpoints_are_idempotent_durable_and_resumable(tmp_path):
    from advisorai.ledger import LedgerNamespace, SqliteLedgers

    ledger_path = tmp_path / "restartable-jobs.sqlite3"
    ledgers = SqliteLedgers(ledger_path)
    coordinator = ResearchCoordinator(ledgers)
    job = coordinator.submit(ResearchRole.QUANT_LEAD)

    def interrupted(running, _cancelled):
        coordinator.checkpoint(running.job_id, detail="cursor=7")
        coordinator.checkpoint(running.job_id, detail="cursor=7")
        raise RuntimeError("worker stopped")

    failed = coordinator.run(job.job_id, interrupted)
    assert failed.state is JobState.INTERRUPTED
    assert failed.checkpoint_detail == "cursor=7"
    checkpoint_events = [
        event
        for event in ledgers.events(LedgerNamespace.MISSION)
        if event.event_type == "research_job_checkpointed"
        and event.payload.get("job", {}).get("job_id") == str(job.job_id)
    ]
    assert len(checkpoint_events) == 1

    restarted = ResearchCoordinator(SqliteLedgers(ledger_path))
    hydrated = next(item for item in restarted.jobs() if item.job_id == job.job_id)
    assert hydrated.state is JobState.INTERRUPTED
    assert hydrated.checkpoint_detail == "cursor=7"
    resumed = restarted.run(
        job.job_id,
        lambda current, _cancelled: f"resumed:{current.checkpoint_detail}",
    )
    assert resumed.state is JobState.COMPLETED
    assert resumed.detail == "resumed:cursor=7"
