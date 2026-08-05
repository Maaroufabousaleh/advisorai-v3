"""Structured incident records and corrective-test links."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers


class IncidentSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Incident(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    incident_id: UUID = Field(default_factory=uuid4)
    severity: IncidentSeverity
    owner: str
    summary: str
    runbook: str
    evidence_ids: tuple[UUID, ...] = ()
    timeline: tuple[str, ...] = ()
    containment: str
    reconciliation: str = "pending"
    root_cause: str | None = None
    corrective_test: str | None = None
    rollback_link: str | None = None
    opened_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    closed_at: datetime | None = None

    def canonical_hash(self) -> str:
        """Return the deterministic digest used for incident idempotency."""

        payload = self.model_dump(mode="json", round_trip=True, exclude={"opened_at"})
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return sha256(encoded).hexdigest()

    @model_validator(mode="after")
    def require_incident_record(self) -> Incident:
        if (
            not self.owner.strip()
            or not self.summary.strip()
            or not self.runbook.strip()
            or not self.containment.strip()
            or not self.reconciliation.strip()
        ):
            raise ValueError("incidents require an owner, summary, runbook, and reconciliation")
        if any(not item.strip() for item in self.timeline):
            raise ValueError("incident timeline entries cannot be blank")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("incident evidence IDs must be unique")
        if self.root_cause is not None and not self.root_cause.strip():
            raise ValueError("incident root cause cannot be blank")
        if self.corrective_test is not None and not self.corrective_test.strip():
            raise ValueError("incident corrective test cannot be blank")
        if self.rollback_link is not None and not self.rollback_link.strip():
            raise ValueError("incident rollback link cannot be blank")
        if self.closed_at is not None and (
            not self.root_cause or not self.corrective_test or not self.rollback_link
        ):
            raise ValueError(
                "closed incidents require root cause, corrective test, and rollback link"
            )
        if self.closed_at is not None and self.reconciliation.strip().lower() == "pending":
            raise ValueError("closed incidents require a completed reconciliation status")
        return self

    @field_validator("opened_at", "closed_at")
    @classmethod
    def require_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("incident timestamp must include a timezone")
        return value.astimezone(UTC)

    @field_validator("closed_at")
    @classmethod
    def close_after_open(cls, value: datetime | None, info) -> datetime | None:
        if value is not None:
            opened = info.data.get("opened_at")
            if opened is not None and value < opened:
                raise ValueError("incident cannot close before it opens")
        return value


class IncidentLedger:
    """Append-only incident projection backed by the authoritative ledger."""

    def __init__(self, ledgers: SqliteLedgers) -> None:
        self.ledgers = ledgers

    def record(self, incident: Incident) -> Incident:
        # ``model_copy(update=...)`` intentionally skips Pydantic validation;
        # revalidate before an immutable ledger append so a caller cannot
        # bypass the closed-incident/postmortem invariants.
        incident = Incident.model_validate(incident.model_dump(mode="python", round_trip=True))
        prior = next(
            (item for item in self.all() if item.incident_id == incident.incident_id), None
        )
        if prior is not None and prior.canonical_hash() == incident.canonical_hash():
            return prior
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.INCIDENT,
                event_type="incident_recorded",
                idempotency_key=f"incident:{incident.incident_id}:{incident.canonical_hash()}",
                payload={"incident": incident.model_dump(mode="json", round_trip=True)},
            )
        )
        return incident

    def close(
        self,
        incident_id: UUID,
        *,
        root_cause: str,
        corrective_test: str,
        rollback_link: str,
        reconciliation: str = "verified",
        timeline_entry: str | None = None,
        closed_at: datetime | None = None,
    ) -> Incident:
        """Close an open incident only with the required postmortem links."""

        current = next((item for item in self.all() if item.incident_id == incident_id), None)
        if current is None:
            raise KeyError(str(incident_id))
        if current.closed_at is not None:
            if (
                current.root_cause != root_cause
                or current.corrective_test != corrective_test
                or current.rollback_link != rollback_link
            ):
                raise ValueError("closed incident is immutable")
            return current
        when = closed_at or datetime.now(UTC)
        timeline = current.timeline + ((timeline_entry.strip(),) if timeline_entry else ())
        closed = current.model_copy(
            update={
                "timeline": timeline,
                "root_cause": root_cause,
                "corrective_test": corrective_test,
                "rollback_link": rollback_link,
                "reconciliation": reconciliation,
                "closed_at": when,
            }
        )
        return self.record(closed)

    def all(self) -> tuple[Incident, ...]:
        latest: dict[UUID, Incident] = {}
        for event in self.ledgers.events(LedgerNamespace.INCIDENT):
            if event.event_type != "incident_recorded":
                continue
            payload = event.payload.get("incident")
            if not isinstance(payload, dict):
                raise ValueError("incident ledger contains an invalid incident payload")
            incident = Incident.model_validate(payload)
            latest[incident.incident_id] = incident
        return tuple(latest.values())
