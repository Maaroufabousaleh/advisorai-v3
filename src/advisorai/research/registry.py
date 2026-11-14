"""Durable empirical Research Brain registries.

SQLite WAL storage with content-addressed, immutable, lineage-linked records:
PaperCard → HypothesisCard → CandidateFactor → ExperimentArtifact →
ValidationReport → PromotionDecision, plus outcome links. Retrieval is a
projection; the registry rows are the canonical truth. Vector/wiki/memory
systems remain optional retrieval, never the store of record.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import UUID

from advisorai.research.cards import (
    CandidateFactorArtifact,
    ExperimentArtifact,
    HypothesisCard,
    ImplementationRecord,
    PaperCard,
    PromotionDecision,
    ValidationReport,
)
from advisorai.research.source_policy import ResearchSourcePolicy

_RECORD_TYPES = {
    "paper": PaperCard,
    "hypothesis": HypothesisCard,
    "factor": CandidateFactorArtifact,
    "experiment": ExperimentArtifact,
    "validation": ValidationReport,
    "promotion": PromotionDecision,
    "implementation": ImplementationRecord,
}


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class ResearchRegistry:
    """Append-only, idempotent registry for all six research artifacts."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._source_policy = ResearchSourcePolicy()
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_records (
                    artifact_id TEXT PRIMARY KEY,
                    record_type TEXT NOT NULL,
                    content_hash TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    supersedes TEXT,
                    retired INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_links (
                    link_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_id TEXT NOT NULL,
                    to_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    UNIQUE (from_id, to_id, relation)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_source_observations (
                    source_url TEXT NOT NULL,
                    response_sha256 TEXT NOT NULL,
                    intake_sha256 TEXT NOT NULL,
                    artifact_id TEXT NOT NULL UNIQUE,
                    PRIMARY KEY (source_url, response_sha256, intake_sha256),
                    FOREIGN KEY (artifact_id) REFERENCES research_records(artifact_id)
                )
                """
            )

    def store(self, record: Any, *, record_type: str) -> Any:
        """Store an immutable record; identical content returns the prior row."""
        model = _RECORD_TYPES[record_type]
        if not isinstance(record, model):
            raise TypeError(f"record must be {model.__name__} for type {record_type!r}")
        if record_type == "paper":
            record = self._source_policy.assess_card(record)
        payload = _json(record.model_dump(mode="json", round_trip=True))
        content_hash = hashlib.sha256(payload.encode()).hexdigest()
        observation_key: str | None = None
        if record_type == "paper" and record.source_metadata_sha256 is not None:
            stable_identity = record.model_dump(
                mode="json",
                exclude={
                    "artifact_id",
                    "created_at",
                    "source_metadata_observed_at",
                    "source_license_evidence_observed_at",
                    "source_document_observed_at",
                },
            )
            observation_key = hashlib.sha256(_json(stable_identity).encode()).hexdigest()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if observation_key is not None:
                prior_observation = connection.execute(
                    "SELECT record.payload_json FROM research_source_observations observation "
                    "JOIN research_records record USING (artifact_id) "
                    "WHERE observation.source_url = ? AND observation.response_sha256 = ? "
                    "AND observation.intake_sha256 = ?",
                    (record.source_url, record.source_metadata_sha256, observation_key),
                ).fetchone()
                if prior_observation is not None:
                    return model.model_validate(json.loads(prior_observation["payload_json"]))
            existing = connection.execute(
                "SELECT artifact_id, payload_json FROM research_records WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
            if existing is not None:
                if observation_key is not None:
                    connection.execute(
                        "INSERT OR IGNORE INTO research_source_observations "
                        "(source_url, response_sha256, intake_sha256, artifact_id) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            record.source_url,
                            record.source_metadata_sha256,
                            observation_key,
                            existing["artifact_id"],
                        ),
                    )
                return model.model_validate(json.loads(existing["payload_json"]))
            try:
                connection.execute(
                    "INSERT INTO research_records(artifact_id, record_type, content_hash, payload_json)"
                    " VALUES (?, ?, ?, ?)",
                    (str(record.artifact_id), record_type, content_hash, payload),
                )
            except sqlite3.IntegrityError as exc:
                prior = connection.execute(
                    "SELECT payload_json FROM research_records WHERE artifact_id = ?",
                    (str(record.artifact_id),),
                ).fetchone()
                if prior is None or prior["payload_json"] != payload:
                    raise ValueError(
                        f"research artifact ID {record.artifact_id} is immutable"
                    ) from exc
            if observation_key is not None:
                connection.execute(
                    "INSERT INTO research_source_observations "
                    "(source_url, response_sha256, intake_sha256, artifact_id) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        record.source_url,
                        record.source_metadata_sha256,
                        observation_key,
                        str(record.artifact_id),
                    ),
                )
        return record

    def link(self, from_id: UUID, to_id: UUID, relation: str) -> None:
        """Record a lineage edge: paper→hypothesis→factor→experiment→validation."""
        if not relation.strip():
            raise ValueError("link relation cannot be blank")
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO research_links(from_id, to_id, relation) VALUES (?, ?, ?)",
                (str(from_id), str(to_id), relation.strip()),
            )

    def lineage(self, artifact_id: UUID) -> tuple[tuple[str, str, str], ...]:
        """All (from, to, relation) edges touching an artifact."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT from_id, to_id, relation FROM research_links"
                " WHERE from_id = ? OR to_id = ? ORDER BY link_id",
                (str(artifact_id), str(artifact_id)),
            ).fetchall()
        return tuple((row["from_id"], row["to_id"], row["relation"]) for row in rows)

    def chain(self, promotion_id: UUID) -> tuple[tuple[str, UUID], ...]:
        """Walk promotion→validation→experiment→factor→hypothesis→paper links."""
        ordered: list[tuple[str, UUID]] = []
        seen: set[str] = set()
        stack = [str(promotion_id)]
        with self._connect() as connection:
            while stack:
                current = stack.pop()
                if current in seen:
                    continue
                seen.add(current)
                row = connection.execute(
                    "SELECT record_type, artifact_id FROM research_records WHERE artifact_id = ?",
                    (current,),
                ).fetchone()
                if row is None:
                    continue
                ordered.append((row["record_type"], UUID(row["artifact_id"])))
                parents = connection.execute(
                    "SELECT from_id FROM research_links WHERE to_id = ?", (current,)
                ).fetchall()
                stack.extend(item["from_id"] for item in parents)
        return tuple(ordered)

    def get(self, artifact_id: UUID) -> Any | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record_type, payload_json FROM research_records WHERE artifact_id = ?",
                (str(artifact_id),),
            ).fetchone()
        if row is None:
            return None
        return _RECORD_TYPES[row["record_type"]].model_validate(json.loads(row["payload_json"]))

    def count(self, record_type: str | None = None) -> int:
        with self._connect() as connection:
            if record_type is None:
                row = connection.execute("SELECT COUNT(*) AS n FROM research_records").fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) AS n FROM research_records WHERE record_type = ?",
                    (record_type,),
                ).fetchone()
        return int(row["n"])

    def retire(self, artifact_id: UUID, *, reason: str) -> None:
        """Retire (never delete) a record with an explicit reason."""
        if not reason.strip():
            raise ValueError("retirement requires a reason")
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE research_records SET retired = 1 WHERE artifact_id = ?",
                (str(artifact_id),),
            )
            if updated.rowcount == 0:
                raise KeyError(f"unknown research artifact {artifact_id}")


__all__ = ["ResearchRegistry"]
