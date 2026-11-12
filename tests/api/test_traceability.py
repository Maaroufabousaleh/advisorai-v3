"""Source-to-decision-to-outcome traceability: joined by exact IDs."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from advisorai.api.dashboard import DashboardProjection
from advisorai.learning import PaperDecisionRecord, PaperLearningLoop, PaperOutcome
from advisorai.ledger import SqliteLedgers


def test_traceability_joins_decision_fill_outcome(tmp_path):
    from advisorai.ledger import LedgerEvent, LedgerNamespace

    ledgers = SqliteLedgers(tmp_path / "trace.sqlite3")
    mission_id, snapshot_id = uuid4(), uuid4()
    proposal = {
        "mission_id": str(mission_id),
        "snapshot_id": str(snapshot_id),
        "stance": "flat",
        "abstained": True,
        "expires_at": datetime(2026, 8, 5, 16, tzinfo=UTC).isoformat(),
    }
    ledgers.append(
        LedgerEvent(
            namespace=LedgerNamespace.MISSION,
            event_type="proposal_recorded",
            idempotency_key=f"proposal:{mission_id}",
            payload={"proposal": proposal},
        )
    )
    loop = PaperLearningLoop(ledgers)
    record = loop.record_decision(
        PaperDecisionRecord(
            decision_id=uuid4(),
            mission_id=mission_id,
            snapshot_id=snapshot_id,
            order_ids=(uuid4(),),
            subject="decision-pipeline",
            subject_version="v1",
            asset="BTC",
            horizon="1h",
            cutoff=datetime(2026, 8, 5, 15, tzinfo=UTC),
            horizon_end=datetime(2026, 8, 5, 16, tzinfo=UTC),
        )
    )
    loop.record_outcome(
        PaperOutcome(
            decision_id=record.decision_id,
            as_of=record.horizon_end,
            net_utility=Decimal("3"),
            cost_usd=Decimal("1"),
            fill_quality=Decimal("1"),
            data_reliability=Decimal("1"),
            latency_ms=5,
            failure_rate=Decimal("0"),
        )
    )
    store = DashboardProjection(ledgers=ledgers)
    chains = store.traceability()
    assert len(chains) == 1
    chain = chains[0]
    assert chain.mission_id == str(mission_id)
    assert chain.snapshot_id == str(snapshot_id)
    assert chain.abstained is True
    assert chain.order_ids != ()
    assert chain.outcome_recorded is True
    assert chain.net_utility == "3"


def test_traceability_reports_absent_links(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "trace-empty.sqlite3")
    store = DashboardProjection(ledgers=ledgers)
    assert store.traceability() == ()
    assert DashboardProjection().traceability() == ()


def test_ledger_chain_attests_and_detects_tamper(tmp_path):
    import sqlite3

    from advisorai.api.dashboard import DashboardProjection
    from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers

    ledgers = SqliteLedgers(tmp_path / "chain.sqlite3")
    ledgers.append(
        LedgerEvent(
            namespace=LedgerNamespace.MISSION,
            event_type="session_started",
            idempotency_key="s1",
            payload={"start": {"session_id": "x"}},
        )
    )
    store = DashboardProjection(ledgers=ledgers)
    first = store.ledger_chain()
    assert first.total_events == 1
    assert len(first.chain_hash) == 64
    baseline = first.chain_hash
    assert store.ledger_chain().chain_hash == baseline
    with sqlite3.connect(ledgers.database_path) as connection:
        connection.execute("DELETE FROM mission_events WHERE idempotency_key = 's1'")
    assert store.ledger_chain().chain_hash != baseline
    assert DashboardProjection().ledger_chain().total_events == 0
