"""V3-Core source parsers with explicit origin and availability metadata."""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from hashlib import sha256
from numbers import Real
from pathlib import Path
from typing import Protocol
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.contracts import InstrumentIdentity, PointInTimeObservation, SourceGrade


class SourceDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    family: str = Field(min_length=1)
    origin: str = Field(min_length=1)
    grade: SourceGrade
    intended_use: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)

    @field_validator("name", "family", "origin", "intended_use", "parser_version")
    @classmethod
    def require_nonblank_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source metadata cannot be blank")
        return value.strip()


class HttpResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status_code: int
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()
    fetched_at: datetime
    url: str = Field(min_length=1)

    @field_validator("fetched_at")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("HTTP fetched_at must include a timezone")
        return value.astimezone(UTC)

    @field_validator("status_code")
    @classmethod
    def require_http_status(cls, value: int) -> int:
        if not 100 <= value <= 599:
            raise ValueError("HTTP status code must be between 100 and 599")
        return value

    @field_validator("headers")
    @classmethod
    def require_header_tokens(
        cls, value: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[str, str], ...]:
        if any(not key.strip() for key, _ in value):
            raise ValueError("HTTP response header names cannot be blank")
        return tuple((key.strip(), val.strip()) for key, val in value)

    @field_validator("url")
    @classmethod
    def require_url(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("HTTP response URL cannot be blank")
        return value.strip()


class HttpTransport(Protocol):
    def get(self, url: str) -> HttpResponse: ...


class RawHttpRecord(BaseModel):
    """One exact HTTP response retained before any source parser runs.

    Authentication headers are intentionally absent.  The body is the exact
    response bytes, while URL/status/fetch time preserve enough provenance to
    reproduce a parser result without persisting credential material.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: str = Field(min_length=64, max_length=64)
    raw_sha256: str = Field(min_length=64, max_length=64)
    status_code: int
    url: str = Field(min_length=1)
    fetched_at: datetime
    payload_b64: str = Field(min_length=0)

    @field_validator("message_id", "raw_sha256")
    @classmethod
    def require_digest(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("raw HTTP identifiers must be lowercase SHA-256 digests")
        return value

    @field_validator("status_code")
    @classmethod
    def require_status(cls, value: int) -> int:
        if not 100 <= value <= 599:
            raise ValueError("raw HTTP status must be between 100 and 599")
        return value

    @field_validator("fetched_at")
    @classmethod
    def require_aware_fetch_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("raw HTTP fetched_at must include a timezone")
        return value.astimezone(UTC)

    @property
    def payload(self) -> bytes:
        try:
            return base64.b64decode(self.payload_b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("raw HTTP payload is not valid base64") from exc

    @model_validator(mode="after")
    def verify_identity_and_payload(self) -> RawHttpRecord:
        try:
            body = base64.b64decode(self.payload_b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("raw HTTP payload is not valid base64") from exc
        if sha256(body).hexdigest() != self.raw_sha256:
            raise ValueError("raw HTTP payload digest does not match its record")
        identity = json.dumps(
            {
                "raw_sha256": self.raw_sha256,
                "status_code": self.status_code,
                "url": self.url,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if sha256(identity).hexdigest() != self.message_id:
            raise ValueError("raw HTTP record identity does not match its content")
        return self

    @classmethod
    def from_response(cls, response: HttpResponse) -> RawHttpRecord:
        body = bytes(response.body)
        raw_sha256 = sha256(body).hexdigest()
        # The identity excludes fetched_at so retries of the same response are
        # idempotent while the first receipt remains the canonical provenance.
        identity = json.dumps(
            {
                "raw_sha256": raw_sha256,
                "status_code": response.status_code,
                "url": response.url,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return cls(
            message_id=sha256(identity).hexdigest(),
            raw_sha256=raw_sha256,
            status_code=response.status_code,
            url=response.url,
            fetched_at=response.fetched_at,
            payload_b64=base64.b64encode(body).decode("ascii"),
        )


class RawHttpSpool:
    """Crash-safe JSONL spool for raw REST/bootstrap responses."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, RawHttpRecord] = {}
        if self.path.exists():
            for line_number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not line.strip():
                    continue
                try:
                    record = RawHttpRecord.model_validate_json(line)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"raw HTTP spool is corrupted at line {line_number}"
                    ) from exc
                prior = self._records.get(record.message_id)
                if prior is not None and prior != record:
                    raise RuntimeError(
                        "raw HTTP spool reuses a response hash with different content"
                    )
                self._records[record.message_id] = record

    def append(self, response: HttpResponse) -> bool:
        record = RawHttpRecord.from_response(response)
        prior = self._records.get(record.message_id)
        if prior is not None:
            # fetched_at is receipt metadata rather than response identity;
            # retries of identical bytes are idempotent even when the retry
            # occurred later.
            return False
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._records[record.message_id] = record
        return True

    def read(self) -> tuple[RawHttpRecord, ...]:
        return tuple(
            sorted(self._records.values(), key=lambda item: (item.fetched_at, item.message_id))
        )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("source timestamp must include a timezone")
    return value.astimezone(UTC)


