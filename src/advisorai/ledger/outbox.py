"""Durable SQLite outbox implementing the event-bus port.

The outbox is deliberately small: SQLite remains the local authority and
consumers replay immutable envelopes by ID.  Publishing the same event twice
is idempotent; reusing an event ID for different content is a hard conflict.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from advisorai.ports import EventBusPort, EventEnvelope


class EventIdempotencyConflict(RuntimeError):
    """An event ID was reused for different envelope content."""


class SqliteEventOutbox(EventBusPort):
    """Append-only local event outbox with explicit delivery acknowledgement."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS event_outbox(
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    artifact_ids_json TEXT NOT NULL,
                    payload_ref TEXT,
                    delivered_at TEXT
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _payload(envelope: EventEnvelope) -> tuple[str, str, str, str, str | None]:
        return (
            str(envelope.event_id),
            envelope.event_type,
            envelope.occurred_at.isoformat(),
            json.dumps([str(item) for item in envelope.artifact_ids], separators=(",", ":")),
            envelope.payload_ref,
        )

    def publish(self, envelope: EventEnvelope) -> None:
        values = self._payload(envelope)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM event_outbox WHERE event_id = ?", (values[0],)
            ).fetchone()
            if existing is not None:
                if (
                    tuple(
                        existing[key]
                        for key in ("event_type", "occurred_at", "artifact_ids_json", "payload_ref")
                    )
                    != values[1:]
                ):
                    raise EventIdempotencyConflict(
                        f"event ID {envelope.event_id} was reused for different content"
                    )
                return
            try:
                connection.execute(
                    """
                    INSERT INTO event_outbox(
                        event_id, event_type, occurred_at, artifact_ids_json, payload_ref
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError as exc:
                existing = connection.execute(
                    "SELECT * FROM event_outbox WHERE event_id = ?", (values[0],)
                ).fetchone()
                if existing is None:
                    raise
                if (
                    tuple(
                        existing[key]
                        for key in ("event_type", "occurred_at", "artifact_ids_json", "payload_ref")
                    )
                    != values[1:]
                ):
                    raise EventIdempotencyConflict(
                        f"event ID {envelope.event_id} was reused for different content"
                    ) from exc

    def replay(
        self, event_type: str | None = None, *, pending_only: bool = False
    ) -> tuple[EventEnvelope, ...]:
        clauses: list[str] = []
        parameters: list[object] = []
        if event_type is not None:
            clauses.append("event_type = ?")
            parameters.append(event_type)
        if pending_only:
            clauses.append("delivered_at IS NULL")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM event_outbox{where} ORDER BY occurred_at, rowid",
                parameters,
            ).fetchall()
        return tuple(self._to_envelope(row) for row in rows)

    def pending(self, event_type: str | None = None) -> tuple[EventEnvelope, ...]:
        return self.replay(event_type, pending_only=True)

    def mark_delivered(self, event_id: UUID, *, delivered_at: datetime | None = None) -> None:
        delivered_at = delivered_at or datetime.now(UTC)
        if delivered_at.tzinfo is None or delivered_at.utcoffset() is None:
            raise ValueError("delivered_at must include a timezone")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE event_outbox SET delivered_at = ? WHERE event_id = ?",
                (delivered_at.astimezone(UTC).isoformat(), str(event_id)),
            )
            if cursor.rowcount != 1:
                raise KeyError(str(event_id))

    @staticmethod
    def _to_envelope(row: sqlite3.Row) -> EventEnvelope:
        return EventEnvelope(
            event_id=UUID(row["event_id"]),
            event_type=row["event_type"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            artifact_ids=tuple(UUID(item) for item in json.loads(row["artifact_ids_json"])),
            payload_ref=row["payload_ref"],
        )
