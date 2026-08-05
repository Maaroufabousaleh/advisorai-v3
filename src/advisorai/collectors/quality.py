"""Freshness, gap, origin, revision, and disagreement checks."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.contracts import PointInTimeObservation


class QualityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    severity: str
    detail: str
    observation_ids: tuple[str, ...] = ()

    @field_validator("code", "severity", "detail")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("quality findings require code, severity, and detail")
        normalized = value.strip()
        if not normalized:
            raise ValueError("quality findings cannot be blank")
        return (
            normalized.lower() if normalized.lower() in {"error", "warning", "info"} else normalized
        )

    @field_validator("observation_ids")
    @classmethod
    def require_unique_observations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("quality finding observation IDs cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("quality finding observation IDs must be unique")
        return normalized


class DataQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: str
    as_of: datetime
    observation_count: int = Field(ge=0)
    origins: tuple[str, ...]
    source_families: tuple[str, ...]
    revisions: int = Field(ge=0)
    findings: tuple[QualityFinding, ...]

    @field_validator("dataset")
    @classmethod
    def require_dataset(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("quality report dataset cannot be blank")
        return value.strip()

    @field_validator("origins", "source_families")
    @classmethod
    def normalize_provenance_tokens(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("quality provenance identities must be unique and non-blank")
        return normalized

    @field_validator("as_of")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("quality report cutoff must include a timezone")
        return value.astimezone(UTC)

    @property
    def passed(self) -> bool:
        return not any(item.severity == "error" for item in self.findings)


class QualityDashboard(BaseModel):
    """Deterministic aggregate of per-dataset quality reports at one cutoff."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: datetime
    reports: tuple[DataQualityReport, ...]

    @field_validator("as_of")
    @classmethod
    def require_aware_dashboard_cutoff(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("quality dashboard cutoff must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_reports(self) -> QualityDashboard:
        if not self.reports:
            raise ValueError("quality dashboards require at least one dataset report")
        datasets = [report.dataset for report in self.reports]
        if len(datasets) != len(set(datasets)):
            raise ValueError("quality dashboard datasets must be unique")
        if any(report.as_of > self.as_of for report in self.reports):
            raise ValueError("quality reports cannot be newer than the dashboard cutoff")
        return self

    @property
    def passed(self) -> bool:
        return all(report.passed for report in self.reports)

    @property
    def error_codes(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    finding.code
                    for report in self.reports
                    for finding in report.findings
                    if finding.severity == "error"
                }
            )
        )


