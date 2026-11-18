"""Agent workflow: Scout → Librarian → experiment worker on one registry.

Thin orchestration over the existing ``ResearchCoordinator``, ``ResearchRegistry``,
``ResearchMetadataScout``, and ``ResearchRetrieval``. Each step is a durable
coordinator job, so restart, cancellation, and checkpoints behave exactly as the
tested coordinator defines. No role hard-codes an instrument or data source:
jobs carry their parameters in the durable detail payload.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from advisorai.ledger import SqliteLedgers
from advisorai.phase4.signal_policy import SignalPolicyFamily, SignalPolicySpec
from advisorai.research import (
    HypothesisCard,
    JobState,
    PaperCard,
    ResearchCoordinator,
    ResearchJob,
    ResearchMetadataScout,
    ResearchRegistry,
    ResearchRetrieval,
    ResearchRole,
    ResearchSourceUse,
)
from advisorai.research.replay import ExposureSpec, run_replay

WORKFLOW_VERSION = "advisorai.research.agent-workflow.v1"


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    job_id: UUID
    role: ResearchRole
    state: JobState
    detail: str


@dataclass(frozen=True, slots=True)
class ExperimentOutcome:
    experiment_id: UUID
    symbol: str
    net_bps: str
    baseline_net_bps: str
    succeeded: bool
    detail: str


@dataclass(frozen=True, slots=True)
class WorkflowReport:
    version: str
    steps: tuple[WorkflowStep, ...]
    outcomes: tuple[ExperimentOutcome, ...]
    prior_experiments: tuple[ExperimentOutcome, ...]


def _step(job: ResearchJob) -> WorkflowStep:
    return WorkflowStep(job_id=job.job_id, role=job.role, state=job.state, detail=job.detail)


def _outcome_key(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class ResearchWorkflow:
    """Durable three-role workflow bound to one registry and one coordinator."""

    def __init__(
        self,
        *,
        registry: ResearchRegistry,
        coordinator: ResearchCoordinator,
        scout: ResearchMetadataScout,
        snapshot_root: Path,
        state_dir: Path,
    ) -> None:
        self.registry = registry
        self.coordinator = coordinator
        self.scout = scout
        self.snapshot_root = snapshot_root
        self.state_dir = state_dir

    # -- Step 1: Scout ------------------------------------------------------
    def scout_paper(
        self,
        *,
        source_url: str,
        claimed_result: str,
        relevance: str,
        execution_budget_seconds: float = 45.0,
    ) -> tuple[ResearchJob, PaperCard | None]:
        """Fetch one citation record; duplicate fetches reuse the stored card."""
        paper = PaperCard(
            source_url=source_url,
            claimed_result=claimed_result,
            relevance=relevance,
            requested_use=ResearchSourceUse.CITATION_ONLY,
        )
        job = self.scout.submit(paper, execution_budget_seconds=execution_budget_seconds)
        run = self.scout.run(job.job_id)
        final = next(item for item in self.coordinator.jobs() if item.job_id == job.job_id)
        return final, run.paper

    # -- Step 2: Librarian --------------------------------------------------
    def librarian_hypothesis(
        self,
        *,
        paper: PaperCard,
        economic_rationale: str,
        universe: str,
        horizon: str,
        baseline: str,
        preregistered_plan: str,
        owner: str,
        expires_at: datetime,
        failure_modes: tuple[str, ...] = (),
        point_in_time_inputs: tuple[str, ...] = (),
        budget: str = "offline CPU only; one trial",
    ) -> tuple[ResearchJob, HypothesisCard]:
        """Record a sourced hypothesis plus its prior-experiment context."""
        retrieval = ResearchRetrieval(self.registry.database_path)
        retrieval.rebuild()
        prior = retrieval.search(f"{universe} {horizon}", record_type="experiment")
        detail = json.dumps(
            {
                "paper_artifact_id": str(paper.artifact_id),
                "prior_experiments_considered": len(prior),
                "workflow": WORKFLOW_VERSION,
            },
            sort_keys=True,
        )
        job = self.coordinator.submit(ResearchRole.LIBRARIAN, detail=detail)
        stored: dict[str, HypothesisCard] = {}

        def worker(running: ResearchJob, cancelled: Any) -> str:
            if cancelled():
                return "cancelled_before_hypothesis_write"
            candidate = HypothesisCard(
                economic_rationale=economic_rationale,
                universe=universe,
                horizon=horizon,
                point_in_time_inputs=point_in_time_inputs,
                baseline=baseline,
                preregistered_plan=preregistered_plan,
                failure_modes=failure_modes,
                owner=owner,
                budget=budget,
                expires_at=expires_at,
                paper_card_ids=(paper.artifact_id,),
            )
            accepted = self.registry.store(candidate, record_type="hypothesis")
            self.registry.link(paper.artifact_id, accepted.artifact_id, "paper_supports_hypothesis")
            stored["hypothesis"] = accepted
            self.coordinator.checkpoint(job.job_id, detail=f"hypothesis={accepted.artifact_id}")
            return f"hypothesis_artifact_id={accepted.artifact_id};prior={len(prior)}"

        completed = self.coordinator.run(job.job_id, worker)
        hypothesis = stored.get("hypothesis")
        if completed.state is not JobState.COMPLETED or hypothesis is None:
            raise RuntimeError(f"librarian step did not complete: {completed.state.value}")
        return completed, hypothesis

    # -- Step 3: experiment worker ------------------------------------------
    def run_experiments(
        self,
        *,
        hypothesis: HypothesisCard,
        exposures: tuple[ExposureSpec, ...],
        policy_id: str,
        environment_hash: str,
        require_all_windows: bool = True,
    ) -> tuple[ResearchJob, tuple[ExperimentOutcome, ...]]:
        """Replay each exposure; duplicates reuse stored experiments."""
        fingerprint = _outcome_key(
            {
                "hypothesis": str(hypothesis.artifact_id),
                "policy": policy_id,
                "exposures": sorted(item.symbol for item in exposures),
                "workflow": WORKFLOW_VERSION,
            }
        )
        job = self.coordinator.submit(ResearchRole.EXPERIMENT_CONDUCTOR, detail=fingerprint)
        outcomes: dict[str, tuple[ExperimentOutcome, ...]] = {}

        def worker(running: ResearchJob, cancelled: Any) -> str:
            if cancelled():
                return "cancelled_before_replay"
            self.coordinator.checkpoint(job.job_id, detail=f"replay_started:{fingerprint}")
            spec = SignalPolicySpec(policy_id=policy_id, family=SignalPolicyFamily.SIGN_ONLY)
            report = run_replay(
                registry=self.registry,
                snapshot_root=self.snapshot_root,
                exposures=exposures,
                spec=spec,
                hypothesis_id=hypothesis.artifact_id,
                environment_hash=environment_hash,
                require_all_windows=require_all_windows,
            )
            results: list[ExperimentOutcome] = []
            for item in report.exposures:
                results.append(
                    ExperimentOutcome(
                        experiment_id=item.experiment_id,
                        symbol=item.symbol,
                        net_bps=f"{item.net_bps:.2f}",
                        baseline_net_bps=f"{item.baseline_net_bps:.2f}",
                        succeeded=True,
                        detail=f"trades={item.trade_count}",
                    )
                )
            for failure in report.failed:
                symbol = failure.split(":", 1)[0]
                results.append(
                    ExperimentOutcome(
                        experiment_id=hypothesis.artifact_id,
                        symbol=symbol,
                        net_bps="n/a",
                        baseline_net_bps="n/a",
                        succeeded=False,
                        detail=failure,
                    )
                )
            outcomes["results"] = tuple(results)
            self.coordinator.checkpoint(
                job.job_id,
                detail=f"replay_done:{len(report.exposures)}:{len(report.failed)}",
            )
            return f"experiments={len(report.exposures)} failed={len(report.failed)}"

        completed = self.coordinator.run(job.job_id, worker)
        results = outcomes.get("results", ())
        if completed.state is not JobState.COMPLETED:
            raise RuntimeError(f"experiment step did not complete: {completed.state.value}")
        return completed, results

    # -- Outcome memory ------------------------------------------------------
    def prior_experiments(self, query: str, *, limit: int = 20) -> tuple[ExperimentOutcome, ...]:
        """Retrieve successful and failed prior experiments for the next job."""
        retrieval = ResearchRetrieval(self.registry.database_path)
        retrieval.rebuild()
        hits = retrieval.search(query, record_type="experiment", limit=limit)
        if not hits:
            # Experiment FTS titles carry trial counts and metric bodies carry
            # numerics, so fall back to the full experiment set when the
            # terms do not match.
            hits = retrieval.search("experiment", record_type="experiment", limit=limit)
        outcomes: list[ExperimentOutcome] = []
        for hit in hits:
            record = self.registry.get(hit.artifact_id)
            if record is None:
                continue
            metrics = dict(getattr(record, "metrics", {}) or {})
            windows = tuple(getattr(record, "windows", ()) or ())
            symbol = windows[0].split(":")[0] if windows else "unknown"
            outcomes.append(
                ExperimentOutcome(
                    experiment_id=hit.artifact_id,
                    symbol=symbol,
                    net_bps=str(metrics.get("net_bps", "n/a")),
                    baseline_net_bps=str(metrics.get("baseline_net_bps", "n/a")),
                    succeeded=True,
                    detail=f"trial_count={getattr(record, 'trial_count', '?')}",
                )
            )
        # Failed runs are first-class memory: surface interrupted/cancelled
        # experiment jobs alongside stored experiments.
        for job in self.coordinator.jobs():
            if job.role is not ResearchRole.EXPERIMENT_CONDUCTOR:
                continue
            if job.state not in {JobState.INTERRUPTED, JobState.CANCELLED}:
                continue
            outcomes.append(
                ExperimentOutcome(
                    experiment_id=job.job_id,
                    symbol="unfinished",
                    net_bps="n/a",
                    baseline_net_bps="n/a",
                    succeeded=False,
                    detail=f"{job.state.value}:{job.detail[:160]}",
                )
            )
        return tuple(outcomes)

    def decay_status(self, *, now: datetime | None = None) -> tuple[dict[str, Any], ...]:
        """Flag expired hypotheses and experiments older than their window."""
        moment = (now or datetime.now(UTC)).astimezone(UTC)
        flags: list[dict[str, Any]] = []
        for job in self.coordinator.jobs():
            if job.role is not ResearchRole.LIBRARIAN or job.state is not JobState.COMPLETED:
                continue
            marker = "hypothesis_artifact_id="
            if marker not in job.detail:
                continue
            artifact_id = job.detail.split(marker, 1)[1].split(";", 1)[0]
            try:
                record = self.registry.get(UUID(artifact_id))
            except ValueError:
                continue
            if record is None or not hasattr(record, "expires_at"):
                continue
            expires = record.expires_at
            if isinstance(expires, datetime) and expires.astimezone(UTC) <= moment:
                flags.append(
                    {
                        "artifact_id": artifact_id,
                        "kind": "hypothesis_expired",
                        "expires_at": expires.isoformat(),
                    }
                )
        return tuple(flags)


def open_workflow_state(
    state_dir: Path,
) -> tuple[ResearchRegistry, SqliteLedgers, ResearchCoordinator]:
    """Open the isolated workflow state (never the trading checkout's state)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    ledgers = SqliteLedgers(state_dir / "research-jobs.sqlite3")
    registry = ResearchRegistry(state_dir / "research-registry.sqlite3")
    coordinator = ResearchCoordinator(ledgers, max_workers=2)
    return registry, ledgers, coordinator


__all__ = [
    "WORKFLOW_VERSION",
    "ExperimentOutcome",
    "ResearchWorkflow",
    "WorkflowReport",
    "WorkflowStep",
    "open_workflow_state",
]
