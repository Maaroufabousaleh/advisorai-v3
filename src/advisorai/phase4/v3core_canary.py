"""Typed contracts for the bounded prospective V3-Core canary.

The canary is deliberately a separate evidence class.  Its records are
prospective in wall-clock time, but they are never Phase-4 admission evidence.
The module also contains the delayed source-finality boundary used by the
canary collector: raw receipts stay append-only and a normalized bar is
admitted only after a post-close guard and two distinct matching receipts.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.phase4.v3core_cadence import (
    V3_CORE_MARKET_DATA_PROVIDER,
    V3_CORE_MARKET_DATA_REST_ENDPOINT,
    V3_CORE_SYMBOLS,
    V3CoreBar,
    V3CoreBarProvenance,
    sha256_json,
)
from advisorai.phase4.v3core_forward import (
    FORWARD_INTERVAL,
    ForwardNormalizedBarSpool,
    ForwardPredictionRecord,
    ForwardRawResponse,
    parse_binance_klines,
)

CANARY_PREREGISTRATION_SCHEMA = "advisorai.phase4.v3-core.prospective-canary-preregistration.v1"
CANARY_FINALITY_SCHEMA = "advisorai.phase4.v3-core.prospective-canary.finality.v1"
CANARY_REVISION_SCHEMA = "advisorai.phase4.v3-core.prospective-canary.post-admission-revision.v1"
CANARY_PREDICTION_SCHEMA = "advisorai.phase4.v3-core.prospective-canary.prediction.v1"
CANARY_REJECTION_SCHEMA = "advisorai.phase4.v3-core.prospective-canary.rejection.v1"
CANARY_PREFLIGHT_SCHEMA = "advisorai.phase4.v3-core.prospective-canary.preflight.v1"
CANARY_EVIDENCE_CLASS = "PROSPECTIVE_CANARY_ONLY"
CANARY_CONTEXT_BARS = 48
CANARY_CONTEXT_LAG_SECONDS = 600
CANARY_FINALITY_GUARD_SECONDS = 60
CANARY_REPEAT_RECEIPTS = 2
CANARY_MIN_CUTOFFS_PER_SYMBOL = 4
CANARY_WARMUP_POLICY_ID = "v3core-prospective-canary-warmup-v2"
CANARY_WARMUP_NOT_ELIGIBLE = "WARMUP_NOT_ELIGIBLE"
CANARY_WAITING_FOR_CONTEXT = "WAITING_FOR_MANDATORY_CONTEXT"
CANARY_SOURCE_CONTEXT_UNAVAILABLE = "SOURCE_CONTEXT_UNAVAILABLE"
CANARY_MISSED_MANDATORY_CUTOFF = "CANDIDATE_MISSED_MANDATORY_CUTOFF"
CANARY_STATUS_OUTPUT_CONTRACT = "watchdog/status.json-authoritative-v1"


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


def _commit(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 40 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field_name} must be a Git commit identity")
    return normalized


def derive_first_mandatory_cutoff(
    start_at: datetime,
    *,
    context_bars: int = CANARY_CONTEXT_BARS,
    interval_seconds: int = 300,
    context_lag_seconds: int = CANARY_CONTEXT_LAG_SECONDS,
) -> datetime:
    """Derive the first hourly cutoff that can legally have a full context.

    The canary source contract starts with the closed interval ending at the
    UTC-hour ``start_at``.  The latest bar in a context of ``context_bars``
    therefore ends ``(context_bars - 1)`` intervals later.  The hourly cutoff
    must be at least ``context_lag_seconds`` after that bar and is rounded up
    to the next UTC hour.  This is a schedule calculation only; it never uses
    model output or observed utility.
    """

    normalized_start = _aware(start_at, "start_at")
    if normalized_start.minute or normalized_start.second or normalized_start.microsecond:
        raise ValueError("canary start_at must be aligned to a UTC hour")
    if context_bars < 1 or interval_seconds <= 0 or context_lag_seconds < interval_seconds:
        raise ValueError("warm-up timing parameters are invalid")
    latest_required_bar = normalized_start + timedelta(
        seconds=interval_seconds * (context_bars - 1)
    )
    earliest_cutoff = latest_required_bar + timedelta(seconds=context_lag_seconds)
    hour_floor = earliest_cutoff.replace(minute=0, second=0, microsecond=0)
    if hour_floor < earliest_cutoff:
        hour_floor += timedelta(hours=1)
    return hour_floor


def derive_mandatory_cutoffs(
    start_at: datetime,
    *,
    count: int = CANARY_MIN_CUTOFFS_PER_SYMBOL,
) -> tuple[datetime, ...]:
    """Return the preregistered hourly cutoff schedule after warm-up."""

    if count < 1:
        raise ValueError("mandatory cutoff count must be positive")
    first = derive_first_mandatory_cutoff(start_at)
    return tuple(first + timedelta(hours=index) for index in range(count))


class CanaryPreregistration(BaseModel):
    """Immutable launch contract for one bounded canary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[CANARY_PREREGISTRATION_SCHEMA] = CANARY_PREREGISTRATION_SCHEMA
    canary_id: str = Field(min_length=1)
    created_at: datetime
    start_at: datetime
    target_end_at: datetime
    repository_commit: str
    collector_code_sha256: str
    finality_code_sha256: str
    chronos_worker_code_sha256: str
    chronos_runner_sha256: str
    model_identity: str
    checkpoint_sha256: str
    preprocessing_identity: str
    preprocessing_sha256: str
    dependency_lock_sha256: str
    phase3_gate_sha256: str
    symbols: tuple[str, ...] = V3_CORE_SYMBOLS
    market_data_provider: str = V3_CORE_MARKET_DATA_PROVIDER
    market_data_endpoint: str = V3_CORE_MARKET_DATA_REST_ENDPOINT
    interval: str = FORWARD_INTERVAL
    prediction_cadence_seconds: int = 3600
    context_bars: int = CANARY_CONTEXT_BARS
    context_newest_lag_seconds: int = CANARY_CONTEXT_LAG_SECONDS
    finality_guard_seconds: int = CANARY_FINALITY_GUARD_SECONDS
    repeat_requirement: int = CANARY_REPEAT_RECEIPTS
    distinct_receipts_required: bool = True
    evidence_class: Literal[CANARY_EVIDENCE_CLASS] = CANARY_EVIDENCE_CLASS
    admission_eligible: Literal[False] = False
    phase4_materialization_eligible: Literal[False] = False
    credentials_prohibited: Literal[True] = True
    orders_prohibited: Literal[True] = True
    gpu_family: Literal["chronos-2-small"] = "chronos-2-small"
    fail_fast_policy_id: str = Field(min_length=1)
    fail_fast_policy_sha256: str
    watchdog_identity: str = Field(min_length=1)
    watchdog_sha256: str
    terminal_audit_sha256: str
    start_rule: str = Field(min_length=1)
    terminal_rule: str = Field(min_length=1)
    acceptance_criteria: tuple[str, ...] = Field(min_length=1)
    minimum_cutoffs_per_symbol: int = CANARY_MIN_CUTOFFS_PER_SYMBOL
    maximum_cutoffs_per_symbol: int = CANARY_MIN_CUTOFFS_PER_SYMBOL
    # These fields are optional only so historical v1 preregistrations remain
    # readable.  A replacement canary must populate the complete v2 warm-up
    # contract; preflight rejects legacy preregistrations for new launches.
    warmup_policy_id: str | None = None
    warmup_until_at: datetime | None = None
    first_mandatory_cutoff_at: datetime | None = None
    mandatory_cutoffs: tuple[datetime, ...] = ()
    scheduler_identity: str | None = None
    scheduler_sha256: str | None = None
    status_output_contract: str | None = None

    @field_validator("created_at", "start_at", "target_end_at")
    @classmethod
    def aware_timestamps(cls, value: datetime, info: object) -> datetime:
        return _aware(value, getattr(info, "field_name", "timestamp"))

    @field_validator("warmup_until_at", "first_mandatory_cutoff_at")
    @classmethod
    def optional_aware_timestamps(cls, value: datetime | None, info: object) -> datetime | None:
        if value is None:
            return None
        return _aware(value, getattr(info, "field_name", "timestamp"))

    @field_validator("mandatory_cutoffs")
    @classmethod
    def aware_cutoffs(cls, value: tuple[datetime, ...]) -> tuple[datetime, ...]:
        return tuple(_aware(item, "mandatory_cutoff") for item in value)

    @field_validator("repository_commit")
    @classmethod
    def valid_commit(cls, value: str) -> str:
        return _commit(value, "repository_commit")

    @field_validator(
        "collector_code_sha256",
        "finality_code_sha256",
        "chronos_worker_code_sha256",
        "chronos_runner_sha256",
        "checkpoint_sha256",
        "preprocessing_sha256",
        "dependency_lock_sha256",
        "phase3_gate_sha256",
        "fail_fast_policy_sha256",
        "watchdog_sha256",
        "terminal_audit_sha256",
        "scheduler_sha256",
    )
    @classmethod
    def valid_digests(cls, value: str, info: object) -> str:
        return _digest(value, getattr(info, "field_name", "digest"))

    @field_validator("symbols")
    @classmethod
    def valid_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(symbol.strip().upper() for symbol in value)
        if normalized != V3_CORE_SYMBOLS:
            raise ValueError("the canary universe is fixed to BTCUSDT and ETHUSDT")
        return normalized

    @model_validator(mode="after")
    def validate_frozen_contract(self) -> CanaryPreregistration:
        if self.start_at < self.created_at:
            raise ValueError("canary start cannot precede preregistration creation")
        duration = self.target_end_at - self.start_at
        if duration < timedelta(hours=8) or duration > timedelta(hours=10):
            raise ValueError("canary duration must be between eight and ten hours")
        if self.market_data_provider != V3_CORE_MARKET_DATA_PROVIDER:
            raise ValueError("canary provider must be the reviewed public Binance surface")
        if self.market_data_endpoint != V3_CORE_MARKET_DATA_REST_ENDPOINT:
            raise ValueError("canary endpoint must be the reviewed public klines endpoint")
        if self.interval != FORWARD_INTERVAL or self.prediction_cadence_seconds != 3600:
            raise ValueError("canary cadence must remain 5m observations and 1h predictions")
        if self.context_bars != CANARY_CONTEXT_BARS:
            raise ValueError("canary requires exactly 48 context bars")
        if self.context_newest_lag_seconds != CANARY_CONTEXT_LAG_SECONDS:
            raise ValueError("canary context boundary must remain cutoff minus 10 minutes")
        if self.finality_guard_seconds != CANARY_FINALITY_GUARD_SECONDS:
            raise ValueError("canary finality guard must remain 60 seconds")
        if self.repeat_requirement != CANARY_REPEAT_RECEIPTS or not self.distinct_receipts_required:
            raise ValueError("canary requires two distinct matching receipts")
        if self.minimum_cutoffs_per_symbol != CANARY_MIN_CUTOFFS_PER_SYMBOL:
            raise ValueError("canary requires four complete cutoffs per symbol")
        if self.maximum_cutoffs_per_symbol != CANARY_MIN_CUTOFFS_PER_SYMBOL:
            raise ValueError("the bounded canary is capped at four cutoffs per symbol")
        warmup_fields = (
            self.warmup_policy_id,
            self.warmup_until_at,
            self.first_mandatory_cutoff_at,
            self.scheduler_identity,
            self.scheduler_sha256,
            self.status_output_contract,
        )
        has_warmup_contract = any(value is not None for value in warmup_fields) or bool(
            self.mandatory_cutoffs
        )
        if has_warmup_contract:
            if self.warmup_policy_id != CANARY_WARMUP_POLICY_ID:
                raise ValueError("replacement canary warm-up policy identity is required")
            if (
                self.warmup_until_at is None
                or self.first_mandatory_cutoff_at is None
                or self.scheduler_identity is None
                or self.scheduler_sha256 is None
                or self.status_output_contract != CANARY_STATUS_OUTPUT_CONTRACT
            ):
                raise ValueError("replacement canary warm-up and scheduler fields are incomplete")
            expected_first = derive_first_mandatory_cutoff(self.start_at)
            expected_cutoffs = derive_mandatory_cutoffs(
                self.start_at, count=self.minimum_cutoffs_per_symbol
            )
            if self.warmup_until_at != expected_first:
                raise ValueError("warm-up boundary does not match the deterministic schedule")
            if self.first_mandatory_cutoff_at != expected_first:
                raise ValueError("first mandatory cutoff does not match the deterministic schedule")
            if self.mandatory_cutoffs != expected_cutoffs:
                raise ValueError("mandatory cutoff schedule does not match the deterministic rule")
            if self.target_end_at < expected_cutoffs[-1] + timedelta(hours=1):
                raise ValueError("canary deadline must leave time for the final one-hour outcome")
        return self


