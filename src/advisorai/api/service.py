"""End-to-end mission service: evidence can produce a target, never an order."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from advisorai.agents import (
    EvidenceCouncil,
    EvidenceGraph,
    MissionRequest,
    MissionRouter,
    run_adaptive_waves,
)
from advisorai.agents.fusion import DecisionBundle
from advisorai.contracts import RiskDecision, RiskPolicy, Snapshot, TargetPortfolio
from advisorai.decision import (
    DecisionModelArtifact,
    DecisionModelInput,
    DecisionModelPort,
    DecisionProposal,
    DecisionStance,
    ReferenceDecisionAdapter,
)
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
    proposal: DecisionProposal | None = None


class AdvisorService:
    """The API owns missions and approvals; deterministic controls own risk."""

    def __init__(
        self,
        council: EvidenceCouncil,
        ledgers: SqliteLedgers | None = None,
        decision_model: DecisionModelPort | None = None,
    ) -> None:
        self.ledgers = ledgers
        self.router = MissionRouter(ledgers)
        self.council = council
        self.decision_model = decision_model

    def build_decision(
        self,
        *,
        request: MissionRequest,
        snapshot: Snapshot,
        account: AccountState,
        market: RiskMarketState,
        targets=None,
        risk_policy_version: str,
    ) -> DecisionPipelineResult:
        """Build an evidence-gated decision whose executable target is proposal-bound.

        The DecisionProposal is the sole authority for executable intent:
        LONG/SHORT/FLAT/ABSTAIN with calibrated confidence, evidence
        references, and expiry. The deterministic Portfolio Constructor and
        RiskKernel remain the sole trading authorities downstream. Any
        ABSTAIN, invalid, missing, stale, expired, or identity-mismatched
        proposal fails closed: the target is forced to current positions
        (no-trade) and the decision abstains. Caller-supplied targets are
        honored only when they exactly match the proposal stance mapping;
        otherwise they are discarded in favor of the proposal.
        """
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
        evidence_ids = tuple(node.evidence.artifact_id for node in graph.nodes())
        dissent = tuple(item for role_result in role_results for item in role_result.dissent)
        # Proposal first: it is the sole authority for executable intent.
        # The Portfolio Constructor below is deterministic and bound to it.
        proposal = self._build_proposal(
            request=request,
            snapshot=snapshot,
            gate=gate,
            evidence_ids=evidence_ids,
            dissent=dissent,
        )
        proposal_failure = self._proposal_failure_reason(
            proposal,
            request=request,
            snapshot=snapshot,
            evidence_ids=evidence_ids,
            evaluation_at=snapshot.as_of,
        )
        if proposal_failure is None:
            bound_targets = self._proposal_bound_targets(
                proposal, account=account, marks=market.marks
            )
        else:
            # Fail closed: force a no-trade target at current positions.
            bound_targets = {
                instrument: account.positions.get(instrument.canonical_id, Decimal("0"))
                for instrument in self._known_instruments(account, market)
            }
        target: TargetPortfolio = TargetPortfolioBuilder(no_trade_band=Decimal("0")).build(
            snapshot=snapshot,
            account=account,
            targets=bound_targets,
            marks=market.marks,
            risk_constraints_version=risk_policy_version,
        )
        stable_positions = tuple(
            position.model_copy(
                update={
                    "artifact_id": uuid5(
                        NAMESPACE_URL,
                        f"advisorai-v3/target-position/{request.mission_id}/{snapshot.artifact_id}/"
                        f"{position.instrument.canonical_id}/{position.target_quantity}",
                    )
                }
            )
            for position in target.positions
        )
        position_key = ",".join(
            f"{position.instrument.canonical_id}:{position.target_quantity}"
            for position in stable_positions
        )
        target = target.model_copy(
            update={
                "artifact_id": uuid5(
                    NAMESPACE_URL,
                    f"advisorai-v3/target/{request.mission_id}/{snapshot.artifact_id}/"
                    f"{target.risk_constraints_version}/{position_key}",
                ),
                "positions": stable_positions,
            }
        )
        abstained = not gate.passed or proposal_failure is not None
        missing = (*gate.reasons, *(() if proposal_failure is None else (proposal_failure,)))
        # The effective gate is evidence AND proposal validity. A proposal
        # failure fails the combined gate even when the evidence quorum
        # passed; the bundle invariant (passed gate cannot abstain) holds
        # because the recorded gate reflects the combined result.
        effective_gate = (
            gate
            if proposal_failure is None
            else gate.model_copy(
                update={
                    "passed": False,
                    "reasons": (*gate.reasons, proposal_failure),
                }
            )
        )
        decision = DecisionBundle(
            mission_id=request.mission_id,
            snapshot_id=snapshot.artifact_id,
            target_portfolio=target,
            evidence_ids=evidence_ids,
            consensus=(
                "evidence_gate_passed"
                if gate.passed and proposal_failure is None
                else "evidence_gate_not_passed"
            ),
            strongest_dissent=dissent,
            missing_evidence=missing,
            confidence=Decimal("0.8") if not abstained else Decimal("0"),
            abstained=abstained,
            created_at=snapshot.as_of,
            # The decision expiry is bounded by the proposal's, but an
            # already-expired (invalid) proposal still fails closed through
            # the recorded proposal_expired reason; the bundle expiry stays
            # structurally valid so the rejection itself remains auditable.
            expires_at=max(
                snapshot.as_of + timedelta(microseconds=1),
                min(snapshot.as_of + timedelta(hours=1), proposal.expires_at),
            ),
            gate=effective_gate,
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
            decision_hash = sha256(
                decision.model_dump_json(exclude={"created_at", "expires_at"}).encode()
            ).hexdigest()
            self.ledgers.append(
                LedgerEvent(
                    namespace=LedgerNamespace.MISSION,
                    event_type="decision_built",
                    idempotency_key=(f"decision:{request.mission_id}:{decision_hash}"),
                    payload={"decision": decision.model_dump(mode="json", round_trip=True)},
                )
            )
            self.ledgers.append(
                LedgerEvent(
                    namespace=LedgerNamespace.MISSION,
                    event_type="proposal_recorded",
                    idempotency_key=(f"proposal:{request.mission_id}:{proposal.canonical_hash()}"),
                    payload={"proposal": proposal.model_dump(mode="json", round_trip=True)},
                )
            )
        return DecisionPipelineResult(decision=decision, risk_decision=None, proposal=proposal)

    def _build_proposal(self, *, request, snapshot, gate, evidence_ids, dissent):
        """Typed proposal behind the port; never order or risk authority."""
        adapter = self.decision_model
        if adapter is None:
            fallback_artifact = DecisionModelArtifact(
                model_name="reference",
                adapter_name="reference-fallback",
                code_hash="0" * 64,
                config_hash="0" * 64,
                snapshot_id=snapshot.artifact_id,
            )
            adapter = ReferenceDecisionAdapter(fallback_artifact)
        try:
            return adapter.propose(
                DecisionModelInput(
                    mission_id=request.mission_id,
                    snapshot_id=snapshot.artifact_id,
                    evidence_ids=evidence_ids,
                    consensus="evidence_gate_passed" if gate.passed else "evidence_gate_not_passed",
                    missing_evidence=gate.reasons,
                    disagreement=dissent,
                    gate_passed=gate.passed,
                ),
                now=snapshot.as_of,
            )
        except Exception:
            fallback_artifact = DecisionModelArtifact(
                model_name="reference",
                adapter_name="reference-fallback",
                code_hash="0" * 64,
                config_hash="0" * 64,
                snapshot_id=snapshot.artifact_id,
            )
            return DecisionProposal(
                mission_id=request.mission_id,
                snapshot_id=snapshot.artifact_id,
                model_artifact_id=fallback_artifact.artifact_id,
                stance=DecisionStance.ABSTAIN,
                calibrated_confidence=Decimal("0"),
                score=Decimal("0"),
                horizon="1h",
                uncertainty=Decimal("1"),
                evidence_ids=(),
                missing_evidence=("decision_model_failure", *gate.reasons),
                disagreement=dissent,
                expires_at=snapshot.as_of + timedelta(hours=1),
                abstained=True,
                fallback_used=True,
                rationale="decision model failure converted to typed ABSTAIN",
            )

    @staticmethod
    def _proposal_failure_reason(
        proposal: DecisionProposal, *, request, snapshot, evidence_ids, evaluation_at: datetime
    ) -> str | None:
        """Fail closed on any ABSTAIN, invalid, missing, stale, or mismatched proposal."""
        if proposal.mission_id != request.mission_id:
            return "proposal_mission_mismatch"
        if proposal.snapshot_id != snapshot.artifact_id:
            return "proposal_snapshot_mismatch"
        if proposal.abstained or proposal.stance is DecisionStance.ABSTAIN:
            return "proposal_abstained"
        if proposal.expires_at <= evaluation_at:
            return "proposal_expired"
        if proposal.calibrated_confidence <= Decimal("0"):
            return "proposal_zero_confidence"
        if set(proposal.evidence_ids) != set(evidence_ids):
            return "proposal_evidence_mismatch"
        if proposal.uncertainty is not None and (
            proposal.uncertainty < Decimal("0") or proposal.uncertainty > Decimal("1")
        ):
            return "proposal_uncertainty_out_of_range"
        return None

    @staticmethod
    def _known_instruments(account: AccountState, market_or_marks):
        """Union of account and market instruments for fail-closed no-trade targets."""
        from advisorai.contracts import AssetClass, InstrumentIdentity

        marks = market_or_marks.marks if hasattr(market_or_marks, "marks") else market_or_marks
        seen: dict[str, object] = {}
        for canonical_id in (*account.positions, *account.marks, *marks):
            if canonical_id not in seen:
                seen[canonical_id] = InstrumentIdentity(
                    canonical_id=canonical_id, asset_class=AssetClass.CRYPTO, venue="paper"
                )
        return tuple(seen.values())

    def _proposal_bound_targets(self, proposal, *, account, marks) -> dict:
        """Map a validated proposal stance to executable target quantities.

        The mapping is intentionally coarse: LONG keeps/adds exposure only up
        to currently marked instruments, SHORT/FLAT flatten. Precise sizing
        remains the deterministic Portfolio Constructor's job under risk
        policy; this binding only guarantees the direction of intent comes
        from the proposal, never from caller-supplied targets.
        """
        if proposal.stance is DecisionStance.FLAT:
            return {}
        by_id = {item.canonical_id: item for item in self._known_instruments(account, marks)}
        direction = Decimal("1") if proposal.stance is DecisionStance.LONG else Decimal("-1")
        bound: dict = {}
        for canonical_id, mark in marks.items():
            if mark is None or canonical_id not in by_id:
                continue
            bound[by_id[canonical_id]] = (
                account.positions.get(canonical_id, Decimal("0")) + direction
            )
        return bound

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
                proposal=result.proposal,
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
                proposal=result.proposal,
            )
        risk_decision = RiskKernel().evaluate(
            request=RiskRequest(
                target=result.decision.target_portfolio,
                account=account,
                market=market,
                policy=policy,
            )
        )
        return DecisionPipelineResult(
            decision=result.decision, risk_decision=risk_decision, proposal=result.proposal
        )
