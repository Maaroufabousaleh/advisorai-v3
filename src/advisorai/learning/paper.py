"""Durable paper outcomes, incidents, deterministic replays, and scorecards."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers
from advisorai.memory.scorecards import Scorecard, ScorecardStore
from advisorai.observability.incidents import Incident, IncidentLedger, IncidentSeverity


class ProblemKind(StrEnum):
    DATA_AVAILABILITY = "data_availability_quality"
    LINEAGE = "lineage"
    CALIBRATION = "calibration"
    EVIDENCE_INDEPENDENCE = "evidence_independence"
    TARGET_CONSTRUCTION = "target_construction"
    RISK_REJECTION = "risk_rejection"
    EXECUTION_COST = "execution_cost"
    VENUE_API = "venue_api"
    RECONCILIATION = "reconciliation"
    RESOURCES = "resources"
    OPERATOR_PROCESS = "operator_process"


class PaperDecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID
    snapshot_id: UUID
    quality_report_ids: tuple[UUID, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    forecast_ids: tuple[UUID, ...] = ()
    target_id: UUID | None = None
    risk_decision_id: UUID | None = None
    order_ids: tuple[UUID, ...] = ()
    fill_ids: tuple[UUID, ...] = ()
    reconciliation_id: UUID | None = None
    tca_id: UUID | None = None
    attribution_id: UUID | None = None
    subject: str = Field(min_length=1)
    subject_version: str = Field(min_length=1)
    role: str = Field(default="synthesizer", min_length=1)
    asset: str = Field(min_length=1)
    horizon: str = Field(min_length=1)
    regime: str = Field(default="unknown", min_length=1)
    cutoff: datetime
    horizon_end: datetime
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("cutoff", "horizon_end", "recorded_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("paper decision timestamps must include a timezone")
        return value.astimezone(UTC)

    @field_validator("subject", "subject_version", "role", "asset", "horizon", "regime")
    @classmethod
    def nonblank(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def horizon_after_cutoff(self) -> PaperDecisionRecord:
        if self.horizon_end <= self.cutoff:
            raise ValueError("forecast horizon must close after decision cutoff")
        return self


class LearningProblem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    problem_id: UUID = Field(default_factory=uuid4)
    decision_id: UUID
    kind: ProblemKind
    severity: IncidentSeverity = IncidentSeverity.MEDIUM
    summary: str = Field(min_length=3)
    evidence_ids: tuple[UUID, ...] = ()
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    incident_id: UUID | None = None
    replay_required: bool = True
    replay_id: UUID | None = None
    corrective_test: str | None = None

    @field_validator("detected_at")
    @classmethod
    def detected_at_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("problem detected_at must include a timezone")
        return value.astimezone(UTC)

    @field_validator("summary")
    @classmethod
    def summary_nonblank(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def replay_contract(self) -> LearningProblem:
        if self.replay_required and self.replay_id is None and self.corrective_test is not None:
            raise ValueError("a corrective test cannot be claimed before replay")
        return self


class ReplayResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    replay_id: UUID = Field(default_factory=uuid4)
    problem_id: UUID
    passed: bool
    output_hash: str = Field(min_length=64, max_length=64)
    regression_test: str = Field(min_length=1)
    details: tuple[str, ...] = ()
    replayed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("output_hash")
    @classmethod
    def sha256(cls, value: str) -> str:
        normalized = value.lower().strip()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("replay output_hash must be a lowercase SHA-256 digest")
        return normalized

    @field_validator("replayed_at")
    @classmethod
    def replayed_at_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("replay timestamp must include a timezone")
        return value.astimezone(UTC)


class PaperOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: UUID
    as_of: datetime
    forecast_value: Decimal | None = None
    realized_value: Decimal | None = None
    net_utility: Decimal
    cost_usd: Decimal = Field(ge=Decimal("0"))
    fill_quality: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    data_reliability: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    latency_ms: int = Field(ge=0)
    failure_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))

    @field_validator("as_of")
    @classmethod
    def as_of_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("outcome timestamp must include a timezone")
        return value.astimezone(UTC)

    @field_validator(
        "forecast_value",
        "realized_value",
        "net_utility",
        "cost_usd",
        "fill_quality",
        "data_reliability",
        "failure_rate",
    )
    @classmethod
    def finite_decimal(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("outcome metrics must be finite")
        return value


class PaperLearningLoop:
    """A no-autopromotion learning ledger.

    It records failures and scorecards, but never changes prompts, models,
    routing, or risk policy.  A human-owned challenger process must consume
    these records and pass the existing replay and phase gates.
    """

    _incident_kinds = frozenset(
        {
            ProblemKind.RISK_REJECTION,
            ProblemKind.VENUE_API,
            ProblemKind.RECONCILIATION,
            ProblemKind.RESOURCES,
            ProblemKind.OPERATOR_PROCESS,
        }
    )

    def __init__(self, ledgers: SqliteLedgers, *, scorecards_path: Path | None = None) -> None:
        self.ledgers = ledgers
        self.incidents = IncidentLedger(ledgers)
        self.scorecards = ScorecardStore(
            scorecards_path or ledgers.database_path.with_name("scorecards.sqlite3")
        )
        self._decisions: dict[UUID, PaperDecisionRecord] = {}
        self._problems: dict[UUID, LearningProblem] = {}
        self._replays: dict[UUID, ReplayResult] = {}
        self._outcomes: dict[tuple[UUID, datetime], tuple[str, Scorecard]] = {}
        self._hydrate()

    def _hydrate(self) -> None:
        for event in self.ledgers.events(LedgerNamespace.MISSION):
            if event.event_type == "paper_decision_chain":
                payload = event.payload.get("decision")
                if isinstance(payload, dict):
                    record = PaperDecisionRecord.model_validate(payload)
                    self._decisions[record.decision_id] = record
            elif event.event_type == "paper_learning_problem":
                payload = event.payload.get("problem")
                if isinstance(payload, dict):
                    problem = LearningProblem.model_validate(payload)
                    self._problems[problem.problem_id] = problem
        for event in self.ledgers.events(LedgerNamespace.MODEL):
            if event.event_type != "paper_scorecard_recorded":
                continue
            payload = event.payload
            raw_scorecard = payload.get("scorecard")
            decision_id = payload.get("decision_id")
            raw_outcome = payload.get("outcome")
            if not isinstance(raw_scorecard, dict) or not isinstance(decision_id, str):
                continue
            scorecard = Scorecard.model_validate(raw_scorecard)
            if isinstance(raw_outcome, dict):
                outcome = PaperOutcome.model_validate(raw_outcome)
                self._outcomes[(outcome.decision_id, outcome.as_of)] = (
                    self._outcome_hash(outcome),
                    scorecard,
                )
        for event in self.ledgers.events(LedgerNamespace.MISSION):
            if event.event_type != "paper_replay_recorded":
                continue
            raw_replay = event.payload.get("replay")
            if isinstance(raw_replay, dict):
                replay = ReplayResult.model_validate(raw_replay)
                self._replays[replay.problem_id] = replay

    def record_decision(self, decision: PaperDecisionRecord) -> PaperDecisionRecord:
        prior = self._decisions.get(decision.decision_id)
        if prior is not None and prior != decision:
            raise ValueError("paper decision ID is immutable")
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type="paper_decision_chain",
                idempotency_key=f"paper-decision:{decision.decision_id}",
                payload={"decision": decision.model_dump(mode="json", round_trip=True)},
            )
        )
        self._decisions[decision.decision_id] = decision
        return decision

    def record_problem(
        self, problem: LearningProblem, *, create_incident: bool | None = None
    ) -> LearningProblem:
        decision = self._decisions.get(problem.decision_id)
        if decision is None:
            raise KeyError(f"unknown paper decision {problem.decision_id}")
        existing = self._problems.get(problem.problem_id)
        if existing is not None and existing != problem:
            raise ValueError("paper learning problem ID is immutable")
        should_incident = (
            create_incident if create_incident is not None else problem.kind in self._incident_kinds
        )
        if should_incident and problem.incident_id is None:
            incident = Incident(
                severity=problem.severity,
                owner="paper-learning",
                summary=problem.summary,
                runbook=f"docs/runbooks/paper-problem-{problem.kind.value}.md",
                evidence_ids=problem.evidence_ids,
                containment="keep paper runtime fail-closed; do not promote a challenger",
                reconciliation="pending",
            )
            self.incidents.record(incident)
            problem = problem.model_copy(update={"incident_id": incident.incident_id})
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type="paper_learning_problem",
                idempotency_key=f"paper-problem:{problem.problem_id}",
                payload={"problem": problem.model_dump(mode="json", round_trip=True)},
            )
        )
        self._problems[problem.problem_id] = problem
        return problem

    def replay(
        self, problem_id: UUID, runner: Callable[[PaperDecisionRecord], Mapping[str, object]]
    ) -> ReplayResult:
        problem = self._problems.get(problem_id)
        if problem is None:
            raise KeyError(str(problem_id))
        decision = self._decisions[problem.decision_id]
        output = runner(decision)
        encoded = json.dumps(output, sort_keys=True, separators=(",", ":"), default=str).encode()
        output_hash = hashlib.sha256(encoded).hexdigest()
        replay_id = uuid5(NAMESPACE_URL, f"advisorai-v3/paper-replay/{problem_id}/{output_hash}")
        prior_replay = self._replays.get(problem_id)
        if prior_replay is not None and prior_replay.output_hash != output_hash:
            raise ValueError("a paper problem cannot be replayed with a different output")
        passed = bool(output.get("passed", False))
        regression_test = str(output.get("regression_test", "tests/replay/test_paper_problem.py"))
        result = ReplayResult(
            replay_id=replay_id,
            problem_id=problem_id,
            passed=passed,
            output_hash=output_hash,
            regression_test=regression_test,
            details=tuple(str(item) for item in output.get("details", ()) if str(item).strip()),
            replayed_at=decision.recorded_at,
        )
        updated_problem = problem.model_copy(
            update={
                "replay_id": result.replay_id,
                "replay_required": False,
                "corrective_test": regression_test,
            }
        )
        self._problems[problem_id] = updated_problem
        self._replays[problem_id] = result
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type="paper_replay_recorded",
                idempotency_key=f"paper-replay:{result.replay_id}",
                payload={
                    "replay": result.model_dump(mode="json", round_trip=True),
                    "problem": updated_problem.model_dump(mode="json", round_trip=True),
                },
            )
        )
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type="paper_learning_problem",
                idempotency_key=f"paper-problem-replayed:{problem_id}:{result.replay_id}",
                payload={"problem": updated_problem.model_dump(mode="json", round_trip=True)},
            )
        )
        return result

    def record_outcome(self, outcome: PaperOutcome) -> Scorecard:
        decision = self._decisions.get(outcome.decision_id)
        if decision is None:
            raise KeyError(f"unknown paper decision {outcome.decision_id}")
        if outcome.as_of < decision.horizon_end:
            raise ValueError("scorecards cannot be recorded before the forecast horizon closes")
        outcome_key = (outcome.decision_id, outcome.as_of)
        outcome_hash = self._outcome_hash(outcome)
        prior_outcome = self._outcomes.get(outcome_key)
        if prior_outcome is not None:
            if prior_outcome[0] != outcome_hash:
                raise ValueError("paper outcome is immutable for a decision and as_of")
            return prior_outcome[1]
        calibration = Decimal("1")
        if outcome.forecast_value is not None and outcome.realized_value is not None:
            error = abs(outcome.forecast_value - outcome.realized_value)
            calibration = max(Decimal("0"), Decimal("1") - error)
        scorecard = Scorecard(
            scorecard_id=uuid5(
                NAMESPACE_URL, f"advisorai-v3/paper-scorecard/{outcome.decision_id}/{outcome_hash}"
            ),
            subject=decision.subject,
            subject_version=decision.subject_version,
            role=decision.role,
            asset=decision.asset,
            horizon=decision.horizon,
            regime=decision.regime,
            factual_precision=calibration,
            calibration=calibration,
            abstention_quality=outcome.data_reliability,
            contradiction_detection=outcome.fill_quality,
            net_utility=outcome.net_utility,
            latency_ms=outcome.latency_ms,
            api_cost_usd=outcome.cost_usd,
            failure_rate=outcome.failure_rate,
            eligible_for_routing=outcome.failure_rate == 0
            and outcome.data_reliability >= Decimal("0.8"),
            recorded_at=outcome.as_of,
        )
        stored = self.scorecards.append(scorecard)
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MODEL,
                event_type="paper_scorecard_recorded",
                idempotency_key=f"paper-scorecard:{stored.scorecard_id}",
                payload={
                    "scorecard": stored.model_dump(mode="json", round_trip=True),
                    "decision_id": str(outcome.decision_id),
                    "outcome": outcome.model_dump(mode="json", round_trip=True),
                },
            )
        )
        self._outcomes[outcome_key] = (outcome_hash, stored)
        return stored

    @staticmethod
    def _outcome_hash(outcome: PaperOutcome) -> str:
        encoded = json.dumps(
            outcome.model_dump(mode="json", round_trip=True),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def decision(self, decision_id: UUID) -> PaperDecisionRecord | None:
        return self._decisions.get(decision_id)

    def problems(self) -> tuple[LearningProblem, ...]:
        return tuple(self._problems.values())


__all__ = [
    "LearningProblem",
    "PaperDecisionRecord",
    "PaperLearningLoop",
    "PaperOutcome",
    "ProblemKind",
    "ReplayResult",
]
