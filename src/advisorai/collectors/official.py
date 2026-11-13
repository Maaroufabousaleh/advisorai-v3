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


class BlsCollector(VintagedReleaseCollector):
    """BLS public-API macro adapter; unauthenticated use stays within rate limits."""

    def __init__(self, *, parser_version: str = "bls-v1") -> None:
        super().__init__(
            source_family="official_bls",
            origin="bls",
            parser_version=parser_version,
        )

    def parse(
        self,
        body: bytes,
        *,
        instrument: InstrumentIdentity,
        ingested_at: datetime,
    ) -> tuple[PointInTimeObservation, ...]:
        payload = json.loads(body)
        if not isinstance(payload, Mapping):
            raise ValueError("BLS payload must be an object")
        results = payload.get("Results")
        if not isinstance(results, Mapping):
            raise ValueError("BLS payload must contain a Results object")
        series = results.get("series")
        if not isinstance(series, list) or not series:
            raise ValueError("BLS payload must contain a non-empty series list")
        releases: list[dict[str, object]] = []
        for entry in series:
            if not isinstance(entry, Mapping):
                continue
            series_id = str(entry.get("seriesID", "")).strip()
            for point in entry.get("data", []):
                if not isinstance(point, Mapping):
                    continue
                value = str(point.get("value", "")).strip()
                if not value or value == "-":
                    continue
                try:
                    published = self._bls_period_start(
                        str(point.get("year", "")), str(point.get("period", ""))
                    )
                except ValueError:
                    continue
                releases.append(
                    {
                        "series_id": series_id,
                        "published_at": published,
                        "first_available_at": published,
                        "effective_at": published,
                        "value": value,
                        "period": str(point.get("period", "")),
                        "period_name": str(point.get("periodName", "")),
                        "footnotes": point.get("footnotes", []),
                    }
                )
        if not releases:
            raise ValueError("BLS payload contains no usable observations")
        return super().parse(
            json.dumps({"releases": releases}).encode(),
            instrument=instrument,
            ingested_at=ingested_at,
        )

    @staticmethod
    def _bls_period_start(year: str, period: str) -> str:
        year = year.strip()
        period = period.strip().upper()
        if not year.isdigit() or not period.startswith("M"):
            raise ValueError(f"BLS period is not a monthly vintage: {year!r} {period!r}")
        try:
            month = int(period[1:])
        except ValueError as exc:
            raise ValueError(f"BLS period is not a monthly vintage: {period!r}") from exc
        if not 1 <= month <= 12:
            raise ValueError(f"BLS month out of range: {period!r}")
        return f"{int(year):04d}-{month:02d}-01T00:00:00+00:00"


