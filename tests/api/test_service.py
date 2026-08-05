from datetime import timedelta
from decimal import Decimal
from hashlib import sha256

from advisorai.agents import EvidenceCouncil, MissionRequest
from advisorai.agents.council import RoleResult
from advisorai.agents.router import MissionKind
from advisorai.api import AdvisorService
from advisorai.contracts import Evidence, RiskLimit, RiskPolicy, Snapshot
from advisorai.execution import AccountState, RiskMarketState


def test_advisor_service_ends_at_target_then_runs_risk_gate(btc_usdt, timestamp):
    def evidence(claim, family, origin, factor):
        return Evidence(
            claim=claim,
            source_family=family,
            origin=origin,
            observed_at=timestamp,
            first_available_at=timestamp,
            uncertainty=Decimal("0.1"),
            expires_at=timestamp.replace(hour=13),
        ), factor

    data, _ = evidence("fresh", "market", "venue", "data_quality")
    technical, _ = evidence("up", "market", "venue", "technical")
    council = EvidenceCouncil(
        {
            "data_verifier": lambda _: RoleResult("data_verifier", (data,)),
            "technical_flow": lambda _: RoleResult("technical_flow", (technical,)),
            "derivatives_regime": lambda _: RoleResult(
                "derivatives_regime", (evidence("risk", "derivatives", "deribit", "risk")[0],)
            ),
        }
    )
    service = AdvisorService(council)
    result = service.build_decision(
        request=MissionRequest(kind=MissionKind.RECOMMENDATION, user_text="review"),
        snapshot=Snapshot(as_of=timestamp, purpose="api"),
        account=AccountState(cash=Decimal("1000")),
        market=RiskMarketState(marks={btc_usdt.canonical_id: Decimal("100")}),
        targets={btc_usdt: Decimal("1")},
        risk_policy_version="risk-v1",
    )
    assert result.risk_decision is None
    assert result.decision.target_portfolio.positions[0].target_quantity == Decimal("1")
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=timestamp,
        hard_limits=(RiskLimit(name="max_gross_notional", limit=Decimal("50"), unit="USD"),),
        approved_by="human",
    )
    checked = service.evaluate_risk(
        result,
        account=AccountState(cash=Decimal("1000")),
        market=RiskMarketState(marks={btc_usdt.canonical_id: Decimal("100")}),
        policy=policy,
    )
    assert checked.risk_decision.outcome.value == "rejected"


def test_advisor_service_rejects_risk_when_evidence_gate_fails(btc_usdt, timestamp):
    evidence = Evidence(
        claim="one source only",
        source_family="market",
        origin="venue",
        observed_at=timestamp,
        first_available_at=timestamp,
        uncertainty=Decimal("0.2"),
        expires_at=timestamp.replace(hour=13),
    )
    service = AdvisorService(
        EvidenceCouncil({"data_verifier": lambda _: RoleResult("data_verifier", (evidence,))})
    )
    account = AccountState(cash=Decimal("1000"), as_of=timestamp)
    market = RiskMarketState(marks={btc_usdt.canonical_id: Decimal("100")})
    result = service.build_decision(
        request=MissionRequest(kind=MissionKind.RECOMMENDATION, user_text="review"),
        snapshot=Snapshot(as_of=timestamp, purpose="api-gate"),
        account=account,
        market=market,
        targets={btc_usdt: Decimal("1")},
        risk_policy_version="risk-v1",
    )
    policy = RiskPolicy(
        policy_version="risk-v1",
        effective_at=timestamp,
        hard_limits=(),
        approved_by="human",
    )

    checked = service.evaluate_risk(result, account=account, market=market, policy=policy)

    assert not result.decision.gate.passed
    assert result.decision.abstained
    assert checked.risk_decision is not None
    assert checked.risk_decision.outcome.value == "rejected"
    assert "evidence_gate_not_passed" in checked.risk_decision.reasons
    assert (
        checked.risk_decision.authoritative_state_hash
        == sha256(f"{account.snapshot().state_hash}:{market.effective_hash}".encode()).hexdigest()
    )


def test_advisor_service_can_evaluate_decision_expiry_at_an_explicit_cutoff(btc_usdt, timestamp):
    def evidence(claim, family, origin, factor):
        return Evidence(
            claim=claim,
            source_family=family,
            origin=origin,
            observed_at=timestamp,
            first_available_at=timestamp,
            uncertainty=Decimal("0.1"),
            expires_at=timestamp + timedelta(hours=1),
        )

    council = EvidenceCouncil(
        {
            "data_verifier": lambda _: RoleResult(
                "data_verifier", (evidence("fresh", "market", "venue", "data_quality"),)
            ),
            "technical_flow": lambda _: RoleResult(
                "technical_flow", (evidence("up", "flow", "flow-source", "technical"),)
            ),
            "risk_opportunity": lambda _: RoleResult(
                "risk_opportunity", (evidence("safe", "risk", "risk-source", "risk"),)
            ),
        }
    )
    service = AdvisorService(council)
    result = service.build_decision(
        request=MissionRequest(kind=MissionKind.RECOMMENDATION, user_text="review"),
        snapshot=Snapshot(as_of=timestamp, purpose="expiry"),
        account=AccountState(cash=Decimal("1000"), as_of=timestamp),
        market=RiskMarketState(marks={btc_usdt.canonical_id: Decimal("100")}),
        targets={btc_usdt: Decimal("1")},
        risk_policy_version="risk-v1",
    )
    assert result.decision.gate.passed
    policy = RiskPolicy(
        policy_version="risk-v1", effective_at=timestamp, hard_limits=(), approved_by="human"
    )
    checked = service.evaluate_risk(
        result,
        account=AccountState(cash=Decimal("1000"), as_of=timestamp),
        market=RiskMarketState(marks={btc_usdt.canonical_id: Decimal("100")}),
        policy=policy,
        evaluation_at=timestamp + timedelta(hours=1),
    )
    assert checked.risk_decision is not None
    assert checked.risk_decision.reasons == ("decision_expired",)
