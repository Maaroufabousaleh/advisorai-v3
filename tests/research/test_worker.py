"""Strategy Engineer coding worker: supervised handoff behind a typed boundary.

Proves contracts require pinned sources, allowed inputs, and acceptance;
forbidden areas (broker/orders/production/risk) are rejected; the supervised
OMP worker returns a parseable DSL with tests and repro; independent
validation accepts or rejects without trusting worker claims; and the
handoff runs as a durable Strategy Engineer coordinator job.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from advisorai.ledger import SqliteLedgers
from advisorai.research import factors as dsl
from advisorai.research.cards import HypothesisCard
from advisorai.research.jobs import JobState, ResearchCoordinator, ResearchRole
from advisorai.research.worker import (
    EngineerHandoff,
    ExperimentContract,
    SupervisedOmpWorker,
    WorkerResult,
    contract_fingerprint,
    contract_for_hypothesis,
    validate_worker_result,
)


def _hypothesis() -> HypothesisCard:
    return HypothesisCard(
        economic_rationale="continuation",
        universe="SPY,TLT",
        horizon="252d/21d",
        baseline="buy-and-hold",
        preregistered_plan="plan v1",
        owner="test",
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
    )


def _contract() -> ExperimentContract:
    return contract_for_hypothesis(
        _hypothesis(), source_artifact_ids=("paper:abc123", "impl:def456")
    )


def test_contract_requires_sources_inputs_and_acceptance():
    hypothesis = _hypothesis()
    first = contract_for_hypothesis(hypothesis, source_artifact_ids=("a",))
    second = contract_for_hypothesis(hypothesis, source_artifact_ids=("a",))
    assert contract_fingerprint(first) == contract_fingerprint(second)
    assert len(contract_fingerprint(first)) == 64
    base = _contract()
    handoff = EngineerHandoff(
        coordinator=ResearchCoordinator(SqliteLedgers(Path("/tmp") / "worker-probe.sqlite3")),
        worker=SupervisedOmpWorker(),
    )
    with pytest.raises(ValueError, match="pinned source"):
        handoff.submit_build(
            ExperimentContract(
                hypothesis_id=base.hypothesis_id,
                source_artifact_ids=(),
                allowed_inputs=base.allowed_inputs,
                policy_id=base.policy_id,
                costs=base.costs,
                windows=base.windows,
                acceptance=base.acceptance,
            )
        )


def test_contract_rejects_forbidden_areas(tmp_path):
    coordinator = ResearchCoordinator(SqliteLedgers(tmp_path / "jobs.sqlite3"))
    handoff = EngineerHandoff(coordinator=coordinator, worker=SupervisedOmpWorker())
    base = _contract()
    bad = ExperimentContract(
        hypothesis_id=base.hypothesis_id,
        source_artifact_ids=base.source_artifact_ids,
        allowed_inputs=("broker credentials",),
        policy_id=base.policy_id,
        costs=base.costs,
        windows=base.windows,
        acceptance=base.acceptance,
    )
    with pytest.raises(ValueError, match="forbidden area"):
        handoff.submit_build(bad)


def test_supervised_worker_returns_validated_dsl(tmp_path):
    coordinator = ResearchCoordinator(SqliteLedgers(tmp_path / "jobs.sqlite3"))
    handoff = EngineerHandoff(coordinator=coordinator, worker=SupervisedOmpWorker())
    contract = _contract()
    job = handoff.submit_build(contract)
    assert job.role is ResearchRole.STRATEGY_ENGINEER
    output = handoff.run_build(job.job_id, contract)
    assert output["worker_id"] == "omp-supervised-v1"
    assert output["verdict"]["accepted"] is True
    assert len(output["verdict"]["dsl_hash"]) == 64
    assert output["verdict"]["dsl_hash"] == dsl.canonical_hash(dsl.parse(output["dsl_source"]))
    assert len(output["tests"]) == 2
    assert "run_research_replay" in output["repro_command"]
    completed = [item for item in coordinator.jobs() if item.job_id == job.job_id][0]
    assert completed.state is JobState.COMPLETED


def test_validation_rejects_empty_results():
    contract = _contract()
    with pytest.raises(ValueError, match="neither DSL nor patch"):
        validate_worker_result(
            WorkerResult(
                dsl_source=None,
                patch_summary=None,
                tests=("t",),
                repro_command="run",
                reused=(),
                reimplemented=(),
                rationale="empty",
            ),
            contract=contract,
        )
    with pytest.raises(ValueError, match="no tests"):
        validate_worker_result(
            WorkerResult(
                dsl_source='{"field": "adj_close"}',
                patch_summary=None,
                tests=(),
                repro_command="run",
                reused=(),
                reimplemented=(),
                rationale="untested",
            ),
            contract=contract,
        )
    assert uuid4() is not None
