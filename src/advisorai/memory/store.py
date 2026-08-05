"""SQLite WAL memory with FTS5-first retrieval and append-only supersession."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MemoryLayer(StrEnum):
    WORKING = "working"
    EVIDENCE = "evidence"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    EXPERIMENT = "experiment"
    TRADING = "trading"
    CAPABILITY = "capability"


class MemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: UUID = Field(default_factory=uuid4)
    layer: MemoryLayer
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    evidence_ids: tuple[UUID, ...] = ()
    supersedes: UUID | None = None
    authoritative: bool = False
    negative_result: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("title", "body")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("memory title and body cannot be blank")
        return value.strip()

    @field_validator("evidence_ids")
    @classmethod
    def require_unique_evidence(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(value) != len(set(value)):
            raise ValueError("memory evidence IDs must be unique")
        return value

    @field_validator("created_at")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("memory timestamp must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def enforce_authority_boundary(self) -> MemoryRecord:
        if self.layer is MemoryLayer.SEMANTIC and self.authoritative:
            raise ValueError("semantic retrieval memory can never be authoritative")
        return self


class MemoryStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_records(
                    record_id TEXT PRIMARY KEY,
                    layer TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    supersedes TEXT,
                    authoritative INTEGER NOT NULL,
                    negative_result INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(record_id UNINDEXED, title, body)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def append(self, record: MemoryRecord) -> MemoryRecord:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM memory_records WHERE record_id = ?", (str(record.record_id),)
            ).fetchone()
            if existing is not None:
                if self.get(record.record_id) != record:
                    raise ValueError("memory record ID is immutable")
                return record
            if record.supersedes is not None:
                if record.supersedes == record.record_id:
                    raise ValueError("memory record cannot supersede itself")
                prior = connection.execute(
                    "SELECT 1 FROM memory_records WHERE record_id = ?",
                    (str(record.supersedes),),
                ).fetchone()
                if prior is None:
                    raise ValueError("memory supersession must reference an existing record")
            connection.execute(
                """
                INSERT INTO memory_records(
                    record_id, layer, title, body, evidence_ids_json, supersedes,
                    authoritative, negative_result, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.record_id),
                    record.layer.value,
                    record.title,
                    record.body,
                    json.dumps([str(item) for item in record.evidence_ids]),
                    str(record.supersedes) if record.supersedes else None,
                    int(record.authoritative),
                    int(record.negative_result),
                    record.created_at.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO memory_fts(record_id, title, body) VALUES (?, ?, ?)",
                (str(record.record_id), record.title, record.body),
            )
        return record

    def get(self, record_id: UUID) -> MemoryRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_records WHERE record_id = ?", (str(record_id),)
            ).fetchone()
        if row is None:
            raise KeyError(str(record_id))
        return MemoryRecord(
            record_id=UUID(row[0]),
            layer=row[1],
            title=row[2],
            body=row[3],
            evidence_ids=tuple(UUID(item) for item in json.loads(row[4])),
            supersedes=UUID(row[5]) if row[5] else None,
            authoritative=bool(row[6]),
            negative_result=bool(row[7]),
            created_at=datetime.fromisoformat(row[8]),
        )

    def search(
        self, query: str, *, layer: MemoryLayer | None = None, limit: int = 20
    ) -> tuple[MemoryRecord, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        if not query.strip():
            raise ValueError("memory search query cannot be blank")
        clauses = ["memory_fts MATCH ?"]
        parameters: list[object] = [query]
        if layer is not None:
            clauses.append("records.layer = ?")
            parameters.append(layer.value)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT memory_fts.record_id FROM memory_fts "
                    "JOIN memory_records AS records ON records.record_id = memory_fts.record_id "
                    f"WHERE {' AND '.join(clauses)} LIMIT ?",
                    (*parameters, limit),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            raise ValueError("memory search query is not valid FTS syntax") from exc
        records = tuple(self.get(UUID(row[0])) for row in rows)
        return records
