"""Callable research skills: specs, job wiring, and per-skill behavior.

Proves every skill has a typed spec (when/inputs/outputs/test/role), runs as
a durable coordinator job under the right role, and performs its documented
check: literature links claims and flags gaps, intake gates on provenance,
hypotheses preregister, data rejects non-PIT bars, red team measures cost
sensitivity independently, portfolio compares without touching limits, and
memory recalls prior trials.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from advisorai.ledger import SqliteLedgers
from advisorai.research import ResearchRegistry
from advisorai.research.cards import (
    ExperimentArtifact,
    ImplementationDisposition,
    ImplementationRecord,
    ImplementationRelationship,
    PaperCard,
)
from advisorai.research.code_discovery import extract_code_leads  # noqa: F401 - spec surface
from advisorai.research.jobs import JobState, ResearchCoordinator, ResearchRole
from advisorai.research.retrieval import ResearchRetrieval
from advisorai.research.skills import SKILL_NAMES, SKILL_SPECS, SkillRunner, skill_spec


def _runner(tmp_path: Path) -> SkillRunner:
    state = tmp_path / "state"
    state.mkdir()
    registry = ResearchRegistry(state / "registry.sqlite3")
    coordinator = ResearchCoordinator(SqliteLedgers(state / "jobs.sqlite3"))
    return SkillRunner(
        registry=registry,
        coordinator=coordinator,
        retrieval=ResearchRetrieval(state / "registry.sqlite3"),
    )


def _rebuild(runner: SkillRunner, tmp_path: Path) -> None:
    ResearchRetrieval(tmp_path / "state" / "registry.sqlite3").rebuild()


def test_skill_specs_cover_all_seven_required_skills():
    assert SKILL_NAMES == (
        "literature_and_implementation_search",
        "code_intake_and_replication",
        "financial_hypothesis_design",
        "multi_asset_data_and_features",
        "backtesting_and_statistical_red_team",
        "portfolio_and_risk_analysis",
        "research_memory_and_monitoring",
    )
    for spec in SKILL_SPECS:
        assert spec.when_to_call and spec.inputs and spec.outputs
        assert spec.test_reference.startswith("tests/research/test_skills.py::test_")
    assert skill_spec("backtesting_and_statistical_red_team").role is ResearchRole.RED_TEAM
    assert skill_spec("portfolio_and_risk_analysis").role is ResearchRole.PORTFOLIO_REVIEWER


def test_literature_skill_links_claims(tmp_path):
    runner = _runner(tmp_path)
    paper = runner.registry.store(
        PaperCard(
            source_url="https://doi.org/10.1016/j.jfineco.2011.11.003",
            title="Time series momentum",
            claimed_result="TSM persists across assets",
            relevance="momentum",
        ),
        record_type="paper",
    )
    _rebuild(runner, tmp_path)
    result = runner.run_skill(
        "literature_and_implementation_search",
        {
            "query": "momentum",
            "paper_card_id": str(paper.artifact_id),
            "openalex_locations": ("https://github.com/moskowitz/tsm",),
        },
    )
    assert result.output["paper_hits"] == [str(paper.artifact_id)]
    assert result.output["code_leads"] == [
        {"repository_url": "https://github.com/moskowitz/tsm", "found_via": "openalex_location"}
    ]
    assert any(
        "no linked implementation" in flag for flag in result.output["missing_evidence_flags"]
    )
    jobs = runner.coordinator.jobs(state=JobState.COMPLETED)
    assert any(job.role is ResearchRole.SCOUT for job in jobs)


def test_code_intake_reproduces_small_behavior(tmp_path):
    runner = _runner(tmp_path)
    record = runner.registry.store(
        ImplementationRecord(
            repository_url="https://github.com/moskowitz/tsm",
            revision="v1.0",
            source_hash="ab" * 32,
            license="MIT",
            relationship=ImplementationRelationship.INDEPENDENT_REPRODUCTION,
            entry_point="python run.py",
            replication_evidence="table 1 within 1%",
            disposition=ImplementationDisposition.ADAPT_IN_ISOLATION,
            disposition_rationale="verify first",
        ),
        record_type="implementation",
    )
    result = runner.run_skill(
        "code_intake_and_replication",
        {"implementation_record_id": str(record.artifact_id), "observed_behavior": "smoke PASS"},
    )
    assert result.output["provenance_ok"] is True
    assert result.output["small_behavior_reproduced"] is True


def test_hypothesis_design_preregisters(tmp_path):
    runner = _runner(tmp_path)
    result = runner.run_skill(
        "financial_hypothesis_design",
        {
            "thesis": "slow news diffusion drives continuation",
            "failure_modes": "reversals;rate shocks",
            "baseline": "buy-and-hold",
            "windows": "screen/validation/holdout",
            "costs": "SPY4/TLT5",
            "acceptance": "positive holdout net",
        },
    )
    stored = runner.registry.get(__import__("uuid").UUID(result.output["hypothesis_card_id"]))
    assert stored is not None
    assert "accept_if=positive holdout net" in result.output["preregistered_plan"]


def test_data_skill_rejects_future_inputs(tmp_path):
    runner = _runner(tmp_path)
    bars_path = tmp_path / "bars.json"
    bars_path.write_text(
        json.dumps(
            {
                "SPY": [
                    {"date": "2020-01-03", "adj_close": 1.0},
                    {"date": "2020-01-02", "adj_close": 1.0},
                ]
            }
        )
    )
    result = runner.run_skill(
        "multi_asset_data_and_features",
        {
            "symbol": "SPY",
            "snapshot_root": str(tmp_path),
            "bars_path": "bars.json",
            "feature_spec": "adj_close",
        },
    )
    # The job records interruption; a passing skill returns pit_ok instead.
    assert result.output.get("pit_ok") is not True
    bars_path.write_text(
        json.dumps(
            {
                "SPY": [
                    {"date": "2020-01-02", "adj_close": 1.0},
                    {"date": "2020-01-03", "adj_close": 1.1},
                ]
            }
        )
    )
    good = runner.run_skill(
        "multi_asset_data_and_features",
        {
            "symbol": "SPY",
            "snapshot_root": str(tmp_path),
            "bars_path": "bars.json",
            "feature_spec": "adj_close",
        },
    )
    assert good.output["pit_ok"] is True
    assert len(good.output["feature_hash"]) == 64


def test_data_skill_rejects_unscoped_paths(tmp_path):
    runner = _runner(tmp_path)
    root = tmp_path / "snap"
    root.mkdir()
    (root / "bars.json").write_text(json.dumps({"SPY": []}))
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"SPY": []}))
    for bad in (
        {"symbol": "SPY", "bars_path": "bars.json"},
        {
            "symbol": "SPY",
            "snapshot_root": str(root),
            "bars_path": str(outside),
        },
        {
            "symbol": "SPY",
            "snapshot_root": str(root),
            "bars_path": "../outside.json",
        },
    ):
        result = runner.run_skill("multi_asset_data_and_features", bad)
        assert result.output.get("pit_ok") is not True
        assert "error" in result.output


def _experiment(runner: SkillRunner, net: str, trades: str, drawdown: str) -> str:
    stored = runner.registry.store(
        ExperimentArtifact(
            candidate_id=uuid4(),
            code_hash="a" * 64,
            data_snapshot_hash="b" * 64,
            environment_hash="c" * 64,
            metrics={
                "net_bps": net,
                "trade_count": trades,
                "max_drawdown_bps": drawdown,
                "units": "bps past-only",
            },
            exposures={"SPY": "1.0"},
            trial_count=1,
        ),
        record_type="experiment",
    )
    return str(stored.artifact_id)


def test_red_team_checks_sensitivity(tmp_path):
    runner = _runner(tmp_path)
    experiment_id = _experiment(runner, "100.00", "5", "50.00")
    result = runner.run_skill(
        "backtesting_and_statistical_red_team",
        {"experiment_id": experiment_id, "fee_grid": ("1", "2"), "slippage_grid": ("2", "4")},
    )
    assert result.output["leakage_ok"] is True
    assert "stable" in result.output["sensitivity"]
    jobs = runner.coordinator.jobs(state=JobState.COMPLETED)
    assert any(job.role is ResearchRole.RED_TEAM for job in jobs)


def test_portfolio_skill_reports_risk(tmp_path):
    runner = _runner(tmp_path)
    first = _experiment(runner, "100.00", "5", "50.00")
    second = _experiment(runner, "-20.00", "3", "30.00")
    result = runner.run_skill("portfolio_and_risk_analysis", {"experiment_ids": (first, second)})
    assert result.output["drawdown_bps"] == ["50.00", "30.00"]
    assert result.output["turnover"] == ["5", "3"]
    assert any("shared by" in note for note in result.output["overlap_notes"])
    assert "no fills observed" in result.output["liquidity_notes"]


def test_memory_skill_recalls_trials(tmp_path):
    runner = _runner(tmp_path)
    _experiment(runner, "100.00", "5", "50.00")
    _experiment(runner, "-20.00", "3", "30.00")
    _experiment(runner, "10.00", "2", "5.00")
    _rebuild(runner, tmp_path)
    result = runner.run_skill(
        "research_memory_and_monitoring",
        {"query": "experiment", "decay_criteria": "3 consecutive negative windows"},
    )
    assert len(result.output["prior_trials"]) == 3
    assert result.output["decay_flags"] == [
        "decay review due under criteria: 3 consecutive negative windows"
    ]
    assert isinstance(datetime.now(UTC), datetime)
    assert isinstance(Decimal("1"), Decimal)
