"""Evidence dependency graph, independence gates, and decision fusion."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.contracts import Evidence, TargetPortfolio


class EvidenceNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence: Evidence
    factor_family: str = Field(min_length=1)
    model_ancestry: str | None = None

    @field_validator("factor_family")
    @classmethod
    def normalize_factor_family(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evidence factor family cannot be blank")
        return value.strip()

    @field_validator("model_ancestry")
    @classmethod
    def normalize_model_ancestry(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("evidence model ancestry cannot be blank")
        return value.strip() if value is not None else None


class EvidenceGateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    independent_origins: tuple[str, ...]
    independent_source_families: tuple[str, ...]
    independent_factor_families: tuple[str, ...]
    discounted_evidence_ids: tuple[UUID, ...]
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_gate_consistency(self) -> EvidenceGateResult:
        if self.passed and self.reasons:
            raise ValueError("a passed evidence gate cannot retain rejection reasons")
        if self.passed and (
            not self.independent_origins
            or not self.independent_source_families
            or not self.independent_factor_families
        ):
            raise ValueError("a passed evidence gate requires independent evidence identities")
        if not self.passed and not self.reasons:
            raise ValueError("a failed evidence gate requires rejection reasons")
        if len(self.discounted_evidence_ids) != len(set(self.discounted_evidence_ids)):
            raise ValueError("discounted evidence IDs must be unique")
        return self


class EvidenceGraph:
    def __init__(self) -> None:
        self._nodes: dict[UUID, EvidenceNode] = {}

    def add(
        self, evidence: Evidence, *, factor_family: str, model_ancestry: str | None = None
    ) -> EvidenceNode:
        if not factor_family.strip():
            raise ValueError("evidence factor_family is required")
        node = EvidenceNode(
            evidence=evidence, factor_family=factor_family, model_ancestry=model_ancestry
        )
        prior = self._nodes.get(evidence.artifact_id)
        if prior is not None:
            if prior != node:
                raise ValueError("evidence artifact ID is immutable")
            return prior
        self._nodes[evidence.artifact_id] = node
        return node

    def nodes(self) -> tuple[EvidenceNode, ...]:
        return tuple(self._nodes.values())

    def gate(
        self,
        *,
        minimum_source_families: int,
        minimum_factor_families: int,
        material: bool = True,
        cutoff: datetime | None = None,
    ) -> EvidenceGateResult:
        if minimum_source_families < 1 or minimum_factor_families < 1:
            raise ValueError("evidence gates require positive minimum family counts")
        if cutoff is not None and (cutoff.tzinfo is None or cutoff.utcoffset() is None):
            raise ValueError("evidence cutoff must include a timezone")
        cutoff = cutoff.astimezone(UTC) if cutoff is not None else None
        discounted: list[UUID] = []
        eligible: list[EvidenceNode] = []
        timing_reasons: list[str] = []
        for node in self._nodes.values():
            if cutoff is not None and node.evidence.first_available_at > cutoff:
                timing_reasons.append("future_evidence_unavailable_at_cutoff")
            elif cutoff is not None and node.evidence.expires_at <= cutoff:
                timing_reasons.append("expired_evidence_at_cutoff")
            elif node.evidence.supports is False:
                # Counter-evidence remains visible in the graph but cannot
                # create a supporting quorum. If it is all that remains, the
                # decision must abstain rather than treating a rejection as a
                # positive claim.
                discounted.append(node.evidence.artifact_id)
            else:
                eligible.append(node)
        # Collapse origins connected by a syndication chain, even when the
        # copied article has a different local origin label.
        origin_groups: list[set[str]] = []
        origin_nodes: list[list[EvidenceNode]] = []
        for node in eligible:
            ancestry = {node.evidence.origin, *node.evidence.syndication_chain}
            matching = [
                index for index, group in enumerate(origin_groups) if group.intersection(ancestry)
            ]
            if not matching:
                origin_groups.append(ancestry)
                origin_nodes.append([node])
                continue
            first = matching[0]
            origin_groups[first].update(ancestry)
            origin_nodes[first].append(node)
            for index in reversed(matching[1:]):
                origin_groups[first].update(origin_groups.pop(index))
                origin_nodes[first].extend(origin_nodes.pop(index))
        independent: list[EvidenceNode] = []
        for nodes in origin_nodes:
            independent.append(nodes[0])
            discounted.extend(node.evidence.artifact_id for node in nodes[1:])
        # A second origin is not independent merely because it was summarized by
        # the same model/prompt ancestry. Explicit ancestry is supplied by the
        # role adapter when known; the evidence metadata is the safe fallback.
        by_ancestry: dict[str, list[EvidenceNode]] = defaultdict(list)
        no_ancestry: list[EvidenceNode] = []
        for node in independent:
            ancestry = node.model_ancestry or self._evidence_ancestry(node.evidence)
            if ancestry:
                by_ancestry[ancestry].append(node)
            else:
                no_ancestry.append(node)
        independent = no_ancestry
        for nodes in by_ancestry.values():
            independent.append(nodes[0])
            discounted.extend(node.evidence.artifact_id for node in nodes[1:])
        origins = tuple(sorted({node.evidence.origin for node in independent}))
        families = tuple(sorted({node.evidence.source_family for node in independent}))
        factors = tuple(sorted({node.factor_family for node in independent}))
        reasons: list[str] = list(dict.fromkeys(timing_reasons))
        if len(families) < minimum_source_families:
            reasons.append(f"source_families:{len(families)}<{minimum_source_families}")
        if len(factors) < minimum_factor_families:
            reasons.append(f"factor_families:{len(factors)}<{minimum_factor_families}")
        if not eligible and discounted:
            reasons.append("unsupported_evidence_abstained")
        if material and not self._has_deterministic_check(independent):
            reasons.append("missing_deterministic_check")
        return EvidenceGateResult(
            passed=not reasons,
            independent_origins=origins,
            independent_source_families=families,
            independent_factor_families=factors,
            discounted_evidence_ids=tuple(discounted),
            reasons=tuple(reasons),
        )

    @staticmethod
    def _has_deterministic_check(nodes: list[EvidenceNode]) -> bool:
        return any(
            node.factor_family in {"deterministic_check", "risk", "data_quality", "data_verifier"}
            for node in nodes
        )

    @staticmethod
    def _evidence_ancestry(evidence: Evidence) -> str | None:
        values = (
            evidence.model_version,
            evidence.provider_route,
            evidence.prompt_version,
            evidence.capability_version,
            *evidence.transformation_lineage,
        )
        return "|".join(value for value in values if value) or None


class DecisionBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mission_id: UUID
    snapshot_id: UUID
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    target_portfolio: TargetPortfolio
    evidence_ids: tuple[UUID, ...]
    consensus: str
    strongest_dissent: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    abstained: bool = False
    expires_at: datetime
    gate: EvidenceGateResult

    @field_validator("expires_at")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision expiry must include a timezone")
        return value.astimezone(UTC)

    @field_validator("created_at")
    @classmethod
    def require_creation_cutoff(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision creation timestamp must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_target_only_decision(self) -> DecisionBundle:
        if self.target_portfolio.snapshot_id != self.snapshot_id:
            raise ValueError("decision target portfolio must use the decision snapshot")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("decision evidence IDs must be unique")
        if not self.abstained and not self.evidence_ids:
            raise ValueError("non-abstained decisions require evidence IDs")
        if self.abstained and self.gate.passed:
            raise ValueError("a passed evidence gate cannot produce an abstained decision")
        if self.abstained and self.confidence != Decimal("0"):
            raise ValueError("abstained decisions must have zero confidence")
        if any(not item.strip() for item in (*self.strongest_dissent, *self.missing_evidence)):
            raise ValueError("decision dissent and missing-evidence entries cannot be blank")
        if len(self.strongest_dissent) != len(set(self.strongest_dissent)):
            raise ValueError("decision dissent entries must be unique")
        if len(self.missing_evidence) != len(set(self.missing_evidence)):
            raise ValueError("decision missing-evidence entries must be unique")
        if not self.consensus.strip():
            raise ValueError("decision bundles require a consensus summary")
        if not self.abstained and not self.gate.passed:
            raise ValueError("non-abstained decisions require a passed evidence gate")
        if self.expires_at <= self.created_at:
            raise ValueError("decision bundle must expire after its creation cutoff")
        return self
