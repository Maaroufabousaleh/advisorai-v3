#!/usr/bin/env python3
"""Run the durable research workflow: Scout → Librarian → experiment worker.

Live command. The Scout step performs one bounded citation-metadata fetch
against the reviewed provider; everything else is offline against the frozen
snapshot and the isolated state dir. No credentials, no orders, no admission
changes. State defaults under ``var/research-replay/`` and never touches
``var/advisorai/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from advisorai.research import ResearchMetadataScout  # noqa: E402
from advisorai.research.replay import ExposureSpec  # noqa: E402
from advisorai.research.workflow import (  # noqa: E402
    ResearchWorkflow,
    open_workflow_state,
)

SOURCE_URL = "https://doi.org/10.1016/j.jfineco.2011.11.003"

COSTS = {
    "SPY": (Decimal("1"), Decimal("1"), Decimal("2")),
    "TLT": (Decimal("1"), Decimal("2"), Decimal("2")),
}


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _environment_hash() -> str:
    identity = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "system": platform.system(),
        "machine": platform.machine(),
        "command": "scripts/run_research_workflow.py",
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot",
        default="data/research-replay/snapshots/etf-daily-2015-2025-v1",
        help="Frozen snapshot directory.",
    )
    parser.add_argument(
        "--state-dir",
        default="var/research-replay",
        help="Isolated state root. Must not be var/advisorai.",
    )
    parser.add_argument("--label", default=None, help="Evidence run label.")
    parser.add_argument("--policy", default="tsmom-sign-only-v1", help="Signal policy identity.")
    args = parser.parse_args()

    snapshot_root = (REPOSITORY_ROOT / args.snapshot).resolve()
    state_dir = Path(args.state_dir)
    if not state_dir.is_absolute():
        state_dir = (REPOSITORY_ROOT / state_dir).resolve()
    if (
        state_dir == (REPOSITORY_ROOT / "var" / "advisorai").resolve()
        or (REPOSITORY_ROOT / "var" / "advisorai").resolve() in state_dir.parents
    ):
        raise ValueError("research workflow state must stay outside var/advisorai")

    registry, _ledgers, coordinator = open_workflow_state(state_dir)
    scout = ResearchMetadataScout(registry=registry, coordinator=coordinator)
    workflow = ResearchWorkflow(
        registry=registry,
        coordinator=coordinator,
        scout=scout,
        snapshot_root=snapshot_root,
        state_dir=state_dir,
    )
    prior_before = workflow.prior_experiments("momentum")

    scout_job, paper = workflow.scout_paper(
        source_url=SOURCE_URL,
        claimed_result=("Past 12-month return predicts next-month return across 58 instruments"),
        relevance="cross-asset time-series momentum: SPY equity and TLT bond exposures",
    )
    if paper is None:
        raise RuntimeError(f"scout step did not store a paper: {scout_job.state.value}")
    librarian_job, hypothesis = workflow.librarian_hypothesis(
        paper=paper,
        economic_rationale=(
            "Slow diffusion of macro news and risk-capacity constraints produce "
            "continuation in broad equity and bond index returns at a 12-month "
            "formation, 1-month holding horizon"
        ),
        universe="SPY,TLT",
        horizon="252d formation, 21d holding, month-end rebalance",
        baseline="buy-and-hold on the same month-end windows",
        preregistered_plan=(
            "research replay v1: screen 2015-2019, validation 2020-2022, "
            "holdout 2023-2025; sign-only policy; per-exposure costs"
        ),
        owner="omp-research-workflow-v1",
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        failure_modes=(
            "momentum reversal in stress months",
            "bond trend breakdown under rate shocks",
            "costs exceed edge on low-volatility exposures",
        ),
        point_in_time_inputs=("adj_close-vendor-adjusted-v1",),
        budget="offline CPU plus one bounded citation fetch; one trial",
    )
    exposures = tuple(
        ExposureSpec(symbol=symbol, fee_bps=fee, spread_bps=spread, slippage_bps=slip)
        for symbol, (fee, spread, slip) in COSTS.items()
    )
    experiment_job, outcomes = workflow.run_experiments(
        hypothesis=hypothesis,
        exposures=exposures,
        policy_id=args.policy,
        environment_hash=_environment_hash(),
    )
    prior_after = workflow.prior_experiments("momentum")
    decay = workflow.decay_status()

    label = args.label or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence_root = state_dir / "evidence" / label
    evidence_root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "advisorai.research.agent-workflow.v1",
        "label": label,
        "repository_commit": _git_head(REPOSITORY_ROOT),
        "command": "scripts/run_research_workflow.py",
        "steps": [
            {
                "role": "scout",
                "job_id": str(scout_job.job_id),
                "state": scout_job.state.value,
                "paper_artifact_id": str(paper.artifact_id),
            },
            {
                "role": "librarian",
                "job_id": str(librarian_job.job_id),
                "state": librarian_job.state.value,
                "hypothesis_artifact_id": str(hypothesis.artifact_id),
            },
            {
                "role": "experiment_conductor",
                "job_id": str(experiment_job.job_id),
                "state": experiment_job.state.value,
            },
        ],
        "outcomes": [
            {
                "symbol": item.symbol,
                "net_bps": item.net_bps,
                "baseline_net_bps": item.baseline_net_bps,
                "succeeded": item.succeeded,
                "experiment_id": str(item.experiment_id),
                "detail": item.detail,
            }
            for item in outcomes
        ],
        "prior_experiments_before": len(prior_before),
        "prior_experiments_after": len(prior_after),
        "decay_flags": list(decay),
    }
    encoded = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (evidence_root / "manifest.json").write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    print(
        f"scout={scout_job.state.value} librarian={librarian_job.state.value} "
        f"experiments={experiment_job.state.value} manifest_sha256={digest}"
    )
    for item in outcomes:
        print(
            f"{item.symbol}: net={item.net_bps}bps baseline={item.baseline_net_bps}bps "
            f"succeeded={item.succeeded} experiment={item.experiment_id}"
        )
    print(f"prior before/after: {len(prior_before)}/{len(prior_after)} decay_flags={len(decay)}")
    print(f"evidence: {evidence_root}")
    return 0 if all(item.succeeded for item in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
