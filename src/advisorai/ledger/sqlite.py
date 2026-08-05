"""Authoritative local ledger primitives.

Phase 1 creates separate namespaces in one SQLite WAL database. The physical
database is a deployment detail; event namespaces, idempotency keys, and
append-only records are the contract.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("ledger payload decimals must be finite")
        return str(value)
    return str(value)


class LedgerNamespace(StrEnum):
    ACCOUNT = "account"
    ORDER = "order"
    MISSION = "mission"
    MODEL = "model"
    CAPABILITY = "capability"
    INCIDENT = "incident"


class LedgerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID = Field(default_factory=uuid4)
    namespace: LedgerNamespace
    event_type: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    occurred_at: datetime = Field(default_factory=_utc_now)
    payload: dict[str, object]

    @field_validator("event_type", "idempotency_key")
    @classmethod
    def require_nonblank_token(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ledger event type and idempotency key cannot be blank")
        return value.strip()

    @field_validator("payload")
    @classmethod
    def require_payload(cls, value: dict[str, object]) -> dict[str, object]:
        if not value:
            raise ValueError("ledger events require a payload")
        try:
            json.dumps(value, allow_nan=False, default=_json_default)
        except (TypeError, ValueError) as exc:
            raise ValueError("ledger event payload must be JSON serializable and finite") from exc
        return value

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value.astimezone(UTC)


class IdempotencyConflict(RuntimeError):
    """An existing key was reused for different event content."""


class SqliteLedgers:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            for namespace in LedgerNamespace:
                connection.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {namespace.value}_events (
                        event_id TEXT PRIMARY KEY,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        event_type TEXT NOT NULL,
                        occurred_at TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """
                )

    def append(self, event: LedgerEvent) -> LedgerEvent:
        """Append or return the matching prior event for an idempotency retry."""

        payload_json = json.dumps(
            event.payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=_json_default,
        )
        table = f"{event.namespace.value}_events"
        with self._connect() as connection:
            existing = connection.execute(
                f"SELECT * FROM {table} WHERE idempotency_key = ?", (event.idempotency_key,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["event_type"] != event.event_type
                    or existing["payload_json"] != payload_json
                ):
                    raise IdempotencyConflict(
                        f"idempotency key {event.idempotency_key!r} already records different content"
                    )
                return LedgerEvent(
                    event_id=UUID(existing["event_id"]),
                    namespace=event.namespace,
                    event_type=existing["event_type"],
                    idempotency_key=event.idempotency_key,
                    occurred_at=datetime.fromisoformat(existing["occurred_at"]),
                    payload=json.loads(existing["payload_json"]),
                )
            existing_event_id = connection.execute(
                f"SELECT event_id FROM {table} WHERE event_id = ?", (str(event.event_id),)
            ).fetchone()
            if existing_event_id is not None:
                raise IdempotencyConflict(
                    f"event ID {event.event_id} is already recorded with different content"
                )
            try:
                connection.execute(
                    f"""
                    INSERT INTO {table}(event_id, idempotency_key, event_type, occurred_at, payload_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(event.event_id),
                        event.idempotency_key,
                        event.event_type,
                        event.occurred_at.isoformat(),
                        payload_json,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                # Another writer may have won the idempotency race after the
                # initial SELECT. Re-read and apply the same conflict check.
                existing = connection.execute(
                    f"SELECT * FROM {table} WHERE idempotency_key = ?",
                    (event.idempotency_key,),
                ).fetchone()
                if existing is None:
                    existing_event_id = connection.execute(
                        f"SELECT event_id FROM {table} WHERE event_id = ?",
                        (str(event.event_id),),
                    ).fetchone()
                    if existing_event_id is not None:
                        raise IdempotencyConflict(
                            f"event ID {event.event_id} is already recorded with different content"
                        ) from exc
                    raise
                if (
                    existing["event_type"] != event.event_type
                    or existing["payload_json"] != payload_json
                ):
                    raise IdempotencyConflict(
                        f"idempotency key {event.idempotency_key!r} already records different content"
                    ) from exc
                return LedgerEvent(
                    event_id=UUID(existing["event_id"]),
                    namespace=event.namespace,
                    event_type=existing["event_type"],
                    idempotency_key=event.idempotency_key,
                    occurred_at=datetime.fromisoformat(existing["occurred_at"]),
                    payload=json.loads(existing["payload_json"]),
                )
        return event

    def events(self, namespace: LedgerNamespace) -> tuple[LedgerEvent, ...]:
        table = f"{namespace.value}_events"
        with self._connect() as connection:
            rows = connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        return tuple(
            LedgerEvent(
                event_id=UUID(row["event_id"]),
                namespace=namespace,
                event_type=row["event_type"],
                idempotency_key=row["idempotency_key"],
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        )
