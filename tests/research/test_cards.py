"""Research Brain records: immutable artifacts with approval boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from advisorai.research import (
    CandidateFactorArtifact,
    ExperimentArtifact,
    HypothesisCard,
    PaperCard,
    PromotionDecision,
    ValidationReport,
)


def _paper() -> PaperCard:
    return PaperCard(
        source_url="https://arxiv.org/abs/2401.00001",
        authors=("Doe, J.",),
        claimed_result="momentum persists at 1h horizons",
        limitations=("single venue",),
        relevance="BTC/ETH intraday",
    )


def test_paper_and_hypothesis_link():
    paper = _paper()
    hypothesis = HypothesisCard(
        economic_rationale="inventory mean reversion",
        universe="BTCUSDT,ETHUSDT",
        horizon="1h",
        baseline="naive",
        preregistered_plan="plan-v1",
        owner="research-lead",
        expires_at=datetime(2026, 9, 1, tzinfo=UTC),
        paper_card_ids=(paper.artifact_id,),
    )
    assert hypothesis.paper_card_ids == (paper.artifact_id,)


def test_factor_requires_dsl_hash():
    with pytest.raises(ValidationError):
        CandidateFactorArtifact(
            dsl_ast_hash="short",
            dsl_source="rank(ts_mean(close, 20))",
            universe="BTCUSDT",
        )


def test_experiment_and_validation_chain():
    experiment = ExperimentArtifact(
        candidate_id=uuid4(),
        code_hash="a" * 64,
        data_snapshot_hash="b" * 64,
        environment_hash="c" * 64,
        trial_count=10,
    )
    report = ValidationReport(
        experiment_id=experiment.artifact_id,
        leakage_check="passed",
        temporal_validation="purged walk-forward",
        multiple_testing="holm",
        independent_replay="reproduced",
        stability="stable",
        portfolio_incrementality="positive",
        approved=True,
        rationale="all gates passed",
    )
    assert report.approved is True
    decision = PromotionDecision(
        experiment_id=experiment.artifact_id,
        validation_id=report.artifact_id,
        policy_version="policy-v1",
        scope="paper-shadow",
        expiry=datetime(2026, 10, 1, tzinfo=UTC),
        rollback_condition="utility turns negative",
        approver="owner",
        approved=True,
    )
    assert decision.approved is True
    with pytest.raises(ValidationError, match="approver"):
        PromotionDecision(
            experiment_id=experiment.artifact_id,
            validation_id=report.artifact_id,
            policy_version="policy-v1",
            scope="paper-shadow",
            expiry=datetime(2026, 10, 1, tzinfo=UTC),
            rollback_condition="utility turns negative",
            approved=True,
        )
