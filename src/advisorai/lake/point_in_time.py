"""Explicit point-in-time admission checks for snapshots and research reads."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from advisorai.contracts import ArtifactReference, PointInTimeObservation, Snapshot


class PointInTimeViolation(ValueError):
    """Raised when a record would expose information before it was available."""


class SnapshotBuilder:
    """Builds snapshots only from observations/artifacts available at the cutoff."""

    @staticmethod
    def assert_observations_available(
        observations: Iterable[PointInTimeObservation], as_of: datetime
    ) -> tuple[PointInTimeObservation, ...]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("snapshot cutoff must include a timezone")
        as_of = as_of.astimezone(UTC)
        checked = tuple(observations)
        leaked = [
            str(observation.artifact_id)
            for observation in checked
            if (
                observation.first_available_at > as_of
                or observation.ingested_at > as_of
                or (observation.event_time is not None and observation.event_time > as_of)
                or (observation.effective_time is not None and observation.effective_time > as_of)
            )
        ]
        if leaked:
            raise PointInTimeViolation(
                f"{len(leaked)} observations were unavailable at snapshot cutoff: {', '.join(leaked)}"
            )
        return checked

    @staticmethod
    def build(
        *,
        as_of: datetime,
        purpose: str,
        observations: Iterable[PointInTimeObservation],
        artifact_references: Iterable[ArtifactReference] = (),
    ) -> Snapshot:
        checked_observations = SnapshotBuilder.assert_observations_available(observations, as_of)
        references = tuple(artifact_references)
        leaked_references = [
            reference.uri for reference in references if reference.first_available_at > as_of
        ]
        if leaked_references:
            raise PointInTimeViolation(
                "artifacts were unavailable at snapshot cutoff: " + ", ".join(leaked_references)
            )
        return Snapshot(
            as_of=as_of,
            purpose=purpose,
            observation_ids=tuple(item.artifact_id for item in checked_observations),
            artifact_references=references,
        )
