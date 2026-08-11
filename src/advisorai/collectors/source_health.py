"""Deterministic health, lineage, and fail-closed contracts for source feeds.

The source plane is allowed to observe and qualify market data only.  These
contracts deliberately do not expose an order, account, or execution method.
Health is derived from measured observations; it is never delegated to a
model, agent, or dashboard action.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceHealthState(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"
    RECOVERING = "RECOVERING"
    QUARANTINED = "QUARANTINED"


class SequenceState(StrEnum):
    PASS = "pass"
    GAP = "gap"
    UNAVAILABLE = "unavailable"


class SnapshotState(StrEnum):
    PASS = "pass"
    RECOVERY_REQUIRED = "recovery_required"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class ReconnectState(StrEnum):
    STABLE = "stable"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class ClockConfidence(StrEnum):
    HIGH = "high"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class DisagreementState(StrEnum):
    NORMAL = "normal"
    DEGRADED = "degraded"
    SEVERE = "severe"
    UNMEASURED = "unmeasured"


class SourceHealthPolicy(BaseModel):
    """Versioned thresholds used by the source-health transition function."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(default="source-health-v1", min_length=1)
    stale_after_seconds: float = Field(default=5.0, gt=0, le=86_400)
    maximum_malformed_event_rate: float = Field(default=0.05, ge=0, le=1)
    maximum_clock_offset_seconds: float = Field(default=5.0, ge=0, le=60)


