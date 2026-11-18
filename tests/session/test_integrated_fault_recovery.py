"""Integrated fault recovery: the full paper path under deterministic faults.

Each test drives the real integrated stack (supervisor + runtime + decision +
RiskKernel + OMS + paper venue + reconciliation) with no network and no
credentials. Faults are injected through the adapter's deterministic hooks or
explicit operator actions; every test proves fail-closed behavior plus the
designed recovery, never a wedged or duplicated order.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from advisorai.agents import EvidenceCouncil, MissionRequest
from advisorai.agents.council import RoleResult
from advisorai.agents.router import MissionKind
from advisorai.api.service import AdvisorService
from advisorai.contracts import (
    AssetClass,
    Evidence,
    InstrumentIdentity,
    OrderState,
    RiskLimit,
    RiskPolicy,
    Snapshot,
)
from advisorai.decision import DecisionModelArtifact, DecisionStance, ReferenceDecisionAdapter
from advisorai.execution import (
    AccountState,
    KillSwitch,
    OrderManager,
    OrderStateError,
    PaperVenueAdapter,
    RiskKernel,
    RiskMarketState,
    VenueAccountSnapshot,
)
from advisorai.ledger import SqliteLedgers
from advisorai.runtime import CadenceGate, PaperRuntime, PaperRuntimeConfig, RuntimeStage
from advisorai.runtime.paper import build_default_orders
from advisorai.session import (
    SessionOutcome,
    SessionRecoveryChecks,
    SessionStopChecks,
    SessionSupervisor,
)

CUTOFF = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)


def _passing_council(as_of):
    def role(name, origin, family, factor):
        def run(_snapshot):
            return RoleResult(
                role=name,
                evidence=(
                    Evidence(
                        claim=f"{name} claim",
                        source_family=family,
                        origin=origin,
                        observed_at=as_of,
                        first_available_at=as_of,
                        uncertainty=Decimal("0.1"),
                        expires_at=as_of + timedelta(hours=1),
                    ),
                ),
            )

        return run

    return EvidenceCouncil(
        {
            "data_verifier": role("data_verifier", "venue-a", "market", "data_verifier"),
            "technical_flow": role("technical_flow", "venue-b", "market", "technical"),
            "derivatives_regime": role("derivatives_regime", "venue-c", "market", "derivatives"),
            "news_event": role("news_event", "wire-a", "news", "event"),
            "skeptic_base_rate": role("skeptic_base_rate", "research-a", "research", "base_rate"),
        }
    )


class _Session:
    """One supervised paper session with a LONG decision path."""

    def __init__(self, tmp_path, name="fault-e2e.sqlite3", **runtime_kwargs):
        self.ledgers = SqliteLedgers(tmp_path / name)
        self.state_dir = tmp_path / f"{name}.state"
        self.supervisor = SessionSupervisor(
            self.ledgers,
            state_dir=self.state_dir,
            lock_identity="owner-workstation",
            code_hash="c" * 64,
            config_hash="d" * 64,
        )
        self.supervisor.start(reason="integrated fault session")
        self.btc = InstrumentIdentity(
            canonical_id="BTC", asset_class=AssetClass.CRYPTO, venue="paper"
        )
        self.account = AccountState(
            cash=Decimal("100000"), as_of=CUTOFF, marks={"BTC": Decimal("50000")}
        )
        self.market = RiskMarketState(
            marks={"BTC": Decimal("50000")},
            volatility={"BTC": Decimal("0.01")},
            liquidity_notional={"BTC": Decimal("1000000")},
        )
        self.policy = RiskPolicy(
            policy_version="risk-v1",
            effective_at=CUTOFF,
            hard_limits=(
                RiskLimit(name="max_gross_notional", limit=Decimal("500000"), unit="USD"),
                RiskLimit(name="max_order_notional", limit=Decimal("500000"), unit="USD"),
            ),
            approved_by="owner",
        )
        self.adapter = PaperVenueAdapter()
        self.orders = OrderManager(self.ledgers, self.adapter)
        self.runtime = PaperRuntime(
            config=PaperRuntimeConfig(run_id=f"fault-{name}"),
            snapshot_provider=lambda cutoff: Snapshot(as_of=cutoff, purpose="fault-fixture"),
            market_provider=lambda snapshot: self.market,
            decision_builder=self._decide,
            account=self.account,
            risk_policy=self.policy,
            orders=self.orders,
            ledgers=self.ledgers,
            **runtime_kwargs,
        )

    def _decide(self, snapshot, account, market):
        artifact = DecisionModelArtifact(
            model_name="reference",
            adapter_name="reference-long-v1",
            code_hash="a" * 64,
            config_hash="b" * 64,
            snapshot_id=snapshot.artifact_id,
        )
        service = AdvisorService(
            _passing_council(snapshot.as_of),
            self.ledgers,
            decision_model=ReferenceDecisionAdapter(
                artifact, directional_stance=DecisionStance.LONG
            ),
        )
        return service.build_decision(
            request=MissionRequest(kind=MissionKind.RECOMMENDATION, user_text="fault e2e"),
            snapshot=snapshot,
            account=account,
            market=market,
            targets={self.btc: Decimal("0")},
            risk_policy_version="risk-v1",
        )


def test_stale_observation_data_abstains_without_orders(tmp_path):
    gate = CadenceGate()
    stale = _Session(
        tmp_path,
        name="stale-e2e.sqlite3",
        cadence=gate,
        observation_cutoff_provider=lambda cutoff: (),
    )
    cycle = stale.runtime.run_once(CUTOFF)
    assert cycle.stage is RuntimeStage.ABSTAINED
    assert "missing_closed_observation_data" in cycle.reasons
    assert cycle.order_ids == ()
    assert stale.adapter.open_orders() == ()

    fresh = _Session(
        tmp_path,
        name="fresh-e2e.sqlite3",
        cadence=gate,
        observation_cutoff_provider=lambda cutoff: gate.expected_observation_cutoffs(cutoff),
    )
    routed = fresh.runtime.run_once(CUTOFF)
    assert routed.stage is RuntimeStage.ORDERS_ROUTED
    assert len(routed.order_ids) == 1


def test_venue_outage_fails_closed_then_recovers_without_duplicates(tmp_path):
    session = _Session(tmp_path)
    session.adapter.inject_outage_once()
    failed = session.runtime.run_once(CUTOFF)
    assert failed.stage is RuntimeStage.FAILED
    assert failed.reasons == ("RuntimeError",)
    assert session.adapter.open_orders() == ()
    # The failed cycle carries no order ids; the orphaned route is found in
    # the durable OMS projection (ROUTED, never acknowledged, not ambiguous:
    # an outage is not proof the venue received anything).
    routed = [
        order_id
        for order_id, order in session.orders.orders.items()
        if order.state is OrderState.ROUTED
    ]
    (orphan_id,) = routed
    assert orphan_id not in session.orders.ambiguous_orders

    # The venue never received the order: expire the orphaned route and
    # reconcile before any further action.
    session.orders.expire_unacknowledged(orphan_id)
    session.orders.reconcile(orphan_id)
    assert session.runtime.reconcile_once().reconciled is True

    healthy = session.runtime.run_once(CUTOFF + timedelta(hours=1))
    assert healthy.stage is RuntimeStage.ORDERS_ROUTED
    assert len(session.adapter.open_orders()) == 1
    assert healthy.order_ids != (orphan_id,)


def test_ambiguous_ack_reconciles_without_resubmission(tmp_path):
    def arming_factory(result, account, market):
        built = build_default_orders(result, account, market)
        for order in built:
            session.adapter.inject_ambiguous_ack_once(order.idempotency_key)
        return built

    session = _Session(tmp_path, order_factory=arming_factory)
    pending = session.runtime.run_once(CUTOFF)
    assert pending.stage is RuntimeStage.RECONCILIATION_FAILED
    assert pending.reasons == ("ambiguous_order_acknowledgement_pending",)
    (order_id,) = pending.order_ids
    assert order_id in session.orders.ambiguous_orders
    # The venue holds the order even though the acknowledgement was lost.
    assert len(session.adapter.open_orders()) == 1

    try:
        session.orders.route(order_id)
    except OrderStateError as exc:
        assert "before retry" in str(exc)
    else:
        raise AssertionError("ambiguous order was resubmitted blindly")

    acknowledgement = session.orders.reconcile_ambiguous(order_id)
    assert acknowledgement.accepted is True
    assert session.orders.orders[order_id].state is OrderState.ACKNOWLEDGED
    assert len(session.adapter.open_orders()) == 1
    assert session.runtime.reconcile_once().reconciled is True


def test_reconciliation_mismatch_trips_kill_switch_and_blocks_new_orders(tmp_path):
    session = _Session(tmp_path)
    first = session.runtime.run_once(CUTOFF)
    assert first.stage is RuntimeStage.ORDERS_ROUTED

    tampered = VenueAccountSnapshot(
        as_of=session.account.as_of,
        cash=session.account.cash + Decimal("1"),
        positions={},
    )
    mismatch = session.runtime.reconcile_once(venue_snapshot=tampered)
    assert mismatch.reconciled is False
    assert any("venue_cash_mismatch" in item for item in mismatch.discrepancies)
    assert session.runtime.risk_kernel.kill_switch.tripped is True
    # The trip is durable: a fresh kernel over the same ledgers stays tripped.
    assert RiskKernel(kill_switch=KillSwitch(session.ledgers)).kill_switch.tripped is True

    blocked = session.runtime.run_once(CUTOFF + timedelta(hours=1))
    assert blocked.stage is RuntimeStage.ABSTAINED
    assert blocked.reasons[0].startswith("kill_switch:")
    assert blocked.order_ids == ()
    assert len(session.adapter.open_orders()) == 1

    session.runtime.resume(approved_by="owner")
    assert session.runtime.risk_kernel.kill_switch.tripped is False
    recovered = session.runtime.run_once(CUTOFF + timedelta(hours=2))
    assert recovered.stage is RuntimeStage.ORDERS_ROUTED


def test_emergency_halt_stops_routing_until_approved_resume(tmp_path):
    session = _Session(tmp_path)
    session.runtime.halt("operator drill")
    assert session.runtime.run_forever(max_cycles=5) == ()

    halted = session.runtime.run_once(CUTOFF)
    assert halted.stage is RuntimeStage.ABSTAINED
    assert halted.reasons == ("kill_switch:operator drill",)
    assert halted.order_ids == ()
    assert session.adapter.open_orders() == ()

    session.supervisor.checkpoint()
    end = session.supervisor.stop()
    assert end.outcome is SessionOutcome.CLEAN

    session.runtime.resume(approved_by="owner")
    recovered = session.runtime.run_once(CUTOFF + timedelta(hours=1))
    assert recovered.stage is RuntimeStage.ORDERS_ROUTED


def test_restart_reconciles_open_orders_before_new_action(tmp_path):
    session = _Session(tmp_path)
    first = session.runtime.run_once(CUTOFF)
    assert first.stage is RuntimeStage.ORDERS_ROUTED
    (open_id,) = first.order_ids

    session.supervisor.checkpoint()
    end = session.supervisor.stop(
        outcome=SessionOutcome.UNCLEAN,
        open_orders=(open_id,),
        unfinished_work=("cycle-1-open",),
    )
    assert end.outcome is SessionOutcome.UNCLEAN

    def fresh_supervisor():
        return SessionSupervisor(
            SqliteLedgers(tmp_path / "fault-e2e.sqlite3"),
            state_dir=session.state_dir,
            lock_identity="owner-workstation",
            code_hash="c" * 64,
            config_hash="d" * 64,
        )

    # Verified recovery: every locally open order must still be present at
    # the venue (the venue may retain reconciled history).
    def venue_matches(ledgers):
        current = OrderManager(ledgers, session.adapter)
        local_open = {
            order.artifact_id
            for order in current.orders.values()
            if order.state.value in {"routed", "acknowledged", "partially_filled", "cancel_pending"}
        }
        venue_open = {ack.order_id for ack in session.adapter.open_orders()}
        return local_open <= venue_open

    # Restart without a venue verifier fails closed: no new proposals while
    # local orders are unresolved.
    abstained = fresh_supervisor()
    _, recovery = abstained.start(reason="restart after crash")
    assert recovery.recovery_mode.value == "abstain"
    assert "abstain_new_proposals" in recovery.decisions

    # Supervised recovery: cancel the stale open order against the live
    # venue projection, then reconcile it before any new action.
    orders = OrderManager(abstained.ledgers, session.adapter)
    assert orders.orders[open_id].state is OrderState.ACKNOWLEDGED
    orders.cancel(open_id)
    orders.acknowledge_cancel(open_id)
    orders.reconcile(open_id)
    assert orders.orders[open_id].state is OrderState.RECONCILED
    abstained.checkpoint()
    clean_end = abstained.stop(
        stop_checks=SessionStopChecks(current_order_account_verifier=venue_matches)
    )
    assert clean_end.outcome is SessionOutcome.CLEAN

    verified = fresh_supervisor()
    _, clean = verified.start(
        reason="verified restart",
        recovery_checks=SessionRecoveryChecks(current_order_account_verifier=venue_matches),
    )
    assert clean.recovery_mode.value == "normal"
    assert clean.decisions == ()

    runtime = PaperRuntime(
        config=PaperRuntimeConfig(run_id="fault-restart"),
        snapshot_provider=lambda cutoff: Snapshot(as_of=cutoff, purpose="fault-fixture"),
        market_provider=lambda snapshot: session.market,
        decision_builder=session._decide,
        account=session.account,
        risk_policy=session.policy,
        orders=OrderManager(verified.ledgers, session.adapter),
        ledgers=verified.ledgers,
    )
    assert runtime.reconcile_once().reconciled is True
    second = runtime.run_once(CUTOFF + timedelta(hours=1))
    assert second.stage is RuntimeStage.ORDERS_ROUTED
    keys = [item.venue_order_id for item in session.adapter.open_orders()]
    assert len(keys) == len(set(keys)) == 2


def test_duplicate_run_at_same_cutoff_routes_nothing_new(tmp_path):
    session = _Session(tmp_path)
    first = session.runtime.run_once(CUTOFF)
    assert first.stage is RuntimeStage.ORDERS_ROUTED
    again = session.runtime.run_once(CUTOFF)
    assert again.cycle_id == first.cycle_id
    assert again.stage is first.stage
    assert len(session.adapter.open_orders()) == 1
