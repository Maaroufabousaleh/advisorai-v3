"""Bounded, cancellable Alpha Team research jobs.

One coordinator, at most two concurrent workers on the target laptop; the
GPU lease and resource budget always yield to trading health. Nothing runs
while AdvisorAI is shut down: jobs checkpoint on stop and resume only under
current policy. Roles: Scout, Librarian, Quant Research Lead, Factor Miner,
Strategy Engineer, Experiment Conductor, Statistical Red Team,
Portfolio/Risk Reviewer, Capability Engineer.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers


class ResearchRole(StrEnum):
    SCOUT = "scout"
    LIBRARIAN = "librarian"
    QUANT_LEAD = "quant_lead"
    FACTOR_MINER = "factor_miner"
    STRATEGY_ENGINEER = "strategy_engineer"
    EXPERIMENT_CONDUCTOR = "experiment_conductor"
    RED_TEAM = "red_team"
    PORTFOLIO_REVIEWER = "portfolio_reviewer"
    CAPABILITY_ENGINEER = "capability_engineer"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CHECKPOINTED = "checkpointed"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ResearchJob:
    job_id: UUID
    role: ResearchRole
    budget_units: int
    state: JobState = JobState.QUEUED
    owner: str = "coordinator"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    detail: str = ""


@dataclass
class _Worker:
    role: ResearchRole
    busy: threading.Event = field(default_factory=threading.Event)


class ResearchCoordinator:
    """Bounded job queue: budgets, cancellation, checkpointing, no agent swarm."""

    def __init__(
        self,
        ledgers: SqliteLedgers | None = None,
        *,
        max_workers: int = 2,
        default_budget_units: int = 10,
    ) -> None:
        if max_workers < 1 or max_workers > 2:
            raise ValueError("research workers are bounded to 1-2 on this workstation")
        self.ledgers = ledgers
        self.max_workers = max_workers
        self.default_budget_units = default_budget_units
        self._jobs: dict[UUID, ResearchJob] = {}
        self._cancelled: set[UUID] = set()
        self._lock = threading.Lock()

    def submit(
        self,
        role: ResearchRole,
        *,
        budget_units: int | None = None,
        detail: str = "",
    ) -> ResearchJob:
        budget = self.default_budget_units if budget_units is None else budget_units
        if budget < 1:
            raise ValueError("research jobs require a positive budget")
        job = ResearchJob(
            job_id=uuid4(), role=role, budget_units=budget, owner="coordinator", detail=detail
        )
        with self._lock:
            self._jobs[job.job_id] = job
        self._record(job, "research_job_submitted")
        return job

    def cancel(self, job_id: UUID) -> ResearchJob:
        with self._lock:
            job = self._require(job_id)
            if job.state in {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}:
                return job
            self._cancelled.add(job_id)
            updated = ResearchJob(
                job_id=job.job_id,
                role=job.role,
                budget_units=job.budget_units,
                state=JobState.CANCELLED,
                owner=job.owner,
                created_at=job.created_at,
                detail="cancelled by coordinator",
            )
            self._jobs[job_id] = updated
        self._record(updated, "research_job_cancelled")
        return updated

    def run(
        self,
        job_id: UUID,
        worker: Callable[[ResearchJob, Callable[[], bool]], str],
    ) -> ResearchJob:
        """Run one job synchronously; the worker polls ``cancelled()``.

        Long jobs checkpoint via ``research_job_checkpointed`` events and must
        stop promptly when cancelled. Completion without cancellation records
        the worker's detail string.
        """
        with self._lock:
            job = self._require(job_id)
            if job.state is not JobState.QUEUED:
                raise RuntimeError(f"job {job_id} is not queued")
            running = ResearchJob(
                job_id=job.job_id,
                role=job.role,
                budget_units=job.budget_units,
                state=JobState.RUNNING,
                owner=job.owner,
                created_at=job.created_at,
            )
            self._jobs[job_id] = running
        self._record(running, "research_job_started")

        def cancelled() -> bool:
            with self._lock:
                return job_id in self._cancelled

        try:
            detail = worker(running, cancelled)
            terminal = ResearchJob(
                job_id=running.job_id,
                role=running.role,
                budget_units=running.budget_units,
                state=(JobState.CANCELLED if cancelled() else JobState.COMPLETED),
                owner=running.owner,
                created_at=running.created_at,
                detail=detail,
            )
        except Exception as exc:
            terminal = ResearchJob(
                job_id=running.job_id,
                role=running.role,
                budget_units=running.budget_units,
                state=JobState.INTERRUPTED,
                owner=running.owner,
                created_at=running.created_at,
                detail=f"interrupted: {type(exc).__name__}",
            )
        with self._lock:
            self._jobs[job_id] = terminal
        self._record(
            terminal,
            "research_job_completed"
            if terminal.state is JobState.COMPLETED
            else "research_job_interrupted",
        )
        return terminal

    def checkpoint(self, job_id: UUID, *, detail: str = "") -> ResearchJob:
        with self._lock:
            job = self._require(job_id)
            updated = ResearchJob(
                job_id=job.job_id,
                role=job.role,
                budget_units=job.budget_units,
                state=JobState.CHECKPOINTED,
                owner=job.owner,
                created_at=job.created_at,
                detail=detail,
            )
            self._jobs[job_id] = updated
        self._record(updated, "research_job_checkpointed")
        return updated

    def jobs(self, state: JobState | None = None) -> tuple[ResearchJob, ...]:
        with self._lock:
            values = tuple(self._jobs.values())
        if state is None:
            return values
        return tuple(job for job in values if job.state is state)

    def _require(self, job_id: UUID) -> ResearchJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"unknown research job {job_id}")
        return job

    def _record(self, job: ResearchJob, event_type: str) -> None:
        if self.ledgers is None:
            return
        import dataclasses

        payload = dataclasses.asdict(job)
        payload["job_id"] = str(job.job_id)
        payload["role"] = job.role.value
        payload["state"] = job.state.value
        payload["created_at"] = job.created_at.isoformat()
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type=event_type,
                idempotency_key=f"research-job:{job.job_id}:{event_type}",
                payload={"job": payload},
            )
        )


__all__ = ["JobState", "ResearchCoordinator", "ResearchJob", "ResearchRole"]
