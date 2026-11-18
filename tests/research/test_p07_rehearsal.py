"""P07 fixture rehearsal: stop/restart without duplicate artifacts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REHEARSAL_PATH = Path(__file__).resolve().parents[2] / "scripts" / "rehearse_p07_fixture.py"


def _load():
    spec = importlib.util.spec_from_file_location("rehearse_p07_fixture", REHEARSAL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rerun_reuses_verified_artifacts(tmp_path):
    module = _load()
    state = tmp_path / "state"
    snapshot = tmp_path / "snapshot"
    first = module.run_fixture_chain(state, snapshot)
    assert first["prior_count"] == 3
    assert first["prior_succeeded"] == 2
    assert first["prior_rejected"] == 1
    assert first["paper_rows"] == 1
    assert first["experiment_rows"] == 3
    assert first["validation_rows"] == 2
    assert first["failed"] == ["FIXC"]
    assert len(first["experiment_ids"]) == 2
    second = module.run_fixture_chain(state, snapshot)
    assert second == first


def test_resume_after_dropped_step_reuses_earlier_artifacts(tmp_path):
    module = _load()
    state = tmp_path / "state"
    snapshot = tmp_path / "snapshot"
    first = module.run_fixture_chain(state, snapshot)
    progress = json.loads((state / "rehearsal.json").read_text())
    del progress["experiments"]
    (state / "rehearsal.json").write_text(json.dumps(progress))
    resumed = module.run_fixture_chain(state, snapshot)
    assert resumed["paper_id"] == first["paper_id"]
    assert resumed["hypothesis_id"] == first["hypothesis_id"]
    assert resumed["paper_rows"] == 1
    # The re-run experiments step reuses stored rows (successes and the
    # recorded FIXC failure) instead of duplicating them.
    assert resumed["experiment_ids"] == first["experiment_ids"]
    assert resumed["experiment_rows"] == 3
    assert resumed["validation_rows"] == 2


def test_prior_retrieves_accepted_and_rejected_experiments(tmp_path):
    module = _load()
    state = tmp_path / "state"
    snapshot = tmp_path / "snapshot"
    module.run_fixture_chain(state, snapshot)
    workflow = module._open_workflow(state, snapshot)
    prior = {item.symbol: item for item in workflow.prior_experiments("momentum")}
    assert {symbol for symbol, item in prior.items() if item.succeeded} == {"FIXA", "FIXB"}
    assert prior["FIXC"].succeeded is False
    assert prior["FIXC"].detail.startswith("FIXC:")
    assert prior["FIXC"].experiment_id is not None


def test_cancelled_step_is_memory_and_resume_completes_without_duplicates(tmp_path):
    from advisorai.research import JobState, ResearchRole

    module = _load()
    state = tmp_path / "state"
    snapshot = tmp_path / "snapshot"
    workflow = module._open_workflow(state, snapshot)
    job = workflow.coordinator.submit(ResearchRole.EXPERIMENT_CONDUCTOR, detail="cancel-demo")
    workflow.coordinator.cancel(job.job_id)

    def worker(running, cancelled):
        raise AssertionError("cancelled job ran its worker")

    with pytest.raises(RuntimeError, match="not runnable from cancelled"):
        workflow.coordinator.run(job.job_id, worker)
    (stored,) = [item for item in workflow.coordinator.jobs() if item.job_id == job.job_id]
    assert stored.state is JobState.CANCELLED
    prior = workflow.prior_experiments("momentum")
    assert any(item.symbol == "unfinished" and item.succeeded is False for item in prior)
    # Resume with the real chain: it completes, and the earlier
    # cancellation left no partial experiment rows behind.
    report = module.run_fixture_chain(state, snapshot)
    assert report["experiment_rows"] == 3
    assert report["validation_rows"] == 2
    assert report["prior_succeeded"] == 2
    assert report["prior_rejected"] == 2
