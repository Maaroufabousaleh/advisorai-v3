"""Point-in-time equity expansion boundaries.

The equity council is deliberately research/paper-only.  It consumes frozen
evidence and corporate-action records, returns a typed admission result, and
does not expose a target portfolio or an order path.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.agents.fusion import EvidenceGateResult, EvidenceGraph
from advisorai.contracts import AssetClass, Evidence, InstrumentIdentity, Snapshot


class CorporateActionType(StrEnum):
    DIVIDEND = "dividend"
    SPLIT = "split"
    MERGER = "merger"
    SPINOFF = "spinoff"


class CorporateAction(BaseModel):
    """Immutable point-in-time corporate-action input for an equity snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: UUID = Field(default_factory=uuid4)
    instrument: InstrumentIdentity
    action_type: CorporateActionType
    announced_at: datetime | None = None
    effective_at: datetime
    first_available_at: datetime
    ingested_at: datetime
    ratio: Decimal | None = None
    cash_amount: Decimal | None = None
    source_artifact_hash: str = Field(min_length=64, max_length=64)

    @field_validator("announced_at", "effective_at", "first_available_at", "ingested_at")
    @classmethod
    def require_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("corporate-action timestamps must include a timezone")
        return value.astimezone(UTC)

    @field_validator("source_artifact_hash")
    @classmethod
    def require_digest(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("corporate-action source hash must be a lowercase SHA-256 digest")
        return value

    @field_validator("ratio", "cash_amount")
    @classmethod
    def require_finite_amounts(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (not value.is_finite() or value < 0):
            raise ValueError("corporate-action amounts must be finite and non-negative")
        return value

    @model_validator(mode="after")
    def validate_action(self) -> CorporateAction:
        if self.instrument.asset_class is not AssetClass.EQUITY:
            raise ValueError("equity corporate actions require an equity instrument")
        if self.ingested_at < self.first_available_at:
            raise ValueError("corporate-action ingestion cannot precede availability")
        if self.announced_at is not None and self.announced_at > self.first_available_at:
            raise ValueError("corporate-action availability cannot precede its announcement")
        if self.action_type is CorporateActionType.SPLIT and (
            self.ratio is None or self.ratio <= 0
        ):
            raise ValueError("stock splits require a positive ratio")
        if self.action_type is CorporateActionType.DIVIDEND and self.cash_amount is None:
            raise ValueError("dividends require a cash amount")
        return self


class EquityEvidence(BaseModel):
    """Evidence plus the factor-family identity used for independence checks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence: Evidence
    factor_family: str = Field(min_length=1)

    @field_validator("factor_family")
    @classmethod
    def normalize_factor_family(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("equity evidence factor family cannot be blank")
        return value.strip()


class EquityDailyCouncilResult(BaseModel):
    """A research/paper admission result; never an execution instruction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: UUID
    as_of: datetime
    evidence_ids: tuple[UUID, ...]
    corporate_action_ids: tuple[UUID, ...]
    gate: EvidenceGateResult
    passed: bool
    reasons: tuple[str, ...] = ()

    @field_validator("as_of")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("equity council cutoff must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_result(self) -> EquityDailyCouncilResult:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("equity council evidence IDs must be unique")
        if len(self.corporate_action_ids) != len(set(self.corporate_action_ids)):
            raise ValueError("equity council corporate-action IDs must be unique")
        if self.passed != (self.gate.passed and not self.reasons):
            raise ValueError("equity council pass state must match its gate and reasons")
        return self


class EquityDailyCouncil:
    """Evaluate one frozen equity daily evidence set with no execution authority."""

    def evaluate(
        self,
        *,
        snapshot: Snapshot,
        evidence: Iterable[EquityEvidence],
        corporate_actions: Iterable[CorporateAction] = (),
        minimum_source_families: int = 2,
        minimum_factor_families: int = 3,
    ) -> EquityDailyCouncilResult:
        records = tuple(evidence)
        actions = tuple(corporate_actions)
        graph = EvidenceGraph()
        for item in records:
            graph.add(item.evidence, factor_family=item.factor_family)
        gate = graph.gate(
            minimum_source_families=minimum_source_families,
            minimum_factor_families=minimum_factor_families,
            cutoff=snapshot.as_of,
        )
        reasons = [
            "corporate_action_unavailable_at_cutoff"
            for action in actions
            if action.first_available_at > snapshot.as_of or action.ingested_at > snapshot.as_of
        ]
        return EquityDailyCouncilResult(
            snapshot_id=snapshot.artifact_id,
            as_of=snapshot.as_of,
            evidence_ids=tuple(item.evidence.artifact_id for item in records),
            corporate_action_ids=tuple(action.action_id for action in actions),
            gate=gate,
            passed=gate.passed and not reasons,
            reasons=tuple(dict.fromkeys(reasons)),
        )
