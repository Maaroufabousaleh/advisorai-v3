"""Agent workflow: durable Scout/Librarian/experiment steps plus outcome memory.

Covers duplicate Scout intake, prior retrieval of stored results, restart
rehydration, queued-job cancellation, and hypothesis-expiry decay flags. The
Scout step uses an injected canned transport so the default suite stays
offline; live verification runs through ``scripts/run_research_workflow.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from advisorai.integrations import HttpClientConfig, SafeHttpClient
from advisorai.ledger import SqliteLedgers
from advisorai.research import (
    JobState,
    ResearchCoordinator,
    ResearchMetadataProvider,
    ResearchMetadataScout,
    ResearchRole,
)
from advisorai.research.replay import ExposureSpec
from advisorai.research.workflow import ResearchWorkflow, open_workflow_state

_CROSSREF_BODY = json.dumps(
    {
        "message": {
            "title": ["Time series momentum"],
            "author": [{"given": "Tobias J.", "family": "Moskowitz"}],
            "DOI": "10.1016/j.jfineco.2011.11.003",
            "published-print": {"date-parts": [[2012, 5]]},
            "license": [{"content-version": "vor", "URL": "https://example.invalid/license"}],
        }
    }
).encode()


def _bars(count: int = 300) -> tuple[dict, ...]:
    from datetime import date, timedelta

    base = date(2020, 1, 2)
    rows = []
    current = base
    added = 0
    while added < count:
        if current.weekday() < 5:
            price = 100 + added
            rows.append(
                {
                    "date": current.isoformat(),
                    "open": float(price),
                    "high": float(price + 1),
                    "low": float(price - 1),
                    "adj_close": float(price),
                    "volume": 1000,
                }
            )
            added += 1
        current += timedelta(days=1)
    return tuple(rows)


def _workflow(tmp_path: Path) -> ResearchWorkflow:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "bars.json").write_text(json.dumps({"FIXA": list(_bars()), "FIXB": list(_bars())}))
    state = tmp_path / "state"
    registry, _ledgers, coordinator = open_workflow_state(state)

    def requester(method, url, headers, body, timeout):
        return 200, _CROSSREF_BODY, (("Content-Type", "application/json"),)

    client = SafeHttpClient(
        HttpClientConfig(allowed_hosts=("api.crossref.org",)), requester=requester
    )
    scout = ResearchMetadataScout(
        registry=registry,
        coordinator=coordinator,
        clients={ResearchMetadataProvider.CROSSREF_API: client},
    )
    return ResearchWorkflow(
        registry=registry,
        coordinator=coordinator,
        scout=scout,
        snapshot_root=snapshot,
        state_dir=state,
    )


def test_scout_librarian_experiment_chain_with_duplicate_intake(tmp_path):
    workflow = _workflow(tmp_path)
    first_job, first_paper = workflow.scout_paper(
        source_url="https://doi.org/10.1016/j.jfineco.2011.11.003",
        claimed_result="TSM persists",
        relevance="momentum",
    )
    assert first_job.state is JobState.COMPLETED
    assert first_paper is not None and first_paper.title == "Time series momentum"
    # A duplicate fetch reuses the stored card: no second paper row.
    second_job, second_paper = workflow.scout_paper(
        source_url="https://doi.org/10.1016/j.jfineco.2011.11.003",
        claimed_result="TSM persists",
        relevance="momentum",
    )
    assert second_job.state is JobState.COMPLETED
    assert second_paper is not None
    assert second_paper.artifact_id == first_paper.artifact_id
    assert workflow.registry.count("paper") == 1

    librarian_job, hypothesis = workflow.librarian_hypothesis(
        paper=first_paper,
        economic_rationale="continuation",
        universe="FIXA,FIXB",
        horizon="252d/21d",
        baseline="buy-and-hold",
        preregistered_plan="workflow test v1",
        owner="test",
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
    )
    assert librarian_job.state is JobState.COMPLETED

    experiment_job, outcomes = workflow.run_experiments(
        hypothesis=hypothesis,
        exposures=(ExposureSpec(symbol="FIXA"), ExposureSpec(symbol="FIXB")),
        policy_id="workflow-test-v1",
        environment_hash="ab" * 32,
    )
    assert experiment_job.state is JobState.COMPLETED
    assert {item.symbol for item in outcomes} == {"FIXA", "FIXB"}
    assert all(item.succeeded for item in outcomes)

    # The next job retrieves both stored experiments.
    prior = workflow.prior_experiments("momentum")
    assert {item.symbol for item in prior} == {"FIXA", "FIXB"}
    assert all(item.succeeded for item in prior)


def test_prior_experiments_surface_failed_jobs(tmp_path):
    workflow = _workflow(tmp_path)
    job = workflow.coordinator.submit(ResearchRole.EXPERIMENT_CONDUCTOR, detail="doomed")

    def doomed(running, cancelled):
        raise RuntimeError("worker blew up")

    finished = workflow.coordinator.run(job.job_id, doomed)
    assert finished.state is JobState.INTERRUPTED
    prior = workflow.prior_experiments("anything")
    failed = [item for item in prior if not item.succeeded]
    assert len(failed) == 1 and failed[0].experiment_id == job.job_id


def test_coordinator_restart_rehydrates_workflow_jobs(tmp_path):
    workflow = _workflow(tmp_path)
    job, paper = workflow.scout_paper(
        source_url="https://doi.org/10.1016/j.jfineco.2011.11.003",
        claimed_result="TSM persists",
        relevance="momentum",
    )
    assert job.state is JobState.COMPLETED and paper is not None
    reopened = ResearchCoordinator(
        SqliteLedgers(workflow.state_dir / "research-jobs.sqlite3"), max_workers=2
    )
    states = {item.job_id: item.state for item in reopened.jobs()}
    assert states[job.job_id] is JobState.COMPLETED


def test_queued_experiment_job_cancels_before_run(tmp_path):
    workflow = _workflow(tmp_path)
    job = workflow.coordinator.submit(ResearchRole.EXPERIMENT_CONDUCTOR, detail="cancel me")
    workflow.coordinator.cancel(job.job_id)
    final = next(item for item in workflow.coordinator.jobs() if item.job_id == job.job_id)
    assert final.state is JobState.CANCELLED


def test_decay_monitor_flags_expired_hypotheses(tmp_path):
    workflow = _workflow(tmp_path)
    _, paper = workflow.scout_paper(
        source_url="https://doi.org/10.1016/j.jfineco.2011.11.003",
        claimed_result="TSM persists",
        relevance="momentum",
    )
    assert paper is not None
    _, _expired = workflow.librarian_hypothesis(
        paper=paper,
        economic_rationale="old idea",
        universe="FIXA",
        horizon="252d/21d",
        baseline="buy-and-hold",
        preregistered_plan="expired plan",
        owner="test",
        expires_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    _, _live = workflow.librarian_hypothesis(
        paper=paper,
        economic_rationale="live idea",
        universe="FIXA",
        horizon="252d/21d",
        baseline="buy-and-hold",
        preregistered_plan="live plan",
        owner="test",
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    flags = workflow.decay_status(now=datetime(2026, 6, 1, tzinfo=UTC))
    assert len(flags) == 1 and flags[0]["kind"] == "hypothesis_expired"