def _raw_hash(body: bytes) -> str:
    return sha256(body).hexdigest()


def _record_event_time(record: Mapping[str, object], available_at: datetime) -> datetime:
    """Extract a provider event time without weakening the PIT contract.

    Native REST/bootstrap payloads use several conventions for timestamps.  A
    missing timestamp is allowed and uses receipt time, but a present,
    malformed, timezone-naive, or future timestamp is rejected by the normal
    ``PointInTimeObservation`` validation path.  That distinction keeps schema
    drift and clock skew visible instead of silently relabelling source data.
    """

    raw: object | None = None
    for key in ("timestamp_ms", "ts", "timestamp", "time", "created_at"):
        if key in record and record[key] is not None:
            raw = record[key]
            break
    if raw is None:
        return available_at
    if isinstance(raw, bool):
        raise ValueError("native venue event timestamp cannot be boolean")
    if isinstance(raw, Real):
        numeric = float(raw)
        if not numeric == numeric or numeric in {float("inf"), float("-inf")}:
            raise ValueError("native venue event timestamp must be finite")
        if abs(numeric) >= 100_000_000_000_000:
            numeric /= 1_000_000
        elif abs(numeric) >= 100_000_000_000:
            numeric /= 1_000
        return datetime.fromtimestamp(numeric, tz=UTC)
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            raise ValueError("native venue event timestamp cannot be blank")
        try:
            numeric = float(value)
        except ValueError:
            try:
                return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
            except ValueError as exc:
                raise ValueError("native venue event timestamp is malformed") from exc
        if not numeric == numeric or numeric in {float("inf"), float("-inf")}:
            raise ValueError("native venue event timestamp must be finite")
        if abs(numeric) >= 100_000_000_000_000:
            numeric /= 1_000_000
        elif abs(numeric) >= 100_000_000_000:
            numeric /= 1_000
        return datetime.fromtimestamp(numeric, tz=UTC)
    raise ValueError("native venue event timestamp has an unsupported type")


def _strip_untrusted_markup(text: str) -> str:
    without_active = re.sub(
        r"<\s*(script|style|iframe|object|embed)[^>]*>.*?<\s*/\s*\1\s*>",
        " ",
        text,
        flags=re.I | re.S,
    )
    without_tags = re.sub(r"<[^>]+>", " ", without_active)
    return re.sub(r"\s+", " ", without_tags).strip()


def _untrusted_claim(text: str) -> str:
    """Keep web text as data; never treat embedded instructions as agent commands."""

    clean = _strip_untrusted_markup(text)
    return json.dumps({"text": clean, "untrusted": True}, sort_keys=True)


