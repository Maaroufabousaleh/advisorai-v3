"""Research registry, factor DSL, and Alpha Team job tests."""

from __future__ import annotations

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
