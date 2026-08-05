from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from advisorai.agents import (
    EvidenceCouncil,
    EvidenceGraph,
    MissionRequest,
    MissionRouter,
    WorkCandidate,
    WorkScheduler,
    run_adaptive_waves,
)
from advisorai.agents.council import RoleResult
from advisorai.agents.router import MissionKind
from advisorai.contracts import Evidence, Snapshot
from advisorai.ledger import LedgerNamespace, SqliteLedgers


def _evidence(timestamp, *, origin, family, claim, factor):
    return Evidence(
        claim=claim,
        source_family=family,
        origin=origin,
        observed_at=timestamp,
        first_available_at=timestamp,
        uncertainty=Decimal("0.1"),
        expires_at=timestamp + timedelta(hours=1),
    ), factor


def test_evidence_graph_discounts_syndicated_copies(timestamp):
    graph = EvidenceGraph()
    first, _ = _evidence(timestamp, origin="origin-a", family="news", claim="same", factor="news")
    second, _ = _evidence(timestamp, origin="origin-a", family="news", claim="copy", factor="news")
    graph.add(first, factor_family="news")
    graph.add(second, factor_family="news")
    result = graph.gate(minimum_source_families=2, minimum_factor_families=2)
    assert not result.passed
    assert second.artifact_id in result.discounted_evidence_ids


def test_evidence_graph_collapses_cross_origin_syndication_chain(timestamp):
    graph = EvidenceGraph()
    first = Evidence(
        claim="same",
        source_family="news",
        origin="wire-a",
        syndication_chain=("wire-a", "publisher"),
        observed_at=timestamp,
        first_available_at=timestamp,
        uncertainty=Decimal("0.1"),
        expires_at=timestamp + timedelta(hours=1),
    )
    second = first.model_copy(
        update={
            "artifact_id": uuid4(),
            "origin": "publisher-copy",
            "syndication_chain": ("publisher", "publisher-copy"),
        }
    )
    graph.add(first, factor_family="news")
    graph.add(second, factor_family="news")
    result = graph.gate(minimum_source_families=1, minimum_factor_families=1, material=False)
    assert result.independent_origins == ("wire-a",)
    assert second.artifact_id in result.discounted_evidence_ids


def test_evidence_graph_discounts_shared_model_ancestry(timestamp):
    graph = EvidenceGraph()
    first, _ = _evidence(
        timestamp, origin="origin-a", family="market", claim="up", factor="technical"
    )
    second, _ = _evidence(timestamp, origin="origin-b", family="news", claim="up", factor="news")
    graph.add(first, factor_family="technical", model_ancestry="model/prompt-v1")
    graph.add(second, factor_family="news", model_ancestry="model/prompt-v1")
    result = graph.gate(minimum_source_families=2, minimum_factor_families=2)
    assert not result.passed
    assert second.artifact_id in result.discounted_evidence_ids


def test_evidence_graph_rejects_future_and_expired_evidence(timestamp):
    graph = EvidenceGraph()
    future = Evidence(
        claim="future",
        source_family="market",
        origin="future-origin",
        observed_at=timestamp,
        first_available_at=timestamp + timedelta(minutes=1),
        uncertainty=Decimal("0.1"),
        expires_at=timestamp + timedelta(hours=1),
    )
    expired = Evidence(
        claim="expired",
        source_family="news",
        origin="expired-origin",
        observed_at=timestamp - timedelta(hours=2),
        first_available_at=timestamp - timedelta(hours=2),
        uncertainty=Decimal("0.1"),
        expires_at=timestamp,
    )
    graph.add(future, factor_family="technical")
    graph.add(expired, factor_family="news")
    result = graph.gate(
        minimum_source_families=1,
        minimum_factor_families=1,
        cutoff=timestamp,
    )
    assert not result.passed
    assert set(result.reasons) >= {
        "future_evidence_unavailable_at_cutoff",
        "expired_evidence_at_cutoff",
        "source_families:0<1",
    }


