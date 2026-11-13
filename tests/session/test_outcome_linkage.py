"""Kill switch, recovery, and outcome-memory linkage for the paper session."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from advisorai.learning import PaperDecisionRecord, PaperLearningLoop, PaperOutcome
from advisorai.ledger import SqliteLedgers


def _record(tmp_path) -> PaperDecisionRecord:
    cutoff = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    return PaperDecisionRecord(
        decision_id=uuid4(),
        mission_id=uuid4(),
        snapshot_id=uuid4(),
        subject="decision-pipeline",
        subject_version="paper-runtime-v1",
        asset="BTC",
        horizon="1h",
        cutoff=cutoff,
        horizon_end=cutoff + timedelta(hours=1),
    )


def test_outcome_links_to_decision_only_after_horizon(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "outcome.sqlite3")
    loop = PaperLearningLoop(ledgers)
    record = loop.record_decision(_record(tmp_path))
    early = PaperOutcome(
        decision_id=record.decision_id,
        as_of=record.cutoff,
        net_utility=Decimal("1"),
        cost_usd=Decimal("0"),
        fill_quality=Decimal("1"),
        data_reliability=Decimal("1"),
        latency_ms=10,
        failure_rate=Decimal("0"),
    )
    try:
        loop.record_outcome(early)
    except ValueError:
        pass
    else:
        raise AssertionError("outcome before horizon close must be refused")
    outcome = PaperOutcome(
        decision_id=record.decision_id,
        as_of=record.horizon_end,
        realized_value=Decimal("50000"),
        forecast_value=Decimal("49900"),
        net_utility=Decimal("5"),
        cost_usd=Decimal("1"),
        fill_quality=Decimal("1"),
        data_reliability=Decimal("1"),
        latency_ms=10,
        failure_rate=Decimal("0"),
    )
    scorecard = loop.record_outcome(outcome)
    assert scorecard.eligible_for_routing is True
    # Idempotent replay of the same outcome returns the same scorecard.
    assert loop.record_outcome(outcome).scorecard_id == scorecard.scorecard_id


def test_unknown_decision_outcome_is_rejected(tmp_path):
    loop = PaperLearningLoop(SqliteLedgers(tmp_path / "unknown.sqlite3"))
    outcome = PaperOutcome(
        decision_id=uuid4(),
        as_of=datetime(2026, 8, 5, 16, 0, tzinfo=UTC),
        net_utility=Decimal("0"),
        cost_usd=Decimal("0"),
        fill_quality=Decimal("0"),
        data_reliability=Decimal("0"),
        latency_ms=0,
        failure_rate=Decimal("1"),
    )
    try:
        loop.record_outcome(outcome)
    except KeyError:
        pass
    else:
        raise AssertionError("unknown decision outcome must raise KeyError")
