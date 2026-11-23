"""Owner-level stale-data fail-closed: the integrated release abstains.

The full owner stack (supervisor + runtime + service + RiskKernel + OMS +
paper venue + reconciliation) must abstain with zero orders when the data
adapter serves a stale snapshot — the P10 matrix's stale-source cell,
through the integrated app rather than a bare runtime.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from decimal import Decimal

from advisorai.execution.account import AccountState
from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.session.application import OwnerApplicationAdapters, PaperOwnerApplication
from tests.session.test_application_adapters import _owner, _RecordingVenueAdapter


class _StaleDataAdapter:
    """Serve the fixture snapshot with a stale quality flag, nothing else changed."""

    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.source = delegate.source
        self.dataset = delegate.dataset
        self.watermark_key = delegate.watermark_key

    def snapshot_at(self, cutoff):
        snapshot = self.delegate.snapshot_at(cutoff)
        return snapshot.model_copy(update={"data_quality_state": "stale"})

    def market_at(self, snapshot):
        return self.delegate.market_at(snapshot)

    def watermark_value(self, snapshot):
        return self.delegate.watermark_value(snapshot)

    def verify_current_watermarks(self, ledgers, watermarks):
        return self.delegate.verify_current_watermarks(ledgers, watermarks)

    def verify_current_pit(self, ledgers, verification):
        return self.delegate.verify_current_pit(ledgers, verification)

    def verify_gap_coverage(self, ledgers, gap, resolution):
        return self.delegate.verify_gap_coverage(ledgers, gap, resolution)


def test_stale_snapshot_abstains_with_zero_orders_under_supervised_owner(tmp_path, monkeypatch):
    ledgers = SqliteLedgers(tmp_path / "ledgers.sqlite3")
    fixture = _owner(tmp_path, ledgers, lock_identity="fixture")
    data = _StaleDataAdapter(fixture.adapters.data)
    venue = _RecordingVenueAdapter(fixture.venue)
    adapters = OwnerApplicationAdapters(
        data=data,
        evidence_council=fixture.adapters.evidence_council,
        decision_model_factory=fixture.adapters.decision_model_factory,
        paper_venue=venue,
        outcome_adapter=fixture.adapters.outcome_adapter,
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
        tmp_path, ledgers, lock_identity="stale", adapters=adapters, stop_requested=stop_requested
    )
    result: dict[str, object] = {}

    def run_owner():
        try:
            result["code"] = owner.run(reason="stale-data-acceptance")
        except BaseException as exc:  # surfaced in the test thread below
            result["error"] = exc

    worker = threading.Thread(target=run_owner, name="stale-owner-test")
    worker.start()
    deadline = time.monotonic() + 20
    stale_cycle = None
    while time.monotonic() < deadline:
        for event in ledgers.events(LedgerNamespace.MISSION):
            if event.event_type != "paper_runtime_cycle":
                continue
            cycle = event.payload.get("cycle", {})
            if cycle.get("stage") == "abstained" and any(
                str(reason).startswith("data_quality:") for reason in cycle.get("reasons", ())
            ):
                stale_cycle = cycle
                break
        if stale_cycle is not None or "error" in result:
            break
        time.sleep(0.05)
    stop_requested.set()
    worker.join(timeout=20)

    assert not worker.is_alive()
    assert "error" not in result, repr(result.get("error"))
    assert result.get("code") == 0
    assert stale_cycle is not None, "no stale-data abstention cycle recorded"
    assert stale_cycle["snapshot_id"] is not None
    assert stale_cycle["order_ids"] == []
    assert stale_cycle["risk_decision_id"] is None
    # Nothing reached the OMS or the venue: the only ORDER-namespace evidence
    # is empty reconciliations (one per abstained cycle), never an order.
    order_events = list(ledgers.events(LedgerNamespace.ORDER))
    assert order_events, "expected per-cycle reconciliation evidence"
    assert all(event.event_type == "reconciliation_recorded" for event in order_events)
    assert all(event.payload["artifact"]["order_ids"] == [] for event in order_events)
    assert venue.submissions == 0
