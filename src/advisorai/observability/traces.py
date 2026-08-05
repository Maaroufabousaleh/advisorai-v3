"""SQLite WAL structured tracing without a permanent monitoring server."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _now() -> datetime:
    return datetime.now(UTC)


class StructuredTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: UUID = Field(default_factory=uuid4)
    at: datetime = Field(default_factory=_now)
    component: str = Field(min_length=1)
    event: str = Field(min_length=1)
    severity: str = "info"
    mission_id: UUID | None = None
    config_hash: str | None = None
    fields: dict[str, object] = Field(default_factory=dict)

    @field_validator("at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("trace timestamp must include a timezone")
        return value.astimezone(UTC)

    @field_validator("component", "event", "severity")
    @classmethod
    def require_trace_tokens(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("structured traces require component, event, and severity")
        return (
            value.strip().lower()
            if value in {"INFO", "WARNING", "ERROR", "CRITICAL"}
            else value.strip()
        )

    @field_validator("config_hash")
    @classmethod
    def validate_config_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("trace config_hash must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def validate_fields(self) -> StructuredTrace:
        try:
            json.dumps(self.fields, allow_nan=False, default=str)
        except (TypeError, ValueError) as exc:
            raise ValueError("trace fields must be JSON serializable and finite") from exc
        return self


class TraceStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS structured_traces (
                    trace_id TEXT PRIMARY KEY,
                    at TEXT NOT NULL,
                    component TEXT NOT NULL,
                    event TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    mission_id TEXT,
                    config_hash TEXT,
                    fields_json TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def write(self, trace: StructuredTrace) -> StructuredTrace:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM structured_traces WHERE trace_id = ?", (str(trace.trace_id),)
            ).fetchone()
            if existing is not None:
                expected_fields = json.dumps(
                    trace.fields, sort_keys=True, separators=(",", ":"), default=str
                )
                if (
                    existing[1] != trace.at.isoformat()
                    or existing[2] != trace.component
                    or existing[3] != trace.event
                    or existing[4] != trace.severity
                    or existing[5] != (str(trace.mission_id) if trace.mission_id else None)
                    or existing[6] != trace.config_hash
                    or existing[7] != expected_fields
                ):
                    raise ValueError("trace ID is immutable")
                return trace
            connection.execute(
                """
                INSERT INTO structured_traces(
                    trace_id, at, component, event, severity, mission_id, config_hash, fields_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(trace.trace_id),
                    trace.at.isoformat(),
                    trace.component,
                    trace.event,
                    trace.severity,
                    str(trace.mission_id) if trace.mission_id else None,
                    trace.config_hash,
                    json.dumps(trace.fields, sort_keys=True, separators=(",", ":"), default=str),
                ),
            )
        return trace

    def recent(
        self, *, limit: int = 100, component: str | None = None
    ) -> tuple[StructuredTrace, ...]:
        if limit < 1:
            raise ValueError("trace limit must be positive")
        query = "SELECT * FROM structured_traces"
        parameters: tuple[object, ...] = ()
        if component is not None:
            query += " WHERE component = ?"
            parameters = (component,)
        query += " ORDER BY at DESC LIMIT ?"
        with self._connect() as connection:
            rows = connection.execute(query, (*parameters, limit)).fetchall()
        return tuple(
            StructuredTrace(
                trace_id=UUID(row[0]),
                at=datetime.fromisoformat(row[1]),
                component=row[2],
                event=row[3],
                severity=row[4],
                mission_id=UUID(row[5]) if row[5] else None,
                config_hash=row[6],
                fields=json.loads(row[7]),
            )
            for row in rows
        )
