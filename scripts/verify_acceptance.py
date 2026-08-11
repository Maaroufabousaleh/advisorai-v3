#!/usr/bin/env python3
"""Run the local acceptance suites one architecture phase at a time.

The repository contains optional, heavyweight runtimes (for example Nautilus,
DuckDB and Arrow). Running every test in one interpreter can exhaust a small
laptop even when each phase is healthy. This runner deliberately starts a new
pytest process for every phase, preserving the plan's gated sequencing and
making failures attributable to one phase.

It verifies executable local evidence only. The time-based Phase 0/7 and human
approval Phase 10 gates remain records that must be supplied separately.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class PhaseSuite:
    number: int
    name: str
    tests: tuple[str, ...]


PHASE_SUITES: tuple[PhaseSuite, ...] = (
    PhaseSuite(
        0,
        "contracts and bakeoffs",
        ("tests/phase0", "tests/gates", "tests/contracts/test_gateway.py"),
    ),
    PhaseSuite(
        1,
        "safety, data truth and resources",
        (
            "tests/config",
            "tests/contracts",
            "tests/data/test_immutable_lake.py",
            "tests/data/test_lake_query.py",
            "tests/data/test_ledgers.py",
            "tests/data/test_observability.py",
            "tests/point_in_time",
            "tests/recovery/test_config_bundles.py",
            "tests/resources",
            "tests/orchestration/test_flows_features.py",
            "tests/orchestration/test_runtimes.py",
            "tests/memory",
            "tests/services",
        ),
    ),
    PhaseSuite(
        2,
        "deterministic paper core",
        ("tests/execution", "tests/integrations", "tests/runtime"),
    ),
    PhaseSuite(
        3,
        "V3-Core data spine",
        (
            "tests/data/test_collectors.py",
            "tests/data/test_market_events.py",
            "tests/data/test_official.py",
            "tests/data/test_acquisition.py",
            "tests/phase3/test_source_qualification.py",
            "tests/phase3/test_coinbase_wss_qualification.py",
            "tests/phase3/test_coinbase_level2_qualification.py",
            "tests/phase3/test_binance_depth_qualification.py",
            "tests/phase3/test_source_health_controls.py",
            "tests/phase3/test_phase3_public_data_qualification_runner.py",
        ),
    ),
    PhaseSuite(4, "quantitative baseline council", ("tests/models",)),
    PhaseSuite(5, "typed evidence council", ("tests/agents", "tests/api")),
    PhaseSuite(6, "institutional controls", ("tests/institutional",)),
    PhaseSuite(
        7,
        "unattended paper soak",
        ("tests/recovery/test_soak.py", "tests/recovery/test_durable_soak.py", "tests/learning"),
    ),
    PhaseSuite(8, "Hermes and Skill Foundry", ("tests/capabilities",)),
    PhaseSuite(9, "controlled expansion", ("tests/expansion",)),
    PhaseSuite(10, "limited live capital guard", ("tests/live",)),
)


def _phase_map() -> dict[int, PhaseSuite]:
    return {suite.number: suite for suite in PHASE_SUITES}


def _run_suite(suite: PhaseSuite) -> tuple[bool, str]:
    command = [sys.executable, "-m", "pytest", "-q", *suite.tests]
    environment = os.environ.copy()
    environment.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    print(f"Phase {suite.number}: {suite.name}", flush=True)
    print("  $ " + " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=ROOT, env=environment, check=False)
    return completed.returncode == 0, "passed" if completed.returncode == 0 else "failed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        type=int,
        choices=range(len(PHASE_SUITES)),
        help="run only one phase; by default run all local phase suites in order",
    )
    args = parser.parse_args()
    suites = PHASE_SUITES if args.phase is None else (_phase_map()[args.phase],)
    failed: list[int] = []
    for suite in suites:
        passed, _summary = _run_suite(suite)
        if not passed:
            failed.append(suite.number)
            # The architecture is gate-driven: a later phase must never be
            # treated as evidence while an earlier phase is failing. Use
            # ``--phase N`` when deliberately debugging one isolated suite.
            break
    if failed:
        print(f"Acceptance failed in phase(s): {', '.join(map(str, failed))}")
        return 1
    print(f"Acceptance suites passed: {len(suites)} phase(s)")
    print(
        "External evidence gates remain separate: Phase 0 stability, Phase 7 soak, Phase 10 approval."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
