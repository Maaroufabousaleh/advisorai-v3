"""Credential-free forward PIT collection for the V3-Core 5-minute contract.

The collector boundary in this module is deliberately narrower than a venue
connector.  It can issue only public ``GET /api/v3/klines`` requests to the
reviewed Binance market-data surface.  It has no credential resolver, account
operation, or order operation.  Raw responses are fsync'd before any kline is
parsed into a :class:`V3CoreBar`.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.collectors.sources import HttpResponse
from advisorai.phase4.v3core_cadence import (
    V3_CORE_MARKET_DATA_PROVIDER,
    V3_CORE_MARKET_DATA_REST_ENDPOINT,
    V3_CORE_SYMBOLS,
    V3CoreBar,
    V3CoreBarProvenance,
    V3CoreCaseBuild,
    V3CoreSourceHealthState,
    build_v3core_cases,
    sha256_json,
)

FORWARD_RAW_SCHEMA = "advisorai.phase4.v3-core-forward.raw-response.v1"
FORWARD_FAILURE_SCHEMA = "advisorai.phase4.v3-core-forward.failure.v1"
FORWARD_HEALTH_SCHEMA = "advisorai.phase4.v3-core-forward.health.v1"
FORWARD_CASE_SCHEMA = "advisorai.phase4.v3-core-forward.case.v1"
FORWARD_REJECTION_SCHEMA = "advisorai.phase4.v3-core-forward.rejection.v1"
FORWARD_PREDICTION_SCHEMA = "advisorai.phase4.v3-core-forward.prediction.v1"
FORWARD_INTERVAL = "5m"
FORWARD_INTERVAL_SECONDS = 300
FORWARD_INTERVAL_MILLISECONDS = FORWARD_INTERVAL_SECONDS * 1000
FORWARD_SOURCE_SNAPSHOT_VERSION = "binance-public-market-data-forward-contract-v2"


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash_payload(payload: object) -> str:
    return sha256(_canonical_bytes(payload)).hexdigest()


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _iso(value: datetime, field_name: str) -> str:
    """Use Pydantic's UTC ``Z`` spelling for stable ledger hashes."""

    return _aware(value, field_name).isoformat().replace("+00:00", "Z")


def _digest(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return normalized


def _datetime_from_milliseconds(value: object, field_name: str) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer millisecond timestamp")
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError(f"{field_name} is outside the supported timestamp range") from exc


def _decimal(value: object, field_name: str, *, allow_zero: bool = True) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field_name} is not numeric") from exc
    if not result.is_finite() or (not allow_zero and result <= 0) or (allow_zero and result < 0):
        raise ValueError(f"{field_name} must be finite and non-negative")
    return result


