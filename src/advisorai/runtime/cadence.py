"""Deterministic V3-Core cadence and closed-data readiness checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CadencePolicy(BaseModel):
    """The fixed transition cadence: 5-minute data, hourly decisions, 4h context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_interval_seconds: int = Field(default=300, ge=60, le=3600)
    decision_interval_seconds: int = Field(default=3600, ge=300, le=86_400)
    context_interval_seconds: int = Field(default=14_400, ge=3600, le=86_400)

    @model_validator(mode="after")
    def require_integral_cadence(self) -> CadencePolicy:
        if self.decision_interval_seconds % self.observation_interval_seconds:
            raise ValueError("decision interval must contain whole observation intervals")
        if self.context_interval_seconds % self.decision_interval_seconds:
            raise ValueError("context interval must contain whole decision intervals")
        return self


class CadenceReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cutoff: datetime
    expected_cutoffs: tuple[datetime, ...]
    observed_cutoffs: tuple[datetime, ...]
    missing_cutoffs: tuple[datetime, ...]
    passed: bool
    reasons: tuple[str, ...] = ()

    @field_validator("cutoff", "expected_cutoffs", "observed_cutoffs", "missing_cutoffs")
    @classmethod
    def require_aware(cls, value):
        values = value if isinstance(value, tuple) else (value,)
        for item in values:
            if item.tzinfo is None or item.utcoffset() is None:
                raise ValueError("cadence timestamps must include a timezone")
        normalized = tuple(item.astimezone(UTC) for item in values)
        return normalized if isinstance(value, tuple) else normalized[0]

    @model_validator(mode="after")
    def validate_state(self) -> CadenceReadiness:
        if self.passed and self.reasons:
            raise ValueError("passed cadence readiness cannot contain reasons")
        if not self.passed and not self.reasons:
            raise ValueError("failed cadence readiness requires reasons")
        if tuple(sorted(self.expected_cutoffs)) != self.expected_cutoffs:
            raise ValueError("expected cadence cutoffs must be sorted")
        return self


class CadenceGate:
    """Check that all closed observation buckets exist before a decision."""

    def __init__(self, policy: CadencePolicy | None = None) -> None:
        self.policy = policy or CadencePolicy()

    def expected_observation_cutoffs(self, cutoff: datetime) -> tuple[datetime, ...]:
        cutoff = self._closed(cutoff, "decision cutoff")
        interval = timedelta(seconds=self.policy.observation_interval_seconds)
        count = self.policy.decision_interval_seconds // self.policy.observation_interval_seconds
        start = cutoff - timedelta(seconds=self.policy.decision_interval_seconds) + interval
        return tuple(start + interval * index for index in range(count))

    def check(
        self,
        *,
        cutoff: datetime,
        observed_cutoffs: tuple[datetime, ...] | list[datetime],
        snapshot_as_of: datetime | None = None,
        snapshot_quality: str | None = None,
    ) -> CadenceReadiness:
        normalized_cutoff = self._closed(cutoff, "decision cutoff")
        expected = self.expected_observation_cutoffs(normalized_cutoff)
        observed = tuple(
            sorted({self._closed(item, "observation cutoff") for item in observed_cutoffs})
        )
        missing = tuple(item for item in expected if item not in set(observed))
        reasons = ["missing_closed_observation_data"] if missing else []
        if snapshot_as_of is not None:
            normalized_snapshot = self._aware(snapshot_as_of, "snapshot cutoff")
            if normalized_snapshot > normalized_cutoff:
                reasons.append("snapshot_after_cutoff")
        if snapshot_quality is not None and snapshot_quality.lower() not in {"validated", "gold"}:
            reasons.append(f"data_quality:{snapshot_quality}")
        return CadenceReadiness(
            cutoff=normalized_cutoff,
            expected_cutoffs=expected,
            observed_cutoffs=observed,
            missing_cutoffs=missing,
            passed=not reasons,
            reasons=tuple(dict.fromkeys(reasons)),
        )

    @staticmethod
    def _aware(value: datetime, label: str) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{label} must include a timezone")
        return value.astimezone(UTC)

    def _closed(self, value: datetime, label: str) -> datetime:
        normalized = self._aware(value, label)
        interval = self.policy.observation_interval_seconds
        if int(normalized.timestamp()) % interval:
            raise ValueError(f"{label} must align to the configured cadence")
        return normalized


__all__ = ["CadenceGate", "CadencePolicy", "CadenceReadiness"]
