"""Append-only typed prediction ledger for forward V3-Core cases.

Prediction entries are written before outcomes exist.  Later outcome linkage is
represented by a separate append-only ledger, never by mutating a prediction
record.  This module has no network, credential, portfolio, risk, OMS, or
execution dependency.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.phase4.v3core_forward import ForwardPredictionRecord

PREDICTION_LEDGER_SCHEMA = "advisorai.phase4.v3-core-forward.prediction-ledger.v1"
OUTCOME_LINK_SCHEMA = "advisorai.phase4.v3-core-forward.prediction-outcome-link.v1"


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash_payload(payload: object) -> str:
    return sha256(_canonical(payload)).hexdigest()


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _digest(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field_name} must be a SHA-256 digest")
    return normalized


class ForwardPredictionLedgerEntry(BaseModel):
    """One immutable prediction with a hash-chain position."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = PREDICTION_LEDGER_SCHEMA
    sequence: int = Field(ge=1)
    prediction: ForwardPredictionRecord
    previous_record_hash: str | None = None
    record_hash: str

    @field_validator("previous_record_hash", "record_hash")
    @classmethod
    def valid_hash(cls, value: str | None, info: object) -> str | None:
        return None if value is None else _digest(value, getattr(info, "field_name", "record hash"))

    @model_validator(mode="after")
    def validate_entry(self) -> ForwardPredictionLedgerEntry:
        if self.schema_version != PREDICTION_LEDGER_SCHEMA:
            raise ValueError("unsupported forward prediction ledger schema")
        unsigned = self.model_dump(mode="json", exclude={"record_hash"})
        if _hash_payload(unsigned) != self.record_hash:
            raise ValueError("forward prediction ledger hash is inconsistent")
        return self


class ForwardPredictionLedger:
    """Crash-safe append-only prediction ledger."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[ForwardPredictionLedgerEntry] = []
        self.prediction_ids: set[str] = set()
        if self.path.exists():
            previous: str | None = None
            for line_number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not line.strip():
                    continue
                try:
                    record = ForwardPredictionLedgerEntry.model_validate_json(line)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"forward prediction ledger is corrupt at line {line_number}"
                    ) from exc
                if (
                    record.sequence != len(self.records) + 1
                    or record.previous_record_hash != previous
                    or record.prediction.prediction_id in self.prediction_ids
                ):
                    raise RuntimeError("forward prediction ledger chain or identity is invalid")
                self.records.append(record)
                self.prediction_ids.add(record.prediction.prediction_id)
                previous = record.record_hash

    @property
    def last_record_hash(self) -> str | None:
        return self.records[-1].record_hash if self.records else None

    def append(self, prediction: ForwardPredictionRecord) -> bool:
        if prediction.prediction_id in self.prediction_ids:
            return False
        unsigned = {
            "schema_version": PREDICTION_LEDGER_SCHEMA,
            "sequence": len(self.records) + 1,
            "prediction": prediction.model_dump(mode="json"),
            "previous_record_hash": self.last_record_hash,
        }
        record = ForwardPredictionLedgerEntry(**unsigned, record_hash=_hash_payload(unsigned))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.records.append(record)
        self.prediction_ids.add(prediction.prediction_id)
        return True


class ForwardPredictionOutcomeLink(BaseModel):
    """A later immutable link from a prediction to its realized case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = OUTCOME_LINK_SCHEMA
    sequence: int = Field(ge=1)
    prediction_id: str = Field(min_length=1)
    outcome_case_id: str = Field(min_length=1)
    linked_at: datetime
    previous_record_hash: str | None = None
    record_hash: str

    @field_validator("linked_at")
    @classmethod
    def aware_link_time(cls, value: datetime) -> datetime:
        return _aware(value, "linked_at")

    @field_validator("previous_record_hash", "record_hash")
    @classmethod
    def valid_link_hash(cls, value: str | None, info: object) -> str | None:
        return None if value is None else _digest(value, getattr(info, "field_name", "record hash"))

    @model_validator(mode="after")
    def validate_link(self) -> ForwardPredictionOutcomeLink:
        if self.schema_version != OUTCOME_LINK_SCHEMA:
            raise ValueError("unsupported forward prediction outcome-link schema")
        unsigned = self.model_dump(mode="json", exclude={"record_hash"})
        if _hash_payload(unsigned) != self.record_hash:
            raise ValueError("forward prediction outcome link hash is inconsistent")
        return self


class ForwardPredictionOutcomeLinkLedger:
    """Append-only outcome links; predictions remain immutable."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[ForwardPredictionOutcomeLink] = []
        self.identities: set[tuple[str, str]] = set()
        if self.path.exists():
            previous: str | None = None
            for line_number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not line.strip():
                    continue
                try:
                    record = ForwardPredictionOutcomeLink.model_validate_json(line)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"forward outcome-link ledger is corrupt at line {line_number}"
                    ) from exc
                identity = (record.prediction_id, record.outcome_case_id)
                if (
                    record.sequence != len(self.records) + 1
                    or record.previous_record_hash != previous
                    or identity in self.identities
                ):
                    raise RuntimeError("forward outcome-link ledger chain or identity is invalid")
                self.records.append(record)
                self.identities.add(identity)
                previous = record.record_hash

    @property
    def last_record_hash(self) -> str | None:
        return self.records[-1].record_hash if self.records else None

    def append(
        self,
        *,
        prediction_id: str,
        outcome_case_id: str,
        linked_at: datetime,
    ) -> bool:
        identity = (prediction_id, outcome_case_id)
        if identity in self.identities:
            return False
        unsigned = {
            "schema_version": OUTCOME_LINK_SCHEMA,
            "sequence": len(self.records) + 1,
            "prediction_id": prediction_id,
            "outcome_case_id": outcome_case_id,
            "linked_at": _aware(linked_at, "linked_at").isoformat().replace("+00:00", "Z"),
            "previous_record_hash": self.last_record_hash,
        }
        record = ForwardPredictionOutcomeLink(**unsigned, record_hash=_hash_payload(unsigned))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.records.append(record)
        self.identities.add(identity)
        return True


__all__ = [
    "ForwardPredictionLedger",
    "ForwardPredictionLedgerEntry",
    "ForwardPredictionOutcomeLink",
    "ForwardPredictionOutcomeLinkLedger",
    "OUTCOME_LINK_SCHEMA",
    "PREDICTION_LEDGER_SCHEMA",
]