class CanaryPreflightCheck(BaseModel):
    """One deterministic pre-launch check."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    passed: bool
    reason: str = Field(min_length=1)


class CanaryPreflightReport(BaseModel):
    """Immutable preflight result; only CANARY_READY permits launch handoff."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[CANARY_PREFLIGHT_SCHEMA] = CANARY_PREFLIGHT_SCHEMA
    decision: Literal["CANARY_READY", "REFUSE_CANARY"]
    canary_id: str = Field(min_length=1)
    evidence_class: Literal[CANARY_EVIDENCE_CLASS] = CANARY_EVIDENCE_CLASS
    admission_eligible: Literal[False] = False
    checks: tuple[CanaryPreflightCheck, ...]
    refusal_reasons: tuple[str, ...] = ()
    report_hash: str

    @model_validator(mode="after")
    def validate_preflight_hash(self) -> CanaryPreflightReport:
        unsigned = self.model_dump(mode="json", exclude={"report_hash"})
        if sha256_json(unsigned) != self.report_hash:
            raise ValueError("canary preflight report hash is inconsistent")
        return self


class CanaryPostAdmissionRevision(BaseModel):
    """Append-only evidence that an admitted bar later changed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[CANARY_REVISION_SCHEMA] = CANARY_REVISION_SCHEMA
    sequence: int = Field(ge=1)
    instrument: str
    interval_end: datetime
    admitted_content_hash: str
    observed_content_hash: str
    observed_at: datetime
    raw_sequence: int = Field(ge=1)
    changed_fields: tuple[str, ...] = Field(min_length=1)
    previous_record_hash: str | None = None
    record_hash: str

    @field_validator("interval_end", "observed_at")
    @classmethod
    def revision_timestamps(cls, value: datetime, info: object) -> datetime:
        return _aware(value, getattr(info, "field_name", "timestamp"))

    @field_validator(
        "admitted_content_hash", "observed_content_hash", "previous_record_hash", "record_hash"
    )
    @classmethod
    def revision_hashes(cls, value: str | None, info: object) -> str | None:
        return None if value is None else _digest(value, getattr(info, "field_name", "hash"))

    @model_validator(mode="after")
    def validate_revision_hash(self) -> CanaryPostAdmissionRevision:
        unsigned = self.model_dump(mode="json", exclude={"record_hash"})
        if sha256_json(unsigned) != self.record_hash:
            raise ValueError("canary post-admission revision hash is inconsistent")
        return self


class CanaryPredictionRecord(BaseModel):
    """Prediction envelope that cannot be mistaken for admission evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[CANARY_PREDICTION_SCHEMA] = CANARY_PREDICTION_SCHEMA
    sequence: int = Field(ge=1)
    evidence_class: Literal[CANARY_EVIDENCE_CLASS] = CANARY_EVIDENCE_CLASS
    admission_eligible: Literal[False] = False
    prediction: ForwardPredictionRecord
    previous_record_hash: str | None = None
    record_hash: str

    @field_validator("previous_record_hash", "record_hash")
    @classmethod
    def prediction_hashes(cls, value: str | None, info: object) -> str | None:
        return None if value is None else _digest(value, getattr(info, "field_name", "hash"))

    @model_validator(mode="after")
    def validate_prediction_envelope(self) -> CanaryPredictionRecord:
        unsigned = self.model_dump(mode="json", exclude={"record_hash"})
        if sha256_json(unsigned) != self.record_hash:
            raise ValueError("canary prediction envelope hash is inconsistent")
        if (
            dict(self.prediction.provenance).get("experiment_evidence_class")
            != CANARY_EVIDENCE_CLASS
        ):
            raise ValueError("canary prediction must carry its explicit evidence class")
        return self


