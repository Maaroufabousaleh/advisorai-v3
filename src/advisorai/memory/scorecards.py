"""Agent/model/source scorecards for routing and retirement."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Scorecard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scorecard_id: UUID = Field(default_factory=uuid4)
    subject: str = Field(min_length=1)
    subject_version: str = Field(min_length=1)
    role: str = Field(min_length=1)
    asset: str = Field(min_length=1)
    horizon: str = Field(min_length=1)
    regime: str = Field(min_length=1)
    factual_precision: Decimal = Field(ge=0, le=1)
    calibration: Decimal = Field(ge=0, le=1)
    abstention_quality: Decimal = Field(ge=0, le=1)
    contradiction_detection: Decimal = Field(ge=0, le=1)
    net_utility: Decimal
    latency_ms: int = Field(ge=0)
    api_cost_usd: Decimal = Field(ge=0)
    failure_rate: Decimal = Field(ge=0, le=1)
    eligible_for_routing: bool
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("subject", "subject_version", "role", "asset", "horizon", "regime")
    @classmethod
    def require_nonblank_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("scorecard identity fields cannot be blank")
        return value.strip()

    @field_validator(
        "factual_precision",
        "calibration",
        "abstention_quality",
        "contradiction_detection",
        "net_utility",
        "api_cost_usd",
        "failure_rate",
    )
    @classmethod
    def require_finite_metric(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("scorecard metrics must be finite")
        return value

    @field_validator("recorded_at")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scorecard timestamp must include a timezone")
        return value.astimezone(UTC)


class ScorecardStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database_path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scorecards(
                    scorecard_id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    subject_version TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )
                """
            )

    def append(self, scorecard: Scorecard) -> Scorecard:
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA busy_timeout=5000")
            existing = connection.execute(
                "SELECT payload_json FROM scorecards WHERE scorecard_id = ?",
                (str(scorecard.scorecard_id),),
            ).fetchone()
            if existing is not None:
                if existing[0] != scorecard.model_dump_json():
                    raise ValueError("scorecard ID is immutable")
                return scorecard
            connection.execute(
                "INSERT INTO scorecards(scorecard_id, subject, subject_version, payload_json, recorded_at) VALUES (?, ?, ?, ?, ?)",
                (
                    str(scorecard.scorecard_id),
                    scorecard.subject,
                    scorecard.subject_version,
                    scorecard.model_dump_json(),
                    scorecard.recorded_at.isoformat(),
                ),
            )
        return scorecard

    def latest(self, subject: str, subject_version: str) -> Scorecard | None:
        subject = subject.strip()
        subject_version = subject_version.strip()
        if not subject or not subject_version:
            raise ValueError("scorecard lookup requires subject and version")
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            row = connection.execute(
                "SELECT payload_json FROM scorecards WHERE subject = ? AND subject_version = ? "
                "ORDER BY recorded_at DESC, rowid DESC LIMIT 1",
                (subject, subject_version),
            ).fetchone()
        return Scorecard.model_validate_json(row[0]) if row else None
