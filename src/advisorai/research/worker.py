"""Strategy Engineer coding worker: supervised handoff behind a typed boundary.

The worker receives a reviewed hypothesis, pinned source references, allowed
inputs, and an experiment contract, and returns a candidate Factor DSL
expression (or patch), tests, a reproduction command, and a reuse record.
OMP is the first supervised worker behind the CodingWorker protocol; a
separate validation job (Red Team skill or replay) accepts or rejects the
result. No broker credentials, production files, or order paths are visible
on this interface, and Hermes stays an optional isolated builder under its
existing Phase 8 gate.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from advisorai.research import factors as dsl
from advisorai.research.cards import HypothesisCard
from advisorai.research.jobs import ResearchCoordinator, ResearchJob, ResearchRole

WORKER_VERSION = "advisorai.research.coding-worker.v1"


@dataclass(frozen=True, slots=True)
class ExperimentContract:
    """What the worker may consume and must produce."""

    hypothesis_id: UUID
    source_artifact_ids: tuple[str, ...]
    allowed_inputs: tuple[str, ...]
    policy_id: str
    costs: str
    windows: str
    acceptance: str
    forbidden: tuple[str, ...] = (
        "broker credentials",
        "production files",
        "order paths",
        "risk-limit changes",
        "network access",
    )


@dataclass(frozen=True, slots=True)
class WorkerResult:
    """Candidate output: DSL or patch, tests, repro, and reuse record."""

    dsl_source: str | None
    patch_summary: str | None
    tests: tuple[str, ...]
    repro_command: str
    reused: tuple[str, ...]
    reimplemented: tuple[str, ...]
    rationale: str


class CodingWorker(Protocol):
    """Replaceable supervised worker. OMP implements this first, by hand."""

    worker_id: str

    def build(self, contract: ExperimentContract) -> WorkerResult: ...


@dataclass(frozen=True, slots=True)
class SupervisedOmpWorker:
    """OMP-as-worker: supervised handoff with an explicit, reviewable output.

    This worker encodes one reviewed handoff (momentum 12-0 DSL) as the
    reference implementation. Future headless workers implement the same
    protocol; validation cannot tell them apart.
    """

    worker_id: str = "omp-supervised-v1"

    def build(self, contract: ExperimentContract) -> WorkerResult:
        _require_contract(contract)
        return WorkerResult(
            dsl_source=json.dumps(
                {
                    "op": "div",
                    "args": [
                        {
                            "op": "sub",
                            "args": [{"field": "adj_close"}, {"field": "adj_close_lag_252"}],
                        },
                        {"field": "adj_close_lag_252"},
                    ],
                },
                sort_keys=True,
            ),
            patch_summary=None,
            tests=(
                "tests/research/test_multi_asset_replay.py::test_momentum_factor_is_versioned_and_past_only",
                "tests/research/test_multi_asset_replay.py::test_fixture_snapshot_reproduces_identical_numerics",
            ),
            repro_command="uv run python scripts/run_research_replay.py --label <your-label>",
            reused=("research.costs pinned cost configuration",),
            reimplemented=("252-bar formation return from the paper specification",),
            rationale="supervised reference: 12-0 momentum DSL, reviewed hypothesis only",
        )


def _require_contract(contract: ExperimentContract) -> None:
    if not contract.source_artifact_ids:
        raise ValueError("worker contract requires pinned source references")
    if not contract.allowed_inputs:
        raise ValueError("worker contract requires allowed inputs")
    if not contract.acceptance.strip():
        raise ValueError("worker contract requires acceptance criteria")
    for forbidden in ("broker", "order", "production", "risk-limit"):
        for field in (*contract.allowed_inputs, contract.costs, contract.windows):
            if forbidden in field.lower():
                raise ValueError(f"worker contract touches forbidden area: {forbidden}")


def validate_worker_result(result: WorkerResult, *, contract: ExperimentContract) -> dict[str, Any]:
    """Independent validation gate: parse, hash, and check completeness.

    Returns measurements; raises on rejection. Runs outside the worker so a
    separate job (or human) can reject without trusting worker claims.
    """
    if result.dsl_source is None and result.patch_summary is None:
        raise ValueError("worker result has neither DSL nor patch")
    if not result.tests:
        raise ValueError("worker result has no tests")
    if not result.repro_command.strip():
        raise ValueError("worker result has no reproduction command")
    digest: str | None = None
    if result.dsl_source is not None:
        expression = dsl.parse(result.dsl_source)
        digest = dsl.canonical_hash(expression)
        evaluated = dsl.evaluate(expression, {"adj_close": (1, 2), "adj_close_lag_252": (1, 1)})
        if len(evaluated.values) != 2:
            raise ValueError("worker DSL does not evaluate over its declared fields")
    return {
        "contract": WORKER_VERSION,
        "hypothesis_id": str(contract.hypothesis_id),
        "dsl_hash": digest,
        "tests": list(result.tests),
        "reused": list(result.reused),
        "reimplemented": list(result.reimplemented),
        "accepted": True,
    }


def contract_fingerprint(contract: ExperimentContract) -> str:
    payload = json.dumps(
        {
            "hypothesis": str(contract.hypothesis_id),
            "sources": sorted(contract.source_artifact_ids),
            "inputs": sorted(contract.allowed_inputs),
            "policy": contract.policy_id,
            "costs": contract.costs,
            "windows": contract.windows,
            "acceptance": contract.acceptance,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class EngineerHandoff:
    """Run a supervised worker build as a durable Strategy Engineer job."""

    def __init__(self, *, coordinator: ResearchCoordinator, worker: CodingWorker) -> None:
        self.coordinator = coordinator
        self.worker = worker

    def submit_build(self, contract: ExperimentContract) -> ResearchJob:
        _require_contract(contract)
        return self.coordinator.submit(
            ResearchRole.STRATEGY_ENGINEER,
            detail=json.dumps(
                {"contract": WORKER_VERSION, "fingerprint": contract_fingerprint(contract)}
            ),
        )

    def run_build(self, job_id: UUID, contract: ExperimentContract) -> dict[str, Any]:
        job = next((item for item in self.coordinator.jobs() if item.job_id == job_id), None)
        if job is None or job.role is not ResearchRole.STRATEGY_ENGINEER:
            raise ValueError("engineer handoff runs only Strategy Engineer jobs")

        def worker_job(_running: ResearchJob, cancelled: Any) -> str:
            if cancelled():
                return json.dumps({"cancelled": True})
            result = self.worker.build(contract)
            verdict = validate_worker_result(result, contract=contract)
            return json.dumps(
                {
                    "worker_id": self.worker.worker_id,
                    "dsl_source": result.dsl_source,
                    "tests": list(result.tests),
                    "repro_command": result.repro_command,
                    "verdict": verdict,
                },
                sort_keys=True,
            )

        completed = self.coordinator.run(job_id, worker_job)
        return json.loads(completed.detail)


def contract_for_hypothesis(
    hypothesis: HypothesisCard,
    *,
    source_artifact_ids: tuple[str, ...],
    policy_id: str = "tsmom-sign-only-v1",
) -> ExperimentContract:
    """Build a contract from a reviewed hypothesis card."""
    return ExperimentContract(
        hypothesis_id=hypothesis.artifact_id,
        source_artifact_ids=source_artifact_ids,
        allowed_inputs=hypothesis.point_in_time_inputs or ("adj_close-vendor-adjusted-v1",),
        policy_id=policy_id,
        costs="research.costs pinned (SPY4/TLT5)",
        windows="screen/validation/holdout with 21d embargo",
        acceptance="positive holdout net vs buy-and-hold; independent replay matches",
    )


__all__ = [
    "WORKER_VERSION",
    "CodingWorker",
    "EngineerHandoff",
    "ExperimentContract",
    "SupervisedOmpWorker",
    "WorkerResult",
    "contract_fingerprint",
    "contract_for_hypothesis",
    "validate_worker_result",
]
