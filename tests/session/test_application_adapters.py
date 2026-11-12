"""Typed adapter composition stays inside the supervised paper owner."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from advisorai.agents import EvidenceCouncil
from advisorai.execution import SimulatedPaperVenueAdapter
from advisorai.execution.account import AccountState
from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.session.application import (
    OwnerApplicationAdapters,
    OwnerApplicationConfig,
    PaperOwnerApplication,
)
from advisorai.session.records import (
    DataGapRecord,
    DataGapResolutionRecord,
    PITSnapshotVerification,
)


class _RecordingDataAdapter:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.source = delegate.source
        self.dataset = delegate.dataset
        self.watermark_key = delegate.watermark_key
        self.snapshot_calls = 0
        self.market_calls = 0
        self.watermark_checks = 0
        self.pit_checks = 0
        self.gap_checks = 0

    def snapshot_at(self, cutoff):
        self.snapshot_calls += 1
        return self.delegate.snapshot_at(cutoff)

    def market_at(self, snapshot):
        self.market_calls += 1
        return self.delegate.market_at(snapshot)

    def watermark_value(self, snapshot):
        return self.delegate.watermark_value(snapshot)

    def verify_current_watermarks(self, ledgers, watermarks):
        self.watermark_checks += 1
        return self.delegate.verify_current_watermarks(ledgers, watermarks)

    def verify_current_pit(self, ledgers, verification):
        self.pit_checks += 1
        return self.delegate.verify_current_pit(ledgers, verification)

    def verify_gap_coverage(self, ledgers, gap, resolution):
        self.gap_checks += 1
        return self.delegate.verify_gap_coverage(ledgers, gap, resolution)


class _RecordingVenueAdapter:
    venue = "paper"

    def __init__(self, delegate: SimulatedPaperVenueAdapter) -> None:
        self.delegate = delegate
        self.submissions = 0

    def submit(self, order):
        self.submissions += 1
        return self.delegate.submit(order)

    def reconcile(self, order):
        return self.delegate.reconcile(order)

    def open_orders(self):
        return self.delegate.open_orders()

    def cancel(self, order):
        return self.delegate.cancel(order)

    def account_snapshot(self):
        return self.delegate.account_snapshot()

    @property
    def fills(self):
        return self.delegate.fills

    def execute_pending(self, *, at=None):
        return self.delegate.execute_pending(at=at)


class _RecordingOutcomeAdapter:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.outcomes = []

    def build_outcome(self, **context):
        outcome = self.delegate.build_outcome(**context)
        self.outcomes.append(outcome)
        return outcome


def _owner(
    tmp_path: Path,
    ledgers: SqliteLedgers,
    *,
    lock_identity: str,
    adapters: OwnerApplicationAdapters | None = None,
    stop_requested: threading.Event | None = None,
) -> PaperOwnerApplication:
    return PaperOwnerApplication(
        ledgers=ledgers,
        state_dir=tmp_path / lock_identity,
        lock_identity=lock_identity,
        code_hash="a" * 64,
        config_hash="b" * 64,
        config=OwnerApplicationConfig(
            scenario="qualification", interval_seconds=0.1, accelerated_clock=True
        ),
        adapters=adapters,
        stop_requested=stop_requested,
    )


def test_injected_data_model_and_paper_venue_run_under_supervised_owner(tmp_path, monkeypatch):
    ledgers = SqliteLedgers(tmp_path / "ledgers.sqlite3")
    fixture = _owner(tmp_path, ledgers, lock_identity="fixture")
    data = _RecordingDataAdapter(fixture.adapters.data)
    venue = _RecordingVenueAdapter(fixture.venue)
    outcome_adapter = _RecordingOutcomeAdapter(fixture.adapters.outcome_adapter)
    model_snapshots = []

    def model_factory(snapshot):
        model_snapshots.append(snapshot.artifact_id)
        return fixture.adapters.decision_model_factory(snapshot)

    adapters = OwnerApplicationAdapters(
        data=data,
        evidence_council=fixture.adapters.evidence_council,
        decision_model_factory=model_factory,
        paper_venue=venue,
        outcome_adapter=outcome_adapter,
        initial_account=AccountState(
            cash=Decimal("100000"),
            marks={"BTC": Decimal("50000")},
            as_of=datetime(1970, 1, 1, tzinfo=UTC),
        ),
    )
    stop_requested = threading.Event()
    monkeypatch.setattr(
        PaperOwnerApplication, "_apply_resource_envelope", staticmethod(lambda: None)
    )
    owner = _owner(
        tmp_path,
        ledgers,
        lock_identity="injected",
        adapters=adapters,
        stop_requested=stop_requested,
    )
    result: dict[str, object] = {}

    def run_owner():
        try:
            result["code"] = owner.run(reason="injected-adapter-acceptance")
        except BaseException as exc:  # surfaced in the test thread below
            result["error"] = exc

    worker = threading.Thread(target=run_owner, name="injected-owner-test")
    worker.start()
    deadline = time.monotonic() + 20
    completed_chain = False
    while time.monotonic() < deadline:
        order_events = ledgers.events(LedgerNamespace.ORDER)
        venue_events = ledgers.events(LedgerNamespace.VENUE)
        model_events = ledgers.events(LedgerNamespace.MODEL)
        mission_events = ledgers.events(LedgerNamespace.MISSION)
        completed_chain = (
            any(event.event_type == "order_reconciled" for event in order_events)
            and any(event.event_type == "paper_fill_recorded" for event in venue_events)
            and any(event.event_type == "paper_scorecard_recorded" for event in model_events)
            and any(event.event_type == "session_checkpoint" for event in mission_events)
        )
        if completed_chain:
            break
        if "error" in result:
            break
        time.sleep(0.05)
    stop_requested.set()
    worker.join(timeout=20)

    assert not worker.is_alive()
    assert "error" not in result, repr(result.get("error"))
    assert result.get("code") == 0
    assert completed_chain
    assert data.snapshot_calls >= 4
    assert data.market_calls >= 1
    assert len(model_snapshots) >= 2
    assert venue.submissions >= 1
    assert venue.fills
    assert outcome_adapter.outcomes
    assert all(item.forecast_value is None for item in outcome_adapter.outcomes)
    assert not owner.owner_record_path.exists()

    cutoff = datetime.fromisoformat(
        next(
            event.payload["checkpoint"]["watermarks"][data.watermark_key]
            for event in reversed(ledgers.events(LedgerNamespace.MISSION))
            if event.event_type == "session_checkpoint"
            and data.watermark_key in event.payload.get("checkpoint", {}).get("watermarks", {})
        )
    )
    snapshot = data.delegate.snapshot_at(cutoff)
    watermark = data.watermark_value(snapshot)
    watermark_checks_before = data.watermark_checks
    assert owner._verify_current_watermarks(ledgers, {data.watermark_key: watermark})
    pit = PITSnapshotVerification(
        session_id=uuid4(),
        snapshot_id=snapshot.artifact_id,
        snapshot_hash=owner._snapshot_hash(snapshot),
        source=data.source,
        cutoff=cutoff,
        first_available_at=cutoff,
        watermarks={data.watermark_key: watermark},
    )
    assert owner._verify_current_pit(ledgers, pit)
    gap = DataGapRecord(
        source=data.source,
        instrument="BTC",
        gap_start=cutoff - timedelta(hours=2),
        gap_end=cutoff,
        last_valid_watermark=(cutoff - timedelta(hours=3)).isoformat(),
    )
    resolution = DataGapResolutionRecord(
        gap_id=gap.artifact_id,
        source=data.source,
        instrument="BTC",
        covered_through=cutoff,
        watermark=watermark,
        evidence_id=uuid4(),
    )
    assert owner._verify_current_gap_coverage(ledgers, gap, resolution)
    assert data.watermark_checks == watermark_checks_before + 1
    assert (data.pit_checks, data.gap_checks) == (1, 1)


def test_owner_composition_rejects_non_paper_venue_identity(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "ledgers.sqlite3")
    fixture = _owner(tmp_path, ledgers, lock_identity="fixture")
    wrong_venue = _RecordingVenueAdapter(fixture.venue)
    wrong_venue.venue = "live"

    with pytest.raises(ValueError, match="paper venue identity"):
        OwnerApplicationAdapters(
            data=fixture.adapters.data,
            evidence_council=EvidenceCouncil({}),
            decision_model_factory=fixture.adapters.decision_model_factory,
            paper_venue=wrong_venue,
            outcome_adapter=fixture.adapters.outcome_adapter,
            initial_account=fixture.account,
        )
