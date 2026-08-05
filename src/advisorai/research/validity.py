"""Past-only, purged, multiple-testing, sensitivity, and regime guards."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PurgedWalkForward(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    embargo_seconds: int = Field(ge=0)
    passed: bool
    reason: str

    @field_validator("train_start", "train_end", "validation_start", "validation_end")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("walk-forward timestamps must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_split(self) -> PurgedWalkForward:
        if self.passed:
            if not self.train_start < self.train_end <= self.validation_start < self.validation_end:
                raise ValueError("a passed split must be strictly past-only")
            if (self.validation_start - self.train_end).total_seconds() < self.embargo_seconds:
                raise ValueError("a passed split must retain its embargo")
        if not self.reason.strip():
            raise ValueError("walk-forward split requires a reason")
        return self

    @classmethod
    def make(
        cls,
        *,
        train_start: datetime,
        train_end: datetime,
        validation_start: datetime,
        validation_end: datetime,
        embargo_seconds: int,
    ) -> PurgedWalkForward:
        passed = train_start < train_end <= validation_start < validation_end
        if passed and (validation_start - train_end).total_seconds() < embargo_seconds:
            passed = False
        return cls(
            train_start=train_start,
            train_end=train_end,
            validation_start=validation_start,
            validation_end=validation_end,
            embargo_seconds=embargo_seconds,
            passed=passed,
            reason="past-only purged split" if passed else "overlap or insufficient embargo",
        )


class MultipleTestingAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tests_run: int = Field(gt=0)
    raw_p_value: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    adjusted_p_value: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    alpha: Decimal = Field(gt=Decimal("0"), le=Decimal("1"))
    passed: bool

    @model_validator(mode="after")
    def validate_adjustment(self) -> MultipleTestingAudit:
        expected = min(Decimal("1"), self.raw_p_value * self.tests_run)
        if self.adjusted_p_value != expected:
            raise ValueError("adjusted p-value must match Bonferroni correction")
        if self.passed != (self.adjusted_p_value < self.alpha):
            raise ValueError("multiple-testing pass state does not match adjusted p-value")
        return self

    @classmethod
    def bonferroni(
        cls, *, tests_run: int, raw_p_value: Decimal, alpha: Decimal
    ) -> MultipleTestingAudit:
        if tests_run < 1:
            raise ValueError("tests_run must be positive")
        adjusted = min(Decimal("1"), raw_p_value * tests_run)
        return cls(
            tests_run=tests_run,
            raw_p_value=raw_p_value,
            adjusted_p_value=adjusted,
            alpha=alpha,
            passed=adjusted < alpha,
        )


class SensitivityResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parameter: str
    values_tested: tuple[Decimal, ...]
    utilities: tuple[Decimal, ...]
    worst_case_utility: Decimal
    stable: bool

    @model_validator(mode="after")
    def validate_sensitivity(self) -> SensitivityResult:
        if not self.parameter.strip() or not self.values_tested:
            raise ValueError("sensitivity requires a parameter and tested values")
        if len(self.values_tested) != len(self.utilities):
            raise ValueError("sensitivity values and utilities must have equal lengths")
        if self.worst_case_utility != min(self.utilities):
            raise ValueError("worst_case_utility must match the tested utilities")
        if any(not value.is_finite() for value in (*self.values_tested, *self.utilities)):
            raise ValueError("sensitivity values and utilities must be finite")
        return self


class RegimeSplit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    regime: str
    observations: int = Field(ge=0)
    net_utility: Decimal
    passed: bool

    @model_validator(mode="after")
    def validate_regime(self) -> RegimeSplit:
        if not self.regime.strip():
            raise ValueError("regime name is required")
        if self.passed and (self.observations < 1 or self.net_utility <= 0):
            raise ValueError("a passed regime split needs positive observed utility")
        if not self.net_utility.is_finite():
            raise ValueError("regime utility must be finite")
        return self


def evaluate_sensitivity(
    *,
    parameter: str,
    values_tested: tuple[Decimal, ...],
    utilities: tuple[Decimal, ...],
    maximum_utility_range: Decimal,
) -> SensitivityResult:
    if not parameter.strip() or not values_tested:
        raise ValueError("sensitivity needs a parameter and at least one tested value")
    if len(values_tested) != len(utilities):
        raise ValueError("sensitivity values and utilities must have equal length")
    if maximum_utility_range < 0:
        raise ValueError("maximum utility range cannot be negative")
    utility_range = max(utilities) - min(utilities)
    return SensitivityResult(
        parameter=parameter,
        values_tested=values_tested,
        utilities=utilities,
        worst_case_utility=min(utilities),
        stable=utility_range <= maximum_utility_range,
    )


def evaluate_regime(
    *, regime: str, utilities: tuple[Decimal, ...], minimum_observations: int = 1
) -> RegimeSplit:
    if not regime.strip() or not utilities:
        raise ValueError("regime evaluation needs a name and observations")
    if minimum_observations < 1:
        raise ValueError("minimum_observations must be positive")
    net_utility = sum(utilities)
    return RegimeSplit(
        regime=regime,
        observations=len(utilities),
        net_utility=net_utility,
        passed=len(utilities) >= minimum_observations and net_utility > 0,
    )
