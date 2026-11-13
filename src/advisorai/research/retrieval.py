"""FTS retrieval over the Research Brain registry — retrieval only, never truth.

The FTS5 index is a disposable projection rebuilt from the canonical
``research_records`` rows. It exists so Hermes, the Evidence Council, and
Alpha Team jobs can find relevant papers/hypotheses/factors/experiments
without scanning. The registry remains the store of record; dropping and
rebuilding this index changes no evidence.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    artifact_id: UUID
    record_type: str
    summary: str
    rank: float


def _extract_text(record_type: str, payload: dict) -> tuple[str, str]:
    get = payload.get if isinstance(payload, dict) else {}.get
    if record_type == "paper":
        title = str(get("source_url", "paper") or "paper")
        body = " ".join(
            str(value)
            for value in (
                get("claimed_result", ""),
                get("relevance", ""),
                " ".join(get("limitations", []) or []),
                " ".join(get("authors", []) or []),
            )
        )
    elif record_type == "hypothesis":
        title = str(get("economic_rationale", "hypothesis") or "hypothesis")
        body = " ".join(
            str(value)
            for value in (
                get("universe", ""),
                get("horizon", ""),
                get("baseline", ""),
                get("preregistered_plan", ""),
                " ".join(get("failure_modes", []) or []),
            )
        )
    elif record_type == "factor":
        title = str(get("dsl_source", "factor") or "factor")
        body = " ".join(
            str(value) for value in (get("universe", ""), " ".join(get("lineage", []) or []))
        )
    elif record_type == "experiment":
        title = f"experiment trial_count={get('trial_count', '?')}"
        body = str(get("metrics", "") or "")
    elif record_type == "validation":
        title = f"validation {'approved' if get('approved') else 'rejected'}"
        body = " ".join(
            str(value)
            for value in (
                get("leakage_check", ""),
                get("temporal_validation", ""),
                get("rationale", ""),
            )
        )
    elif record_type == "promotion":
        title = f"promotion scope={get('scope', '?')}"
        body = " ".join(
            str(value) for value in (get("policy_version", ""), get("rollback_condition", ""))
        )
    else:
        title, body = record_type, ""
    return title, body


class ResearchRetrieval:
    """Rebuildable FTS5 index over a ResearchRegistry database file."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        if not database_path.exists():
            raise FileNotFoundError(f"research registry not found: {database_path}")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def rebuild(self) -> int:
        """Drop and rebuild the FTS index from canonical rows. Returns count."""
        with self._connect() as connection:
            connection.execute("DROP TABLE IF EXISTS research_fts")
            connection.execute(
                "CREATE VIRTUAL TABLE research_fts USING fts5"
                "(artifact_id UNINDEXED, record_type UNINDEXED, title, body)"
            )
            rows = connection.execute(
                "SELECT artifact_id, record_type, payload_json FROM research_records"
                " WHERE retired = 0"
            ).fetchall()
            count = 0
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"])
                except Exception:
                    continue
                title, body = _extract_text(row["record_type"], payload)
                connection.execute(
                    "INSERT INTO research_fts(artifact_id, record_type, title, body)"
                    " VALUES (?, ?, ?, ?)",
                    (row["artifact_id"], row["record_type"], title, body),
                )
                count += 1
        return count

    def search(
        self, query: str, *, record_type: str | None = None, limit: int = 20
    ) -> tuple[RetrievalHit, ...]:
        """BM25-ranked full-text search. Empty query or no index returns ()."""
        if not query.strip() or limit < 1:
            return ()
        with self._connect() as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if "research_fts" not in tables:
                return ()
            sql = (
                "SELECT artifact_id, record_type, title, rank AS score"
                " FROM research_fts WHERE research_fts MATCH ?"
            )
            params: list[object] = [query.strip()]
            if record_type is not None:
                sql += " AND record_type = ?"
                params.append(record_type)
            sql += " ORDER BY score LIMIT ?"
            params.append(limit)
            try:
                rows = connection.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                return ()
        hits: list[RetrievalHit] = []
        for row in rows:
            try:
                hits.append(
                    RetrievalHit(
                        artifact_id=UUID(row["artifact_id"]),
                        record_type=row["record_type"],
                        summary=row["title"],
                        rank=float(row["score"]),
                    )
                )
            except (ValueError, TypeError):
                continue
        return tuple(hits)


__all__ = ["ResearchRetrieval", "RetrievalHit", "librarian_search"]


def librarian_search(
    database_path: Path, query: str, *, limit: int = 10
) -> tuple[RetrievalHit, ...]:
    """Librarian worker entry: rebuild-if-missing, then ranked search.

    A coordinator job calls this with its query; the index rebuilds itself
    when absent so a fresh registry is searchable without manual steps.
    """
    retrieval = ResearchRetrieval(database_path)
    with retrieval._connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    if "research_fts" not in tables:
        retrieval.rebuild()
    return retrieval.search(query, limit=limit)
