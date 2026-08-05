"""Gate-driven V3-Core paper soak records."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers


class FailureScenario(StrEnum):
    PRICE_GAP = "price_gap"
    VOLATILITY_JUMP = "volatility_jump"
    CORRELATION_BREAKDOWN = "correlation_breakdown"
    SPREAD_DEPTH_COLLAPSE = "spread_depth_collapse"
    HALT_DELIST = "halt_delist"
    FUNDING_LIQUIDATION_CASCADE = "funding_liquidation_cascade"
    STABLECOIN_DEPEG = "stablecoin_depeg"
    VENUE_OUTAGE = "venue_outage"
    WITHDRAWAL_FREEZE = "withdrawal_freeze"
    COUNTERPARTY_FAILURE = "counterparty_failure"
    STALE_DUPLICATE_DATA = "stale_duplicate_data"
    CLOCK_DRIFT = "clock_drift"
    DUPLICATE_PARTIAL_FILL = "duplicate_partial_fill"


class SoakSample(BaseModel):
    """One interval's counters; counts are aggregated by the gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_id: UUID = Field(default_factory=uuid4)
    at: datetime
    decision_count: int = Field(ge=0)
    trade_count: int = Field(ge=0)
    net_utility_after_costs: Decimal
    resource_stable: bool
    reconciliation_clean: bool
    safety_clean: bool
    adverse_scenarios: tuple[FailureScenario, ...] = ()
    data_scorecard_passed: bool = True
    model_scorecard_passed: bool = True
    agent_scorecard_passed: bool = True
    risk_scorecard_passed: bool = True
    execution_scorecard_passed: bool = True
    headroom_gib: Decimal | None = None
    no_trade_net_utility: Decimal | None = None
    benchmark_net_utility: Decimal | None = None

    @field_validator("net_utility_after_costs")
    @classmethod
    def require_finite_utility(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("soak utility must be finite")
        return value

    @field_validator("headroom_gib", "no_trade_net_utility", "benchmark_net_utility")
    @classmethod
    def require_finite_scorecard_values(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("soak scorecard values must be finite")
        return value

    @field_validator("at")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("soak sample timestamp must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_sample_counts(self) -> SoakSample:
        if self.trade_count > self.decision_count:
            raise ValueError("soak trades cannot exceed decisions")
        if len(self.adverse_scenarios) != len(set(self.adverse_scenarios)):
            raise ValueError("soak adverse scenarios must be unique per sample")
        if self.headroom_gib is not None and self.headroom_gib < 0:
            raise ValueError("soak headroom cannot be negative")
        return self


class SoakGate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    calendar_days: int = Field(ge=0)
    decision_count: int = Field(ge=0)
    trade_count: int = Field(ge=0)
    adverse_scenarios_seen: tuple[FailureScenario, ...]
    resources_stable: bool
    unresolved_reconciliation: bool
    unresolved_safety_incident: bool
    net_utility_after_costs: Decimal
    passed: bool
    reasons: tuple[str, ...] = ()
    minimum_headroom_gib: Decimal = Field(default=Decimal("1.5"), ge=Decimal("1.5"))

    @field_validator("net_utility_after_costs")
    @classmethod
    def require_finite_utility(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("soak utility must be finite")
        return value

    @field_validator("minimum_headroom_gib")
    @classmethod
    def require_finite_headroom(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("soak minimum headroom must be finite and non-negative")
        return value

    @model_validator(mode="after")
    def validate_gate_record(self) -> SoakGate:
        if len(self.adverse_scenarios_seen) != len(set(self.adverse_scenarios_seen)):
            raise ValueError("soak adverse scenarios must be unique")
        if self.passed:
            if self.calendar_days < 60:
                raise ValueError("a passed soak gate requires at least 60 calendar days")
            if self.decision_count < 1 or self.trade_count < 1:
                raise ValueError("a passed soak gate requires a meaningful sample")
            if not self.adverse_scenarios_seen:
                raise ValueError("a passed soak gate requires adverse-condition evidence")
            if (
                not self.resources_stable
                or self.unresolved_reconciliation
                or self.unresolved_safety_incident
                or self.net_utility_after_costs <= 0
                or self.reasons
            ):
                raise ValueError("a passed soak gate cannot contain unresolved failures")
        return self


class PaperSoakController:
    def __init__(self, started_at: datetime, *, ledgers: SqliteLedgers | None = None) -> None:
        self.started_at = self._aware(started_at)
        self.ledgers = ledgers
        self.samples: list[SoakSample] = []
        self._by_id: dict[UUID, SoakSample] = {}
        if ledgers is not None:
            self._hydrate()

    def _hydrate(self) -> None:
        assert self.ledgers is not None
        for event in self.ledgers.events(LedgerNamespace.INCIDENT):
            if event.event_type != "soak_sample_recorded":
                continue
            payload = event.payload.get("sample")
            if not isinstance(payload, dict):
                raise ValueError("soak ledger contains an invalid sample payload")
            sample = SoakSample.model_validate(payload)
            self._record_local(sample)

    def _record_local(self, sample: SoakSample) -> None:
        if sample.at < self.started_at:
            raise ValueError("soak sample precedes soak start")
        prior = self._by_id.get(sample.sample_id)
        if prior is not None:
            if prior != sample:
                raise ValueError("soak sample ID is immutable")
            return
        self._by_id[sample.sample_id] = sample
        self.samples.append(sample)

    def record(self, sample: SoakSample) -> None:
        prior = self._by_id.get(sample.sample_id)
        self._record_local(sample)
        if prior is not None:
            return
        if self.ledgers is not None:
            self.ledgers.append(
                LedgerEvent(
                    namespace=LedgerNamespace.INCIDENT,
                    event_type="soak_sample_recorded",
                    idempotency_key=f"soak:{sample.sample_id}",
                    payload={"sample": sample.model_dump(mode="json", round_trip=True)},
                )
            )

    def gate(
        self,
        *,
        minimum_decisions: int,
        minimum_trades: int,
        minimum_headroom_gib: Decimal = Decimal("1.5"),
    ) -> SoakGate:
        if not self.samples:
            raise ValueError("cannot evaluate a soak without samples")
        if minimum_decisions < 1 or minimum_trades < 1:
            raise ValueError("soak minimum samples must be positive")
        if not isinstance(minimum_headroom_gib, Decimal):
            minimum_headroom_gib = Decimal(str(minimum_headroom_gib))
        if not minimum_headroom_gib.is_finite() or minimum_headroom_gib < Decimal("1.5"):
            raise ValueError("soak minimum headroom must be finite and at least 1.5 GiB")
        latest = max(self.samples, key=lambda sample: sample.at)
        days = (latest.at - self.started_at).days
        scenarios = tuple(
            sorted(
                {scenario for sample in self.samples for scenario in sample.adverse_scenarios},
                key=str,
            )
        )
        reasons: list[str] = []
        if days < 60:
            reasons.append("less_than_60_calendar_days")
        decision_count = sum(sample.decision_count for sample in self.samples)
        trade_count = sum(sample.trade_count for sample in self.samples)
        net_utility = sum(sample.net_utility_after_costs for sample in self.samples)
        if decision_count < minimum_decisions:
            reasons.append("insufficient_decision_sample")
        if trade_count < minimum_trades:
            reasons.append("insufficient_trade_sample")
        if not scenarios:
            reasons.append("no_adverse_conditions_observed_or_injected")
        if not all(sample.resource_stable for sample in self.samples):
            reasons.append("resource_instability")
        scorecard_fields = (
            "data_scorecard_passed",
            "model_scorecard_passed",
            "agent_scorecard_passed",
            "risk_scorecard_passed",
            "execution_scorecard_passed",
        )
        if any(not getattr(sample, field) for sample in self.samples for field in scorecard_fields):
            reasons.append("scorecard_failure")
        measured_headroom = [sample.headroom_gib for sample in self.samples]
        if any(value is not None and value < minimum_headroom_gib for value in measured_headroom):
            reasons.append("resource_headroom_breach")
        no_trade_values = [
            value
            for value in (sample.no_trade_net_utility for sample in self.samples)
            if value is not None
        ]
        benchmark_values = [
            value
            for value in (sample.benchmark_net_utility for sample in self.samples)
            if value is not None
        ]
        if no_trade_values and net_utility <= sum(no_trade_values, Decimal("0")):
            reasons.append("no_trade_not_beaten")
        if benchmark_values and net_utility <= sum(benchmark_values, Decimal("0")):
            reasons.append("benchmark_not_beaten")
        if any(not sample.reconciliation_clean for sample in self.samples):
            reasons.append("unresolved_reconciliation")
        if any(not sample.safety_clean for sample in self.samples):
            reasons.append("unresolved_safety_incident")
        if net_utility <= 0:
            reasons.append("non_positive_net_utility")
        return SoakGate(
            calendar_days=days,
            decision_count=decision_count,
            trade_count=trade_count,
            adverse_scenarios_seen=scenarios,
            resources_stable=not any(not sample.resource_stable for sample in self.samples),
            unresolved_reconciliation=any(
                not sample.reconciliation_clean for sample in self.samples
            ),
            unresolved_safety_incident=any(not sample.safety_clean for sample in self.samples),
            net_utility_after_costs=net_utility,
            passed=not reasons,
            reasons=tuple(reasons),
            minimum_headroom_gib=minimum_headroom_gib,
        )

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("soak timestamp must include a timezone")
        return value.astimezone(UTC)
