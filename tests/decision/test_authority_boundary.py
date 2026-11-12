"""Adversarial Decision Model authority tests.

Every executable target must be consistent with the validated, unexpired,
identity-matched DecisionProposal. ABSTAIN, invalid, missing, stale, expired,
or mismatched proposals fail closed to no-trade. The Portfolio Constructor
and RiskKernel remain the sole trading authorities; the Decision Model (and
any caller-supplied targets) can never bypass them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock
from uuid import uuid4

from advisorai.agents import EvidenceCouncil, MissionRequest
from advisorai.agents.council import RoleResult
from advisorai.agents.router import MissionKind
from advisorai.api import AdvisorService
from advisorai.contracts import (
    AssetClass,
    Evidence,
    InstrumentIdentity,
    RiskLimit,
    RiskPolicy,
    Snapshot,
)
from advisorai.decision import (
    DecisionModelArtifact,
    DecisionModelInput,
    DecisionStance,
    ReferenceDecisionAdapter,
)
from advisorai.execution import AccountState, RiskMarketState


def _passing_council(timestamp):
    def role(name, origin, family, factor):
        def run(_snapshot):
            evidence = Evidence(
                claim=f"{name} claim",
                source_family=family,
                origin=origin,
                observed_at=timestamp,
                first_available_at=timestamp,
                uncertainty=Decimal("0.1"),
                expires_at=timestamp + timedelta(hours=1),
            )
            return RoleResult(role=name, evidence=(evidence,))

        return run

    return EvidenceCouncil(
        {
            "data_verifier": role("data_verifier", "venue-a", "market", "data_verifier"),
            "technical_flow": role("technical_flow", "venue-b", "market", "technical"),
            "derivatives_regime": role("derivatives_regime", "venue-c", "market", "risk"),
            "news_event": role("news_event", "wire-a", "news", "event"),
        }
    )


def _context(tmp_path):
    timestamp = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    btc = InstrumentIdentity(canonical_id="BTC", asset_class=AssetClass.CRYPTO, venue="paper")
    snapshot = Snapshot(as_of=timestamp, purpose="adversarial")
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    market = RiskMarketState(marks={"BTC": Decimal("100")})
    artifact = DecisionModelArtifact(
        model_name="reference",
        adapter_name="reference-adversarial",
        code_hash="a" * 64,
        config_hash="b" * 64,
        snapshot_id=snapshot.artifact_id,
    )
    return timestamp, btc, snapshot, account, market, artifact


def _policy(timestamp):
    return RiskPolicy(
        policy_version="risk-v1",
        effective_at=timestamp,
        hard_limits=(RiskLimit(name="max_gross_notional", limit=Decimal("50"), unit="USD"),),
        approved_by="human",
    )


def test_caller_targets_cannot_smuggle_quantity_past_gate(tmp_path):
    timestamp, btc, snapshot, account, market, artifact = _context(tmp_path)
    service = AdvisorService(_passing_council(timestamp))
    result = service.build_decision(
        request=MissionRequest(kind=MissionKind.RECOMMENDATION, user_text="review"),
        snapshot=snapshot,
        account=account,
        market=market,
        targets={btc: Decimal("999")},
        risk_policy_version="risk-v1",
    )
    # Gate passes here (4 roles, 3 families) and the reference adapter holds
    # FLAT: the caller asked for 999 but the proposal binds to no-trade.
    assert result.decision.gate.passed is True
    assert result.proposal is not None and result.proposal.stance is DecisionStance.FLAT
    assert all(
        position.target_quantity == Decimal("0")
        for position in result.decision.target_portfolio.positions
    )


def test_mismatched_proposal_snapshot_fails_closed(tmp_path):
    timestamp, btc, snapshot, account, market, artifact = _context(tmp_path)

    class SnapshotLiar(ReferenceDecisionAdapter):
        def propose(self, request: DecisionModelInput, *, now):
            good = super().propose(request, now=now)
            return good.model_copy(update={"snapshot_id": uuid4()})

    service = AdvisorService(_passing_council(timestamp), decision_model=SnapshotLiar(artifact))
    result = service.build_decision(
        request=MissionRequest(kind=MissionKind.RECOMMENDATION, user_text="review"),
        snapshot=snapshot,
        account=account,
        market=market,
        targets={btc: Decimal("1")},
        risk_policy_version="risk-v1",
    )
    assert "proposal_snapshot_mismatch" in result.decision.missing_evidence
    assert result.decision.abstained is True
    checked = service.evaluate_risk(
        result, account=account, market=market, policy=_policy(timestamp)
    )
    assert checked.risk_decision is not None
    assert checked.risk_decision.outcome.value == "rejected"


def test_expired_proposal_fails_closed(tmp_path):
    timestamp, btc, snapshot, account, market, artifact = _context(tmp_path)

    class Expired(ReferenceDecisionAdapter):
        def propose(self, request: DecisionModelInput, *, now):
            good = super().propose(request, now=now)
            return good.model_copy(update={"expires_at": timestamp - timedelta(minutes=1)})

    service = AdvisorService(_passing_council(timestamp), decision_model=Expired(artifact))
    result = service.build_decision(
        request=MissionRequest(kind=MissionKind.RECOMMENDATION, user_text="review"),
        snapshot=snapshot,
        account=account,
        market=market,
        targets={btc: Decimal("1")},
        risk_policy_version="risk-v1",
    )
    assert "proposal_expired" in result.decision.missing_evidence
    assert result.decision.abstained is True


def test_model_failure_becomes_abstain_never_an_order(tmp_path):
    timestamp, btc, snapshot, account, market, artifact = _context(tmp_path)
    broken = Mock()
    broken.propose.side_effect = RuntimeError("model blew up")
    service = AdvisorService(_passing_council(timestamp), decision_model=broken)
    result = service.build_decision(
        request=MissionRequest(kind=MissionKind.RECOMMENDATION, user_text="review"),
        snapshot=snapshot,
        account=account,
        market=market,
        targets={btc: Decimal("1")},
        risk_policy_version="risk-v1",
    )
    assert result.proposal is not None
    assert result.proposal.abstained is True
    assert result.proposal.fallback_used is True
    assert result.decision.abstained is True
    assert all(
        position.target_quantity == Decimal("0")
        for position in result.decision.target_portfolio.positions
    )
