"""P08 strategy-approval gate: only the owner-pinned adapter may reach OMS.

An unapproved decision (research output, fallback, or no approval configured)
must abstain fail-closed with traceable ids and zero orders — never reaching
RiskKernel approval or the venue adapter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from advisorai.agents.fusion import DecisionBundle, EvidenceGateResult
from advisorai.api.service import DecisionPipelineResult
from advisorai.contracts import (
    AssetClass,
    InstrumentIdentity,
    RiskLimit,
    RiskPolicy,
    Snapshot,
    TargetPortfolio,
    TargetPosition,
)
from advisorai.decision import DecisionModelArtifact, DecisionProposal, DecisionStance
from advisorai.execution import AccountState, OrderManager, PaperVenueAdapter, RiskMarketState
from advisorai.ledger import SqliteLedgers
from advisorai.runtime import PaperRuntime, PaperRuntimeConfig, RuntimeStage


def _result(
    snapshot: Snapshot,
    *,
    proposal_adapter: str | None,
    actual_adapter: str | None = None,
    admitted: bool = True,
) -> DecisionPipelineResult:
    btc = InstrumentIdentity(canonical_id="BTC", asset_class=AssetClass.CRYPTO, venue="paper")
    target = TargetPortfolio(
        snapshot_id=snapshot.artifact_id,
        positions=(TargetPosition(instrument=btc, target_quantity=Decimal("1")),),
        cash_target=Decimal("900"),
        expected_cost=Decimal("0"),
        construction_method="fixture",
        risk_constraints_version="risk-v1",
        no_trade_comparison="current",
    )
    decision = DecisionBundle(
        mission_id=uuid4(),
        snapshot_id=snapshot.artifact_id,
        target_portfolio=target,
        evidence_ids=(uuid4(),),
        consensus="fixture",
        confidence=Decimal("0.8"),
        abstained=not admitted,
        expires_at=snapshot.as_of + timedelta(hours=1),
        created_at=snapshot.as_of,
        gate=EvidenceGateResult(
            passed=admitted,
            independent_origins=("fixture",),
            independent_source_families=("market",),
            independent_factor_families=("data_quality",),
            discounted_evidence_ids=(),
            reasons=(),
        ),
    )
    artifact = DecisionModelArtifact(
        model_name="fixture",
        adapter_name=actual_adapter or proposal_adapter or "missing",
        code_hash="a" * 64,
        config_hash="b" * 64,
        snapshot_id=snapshot.artifact_id,
    )
    proposal = (
        None
        if proposal_adapter is None
        else DecisionProposal(
            mission_id=decision.mission_id,
            snapshot_id=snapshot.artifact_id,
            model_artifact_id=artifact.artifact_id,
            adapter_name=proposal_adapter,
            stance=DecisionStance.FLAT,
            probabilities={"flat": Decimal("1")},
            calibrated_confidence=Decimal("0.5"),
            horizon="1h",
            uncertainty=Decimal("0.5"),
            evidence_ids=decision.evidence_ids,
            expires_at=snapshot.as_of + timedelta(hours=1),
        )
    )
    return DecisionPipelineResult(
        decision=decision,
        risk_decision=None,
        proposal=proposal,
        model_artifact=artifact if proposal is not None else None,
    )


def _runtime(
    tmp_path, name, *, approved: str | None, proposal_adapter: str | None, actual_adapter=None
):
    cutoff = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    snapshot = Snapshot(as_of=cutoff, purpose="approval-gate-fixture")
    ledgers = SqliteLedgers(tmp_path / name)
    adapter = PaperVenueAdapter(venue="paper")
    runtime = PaperRuntime(
        config=PaperRuntimeConfig(run_id=name, approved_strategy_adapter=approved),
        snapshot_provider=lambda requested: snapshot,
        market_provider=lambda _: RiskMarketState(
            marks={"BTC": Decimal("100")}, venue_healthy={"BTC": True}
        ),
        decision_builder=lambda *_: _result(
            snapshot, proposal_adapter=proposal_adapter, actual_adapter=actual_adapter
        ),
        account=AccountState(cash=Decimal("1000"), as_of=cutoff),
        risk_policy=RiskPolicy(
            policy_version="risk-v1",
            effective_at=cutoff - timedelta(hours=1),
            hard_limits=(RiskLimit(name="max_order_notional", limit=Decimal("200"), unit="USD"),),
            approved_by="reviewer",
        ),
        orders=OrderManager(ledgers, adapter),
        ledgers=ledgers,
    )
    return runtime, cutoff, adapter


def test_approved_strategy_routes_with_traceable_ids(tmp_path):
    runtime, cutoff, adapter = _runtime(
        tmp_path, "gate-approved.sqlite3", approved="strat-a", proposal_adapter="strat-a"
    )
    cycle = runtime.run_once(cutoff)
    assert cycle.stage is RuntimeStage.ORDERS_ROUTED
    assert len(cycle.order_ids) == 1
    assert cycle.decision_id is not None
    assert cycle.snapshot_id is not None
    assert cycle.risk_decision_id is not None
    assert len(adapter.open_orders()) == 1


def test_unapproved_adapter_abstains_with_zero_orders(tmp_path):
    runtime, cutoff, adapter = _runtime(
        tmp_path,
        "gate-denied.sqlite3",
        approved="strat-a",
        proposal_adapter="research-candidate-x",
    )
    cycle = runtime.run_once(cutoff)
    assert cycle.stage is RuntimeStage.ABSTAINED
    assert cycle.reasons == ("unapproved_strategy:research-candidate-x",)
    assert cycle.order_ids == ()
    assert cycle.risk_decision_id is None
    # Traceable: the blocked decision is still recorded with ids.
    assert cycle.decision_id is not None
    assert cycle.snapshot_id is not None
    # Nothing reached the venue adapter.
    assert adapter.open_orders() == ()
    assert runtime.reconcile_once().reconciled is True


def test_no_approval_configured_denies_routing(tmp_path):
    runtime, cutoff, adapter = _runtime(
        tmp_path, "gate-none.sqlite3", approved=None, proposal_adapter="strat-a"
    )
    cycle = runtime.run_once(cutoff)
    assert cycle.stage is RuntimeStage.ABSTAINED
    assert cycle.reasons == ("unapproved_strategy:strat-a",)
    assert cycle.order_ids == ()
    assert adapter.open_orders() == ()


def test_proposal_cannot_claim_another_adapters_approval(tmp_path):
    runtime, cutoff, adapter = _runtime(
        tmp_path,
        "gate-forged-name.sqlite3",
        approved="strat-a",
        proposal_adapter="strat-a",
        actual_adapter="research-candidate",
    )
    cycle = runtime.run_once(cutoff)
    assert cycle.stage is RuntimeStage.ABSTAINED
    assert cycle.reasons == ("unapproved_strategy:research-candidate",)
    assert cycle.risk_decision_id is None
    assert cycle.order_ids == ()
    assert adapter.open_orders() == ()


def test_missing_proposal_abstains_fail_closed(tmp_path):
    runtime, cutoff, adapter = _runtime(
        tmp_path, "gate-missing.sqlite3", approved="strat-a", proposal_adapter=None
    )
    cycle = runtime.run_once(cutoff)
    assert cycle.stage is RuntimeStage.ABSTAINED
    assert cycle.reasons == ("unapproved_strategy:missing-proposal",)
    assert cycle.order_ids == ()
    assert adapter.open_orders() == ()


def test_blocked_cycle_rerun_returns_prior_without_orders(tmp_path):
    runtime, cutoff, adapter = _runtime(
        tmp_path,
        "gate-rerun.sqlite3",
        approved="strat-a",
        proposal_adapter="research-candidate-x",
    )
    first = runtime.run_once(cutoff)
    again = runtime.run_once(cutoff)
    assert again.cycle_id == first.cycle_id
    assert again.stage is RuntimeStage.ABSTAINED
    assert adapter.open_orders() == ()
