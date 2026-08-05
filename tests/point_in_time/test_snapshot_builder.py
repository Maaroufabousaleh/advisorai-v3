from datetime import timedelta

import pytest

from advisorai.lake import PointInTimeViolation, SnapshotBuilder


def test_snapshot_builder_rejects_leaked_observation(observation, timestamp):
    with pytest.raises(PointInTimeViolation, match="unavailable"):
        SnapshotBuilder.build(
            as_of=timestamp - timedelta(minutes=4),
            purpose="leakage-fixture",
            observations=(observation,),
        )


def test_snapshot_builder_accepts_available_observation(observation, timestamp):
    snapshot = SnapshotBuilder.build(
        as_of=timestamp,
        purpose="paper-research",
        observations=(observation,),
    )
    assert snapshot.observation_ids == (observation.artifact_id,)


def test_snapshot_builder_rejects_observation_ingested_after_cutoff(observation, timestamp):
    late = observation.model_copy(update={"ingested_at": timestamp + timedelta(seconds=1)})
    with pytest.raises(PointInTimeViolation, match="unavailable"):
        SnapshotBuilder.build(as_of=timestamp, purpose="ingestion-leakage", observations=(late,))


def test_snapshot_builder_rejects_future_event_time_even_if_already_ingested(
    observation, timestamp
):
    future_event = observation.model_copy(update={"event_time": timestamp + timedelta(minutes=1)})
    with pytest.raises(PointInTimeViolation, match="unavailable"):
        SnapshotBuilder.build(as_of=timestamp, purpose="future-event", observations=(future_event,))