class ForwardRawResponse(BaseModel):
    """One raw public response, retained once per receipt in hash-chain order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = FORWARD_RAW_SCHEMA
    sequence: int = Field(ge=1)
    symbol: str = Field(min_length=1)
    endpoint: str = V3_CORE_MARKET_DATA_REST_ENDPOINT
    request_url: str = Field(min_length=1)
    collected_at: datetime
    status_code: int
    response_sha256: str
    payload_b64: str = Field(min_length=1)
    previous_record_hash: str | None = None
    record_hash: str

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in V3_CORE_SYMBOLS:
            raise ValueError("forward collection is restricted to BTCUSDT and ETHUSDT")
        return normalized

    @field_validator("collected_at")
    @classmethod
    def validate_collected_at(cls, value: datetime) -> datetime:
        return _aware(value, "collected_at")

    @field_validator("response_sha256", "previous_record_hash", "record_hash")
    @classmethod
    def validate_hash(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _digest(value, getattr(info, "field_name", "record hash"))

    @field_validator("status_code")
    @classmethod
    def validate_status(cls, value: int) -> int:
        if not 100 <= value <= 599:
            raise ValueError("response status must be an HTTP status code")
        return value

    @model_validator(mode="after")
    def validate_record(self) -> ForwardRawResponse:
        if self.schema_version != FORWARD_RAW_SCHEMA:
            raise ValueError("unsupported forward raw response schema")
        if self.endpoint != V3_CORE_MARKET_DATA_REST_ENDPOINT:
            raise ValueError("forward responses must use the reviewed market-data-only endpoint")
        try:
            payload = base64.b64decode(self.payload_b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("forward raw payload is not valid base64") from exc
        if sha256(payload).hexdigest() != self.response_sha256:
            raise ValueError("forward raw payload hash does not match")
        unsigned = self.model_dump(mode="json", exclude={"record_hash"})
        if _hash_payload(unsigned) != self.record_hash:
            raise ValueError("forward raw response hash chain record is inconsistent")
        return self

    @property
    def payload(self) -> bytes:
        return base64.b64decode(self.payload_b64, validate=True)

    @classmethod
    def from_response(
        cls,
        response: HttpResponse,
        *,
        sequence: int,
        symbol: str,
        request_url: str,
        previous_record_hash: str | None,
    ) -> ForwardRawResponse:
        payload = bytes(response.body)
        unsigned = {
            "schema_version": FORWARD_RAW_SCHEMA,
            "sequence": sequence,
            "symbol": symbol,
            "endpoint": V3_CORE_MARKET_DATA_REST_ENDPOINT,
            "request_url": request_url,
            "collected_at": _iso(response.fetched_at, "response fetched_at"),
            "status_code": response.status_code,
            "response_sha256": sha256(payload).hexdigest(),
            "payload_b64": base64.b64encode(payload).decode("ascii"),
            "previous_record_hash": previous_record_hash,
        }
        return cls(**unsigned, record_hash=_hash_payload(unsigned))


class ForwardRawSpool:
    """Crash-safe append-only spool that retains repeated polling receipts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[ForwardRawResponse] = []
        if self.path.exists():
            previous: str | None = None
            for line_number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not line.strip():
                    continue
                try:
                    record = ForwardRawResponse.model_validate_json(line)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"forward raw spool is corrupt at line {line_number}"
                    ) from exc
                if record.sequence != len(self.records) + 1:
                    raise RuntimeError("forward raw spool sequence is not append-only")
                if record.previous_record_hash != previous:
                    raise RuntimeError("forward raw spool hash chain is not continuous")
                self.records.append(record)
                previous = record.record_hash

    @property
    def last_record_hash(self) -> str | None:
        return self.records[-1].record_hash if self.records else None

    def append(
        self,
        response: HttpResponse,
        *,
        symbol: str,
        request_url: str,
    ) -> ForwardRawResponse:
        record = ForwardRawResponse.from_response(
            response,
            sequence=len(self.records) + 1,
            symbol=symbol,
            request_url=request_url,
            previous_record_hash=self.last_record_hash,
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.records.append(record)
        return record

    def read(self) -> tuple[ForwardRawResponse, ...]:
        return tuple(self.records)


class ForwardFailureRecord(BaseModel):
    """Sanitized failure evidence; provider bodies and headers are excluded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = FORWARD_FAILURE_SCHEMA
    sequence: int = Field(ge=1)
    symbol: str
    observed_at: datetime
    failure_class: str = Field(min_length=1)
    status_code: int | None = None
    retriable: bool = False
    previous_record_hash: str | None = None
    record_hash: str

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in V3_CORE_SYMBOLS:
            raise ValueError("forward failures are restricted to BTCUSDT and ETHUSDT")
        return normalized

    @field_validator("observed_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _aware(value, "observed_at")

    @field_validator("previous_record_hash", "record_hash")
    @classmethod
    def validate_hash(cls, value: str | None, info: object) -> str | None:
        return None if value is None else _digest(value, getattr(info, "field_name", "record hash"))

    @model_validator(mode="after")
    def validate_failure(self) -> ForwardFailureRecord:
        if self.schema_version != FORWARD_FAILURE_SCHEMA:
            raise ValueError("unsupported forward failure schema")
        if self.status_code is not None and not 100 <= self.status_code <= 599:
            raise ValueError("failure status must be an HTTP status code")
        unsigned = self.model_dump(mode="json", exclude={"record_hash"})
        if _hash_payload(unsigned) != self.record_hash:
            raise ValueError("forward failure hash is inconsistent")
        return self


class ForwardFailureSpool:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[ForwardFailureRecord] = []
        if self.path.exists():
            previous: str | None = None
            for line_number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not line.strip():
                    continue
                try:
                    record = ForwardFailureRecord.model_validate_json(line)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"forward failure spool is corrupt at line {line_number}"
                    ) from exc
                if (
                    record.sequence != len(self.records) + 1
                    or record.previous_record_hash != previous
                ):
                    raise RuntimeError("forward failure spool hash chain is not continuous")
                self.records.append(record)
                previous = record.record_hash

    @property
    def last_record_hash(self) -> str | None:
        return self.records[-1].record_hash if self.records else None

    def append(
        self,
        *,
        symbol: str,
        observed_at: datetime,
        failure_class: str,
        status_code: int | None = None,
        retriable: bool = False,
    ) -> ForwardFailureRecord:
        unsigned = {
            "schema_version": FORWARD_FAILURE_SCHEMA,
            "sequence": len(self.records) + 1,
            "symbol": symbol,
            "observed_at": _iso(observed_at, "observed_at"),
            "failure_class": failure_class,
            "status_code": status_code,
            "retriable": retriable,
            "previous_record_hash": self.last_record_hash,
        }
        record = ForwardFailureRecord(**unsigned, record_hash=_hash_payload(unsigned))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.records.append(record)
        return record


class ForwardHealthTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = FORWARD_HEALTH_SCHEMA
    sequence: int = Field(ge=1)
    symbol: str
    observed_at: datetime
    from_state: V3CoreSourceHealthState | None = None
    to_state: V3CoreSourceHealthState
    reason: str = Field(min_length=1)
    last_valid_interval_end: datetime | None = None
    last_collected_at: datetime | None = None
    previous_record_hash: str | None = None
    record_hash: str

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in V3_CORE_SYMBOLS:
            raise ValueError("forward health is restricted to BTCUSDT and ETHUSDT")
        return normalized

    @field_validator("observed_at", "last_valid_interval_end", "last_collected_at")
    @classmethod
    def validate_times(cls, value: datetime | None, info: object) -> datetime | None:
        return None if value is None else _aware(value, getattr(info, "field_name", "health time"))

    @field_validator("previous_record_hash", "record_hash")
    @classmethod
    def validate_hash(cls, value: str | None, info: object) -> str | None:
        return None if value is None else _digest(value, getattr(info, "field_name", "record hash"))

    @model_validator(mode="after")
    def validate_transition(self) -> ForwardHealthTransition:
        if self.schema_version != FORWARD_HEALTH_SCHEMA:
            raise ValueError("unsupported forward health schema")
        unsigned = self.model_dump(mode="json", exclude={"record_hash"})
        if _hash_payload(unsigned) != self.record_hash:
            raise ValueError("forward health hash is inconsistent")
        return self


class ForwardHealthLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[ForwardHealthTransition] = []
        if self.path.exists():
            previous: str | None = None
            for line_number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not line.strip():
                    continue
                try:
                    record = ForwardHealthTransition.model_validate_json(line)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"forward health ledger is corrupt at line {line_number}"
                    ) from exc
                if (
                    record.sequence != len(self.records) + 1
                    or record.previous_record_hash != previous
                ):
                    raise RuntimeError("forward health ledger hash chain is not continuous")
                self.records.append(record)
                previous = record.record_hash

    @property
    def last_record_hash(self) -> str | None:
        return self.records[-1].record_hash if self.records else None

    def append(
        self,
        *,
        symbol: str,
        observed_at: datetime,
        to_state: V3CoreSourceHealthState,
        reason: str,
        last_valid_interval_end: datetime | None = None,
        last_collected_at: datetime | None = None,
    ) -> ForwardHealthTransition | None:
        prior = next(
            (record.to_state for record in reversed(self.records) if record.symbol == symbol),
            None,
        )
        if prior == to_state:
            return None
        unsigned = {
            "schema_version": FORWARD_HEALTH_SCHEMA,
            "sequence": len(self.records) + 1,
            "symbol": symbol,
            "observed_at": _iso(observed_at, "observed_at"),
            "from_state": prior,
            "to_state": to_state,
            "reason": reason,
            "last_valid_interval_end": (
                _iso(last_valid_interval_end, "last_valid_interval_end")
                if last_valid_interval_end
                else None
            ),
            "last_collected_at": (
                _iso(last_collected_at, "last_collected_at") if last_collected_at else None
            ),
            "previous_record_hash": self.last_record_hash,
        }
        record = ForwardHealthTransition(**unsigned, record_hash=_hash_payload(unsigned))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.records.append(record)
        return record


class ForwardNormalizedBarSpool:
    """Append-only normalized bars, keyed by instrument and interval end."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.bars: dict[tuple[str, datetime], V3CoreBar] = {}
        if self.path.exists():
            for line_number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not line.strip():
                    continue
                try:
                    bar = V3CoreBar.model_validate_json(line)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"forward normalized spool is corrupt at line {line_number}"
                    ) from exc
                key = (bar.instrument, bar.interval_end)
                prior = self.bars.get(key)
                if prior is not None and prior != bar:
                    raise RuntimeError("forward normalized spool contains conflicting bar identity")
                self.bars[key] = bar

    def append(self, bar: V3CoreBar) -> bool:
        key = (bar.instrument, bar.interval_end)
        prior = self.bars.get(key)
        if prior is not None:
            prior_identity = prior.model_dump(mode="json")
            current_identity = bar.model_dump(mode="json")
            for payload in (prior_identity, current_identity):
                provenance = payload["provenance"]
                provenance.pop("collected_at", None)
                provenance.pop("normalized_record_hash", None)
            if prior_identity != current_identity:
                raise RuntimeError("forward normalization changed an existing bar")
            # The same closed bar is commonly returned on several polls.  Keep
            # the first receipt as the canonical normalized record while the
            # raw spool retains every later receipt and its local timestamp.
            return False
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(bar.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.bars[key] = bar
        return True

    def read(self) -> tuple[V3CoreBar, ...]:
        return tuple(self.bars[key] for key in sorted(self.bars))


class ForwardCaseSpool:
    """Append-only case ledger; cases appear only after their outcomes close."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cases: dict[str, object] = {}
        if self.path.exists():
            for line_number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    case_id = str(record["case"]["case_id"])
                    case_hash = str(record["case_hash"])
                    if _hash_payload(record["case"]) != case_hash:
                        raise ValueError("case hash mismatch")
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"forward case spool is corrupt at line {line_number}"
                    ) from exc
                prior = self.cases.get(case_id)
                if prior is not None and prior != record["case"]:
                    raise RuntimeError("forward case spool contains conflicting case identity")
                self.cases[case_id] = record["case"]

    def append(self, case: BaseModel) -> bool:
        case_payload = case.model_dump(mode="json")
        case_id = str(case_payload["case_id"])
        prior = self.cases.get(case_id)
        if prior is not None:
            if prior != case_payload:
                raise RuntimeError("forward case reconstruction changed an existing case")
            return False
        record = {
            "schema": FORWARD_CASE_SCHEMA,
            "case": case_payload,
            "case_hash": _hash_payload(case_payload),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.cases[case_id] = case_payload
        return True

    def count_by_symbol(self) -> dict[str, int]:
        return {
            symbol: sum(case.get("instrument") == symbol for case in self.cases.values())
            for symbol in V3_CORE_SYMBOLS
        }


class ForwardRejectionRecord(BaseModel):
    """A retained invalid cutoff; gaps are never silently repaired."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = FORWARD_REJECTION_SCHEMA
    sequence: int = Field(ge=1)
    instrument: str
    cutoff: datetime
    reason: str = Field(min_length=1)
    previous_record_hash: str | None = None
    record_hash: str

    @field_validator("instrument")
    @classmethod
    def validate_rejection_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in V3_CORE_SYMBOLS:
            raise ValueError("forward rejections are restricted to BTCUSDT and ETHUSDT")
        return normalized

    @field_validator("cutoff")
    @classmethod
    def validate_rejection_cutoff(cls, value: datetime) -> datetime:
        return _aware(value, "cutoff")

    @field_validator("previous_record_hash", "record_hash")
    @classmethod
    def validate_rejection_hash(cls, value: str | None, info: object) -> str | None:
        return None if value is None else _digest(value, getattr(info, "field_name", "record hash"))

    @model_validator(mode="after")
    def validate_rejection(self) -> ForwardRejectionRecord:
        if self.schema_version != FORWARD_REJECTION_SCHEMA:
            raise ValueError("unsupported forward rejection schema")
        unsigned = self.model_dump(mode="json", exclude={"record_hash"})
        if _hash_payload(unsigned) != self.record_hash:
            raise ValueError("forward rejection hash is inconsistent")
        return self


class ForwardRejectionSpool:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[ForwardRejectionRecord] = []
        self.identities: set[tuple[str, datetime, str]] = set()
        if self.path.exists():
            previous: str | None = None
            for line_number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not line.strip():
                    continue
                try:
                    record = ForwardRejectionRecord.model_validate_json(line)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"forward rejection spool is corrupt at line {line_number}"
                    ) from exc
                if (
                    record.sequence != len(self.records) + 1
                    or record.previous_record_hash != previous
                ):
                    raise RuntimeError("forward rejection spool hash chain is not continuous")
                identity = (record.instrument, record.cutoff, record.reason)
                if identity in self.identities:
                    raise RuntimeError("forward rejection spool contains a duplicate identity")
                self.identities.add(identity)
                self.records.append(record)
                previous = record.record_hash

    @property
    def last_record_hash(self) -> str | None:
        return self.records[-1].record_hash if self.records else None

    def append(self, *, instrument: str, cutoff: datetime, reason: str) -> bool:
        identity = (instrument.strip().upper(), _aware(cutoff, "cutoff"), reason)
        if identity in self.identities:
            return False
        unsigned = {
            "schema_version": FORWARD_REJECTION_SCHEMA,
            "sequence": len(self.records) + 1,
            "instrument": identity[0],
            "cutoff": _iso(identity[1], "cutoff"),
            "reason": reason,
            "previous_record_hash": self.last_record_hash,
        }
        record = ForwardRejectionRecord(**unsigned, record_hash=_hash_payload(unsigned))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.records.append(record)
        self.identities.add(identity)
        return True


