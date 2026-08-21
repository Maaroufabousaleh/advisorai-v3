"""Immutable V1 human-governance and live-risk policy contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .authorization import HumanAuthorization
from .hashing import canonical_sha256


def _finite_non_negative(value: Decimal, field_name: str) -> Decimal:
    if not value.is_finite() or value < 0:
        raise ValueError(f"{field_name} must be finite and non-negative")
    return value


def _finite_ratio(value: Decimal, field_name: str) -> Decimal:
    _finite_non_negative(value, field_name)
    if value > 1:
        raise ValueError(f"{field_name} must be at most 1")
    return value


class AllocationStage(BaseModel):
    """A human-enabled allocation stage; stages never advance automatically."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    stage: int = Field(ge=0)
    fraction: Decimal

    @field_validator("fraction")
    @classmethod
    def validate_fraction(cls, value: Decimal) -> Decimal:
        return _finite_ratio(value, "allocation stage fraction")


class CorrelatedExposureGroup(BaseModel):
    """Named instruments whose directional exposure is assessed together."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    group_id: str = Field(min_length=1)
    instruments: tuple[str, ...] = Field(min_length=2)

    @field_validator("instruments")
    @classmethod
    def normalize_instruments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().upper() for item in value)
        if any(not item for item in normalized):
            raise ValueError("correlated exposure instruments cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("correlated exposure instruments must be unique")
        return normalized


class AggregateGroupExposureLimit(BaseModel):
    """Optional reviewed cap for one correlated group.

    ``None`` is intentional: V1 records the group and requires an independent
    valid group-exposure assessment without inventing a production threshold.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    group_id: str = Field(min_length=1)
    max_fraction: Decimal | None = None

    @field_validator("max_fraction")
    @classmethod
    def validate_fraction(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return _finite_ratio(value, "aggregate group exposure limit")


class GovernancePolicy(BaseModel):
    """Frozen policy values used by the governance decision function.

    This contract is not an activation switch.  In particular,
    ``live_capital_authorized`` defaults to false and a separate, valid human
    authorization plus the Phase-10 gate is still required by
    :func:`evaluate_live_activation`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    live_capital_authorized: bool = False
    initial_live_fraction_of_planned_allocation: Decimal = Decimal("0.05")
    allocation_stages: tuple[AllocationStage, ...] = ()
    soft_daily_loss_fraction: Decimal = Decimal("-0.005")
    hard_daily_loss_fraction: Decimal = Decimal("-0.01")
    soft_drawdown_fraction: Decimal = Decimal("-0.04")
    hard_drawdown_fraction: Decimal = Decimal("-0.06")
    derisk_risk_multiplier: Decimal = Decimal("0.50")
    quarter_kelly_multiplier: Decimal = Decimal("0.25")
    max_single_asset_fraction: Decimal = Decimal("0.15")
    normal_max_gross_leverage: Decimal = Decimal("1.00")
    hard_max_gross_leverage: Decimal = Decimal("1.25")
    future_leverage_enabled: bool = False
    high_confidence_threshold: Decimal = Decimal("0.90")
    medium_confidence_threshold: Decimal = Decimal("0.70")
    conservative_cost_safety_factor: Decimal = Decimal("2.0")
    correlated_exposure_groups: tuple[CorrelatedExposureGroup, ...] = ()
    aggregate_group_exposure_limits: tuple[AggregateGroupExposureLimit, ...] = ()
    risk_kernel_final_veto: bool = True
    oms_authoritative: bool = True
    llm_execution_authority: bool = False
    human_silence_is_approval: bool = False
    content_hash: str = ""

    @field_validator(
        "initial_live_fraction_of_planned_allocation",
        "derisk_risk_multiplier",
        "quarter_kelly_multiplier",
        "max_single_asset_fraction",
        "normal_max_gross_leverage",
        "hard_max_gross_leverage",
        "high_confidence_threshold",
        "medium_confidence_threshold",
        "conservative_cost_safety_factor",
    )
    @classmethod
    def validate_non_negative_policy_values(cls, value: Decimal, info) -> Decimal:
        return _finite_non_negative(value, info.field_name)

    @field_validator("content_hash")
    @classmethod
    def validate_optional_digest(cls, value: str) -> str:
        if value and (
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError("content_hash must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def validate_policy(self) -> GovernancePolicy:
        if not 0 < self.initial_live_fraction_of_planned_allocation <= 1:
            raise ValueError("initial live allocation fraction must be in (0, 1]")
        if not self.allocation_stages:
            raise ValueError("allocation stages are required")
        stages = tuple(stage.stage for stage in self.allocation_stages)
        if stages != tuple(sorted(set(stages))) or stages[0] != 0:
            raise ValueError("allocation stages must be unique, sorted, and start at stage 0")
        if self.allocation_stages[0].fraction != self.initial_live_fraction_of_planned_allocation:
            raise ValueError("stage 0 must equal the initial live allocation fraction")
        if self.hard_daily_loss_fraction >= self.soft_daily_loss_fraction:
            raise ValueError("hard daily loss must be more negative than soft daily loss")
        if self.hard_drawdown_fraction >= self.soft_drawdown_fraction:
            raise ValueError("hard drawdown must be more negative than soft drawdown")
        if not 0 < self.derisk_risk_multiplier <= 1:
            raise ValueError("derisk risk multiplier must be in (0, 1]")
        if not 0 < self.quarter_kelly_multiplier <= 1:
            raise ValueError("quarter-Kelly multiplier must be in (0, 1]")
        if not 0 < self.max_single_asset_fraction <= 1:
            raise ValueError("single-asset fraction must be in (0, 1]")
        if self.hard_max_gross_leverage < self.normal_max_gross_leverage:
            raise ValueError("hard leverage ceiling cannot be below normal leverage")
        if self.hard_max_gross_leverage > Decimal("1.25"):
            raise ValueError("V1 hard leverage ceiling cannot exceed 1.25")
        if not (0 <= self.medium_confidence_threshold < self.high_confidence_threshold <= 1):
            raise ValueError("confidence thresholds must satisfy 0 <= medium < high <= 1")
        if self.conservative_cost_safety_factor < 1:
            raise ValueError("conservative cost safety factor must be at least 1")
        group_ids = tuple(group.group_id for group in self.correlated_exposure_groups)
        limit_ids = tuple(limit.group_id for limit in self.aggregate_group_exposure_limits)
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("correlated exposure group identifiers must be unique")
        if len(limit_ids) != len(set(limit_ids)):
            raise ValueError("aggregate group exposure limit identifiers must be unique")
        if not set(limit_ids).issubset(group_ids):
            raise ValueError("aggregate group exposure limits must reference known groups")
        if not self.risk_kernel_final_veto or not self.oms_authoritative:
            raise ValueError("RiskKernel veto and OMS authority are non-negotiable")
        if self.llm_execution_authority:
            raise ValueError("LLM execution authority is prohibited")
        if self.human_silence_is_approval:
            raise ValueError("human silence cannot be approval")
        expected_hash = self._computed_content_hash()
        if self.content_hash and self.content_hash != expected_hash:
            raise ValueError("governance policy content_hash does not match policy content")
        object.__setattr__(self, "content_hash", expected_hash)
        return self

    def _canonical_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"content_hash"})

    def _computed_content_hash(self) -> str:
        return canonical_sha256(self._canonical_payload())

    @property
    def policy_hash(self) -> str:
        return self.content_hash

    def group_for_instrument(self, instrument: str) -> CorrelatedExposureGroup | None:
        normalized = instrument.strip().upper()
        matches = [
            group for group in self.correlated_exposure_groups if normalized in group.instruments
        ]
        if len(matches) > 1:
            raise ValueError(f"instrument belongs to multiple correlated groups: {normalized}")
        return matches[0] if matches else None


class PositionSizingInput(BaseModel):
    """Already-reviewed caps supplied to the quarter-Kelly policy boundary.

    The policy intentionally does not estimate Kelly inputs.  Missing inputs
    fail closed rather than being filled with a model or account-balance guess.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    raw_kelly_fraction: Decimal | None = None
    volatility_cap_fraction: Decimal | None = None
    liquidity_cap_fraction: Decimal | None = None
    correlation_cap_fraction: Decimal | None = None
    risk_kernel_cap_fraction: Decimal | None = None

    @field_validator(
        "raw_kelly_fraction",
        "volatility_cap_fraction",
        "liquidity_cap_fraction",
        "correlation_cap_fraction",
        "risk_kernel_cap_fraction",
    )
    @classmethod
    def validate_caps(cls, value: Decimal | None, info) -> Decimal | None:
        if value is None:
            return None
        return _finite_non_negative(value, info.field_name)


class PositionSizingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    allowed: bool
    final_fraction: Decimal | None = None
    quarter_kelly_fraction: Decimal | None = None
    applied_limits: tuple[str, ...] = ()
    reason: str | None = None


def apply_quarter_kelly(
    policy: GovernancePolicy, sizing: PositionSizingInput
) -> PositionSizingResult:
    """Apply the reviewed quarter-Kelly boundary without estimating Kelly."""

    required = (
        sizing.raw_kelly_fraction,
        sizing.volatility_cap_fraction,
        sizing.liquidity_cap_fraction,
        sizing.correlation_cap_fraction,
        sizing.risk_kernel_cap_fraction,
    )
    if any(value is None for value in required):
        return PositionSizingResult(allowed=False, reason="POSITION_SIZING_INPUT_UNKNOWN")
    assert all(value is not None for value in required)
    quarter = sizing.raw_kelly_fraction * policy.quarter_kelly_multiplier
    limits = (
        ("volatility", sizing.volatility_cap_fraction),
        ("liquidity", sizing.liquidity_cap_fraction),
        ("correlation", sizing.correlation_cap_fraction),
        ("single_asset", policy.max_single_asset_fraction),
        ("risk_kernel", sizing.risk_kernel_cap_fraction),
    )
    final = quarter
    applied: list[str] = []
    for name, limit in limits:
        assert limit is not None
        if final > limit:
            final = limit
            applied.append(name)
    return PositionSizingResult(
        allowed=True,
        final_fraction=final,
        quarter_kelly_fraction=quarter,
        applied_limits=tuple(applied),
    )


class LiveActivationInput(BaseModel):
    """Inputs for a non-mutating Phase-10 activation decision."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    planned_advisorai_capital: Decimal | None = None
    authorization: HumanAuthorization | None = None
    phase10_gate_passed: bool = False
    evaluated_at: datetime
    input_snapshot_hash: str = Field(min_length=64, max_length=64)

    @field_validator("evaluated_at")
    @classmethod
    def normalize_evaluated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("live activation evaluation time must include a timezone")
        return value.astimezone(UTC)

    @field_validator("planned_advisorai_capital")
    @classmethod
    def validate_capital(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (not value.is_finite() or value <= 0):
            raise ValueError("planned AdvisorAI capital must be positive and finite")
        return value

    @field_validator("input_snapshot_hash")
    @classmethod
    def validate_snapshot_hash(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError("input_snapshot_hash must be a lowercase SHA-256 digest")
        return value


def load_governance_policy(path: Path) -> GovernancePolicy:
    """Load one pinned, local YAML policy; no network access is performed."""

    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"governance policy {path} must contain an object")
    return GovernancePolicy.model_validate(payload)
