"""Read-only raw/normalized integrity auditing for forward V3-Core evidence.

The forward collector deliberately keeps the first normalized bar while its raw
spool keeps every HTTP receipt.  This module compares those two immutable
surfaces after a run is sealed.  It never writes to an input spool, never
acquires data, and never makes a performance or model-selection decision.

Terminal stability is intentionally conservative: only observations received
at or after the provider interval end are eligible, and the terminal version
must repeat a reviewed number of times.  A bar with an unresolved or canonical
disagreement contaminates a case through a separate exclusion overlay; the
original case and prediction ledgers are not edited.
"""

from __future__ import annotations

import base64
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from advisorai.phase4.v3core_cadence import (
    V3_CORE_SYMBOLS,
    V3CoreForecastCase,
    sha256_json,
)
from advisorai.phase4.v3core_forward import (
    FORWARD_CASE_SCHEMA,
    ForwardRawResponse,
)
from advisorai.phase4.v3core_prediction_ledger import (
    ForwardPredictionLedgerEntry,
    ForwardPredictionOutcomeLink,
)

INTEGRITY_AUDIT_SCHEMA = "advisorai.phase4.v3-core.integrity-audit.v1"
INTEGRITY_OVERLAY_SCHEMA = "advisorai.phase4.v3-core.integrity-exclusion-overlay.v1"
STABILITY_RULE_VERSION = "closed_terminal_repeat_v1"
DEFAULT_MINIMUM_TERMINAL_CLOSED_OBSERVATIONS = 2
DEFAULT_MINIMUM_CASES_PER_SYMBOL = 64

BarStabilityClassification = Literal[
    "STABLE",
    "REVISED_BUT_CANONICAL_FINAL",
    "REVISED_CANONICAL_DISAGREES",
    "UNRESOLVED",
]
InvalidBarClassification = Literal["REVISED_CANONICAL_DISAGREES", "UNRESOLVED"]
CaseSegment = Literal["context", "outcome"]


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash_payload(payload: object) -> str:
    return sha256(_canonical_bytes(payload)).hexdigest()


def _sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


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


def _timestamp_from_milliseconds(value: object, field_name: str) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative millisecond timestamp")
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError(f"{field_name} is outside the supported timestamp range") from exc


