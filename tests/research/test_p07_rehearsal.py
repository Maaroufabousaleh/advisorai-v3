"""P07 fixture rehearsal: stop/restart without duplicate artifacts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

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
    assert first["prior_count"] == 2
    assert first["paper_rows"] == 1
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