class DataQualityMonitor:
    def evaluate(
        self,
        *,
        dataset: str,
        observations: Iterable[PointInTimeObservation],
        as_of: datetime,
        max_age_seconds: int,
        expected_interval_seconds: int | None = None,
    ) -> DataQualityReport:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("quality cutoff must include a timezone")
        if max_age_seconds < 0:
            raise ValueError("max_age_seconds cannot be negative")
        if expected_interval_seconds is not None and expected_interval_seconds <= 0:
            raise ValueError("expected_interval_seconds must be positive")
        as_of = as_of.astimezone(UTC)
        records = tuple(observations)
        findings: list[QualityFinding] = []
        if not records:
            findings.append(
                QualityFinding(
                    code="missing_data",
                    severity="error",
                    detail="no observations were available for the quality cutoff",
                )
            )
        future = [
            item
            for item in records
            if (
                item.first_available_at > as_of
                or item.ingested_at > as_of
                or (item.event_time is not None and item.event_time > as_of)
                or (item.effective_time is not None and item.effective_time > as_of)
            )
        ]
        if future:
            findings.append(
                QualityFinding(
                    code="future_data",
                    severity="error",
                    detail=f"{len(future)} records were unavailable at the quality cutoff",
                    observation_ids=tuple(str(item.artifact_id) for item in future),
                )
            )
        # Freshness is evaluated on the latest admissible observation for each
        # instrument/source family, not every historical row.  Treating every
        # old row as stale would make a healthy append-only time series fail as
        # soon as it accumulated more than one freshness window of history.
        latest: dict[tuple[str, str], PointInTimeObservation] = {}
        for item in records:
            if item.first_available_at > as_of or item.ingested_at > as_of:
                continue
            key = (item.instrument.canonical_id, item.source_family)
            item_time = item.event_time or item.first_available_at
            prior = latest.get(key)
            if prior is None or (prior.event_time or prior.first_available_at) < item_time:
                latest[key] = item
        stale = [
            item
            for item in latest.values()
            if (as_of - (item.event_time or item.first_available_at)).total_seconds()
            > max_age_seconds
        ]
        if stale:
            findings.append(
                QualityFinding(
                    code="stale",
                    severity="error",
                    detail=f"{len(stale)} records exceed freshness budget",
                    observation_ids=tuple(str(item.artifact_id) for item in stale),
                )
            )
        duplicates = len(records) - len({item.artifact_id for item in records})
        if duplicates:
            findings.append(
                QualityFinding(
                    code="duplicate",
                    severity="error",
                    detail=f"{duplicates} duplicate observation IDs",
                )
            )
        if expected_interval_seconds and len(records) > 1:
            ordered = sorted(
                (item for item in records if item.event_time is not None),
                key=lambda item: item.event_time,
            )
            gaps = [
                (right.event_time - left.event_time).total_seconds()
                for left, right in zip(ordered, ordered[1:], strict=False)
                if right.event_time and left.event_time
            ]
            if any(gap > expected_interval_seconds * 2 for gap in gaps):
                findings.append(
                    QualityFinding(
                        code="gap",
                        severity="warning",
                        detail="observation interval exceeds twice expected cadence",
                    )
                )
        # Keep origin disagreement visible whenever multiple source families or
        # origins publish the same instrument/event.  Syndicated copies are
        # still represented in the finding; the evidence graph decides later
        # whether their shared ancestry discounts them.
        if (
            len({item.origin for item in records}) > 1
            or len({item.source_family for item in records}) > 1
        ):
            findings.append(
                QualityFinding(
                    code="origin_disagreement",
                    severity="warning",
                    detail="more than one origin is present; cross-source disagreement must be retained",
                )
            )
        by_event: dict[tuple[str, object], set[str]] = {}
        for item in records:
            key = (item.instrument.canonical_id, item.event_time)
            by_event.setdefault(key, set()).add(item.value)
        disagreements = [key for key, values in by_event.items() if len(values) > 1]
        if disagreements:
            findings.append(
                QualityFinding(
                    code="cross_source_disagreement",
                    severity="warning",
                    detail=f"{len(disagreements)} instrument/event values disagree across sources",
                )
            )
        revisions = sum(item.source_revision is not None for item in records)
        identifiers = {item.artifact_id for item in records}
        dangling_revisions = [
            item
            for item in records
            if item.supersedes_observation_id is not None
            and item.supersedes_observation_id not in identifiers
        ]
        if dangling_revisions:
            findings.append(
                QualityFinding(
                    code="dangling_revision",
                    severity="error",
                    detail=f"{len(dangling_revisions)} revisions reference unavailable observations",
                    observation_ids=tuple(str(item.artifact_id) for item in dangling_revisions),
                )
            )
        revision_links = {
            item.artifact_id: item.supersedes_observation_id
            for item in records
            if item.supersedes_observation_id is not None
        }
        cyclic: list[PointInTimeObservation] = []
        for item in records:
            seen: set[object] = set()
            current = item.artifact_id
            while current in revision_links:
                if current in seen:
                    cyclic.append(item)
                    break
                seen.add(current)
                next_id = revision_links[current]
                if next_id is None:
                    break
                current = next_id
        if cyclic:
            findings.append(
                QualityFinding(
                    code="revision_cycle",
                    severity="error",
                    detail=f"{len(cyclic)} observations participate in a revision cycle",
                    observation_ids=tuple(str(item.artifact_id) for item in cyclic),
                )
            )
        return DataQualityReport(
            dataset=dataset,
            as_of=as_of,
            observation_count=len(records),
            origins=tuple(sorted({item.origin for item in records})),
            source_families=tuple(sorted({item.source_family for item in records})),
            revisions=revisions,
            findings=tuple(findings),
        )

    def dashboard(
        self, *, reports: Iterable[DataQualityReport], as_of: datetime
    ) -> QualityDashboard:
        return QualityDashboard(
            as_of=as_of,
            reports=tuple(sorted(reports, key=lambda report: report.dataset)),
        )
