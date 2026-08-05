"""Durable retries/backfills/experiments without owning market events or orders."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers


class FlowState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"


class FlowRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID = Field(default_factory=uuid4)
    flow_name: str = Field(min_length=1)
    state: FlowState = FlowState.PENDING
    attempt: int = Field(default=0, ge=0)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def require_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("flow timestamps must include a timezone")
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def validate_finished_order(self) -> FlowRun:
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValueError("flow cannot finish before it starts")
        return self


@dataclass
class DurableFlow:
    name: str
    max_retries: int = 2
    ledgers: SqliteLedgers | None = None

    def __post_init__(self) -> None:
        if not self.name.strip() or self.max_retries < 0:
            raise ValueError("durable flow requires a name and non-negative retry count")

    def run(self, task: Callable[[], object]) -> tuple[FlowRun, object | None]:
        run = FlowRun(flow_name=self.name)
        self._record(run, "flow_pending")
        last_error: str | None = None
        for attempt in range(self.max_retries + 1):
            run = run.model_copy(
                update={
                    "state": FlowState.RUNNING,
                    "attempt": attempt + 1,
                    "started_at": datetime.now(UTC),
                }
            )
            self._record(run, "flow_running")
            try:
                value = task()
                completed = run.model_copy(
                    update={"state": FlowState.SUCCEEDED, "finished_at": datetime.now(UTC)}
                )
                self._record(completed, "flow_succeeded")
                return completed, value
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                run = run.model_copy(update={"state": FlowState.RETRYING, "error": last_error})
                self._record(run, "flow_retrying")
        failed = run.model_copy(
            update={
                "state": FlowState.FAILED,
                "finished_at": datetime.now(UTC),
                "error": last_error,
            }
        )
        self._record(failed, "flow_failed")
        return failed, None

    def _record(self, run: FlowRun, event_type: str) -> None:
        if self.ledgers is None:
            return
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MISSION,
                event_type=event_type,
                idempotency_key=f"flow:{run.run_id}:{event_type}:{run.attempt}",
                payload={"run": run.model_dump(mode="json", round_trip=True)},
            )
        )