class BeaCollector(VintagedReleaseCollector):
    """BEA data-API macro adapter; UserID travels as a credential reference only.

    BEA wraps rows in ``{"BEAAPI": {"Results": ...}}``. Each observation keeps
    its own vintage time so revised series never leak later values backwards.
    """

    def __init__(self, *, parser_version: str = "bea-v1") -> None:
        super().__init__(
            source_family="official_bea",
            origin="bea",
            parser_version=parser_version,
        )

    def parse(
        self,
        body: bytes,
        *,
        instrument: InstrumentIdentity,
        ingested_at: datetime,
    ) -> tuple[PointInTimeObservation, ...]:
        payload = json.loads(body)
        if not isinstance(payload, Mapping):
            raise ValueError("BEA payload must be an object")
        api = payload.get("BEAAPI", payload)
        if not isinstance(api, Mapping):
            raise ValueError("BEA payload must contain a BEAAPI object")
        results = api.get("Results", api)
        if not isinstance(results, Mapping):
            raise ValueError("BEA payload must contain a Results object")
        rows = results.get("Data", results.get("data", []))
        if not isinstance(rows, list) or not rows:
            raise ValueError("BEA payload must contain a non-empty Data list")
        releases: list[dict[str, object]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            try:
                published = self._normalize_bea_vintage(
                    str(row.get("ReleaseDate", "")).strip(),
                    str(row.get("TimePeriod", "")).strip(),
                )
            except ValueError:
                continue
            releases.append(
                {
                    "series": str(row.get("SeriesCode", row.get("series", ""))),
                    "published_at": published,
                    "first_available_at": published,
                    "effective_at": published,
                    "value": str(row.get("DataValue", row.get("value", ""))),
                    "time_period": str(row.get("TimePeriod", "")),
                    "unit": str(row.get("UNIT_MULT", row.get("unit", ""))),
                }
            )
        if not releases:
            raise ValueError("BEA payload contains no usable observations")
        return super().parse(
            json.dumps({"releases": releases}).encode(),
            instrument=instrument,
            ingested_at=ingested_at,
        )

    @staticmethod
    def _normalize_bea_vintage(release_date: str, time_period: str) -> str:
        for candidate in (release_date, time_period):
            candidate = candidate.strip()
            if not candidate:
                continue
            normalized = candidate.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                parsed = None
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return parsed.astimezone(UTC).isoformat()
            quarter = candidate.upper()
            if (
                len(quarter) == 6
                and quarter[:4].isdigit()
                and quarter[4] == "Q"
                and quarter[5] in "1234"
            ):
                month = (int(quarter[5]) - 1) * 3 + 1
                return f"{quarter[:4]}-{month:02d}-01T00:00:00+00:00"
            if len(quarter) == 4 and quarter.isdigit():
                return f"{quarter}-01-01T00:00:00+00:00"
        raise ValueError(f"BEA row has no usable vintage: {release_date!r}")


class EiaCollector(VintagedReleaseCollector):
    """EIA v2 macro/energy adapter; api_key travels as a credential reference only.

    EIA v2 wraps rows in ``{"response": {"data": [...]}}`` with an explicit
    ``period`` per row. Each row keeps its own vintage time.
    """

    def __init__(self, *, parser_version: str = "eia-v1") -> None:
        super().__init__(
            source_family="official_eia",
            origin="eia",
            parser_version=parser_version,
        )

    def parse(
        self,
        body: bytes,
        *,
        instrument: InstrumentIdentity,
        ingested_at: datetime,
    ) -> tuple[PointInTimeObservation, ...]:
        payload = json.loads(body)
        if not isinstance(payload, Mapping):
            raise ValueError("EIA payload must be an object")
        response = payload.get("response", payload)
        if not isinstance(response, Mapping):
            raise ValueError("EIA payload must contain a response object")
        rows = response.get("data", [])
        if not isinstance(rows, list) or not rows:
            raise ValueError("EIA payload must contain a non-empty data list")
        releases: list[dict[str, object]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            period = str(row.get("period", "")).strip()
            value = str(row.get("value", "")).strip()
            if not period or not value or value == "-":
                continue
            try:
                vintage = self._normalize_period(period)
            except ValueError:
                continue
            releases.append(
                {
                    "series": str(row.get("series", row.get("series-description", ""))),
                    "published_at": vintage,
                    "first_available_at": vintage,
                    "effective_at": vintage,
                    "value": value,
                    "period": period,
                    "units": str(row.get("units", "")),
                }
            )
        if not releases:
            raise ValueError("EIA payload contains no usable observations")
        return super().parse(
            json.dumps({"releases": releases}).encode(),
            instrument=instrument,
            ingested_at=ingested_at,
        )

    @staticmethod
    def _normalize_period(period: str) -> str:
        period = period.strip()
        if len(period) == 7 and period[4] == "-" and period[:4].isdigit():
            return f"{period}-01T00:00:00+00:00"
        if len(period) == 10 and period[4] == "-" and period[7] == "-":
            return f"{period}T00:00:00+00:00"
        if len(period) == 4 and period.isdigit():
            return f"{period}-01-01T00:00:00+00:00"
        raise ValueError(f"EIA period is not a usable vintage: {period!r}")
