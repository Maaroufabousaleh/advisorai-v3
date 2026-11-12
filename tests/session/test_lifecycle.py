"""Session lifecycle: lock, start classification, leases, clean stop/restart."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers
from advisorai.session import (
    DataGapRecord,
    SessionOutcome,
    SessionRecoveryChecks,
    SessionStartRecord,
    SessionStopChecks,
    SessionSupervisor,
    WorkerRegistry,
)


def _supervisor(tmp_path: Path, name: str = "ledgers.sqlite3") -> SessionSupervisor:
    return SessionSupervisor(
        SqliteLedgers(tmp_path / name),
        state_dir=tmp_path / "state",
        lock_identity="owner-workstation",
        code_hash="c" * 64,
        config_hash="d" * 64,
    )


def test_first_start_records_normal_recovery(tmp_path):
    supervisor = _supervisor(tmp_path)
    start, recovery = supervisor.start(reason="owner start")
    assert start.recovery_classification == "first_start"
    assert recovery.recovery_mode.value == "normal"
    end = supervisor.stop()
    assert end.outcome is SessionOutcome.CLEAN
    assert end.leases_released is True


def test_clean_stop_then_restart_is_clean(tmp_path):
    first = _supervisor(tmp_path)
    first.start(reason="first")
    first.checkpoint()
    first.stop()
    second = SessionSupervisor(
        SqliteLedgers(tmp_path / "ledgers.sqlite3"),
        state_dir=tmp_path / "state",
        lock_identity="owner-workstation",
        code_hash="c" * 64,
        config_hash="d" * 64,
    )
    start, recovery = second.start(reason="restart")
    assert start.recovery_classification == "clean_start"
    # A restart without fresh verification artifacts enters recovery.
    assert recovery.recovery_mode.value in {"normal", "recovery"}
    second.checkpoint()
    second.stop()


def test_unclean_stop_triggers_recovery_mode(tmp_path):
    first = _supervisor(tmp_path)
    first.start(reason="first")
    first.stop(outcome=SessionOutcome.UNCLEAN, unfinished_work=("cycle-1",))
    second = SessionSupervisor(
        SqliteLedgers(tmp_path / "ledgers.sqlite3"),
        state_dir=tmp_path / "state",
        lock_identity="owner-workstation",
        code_hash="c" * 64,
        config_hash="d" * 64,
    )
    _, recovery = second.start(reason="restart after crash")
    assert recovery.recovery_mode.value == "recovery"
    second.checkpoint()
    second.stop()


def test_unmatched_start_is_crash_despite_older_clean_end(tmp_path):
    """An unmatched session_started after a clean end is a crash, not clean."""
    ledgers = SqliteLedgers(tmp_path / "ledgers.sqlite3")
    first = SessionSupervisor(
        ledgers,
        state_dir=tmp_path / "state",
        lock_identity="owner-workstation",
        code_hash="c" * 64,
        config_hash="d" * 64,
    )
    first.start(reason="first")
    first.checkpoint()
    first.stop()
    # Simulate a crashed session: a start event with no matching end.
    from uuid import uuid4

    from advisorai.session import SessionStartRecord

    orphan = SessionStartRecord(
        session_id=uuid4(),
        lock_identity="owner-workstation",
        code_hash="c" * 64,
        config_hash="d" * 64,
        start_reason="crashed session",
        recovery_classification="clean_start",
    )
    ledgers.append(
        LedgerEvent(
            namespace=LedgerNamespace.MISSION,
            event_type="session_started",
            idempotency_key=f"session-started:{orphan.session_id}",
            payload={"start": orphan.model_dump(mode="json", round_trip=True)},
        )
    )
    second = SessionSupervisor(
        SqliteLedgers(tmp_path / "ledgers.sqlite3"),
        state_dir=tmp_path / "state",
        lock_identity="owner-workstation",
        code_hash="c" * 64,
        config_hash="d" * 64,
    )
    start, recovery = second.start(reason="restart after orphan")
    assert start.recovery_classification == "crash_recovery"
    assert recovery.recovery_mode.value == "abstain"
    second.checkpoint()
    second.stop()


def test_second_live_supervisor_is_rejected(tmp_path):
    first = _supervisor(tmp_path)
    first.start(reason="first")
    second = _supervisor(tmp_path)
    with pytest.raises(RuntimeError, match="another AdvisorAI session"):
        second.start(reason="second")
    first.stop()


def test_leases_are_released_on_stop(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.start(reason="leases")
    lease = supervisor.acquire_lease(worker_identity="chronos", resource_envelope="gpu")
    released = supervisor.release_lease(lease.lease_id)
    assert released.cleanup_outcome == "released"
    supervisor.acquire_lease(worker_identity="ttm", resource_envelope="gpu")
    end = supervisor.stop()
    assert end.leases_released is True


def test_live_worker_blocks_clean_stop(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.start(reason="leak check")
    registry = WorkerRegistry()
    registry.register("chronos-worker")
    end = supervisor.stop(worker_registry=registry)
    assert end.outcome is SessionOutcome.UNCLEAN
    # A still-live worker is a leak: leases_released must stay False, honestly.
    assert end.leases_released is False
    assert any("worker_still_live" in item for item in end.unfinished_work)
    replacement = _supervisor(tmp_path)
    with pytest.raises(RuntimeError, match="another AdvisorAI session"):
        replacement.start(reason="must wait for leaked worker")


def test_checkpoint_hash_is_verified_not_placeholder(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.start(reason="work")
    checkpoint = supervisor.checkpoint(pending_work=("cycle-2",))
    assert checkpoint.checkpoint_hash != "0" * 64
    assert len(checkpoint.checkpoint_hash) == 64
    with pytest.raises(ValueError, match="checkpoint hash does not match"):
        supervisor.checkpoint(checkpoint_hash="f" * 64)
    supervisor.stop()


def test_unbackfilled_gap_blocks_normal_recovery(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.start(reason="work")
    supervisor.record_data_gap(
        DataGapRecord(
            source="binance",
            instrument="BTCUSDT",
            gap_start=datetime(2026, 8, 1, tzinfo=UTC),
            gap_end=datetime(2026, 8, 1, 1, tzinfo=UTC),
            last_valid_watermark="wm-1",
        )
    )
    supervisor.checkpoint()
    supervisor.stop()
    second = _supervisor(tmp_path)
    _, recovery = second.start(reason="restart with gap")
    # Failed mandatory checks on a clean history fail closed to ABSTAIN.
    assert recovery.recovery_mode.value == "abstain"
    assert any("source_freshness" in item for item in recovery.decisions)
    second.checkpoint()
    second.stop()


def test_checkpoint_and_data_gap_are_recorded(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.start(reason="work")
    checkpoint = supervisor.checkpoint(pending_work=("cycle-2",))
    assert checkpoint.pending_work == ("cycle-2",)
    gap = supervisor.record_data_gap(
        DataGapRecord(
            source="binance",
            instrument="BTCUSDT",
            gap_start=datetime(2026, 8, 1, tzinfo=UTC),
            gap_end=datetime(2026, 8, 1, 1, tzinfo=UTC),
            last_valid_watermark="wm-1",
        )
    )
    assert gap.backfilled is False
    supervisor.stop()


def test_resolved_gap_uses_verified_coverage_instead_of_permanent_rejection(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.start(reason="gap recovery")
    gap = supervisor.record_data_gap(
        DataGapRecord(
            source="local-fixture",
            instrument="BTC",
            gap_start=datetime(2026, 8, 1, tzinfo=UTC),
            gap_end=datetime(2026, 8, 1, 1, tzinfo=UTC),
            last_valid_watermark="wm-before-gap",
        )
    )
    checks = SessionRecoveryChecks()
    assert not checks.verify_source_freshness(supervisor.ledgers)
    resolution = supervisor.resolve_data_gap(
        gap.artifact_id,
        covered_through=gap.gap_end,
        watermark="wm-after-gap",
        evidence_id=uuid4(),
    )
    assert resolution.gap_id == gap.artifact_id
    # A durable resolution record alone is not proof that the permitted source
    # currently contains the recovered interval.
    assert not checks.verify_source_freshness(supervisor.ledgers)
    verified = SessionRecoveryChecks(
        current_gap_coverage_verifier=lambda _ledgers, current_gap, current_resolution: (
            current_gap.artifact_id == current_resolution.gap_id
            and current_resolution.covered_through >= current_gap.gap_end
        )
    )
    assert verified.verify_source_freshness(supervisor.ledgers)
    supervisor.stop()


def test_watermark_verification_requires_a_valid_durable_checkpoint(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "watermark.sqlite3")
    checks = SessionStopChecks()
    assert not checks.verify_watermark_persistence(ledgers)
    supervisor = _supervisor(tmp_path, "watermark.sqlite3")
    supervisor.start(reason="watermark check")
    assert not checks.verify_watermark_persistence(ledgers)
    supervisor.checkpoint()
    assert checks.verify_watermark_persistence(ledgers)
    observed_at = datetime(2026, 8, 1, tzinfo=UTC)
    supervisor.record_watermark(
        source="fixture", dataset="BTC", value=observed_at.isoformat(), observed_at=observed_at
    )
    supervisor.checkpoint(watermarks={"fixture/BTC": observed_at.isoformat()})
    assert not checks.verify_watermark_persistence(ledgers)
    current = SessionStopChecks(
        current_watermark_verifier=lambda _ledgers, values: (
            values == {"fixture/BTC": observed_at.isoformat()}
        )
    )
    assert current.verify_watermark_persistence(ledgers)
    recovery_without_source = SessionRecoveryChecks()
    recovery_with_source = SessionRecoveryChecks(
        current_watermark_verifier=lambda _ledgers, values: (
            values == {"fixture/BTC": observed_at.isoformat()}
        )
    )
    assert not recovery_without_source.verify_source_freshness(ledgers)
    assert recovery_with_source.verify_source_freshness(ledgers)
    supervisor.stop()


def test_current_pit_recovery_requires_an_authoritative_snapshot_verifier(tmp_path):
    supervisor = _supervisor(tmp_path, "pit-current.sqlite3")
    start, _ = supervisor.start(reason="verified source cycle")
    snapshot_id = uuid4()
    cutoff = datetime(2026, 8, 1, tzinfo=UTC)
    watermark = cutoff.isoformat()
    supervisor.record_watermark(
        source="fixture", dataset="BTC", value=watermark, observed_at=cutoff
    )
    verification = supervisor.record_pit_snapshot(
        snapshot_id=snapshot_id,
        snapshot_hash="a" * 64,
        source="fixture",
        cutoff=cutoff,
        first_available_at=cutoff,
        watermarks={"fixture/BTC": watermark},
    )
    supervisor.checkpoint(artifact_ids=(snapshot_id,), watermarks={"fixture/BTC": watermark})

    assert not SessionRecoveryChecks().verify_pit_rebuild(supervisor.ledgers)
    assert SessionRecoveryChecks(
        current_pit_verifier=lambda _ledgers, current: (
            current.session_id == start.session_id
            and current.snapshot_id == verification.snapshot_id
            and current.snapshot_hash == verification.snapshot_hash
        )
    ).verify_pit_rebuild(supervisor.ledgers)
    supervisor.stop(
        stop_checks=SessionStopChecks(
            current_watermark_verifier=lambda _ledgers, values: values == {"fixture/BTC": watermark}
        )
    )


def test_old_session_checkpoint_cannot_verify_a_new_sessions_pit_rebuild(tmp_path):
    supervisor = _supervisor(tmp_path, "pit-rebuild.sqlite3")
    first, _ = supervisor.start(reason="first session")
    snapshot_id = uuid4()
    cutoff = datetime(2026, 8, 1, tzinfo=UTC)
    watermark = cutoff.isoformat()
    supervisor.record_watermark(
        source="fixture", dataset="BTC", value=watermark, observed_at=cutoff
    )
    supervisor.record_pit_snapshot(
        snapshot_id=snapshot_id,
        snapshot_hash="a" * 64,
        source="fixture",
        cutoff=cutoff,
        first_available_at=cutoff,
        watermarks={"fixture/BTC": watermark},
    )
    supervisor.checkpoint(artifact_ids=(snapshot_id,), watermarks={"fixture/BTC": watermark})
    supervisor.stop()

    second_start = SessionStartRecord(
        session_id=uuid4(),
        lock_identity="owner-workstation",
        code_hash="c" * 64,
        config_hash="d" * 64,
        prior_end_record_id=None,
        start_reason="new process with no current checkpoint",
    )
    supervisor.ledgers.append(
        LedgerEvent(
            namespace=LedgerNamespace.MISSION,
            event_type="session_started",
            idempotency_key=f"session-started:{second_start.session_id}",
            payload={"start": second_start.model_dump(mode="json", round_trip=True)},
        )
    )
    assert first.session_id != second_start.session_id
    assert not SessionRecoveryChecks().verify_pit_rebuild(supervisor.ledgers)


def test_latest_order_state_replaces_historical_ambiguous_and_routed_events(tmp_path):
    from advisorai.contracts import AssetClass, InstrumentIdentity, Order, OrderState

    ledgers = SqliteLedgers(tmp_path / "order-projection.sqlite3")
    order = Order(
        parent_intent_id=uuid4(),
        execution_plan_id=uuid4(),
        instrument=InstrumentIdentity(
            canonical_id="BTC", asset_class=AssetClass.CRYPTO, venue="paper"
        ),
        side="buy",
        quantity=Decimal("1"),
        order_type="limit",
        price=Decimal("50000"),
        time_in_force="gtc",
        idempotency_key="order-projection-1",
        state=OrderState.ROUTED,
    )
    for event_type, item in (
        ("order_routed", order),
        ("order_ack_ambiguous", order),
        ("order_acknowledged", order.model_copy(update={"state": OrderState.ACKNOWLEDGED})),
        ("order_filled", order.model_copy(update={"state": OrderState.FILLED})),
        ("order_reconciled", order.model_copy(update={"state": OrderState.RECONCILED})),
    ):
        ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.ORDER,
                event_type=event_type,
                idempotency_key=f"projection:{event_type}",
                payload={
                    "artifact_id": str(order.artifact_id),
                    "artifact": item.model_dump(mode="json", round_trip=True),
                },
            )
        )
    current_state_verified = []
    recovery = SessionRecoveryChecks(
        current_order_account_verifier=lambda _ledgers: current_state_verified.append(True) or True
    )
    stop = SessionStopChecks(
        current_order_account_verifier=lambda _ledgers: current_state_verified.append(True) or True
    )
    assert recovery.verify_account_order_reconciliation(ledgers)
    assert stop.verify_order_reconciliation(ledgers)
    assert len(current_state_verified) == 2


def test_dead_pid_lease_is_refused(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.start(reason="phantom lease check")
    with pytest.raises(RuntimeError, match="not alive"):
        supervisor.acquire_lease(worker_identity="pid:99999999", resource_envelope="gpu")
    import os

    live = supervisor.acquire_lease(worker_identity=f"pid:{os.getpid()}", resource_envelope="test")
    assert live.worker_identity == f"pid:{os.getpid()}"
    supervisor.stop()


def test_crash_recovery_releases_dead_owner_thread_lease(tmp_path):
    crashed = _supervisor(tmp_path, "crashed-worker.sqlite3")
    prior_start, _ = crashed.start(reason="owner crashed with scheduler active")
    prior_lease = crashed.acquire_lease(
        worker_identity="thread:paper-scheduler", resource_envelope="paper-only"
    )
    crashed.checkpoint(pending_work=("paper_scheduler_active",))
    # Simulate process death: the durable start and lease remain, while the
    # OS lock is released and no session end is written.
    crashed._release_lock()

    restarted = _supervisor(tmp_path, "crashed-worker.sqlite3")
    _, recovery = restarted.start(reason="recover crashed owner")

    assert recovery.recovery_mode.value == "recovery"
    assert f"released_stale_lease:{prior_lease.lease_id}" in recovery.rebuild_actions
    assert all(lease.lease_id != prior_lease.lease_id for lease in restarted._open_leases())
    replacement = restarted.acquire_lease(
        worker_identity="thread:paper-scheduler", resource_envelope="paper-only"
    )
    assert replacement.session_id != prior_start.session_id
    restarted.stop()


def test_crash_recovery_keeps_live_process_lease_and_abstains(tmp_path):
    import os

    crashed = _supervisor(tmp_path, "live-worker.sqlite3")
    prior_start, _ = crashed.start(reason="owner crashed with external worker")
    lease = crashed.acquire_lease(
        worker_identity=f"pid:{os.getpid()}", resource_envelope="external-worker"
    )
    crashed.checkpoint()
    crashed._release_lock()

    restarted = _supervisor(tmp_path, "live-worker.sqlite3")
    _, recovery = restarted.start(reason="worker must be verified")

    assert recovery.recovery_mode.value == "abstain"
    assert "failed_check:stale_worker_leases" in recovery.decisions
    assert any(item.lease_id == lease.lease_id for item in restarted._open_leases())
    assert all(item.session_id == prior_start.session_id for item in restarted._open_leases())
    ended = restarted.stop(
        outcome=SessionOutcome.UNCLEAN, unfinished_work=("live_worker_lease",)
    )
    assert not ended.leases_released
    assert any(item.lease_id == lease.lease_id for item in restarted._open_leases())
