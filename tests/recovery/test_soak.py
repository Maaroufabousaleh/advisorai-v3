from datetime import timedelta
from decimal import Decimal

from advisorai.recovery import RecoveryService
from advisorai.soak import FailureScenario, PaperSoakController, SoakSample


def test_soak_gate_requires_adverse_conditions_and_sixty_days(timestamp):
    controller = PaperSoakController(timestamp)
    controller.record(
        SoakSample(
            at=timestamp + timedelta(days=59),
            decision_count=100,
            trade_count=20,
            net_utility_after_costs=Decimal("10"),
            resource_stable=True,
            reconciliation_clean=True,
            safety_clean=True,
        )
    )
    gate = controller.gate(minimum_decisions=10, minimum_trades=5)
    assert not gate.passed
    assert "less_than_60_calendar_days" in gate.reasons
    controller.record(
        SoakSample(
            at=timestamp + timedelta(days=60),
            decision_count=110,
            trade_count=22,
            net_utility_after_costs=Decimal("11"),
            resource_stable=True,
            reconciliation_clean=True,
            safety_clean=True,
            adverse_scenarios=(FailureScenario.VENUE_OUTAGE,),
        )
    )
    assert controller.gate(minimum_decisions=10, minimum_trades=5).passed


def test_soak_gate_rejects_unresolved_safety_or_reconciliation(timestamp):
    controller = PaperSoakController(timestamp)
    controller.record(
        SoakSample(
            at=timestamp + timedelta(days=60),
            decision_count=10,
            trade_count=10,
            net_utility_after_costs=Decimal("1"),
            resource_stable=True,
            reconciliation_clean=False,
            safety_clean=True,
            adverse_scenarios=(FailureScenario.DUPLICATE_PARTIAL_FILL,),
        )
    )
    gate = controller.gate(minimum_decisions=1, minimum_trades=1)
    assert not gate.passed
    assert "unresolved_reconciliation" in gate.reasons


def test_soak_samples_rebuild_from_incident_ledger(tmp_path, timestamp):
    from advisorai.ledger import LedgerNamespace, SqliteLedgers

    ledgers = SqliteLedgers(tmp_path / "soak.sqlite")
    sample = SoakSample(
        at=timestamp + timedelta(days=1),
        decision_count=2,
        trade_count=1,
        net_utility_after_costs=Decimal("0.1"),
        resource_stable=True,
        reconciliation_clean=True,
        safety_clean=True,
        adverse_scenarios=(FailureScenario.VENUE_OUTAGE,),
    )
    PaperSoakController(timestamp, ledgers=ledgers).record(sample)
    restarted = PaperSoakController(timestamp, ledgers=ledgers)
    assert restarted.samples == [sample]
    assert any(
        event.event_type == "soak_sample_recorded"
        for event in ledgers.events(LedgerNamespace.INCIDENT)
    )


def test_soak_gate_tracks_scorecards_headroom_and_benchmarks(timestamp):
    controller = PaperSoakController(timestamp)
    controller.record(
        SoakSample(
            at=timestamp + timedelta(days=60),
            decision_count=10,
            trade_count=2,
            net_utility_after_costs=Decimal("10"),
            no_trade_net_utility=Decimal("11"),
            benchmark_net_utility=Decimal("9"),
            headroom_gib=Decimal("1.0"),
            resource_stable=True,
            reconciliation_clean=True,
            safety_clean=True,
            model_scorecard_passed=False,
            adverse_scenarios=(FailureScenario.VENUE_OUTAGE,),
        )
    )
    gate = controller.gate(minimum_decisions=1, minimum_trades=1)
    assert not gate.passed
    assert {
        "scorecard_failure",
        "resource_headroom_breach",
        "no_trade_not_beaten",
    }.issubset(gate.reasons)


def test_recovery_requires_archive_restore_evidence(tmp_path, timestamp, observation):
    from advisorai.contracts import ArtifactTier
    from advisorai.lake import DataLake
    from advisorai.ledger import SqliteLedgers

    lake = DataLake(tmp_path / "lake")
    manifest = lake.write_observations(
        tier=ArtifactTier.SILVER, dataset="market", observations=(observation,)
    )
    report = RecoveryService(lake, SqliteLedgers(tmp_path / "state.sqlite")).rebuild(
        manifests=(manifest,), archive_restore_verified=False
    )
    assert not report.passed
    assert "archive_restore_not_verified" in report.reasons
    assert (
        RecoveryService(lake, SqliteLedgers(tmp_path / "state.sqlite"))
        .rebuild(manifests=(manifest,), archive_restore_verified=True)
        .passed
    )