class NativeVenueCollector:
    def __init__(
        self,
        descriptor: SourceDescriptor,
        transport: HttpTransport | None = None,
        *,
        raw_spool: RawHttpSpool | None = None,
    ) -> None:
        self.descriptor = descriptor
        self.transport = transport
        self.raw_spool = raw_spool

    def fetch(self, url: str, instrument: InstrumentIdentity) -> tuple[PointInTimeObservation, ...]:
        if self.transport is None:
            raise RuntimeError("native collector requires an injected HTTP/WebSocket transport")
        response = self.transport.get(url)
        # Persist exact bytes before status handling or JSON normalization so a
        # malformed/error response remains available for offline diagnosis.
        if self.raw_spool is not None:
            self.raw_spool.append(response)
        if response.status_code != 200:
            raise RuntimeError(f"native source returned HTTP {response.status_code}")
        return self.parse(response.body, instrument=instrument, available_at=response.fetched_at)

    def parse(
        self,
        body: bytes,
        *,
        instrument: InstrumentIdentity,
        available_at: datetime,
    ) -> tuple[PointInTimeObservation, ...]:
        available_at = _aware(available_at)
        payload = json.loads(body)
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, Mapping):
            if "data" in payload:
                records = payload["data"]
            elif "result" in payload:
                records = payload["result"]
            else:
                # A number of native venue bootstrap endpoints return one
                # ticker/trade object rather than a list.  Treat it as one
                # record instead of silently producing an empty observation.
                records = [payload]
        else:
            raise ValueError("native venue payload must be a list or object")
        if not isinstance(records, list):
            raise ValueError("native venue payload must contain a list")
        observations: list[PointInTimeObservation] = []
        for record in records:
            if not isinstance(record, Mapping):
                raise ValueError("native venue record must be an object")
            event_time = _record_event_time(record, available_at)
            value = json.dumps(record, sort_keys=True, separators=(",", ":"))
            observations.append(
                PointInTimeObservation(
                    instrument=instrument,
                    event_time=event_time,
                    effective_time=event_time,
                    source_published_at=event_time,
                    first_available_at=available_at,
                    ingested_at=available_at,
                    source_revision=str(record.get("revision")) if record.get("revision") else None,
                    raw_artifact_hash=_raw_hash(body),
                    parser_version=self.descriptor.parser_version,
                    source_family=self.descriptor.family,
                    origin=self.descriptor.origin,
                    quality_grade=self.descriptor.grade,
                    intended_use=self.descriptor.intended_use,
                    value=value,
                )
            )
        return tuple(observations)

    def parse_market_events(
        self,
        body: bytes | str | Mapping[str, object],
        *,
        instrument_id: str | None = None,
        received_at: datetime | None = None,
        sequence: int | None = None,
    ):
        """Normalize native REST/WebSocket market records for event replay.

        The observation parser above intentionally retains the full source
        record as a point-in-time data artifact.  This companion path creates
        the smaller typed market-event stream consumed by the execution/replay
        boundary, using the same parser for REST bootstrap and WSS messages.
        """

        from advisorai.execution.events import NativeMarketMessageParser

        return NativeMarketMessageParser().parse_many(
            body,
            instrument_id=instrument_id,
            received_at=received_at or datetime.now(UTC),
            sequence=sequence,
        )


class DeribitCollector(NativeVenueCollector):
    """Deribit public derivatives context normalized to the PIT source contract."""

    def parse(
        self,
        body: bytes,
        *,
        instrument: InstrumentIdentity,
        available_at: datetime,
    ) -> tuple[PointInTimeObservation, ...]:
        payload = json.loads(body)
        if isinstance(payload, Mapping) and isinstance(payload.get("result"), Mapping):
            payload = {"data": [payload["result"]]}
        elif isinstance(payload, Mapping) and isinstance(payload.get("result"), list):
            payload = {"data": payload["result"]}
        return super().parse(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
            instrument=instrument,
            available_at=available_at,
        )