class CanaryPredictionLedger:
    """Append-only ledger for canary-only prediction envelopes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[CanaryPredictionRecord] = []
        self._by_key: dict[tuple[str, datetime], CanaryPredictionRecord] = {}
        previous: str | None = None
        if self.path.exists():
            for line_number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if not line.strip():
                    continue
                try:
                    record = CanaryPredictionRecord.model_validate_json(line)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"canary prediction ledger is corrupt at line {line_number}"
                    ) from exc
                if (
                    record.sequence != len(self.records) + 1
                    or record.previous_record_hash != previous
                ):
                    raise RuntimeError("canary prediction ledger chain is not continuous")
                key = (record.prediction.instrument, record.prediction.cutoff)
                if key in self._by_key:
                    raise RuntimeError("canary prediction ledger contains a duplicate cutoff")
                self.records.append(record)
                self._by_key[key] = record
                previous = record.record_hash

    @property
    def last_record_hash(self) -> str | None:
        return self.records[-1].record_hash if self.records else None

    def append(self, prediction: ForwardPredictionRecord) -> CanaryPredictionRecord:
        if dict(prediction.provenance).get("experiment_evidence_class") != CANARY_EVIDENCE_CLASS:
            raise ValueError("only explicitly canary-labelled predictions may be appended")
        key = (prediction.instrument, prediction.cutoff)
        prior = self._by_key.get(key)
        if prior is not None:
            if prior.prediction != prediction:
                raise RuntimeError("conflicting canary prediction for an existing cutoff")
            return prior
        unsigned = {
            "schema": CANARY_PREDICTION_SCHEMA,
            "sequence": len(self.records) + 1,
            "evidence_class": CANARY_EVIDENCE_CLASS,
            "admission_eligible": False,
            "prediction": prediction.model_dump(mode="json"),
            "previous_record_hash": self.last_record_hash,
        }
        record = CanaryPredictionRecord(**unsigned, record_hash=sha256_json(unsigned))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.records.append(record)
        self._by_key[key] = record
        return record

    def for_cutoff(self, instrument: str, cutoff: datetime) -> CanaryPredictionRecord | None:
        return self._by_key.get((instrument.strip().upper(), _aware(cutoff, "cutoff")))


class CanaryRejectionRecord(BaseModel):
    """Append-only canary failure evidence for one mandatory cutoff."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal[CANARY_REJECTION_SCHEMA] = CANARY_REJECTION_SCHEMA
    sequence: int = Field(ge=1)
    evidence_class: Literal[CANARY_EVIDENCE_CLASS] = CANARY_EVIDENCE_CLASS
    admission_eligible: Literal[False] = False
    instrument: str
    cutoff: datetime
    reason: str = Field(min_length=1)
    recorded_at: datetime
    previous_record_hash: str | None = None
    record_hash: str

    @field_validator("cutoff", "recorded_at")
    @classmethod
    def rejection_timestamps(cls, value: datetime, info: object) -> datetime:
        return _aware(value, getattr(info, "field_name", "timestamp"))

    @field_validator("previous_record_hash", "record_hash")
    @classmethod
    def rejection_hashes(cls, value: str | None, info: object) -> str | None:
        return None if value is None else _digest(value, getattr(info, "field_name", "hash"))

    @model_validator(mode="after")
    def validate_rejection_hash(self) -> CanaryRejectionRecord:
        unsigned = self.model_dump(mode="json", exclude={"record_hash"})
        if sha256_json(unsigned) != self.record_hash:
            raise ValueError("canary rejection hash is inconsistent")
        return self


