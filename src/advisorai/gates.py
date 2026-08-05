"""Durable, dependency-ordered phase admission records.

The architecture uses gates rather than a calendar or a green unit-test count
to admit a component.  This module makes that rule executable: local test
evidence, timed operational evidence, and human approvals are represented as
immutable records, and a later phase cannot be recorded as passed while an
earlier phase is pending or failed.

The registry is deliberately advisory with respect to execution.  It records
what was proven; callers still have to ask the relevant runtime/authority
boundary whether a component is admitted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers


class GateEvidenceKind(StrEnum):
    LOCAL_TEST = "local_test"
    EXTERNAL_TIMED = "external_timed"
    OPERATIONAL = "operational"
    HUMAN_APPROVAL = "human_approval"


class GateDecision(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


class GateEvidence(BaseModel):
    """One auditable assertion supporting (or blocking) a phase gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1)
    kind: GateEvidenceKind
    passed: bool
    artifact_hash: str | None = None
    source: str = Field(min_length=1)
    verified_by: str = Field(min_length=1)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    details: str = ""

    @field_validator("name", "source", "verified_by", "details")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("artifact_hash")
    @classmethod
    def validate_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("gate evidence artifact_hash must be a lowercase SHA-256 digest")
        return value

    @field_validator("observed_at", "expires_at")
    @classmethod
    def require_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("gate evidence timestamps must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_evidence(self) -> GateEvidence:
        if not self.name or not self.source or not self.verified_by:
            raise ValueError("gate evidence requires a name, source, and verifier")
        if self.passed and self.artifact_hash is None:
            raise ValueError("passed gate evidence requires an artifact hash")
        if self.expires_at is not None and self.expires_at <= self.observed_at:
            raise ValueError("gate evidence expiry must be after observation")
        return self

    def is_valid_at(self, at: datetime) -> bool:
        """Return whether this evidence is still usable at a gate decision time."""

        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("gate evaluation timestamp must include a timezone")
        at = at.astimezone(UTC)
        return self.observed_at <= at and (self.expires_at is None or at < self.expires_at)


class PhaseGateRecord(BaseModel):
    """Immutable result for one numbered architecture phase."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: UUID = Field(default_factory=uuid4)
    phase: int = Field(ge=0, le=10)
    name: str = Field(min_length=1)
    decision: GateDecision
    required_evidence: tuple[str, ...] = ()
    evidence: tuple[GateEvidence, ...] = ()
    prerequisite_phase: int | None = Field(default=None, ge=0, le=10)
    recorded_by: str = Field(min_length=1)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reasons: tuple[str, ...] = ()

    @field_validator("name", "recorded_by")
    @classmethod
    def normalize_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("phase gate identity fields cannot be blank")
        return value.strip()

    @field_validator("required_evidence", "reasons")
    @classmethod
    def normalize_tokens(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("phase gate tokens must be unique and non-blank")
        return normalized

    @field_validator("recorded_at")
    @classmethod
    def require_recorded_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("phase gate recorded_at must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_gate(self) -> PhaseGateRecord:
        if self.prerequisite_phase is not None and self.prerequisite_phase >= self.phase:
            raise ValueError("phase gate prerequisite must be an earlier phase")
        if self.phase > 0 and self.prerequisite_phase not in {None, self.phase - 1}:
            raise ValueError("phase gates must depend on the immediately preceding phase")
        evidence_names = tuple(item.name for item in self.evidence)
        if len(evidence_names) != len(set(evidence_names)):
            raise ValueError("phase gate evidence names must be unique")
        if self.decision is GateDecision.PASSED:
            if not self.required_evidence:
                raise ValueError("a passed phase gate requires named evidence")
            required = set(self.required_evidence)
            available = {item.name: item for item in self.evidence}
            missing = required.difference(available)
            if missing:
                raise ValueError(f"passed phase gate is missing evidence: {sorted(missing)}")
            invalid = sorted(
                name
                for name in required
                if not available[name].passed or not available[name].is_valid_at(self.recorded_at)
            )
            if invalid:
                raise ValueError(f"passed phase gate has invalid evidence: {invalid}")
            required_items = tuple(available[name] for name in required)
            if self.phase in {0, 7} and not any(
                item.kind is GateEvidenceKind.EXTERNAL_TIMED for item in required_items
            ):
                raise ValueError(
                    f"Phase {self.phase} requires external timed evidence before admission"
                )
            if self.phase == 10 and not any(
                item.kind is GateEvidenceKind.HUMAN_APPROVAL for item in required_items
            ):
                raise ValueError("Phase 10 requires explicit human approval evidence")
            if self.reasons:
                raise ValueError("a passed phase gate cannot retain blocking reasons")
        elif self.decision is GateDecision.PENDING and not self.reasons:
            raise ValueError("a pending phase gate must explain what remains")
        elif self.decision is GateDecision.FAILED and not self.reasons:
            raise ValueError("a failed phase gate must explain the failure")
        return self

    def canonical_hash(self) -> str:
        payload = self.model_dump_json(exclude={"record_id", "recorded_at"})
        return sha256(payload.encode()).hexdigest()

    def is_valid_at(self, at: datetime) -> bool:
        """Return whether this passing record is still supported at ``at``.

        A gate may be recorded successfully and later become unusable when
        timed evidence expires.  Admission checks must therefore re-evaluate
        evidence validity instead of treating the latest ``passed`` row as a
        permanent capability grant.
        """

        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("phase gate evaluation timestamp must include a timezone")
        if self.decision is not GateDecision.PASSED:
            return False
        at = at.astimezone(UTC)
        available = {item.name: item for item in self.evidence}
        return all(
            name in available and available[name].passed and available[name].is_valid_at(at)
            for name in self.required_evidence
        )


class PhaseGateRegistry:
    """Append-only ledger projection for phase gates and admission checks."""

    EVENT_TYPE = "phase_gate_recorded"

    def __init__(self, ledgers: SqliteLedgers | None = None) -> None:
        self.ledgers = ledgers
        self._records: dict[int, list[PhaseGateRecord]] = {phase: [] for phase in range(11)}
        if ledgers is not None:
            self._hydrate()

    def _hydrate(self) -> None:
        assert self.ledgers is not None
        for event in self.ledgers.events(LedgerNamespace.MODEL):
            if event.event_type != self.EVENT_TYPE:
                continue
            payload = event.payload.get("record")
            if not isinstance(payload, dict):
                raise ValueError("phase gate ledger contains an invalid record")
            record = PhaseGateRecord.model_validate(payload)
            self._append_local(record)

    def _append_local(self, record: PhaseGateRecord) -> None:
        if record.decision is GateDecision.PASSED and record.phase > 0:
            prerequisite = record.prerequisite_phase
            if prerequisite is None:
                prerequisite = record.phase - 1
            if not self.is_admitted(prerequisite):
                raise ValueError(
                    f"phase {record.phase} cannot pass before phase {prerequisite} passes"
                )
        prior = next(
            (item for item in self._records[record.phase] if item.record_id == record.record_id),
            None,
        )
        if prior is not None:
            if prior != record:
                raise ValueError("phase gate record ID is immutable")
            return
        self._records[record.phase].append(record)

    def record(self, record: PhaseGateRecord) -> PhaseGateRecord:
        """Record a gate only after validating phase dependencies."""

        prior = next(
            (item for item in self._records[record.phase] if item.record_id == record.record_id),
            None,
        )
        if prior is not None:
            if prior != record:
                raise ValueError("phase gate record ID is immutable")
            return prior
        self._append_local(record)
        if self.ledgers is not None:
            self.ledgers.append(
                LedgerEvent(
                    namespace=LedgerNamespace.MODEL,
                    event_type=self.EVENT_TYPE,
                    idempotency_key=(
                        f"phase-gate:{record.phase}:{record.record_id}:{record.canonical_hash()}"
                    ),
                    payload={
                        "record": record.model_dump(mode="json", round_trip=True),
                        "actor": record.recorded_by,
                    },
                )
            )
        return record

    def require_admitted(self, phase: int, *, component: str = "component") -> None:
        """Raise a permission error when a gated component is not admitted."""

        if not component.strip():
            raise ValueError("gated component name cannot be blank")
        if not self.is_admitted(phase):
            raise PermissionError(f"{component} requires an admitted Phase {phase} gate")

    def latest(self, phase: int) -> PhaseGateRecord | None:
        if phase not in self._records:
            raise ValueError("phase must be between zero and ten")
        records = self._records[phase]
        return records[-1] if records else None

    def history(self, phase: int | None = None) -> tuple[PhaseGateRecord, ...]:
        if phase is not None and phase not in self._records:
            raise ValueError("phase must be between zero and ten")
        phases = (phase,) if phase is not None else range(11)
        return tuple(record for item in phases for record in self._records[item])

    def is_admitted(self, phase: int, *, at: datetime | None = None) -> bool:
        if at is None:
            at = datetime.now(UTC)
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("phase gate evaluation timestamp must include a timezone")
        at = at.astimezone(UTC)
        latest = self.latest(phase)
        if latest is None or not latest.is_valid_at(at):
            return False
        if phase == 0:
            return True
        prerequisite = latest.prerequisite_phase
        if prerequisite is None:
            prerequisite = phase - 1
        return self.is_admitted(prerequisite, at=at)

    def can_start(self, phase: int) -> bool:
        if phase not in self._records:
            raise ValueError("phase must be between zero and ten")
        return phase == 0 or self.is_admitted(phase - 1)


def local_test_evidence(
    *, name: str, passed: bool, command: str, output: str, verified_by: str = "acceptance-runner"
) -> GateEvidence:
    """Create reproducible evidence for a local command without trusting text alone."""

    if not command.strip():
        raise ValueError("local gate evidence requires a command")
    digest = sha256(output.encode()).hexdigest()
    return GateEvidence(
        name=name,
        kind=GateEvidenceKind.LOCAL_TEST,
        passed=passed,
        artifact_hash=digest if passed else None,
        source=command,
        verified_by=verified_by,
        details="captured command output hash",
    )


__all__ = [
    "GateDecision",
    "GateEvidence",
    "GateEvidenceKind",
    "PhaseGateRecord",
    "PhaseGateRegistry",
    "local_test_evidence",
]