class SourceHealthObservation(BaseModel):
    """One measured source/symbol health input.

    ``provider_identity`` and ``endpoint`` are required on every observation so
    a later failover cannot inherit the previous source's identity.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime
    source_id: str = Field(min_length=1)
    provider_identity: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    connected: bool
    valid_event_count: int = Field(ge=0)
    last_valid_event_at: datetime | None = None
    last_valid_event_age_seconds: float | None = Field(default=None, ge=0)
    sequence_state: SequenceState
    snapshot_state: SnapshotState
    reconnect_state: ReconnectState
    clock_confidence: ClockConfidence
    clock_offset_seconds: float | None = None
    malformed_event_rate: float = Field(default=0, ge=0, le=1)
    disagreement_state: DisagreementState = DisagreementState.UNMEASURED
    contract_valid: bool = True
    source_identity_valid: bool = True

    @field_validator("observed_at", "last_valid_event_at")
    @classmethod
    def require_aware_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source health timestamps must include a timezone")
        return value.astimezone(UTC)

    @field_validator("source_id", "provider_identity", "endpoint", "symbol")
    @classmethod
    def require_nonblank_identity(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source health identity fields cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_measurements(self) -> SourceHealthObservation:
        if self.clock_offset_seconds is not None and abs(self.clock_offset_seconds) > 60:
            raise ValueError("source clock offset is outside the bounded observation range")
        if self.valid_event_count == 0 and self.last_valid_event_at is not None:
            raise ValueError("a source with no valid events cannot have a last valid event")
        if self.last_valid_event_at is not None and self.last_valid_event_age_seconds is None:
            raise ValueError("last valid event age is required when a last valid event exists")
        return self


class SourceHealthTransition(BaseModel):
    """A state change suitable for an append-only, hash-chained journal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime
    source_id: str = Field(min_length=1)
    provider_identity: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    previous_state: SourceHealthState | None = None
    state: SourceHealthState
    reason_codes: tuple[str, ...] = Field(min_length=1)
    fail_closed: bool
    policy_version: str = Field(min_length=1)
    previous_record_hash: str | None = None
    record_hash: str | None = None

    @field_validator("source_id", "provider_identity", "endpoint", "symbol")
    @classmethod
    def require_nonblank_identity(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source health transition identity fields cannot be blank")
        return value

    @field_validator("observed_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source health transition timestamp must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_hashes(self) -> SourceHealthTransition:
        for name in ("previous_record_hash", "record_hash"):
            value = getattr(self, name)
            if value is not None and (
                len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        return self


def _reasons(observation: SourceHealthObservation, policy: SourceHealthPolicy) -> tuple[str, ...]:
    reasons: list[str] = []
    if not observation.source_identity_valid:
        reasons.append("source_identity_invalid")
    if not observation.contract_valid:
        reasons.append("minimum_data_contract_failed")
    if observation.malformed_event_rate > policy.maximum_malformed_event_rate:
        reasons.append("malformed_event_rate_exceeded")
    if not observation.connected:
        reasons.append("connection_not_established")
    if observation.reconnect_state is ReconnectState.FAILED:
        reasons.append("reconnect_failed")
    elif observation.reconnect_state is ReconnectState.RECONNECTING:
        reasons.append("reconnect_in_progress")
    if observation.snapshot_state is SnapshotState.FAILED:
        reasons.append("snapshot_recovery_failed")
    elif observation.snapshot_state is SnapshotState.RECOVERY_REQUIRED:
        reasons.append("snapshot_recovery_required")
    if observation.sequence_state is SequenceState.GAP:
        reasons.append("sequence_gap")
    if observation.last_valid_event_age_seconds is None:
        reasons.append("no_valid_event")
    elif observation.last_valid_event_age_seconds > policy.stale_after_seconds:
        reasons.append("last_valid_event_stale")
    if observation.clock_confidence is not ClockConfidence.HIGH:
        reasons.append("clock_confidence_degraded")
    if observation.disagreement_state is DisagreementState.SEVERE:
        reasons.append("severe_source_disagreement")
    elif observation.disagreement_state is DisagreementState.DEGRADED:
        reasons.append("degraded_source_disagreement")
    return tuple(dict.fromkeys(reasons))


def evaluate_source_health(
    observation: SourceHealthObservation,
    *,
    policy: SourceHealthPolicy | None = None,
) -> tuple[SourceHealthState, tuple[str, ...], bool]:
    """Return ``state``, deterministic reasons, and whether new decisions fail closed."""

    policy = policy or SourceHealthPolicy()
    reasons = _reasons(observation, policy)
    if any(
        reason in reasons
        for reason in (
            "source_identity_invalid",
            "minimum_data_contract_failed",
            "malformed_event_rate_exceeded",
        )
    ):
        return SourceHealthState.QUARANTINED, reasons, True
    if "connection_not_established" in reasons or "reconnect_failed" in reasons:
        return SourceHealthState.DISCONNECTED, reasons, True
    if "reconnect_in_progress" in reasons or "snapshot_recovery_required" in reasons:
        return SourceHealthState.RECOVERING, reasons, True
    if "snapshot_recovery_failed" in reasons or "sequence_gap" in reasons:
        return SourceHealthState.DEGRADED, reasons, True
    if "no_valid_event" in reasons or "last_valid_event_stale" in reasons:
        return SourceHealthState.STALE, reasons, True
    if any(
        reason in reasons
        for reason in (
            "clock_confidence_degraded",
            "severe_source_disagreement",
            "degraded_source_disagreement",
        )
    ):
        return SourceHealthState.DEGRADED, reasons, True
    return SourceHealthState.HEALTHY, ("all_required_health_inputs_valid",), False


def transition_source_health(
    previous_state: SourceHealthState | None,
    observation: SourceHealthObservation,
    *,
    policy: SourceHealthPolicy | None = None,
) -> SourceHealthTransition:
    policy = policy or SourceHealthPolicy()
    state, reasons, fail_closed = evaluate_source_health(observation, policy=policy)
    return SourceHealthTransition(
        observed_at=observation.observed_at,
        source_id=observation.source_id,
        provider_identity=observation.provider_identity,
        endpoint=observation.endpoint,
        symbol=observation.symbol,
        previous_state=previous_state,
        state=state,
        reason_codes=reasons,
        fail_closed=fail_closed,
        policy_version=policy.policy_version,
    )


class SourceHealthLedger:
    """Crash-safe append-only transition journal with a verifiable hash chain."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records: list[SourceHealthTransition] = []
        self._last_hash: str | None = None
        self._latest_state_by_key: dict[tuple[str, str], SourceHealthState] = {}
        self._identity_by_key: dict[tuple[str, str], tuple[str, str]] = {}
        if path.exists():
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                    record = SourceHealthTransition.model_validate(payload)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"source-health ledger is corrupted at line {line_number}"
                    ) from exc
                if record.record_hash is None:
                    raise RuntimeError("source-health ledger record has no hash")
                expected = self._record_digest(record)
                if record.record_hash != expected:
                    raise RuntimeError("source-health ledger record hash does not match content")
                if record.previous_record_hash != self._last_hash:
                    raise RuntimeError("source-health ledger hash chain is not append-only")
                key = (record.source_id, record.symbol)
                expected_previous_state = self._latest_state_by_key.get(key)
                if record.previous_state != expected_previous_state:
                    raise RuntimeError(
                        "source-health ledger state chain does not match the previous state"
                    )
                identity = (record.provider_identity, record.endpoint)
                previous_identity = self._identity_by_key.get(key)
                if previous_identity is not None and identity != previous_identity:
                    raise RuntimeError(
                        "source-health ledger provider identity changed for a source stream"
                    )
                self._records.append(record)
                self._last_hash = record.record_hash
                self._latest_state_by_key[key] = record.state
                self._identity_by_key[key] = identity

    @staticmethod
    def _record_digest(record: SourceHealthTransition) -> str:
        payload = record.model_dump(mode="json", exclude={"record_hash"}, round_trip=True)
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()

    def append(self, transition: SourceHealthTransition) -> SourceHealthTransition:
        if transition.record_hash is not None:
            raise ValueError(
                "source-health transitions must be appended without a precomputed hash"
            )
        key = (transition.source_id, transition.symbol)
        expected_previous_state = self._latest_state_by_key.get(key)
        if transition.previous_state != expected_previous_state:
            raise ValueError("source-health transition previous state does not match the ledger")
        identity = (transition.provider_identity, transition.endpoint)
        previous_identity = self._identity_by_key.get(key)
        if previous_identity is not None and identity != previous_identity:
            raise ValueError("source-health transition cannot silently change provider identity")
        record = transition.model_copy(
            update={
                "previous_record_hash": self._last_hash,
                "record_hash": self._record_digest(
                    transition.model_copy(update={"previous_record_hash": self._last_hash})
                ),
            }
        )
        encoded = (record.model_dump_json(round_trip=True) + "\n").encode()
        with self.path.open("ab") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        self._records.append(record)
        self._last_hash = record.record_hash
        self._latest_state_by_key[key] = record.state
        self._identity_by_key[key] = identity
        return record

    def read(self) -> tuple[SourceHealthTransition, ...]:
        return tuple(self._records)


__all__ = [
    "ClockConfidence",
    "DisagreementState",
    "ReconnectState",
    "SequenceState",
    "SnapshotState",
    "SourceHealthLedger",
    "SourceHealthObservation",
    "SourceHealthPolicy",
    "SourceHealthState",
    "SourceHealthTransition",
    "evaluate_source_health",
    "transition_source_health",
]