def test_evidence_graph_abstains_when_only_unsupported_claims_remain(timestamp):
    graph = EvidenceGraph()
    unsupported = Evidence(
        claim="unsupported",
        supports=False,
        source_family="news",
        origin="source-a",
        observed_at=timestamp,
        first_available_at=timestamp,
        uncertainty=Decimal("0.5"),
        expires_at=timestamp + timedelta(hours=1),
    )
    graph.add(unsupported, factor_family=" news ")
    result = graph.gate(minimum_source_families=1, minimum_factor_families=1, material=False)
    assert not result.passed
    assert unsupported.artifact_id in result.discounted_evidence_ids
    assert "unsupported_evidence_abstained" in result.reasons


def test_mission_router_is_policy_deterministic():
    router = MissionRouter()
    assert (
        router.route(MissionRequest(kind=MissionKind.TRADE, user_text="fast state")).mode.value
        == "trade_fast"
    )
    assert (
        router.route(MissionRequest(kind=MissionKind.BUILD, user_text="build collector")).mode.value
        == "builder"
    )
    assert (
        router.route(MissionRequest(kind=MissionKind.RECOVERY, user_text="restore")).mode.value
        == "recovery"
    )


def test_optional_work_scheduler_ranks_information_value_with_a_bound():
    candidates = (
        WorkCandidate(
            name="slow-api",
            expected_uncertainty_reduction=Decimal("2"),
            decision_value=Decimal("1"),
            latency_ms=100,
            resource_cost=Decimal("2"),
            api_cost=Decimal("1"),
        ),
        WorkCandidate(
            name="deterministic-check",
            expected_uncertainty_reduction=Decimal("1"),
            decision_value=Decimal("2"),
            latency_ms=10,
            resource_cost=Decimal("1"),
            api_cost=Decimal("0"),
        ),
    )
    ranked = WorkScheduler.select(candidates, budget=1)
    assert ranked[0].name == "deterministic-check"


def test_mission_router_rebuilds_routes_from_mission_ledger(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "missions.sqlite")
    request = MissionRequest(kind=MissionKind.RESEARCH, user_text="research")
    router = MissionRouter(ledgers)
    routed = router.route(request)
    restarted = MissionRouter(ledgers)
    assert restarted.routed_missions() == (routed,)
    assert len(ledgers.events(LedgerNamespace.MISSION)) == 1


def test_adaptive_wave_preserves_dissent_and_escalates(timestamp):
    snapshot = Snapshot(as_of=timestamp, purpose="council")
    mission = MissionRouter().route(MissionRequest(kind=MissionKind.RESEARCH, user_text="research"))
    first, _ = _evidence(
        timestamp, origin="market", family="market", claim="up", factor="technical"
    )
    second, _ = _evidence(
        timestamp, origin="fundamental", family="fundamental", claim="down", factor="fundamental"
    )
    third, _ = _evidence(
        timestamp, origin="risk", family="risk", claim="safe", factor="deterministic_check"
    )
    council = EvidenceCouncil(
        {
            "technical_flow": lambda _: RoleResult(
                "technical_flow", (first,), ("macro disagreement",), True
            ),
            "data_verifier": lambda _: RoleResult("data_verifier", (third,)),
            "skeptic_base_rate": lambda _: RoleResult(
                "skeptic_base_rate", (second,), ("technical dissent",), True
            ),
        }
    )
    graph = EvidenceGraph()
    results = run_adaptive_waves(
        council=council,
        snapshot=snapshot,
        mission=mission,
        graph=graph,
        initial_roles=("technical_flow",),
        optional_roles=("data_verifier", "skeptic_base_rate"),
    )
    assert len(results) == 3
    assert any(result.dissent for result in results)


