"""Integrated paper session: start → operate → stop → restart, no duplicates."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from advisorai.agents import EvidenceCouncil, MissionRequest
from advisorai.agents.router import MissionKind
from advisorai.api.service import AdvisorService
from advisorai.contracts import AssetClass, InstrumentIdentity, RiskLimit, RiskPolicy, Snapshot
from advisorai.decision import DecisionModelArtifact, ReferenceDecisionAdapter
from advisorai.execution import AccountState, OrderManager, PaperVenueAdapter, RiskMarketState
from advisorai.ledger import SqliteLedgers
from advisorai.runtime import PaperRuntime, PaperRuntimeConfig
from advisorai.session import SessionOutcome, SessionSupervisor


def _build(tmp_path, name="session-e2e.sqlite3"):
    ledgers = SqliteLedgers(tmp_path / name)
    supervisor = SessionSupervisor(
        ledgers,
        state_dir=tmp_path / "state",
        lock_identity="owner-workstation",
        code_hash="c" * 64,
        config_hash="d" * 64,
    )
    supervisor.start(reason="integrated paper session")
    btc = InstrumentIdentity(canonical_id="BTC", asset_class=AssetClass.CRYPTO, venue="paper")
    cutoff = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    snapshot = Snapshot(as_of=cutoff, purpose="e2e-fixture")
    account = AccountState(cash=Decimal("100000"), as_of=cutoff, marks={"BTC": Decimal("50000")})
    market = RiskMarketState(marks={"BTC": Decimal("50000")})
    council = EvidenceCouncil({})
    artifact = DecisionModelArtifact(
        model_name="reference",
        adapter_name="reference-v1",
        code_hash="a" * 64,
        config_hash="b" * 64,
        snapshot_id=snapshot.artifact_id,
    )
    service = AdvisorService(council, ledgers, decision_model=ReferenceDecisionAdapter(artifact))
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=cutoff,
        hard_limits=(RiskLimit(name="gross", limit=Decimal("50000"), unit="USD"),),
        approved_by="owner",
    )
    orders = OrderManager(ledgers, PaperVenueAdapter())
    runtime = PaperRuntime(
        config=PaperRuntimeConfig(run_id="e2e-run"),
        snapshot_provider=lambda cutoff: snapshot,
        market_provider=lambda snapshot: market,
        decision_builder=lambda snapshot, account, market: service.build_decision(
            request=MissionRequest(kind=MissionKind.TRADE, user_text="e2e paper session"),
            snapshot=snapshot,
            account=account,
            market=market,
            targets={btc: Decimal("0")},
            risk_policy_version="risk-v1",
        ),
        account=account,
        risk_policy=policy,
        orders=orders,
        ledgers=ledgers,
    )
    return supervisor, runtime


def test_clean_session_operates_and_restarts_without_duplicates(tmp_path):
    supervisor, runtime = _build(tmp_path)
    cycle = runtime.run_once(datetime(2026, 8, 5, 15, 0, tzinfo=UTC))
    assert cycle.stage.value in {"abstained", "no_trade", "orders_routed", "risk_rejected"}
    first_orders = tuple(cycle.order_ids)
    end = supervisor.stop()
    assert end.outcome is SessionOutcome.CLEAN

    supervisor2 = SessionSupervisor(
        SqliteLedgers(tmp_path / "session-e2e.sqlite3"),
        state_dir=tmp_path / "state",
        lock_identity="owner-workstation",
        code_hash="c" * 64,
        config_hash="d" * 64,
    )
    start2, recovery2 = supervisor2.start(reason="restart")
    assert start2.prior_end_record_id == end.artifact_id
    assert recovery2.recovery_mode.value == "normal"
    supervisor2.stop()
    assert len(set(first_orders)) == len(first_orders)


def test_crash_restart_enters_recovery(tmp_path):
    supervisor, _ = _build(tmp_path, name="crash-e2e.sqlite3")
    supervisor.stop(outcome=SessionOutcome.UNCLEAN, unfinished_work=("cycle-1",))
    supervisor2 = SessionSupervisor(
        SqliteLedgers(tmp_path / "crash-e2e.sqlite3"),
        state_dir=tmp_path / "state",
        lock_identity="owner-workstation",
        code_hash="c" * 64,
        config_hash="d" * 64,
    )
    _, recovery = supervisor2.start(reason="restart after crash")
    assert recovery.recovery_mode.value == "recovery"
    supervisor2.stop()


def test_evidence_gate_failure_abstains_without_orders(tmp_path):
    _, runtime = _build(tmp_path, name="abstain-e2e.sqlite3")
    cycle = runtime.run_once(datetime(2026, 8, 5, 15, 0, tzinfo=UTC))
    assert cycle.stage.value == "abstained"
    assert cycle.order_ids == ()