class CcxtCollector(NativeVenueCollector):
    """CCXT backfill/cross-venue normalizer; native venue remains execution truth."""

    def parse(
        self,
        body: bytes,
        *,
        instrument: InstrumentIdentity,
        available_at: datetime,
    ) -> tuple[PointInTimeObservation, ...]:
        payload = json.loads(body)
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, Mapping):
            records = payload.get("data", payload)
        else:
            raise ValueError("CCXT payload must be a list or object")
        if not isinstance(records, list):
            records = [records]
        normalized: list[dict[str, object]] = []
        for record in records:
            if not isinstance(record, Mapping):
                raise ValueError("CCXT payload records must be objects")
            normalized_record = dict(record)
            timestamp = record.get("timestamp", record.get("timestamp_ms"))
            if timestamp is not None:
                normalized_record["timestamp_ms"] = int(timestamp)
            normalized.append(normalized_record)
        return super().parse(
            json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode(),
            instrument=instrument,
            available_at=available_at,
        )


class LseCorroborationCollector(NativeVenueCollector):
    """Optional audited LSE corroborator; never execution authority."""

    def parse(
        self,
        body: bytes,
        *,
        instrument: InstrumentIdentity,
        available_at: datetime,
    ) -> tuple[PointInTimeObservation, ...]:
        observations = super().parse(body, instrument=instrument, available_at=available_at)
        return tuple(
            observation.model_copy(update={"intended_use": "optional_corroboration_only"})
            for observation in observations
        )


class PredictionMarketCollector(NativeVenueCollector):
    """Public prediction-market probabilities as contextual evidence only."""

    def parse(
        self,
        body: bytes,
        *,
        instrument: InstrumentIdentity,
        available_at: datetime,
    ) -> tuple[PointInTimeObservation, ...]:
        payload = json.loads(body)
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, Mapping):
            records = payload.get("markets", payload)
        else:
            raise ValueError("prediction-market payload must be a list or object")
        if not isinstance(records, list):
            records = [records]
        normalized: list[dict[str, object]] = []
        for record in records:
            if not isinstance(record, Mapping):
                raise ValueError("prediction-market records must be objects")
            probability = record.get("probability", record.get("p"))
            if probability is None:
                raise ValueError("prediction-market record requires probability")
            numeric = float(probability)
            if not 0 <= numeric <= 1:
                raise ValueError("prediction-market probability must be between zero and one")
            normalized.append({**record, "probability": str(probability)})
        return super().parse(
            json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode(),
            instrument=instrument,
            available_at=available_at,
        )


class RSSCollector:
    def __init__(
        self,
        descriptor: SourceDescriptor,
        transport: HttpTransport | None = None,
        *,
        raw_spool: RawHttpSpool | None = None,
    ) -> None:
        self.descriptor = descriptor
        self.transport = transport
        self.raw_spool = raw_spool

    def fetch(self, url: str, instrument: InstrumentIdentity) -> tuple[PointInTimeObservation, ...]:
        if self.transport is None:
            raise RuntimeError("RSS collector requires an injected HTTP transport")
        response = self.transport.get(url)
        if self.raw_spool is not None:
            self.raw_spool.append(response)
        if response.status_code != 200:
            raise RuntimeError(f"RSS source returned HTTP {response.status_code}")
        return self.parse(response.body, instrument=instrument, available_at=response.fetched_at)

    def parse(
        self,
        body: bytes,
        *,
        instrument: InstrumentIdentity,
        available_at: datetime,
    ) -> tuple[PointInTimeObservation, ...]:
        available_at = _aware(available_at)
        root = ElementTree.fromstring(body)
        observations: list[PointInTimeObservation] = []
        entries = [
            element
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}
        ]
        for item in entries:
            title = self._child_text(item, "title")
            link = self._link_text(item)
            guid = self._child_text(item, "guid") or self._child_text(item, "id") or link or title
            publication = (
                self._child_text(item, "pubDate")
                or self._child_text(item, "published")
                or self._child_text(item, "updated")
            )
            if publication:
                try:
                    published_at = _aware(parsedate_to_datetime(publication))
                except (TypeError, ValueError):
                    published_at = _aware(
                        datetime.fromisoformat(publication.replace("Z", "+00:00"))
                    )
            else:
                published_at = available_at
            value = _untrusted_claim(
                json.dumps(
                    {
                        "guid": guid,
                        "title": title,
                        "link": link,
                        "description": self._child_text(item, "description")
                        or self._child_text(item, "summary")
                        or self._child_text(item, "content"),
                    },
                    sort_keys=True,
                )
            )
            observations.append(
                PointInTimeObservation(
                    instrument=instrument,
                    event_time=published_at,
                    effective_time=published_at,
                    source_published_at=published_at,
                    first_available_at=available_at,
                    ingested_at=available_at,
                    raw_artifact_hash=_raw_hash(body),
                    parser_version=self.descriptor.parser_version,
                    source_family=self.descriptor.family,
                    origin=self.descriptor.origin,
                    quality_grade=self.descriptor.grade,
                    intended_use=self.descriptor.intended_use,
                    value=value,
                )
            )
        return tuple(observations)

    @staticmethod
    def _child_text(element: ElementTree.Element, local_name: str) -> str:
        for child in element:
            if child.tag.rsplit("}", 1)[-1].lower() == local_name.lower():
                return (child.text or "").strip()
        return ""

    @classmethod
    def _link_text(cls, element: ElementTree.Element) -> str:
        for child in element:
            if child.tag.rsplit("}", 1)[-1].lower() != "link":
                continue
            href = (child.attrib.get("href") or "").strip()
            if href:
                return href
            return (child.text or "").strip()
        return ""


