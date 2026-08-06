from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from advisorai.learning import (
    LearningProblem,
    PaperDecisionRecord,
    PaperLearningLoop,
    PaperOutcome,
    ProblemKind,
)
from advisorai.ledger import LedgerNamespace, SqliteLedgers


def _decision(now):
    return PaperDecisionRecord(
        mission_id=uuid4(),
        snapshot_id=uuid4(),
        subject="direct-provider",
        subject_version="model-v1",
        asset="BTC",
        horizon="1h",
        cutoff=now,
        horizon_end=now + timedelta(hours=1),
    )


def test_problem_incident_replay_and_scorecard_wait_for_horizon(tmp_path):
    now = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    loop = PaperLearningLoop(SqliteLedgers(tmp_path / "ledger.sqlite3"))
    decision = loop.record_decision(_decision(now))
    problem = loop.record_problem(
        LearningProblem(
            decision_id=decision.decision_id,
            kind=ProblemKind.RECONCILIATION,
            summary="paper account did not match venue projection",
        )
    )
    assert problem.incident_id is not None
    with pytest.raises(ValueError, match="horizon"):
        loop.record_outcome(
            PaperOutcome(
                decision_id=decision.decision_id,
                as_of=now + timedelta(minutes=30),
                net_utility=Decimal("0"),
                cost_usd=Decimal("0.01"),
                fill_quality=Decimal("0.5"),
                data_reliability=Decimal("0.9"),
                latency_ms=100,
                failure_rate=Decimal("0"),
            )
        )
    replay = loop.replay(
        problem.problem_id,
        lambda _: {"passed": True, "regression_test": "tests/replay/test_fix.py"},
    )
    assert replay.passed
    scorecard = loop.record_outcome(
        PaperOutcome(
            decision_id=decision.decision_id,
            as_of=now + timedelta(hours=1),
            forecast_value=Decimal("0.1"),
            realized_value=Decimal("0.08"),
            net_utility=Decimal("0.02"),
            cost_usd=Decimal("0.01"),
            fill_quality=Decimal("0.95"),
            data_reliability=Decimal("0.9"),
            latency_ms=120,
            failure_rate=Decimal("0"),
        )
    )
    assert scorecard.subject == "direct-provider"
    assert loop.problems()[0].replay_id == replay.replay_id


def test_replay_and_scorecard_retries_are_deterministic_and_immutable(tmp_path):
    now = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    ledgers = SqliteLedgers(tmp_path / "ledger.sqlite3")
    loop = PaperLearningLoop(ledgers)
    decision = loop.record_decision(_decision(now))
    problem = loop.record_problem(
        LearningProblem(
            decision_id=decision.decision_id,
            kind=ProblemKind.CALIBRATION,
            summary="calibration regression requires replay",
        )
    )

    def runner(_):
        return {"passed": True, "regression_test": "tests/replay/calibration.py"}

    first_replay = loop.replay(problem.problem_id, runner)
    second_replay = loop.replay(problem.problem_id, runner)
    assert first_replay == second_replay
    assert (
        len(
            [
                event
                for event in ledgers.events(LedgerNamespace.MISSION)
                if event.event_type == "paper_replay_recorded"
            ]
        )
        == 1
    )

    outcome = PaperOutcome(
        decision_id=decision.decision_id,
        as_of=now + timedelta(hours=1),
        net_utility=Decimal("0.02"),
        cost_usd=Decimal("0.01"),
        fill_quality=Decimal("0.95"),
        data_reliability=Decimal("0.9"),
        latency_ms=120,
        failure_rate=Decimal("0"),
    )
    first_scorecard = loop.record_outcome(outcome)
    second_scorecard = loop.record_outcome(outcome)
    assert first_scorecard == second_scorecard
    assert (
        len(
            [
                event
                for event in ledgers.events(LedgerNamespace.MODEL)
                if event.event_type == "paper_scorecard_recorded"
            ]
        )
        == 1
    )
    with pytest.raises(ValueError, match="immutable"):
        loop.record_outcome(outcome.model_copy(update={"net_utility": Decimal("0.03")}))
