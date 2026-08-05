"""V3-Core source parsers with explicit origin and availability metadata."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from hashlib import sha256
from typing import Protocol
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("source timestamp must include a timezone")
    return value.astimezone(UTC)


def _raw_hash(body: bytes) -> str:
    return sha256(body).hexdigest()


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
        self, descriptor: SourceDescriptor, transport: HttpTransport | None = None
    ) -> None:
        self.descriptor = descriptor
        self.transport = transport

    def fetch(self, url: str, instrument: InstrumentIdentity) -> tuple[PointInTimeObservation, ...]:
        if self.transport is None:
            raise RuntimeError("native collector requires an injected HTTP/WebSocket transport")
        response = self.transport.get(url)
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
            records = payload.get("data", payload.get("result", []))
        else:
            raise ValueError("native venue payload must be a list or object")
        if not isinstance(records, list):
            raise ValueError("native venue payload must contain a list")
        observations: list[PointInTimeObservation] = []
        for record in records:
            if not isinstance(record, Mapping):
                raise ValueError("native venue record must be an object")
            event_ms = record.get("timestamp_ms", record.get("ts"))
            event_time = (
                datetime.fromtimestamp(int(event_ms) / 1000, tz=UTC)
                if event_ms is not None
                else available_at
            )
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
        self, descriptor: SourceDescriptor, transport: HttpTransport | None = None
    ) -> None:
        self.descriptor = descriptor
        self.transport = transport

    def fetch(self, url: str, instrument: InstrumentIdentity) -> tuple[PointInTimeObservation, ...]:
        if self.transport is None:
            raise RuntimeError("RSS collector requires an injected HTTP transport")
        response = self.transport.get(url)
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
        self, descriptor: SourceDescriptor, transport: HttpTransport | None = None
    ) -> None:
        self.descriptor = descriptor
        self.transport = transport

    def fetch(self, url: str, instrument: InstrumentIdentity) -> tuple[PointInTimeObservation, ...]:
        if self.transport is None:
            raise RuntimeError("GDELT collector requires an injected HTTP transport")
        response = self.transport.get(url)
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
