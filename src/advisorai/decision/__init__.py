"""Typed Decision Model boundary: evidence in, proposal out, never authority.

The Decision Model synthesizes calibrated evidence into a typed proposal for
the deterministic Portfolio Constructor. It cannot change RiskPolicy, submit
or cancel orders, read broker secrets, override EvidenceGraph failure, or
self-promote. Invalid, late, or unavailable output becomes missing evidence,
a permitted fallback, or ABSTAIN.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.contracts.core import ArtifactModel


class DecisionStance(StrEnum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"
    ABSTAIN = "abstain"


class DecisionModelArtifact(ArtifactModel):
    """Exact model/adapter/code/config/snapshot/evidence identity for replay."""

    model_name: str = Field(min_length=1)
    adapter_name: str = Field(min_length=1)
    code_hash: str = Field(min_length=64, max_length=64)
    config_hash: str = Field(min_length=64, max_length=64)
    snapshot_id: UUID
    evidence_bundle_id: UUID | None = None


class DecisionProposal(ArtifactModel):
    """Typed proposal consumed only by the Portfolio Constructor."""

    mission_id: UUID
    snapshot_id: UUID
    model_artifact_id: UUID
    stance: DecisionStance
    probabilities: dict[str, Decimal] = Field(default_factory=dict)
    calibrated_confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    score: Decimal = Field(default=Decimal("0"))
    horizon: str = Field(min_length=1)
    regime_class: str | None = None
    uncertainty: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    evidence_ids: tuple[UUID, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    disagreement: tuple[str, ...] = ()
    expires_at: datetime
    abstained: bool = False
    fallback_used: bool = False
    rationale: str = Field(default="")

    @field_validator("expires_at")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("proposal expiry must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_proposal(self) -> DecisionProposal:
        if self.stance is DecisionStance.ABSTAIN and not self.abstained:
            raise ValueError("ABSTAIN stance requires the abstained flag")
        if self.abstained and self.stance is not DecisionStance.ABSTAIN:
            raise ValueError("an abstained proposal must use the ABSTAIN stance")
        if self.abstained and self.calibrated_confidence != Decimal("0"):
            raise ValueError("abstained proposals must carry zero confidence")
        if not self.abstained and not self.evidence_ids:
            raise ValueError("non-abstained proposals require evidence references")
        total = sum(self.probabilities.values(), Decimal("0"))
        if self.probabilities and abs(total - Decimal("1")) > Decimal("0.000001"):
            raise ValueError("proposal probabilities must sum to one")
        if any(value < 0 or value > 1 for value in self.probabilities.values()):
            raise ValueError("proposal probabilities must be within [0, 1]")
        return self


class DecisionModelInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mission_id: UUID
    snapshot_id: UUID
    evidence_ids: tuple[UUID, ...]
    consensus: str = Field(min_length=1)
    missing_evidence: tuple[str, ...] = ()
    disagreement: tuple[str, ...] = ()
    gate_passed: bool = False


@runtime_checkable
class DecisionModelPort(Protocol):
    """Replaceable typed decision boundary; never order/risk authority."""

    @property
    def artifact(self) -> DecisionModelArtifact: ...

    def propose(self, request: DecisionModelInput, *, now: datetime) -> DecisionProposal: ...


class ReferenceDecisionAdapter:
    """Deterministic local reference behind the port.

    Gate failure (or missing/expired input) yields a typed ABSTAIN, never a
    silent fallback stance. A missing or unqualified model therefore produces
    safe no-trade behavior downstream.
    """

    def __init__(self, artifact: DecisionModelArtifact) -> None:
        self._artifact = artifact

    @property
    def artifact(self) -> DecisionModelArtifact:
        return self._artifact

    def propose(self, request: DecisionModelInput, *, now: datetime) -> DecisionProposal:
        current = now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
        expiry = current.replace(tzinfo=UTC)
        from datetime import timedelta

        expiry = current + timedelta(hours=1)
        if not request.gate_passed or not request.evidence_ids:
            return DecisionProposal(
                mission_id=request.mission_id,
                snapshot_id=request.snapshot_id,
                model_artifact_id=self._artifact.artifact_id,
                stance=DecisionStance.ABSTAIN,
                calibrated_confidence=Decimal("0"),
                score=Decimal("0"),
                horizon="1h",
                uncertainty=Decimal("1"),
                evidence_ids=(),
                missing_evidence=request.missing_evidence or ("evidence_gate_not_passed",),
                disagreement=request.disagreement,
                expires_at=expiry,
                abstained=True,
                fallback_used=True,
                rationale="reference adapter abstained: evidence gate did not pass",
            )
        return DecisionProposal(
            mission_id=request.mission_id,
            snapshot_id=request.snapshot_id,
            model_artifact_id=self._artifact.artifact_id,
            stance=DecisionStance.FLAT,
            probabilities={"long": Decimal("0.2"), "short": Decimal("0.2"), "flat": Decimal("0.6")},
            calibrated_confidence=Decimal("0.5"),
            score=Decimal("0"),
            horizon="1h",
            uncertainty=Decimal("0.5"),
            evidence_ids=request.evidence_ids,
            missing_evidence=request.missing_evidence,
            disagreement=request.disagreement,
            expires_at=expiry,
            rationale="reference adapter: insufficient signal for directional stance",
        )


__all__ = [
    "DecisionModelArtifact",
    "DecisionModelInput",
    "DecisionModelPort",
    "DecisionProposal",
    "DecisionStance",
    "ReferenceDecisionAdapter",
]
