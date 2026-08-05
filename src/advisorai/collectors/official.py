"""Point-in-time official filing/macro release parsers for Phase 9."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256

from advisorai.contracts import InstrumentIdentity, PointInTimeObservation, SourceGrade


class VintagedReleaseCollector:
    def __init__(self, *, source_family: str, origin: str, parser_version: str) -> None:
        if not source_family.strip() or not origin.strip() or not parser_version.strip():
            raise ValueError("official collector metadata cannot be blank")
        self.source_family = source_family.strip()
        self.origin = origin.strip()
        self.parser_version = parser_version.strip()

    def parse(
        self,
        body: bytes,
        *,
        instrument: InstrumentIdentity,
        ingested_at: datetime,
    ) -> tuple[PointInTimeObservation, ...]:
        if ingested_at.tzinfo is None or ingested_at.utcoffset() is None:
            raise ValueError("ingested_at must include a timezone")
        ingested_at = ingested_at.astimezone(UTC)
        payload = json.loads(body)
        if not isinstance(payload, Mapping):
            raise ValueError("official release payload must be an object")
        records = payload.get("releases", payload.get("facts", []))
        if not isinstance(records, list):
            raise ValueError("official release payload must contain a releases/facts list")
        result: list[PointInTimeObservation] = []
        for record in records:
            if not isinstance(record, Mapping):
                continue
            if "published_at" not in record:
                raise ValueError("official release records require published_at")
            published = self._parse_aware(record["published_at"], "published_at")
            first_available = self._parse_aware(
                record.get("first_available_at", record["published_at"]),
                "first_available_at",
            )
            effective = self._parse_aware(
                record.get("effective_at", record["published_at"]), "effective_at"
            )
            result.append(
                PointInTimeObservation(
                    instrument=instrument,
                    event_time=published,
                    effective_time=effective,
                    source_published_at=published,
                    first_available_at=first_available,
                    ingested_at=ingested_at,
                    source_revision=str(record.get("vintage")) if record.get("vintage") else None,
                    raw_artifact_hash=sha256(body).hexdigest(),
                    parser_version=self.parser_version,
                    source_family=self.source_family,
                    origin=self.origin,
                    quality_grade=SourceGrade.RESEARCH,
                    intended_use="point_in_time_macro_or_fundamental",
                    value=json.dumps(record, sort_keys=True, separators=(",", ":")),
                )
            )
        return tuple(result)

    @staticmethod
    def _parse_aware(value: object, field: str) -> datetime:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"{field} must include a timezone")
        return parsed.astimezone(UTC)


class SecEdgarCollector(VintagedReleaseCollector):
    """SEC facts/filings adapter; revisions remain point-in-time records."""

    def __init__(self, *, parser_version: str = "sec-edgar-v1") -> None:
        super().__init__(
            source_family="official_sec_edgar",
            origin="sec_edgar",
            parser_version=parser_version,
        )


class AlfredCollector(VintagedReleaseCollector):
    """ALFRED vintaged macro release adapter; no revised-value backfill leakage."""

    def __init__(self, *, parser_version: str = "alfred-v1") -> None:
        super().__init__(
            source_family="official_alfred",
            origin="alfred",
            parser_version=parser_version,
        )