def test_adaptive_waves_enforce_the_policy_role_budget(timestamp):
    snapshot = Snapshot(as_of=timestamp, purpose="budget")
    mission = MissionRouter().route(MissionRequest(kind=MissionKind.RESEARCH, user_text="research"))
    calls: list[str] = []

    def role(name):
        def run(_snapshot):
            calls.append(name)
            return RoleResult(name, ())

        return run

    council = EvidenceCouncil({name: role(name) for name in ("one", "two", "three")})
    bounded = mission.model_copy(update={"role_budget": 2})
    results = run_adaptive_waves(
        council=council,
        snapshot=snapshot,
        mission=bounded,
        graph=EvidenceGraph(),
        initial_roles=("one", "two"),
        optional_roles=("three",),
        minimum_source_families=1,
        minimum_factor_families=1,
    )
    assert len(results) == 2
    assert calls == ["one", "two"]


def test_adaptive_wave_budget_counts_missing_role_admissions(timestamp):
    snapshot = Snapshot(as_of=timestamp, purpose="missing-role-budget")
    mission = MissionRouter().route(MissionRequest(kind=MissionKind.RESEARCH, user_text="research"))
    calls: list[str] = []

    def role(name):
        def run(_snapshot):
            calls.append(name)
            return RoleResult(name, ())

        return run

    council = EvidenceCouncil({"present": role("present"), "optional": role("optional")})
    bounded = mission.model_copy(update={"role_budget": 2})
    results = run_adaptive_waves(
        council=council,
        snapshot=snapshot,
        mission=bounded,
        graph=EvidenceGraph(),
        initial_roles=("missing", "present"),
        optional_roles=("optional",),
        minimum_source_families=1,
        minimum_factor_families=1,
    )
    assert len(results) == 1
    assert calls == ["present"]


def test_adaptive_wave_records_typed_agent_run_metadata(timestamp):
    snapshot = Snapshot(as_of=timestamp, purpose="agent-run")
    mission = MissionRouter().route(MissionRequest(kind=MissionKind.RESEARCH, user_text="research"))
    evidence, _ = _evidence(
        timestamp, origin="venue", family="market", claim="fresh", factor="data_quality"
    )
    council = EvidenceCouncil(
        {
            "data_verifier": lambda _: RoleResult(
                "data_verifier",
                (evidence,),
                provider="direct",
                model_route="direct/provider/model",
                prompt_version="prompt-v1",
                tool_versions=("tool-v1",),
            )
        }
    )
    results = run_adaptive_waves(
        council=council,
        snapshot=snapshot,
        mission=mission,
        graph=EvidenceGraph(),
        initial_roles=("data_verifier",),
        minimum_source_families=1,
        minimum_factor_families=1,
    )
    assert results[0].agent_run is not None
    assert results[0].agent_run.mission_id == mission.mission_id
    assert results[0].agent_run.output_artifact_ids == (evidence.artifact_id,)


def test_role_result_rejects_mismatched_agent_run_metadata(timestamp):
    from advisorai.contracts import AgentRun

    snapshot = Snapshot(as_of=timestamp, purpose="agent-run-mismatch")
    with __import__("pytest").raises(ValueError, match="role"):
        RoleResult(
            role="data_verifier",
            evidence=(),
            agent_run=AgentRun(
                mission_id=uuid4(),
                role="technical_flow",
                mode="standard",
                snapshot_id=snapshot.artifact_id,
                latency_ms=0,
            ),
        )


def test_council_rejects_agent_run_bound_to_another_snapshot(timestamp):
    from advisorai.contracts import AgentRun

    snapshot = Snapshot(as_of=timestamp, purpose="agent-run-binding")
    other_snapshot = Snapshot(as_of=timestamp, purpose="other-snapshot")
    council = EvidenceCouncil(
        {
            "data_verifier": lambda _: RoleResult(
                role="data_verifier",
                evidence=(),
                agent_run=AgentRun(
                    mission_id=uuid4(),
                    role="data_verifier",
                    mode="council",
                    snapshot_id=other_snapshot.artifact_id,
                    input_artifact_ids=(other_snapshot.artifact_id,),
                    latency_ms=0,
                ),
            )
        }
    )
    with __import__("pytest").raises(ValueError, match="snapshot"):
        council.run_wave(
            snapshot=snapshot,
            roles=("data_verifier",),
            graph=EvidenceGraph(),
            mission_id=uuid4(),
        )