class ForwardPredictionRecord(BaseModel):
    """Immutable prediction-side ledger record, before its outcome exists."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = FORWARD_PREDICTION_SCHEMA
    prediction_id: str = Field(min_length=1)
    instrument: str
    model: str = Field(min_length=1)
    model_identity_hash: str
    cutoff: datetime
    input_snapshot_hash: str
    predicted_return_bps: Decimal
    generated_at: datetime
    runtime_latency_ms: Decimal = Field(ge=0)
    outcome_case_id: str | None = None

    # Candidate inference timing is optional for backward compatibility with
    # existing baseline records.  When present, these fields distinguish the
    # time the model became available from the later ledger write boundary.
    inference_started_at: datetime | None = None
    inference_finished_at: datetime | None = None
    ledger_persisted_at: datetime | None = None

    # Candidate-specific runtime metadata is optional so the shared ledger
    # remains backward-compatible with deterministic baseline predictions.
    # When present, it binds the candidate output to the exact source,
    # checkpoint, preprocessing, environment, and resource observation used
    # to produce it.
    source_snapshot_hash: str | None = None
    checkpoint_hash: str | None = None
    runner_hash: str | None = None
    preprocessing_identity: str | None = Field(default=None, min_length=1)
    preprocessing_hash: str | None = None
    dependency_lock_hash: str | None = None
    runtime_environment_hash: str | None = None
    device: str | None = Field(default=None, min_length=1)
    native_interval_lower_bps: Decimal | None = None
    native_interval_upper_bps: Decimal | None = None
    native_confidence: Decimal | None = None
    resource_peak_rss_mib: Decimal | None = Field(default=None, ge=0)
    resource_peak_cpu_percent: Decimal | None = Field(default=None, ge=0)
    resource_sample_count: int | None = Field(default=None, ge=1)
    provenance: tuple[tuple[str, str], ...] = ()

    @field_validator("instrument")
    @classmethod
    def validate_prediction_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in V3_CORE_SYMBOLS:
            raise ValueError("forward predictions are restricted to BTCUSDT and ETHUSDT")
        return normalized

    @field_validator(
        "model_identity_hash",
        "input_snapshot_hash",
        "source_snapshot_hash",
        "checkpoint_hash",
        "runner_hash",
        "preprocessing_hash",
        "dependency_lock_hash",
        "runtime_environment_hash",
    )
    @classmethod
    def validate_prediction_hash(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _digest(value, getattr(info, "field_name", "prediction hash"))

    @field_validator(
        "cutoff",
        "generated_at",
        "inference_started_at",
        "inference_finished_at",
        "ledger_persisted_at",
    )
    @classmethod
    def validate_prediction_time(cls, value: datetime | None, info: object) -> datetime | None:
        if value is None:
            return None
        return _aware(value, getattr(info, "field_name", "prediction timestamp"))

    @field_validator(
        "predicted_return_bps",
        "runtime_latency_ms",
        "native_interval_lower_bps",
        "native_interval_upper_bps",
        "native_confidence",
        "resource_peak_rss_mib",
        "resource_peak_cpu_percent",
    )
    @classmethod
    def validate_prediction_decimal(cls, value: Decimal | None, info: object) -> Decimal | None:
        if value is None:
            return None
        if not value.is_finite():
            raise ValueError("prediction numeric fields must be finite")
        if (
            getattr(info, "field_name", "")
            in {
                "runtime_latency_ms",
                "resource_peak_rss_mib",
                "resource_peak_cpu_percent",
            }
            and value < 0
        ):
            raise ValueError("prediction resource and latency fields must be non-negative")
        return value

    @model_validator(mode="after")
    def validate_prediction(self) -> ForwardPredictionRecord:
        if self.schema_version != FORWARD_PREDICTION_SCHEMA:
            raise ValueError("unsupported forward prediction schema")
        if self.generated_at > self.cutoff:
            raise ValueError("prediction cannot be generated after its cutoff")
        timing = (
            self.inference_started_at,
            self.inference_finished_at,
            self.ledger_persisted_at,
        )
        if any(value is not None for value in timing) and not all(
            value is not None for value in timing
        ):
            raise ValueError("candidate inference timing must be complete when present")
        if all(value is not None for value in timing):
            assert self.inference_started_at is not None
            assert self.inference_finished_at is not None
            assert self.ledger_persisted_at is not None
            if self.inference_finished_at < self.inference_started_at:
                raise ValueError("candidate inference finished before it started")
            if self.ledger_persisted_at < self.inference_finished_at:
                raise ValueError("candidate ledger persistence precedes inference completion")
            if self.generated_at != self.inference_finished_at:
                raise ValueError("candidate generated_at must equal inference completion")
        if (self.native_interval_lower_bps is None) != (self.native_interval_upper_bps is None):
            raise ValueError("native prediction intervals require both bounds")
        if (
            self.native_interval_lower_bps is not None
            and self.native_interval_lower_bps > self.native_interval_upper_bps
        ):
            raise ValueError("native prediction interval bounds are inconsistent")
        identity_fields = (
            self.source_snapshot_hash,
            self.checkpoint_hash,
            self.runner_hash,
            self.preprocessing_identity,
            self.preprocessing_hash,
            self.dependency_lock_hash,
            self.runtime_environment_hash,
            self.device,
            self.resource_peak_rss_mib,
            self.resource_peak_cpu_percent,
            self.resource_sample_count,
        )
        if any(value is not None for value in identity_fields) and not all(
            value is not None for value in identity_fields
        ):
            raise ValueError("candidate runtime metadata must be complete when present")
        provenance_keys = [key for key, _ in self.provenance]
        if len(provenance_keys) != len(set(provenance_keys)) or any(
            not key.strip() or not value.strip() for key, value in self.provenance
        ):
            raise ValueError("prediction provenance keys and values must be unique and non-blank")
        return self


def parse_binance_klines(
    payload: bytes,
    *,
    symbol: str,
    collected_at: datetime,
    source_snapshot_hash: str,
    source_health_state: V3CoreSourceHealthState = "HEALTHY",
) -> tuple[V3CoreBar, ...]:
    """Normalize only closed Binance 5-minute rows; never synthesize missing bars."""

    collected_at = _aware(collected_at, "collected_at")
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Binance kline response is not valid JSON") from exc
    if not isinstance(decoded, list):
        raise ValueError("Binance kline response must be a list")
    normalized_symbol = symbol.strip().upper()
    if normalized_symbol not in V3_CORE_SYMBOLS:
        raise ValueError("forward collection is restricted to BTCUSDT and ETHUSDT")
    bars: list[V3CoreBar] = []
    seen_intervals: set[datetime] = set()
    for row in decoded:
        if not isinstance(row, list) or len(row) < 11:
            raise ValueError("Binance kline row is incomplete")
        interval_start = _datetime_from_milliseconds(row[0], "kline open time")
        interval_end = interval_start + timedelta(seconds=FORWARD_INTERVAL_SECONDS)
        close_time = _datetime_from_milliseconds(row[6], "kline close time")
        if close_time + timedelta(milliseconds=1) != interval_end:
            raise ValueError("Binance kline close semantics do not match the 5-minute contract")
        if interval_end in seen_intervals:
            raise ValueError("Binance kline response contains a duplicate interval")
        seen_intervals.add(interval_end)
        if collected_at < interval_end:
            # The newest row is commonly still open.  It is retained in the
            # raw spool but is not admitted to the normalized PIT plane.
            continue
        values = {
            "open": _decimal(row[1], "kline open", allow_zero=False),
            "high": _decimal(row[2], "kline high", allow_zero=False),
            "low": _decimal(row[3], "kline low", allow_zero=False),
            "close": _decimal(row[4], "kline close", allow_zero=False),
            "volume": _decimal(row[5], "kline volume"),
        }
        raw_record_hash = _hash_payload(row)
        provenance_without_hash = {
            "interval_end": interval_end.isoformat(),
            "provider_available_at": interval_end.isoformat(),
            "collected_at": collected_at.isoformat(),
            "provider_event_at": close_time.isoformat(),
            "availability_basis": "forward_observed",
            "evidence_class": "forward_pit_admission",
            "source_snapshot_hash": source_snapshot_hash,
            "raw_record_hash": raw_record_hash,
            "source_health_state": source_health_state,
        }
        normalized_hash = _hash_payload(
            {
                "instrument": normalized_symbol,
                **{key: str(value) for key, value in values.items()},
                "source_id": V3_CORE_MARKET_DATA_PROVIDER,
                "provider_identity": V3_CORE_MARKET_DATA_PROVIDER,
                "endpoint": V3_CORE_MARKET_DATA_REST_ENDPOINT,
                **provenance_without_hash,
            }
        )
        bars.append(
            V3CoreBar(
                instrument=normalized_symbol,
                provenance=V3CoreBarProvenance(
                    interval_end=interval_end,
                    provider_available_at=interval_end,
                    collected_at=collected_at,
                    provider_event_at=close_time,
                    availability_basis="forward_observed",
                    evidence_class="forward_pit_admission",
                    source_snapshot_hash=source_snapshot_hash,
                    raw_record_hash=raw_record_hash,
                    normalized_record_hash=normalized_hash,
                    source_health_state=source_health_state,
                ),
                **values,
                source_id=V3_CORE_MARKET_DATA_PROVIDER,
                provider_identity=V3_CORE_MARKET_DATA_PROVIDER,
                endpoint=V3_CORE_MARKET_DATA_REST_ENDPOINT,
                source_snapshot_hash=source_snapshot_hash,
            )
        )
    return tuple(bars)


def build_forward_cases(
    bars: Iterable[V3CoreBar],
    *,
    source_snapshot_hash: str,
    phase3_gate_record_sha256: str,
) -> V3CoreCaseBuild:
    """Build only completed forward cases and bind them to the Phase-3 gate."""

    normalized = tuple(bars)
    if any(bar.evidence_class != "forward_pit_admission" for bar in normalized):
        raise ValueError("forward case builder rejects non-forward evidence")
    _digest(phase3_gate_record_sha256, "phase3_gate_record_sha256")
    return build_v3core_cases(
        normalized,
        evidence_class="forward_pit_admission",
        source_id=V3_CORE_MARKET_DATA_PROVIDER,
        provider_identity=V3_CORE_MARKET_DATA_PROVIDER,
        endpoint=V3_CORE_MARKET_DATA_REST_ENDPOINT,
        source_snapshot_hash=source_snapshot_hash,
        phase3_admitted=True,
    )


def source_snapshot_hash(*, preregistration_sha256: str, phase3_gate_record_sha256: str) -> str:
    """Derive the immutable run snapshot identity without using response data."""

    return sha256_json(
        {
            "version": FORWARD_SOURCE_SNAPSHOT_VERSION,
            "provider_identity": V3_CORE_MARKET_DATA_PROVIDER,
            "endpoint": V3_CORE_MARKET_DATA_REST_ENDPOINT,
            "symbols": list(V3_CORE_SYMBOLS),
            "interval": FORWARD_INTERVAL,
            "evidence_class": "forward_pit_admission",
            "preregistration_sha256": _digest(preregistration_sha256, "preregistration_sha256"),
            "phase3_gate_record_sha256": _digest(
                phase3_gate_record_sha256, "phase3_gate_record_sha256"
            ),
            "credentials_loaded": False,
            "order_writes_attempted": False,
        }
    )


__all__ = [
    "FORWARD_CASE_SCHEMA",
    "FORWARD_FAILURE_SCHEMA",
    "FORWARD_HEALTH_SCHEMA",
    "FORWARD_INTERVAL",
    "FORWARD_INTERVAL_MILLISECONDS",
    "FORWARD_INTERVAL_SECONDS",
    "FORWARD_PREDICTION_SCHEMA",
    "FORWARD_RAW_SCHEMA",
    "ForwardCaseSpool",
    "ForwardFailureRecord",
    "ForwardFailureSpool",
    "ForwardHealthLedger",
    "ForwardHealthTransition",
    "ForwardNormalizedBarSpool",
    "ForwardPredictionRecord",
    "ForwardRejectionRecord",
    "ForwardRejectionSpool",
    "ForwardRawResponse",
    "ForwardRawSpool",
    "build_forward_cases",
    "parse_binance_klines",
    "source_snapshot_hash",
]
