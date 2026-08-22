"""Explicitly non-admission diagnostic boundaries for V3-Core experiments.

Historical replay is useful for qualifying schemas and runtimes, but a record
produced from an already-sealed source cannot become prospective Phase-4
evidence.  This module gives that distinction a typed, append-only surface so
diagnostic output cannot be mistaken for the normal candidate ledger.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.phase4.v3core_forward import ForwardPredictionRecord

RETROSPECTIVE_DIAGNOSTIC = "RETROSPECTIVE_DIAGNOSTIC"
DIAGNOSTIC_LEDGER_SCHEMA = "advisorai.phase4.v3-core.retrospective-diagnostic-ledger.v1"


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash_payload(payload: object) -> str:
    return sha256(_canonical(payload)).hexdigest()


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


class RetrospectiveEvidenceRefused(ValueError):
    """Raised when diagnostic output is presented to a prospective boundary."""


class RetrospectiveDiagnosticRecord(BaseModel):
    """One immutable diagnostic prediction envelope.

    The wrapped prediction remains useful for schema/runtime inspection, while
    the envelope makes its non-admission status explicit and machine-checkable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[DIAGNOSTIC_LEDGER_SCHEMA] = DIAGNOSTIC_LEDGER_SCHEMA
    evidence_class: Literal[RETROSPECTIVE_DIAGNOSTIC] = RETROSPECTIVE_DIAGNOSTIC
    admission_evidence: Literal[False] = False
    prospective_coverage_eligible: Literal[False] = False
    phase4_materialization_eligible: Literal[False] = False
    diagnostic_reason: str = Field(min_length=1)
    prediction: ForwardPredictionRecord

    @model_validator(mode="after")
    def validate_separation(self) -> RetrospectiveDiagnosticRecord:
        if self.admission_evidence or self.prospective_coverage_eligible:
            raise ValueError("retrospective diagnostic records cannot be admission eligible")
        return self


class RetrospectiveDiagnosticLedgerEntry(BaseModel):
    """Hash-chained entry for the diagnostic-only ledger."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[DIAGNOSTIC_LEDGER_SCHEMA] = DIAGNOSTIC_LEDGER_SCHEMA
    sequence: int = Field(ge=1)
    record: RetrospectiveDiagnosticRecord
    previous_record_hash: str | None = None
    record_hash: str

    @field_validator("record_hash", "previous_record_hash")
    @classmethod
    def validate_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("diagnostic ledger hashes must be lowercase SHA-256 digests")
        return value

    @model_validator(mode="after")
    def validate_hash(self) -> RetrospectiveDiagnosticLedgerEntry:
        unsigned = self.model_dump(mode="json", exclude={"record_hash"})
        if _hash_payload(unsigned) != self.record_hash:
            raise ValueError("retrospective diagnostic ledger hash is inconsistent")
        return self


class RetrospectiveDiagnosticLedger:
    """Append-only ledger deliberately incompatible with the prospective ledger."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[RetrospectiveDiagnosticLedgerEntry] = []
        self.record_ids: set[str] = set()
        if self.path.exists():
            previous: str | None = None
            for line_number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if not line.strip():
                    continue
                try:
                    entry = RetrospectiveDiagnosticLedgerEntry.model_validate_json(line)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"retrospective diagnostic ledger is corrupt at line {line_number}"
                    ) from exc
                if (
                    entry.sequence != len(self.records) + 1
                    or entry.previous_record_hash != previous
                    or entry.record.prediction.prediction_id in self.record_ids
                ):
                    raise RuntimeError(
                        "retrospective diagnostic ledger chain or identity is invalid"
                    )
                self.records.append(entry)
                self.record_ids.add(entry.record.prediction.prediction_id)
                previous = entry.record_hash

    @property
    def last_record_hash(self) -> str | None:
        return self.records[-1].record_hash if self.records else None

    def append(self, record: RetrospectiveDiagnosticRecord) -> bool:
        prediction_id = record.prediction.prediction_id
        if prediction_id in self.record_ids:
            return False
        unsigned = {
            "schema_version": DIAGNOSTIC_LEDGER_SCHEMA,
            "sequence": len(self.records) + 1,
            "record": record.model_dump(mode="json"),
            "previous_record_hash": self.last_record_hash,
        }
        entry = RetrospectiveDiagnosticLedgerEntry(
            **unsigned,
            record_hash=_hash_payload(unsigned),
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.records.append(entry)
        self.record_ids.add(prediction_id)
        return True


def reject_retrospective_for_admission(records: Sequence[object]) -> None:
    """Fail closed if a diagnostic envelope reaches a prospective boundary."""

    if any(
        isinstance(record, RetrospectiveDiagnosticRecord)
        or getattr(record, "evidence_class", None) == RETROSPECTIVE_DIAGNOSTIC
        for record in records
    ):
        raise RetrospectiveEvidenceRefused(
            "RETROSPECTIVE_DIAGNOSTIC records cannot satisfy prospective candidate coverage"
        )


__all__ = [
    "DIAGNOSTIC_LEDGER_SCHEMA",
    "RETROSPECTIVE_DIAGNOSTIC",
    "RetrospectiveDiagnosticLedger",
    "RetrospectiveDiagnosticLedgerEntry",
    "RetrospectiveDiagnosticRecord",
    "RetrospectiveEvidenceRefused",
    "reject_retrospective_for_admission",
]
