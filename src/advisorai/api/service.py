"""End-to-end mission service: evidence can produce a target, never an order."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

from advisorai.agents import (
    EvidenceCouncil,
    EvidenceGraph,
    MissionRequest,
    MissionRouter,
    run_adaptive_waves,
)
from advisorai.agents.fusion import DecisionBundle
from advisorai.contracts import RiskDecision, RiskPolicy, Snapshot, TargetPortfolio
from advisorai.execution import (
    AccountState,
    RiskKernel,
    RiskMarketState,
    RiskRequest,
    TargetPortfolioBuilder,
)
from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers


@dataclass(frozen=True, slots=True)
class DecisionPipelineResult:
    decision: DecisionBundle
    risk_decision: RiskDecision | None


class AdvisorService:
    """The API owns missions and approvals; deterministic controls own risk."""

    def __init__(self, council: EvidenceCouncil, ledgers: SqliteLedgers | None = None) -> None:
        self.ledgers = ledgers
        self.router = MissionRouter(ledgers)
        self.council = council

    def build_decision(
        self,
        *,
        request: MissionRequest,
        snapshot: Snapshot,
        account: AccountState,
        market: RiskMarketState,
        targets,
        risk_policy_version: str,
    ) -> DecisionPipelineResult:
        routed = self.router.route(request)
        graph = EvidenceGraph()
        role_results = run_adaptive_waves(
            council=self.council,
            snapshot=snapshot,
            mission=routed,
            graph=graph,
            initial_roles=("data_verifier", "technical_flow"),
            optional_roles=(
                "derivatives_regime",
                "news_event",
                "skeptic_base_rate",
                "risk_opportunity",
                "synthesizer",
            ),
        )
        gate = graph.gate(
            minimum_source_families=2,
            minimum_factor_families=3,
            cutoff=snapshot.as_of,
        )
        target: TargetPortfolio = TargetPortfolioBuilder(no_trade_band=Decimal("0")).build(
            snapshot=snapshot,
            account=account,
            targets=targets,
            marks=market.marks,
            risk_constraints_version=risk_policy_version,
        )
        dissent = tuple(item for role_result in role_results for item in role_result.dissent)
        decision = DecisionBundle(
            mission_id=request.mission_id,
            snapshot_id=snapshot.artifact_id,
            target_portfolio=target,
            evidence_ids=tuple(node.evidence.artifact_id for node in graph.nodes()),
            consensus=("evidence_gate_passed" if gate.passed else "evidence_gate_not_passed"),
            strongest_dissent=dissent,
            missing_evidence=gate.reasons,
            confidence=Decimal("0.8") if gate.passed else Decimal("0"),
            abstained=not gate.passed,
            created_at=snapshot.as_of,
            expires_at=snapshot.as_of + timedelta(hours=1),
            gate=gate,
        )
        if self.ledgers is not None:
            for role_result in role_results:
                if role_result.agent_run is None:
                    continue
                self.ledgers.append(
                    LedgerEvent(
                        namespace=LedgerNamespace.MISSION,
                        event_type="agent_run_recorded",
                        idempotency_key=f"agent-run:{role_result.agent_run.artifact_id}",
                        payload={
                            "run": role_result.agent_run.model_dump(mode="json", round_trip=True)
                        },
                    )
                )
            self.ledgers.append(
                LedgerEvent(
                    namespace=LedgerNamespace.MISSION,
                    event_type="decision_built",
                    idempotency_key=(f"decision:{request.mission_id}:{decision.canonical_hash()}"),
                    payload={"decision": decision.model_dump(mode="json", round_trip=True)},
                )
            )
        return DecisionPipelineResult(decision=decision, risk_decision=None)

    def evaluate_risk(
        self,
        result: DecisionPipelineResult,
        *,
        account: AccountState,
        market: RiskMarketState,
        policy: RiskPolicy,
        evaluation_at: datetime | None = None,
    ) -> DecisionPipelineResult:
        # A target is still a valid research artifact when evidence is
        # insufficient, but it can never reach the risk-approved path. Keep
        # the rejection typed and auditable instead of relying on callers to
        # remember the evidence gate themselves.
        if result.decision.abstained or not result.decision.gate.passed:
            account_hash = account.snapshot().state_hash
            market_hash = market.effective_hash
            state_hash = sha256(f"{account_hash}:{market_hash}".encode()).hexdigest()
            return DecisionPipelineResult(
                decision=result.decision,
                risk_decision=RiskDecision(
                    target_portfolio_id=result.decision.target_portfolio.artifact_id,
                    risk_policy_id=policy.artifact_id,
                    outcome="rejected",
                    authoritative_state_hash=state_hash,
                    reasons=(
                        "evidence_gate_not_passed",
                        *result.decision.gate.reasons,
                    ),
                ),
            )
        if evaluation_at is None:
            decision_cutoff = account.as_of.astimezone(UTC)
        else:
            if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
                raise ValueError("risk evaluation timestamp must include a timezone")
            decision_cutoff = evaluation_at.astimezone(UTC)
        if result.decision.expires_at <= decision_cutoff:
            account_hash = account.snapshot().state_hash
            state_hash = sha256(f"{account_hash}:{market.effective_hash}".encode()).hexdigest()
            return DecisionPipelineResult(
                decision=result.decision,
                risk_decision=RiskDecision(
                    target_portfolio_id=result.decision.target_portfolio.artifact_id,
                    risk_policy_id=policy.artifact_id,
                    outcome="rejected",
                    authoritative_state_hash=state_hash,
                    reasons=("decision_expired",),
                ),
            )
        risk_decision = RiskKernel().evaluate(
            request=RiskRequest(
                target=result.decision.target_portfolio,
                account=account,
                market=market,
                policy=policy,
            )
        )
        return DecisionPipelineResult(decision=result.decision, risk_decision=risk_decision)
