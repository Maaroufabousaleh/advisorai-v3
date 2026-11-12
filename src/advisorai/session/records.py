"""Immutable session lifecycle records for the owner-operated workstation.

Start → validate → recover → reconcile → gaps → snapshot → operate →
checkpoint → stop → restart. Records are immutable and linked by stable IDs;
a projection may summarize them but is never authority.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.contracts.core import ArtifactModel


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("session timestamps must include a timezone")
    return value.astimezone(UTC)


class SessionOutcome(StrEnum):
    CLEAN = "clean"
    EMERGENCY = "emergency"
    UNCLEAN = "unclean"


class RecoveryMode(StrEnum):
    NORMAL = "normal"
    RECOVERY = "recovery"
    ABSTAIN = "abstain"


class SessionStartRecord(ArtifactModel):
    """Authoritative mark that one owner session began under one lock identity."""

    session_id: UUID = Field(default_factory=uuid4)
    lock_identity: str = Field(min_length=1)
    code_hash: str = Field(min_length=1)
    config_hash: str = Field(min_length=1)
    prior_end_record_id: UUID | None = None
    start_reason: str = Field(min_length=1)
    recovery_classification: str = Field(default="clean_start", min_length=1)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _started_at = field_validator("started_at")(_require_aware)


class SessionCheckpoint(ArtifactModel):
    """Durable progress mark; resume is permitted only under current policy."""

    session_id: UUID
    ledger_offsets: dict[str, int] = Field(default_factory=dict)
    artifact_ids: tuple[UUID, ...] = ()
    watermarks: dict[str, str] = Field(default_factory=dict)
    pending_work: tuple[str, ...] = ()
    resumable: bool = True
    checkpoint_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _created_at = field_validator("created_at")(_require_aware)

    @model_validator(mode="after")
    def validate_offsets(self) -> SessionCheckpoint:
        if any(value < 0 for value in self.ledger_offsets.values()):
            raise ValueError("ledger offsets cannot be negative")
        return self


class SessionEndRecord(ArtifactModel):
    """Authoritative mark that a session stopped; unfinished work stays explicit."""

    session_id: UUID
    outcome: SessionOutcome
    end_offsets: dict[str, int] = Field(default_factory=dict)
    reconciliation_state: str = Field(min_length=1)
    open_orders: tuple[UUID, ...] = ()
    unfinished_work: tuple[str, ...] = ()
    leases_released: bool = False
    ended_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _ended_at = field_validator("ended_at")(_require_aware)

    @model_validator(mode="after")
    def validate_end(self) -> SessionEndRecord:
        if self.outcome is SessionOutcome.CLEAN and self.unfinished_work:
            raise ValueError("a clean stop cannot claim unfinished work completed")
        return self


class RecoveryRecord(ArtifactModel):
    """What restart detected, rebuilt, reconciled, and which mode resulted."""

    session_id: UUID
    prior_session_id: UUID | None = None
    detected_fault: str = Field(min_length=1)
    rebuild_actions: tuple[str, ...] = ()
    reconciliation_actions: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    recovery_mode: RecoveryMode = RecoveryMode.NORMAL
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _created_at = field_validator("created_at")(_require_aware)


class DataGapRecord(ArtifactModel):
    """Exact offline interval for one source/instrument; backfill preserves PIT times."""

    source: str = Field(min_length=1)
    instrument: str = Field(min_length=1)
    gap_start: datetime
    gap_end: datetime
    last_valid_watermark: str = Field(min_length=1)
    backfill_policy: str = Field(default="permitted_only", min_length=1)
    backfilled: bool = False

    _gap_start = field_validator("gap_start")(_require_aware)
    _gap_end = field_validator("gap_end")(_require_aware)

    @model_validator(mode="after")
    def validate_gap(self) -> DataGapRecord:
        if self.gap_end <= self.gap_start:
            raise ValueError("data gap must have a positive interval")
        return self


class DataGapResolutionRecord(ArtifactModel):
    """Verified backfill coverage for one exact, previously recorded gap."""

    gap_id: UUID
    source: str = Field(min_length=1)
    instrument: str = Field(min_length=1)
    covered_through: datetime
    watermark: str = Field(min_length=1)
    evidence_id: UUID
    verified_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _covered_through = field_validator("covered_through")(_require_aware)
    _verified_at = field_validator("verified_at")(_require_aware)


class SourceWatermarkRecord(ArtifactModel):
    """Durable source progress observed from the permitted source adapter."""

    source: str = Field(min_length=1)
    dataset: str = Field(min_length=1)
    value: str = Field(min_length=1)
    observed_at: datetime

    _observed_at = field_validator("observed_at")(_require_aware)

    @property
    def key(self) -> str:
        return f"{self.source}/{self.dataset}"


class PITSnapshotVerification(ArtifactModel):
    """Source-bound PIT snapshot verification tied to one owner session."""

    session_id: UUID
    snapshot_id: UUID
    snapshot_hash: str = Field(min_length=64, max_length=64)
    source: str = Field(min_length=1)
    cutoff: datetime
    first_available_at: datetime
    watermarks: dict[str, str] = Field(default_factory=dict)
    verified_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _cutoff = field_validator("cutoff")(_require_aware)
    _first_available_at = field_validator("first_available_at")(_require_aware)
    _verified_at = field_validator("verified_at")(_require_aware)

    @field_validator("snapshot_hash")
    @classmethod
    def valid_snapshot_hash(cls, value: str) -> str:
        normalized = value.strip().lower()
        if any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("snapshot_hash must be a lowercase SHA-256 digest")
        return normalized

    @model_validator(mode="after")
    def point_in_time(self) -> PITSnapshotVerification:
        if self.first_available_at > self.cutoff:
            raise ValueError("PIT snapshot was not available at its cutoff")
        return self


class RuntimeLeaseRecord(ArtifactModel):
    """Worker/model lease with explicit acquire/release and cleanup outcome."""

    lease_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    worker_identity: str = Field(min_length=1)
    resource_envelope: str = Field(min_length=1)
    acquired_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    released_at: datetime | None = None
    cleanup_outcome: str = Field(default="held", min_length=1)

    _acquired_at = field_validator("acquired_at")(_require_aware)

    @field_validator("released_at")
    @classmethod
    def release_aware(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value) if value is not None else None

    @model_validator(mode="after")
    def validate_release(self) -> RuntimeLeaseRecord:
        if self.released_at is not None and self.released_at < self.acquired_at:
            raise ValueError("lease cannot be released before acquisition")
        if self.released_at is None and self.cleanup_outcome != "held":
            raise ValueError("an unreleased lease must report outcome held")
        return self


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


__all__ = [
    "DataGapRecord",
    "DataGapResolutionRecord",
    "PITSnapshotVerification",
    "RecoveryMode",
    "RecoveryRecord",
    "RuntimeLeaseRecord",
    "SourceWatermarkRecord",
    "SessionCheckpoint",
    "SessionEndRecord",
    "SessionOutcome",
    "SessionStartRecord",
]
