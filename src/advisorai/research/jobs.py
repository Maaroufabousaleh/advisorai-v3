"""Durable, bounded, cancellable Alpha Team research jobs.

Jobs are cooperative Python callables. The coordinator limits actual parallel
execution, passes a deadline-aware cancellation probe, rejects over-budget
results, retains checkpoint state across process restarts, and never admits
more than the configured bounded queue.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
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
    execution_budget_seconds: float = 60.0
    checkpoint_detail: str | None = None
    budget_consumed_seconds: float = 0.0
    running_since: datetime | None = None


class ResearchCoordinator:
    """One durable queue with a hard parallel-worker cap of one or two."""

    _terminal_states = frozenset({JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED})

    def __init__(
        self,
        ledgers: SqliteLedgers | None = None,
        *,
        max_workers: int = 2,
        default_budget_units: int = 10,
        default_execution_budget_seconds: float = 60.0,
        max_execution_budget_seconds: float = 3600.0,
        max_queued_jobs: int = 20,
    ) -> None:
        if max_workers < 1 or max_workers > 2:
            raise ValueError("research workers are bounded to 1-2 on this workstation")
        if default_budget_units < 1:
            raise ValueError("default research budget must be positive")
        if not 0 < default_execution_budget_seconds <= max_execution_budget_seconds:
            raise ValueError("default execution budget is outside the permitted range")
        if max_execution_budget_seconds > 3600 or max_queued_jobs < 1:
            raise ValueError("research queue and execution budgets exceed workstation limits")
        self.ledgers = ledgers
        self.max_workers = max_workers
        self.default_budget_units = default_budget_units
        self.default_execution_budget_seconds = default_execution_budget_seconds
        self.max_execution_budget_seconds = max_execution_budget_seconds
        self.max_queued_jobs = max_queued_jobs
        self._jobs: dict[UUID, ResearchJob] = {}
        self._cancelled: set[UUID] = set()
        self._lock = threading.RLock()
        self._slots = threading.BoundedSemaphore(max_workers)
        self._active_workers = 0
        self._peak_workers = 0
        self._hydrate()

    @property
    def active_workers(self) -> int:
        with self._lock:
            return self._active_workers

    @property
    def peak_workers(self) -> int:
        with self._lock:
            return self._peak_workers

    def submit(
        self,
        role: ResearchRole,
        *,
        budget_units: int | None = None,
        execution_budget_seconds: float | None = None,
        detail: str = "",
    ) -> ResearchJob:
        budget = self.default_budget_units if budget_units is None else budget_units
        seconds = (
            self.default_execution_budget_seconds
            if execution_budget_seconds is None
            else execution_budget_seconds
        )
        if budget < 1:
            raise ValueError("research jobs require a positive budget")
        if not 0 < seconds <= self.max_execution_budget_seconds:
            raise ValueError("research execution budget is outside the permitted range")
        job = ResearchJob(
            job_id=uuid4(),
            role=role,
            budget_units=budget,
            owner="coordinator",
            detail=detail,
            execution_budget_seconds=float(seconds),
        )
        with self._lock:
            outstanding = sum(
                item.state in {JobState.QUEUED, JobState.RUNNING, JobState.CHECKPOINTED}
                or (item.state is JobState.INTERRUPTED and item.checkpoint_detail is not None)
                for item in self._jobs.values()
            )
            if outstanding >= self.max_queued_jobs:
                raise RuntimeError("research queue is full")
            self._jobs[job.job_id] = job
            self._record(job, "research_job_submitted")
        return job

    def cancel(self, job_id: UUID) -> ResearchJob:
        with self._lock:
            job = self._require(job_id)
            if job.state in self._terminal_states:
                return job
            self._cancelled.add(job_id)
            if job.state is JobState.RUNNING:
                self._record(job, "research_job_cancel_requested")
                return job
            updated = self._copy(job, state=JobState.CANCELLED, detail="cancelled by coordinator")
            self._jobs[job_id] = updated
            self._record(updated, "research_job_cancelled")
            return updated

    def run(
        self,
        job_id: UUID,
        worker: Callable[[ResearchJob, Callable[[], bool]], str],
    ) -> ResearchJob:
        """Run one cooperative worker inside a bounded slot and deadline.

        Callbacks must poll ``cancelled`` around expensive operations. A
        non-cooperative callback cannot create extra parallel capacity: the
        slot stays occupied until it actually returns. Late results are
        retained as INTERRUPTED and are never accepted as successful work.
        """
        with self._lock:
            job = self._require(job_id)
            if job.state not in {JobState.QUEUED, JobState.CHECKPOINTED} and not (
                job.state is JobState.INTERRUPTED and job.checkpoint_detail is not None
            ):
                raise RuntimeError(f"job {job_id} is not runnable from {job.state.value}")

        self._slots.acquire()
        counted_active = False
        try:
            with self._lock:
                job = self._require(job_id)
                if job.state is JobState.CANCELLED or job_id in self._cancelled:
                    return job
                if job.state not in {JobState.QUEUED, JobState.CHECKPOINTED} and not (
                    job.state is JobState.INTERRUPTED and job.checkpoint_detail is not None
                ):
                    raise RuntimeError(f"job {job_id} is not runnable from {job.state.value}")
                remaining = job.execution_budget_seconds - job.budget_consumed_seconds
                if remaining <= 0:
                    expired = self._copy(
                        job,
                        state=JobState.INTERRUPTED,
                        detail="execution_budget_exceeded",
                    )
                    self._jobs[job_id] = expired
                    self._record(expired, "research_job_interrupted")
                    return expired
                running = self._copy(
                    job,
                    state=JobState.RUNNING,
                    detail=job.detail,
                    running_since=datetime.now(UTC),
                )
                self._jobs[job_id] = running
                self._active_workers += 1
                counted_active = True
                self._peak_workers = max(self._peak_workers, self._active_workers)
                self._record(running, "research_job_started")

            deadline = time.monotonic() + remaining

            def cancelled() -> bool:
                with self._lock:
                    requested = job_id in self._cancelled
                return requested or time.monotonic() >= deadline

            started = time.monotonic()
            try:
                detail = worker(running, cancelled)
                with self._lock:
                    requested = job_id in self._cancelled
                if requested:
                    state, final_detail = JobState.CANCELLED, "cancelled by coordinator"
                else:
                    state, final_detail = JobState.COMPLETED, str(detail)
            except Exception as exc:
                state, final_detail = JobState.INTERRUPTED, f"interrupted:{type(exc).__name__}"
            elapsed = time.monotonic() - started
            consumed = running.budget_consumed_seconds + elapsed
            if elapsed > remaining:
                state, final_detail = JobState.INTERRUPTED, "execution_budget_exceeded"
            with self._lock:
                current = self._require(job_id)
                terminal = self._copy(
                    current,
                    state=state,
                    detail=final_detail,
                    budget_consumed_seconds=min(consumed, running.execution_budget_seconds),
                    running_since=None,
                )
                self._jobs[job_id] = terminal
                self._cancelled.discard(job_id)
                event = {
                    JobState.COMPLETED: "research_job_completed",
                    JobState.CANCELLED: "research_job_cancelled",
                }.get(state, "research_job_interrupted")
                self._record(terminal, event)
            return terminal
        finally:
            with self._lock:
                if counted_active:
                    self._active_workers -= 1
            self._slots.release()

    def checkpoint(self, job_id: UUID, *, detail: str = "") -> ResearchJob:
        with self._lock:
            job = self._require(job_id)
            if job.state not in {JobState.RUNNING, JobState.CHECKPOINTED}:
                raise RuntimeError("only a running or resumed job can checkpoint")
            if job_id in self._cancelled:
                raise RuntimeError("a cancelled job cannot checkpoint new work")
            updated = self._copy(
                job,
                state=JobState.CHECKPOINTED,
                detail=detail,
                checkpoint_detail=detail,
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

    def _hydrate(self) -> None:
        if self.ledgers is None:
            return
        cancel_requested: set[UUID] = set()
        for event in self.ledgers.events(LedgerNamespace.MISSION):
            if not event.event_type.startswith("research_job_"):
                continue
            payload = event.payload.get("job")
            if not isinstance(payload, dict):
                continue
            try:
                job = ResearchJob(
                    job_id=UUID(str(payload["job_id"])),
                    role=ResearchRole(str(payload["role"])),
                    budget_units=int(payload["budget_units"]),
                    state=JobState(str(payload["state"])),
                    owner=str(payload["owner"]),
                    created_at=datetime.fromisoformat(str(payload["created_at"])),
                    detail=str(payload.get("detail", "")),
                    execution_budget_seconds=float(payload.get("execution_budget_seconds", 60.0)),
                    checkpoint_detail=(
                        str(payload["checkpoint_detail"])
                        if payload.get("checkpoint_detail") is not None
                        else None
                    ),
                    budget_consumed_seconds=float(payload.get("budget_consumed_seconds", 0.0)),
                    running_since=(
                        datetime.fromisoformat(str(payload["running_since"]))
                        if payload.get("running_since") is not None
                        else None
                    ),
                )
            except (KeyError, TypeError, ValueError):
                continue
            self._jobs[job.job_id] = job
            if event.event_type == "research_job_cancel_requested":
                cancel_requested.add(job.job_id)
        # A worker that was RUNNING when the owner process stopped has no
        # live execution context. Mark that fact durably, then permit only a
        # retained checkpoint to resume.
        for job_id, job in tuple(self._jobs.items()):
            if job.state not in {JobState.RUNNING, JobState.CHECKPOINTED}:
                continue
            consumed = job.budget_consumed_seconds
            if job.running_since is not None:
                consumed += max(
                    0.0,
                    (datetime.now(UTC) - job.running_since.astimezone(UTC)).total_seconds(),
                )
            state = job.state
            if job.state is JobState.RUNNING:
                state = JobState.CANCELLED if job_id in cancel_requested else JobState.INTERRUPTED
            if consumed >= job.execution_budget_seconds:
                state = JobState.INTERRUPTED
                detail = "execution_budget_exceeded"
            else:
                detail = (
                    "cancelled_during_restart"
                    if state is JobState.CANCELLED
                    else "worker_lost_on_restart"
                    if state is JobState.INTERRUPTED
                    else job.detail
                )
            stopped = self._copy(
                job,
                state=state,
                detail=detail,
                budget_consumed_seconds=min(consumed, job.execution_budget_seconds),
                running_since=None,
            )
            self._jobs[job_id] = stopped
            if job.state is JobState.RUNNING:
                self._record(
                    stopped,
                    "research_job_cancelled"
                    if state is JobState.CANCELLED
                    else "research_job_interrupted",
                )
            elif stopped != job:
                self._record(
                    stopped,
                    "research_job_checkpointed"
                    if state is JobState.CHECKPOINTED
                    else "research_job_interrupted",
                )

    @staticmethod
    def _copy(
        job: ResearchJob,
        *,
        state: JobState,
        detail: str,
        checkpoint_detail: str | None = None,
        budget_consumed_seconds: float | None = None,
        running_since: datetime | None | object = ...,
    ) -> ResearchJob:
        return ResearchJob(
            job_id=job.job_id,
            role=job.role,
            budget_units=job.budget_units,
            state=state,
            owner=job.owner,
            created_at=job.created_at,
            detail=detail,
            execution_budget_seconds=job.execution_budget_seconds,
            checkpoint_detail=(
                job.checkpoint_detail if checkpoint_detail is None else checkpoint_detail
            ),
            budget_consumed_seconds=(
                job.budget_consumed_seconds
                if budget_consumed_seconds is None
                else budget_consumed_seconds
            ),
            running_since=job.running_since if running_since is ... else running_since,
        )

    def _require(self, job_id: UUID) -> ResearchJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"unknown research job {job_id}")
        return job

    def _record(self, job: ResearchJob, event_type: str) -> None:
        if self.ledgers is None:
            return
        payload = asdict(job)
        payload["job_id"] = str(job.job_id)
        payload["role"] = job.role.value
        payload["state"] = job.state.value
        payload["created_at"] = job.created_at.isoformat()
        payload["running_since"] = (
            job.running_since.isoformat() if job.running_since is not None else None
        )
        logical_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type=event_type,
                idempotency_key=f"research-job:{job.job_id}:{event_type}:{logical_hash}",
                payload={"job": payload},
            )
        )


__all__ = ["JobState", "ResearchCoordinator", "ResearchJob", "ResearchRole"]