class GDELTCollector:
    def __init__(
        self,
        descriptor: SourceDescriptor,
        transport: HttpTransport | None = None,
        *,
        raw_spool: RawHttpSpool | None = None,
    ) -> None:
        self.descriptor = descriptor
        self.transport = transport
        self.raw_spool = raw_spool

    def fetch(self, url: str, instrument: InstrumentIdentity) -> tuple[PointInTimeObservation, ...]:
        if self.transport is None:
            raise RuntimeError("GDELT collector requires an injected HTTP transport")
        response = self.transport.get(url)
        if self.raw_spool is not None:
            self.raw_spool.append(response)
        if response.status_code != 200:
            raise RuntimeError(f"GDELT source returned HTTP {response.status_code}")
        return self.parse(response.body, instrument=instrument, available_at=response.fetched_at)

    def parse(
        self,
        body: bytes,
        *,
        instrument: InstrumentIdentity,
        available_at: datetime,
    ) -> tuple[PointInTimeObservation, ...]:
        available_at = _aware(available_at)
        payload = json.loads(body)
        if not isinstance(payload, Mapping):
            raise ValueError("GDELT payload must be an object")
        docs = payload.get("articles", payload.get("documents", []))
        if not isinstance(docs, list):
            raise ValueError("GDELT payload must contain articles/documents")
        observations: list[PointInTimeObservation] = []
        for document in docs:
            if not isinstance(document, Mapping):
                continue
            seen = document.get("seendate") or document.get("seen_at")
            published_at = available_at
            if isinstance(seen, str):
                compact = seen.replace("Z", "+00:00")
                try:
                    published_at = _aware(datetime.fromisoformat(compact))
                except ValueError:
                    try:
                        published_at = datetime.strptime(seen[:15], "%Y%m%dT%H%M%S").replace(
                            tzinfo=UTC
                        )
                    except ValueError:
                        published_at = available_at
            value = _untrusted_claim(json.dumps(document, sort_keys=True))
            observations.append(
                PointInTimeObservation(
                    instrument=instrument,
                    event_time=published_at,
                    effective_time=published_at,
                    source_published_at=published_at,
                    first_available_at=available_at,
                    ingested_at=available_at,
                    raw_artifact_hash=_raw_hash(body),
                    parser_version=self.descriptor.parser_version,
                    source_family=self.descriptor.family,
                    origin=self.descriptor.origin,
                    quality_grade=self.descriptor.grade,
                    intended_use=self.descriptor.intended_use,
                    value=value,
                )
            )
        return tuple(observations)
