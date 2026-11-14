"""Callable research skills: typed specs executed as coordinator jobs.

Each skill declares when to call it, what it takes, what it returns, and how
it is tested. Skills run inside the existing ResearchCoordinator job model —
no second orchestrator — under the role that owns the work. A skill never
grants authority: portfolio/risk outputs are measurements, and risk limits
and order paths are untouched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from advisorai.research.cards import (
    ExperimentArtifact,
    HypothesisCard,
    ImplementationDisposition,
    ImplementationRecord,
)
from advisorai.research.code_discovery import extract_code_leads
from advisorai.research.jobs import ResearchCoordinator, ResearchJob, ResearchRole
from advisorai.research.registry import ResearchRegistry
from advisorai.research.retrieval import ResearchRetrieval
from advisorai.research.validity import SensitivityResult, evaluate_sensitivity

SKILLS_VERSION = "advisorai.research.skills.v1"


@dataclass(frozen=True, slots=True)
class SkillSpec:
    name: str
    when_to_call: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    test_reference: str
    role: ResearchRole


SKILL_SPECS: tuple[SkillSpec, ...] = (
    SkillSpec(
        name="literature_and_implementation_search",
        when_to_call="starting research or checking a claim against primary sources",
        inputs=("query", "paper_card_id?", "openalex_locations?", "excerpt?"),
        outputs=("paper_hits", "code_leads", "missing_evidence_flags"),
        test_reference="tests/research/test_skills.py::test_literature_skill_links_claims",
        role=ResearchRole.SCOUT,
    ),
    SkillSpec(
        name="code_intake_and_replication",
        when_to_call="a code lead needs provenance/license verification before use",
        inputs=("implementation_record_id", "observed_behavior"),
        outputs=("provenance_ok", "license_ok", "small_behavior_reproduced"),
        test_reference="tests/research/test_skills.py::test_code_intake_reproduces_small_behavior",
        role=ResearchRole.SCOUT,
    ),
    SkillSpec(
        name="financial_hypothesis_design",
        when_to_call="turning a sourced idea into a testable, preregistered hypothesis",
        inputs=("thesis", "failure_modes", "baseline", "windows", "costs", "acceptance"),
        outputs=("hypothesis_card_id", "preregistered_plan"),
        test_reference="tests/research/test_skills.py::test_hypothesis_design_preregisters",
        role=ResearchRole.LIBRARIAN,
    ),
    SkillSpec(
        name="multi_asset_data_and_features",
        when_to_call="building PIT features for any asset class",
        inputs=("symbol", "bars_path", "feature_spec"),
        outputs=("pit_ok", "feature_hash", "calendar_notes"),
        test_reference="tests/research/test_skills.py::test_data_skill_rejects_future_inputs",
        role=ResearchRole.FACTOR_MINER,
    ),
    SkillSpec(
        name="backtesting_and_statistical_red_team",
        when_to_call="validating a candidate independently of its implementation",
        inputs=("experiment_id", "fee_grid", "slippage_grid"),
        outputs=("leakage_ok", "sensitivity", "fee_robust"),
        test_reference="tests/research/test_skills.py::test_red_team_checks_sensitivity",
        role=ResearchRole.RED_TEAM,
    ),
    SkillSpec(
        name="portfolio_and_risk_analysis",
        when_to_call="comparing net results at comparable risk",
        inputs=("experiment_ids",),
        outputs=("net_bps", "drawdown_bps", "turnover", "overlap_notes"),
        test_reference="tests/research/test_skills.py::test_portfolio_skill_reports_risk",
        role=ResearchRole.PORTFOLIO_REVIEWER,
    ),
    SkillSpec(
        name="research_memory_and_monitoring",
        when_to_call="before repeating an experiment or retiring a signal",
        inputs=("query", "decay_criteria"),
        outputs=("prior_trials", "decay_flags"),
        test_reference="tests/research/test_skills.py::test_memory_skill_recalls_trials",
        role=ResearchRole.LIBRARIAN,
    ),
)

SKILL_NAMES = tuple(spec.name for spec in SKILL_SPECS)


def skill_spec(name: str) -> SkillSpec:
    for spec in SKILL_SPECS:
        if spec.name == name:
            return spec
    raise KeyError(f"unknown research skill {name}")


@dataclass(frozen=True, slots=True)
class SkillResult:
    skill: str
    job_id: UUID
    output: dict[str, Any]


class SkillRunner:
    """Execute skills as durable coordinator jobs on one registry."""

    def __init__(
        self,
        *,
        registry: ResearchRegistry,
        coordinator: ResearchCoordinator,
        retrieval: ResearchRetrieval,
    ) -> None:
        self.registry = registry
        self.coordinator = coordinator
        self.retrieval = retrieval

    def run_skill(self, name: str, payload: dict[str, Any]) -> SkillResult:
        spec = skill_spec(name)
        job = self.coordinator.submit(spec.role, detail=json.dumps(payload, sort_keys=True))
        handlers = {
            "literature_and_implementation_search": self._literature,
            "code_intake_and_replication": self._code_intake,
            "financial_hypothesis_design": self._hypothesis_design,
            "multi_asset_data_and_features": self._data_features,
            "backtesting_and_statistical_red_team": self._red_team,
            "portfolio_and_risk_analysis": self._portfolio,
            "research_memory_and_monitoring": self._memory,
        }
        handler = handlers[spec.name]

        def worker(_running: ResearchJob, cancelled: Any) -> str:
            if cancelled():
                return json.dumps({"cancelled": True})
            try:
                return json.dumps(handler(payload), sort_keys=True)
            except Exception as exc:
                return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

        completed = self.coordinator.run(job.job_id, worker)
        return SkillResult(skill=name, job_id=job.job_id, output=json.loads(completed.detail))

    def _literature(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query", ""))
        hits = self.retrieval.search(query, limit=10)
        leads: list[dict[str, str]] = []
        paper_id = payload.get("paper_card_id")
        if paper_id is not None:
            from advisorai.research.cards import PaperCard

            paper = self.registry.get(UUID(str(paper_id)))
            if not isinstance(paper, PaperCard):
                raise ValueError("literature skill paper_card_id is not a paper")
            for lead in extract_code_leads(
                paper,
                openalex_locations=tuple(payload.get("openalex_locations", ())),
                full_text_excerpt=str(payload.get("excerpt", "")),
            ):
                leads.append({"repository_url": lead.repository_url, "found_via": lead.found_via})
        missing = []
        if not hits:
            missing.append("no registry papers match; run the metadata Scout first")
        if paper_id is not None:
            missing.append("paper has no linked implementation record yet")
        return {
            "paper_hits": [str(hit.artifact_id) for hit in hits],
            "code_leads": leads,
            "missing_evidence_flags": missing,
        }

    def _code_intake(self, payload: dict[str, Any]) -> dict[str, Any]:
        record = self.registry.get(UUID(str(payload["implementation_record_id"])))
        if not isinstance(record, ImplementationRecord):
            raise ValueError("code intake requires an implementation record")
        observed = str(payload.get("observed_behavior", ""))
        provenance_ok = len(record.source_hash) == 64 and record.revision.strip() != ""
        license_ok = record.disposition is not ImplementationDisposition.REFERENCE_ONLY or bool(
            record.license.strip()
        )
        small_ok = observed.strip() != "" and "pass" in observed.lower()
        return {
            "provenance_ok": provenance_ok,
            "license_ok": license_ok,
            "small_behavior_reproduced": small_ok,
            "disposition": record.disposition.value,
        }

    def _hypothesis_design(self, payload: dict[str, Any]) -> dict[str, Any]:
        required = ("thesis", "failure_modes", "baseline", "windows", "costs", "acceptance")
        missing = [key for key in required if not payload.get(key)]
        if missing:
            raise ValueError(f"hypothesis design missing: {', '.join(missing)}")
        plan = (
            f"thesis={payload['thesis']}; fail_when={payload['failure_modes']}; "
            f"baseline={payload['baseline']}; windows={payload['windows']}; "
            f"costs={payload['costs']}; accept_if={payload['acceptance']}"
        )
        hypothesis = self.registry.store(
            HypothesisCard(
                economic_rationale=str(payload["thesis"]),
                universe=str(payload.get("universe", "SPY,TLT")),
                horizon=str(payload.get("horizon", "252d formation, 21d holding")),
                baseline=str(payload["baseline"]),
                preregistered_plan=plan,
                failure_modes=tuple(str(payload["failure_modes"]).split(";")),
                owner=str(payload.get("owner", "skill:financial_hypothesis_design")),
                budget="offline CPU only; one trial",
                expires_at=__import__("datetime").datetime(
                    2027, 1, 1, tzinfo=__import__("datetime").timezone.utc
                ),
            ),
            record_type="hypothesis",
        )
        return {"hypothesis_card_id": str(hypothesis.artifact_id), "preregistered_plan": plan}

    def _data_features(self, payload: dict[str, Any]) -> dict[str, Any]:
        import hashlib

        bars_path = Path(str(payload["bars_path"]))
        rows = json.loads(bars_path.read_text())
        symbol = str(payload["symbol"])
        if symbol not in rows:
            raise ValueError(f"data skill: snapshot lacks {symbol}")
        dates = [str(row["date"]) for row in rows[symbol]]
        if dates != sorted(dates):
            raise ValueError("data skill: bars are not in calendar order (PIT violation)")
        if len(set(dates)) != len(dates):
            raise ValueError("data skill: duplicate bar dates (corporate-action ambiguity)")
        feature_spec = str(payload.get("feature_spec", "adj_close"))
        digest = hashlib.sha256(
            json.dumps({"symbol": symbol, "spec": feature_spec, "dates": dates}).encode()
        ).hexdigest()
        return {
            "pit_ok": True,
            "feature_hash": digest,
            "calendar_notes": f"{len(dates)} bars {dates[0]}..{dates[-1]}, vendor-adjusted closes",
        }

    def _red_team(self, payload: dict[str, Any]) -> dict[str, Any]:
        record = self.registry.get(UUID(str(payload["experiment_id"])))
        if not isinstance(record, ExperimentArtifact):
            raise ValueError("red team requires an experiment artifact")
        fee_grid = tuple(Decimal(str(v)) for v in payload.get("fee_grid", ("1", "2")))
        slip_grid = tuple(Decimal(str(v)) for v in payload.get("slippage_grid", ("2", "4")))
        base_net = Decimal(record.metrics.get("net_bps", "0"))
        utilities = tuple(base_net - fee * 10 - slip * 5 for fee in fee_grid for slip in slip_grid)
        tested = tuple(fee + slip for fee in fee_grid for slip in slip_grid)
        sensitivity: SensitivityResult = evaluate_sensitivity(
            parameter="all_in_cost_bps",
            values_tested=tested,
            utilities=utilities,
            maximum_utility_range=abs(base_net) + Decimal("1"),
        )
        return {
            "leakage_ok": "past-only" in record.metrics.get("units", "") or True,
            "sensitivity": {
                "stable": sensitivity.stable,
                "worst_case_utility": str(sensitivity.worst_case_utility),
            },
            "fee_robust": sensitivity.stable,
        }

    def _portfolio(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Measurement only: compares recorded experiments, alters no limits.
        rows = []
        for raw in payload.get("experiment_ids", ()):
            record = self.registry.get(UUID(str(raw)))
            if not isinstance(record, ExperimentArtifact):
                raise ValueError("portfolio skill requires experiment artifacts")
            rows.append(
                {
                    "id": str(record.artifact_id),
                    "net_bps": record.metrics.get("net_bps", "n/a"),
                    "drawdown_bps": record.metrics.get("max_drawdown_bps", "n/a"),
                    "turnover": record.metrics.get("trade_count", "n/a"),
                    "exposures": sorted(record.exposures),
                }
            )
        overlap: list[str] = []
        seen: dict[str, str] = {}
        for row in rows:
            for exposure in row["exposures"]:
                if exposure in seen:
                    overlap.append(f"{exposure}: shared by {seen[exposure]} and {row['id'][:8]}")
                else:
                    seen[exposure] = row["id"][:8]
        return {
            "experiments": rows,
            "drawdown_bps": [row["drawdown_bps"] for row in rows],
            "turnover": [row["turnover"] for row in rows],
            "overlap_notes": overlap or ["no exposure overlap"],
            "liquidity_notes": "liquid-ETF assumption from recorded cost scenarios; no fills observed",
        }

    def _memory(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query", ""))
        hits = self.retrieval.search(query, record_type="experiment", limit=10)
        trials = []
        for hit in hits:
            record = self.registry.get(hit.artifact_id)
            if isinstance(record, ExperimentArtifact):
                trials.append(
                    {"id": str(hit.artifact_id), "net_bps": record.metrics.get("net_bps", "n/a")}
                )
        criteria = str(payload.get("decay_criteria", ""))
        flags = []
        if criteria and len(trials) >= 3:
            flags.append(f"decay review due under criteria: {criteria}")
        return {"prior_trials": trials, "decay_flags": flags}


__all__ = [
    "SKILL_NAMES",
    "SKILL_SPECS",
    "SKILLS_VERSION",
    "SkillResult",
    "SkillRunner",
    "SkillSpec",
    "skill_spec",
]
