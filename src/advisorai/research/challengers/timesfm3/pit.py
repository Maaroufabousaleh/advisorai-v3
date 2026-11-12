"""Point-in-time input contracts for the TimesFM 3 research track.

The contract makes availability explicit instead of inferring it from the
event timestamp.  A future timestamp is usable only when its value is an
explicitly classified schedule/calendar fact that was available at the
forecast cutoff.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.contracts import InstrumentIdentity


class PointInTimeViolation(ValueError):
    """Raised when a TimesFM3 research input cannot be proven point-in-time safe."""


class CovariateRole(StrEnum):
    TARGET_VARIATE = "TARGET_VARIATE"
    PAST_ONLY_COVARIATE = "PAST_ONLY_COVARIATE"
    KNOWN_FUTURE_COVARIATE = "KNOWN_FUTURE_COVARIATE"


class KnownFutureBasis(StrEnum):
    """Closed vocabulary for values genuinely knowable at a cutoff."""

    SCHEDULED_FOMC_TIMESTAMP = "scheduled_fomc_timestamp"
    SCHEDULED_CPI_TIMESTAMP = "scheduled_cpi_timestamp"
    KNOWN_EXPIRY_DATE = "known_expiry_date"
    CALENDAR = "calendar"
    SESSION = "session"


class KnownFutureValueKind(StrEnum):
    """Value semantics permitted for a genuinely known future covariate."""

    TIMESTAMP = "timestamp"
    DATE = "date"
    CALENDAR = "calendar"
    SESSION = "session"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PointInTimeViolation("TimesFM3 timestamps must include a timezone")
    return value.astimezone(UTC)


class TimesFM3CovariatePoint(BaseModel):
    """One value with the complete availability and lineage attestation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: CovariateRole
    event_at: datetime
    effective_at: datetime | None = None
    value: Decimal
    as_of: datetime
    first_available_at: datetime
    ingested_at: datetime
    source_identity: str = Field(min_length=1)
    lineage: tuple[str, ...] = Field(min_length=1)
    known_at_cutoff: bool = False
    known_future_basis: KnownFutureBasis | None = None
    known_future_value_kind: KnownFutureValueKind | None = None

    _event_aware = field_validator("event_at")(_aware)
    _effective_aware = field_validator("effective_at")(
        lambda value: _aware(value) if value else value
    )
    _as_of_aware = field_validator("as_of")(_aware)
    _first_available_aware = field_validator("first_available_at")(_aware)
    _ingested_aware = field_validator("ingested_at")(_aware)

    @field_validator("value")
    @classmethod
    def require_finite_value(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise PointInTimeViolation("TimesFM3 covariate values must be finite")
        return value

    @field_validator("source_identity")
    @classmethod
    def require_source_identity(cls, value: str) -> str:
        if not value.strip():
            raise PointInTimeViolation("TimesFM3 points require a source identity")
        return value.strip()

    @field_validator("lineage")
    @classmethod
    def require_lineage(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise PointInTimeViolation("TimesFM3 points require unique, non-blank lineage")
        return normalized

    @model_validator(mode="after")
    def validate_availability(self) -> TimesFM3CovariatePoint:
        if self.first_available_at > self.as_of:
            raise PointInTimeViolation(
                "a TimesFM3 value first available after the cutoff is future leakage"
            )
        if self.ingested_at < self.first_available_at:
            raise PointInTimeViolation("ingestion cannot precede first availability")

        if self.role in {CovariateRole.TARGET_VARIATE, CovariateRole.PAST_ONLY_COVARIATE}:
            if self.event_at > self.as_of or (
                self.effective_at is not None and self.effective_at > self.as_of
            ):
                raise PointInTimeViolation(
                    f"{self.role.value} cannot contain a future event/effective timestamp"
                )
            if (
                self.known_at_cutoff
                or self.known_future_basis is not None
                or self.known_future_value_kind is not None
            ):
                raise PointInTimeViolation(
                    "past-only and target values cannot claim known-future provenance"
                )
            return self

        if (
            self.known_at_cutoff is not True
            or self.known_future_basis is None
            or self.known_future_value_kind is None
        ):
            raise PointInTimeViolation(
                "known-future covariates require explicit cutoff knowledge, basis, and value kind"
            )
        expected_kind = {
            KnownFutureBasis.SCHEDULED_FOMC_TIMESTAMP: KnownFutureValueKind.TIMESTAMP,
            KnownFutureBasis.SCHEDULED_CPI_TIMESTAMP: KnownFutureValueKind.TIMESTAMP,
            KnownFutureBasis.KNOWN_EXPIRY_DATE: KnownFutureValueKind.DATE,
            KnownFutureBasis.CALENDAR: KnownFutureValueKind.CALENDAR,
            KnownFutureBasis.SESSION: KnownFutureValueKind.SESSION,
        }[self.known_future_basis]
        if self.known_future_value_kind is not expected_kind:
            raise PointInTimeViolation("known-future value kind does not match its closed basis")
        return self


class TimesFM3TargetSeries(BaseModel):
    """A target variate whose observations are all available by the cutoff."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    instrument: InstrumentIdentity
    points: tuple[TimesFM3CovariatePoint, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_target_points(self) -> TimesFM3TargetSeries:
        timestamps = [point.event_at for point in self.points]
        if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
            raise PointInTimeViolation("TimesFM3 target points must be strictly time ordered")
        if any(point.role is not CovariateRole.TARGET_VARIATE for point in self.points):
            raise PointInTimeViolation("target series points must be TARGET_VARIATE")
        return self


class TimesFM3CovariateSeries(BaseModel):
    """A named past-only or known-future covariate channel."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    role: CovariateRole
    points: tuple[TimesFM3CovariatePoint, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_covariate_points(self) -> TimesFM3CovariateSeries:
        if self.role is CovariateRole.TARGET_VARIATE:
            raise PointInTimeViolation("target variates belong in target_variates")
        if any(point.role is not self.role for point in self.points):
            raise PointInTimeViolation("covariate point roles must match their series role")
        timestamps = [point.event_at for point in self.points]
        if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
            raise PointInTimeViolation("TimesFM3 covariate points must be strictly time ordered")
        return self


class TimesFM3ResearchInput(BaseModel):
    """Frozen research input passed to the adapter and evaluation layer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: UUID
    cutoff: datetime
    horizon_bars: int = Field(gt=0)
    interval_seconds: int = Field(gt=0)
    target_variates: tuple[TimesFM3TargetSeries, ...] = Field(min_length=1)
    covariates: tuple[TimesFM3CovariateSeries, ...] = ()
    calibration_version: str = Field(min_length=1)

    _cutoff_aware = field_validator("cutoff")(_aware)

    @model_validator(mode="after")
    def validate_snapshot(self) -> TimesFM3ResearchInput:
        names = [series.name for series in self.target_variates]
        if len(names) != len(set(names)):
            raise PointInTimeViolation("TimesFM3 target variate names must be unique")
        covariate_names = [series.name for series in self.covariates]
        if len(covariate_names) != len(set(covariate_names)):
            raise PointInTimeViolation("TimesFM3 covariate names must be unique")
        if set(names).intersection(covariate_names):
            raise PointInTimeViolation("target and covariate names must not overlap")
        for series in (*self.target_variates, *self.covariates):
            for point in series.points:
                if point.as_of != self.cutoff:
                    raise PointInTimeViolation(
                        "every TimesFM3 input point must use the experiment cutoff as as_of"
                    )
        return self

    def canonical_pit_hash(self) -> str:
        """Hash the frozen, validated input including availability metadata."""

        return sha256(self.model_dump_json().encode("utf-8")).hexdigest()


def validate_timesfm3_point_in_time_input(
    research_input: TimesFM3ResearchInput,
) -> TimesFM3ResearchInput:
    """Explicit validation entry point used by runners and qualification jobs."""

    return TimesFM3ResearchInput.model_validate(research_input.model_dump(mode="python"))
