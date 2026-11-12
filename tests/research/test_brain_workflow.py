"""Full Research Brain workflow: paper → hypothesis → factor → experiment → validation.

Proves the complete chain with durable registry storage, lineage links,
experiment reproduction, and red-team rejection — including a deliberately
leaky candidate that must be rejected, and a known-good path that reaches
paper-shadow approval. Failed and rejected work is retained, never deleted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

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
from advisorai.research.validity import MultipleTestingAudit, PurgedWalkForward


def _expiry() -> datetime:
    return datetime(2026, 12, 1, tzinfo=UTC)


def test_full_paper_to_approval_workflow(tmp_path):
    registry = ResearchRegistry(tmp_path / "brain.sqlite3")
    coordinator = ResearchCoordinator(None, max_workers=2)

    # Scout: ingest one paper.
    scout_job = coordinator.submit(ResearchRole.SCOUT, detail="arxiv scan")
    scout_done = coordinator.run(scout_job.job_id, lambda running, cancelled: "ingested 1 paper")
    assert scout_done.state is JobState.COMPLETED
    paper = registry.store(
        PaperCard(
            source_url="https://arxiv.org/abs/2401.00001",
            authors=("Doe, J.",),
            claimed_result="short-horizon mean reversion in BTC",
            limitations=("single venue", "2023-2024 sample"),
            relevance="BTC/USDT 1h",
        ),
        record_type="paper",
    )

    # Quant lead: hypothesis with preregistered plan.
    hypothesis = registry.store(
        HypothesisCard(
            economic_rationale="inventory mean reversion after liquidity shocks",
            universe="BTCUSDT",
            horizon="1h",
            point_in_time_inputs=("close", "open"),
            baseline="naive",
            preregistered_plan="purged walk-forward, holm correction, costs 14bps",
            failure_modes=("regime break", "fee regime change"),
            owner="quant-lead",
            expires_at=_expiry(),
            paper_card_ids=(paper.artifact_id,),
        ),
        record_type="hypothesis",
    )
    registry.link(paper.artifact_id, hypothesis.artifact_id, "paper_supports_hypothesis")

    # Miner: restricted-DSL factor, duplicate-checked.
    source = '{"op": "ts_delta", "args": [{"field": "close"}, {"field": "open"}]}'
    expression = dsl.parse(source)
    assert dsl.duplicates(expression, set()) is False
    factor = registry.store(
        CandidateFactorArtifact(
            dsl_ast_hash=dsl.canonical_hash(expression),
            dsl_source=source,
            lineage=("lake:bronze:binance:1h",),
            feature_versions=("dsl-v1",),
            lookbacks=(1,),
            universe="BTCUSDT",
            hypothesis_card_id=hypothesis.artifact_id,
        ),
        record_type="factor",
    )
    registry.link(hypothesis.artifact_id, factor.artifact_id, "hypothesis_proposes_factor")

    # Engineer: run the factor over past-only data, store the experiment.
    closes = tuple(Decimal(str(100 + i)) for i in range(20))
    opens = tuple(Decimal(str(99 + i)) for i in range(20))
    evaluated = dsl.evaluate(expression, {"close": closes, "open": opens})
    assert len(evaluated.values) == 20
    engineer_job = coordinator.submit(ResearchRole.EXPERIMENT_CONDUCTOR)
    engineer_done = coordinator.run(
        engineer_job.job_id, lambda running, cancelled: "reproduced 20 bars"
    )
    assert engineer_done.state is JobState.COMPLETED
    experiment = registry.store(
        ExperimentArtifact(
            candidate_id=factor.artifact_id,
            code_hash="e" * 64,
            data_snapshot_hash="f" * 64,
            environment_hash="0" * 64,
            seeds=(7,),
            windows=("2024-01/2024-06",),
            metrics={"mean_delta": str(sum(evaluated.values) / len(evaluated.values))},
            trial_count=1,
        ),
        record_type="experiment",
    )
    registry.link(factor.artifact_id, experiment.artifact_id, "factor_tested_by_experiment")

    # Red team: purged walk-forward + multiple-testing audit.
    split = PurgedWalkForward.make(
        train_start=datetime(2024, 1, 1, tzinfo=UTC),
        train_end=datetime(2024, 4, 1, tzinfo=UTC),
        validation_start=datetime(2024, 4, 2, tzinfo=UTC),
        validation_end=datetime(2024, 6, 1, tzinfo=UTC),
        embargo_seconds=3600,
    )
    assert split.passed is True
    audit = MultipleTestingAudit.bonferroni(
        tests_run=1, raw_p_value=Decimal("0.01"), alpha=Decimal("0.05")
    )
    assert audit.passed is True
    red_job = coordinator.submit(ResearchRole.RED_TEAM)
    red_done = coordinator.run(red_job.job_id, lambda running, cancelled: "no leakage found")
    assert red_done.state is JobState.COMPLETED
    report = registry.store(
        ValidationReport(
            experiment_id=experiment.artifact_id,
            leakage_check="passed: past-only inputs, purged split",
            temporal_validation="purged walk-forward",
            multiple_testing="holm over 1 trial",
            independent_replay="reproduced 20 bars",
            stability="stable",
            portfolio_incrementality="pending paper shadow",
            approved=True,
            rationale="all preregistered gates passed",
        ),
        record_type="validation",
    )
    registry.link(experiment.artifact_id, report.artifact_id, "experiment_validated_by")

    # Reviewer: bounded paper-shadow promotion, human approver recorded.
    decision = registry.store(
        PromotionDecision(
            experiment_id=experiment.artifact_id,
            validation_id=report.artifact_id,
            policy_version="policy-v1",
            scope="paper-shadow",
            expiry=_expiry(),
            rollback_condition="utility turns negative",
            approver="owner",
            approved=True,
        ),
        record_type="promotion",
    )
    registry.link(report.artifact_id, decision.artifact_id, "validation_decided_by")
    chain = registry.chain(decision.artifact_id)
    assert {item[0] for item in chain} == {
        "paper",
        "hypothesis",
        "factor",
        "experiment",
        "validation",
        "promotion",
    }


def test_leaky_candidate_is_rejected_and_retained(tmp_path):
    registry = ResearchRegistry(tmp_path / "brain-leak.sqlite3")
    registry.store(
        PaperCard(
            source_url="https://example.com/leak",
            authors=("X",),
            claimed_result="perfect foresight",
            limitations=("uses future returns",),
            relevance="none",
        ),
        record_type="paper",
    )
    # The DSL has no future operator: a leaky field reference fails loudly.
    expression = dsl.parse('{"op": "add", "args": [{"field": "close"}, {"field": "next_close"}]}')
    import pytest

    with pytest.raises(dsl.FactorDslError, match="unknown factor field"):
        dsl.evaluate(expression, {"close": (Decimal("1"), Decimal("2"))})
    # The rejected attempt is retained as an artifact, not deleted.
    factor = registry.store(
        CandidateFactorArtifact(
            dsl_ast_hash=dsl.canonical_hash(expression),
            dsl_source='{"op": "add", "args": [{"field": "close"}, {"field": "next_close"}]}',
            universe="BTCUSDT",
        ),
        record_type="factor",
    )
    report = registry.store(
        ValidationReport(
            experiment_id=factor.artifact_id,
            leakage_check="FAILED: references unavailable future field",
            temporal_validation="not run",
            multiple_testing="not run",
            independent_replay="not run",
            stability="unknown",
            portfolio_incrementality="none",
            approved=False,
            rationale="leakage check failed; candidate rejected",
        ),
        record_type="validation",
    )
    registry.link(factor.artifact_id, report.artifact_id, "factor_rejected_by")
    assert registry.get(report.artifact_id).approved is False
    assert registry.count("validation") == 1
