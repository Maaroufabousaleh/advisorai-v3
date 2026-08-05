import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from advisorai.ledger import (
    EventIdempotencyConflict,
    IdempotencyConflict,
    LedgerEvent,
    LedgerNamespace,
    SqliteEventOutbox,
    SqliteLedgers,
)
from advisorai.ports import EventEnvelope


def test_ledgers_create_all_namespaces_and_retry_idempotently(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "state" / "ledgers.sqlite")
    event = LedgerEvent(
        namespace=LedgerNamespace.ORDER,
        event_type="order_created",
        idempotency_key="parent-intent-123",
        payload={"quantity": "1"},
    )
    first = ledgers.append(event)
    retry = ledgers.append(event)
    assert retry.event_id == first.event_id
    assert len(ledgers.events(LedgerNamespace.ORDER)) == 1

    with pytest.raises(IdempotencyConflict):
        ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.ORDER,
                event_type="order_created",
                idempotency_key="parent-intent-123",
                payload={"quantity": "2"},
            )
        )

    with pytest.raises(IdempotencyConflict, match="event ID"):
        ledgers.append(
            LedgerEvent(
                event_id=event.event_id,
                namespace=LedgerNamespace.ORDER,
                event_type="order_transitioned",
                idempotency_key="different-key",
                payload={"quantity": "1"},
            )
        )

    with sqlite3.connect(tmp_path / "state" / "ledgers.sqlite") as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {f"{namespace.value}_events" for namespace in LedgerNamespace}.issubset(tables)


def test_sqlite_event_outbox_replays_and_acknowledges_idempotently(tmp_path):
    outbox = SqliteEventOutbox(tmp_path / "state" / "outbox.sqlite")
    event = EventEnvelope(
        event_id=uuid4(),
        event_type="snapshot_ready",
        occurred_at=datetime(2026, 8, 4, tzinfo=UTC),
        artifact_ids=(uuid4(),),
        payload_ref="gold/market/manifest.json",
    )
    outbox.publish(event)
    outbox.publish(event)
    assert outbox.replay() == (event,)
    assert outbox.pending() == (event,)
    outbox.mark_delivered(event.event_id, delivered_at=datetime(2026, 8, 4, 1, tzinfo=UTC))
    assert outbox.pending() == ()
    assert outbox.replay("snapshot_ready") == (event,)
    with pytest.raises(EventIdempotencyConflict):
        outbox.publish(event.model_copy(update={"payload_ref": "different"}))


def test_sqlite_event_outbox_rejects_unknown_delivery(tmp_path):
    with pytest.raises(KeyError):
        SqliteEventOutbox(tmp_path / "outbox.sqlite").mark_delivered(uuid4())