class CanaryRejectionLedger:
    """Append-only, explicitly canary-labelled rejection ledger."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[CanaryRejectionRecord] = []
        self._keys: set[tuple[str, datetime]] = set()
        previous: str | None = None
        if path.exists():
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    record = CanaryRejectionRecord.model_validate_json(line)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"canary rejection ledger is corrupt at line {line_number}"
                    ) from exc
                key = (record.instrument, record.cutoff)
                if (
                    record.sequence != len(self.records) + 1
                    or record.previous_record_hash != previous
                ):
                    raise RuntimeError("canary rejection ledger chain is not continuous")
                if key in self._keys:
                    raise RuntimeError("canary rejection ledger contains a duplicate cutoff")
                self.records.append(record)
                self._keys.add(key)
                previous = record.record_hash

    @property
    def last_record_hash(self) -> str | None:
        return self.records[-1].record_hash if self.records else None

    def append(self, *, instrument: str, cutoff: datetime, reason: str) -> CanaryRejectionRecord:
        normalized_instrument = instrument.strip().upper()
        normalized_cutoff = _aware(cutoff, "cutoff")
        key = (normalized_instrument, normalized_cutoff)
        if key in self._keys:
            existing = next(
                record for record in self.records if (record.instrument, record.cutoff) == key
            )
            if existing.reason != reason:
                raise RuntimeError("conflicting canary rejection for an existing cutoff")
            return existing
        unsigned = {
            "schema": CANARY_REJECTION_SCHEMA,
            "sequence": len(self.records) + 1,
            "evidence_class": CANARY_EVIDENCE_CLASS,
            "admission_eligible": False,
            "instrument": normalized_instrument,
            "cutoff": normalized_cutoff,
            "reason": reason,
            "recorded_at": datetime.now(UTC),
            "previous_record_hash": self.last_record_hash,
        }
        draft = CanaryRejectionRecord.model_construct(**unsigned, record_hash="0" * 64)
        record = CanaryRejectionRecord(
            **unsigned,
            record_hash=sha256_json(draft.model_dump(mode="json", exclude={"record_hash"})),
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.records.append(record)
        self._keys.add(key)
        return record


def _bar_content_payload(bar: V3CoreBar) -> dict[str, str]:
    """Return the stable identity used by the finality rule.

    Receipt-local timestamps and the derived normalized hash are deliberately
    excluded.  The source identity, raw kline identity, provider event time,
    and source-health classification are part of the scientific content and
    must therefore participate in equality checks as well as OHLCV values.
    """

    provenance = bar.provenance.model_dump(mode="json")
    for field in ("provider_available_at", "collected_at", "normalized_record_hash"):
        provenance.pop(field, None)
    return {
        "instrument": bar.instrument,
        "interval_end": bar.interval_end.isoformat(),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
        "source_id": bar.source_id,
        "provider_identity": bar.provider_identity,
        "endpoint": bar.endpoint,
        "source_snapshot_hash": bar.source_snapshot_hash,
        "quality": bar.quality,
        "provenance": provenance,
    }


def bar_content_hash(bar: V3CoreBar) -> str:
    """Hash stable OHLCV and source identity used by the finality rule."""

    return sha256_json(_bar_content_payload(bar))


class CanaryFinalityViolation(RuntimeError):
    """Raised when an admitted bar changes in later raw evidence."""


class CanaryFinalityTracker:
    """Delay canonicalization until the frozen canary finality condition holds."""

    def __init__(
        self,
        normalized: ForwardNormalizedBarSpool,
        revision_path: Path,
        *,
        guard_seconds: int = CANARY_FINALITY_GUARD_SECONDS,
        repeat_receipts: int = CANARY_REPEAT_RECEIPTS,
    ) -> None:
        if (
            guard_seconds != CANARY_FINALITY_GUARD_SECONDS
            or repeat_receipts != CANARY_REPEAT_RECEIPTS
        ):
            raise ValueError("canary finality parameters are frozen")
        self.normalized = normalized
        self.revision_path = revision_path
        self.revision_path.parent.mkdir(parents=True, exist_ok=True)
        self.guard_seconds = guard_seconds
        self.repeat_receipts = repeat_receipts
        self._versions: dict[tuple[str, datetime], dict[str, list[tuple[int, datetime]]]] = (
            defaultdict(lambda: defaultdict(list))
        )
        self._admissions: dict[tuple[str, datetime], dict[str, object]] = {}
        self.revisions: list[CanaryPostAdmissionRevision] = []
        previous: str | None = None
        if revision_path.exists():
            for line_number, line in enumerate(
                revision_path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if not line.strip():
                    continue
                try:
                    revision = CanaryPostAdmissionRevision.model_validate_json(line)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"canary revision ledger is corrupt at line {line_number}"
                    ) from exc
                if (
                    revision.sequence != len(self.revisions) + 1
                    or revision.previous_record_hash != previous
                ):
                    raise RuntimeError("canary revision ledger chain is not continuous")
                self.revisions.append(revision)
                previous = revision.record_hash

    def _admitted_bar(self, bar: V3CoreBar, observed_at: datetime) -> V3CoreBar:
        observed_at = _aware(observed_at, "observed_at")
        provenance_without_hash = {
            "interval_end": bar.interval_end.isoformat(),
            "provider_available_at": observed_at.isoformat(),
            "collected_at": observed_at.isoformat(),
            "provider_event_at": (
                None
                if bar.provenance.provider_event_at is None
                else bar.provenance.provider_event_at.isoformat()
            ),
            "availability_basis": "forward_observed",
            "evidence_class": "forward_pit_admission",
            "source_snapshot_hash": bar.source_snapshot_hash,
            "raw_record_hash": bar.provenance.raw_record_hash,
            "source_health_state": bar.provenance.source_health_state,
        }
        normalized_hash = sha256_json(
            {
                "instrument": bar.instrument,
                **{
                    key: str(getattr(bar, key))
                    for key in ("open", "high", "low", "close", "volume")
                },
                "source_id": bar.source_id,
                "provider_identity": bar.provider_identity,
                "endpoint": bar.endpoint,
                **provenance_without_hash,
            }
        )
        provenance = V3CoreBarProvenance(
            interval_end=bar.interval_end,
            provider_available_at=observed_at,
            collected_at=observed_at,
            provider_event_at=bar.provenance.provider_event_at,
            availability_basis="forward_observed",
            evidence_class="forward_pit_admission",
            source_snapshot_hash=bar.source_snapshot_hash,
            raw_record_hash=bar.provenance.raw_record_hash,
            normalized_record_hash=normalized_hash,
            source_health_state=bar.provenance.source_health_state,
        )
        return bar.model_copy(update={"provenance": provenance})

    def _append_revision(
        self,
        *,
        bar: V3CoreBar,
        admitted: V3CoreBar,
        raw: ForwardRawResponse,
        observed_hash: str,
    ) -> CanaryPostAdmissionRevision:
        changed = tuple(
            field
            for field in ("open", "high", "low", "close", "volume")
            if getattr(admitted, field) != getattr(bar, field)
        )
        unsigned = {
            "schema": CANARY_REVISION_SCHEMA,
            "sequence": len(self.revisions) + 1,
            "instrument": bar.instrument,
            "interval_end": bar.interval_end,
            "admitted_content_hash": bar_content_hash(admitted),
            "observed_content_hash": observed_hash,
            "observed_at": raw.collected_at,
            "raw_sequence": raw.sequence,
            "changed_fields": changed or ("content",),
            "previous_record_hash": self.revisions[-1].record_hash if self.revisions else None,
        }
        draft = CanaryPostAdmissionRevision.model_construct(**unsigned, record_hash="0" * 64)
        revision = CanaryPostAdmissionRevision(
            **unsigned,
            record_hash=sha256_json(draft.model_dump(mode="json", exclude={"record_hash"})),
        )
        with self.revision_path.open("a", encoding="utf-8") as handle:
            handle.write(revision.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.revisions.append(revision)
        return revision

    def _observe_state(
        self,
        raw: ForwardRawResponse,
        bars: Iterable[V3CoreBar],
        *,
        versions: dict[tuple[str, datetime], dict[str, list[tuple[int, datetime]]]],
        admitted_bars: dict[tuple[str, datetime], V3CoreBar],
        admissions: dict[tuple[str, datetime], dict[str, object]],
        persist_normalized: bool,
        record_revisions: bool,
    ) -> tuple[V3CoreBar, ...]:
        """Apply one receipt to an explicit state projection.

        Replay uses a shadow projection so a persisted canonical bar cannot
        cause an earlier raw variation to be mistaken for a later revision.
        The live path uses the tracker's mutable projection and persists only
        after the same deterministic checks pass.
        """

        admitted_now: list[V3CoreBar] = []
        for bar in bars:
            key = (bar.instrument, bar.interval_end)
            content_hash = bar_content_hash(bar)
            interval_versions = versions[key]
            sequence_hashes = {
                version_hash
                for version_hash, receipts in interval_versions.items()
                if any(sequence == raw.sequence for sequence, _timestamp in receipts)
            }
            if sequence_hashes and content_hash not in sequence_hashes:
                raise RuntimeError(
                    f"raw receipt sequence {raw.sequence} contains conflicting content for "
                    f"{bar.instrument}:{bar.interval_end.isoformat()}"
                )
            if raw.sequence not in {
                sequence for sequence, _timestamp in interval_versions[content_hash]
            }:
                interval_versions[content_hash].append((raw.sequence, raw.collected_at))
            prior = admitted_bars.get(key)
            if prior is not None:
                if bar_content_hash(prior) != content_hash:
                    if record_revisions:
                        revision = self._append_revision(
                            bar=bar,
                            admitted=prior,
                            raw=raw,
                            observed_hash=content_hash,
                        )
                        message = (
                            f"POST_ADMISSION_REVISION {revision.instrument}:"
                            f"{revision.interval_end.isoformat()}"
                        )
                    else:
                        message = (
                            "POST_ADMISSION_REVISION "
                            f"{bar.instrument}:{bar.interval_end.isoformat()}"
                        )
                    raise CanaryFinalityViolation(message)
                continue
            if raw.collected_at < bar.interval_end + timedelta(seconds=self.guard_seconds):
                continue
            if len(interval_versions[content_hash]) < self.repeat_receipts:
                continue
            final_bar = self._admitted_bar(bar, raw.collected_at)
            if persist_normalized:
                self.normalized.append(final_bar)
            admitted_bars[key] = final_bar
            supporting = sorted(
                interval_versions[content_hash], key=lambda item: (item[1], item[0])
            )
            pre_admission_hashes = sorted(
                version_hash for version_hash in interval_versions if version_hash != content_hash
            )
            admissions[key] = {
                "instrument": bar.instrument,
                "interval_end": bar.interval_end,
                "admission_observed_at": raw.collected_at,
                "admission_raw_sequence": raw.sequence,
                "admitted_content_hash": content_hash,
                "supporting_receipt_sequences": tuple(
                    sequence for sequence, _timestamp in supporting[: self.repeat_receipts]
                ),
                "supporting_receipt_timestamps": tuple(
                    timestamp for _sequence, timestamp in supporting[: self.repeat_receipts]
                ),
                "pre_admission_content_hashes": tuple(pre_admission_hashes),
                "pre_admission_variation_count": len(pre_admission_hashes),
            }
            admitted_now.append(final_bar)
        return tuple(admitted_now)

    def observe(self, raw: ForwardRawResponse, bars: Iterable[V3CoreBar]) -> tuple[V3CoreBar, ...]:
        return self._observe_state(
            raw,
            bars,
            versions=self._versions,
            admitted_bars=self.normalized.bars,
            admissions=self._admissions,
            persist_normalized=True,
            record_revisions=True,
        )

    def replay(self, raw_records: Sequence[ForwardRawResponse], source_snapshot_hash: str) -> None:
        """Rebuild finality state from persisted raw receipts without changing them."""

        if self.revisions:
            raise CanaryFinalityViolation(
                "cannot replay a canary with an existing post-admission revision history"
            )

        shadow_versions: dict[tuple[str, datetime], dict[str, list[tuple[int, datetime]]]] = (
            defaultdict(lambda: defaultdict(list))
        )
        shadow_bars: dict[tuple[str, datetime], V3CoreBar] = {}
        shadow_admissions: dict[tuple[str, datetime], dict[str, object]] = {}
        previous_sequence = 0
        previous_record_hash: str | None = None
        for raw in raw_records:
            if raw.sequence != previous_sequence + 1:
                raise RuntimeError("raw replay sequence is not continuous")
            if raw.previous_record_hash != previous_record_hash:
                raise RuntimeError("raw replay hash chain is not continuous")
            previous_sequence = raw.sequence
            previous_record_hash = raw.record_hash
            bars = parse_binance_klines(
                raw.payload,
                symbol=raw.symbol,
                collected_at=raw.collected_at,
                source_snapshot_hash=source_snapshot_hash,
            )
            self._observe_state(
                raw,
                bars,
                versions=shadow_versions,
                admitted_bars=shadow_bars,
                admissions=shadow_admissions,
                persist_normalized=False,
                record_revisions=False,
            )

        persisted_bars = dict(self.normalized.bars)
        if not set(persisted_bars).issubset(shadow_bars):
            raise RuntimeError("persisted canonical bars do not match chronological replay")
        for key, persisted in persisted_bars.items():
            if bar_content_hash(persisted) != bar_content_hash(shadow_bars[key]):
                raise RuntimeError("persisted canonical bar identity differs from replay")

        # A crash after a raw receipt was durably appended but before the
        # derived canonical append is recoverable: reconstruct the missing
        # derived records only after the complete raw replay validates.  Raw
        # evidence is never rewritten and existing canonical records are never
        # replaced.
        for key in sorted(set(shadow_bars) - set(persisted_bars)):
            self.normalized.append(shadow_bars[key])

        self._versions = shadow_versions
        self._admissions = shadow_admissions

    def metrics(self) -> dict[str, object]:
        observed = set(self._versions)
        admitted = set(self.normalized.bars)
        first_receipts: list[float] = []
        first_revisions: list[float] = []
        last_revisions: list[float] = []
        stable_repeats: list[float] = []
        for key, versions in self._versions.items():
            observations = sorted(
                (timestamp, content_hash)
                for content_hash, values in versions.items()
                for _sequence, timestamp in values
            )
            if not observations:
                continue
            interval_end = key[1]
            first_receipts.append((observations[0][0] - interval_end).total_seconds())
            first_hash = observations[0][1]
            changed = [
                timestamp for timestamp, content_hash in observations if content_hash != first_hash
            ]
            if changed:
                first_revisions.append((changed[0] - interval_end).total_seconds())
                last_revisions.append((changed[-1] - interval_end).total_seconds())
            repeated = [
                timestamp
                for content_hash, values in versions.items()
                if len(values) >= self.repeat_receipts
                for _sequence, timestamp in sorted(values, key=lambda item: item[1])[
                    self.repeat_receipts - 1 : self.repeat_receipts
                ]
            ]
            if repeated:
                stable_repeats.append((min(repeated) - interval_end).total_seconds())
        return {
            "raw_interval_identities": len(observed),
            "admitted_final_intervals": len(admitted),
            "unresolved_intervals": len(observed - admitted),
            "post_admission_revision_count": len(self.revisions),
            "pre_admission_variation_intervals": sum(
                1
                for admission in self._admissions.values()
                if int(admission["pre_admission_variation_count"]) > 0
            ),
            "admissions": [
                {
                    **admission,
                    "interval_end": admission["interval_end"].isoformat(),
                    "admission_observed_at": admission["admission_observed_at"].isoformat(),
                    "supporting_receipt_timestamps": [
                        timestamp.isoformat()
                        for timestamp in admission["supporting_receipt_timestamps"]
                    ],
                    "supporting_receipt_sequences": list(admission["supporting_receipt_sequences"]),
                    "pre_admission_content_hashes": list(admission["pre_admission_content_hashes"]),
                }
                for _key, admission in sorted(self._admissions.items(), key=lambda item: item[0])
            ],
            "first_post_close_receipt_latency_seconds": first_receipts,
            "first_revision_latency_seconds": first_revisions,
            "last_revision_latency_seconds": last_revisions,
            "first_repeated_terminal_latency_seconds": stable_repeats,
        }


def sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def write_canary_preregistration(path: Path, preregistration: CanaryPreregistration) -> str:
    """Write one preregistration artifact and return its immutable file hash."""

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = load_canary_preregistration(path)
        if existing != preregistration:
            raise RuntimeError("canary preregistration already exists with different content")
        return sha256_file(path)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(preregistration.model_dump(mode="json"), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return sha256_file(path)


def load_canary_preregistration(
    path: Path, *, expected_sha256: str | None = None
) -> CanaryPreregistration:
    path = path.resolve()
    if expected_sha256 is not None and sha256_file(path) != _digest(
        expected_sha256, "preregistration hash"
    ):
        raise ValueError("canary preregistration hash mismatch")
    value = json.loads(path.read_text(encoding="utf-8"))
    return CanaryPreregistration.model_validate(value)


def require_canary_artifact(payload: Mapping[str, object]) -> None:
    """Reject any artifact that could be mistaken for admission evidence."""

    if payload.get("evidence_class") != CANARY_EVIDENCE_CLASS:
        raise ValueError("canary artifact evidence class is missing or incorrect")
    if payload.get("admission_eligible") is not False:
        raise ValueError("canary artifact cannot be admission eligible")
    if payload.get("phase4_materialization_eligible") is True:
        raise ValueError("canary artifact cannot be materialized for Phase 4")


__all__ = [
    "CANARY_CONTEXT_BARS",
    "CANARY_CONTEXT_LAG_SECONDS",
    "CANARY_EVIDENCE_CLASS",
    "CANARY_FINALITY_GUARD_SECONDS",
    "CANARY_MIN_CUTOFFS_PER_SYMBOL",
    "CANARY_PREREGISTRATION_SCHEMA",
    "CANARY_PREFLIGHT_SCHEMA",
    "CANARY_REJECTION_SCHEMA",
    "CANARY_REPEAT_RECEIPTS",
    "CanaryFinalityTracker",
    "CanaryFinalityViolation",
    "CanaryPostAdmissionRevision",
    "CanaryPredictionLedger",
    "CanaryPredictionRecord",
    "CanaryPreflightCheck",
    "CanaryPreflightReport",
    "CanaryPreregistration",
    "CanaryRejectionLedger",
    "CanaryRejectionRecord",
    "bar_content_hash",
    "load_canary_preregistration",
    "require_canary_artifact",
    "sha256_file",
    "write_canary_preregistration",
]