def _read_lines(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        raise FileNotFoundError(f"integrity input is missing: {path}")
    return tuple(path.read_text(encoding="utf-8").splitlines())


class IntegrityAuditError(RuntimeError):
    """An input evidence surface is malformed or its immutable identity broke."""


class RawKlineObservation(BaseModel):
    """One kline from one raw response; public market data only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument: str
    interval_end: datetime
    provider_event_at: datetime
    receipt_at: datetime
    closed_at_receipt: bool
    raw_response_sequence: int = Field(ge=1)
    raw_record_hash: str
    response_sha256: str
    row_index: int = Field(ge=0)
    row_content_hash: str
    ohlcv_hash: str
    ohlcv: dict[str, str]

    @field_validator("interval_end", "provider_event_at", "receipt_at")
    @classmethod
    def aware_time(cls, value: datetime, info: object) -> datetime:
        return _aware(value, getattr(info, "field_name", "timestamp"))

    @field_validator("raw_record_hash", "response_sha256", "row_content_hash", "ohlcv_hash")
    @classmethod
    def valid_digest(cls, value: str, info: object) -> str:
        return _digest(value, getattr(info, "field_name", "digest"))


class RawKlineVersion(BaseModel):
    """All receipts of one distinct OHLCV value for an interval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ohlcv_hash: str
    ohlcv: dict[str, str]
    observation_count: int = Field(ge=1)
    closed_observation_count: int = Field(ge=0)
    first_receipt_at: datetime
    last_receipt_at: datetime
    raw_record_hashes: tuple[str, ...]
    response_sha256s: tuple[str, ...]

    @field_validator("ohlcv_hash")
    @classmethod
    def valid_ohlcv_digest(cls, value: str) -> str:
        return _digest(value, "ohlcv_hash")

    @field_validator("first_receipt_at", "last_receipt_at")
    @classmethod
    def aware_time(cls, value: datetime, info: object) -> datetime:
        return _aware(value, getattr(info, "field_name", "receipt timestamp"))


class NormalizedBarObservation(BaseModel):
    """The canonical normalized record observed in normalized-bars.jsonl."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument: str
    interval_end: datetime
    receipt_at: datetime
    normalized_record_hash: str
    normalized_hash_valid: bool
    raw_record_hash: str
    ohlcv_hash: str
    ohlcv: dict[str, str]
    source_health_state: str

    @field_validator("interval_end", "receipt_at")
    @classmethod
    def aware_time(cls, value: datetime, info: object) -> datetime:
        return _aware(value, getattr(info, "field_name", "timestamp"))

    @field_validator("normalized_record_hash", "raw_record_hash", "ohlcv_hash")
    @classmethod
    def valid_digest(cls, value: str, info: object) -> str:
        return _digest(value, getattr(info, "field_name", "digest"))


class BarIntegrityRecord(BaseModel):
    """Deterministic terminal integrity result for one market interval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument: str
    interval_end: datetime
    first_raw_observation: RawKlineObservation
    first_normalized_observation: NormalizedBarObservation | None
    raw_observations: tuple[RawKlineObservation, ...]
    raw_versions: tuple[RawKlineVersion, ...]
    closed_version_sequence: tuple[str, ...]
    terminal_closed_observation: RawKlineObservation | None
    final_observed_value: RawKlineObservation
    terminal_stable_version_hash: str | None
    terminal_consecutive_observations: int = Field(ge=0)
    repeated_identical_observation_count: int = Field(ge=0)
    revision_count: int = Field(ge=0)
    changed_ohlcv_fields: tuple[str, ...]
    normalized_record_hash: str | None
    normalized_hash_valid: bool
    normalized_conflict: bool
    classification: BarStabilityClassification
    classification_reason: str

    @field_validator("interval_end")
    @classmethod
    def aware_interval(cls, value: datetime) -> datetime:
        return _aware(value, "interval_end")


class CaseContamination(BaseModel):
    """An exclusion overlay entry; it never edits the completed case ledger."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    instrument: str
    cutoff: datetime
    affected_segments: tuple[CaseSegment, ...]
    affected_interval_ends: tuple[datetime, ...]
    reasons: tuple[str, ...]
    classifications: tuple[InvalidBarClassification, ...]

    @field_validator("cutoff", "affected_interval_ends")
    @classmethod
    def aware_times(cls, value: datetime | tuple[datetime, ...], info: object):
        if isinstance(value, tuple):
            return tuple(_aware(item, getattr(info, "field_name", "timestamp")) for item in value)
        return _aware(value, getattr(info, "field_name", "timestamp"))


class PredictionIntegrityExclusion(BaseModel):
    """A preserved prediction that cannot enter scoring because its case is bad."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prediction_id: str
    instrument: str
    model: str
    cutoff: datetime
    outcome_case_id: str
    status: Literal["EXCLUDED_DATA_INTEGRITY"] = "EXCLUDED_DATA_INTEGRITY"
    reasons: tuple[str, ...]

    @field_validator("cutoff")
    @classmethod
    def aware_cutoff(cls, value: datetime) -> datetime:
        return _aware(value, "cutoff")


class IntegrityAuditReport(BaseModel):
    """Immutable-shaped audit output written beside, never over, source ledgers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = INTEGRITY_AUDIT_SCHEMA
    generated_at: datetime
    terminal_observed_at: datetime
    stability_rule_version: str = STABILITY_RULE_VERSION
    minimum_terminal_closed_observations: int = Field(
        ge=2, default=DEFAULT_MINIMUM_TERMINAL_CLOSED_OBSERVATIONS
    )
    minimum_cases_per_symbol: int = Field(ge=1, default=DEFAULT_MINIMUM_CASES_PER_SYMBOL)
    raw_responses_sha256: str
    normalized_bars_sha256: str
    completed_cases_sha256: str | None = None
    raw_response_count: int = Field(ge=0)
    raw_observation_count: int = Field(ge=0)
    normalized_bar_count: int = Field(ge=0)
    raw_hash_chain_valid: bool
    normalized_input_valid: bool
    normalized_hash_validation_failures: int = Field(ge=0)
    completed_case_ledger_valid: bool
    prediction_ledgers_valid: bool
    prediction_timing_valid: bool
    prediction_link_integrity_valid: bool
    prediction_count: int = Field(ge=0)
    classification_counts: dict[str, int]
    bar_records: tuple[BarIntegrityRecord, ...]
    raw_completed_case_counts: dict[str, int]
    integrity_eligible_case_counts: dict[str, int]
    contaminated_cases: tuple[CaseContamination, ...]
    excluded_predictions: tuple[PredictionIntegrityExclusion, ...]
    admission_minimum_met: bool
    errors: tuple[str, ...] = ()

    @field_validator("generated_at", "terminal_observed_at")
    @classmethod
    def aware_report_time(cls, value: datetime, info: object) -> datetime:
        return _aware(value, getattr(info, "field_name", "report timestamp"))

    @field_validator(
        "raw_responses_sha256",
        "normalized_bars_sha256",
        "completed_cases_sha256",
    )
    @classmethod
    def valid_report_digest(cls, value: str | None, info: object) -> str | None:
        return None if value is None else _digest(value, getattr(info, "field_name", "digest"))


class IntegrityExclusionOverlay(BaseModel):
    """Separate, non-authoritative exclusion view derived from an audit report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = INTEGRITY_OVERLAY_SCHEMA
    generated_at: datetime
    audit_report_sha256: str
    contaminated_case_ids: tuple[str, ...]
    contaminated_cases: tuple[CaseContamination, ...]
    excluded_predictions: tuple[PredictionIntegrityExclusion, ...]
    raw_completed_case_counts: dict[str, int]
    integrity_eligible_case_counts: dict[str, int]
    admission_minimum_met: bool
    authority: str = "read_only_exclusion_overlay_no_mutation"

    @field_validator("generated_at")
    @classmethod
    def aware_overlay_time(cls, value: datetime) -> datetime:
        return _aware(value, "generated_at")

    @field_validator("audit_report_sha256")
    @classmethod
    def valid_overlay_digest(cls, value: str) -> str:
        return _digest(value, "audit_report_sha256")


def _load_raw_records(path: Path) -> tuple[ForwardRawResponse, ...]:
    records: list[ForwardRawResponse] = []
    previous: str | None = None
    for line_number, line in enumerate(_read_lines(path), start=1):
        if not line.strip():
            continue
        try:
            record = ForwardRawResponse.model_validate_json(line)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise IntegrityAuditError(f"raw response is invalid at line {line_number}") from exc
        if record.sequence != len(records) + 1 or record.previous_record_hash != previous:
            raise IntegrityAuditError("raw response hash chain is not continuous")
        records.append(record)
        previous = record.record_hash
    return tuple(records)


def _decode_raw_observations(record: ForwardRawResponse) -> tuple[RawKlineObservation, ...]:
    if record.status_code != 200:
        return ()
    try:
        payload = json.loads(base64.b64decode(record.payload_b64, validate=True))
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityAuditError(
            f"successful raw response {record.sequence} is not valid JSON"
        ) from exc
    if not isinstance(payload, list):
        raise IntegrityAuditError(f"successful raw response {record.sequence} is not a kline list")
    observations: list[RawKlineObservation] = []
    for row_index, row in enumerate(payload):
        if not isinstance(row, list) or len(row) < 11:
            raise IntegrityAuditError(
                f"raw response {record.sequence} contains an incomplete kline row"
            )
        interval_start = _timestamp_from_milliseconds(row[0], "kline open time")
        interval_end = interval_start + timedelta(minutes=5)
        close_time = _timestamp_from_milliseconds(row[6], "kline close time")
        if close_time + timedelta(milliseconds=1) != interval_end:
            raise IntegrityAuditError(
                f"raw response {record.sequence} has invalid provider close semantics"
            )
        ohlcv = {
            "open": str(row[1]),
            "high": str(row[2]),
            "low": str(row[3]),
            "close": str(row[4]),
            "volume": str(row[5]),
        }
        observations.append(
            RawKlineObservation(
                instrument=record.symbol,
                interval_end=interval_end,
                provider_event_at=close_time,
                receipt_at=record.collected_at,
                closed_at_receipt=record.collected_at >= interval_end,
                raw_response_sequence=record.sequence,
                raw_record_hash=record.record_hash,
                response_sha256=record.response_sha256,
                row_index=row_index,
                row_content_hash=_hash_payload(row),
                ohlcv_hash=_hash_payload(ohlcv),
                ohlcv=ohlcv,
            )
        )
    return tuple(observations)


def _normalized_identity_payload(bar: object) -> dict[str, object]:
    provenance = bar.provenance
    return {
        "instrument": bar.instrument,
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
        "source_id": bar.source_id,
        "provider_identity": bar.provider_identity,
        "endpoint": bar.endpoint,
        "interval_end": provenance.interval_end.isoformat(),
        "provider_available_at": provenance.provider_available_at.isoformat(),
        "collected_at": provenance.collected_at.isoformat(),
        "provider_event_at": (
            provenance.provider_event_at.isoformat() if provenance.provider_event_at else None
        ),
        "availability_basis": provenance.availability_basis,
        "evidence_class": provenance.evidence_class,
        "source_snapshot_hash": provenance.source_snapshot_hash,
        "raw_record_hash": provenance.raw_record_hash,
        "source_health_state": provenance.source_health_state,
    }


def _load_normalized_bars(path: Path):
    from advisorai.phase4.v3core_cadence import V3CoreBar

    bars: list[V3CoreBar] = []
    for line_number, line in enumerate(_read_lines(path), start=1):
        if not line.strip():
            continue
        try:
            bars.append(V3CoreBar.model_validate_json(line))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise IntegrityAuditError(f"normalized bar is invalid at line {line_number}") from exc
    return tuple(bars)


def _load_cases(path: Path | None) -> tuple[V3CoreForecastCase, ...]:
    if path is None:
        return ()
    cases: list[V3CoreForecastCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(_read_lines(path), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if record.get("schema") != FORWARD_CASE_SCHEMA:
                raise ValueError("case schema is not the forward case schema")
            case_payload = record["case"]
            if sha256_json(case_payload) != str(record["case_hash"]):
                raise ValueError("case hash mismatch")
            case = V3CoreForecastCase.model_validate(case_payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise IntegrityAuditError(f"completed case is invalid at line {line_number}") from exc
        if case.case_id in seen:
            raise IntegrityAuditError("completed case ledger contains a duplicate case")
        seen.add(case.case_id)
        cases.append(case)
    return tuple(cases)


def _load_prediction_entries(paths: Sequence[Path]) -> tuple[ForwardPredictionLedgerEntry, ...]:
    entries: list[ForwardPredictionLedgerEntry] = []
    seen: set[str] = set()
    for path in paths:
        previous: str | None = None
        local_sequence = 0
        for line_number, line in enumerate(_read_lines(path), start=1):
            if not line.strip():
                continue
            try:
                entry = ForwardPredictionLedgerEntry.model_validate_json(line)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise IntegrityAuditError(
                    f"prediction ledger is invalid at {path}:{line_number}"
                ) from exc
            local_sequence += 1
            if entry.sequence != local_sequence or entry.previous_record_hash != previous:
                raise IntegrityAuditError(
                    f"prediction ledger hash chain is not continuous at {path}:{line_number}"
                )
            if entry.prediction.prediction_id in seen:
                raise IntegrityAuditError("prediction ledger contains a duplicate prediction")
            seen.add(entry.prediction.prediction_id)
            entries.append(entry)
            previous = entry.record_hash
    return tuple(entries)


def _load_outcome_links(paths: Sequence[Path]) -> tuple[ForwardPredictionOutcomeLink, ...]:
    links: list[ForwardPredictionOutcomeLink] = []
    identities: set[tuple[str, str]] = set()
    for path in paths:
        previous = None
        local_sequence = 0
        for line_number, line in enumerate(_read_lines(path), start=1):
            if not line.strip():
                continue
            try:
                link = ForwardPredictionOutcomeLink.model_validate_json(line)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise IntegrityAuditError(
                    f"outcome-link ledger is invalid at {path}:{line_number}"
                ) from exc
            local_sequence += 1
            identity = (link.prediction_id, link.outcome_case_id)
            if link.sequence != local_sequence or link.previous_record_hash != previous:
                raise IntegrityAuditError(
                    f"outcome-link hash chain is not continuous at {path}:{line_number}"
                )
            if identity in identities:
                raise IntegrityAuditError("outcome-link ledgers contain a duplicate link")
            identities.add(identity)
            links.append(link)
            previous = link.record_hash
    return tuple(links)


def _version_summary(observations: Sequence[RawKlineObservation]) -> tuple[RawKlineVersion, ...]:
    grouped: dict[str, list[RawKlineObservation]] = {}
    order: list[str] = []
    for observation in observations:
        if observation.ohlcv_hash not in grouped:
            grouped[observation.ohlcv_hash] = []
            order.append(observation.ohlcv_hash)
        grouped[observation.ohlcv_hash].append(observation)
    return tuple(
        RawKlineVersion(
            ohlcv_hash=version_hash,
            ohlcv=grouped[version_hash][0].ohlcv,
            observation_count=len(grouped[version_hash]),
            closed_observation_count=sum(
                observation.closed_at_receipt for observation in grouped[version_hash]
            ),
            first_receipt_at=grouped[version_hash][0].receipt_at,
            last_receipt_at=grouped[version_hash][-1].receipt_at,
            raw_record_hashes=tuple(
                observation.raw_record_hash for observation in grouped[version_hash]
            ),
            response_sha256s=tuple(
                observation.response_sha256 for observation in grouped[version_hash]
            ),
        )
        for version_hash in order
    )


def _changed_fields(observations: Sequence[RawKlineObservation]) -> tuple[str, ...]:
    if not observations:
        return ()
    first = observations[0].ohlcv
    return tuple(
        field
        for field in ("open", "high", "low", "close", "volume")
        if any(observation.ohlcv[field] != first[field] for observation in observations[1:])
    )


def _normalized_observation(
    bar: object, *, normalized_hash_valid: bool
) -> NormalizedBarObservation:
    ohlcv = {
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
    }
    return NormalizedBarObservation(
        instrument=bar.instrument,
        interval_end=bar.interval_end,
        receipt_at=bar.collected_at,
        normalized_record_hash=bar.provenance.normalized_record_hash,
        normalized_hash_valid=normalized_hash_valid,
        raw_record_hash=bar.provenance.raw_record_hash,
        ohlcv_hash=_hash_payload(ohlcv),
        ohlcv=ohlcv,
        source_health_state=bar.provenance.source_health_state,
    )


def _classify_bar(
    observations: Sequence[RawKlineObservation],
    normalized: Sequence[NormalizedBarObservation],
    *,
    minimum_terminal_closed_observations: int,
) -> BarIntegrityRecord:
    if not observations:
        raise ValueError("a bar integrity record requires raw observations")
    first_normalized = normalized[0] if normalized else None
    closed = tuple(observation for observation in observations if observation.closed_at_receipt)
    closed_version_sequence: list[str] = []
    for observation in closed:
        if not closed_version_sequence or closed_version_sequence[-1] != observation.ohlcv_hash:
            closed_version_sequence.append(observation.ohlcv_hash)
    terminal = closed[-1] if closed else None
    terminal_run = 0
    if terminal is not None:
        for observation in reversed(closed):
            if observation.ohlcv_hash != terminal.ohlcv_hash:
                break
            terminal_run += 1
    canonical_hash = first_normalized.ohlcv_hash if first_normalized else None
    normalized_hash_valid = bool(
        first_normalized and all(item.normalized_hash_valid for item in normalized)
    )
    normalized_conflict = len({item.ohlcv_hash for item in normalized}) > 1
    terminal_stable = terminal is not None and terminal_run >= minimum_terminal_closed_observations

    if not normalized or not normalized_hash_valid or normalized_conflict or not terminal_stable:
        classification: BarStabilityClassification = "UNRESOLVED"
        reason = (
            "missing or conflicting normalized canonical record"
            if not normalized or normalized_conflict or not normalized_hash_valid
            else "terminal closed version lacks the required repeated observations"
        )
    elif canonical_hash == terminal.ohlcv_hash and len(closed_version_sequence) == 1:
        classification = "STABLE"
        reason = "all closed observations agree with the canonical normalized content"
    elif canonical_hash == terminal.ohlcv_hash:
        classification = "REVISED_BUT_CANONICAL_FINAL"
        reason = "closed content was revised and the canonical content is terminally stable"
    else:
        classification = "REVISED_CANONICAL_DISAGREES"
        reason = "terminal stable closed content differs from the canonical normalized content"

    return BarIntegrityRecord(
        instrument=observations[0].instrument,
        interval_end=observations[0].interval_end,
        first_raw_observation=observations[0],
        first_normalized_observation=first_normalized,
        raw_observations=tuple(observations),
        raw_versions=_version_summary(observations),
        closed_version_sequence=tuple(closed_version_sequence),
        terminal_closed_observation=terminal,
        final_observed_value=observations[-1],
        terminal_stable_version_hash=terminal.ohlcv_hash if terminal_stable else None,
        terminal_consecutive_observations=terminal_run,
        repeated_identical_observation_count=len(observations)
        - len({observation.ohlcv_hash for observation in observations}),
        revision_count=max(0, len(closed_version_sequence) - 1),
        changed_ohlcv_fields=_changed_fields(observations),
        normalized_record_hash=(
            first_normalized.normalized_record_hash if first_normalized else None
        ),
        normalized_hash_valid=normalized_hash_valid,
        normalized_conflict=normalized_conflict,
        classification=classification,
        classification_reason=reason,
    )


def _audit_bars(
    raw_observations: Iterable[RawKlineObservation],
    normalized_bars: Iterable[object],
    *,
    minimum_terminal_closed_observations: int,
) -> tuple[BarIntegrityRecord, ...]:
    raw_by_key: dict[tuple[str, datetime], list[RawKlineObservation]] = defaultdict(list)
    for observation in raw_observations:
        raw_by_key[(observation.instrument, observation.interval_end)].append(observation)
    normalized_by_key: dict[tuple[str, datetime], list[NormalizedBarObservation]] = defaultdict(
        list
    )
    normalized_hash_failures = 0
    for bar in normalized_bars:
        expected_hash = _hash_payload(_normalized_identity_payload(bar))
        valid = expected_hash == bar.provenance.normalized_record_hash
        normalized_hash_failures += not valid
        normalized_by_key[(bar.instrument, bar.interval_end)].append(
            _normalized_observation(bar, normalized_hash_valid=valid)
        )

    records: list[BarIntegrityRecord] = []
    for key in sorted(raw_by_key):
        records.append(
            _classify_bar(
                raw_by_key[key],
                normalized_by_key.get(key, ()),
                minimum_terminal_closed_observations=minimum_terminal_closed_observations,
            )
        )
    for key in sorted(set(normalized_by_key) - set(raw_by_key)):
        normalized = normalized_by_key[key][0]
        raise IntegrityAuditError(
            "normalized bar has no corresponding raw observation: "
            f"{normalized.instrument}:{normalized.interval_end.isoformat()}"
        )
    return tuple(records)


def _contaminate_cases(
    cases: Sequence[V3CoreForecastCase],
    bar_records: Sequence[BarIntegrityRecord],
) -> tuple[CaseContamination, ...]:
    by_key = {(record.instrument, record.interval_end): record for record in bar_records}
    invalid = {"REVISED_CANONICAL_DISAGREES", "UNRESOLVED"}
    contaminated: list[CaseContamination] = []
    for case in cases:
        segments: list[CaseSegment] = []
        intervals: list[datetime] = []
        reasons: list[str] = []
        classifications: list[InvalidBarClassification] = []
        for segment_name, bars in (("context", case.context_bars), ("outcome", case.future_bars)):
            for bar in bars:
                record = by_key.get((bar.instrument, bar.interval_end))
                if record is None:
                    classification: InvalidBarClassification = "UNRESOLVED"
                    reason = "no raw/normalized audit record for case bar"
                elif record.classification in invalid:
                    classification = record.classification
                    reason = record.classification_reason
                else:
                    continue
                if segment_name not in segments:
                    segments.append(segment_name)  # type: ignore[arg-type]
                if bar.interval_end not in intervals:
                    intervals.append(bar.interval_end)
                if reason not in reasons:
                    reasons.append(reason)
                if classification not in classifications:
                    classifications.append(classification)
        if segments:
            contaminated.append(
                CaseContamination(
                    case_id=case.case_id,
                    instrument=case.instrument,
                    cutoff=case.cutoff,
                    affected_segments=tuple(segments),
                    affected_interval_ends=tuple(intervals),
                    reasons=tuple(reasons),
                    classifications=tuple(classifications),
                )
            )
    return tuple(contaminated)


def _prediction_exclusions(
    entries: Sequence[ForwardPredictionLedgerEntry],
    links: Sequence[ForwardPredictionOutcomeLink],
    contaminated: Sequence[CaseContamination],
) -> tuple[PredictionIntegrityExclusion, ...]:
    contaminated_by_case = {case.case_id: case for case in contaminated}
    links_by_prediction: dict[str, list[str]] = defaultdict(list)
    for link in links:
        links_by_prediction[link.prediction_id].append(link.outcome_case_id)
    exclusions: list[PredictionIntegrityExclusion] = []
    for entry in entries:
        prediction = entry.prediction
        case_ids = list(links_by_prediction.get(prediction.prediction_id, ()))
        if prediction.outcome_case_id and prediction.outcome_case_id not in case_ids:
            case_ids.append(prediction.outcome_case_id)
        for case_id in case_ids:
            contamination = contaminated_by_case.get(case_id)
            if contamination is None:
                continue
            exclusions.append(
                PredictionIntegrityExclusion(
                    prediction_id=prediction.prediction_id,
                    instrument=prediction.instrument,
                    model=prediction.model,
                    cutoff=prediction.cutoff,
                    outcome_case_id=case_id,
                    reasons=contamination.reasons,
                )
            )
    return tuple(exclusions)


def audit_forward_root(
    raw_responses_path: Path,
    normalized_bars_path: Path,
    *,
    completed_cases_path: Path | None = None,
    prediction_ledger_paths: Sequence[Path] = (),
    outcome_link_ledger_paths: Sequence[Path] = (),
    terminal_observed_at: datetime | None = None,
    minimum_terminal_closed_observations: int = DEFAULT_MINIMUM_TERMINAL_CLOSED_OBSERVATIONS,
    minimum_cases_per_symbol: int = DEFAULT_MINIMUM_CASES_PER_SYMBOL,
) -> IntegrityAuditReport:
    """Audit immutable inputs without writing to any input path."""

    if minimum_terminal_closed_observations < 2:
        raise ValueError("terminal stability requires at least two closed observations")
    raw_records = _load_raw_records(raw_responses_path)
    raw_observations = tuple(
        observation for record in raw_records for observation in _decode_raw_observations(record)
    )
    normalized_bars = _load_normalized_bars(normalized_bars_path)
    cases = _load_cases(completed_cases_path)
    prediction_entries = _load_prediction_entries(tuple(prediction_ledger_paths))
    outcome_links = _load_outcome_links(tuple(outcome_link_ledger_paths))
    bar_records = _audit_bars(
        raw_observations,
        normalized_bars,
        minimum_terminal_closed_observations=minimum_terminal_closed_observations,
    )
    contaminated = _contaminate_cases(cases, bar_records)
    exclusions = _prediction_exclusions(prediction_entries, outcome_links, contaminated)
    contaminated_ids = {case.case_id for case in contaminated}
    raw_counts = {
        symbol: sum(case.instrument == symbol for case in cases) for symbol in V3_CORE_SYMBOLS
    }
    eligible_counts = {
        symbol: sum(
            case.instrument == symbol and case.case_id not in contaminated_ids for case in cases
        )
        for symbol in V3_CORE_SYMBOLS
    }
    classification_counts = {name: 0 for name in BarStabilityClassification.__args__}
    for record in bar_records:
        classification_counts[record.classification] += 1
    normalized_hash_failures = sum(
        not record.normalized_hash_valid
        for record in bar_records
        if record.first_normalized_observation
    )
    normalized_input_valid = normalized_hash_failures == 0 and not any(
        record.normalized_conflict for record in bar_records
    )
    observed_times = [observation.receipt_at for observation in raw_observations]
    terminal = _aware(
        terminal_observed_at
        if terminal_observed_at is not None
        else (max(observed_times) if observed_times else datetime.now(UTC)),
        "terminal_observed_at",
    )
    if observed_times and terminal < max(observed_times):
        raise IntegrityAuditError("terminal boundary precedes a raw observation receipt")
    report = IntegrityAuditReport(
        generated_at=datetime.now(UTC),
        terminal_observed_at=terminal,
        minimum_terminal_closed_observations=minimum_terminal_closed_observations,
        minimum_cases_per_symbol=minimum_cases_per_symbol,
        raw_responses_sha256=_sha256_file(raw_responses_path),
        normalized_bars_sha256=_sha256_file(normalized_bars_path),
        completed_cases_sha256=(
            _sha256_file(completed_cases_path) if completed_cases_path is not None else None
        ),
        raw_response_count=len(raw_records),
        raw_observation_count=len(raw_observations),
        normalized_bar_count=len(normalized_bars),
        raw_hash_chain_valid=True,
        normalized_input_valid=normalized_input_valid,
        normalized_hash_validation_failures=normalized_hash_failures,
        completed_case_ledger_valid=True,
        prediction_ledgers_valid=True,
        prediction_timing_valid=all(
            entry.prediction.generated_at <= entry.prediction.cutoff for entry in prediction_entries
        ),
        prediction_link_integrity_valid=all(
            link.prediction_id in {entry.prediction.prediction_id for entry in prediction_entries}
            for link in outcome_links
        ),
        prediction_count=len(prediction_entries),
        classification_counts=classification_counts,
        bar_records=bar_records,
        raw_completed_case_counts=raw_counts,
        integrity_eligible_case_counts=eligible_counts,
        contaminated_cases=contaminated,
        excluded_predictions=exclusions,
        admission_minimum_met=all(
            eligible_counts[symbol] >= minimum_cases_per_symbol for symbol in V3_CORE_SYMBOLS
        ),
    )
    return report


def build_exclusion_overlay(
    report: IntegrityAuditReport, *, report_sha256: str
) -> IntegrityExclusionOverlay:
    """Build the separate case/prediction exclusion view from a report."""

    return IntegrityExclusionOverlay(
        generated_at=report.generated_at,
        audit_report_sha256=report_sha256,
        contaminated_case_ids=tuple(case.case_id for case in report.contaminated_cases),
        contaminated_cases=report.contaminated_cases,
        excluded_predictions=report.excluded_predictions,
        raw_completed_case_counts=report.raw_completed_case_counts,
        integrity_eligible_case_counts=report.integrity_eligible_case_counts,
        admission_minimum_met=report.admission_minimum_met,
    )


__all__ = [
    "BarIntegrityRecord",
    "CaseContamination",
    "DEFAULT_MINIMUM_CASES_PER_SYMBOL",
    "DEFAULT_MINIMUM_TERMINAL_CLOSED_OBSERVATIONS",
    "INTEGRITY_AUDIT_SCHEMA",
    "INTEGRITY_OVERLAY_SCHEMA",
    "IntegrityAuditError",
    "IntegrityAuditReport",
    "IntegrityExclusionOverlay",
    "NormalizedBarObservation",
    "PredictionIntegrityExclusion",
    "RawKlineObservation",
    "RawKlineVersion",
    "STABILITY_RULE_VERSION",
    "audit_forward_root",
    "build_exclusion_overlay",
]
