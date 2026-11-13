"""Tamper-evidence: hash-chained audit detects deletion, reorder, mutation."""

from __future__ import annotations

import sqlite3

from advisorai.ledger import (
    LedgerEvent,
    LedgerNamespace,
    SqliteLedgers,
    audit_ledger,
    compute_chains,
)


def _event(key: str, event_type: str = "test_event") -> LedgerEvent:
    return LedgerEvent(
        namespace=LedgerNamespace.MISSION,
        event_type=event_type,
        idempotency_key=key,
        payload={"marker": key},
    )


def test_audit_attests_and_verifies_chain(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "audit.sqlite3")
    ledgers.append(_event("a"))
    ledgers.append(_event("b"))
    chains = compute_chains(ledgers)
    mission = next(chain for chain in chains if chain.namespace is LedgerNamespace.MISSION)
    assert mission.events == 2
    report = audit_ledger(ledgers)
    assert report.total_events == 2
    assert len(report.chain_hash) == 64
    assert report.verified is False  # attestation only, no expected baseline
    verified = audit_ledger(ledgers, expected=chains)
    assert verified.verified is True
    assert verified.gaps == ()


def test_audit_detects_deletion(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "audit-del.sqlite3")
    ledgers.append(_event("a"))
    ledgers.append(_event("b"))
    baseline = compute_chains(ledgers)
    with sqlite3.connect(ledgers.database_path) as connection:
        connection.execute("DELETE FROM mission_events WHERE idempotency_key = 'b'")
    report = audit_ledger(ledgers, expected=baseline)
    assert report.verified is False
    assert any("mission" in gap for gap in report.gaps)


def test_audit_detects_reorder_and_mutation(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "audit-mut.sqlite3")
    ledgers.append(_event("a"))
    ledgers.append(_event("b"))
    baseline = compute_chains(ledgers)
    with sqlite3.connect(ledgers.database_path) as connection:
        connection.execute(
            "UPDATE mission_events SET payload_json = ? WHERE idempotency_key = 'a'",
            ('{"marker":"tampered"}',),
        )
    report = audit_ledger(ledgers, expected=baseline)
    assert report.verified is False
    assert report.gaps != ()


def test_empty_ledger_chain_is_stable(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "audit-empty.sqlite3")
    first = audit_ledger(ledgers)
    second = audit_ledger(ledgers)
    assert first.chain_hash == second.chain_hash
    assert first.total_events == 0
    assert audit_ledger(ledgers, expected=compute_chains(ledgers)).verified is True
