"""Black-box rehearsal: the P08 prep script stays runnable in both modes.

Default mode abstains cleanly; route mode exercises the approved LONG path
with cancel-to-clean. Both write a P10-shaped report with traceable ids.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(state_dir: Path, *args: str) -> dict:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(ROOT / "src"), environment.get("PYTHONPATH", "")) if item
    )
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_p08_prep_session.py"), *args],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(completed.stdout)
    assert (state_dir / "report.json").read_text(encoding="utf-8")
    return report


def test_prep_script_default_mode_abstains_cleanly(tmp_path):
    report = _run(tmp_path / "default", "--state-dir", str(tmp_path / "default"), "--cycles", "1")
    assert report["stage_counts"] == {"abstained": 2}
    assert report["route_stage_counts"] == {}
    assert report["order_ids"] == []
    assert report["end_outcome"] == "clean"
    assert report["leases_released"] is True
    assert report["strategy"].endswith("rehearsal only)")


def test_prep_script_route_mode_routes_and_closes_cleanly(tmp_path):
    report = _run(
        tmp_path / "route",
        "--state-dir",
        str(tmp_path / "route"),
        "--cycles",
        "1",
        "--route-cycles",
        "1",
    )
    assert report["stage_counts"] == {"abstained": 2}
    assert report["route_stage_counts"] == {"orders_routed": 1}
    assert len(report["order_ids"]) == 1
    assert report["route_cycles"][0]["reconciled"] is True
    assert report["end_outcome"] == "clean"
    assert report["leases_released"] is True
