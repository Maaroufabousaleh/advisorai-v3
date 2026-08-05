"""Elastic typed evidence roles and adaptive wave scheduling."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from uuid import UUID

from advisorai.agents.fusion import EvidenceGraph
from advisorai.agents.router import RoutedMission
from advisorai.contracts import AgentRun, Evidence, Snapshot


@dataclass(frozen=True, slots=True)
class RoleResult:
    role: str
    evidence: tuple[Evidence, ...]
    dissent: tuple[str, ...] = ()
    unresolved: bool = False
    provider: str | None = None
    model_route: str | None = None
    prompt_version: str | None = None
    tool_versions: tuple[str, ...] = ()
    agent_run: AgentRun | None = None

    def __post_init__(self) -> None:
        if not self.role.strip():
            raise ValueError("evidence role must be non-empty")
        if any(not item.strip() for item in self.dissent):
            raise ValueError("evidence dissent entries cannot be blank")
        if any(not item.strip() for item in self.tool_versions):
            raise ValueError("evidence tool versions cannot be blank")
        if len(self.tool_versions) != len(set(self.tool_versions)):
            raise ValueError("evidence tool versions must be unique")
        if (self.provider is None) != (self.model_route is None):
            raise ValueError("provider and model route metadata must be supplied together")
        if self.provider is not None and not self.provider.strip():
            raise ValueError("provider metadata cannot be blank")
        if self.model_route is not None and not self.model_route.strip():
            raise ValueError("model route metadata cannot be blank")
        if self.prompt_version is not None and not self.prompt_version.strip():
            raise ValueError("prompt version metadata cannot be blank")
        if self.agent_run is not None:
            if self.agent_run.role != self.role:
                raise ValueError("agent run role must match the typed evidence role")
            if (
                self.agent_run.provider != self.provider
                or self.agent_run.model_route != self.model_route
            ):
                raise ValueError("agent run provider metadata must match the typed role result")
            if self.agent_run.prompt_version != self.prompt_version:
                raise ValueError("agent run prompt metadata must match the typed role result")
            if self.agent_run.tool_versions != self.tool_versions:
                raise ValueError("agent run tool metadata must match the typed role result")
            evidence_ids = {item.artifact_id for item in self.evidence}
            if not evidence_ids.issubset(set(self.agent_run.output_artifact_ids)):
                raise ValueError("agent run must record every typed evidence output")


class EvidenceCouncil:
    """Roles are callables admitted on demand; no role can create an order."""

    DEFAULT_ROLES = (
        "data_verifier",
        "technical_flow",
        "derivatives_regime",
        "news_event",
        "skeptic_base_rate",
        "risk_opportunity",
        "synthesizer",
    )

    def __init__(self, role_functions: dict[str, Callable[[Snapshot], RoleResult]]) -> None:
        self.role_functions = role_functions

    def run_wave(
        self,
        *,
        snapshot: Snapshot,
        roles: Sequence[str],
        graph: EvidenceGraph,
        mission_id: UUID | None = None,
    ) -> tuple[RoleResult, ...]:
        results: list[RoleResult] = []
        seen: set[str] = set()
        for raw_role in roles:
            if not raw_role.strip():
                raise ValueError("evidence role names cannot be blank")
            role = raw_role.strip()
            if role in seen:
                continue
            seen.add(role)
            function = self.role_functions.get(role)
            if function is None:
                # A council may intentionally deploy only a subset of the
                # logical roster. Missing specialists remain explicit absence,
                # rather than turning a partial council into a hard process
                # failure; the evidence gate records the resulting deficit.
                continue
            result = function(snapshot)
            if result.role.strip() != role:
                raise ValueError(f"evidence role {role!r} returned a result for {result.role!r}")
            if result.agent_run is not None:
                run = result.agent_run
                if run.snapshot_id != snapshot.artifact_id:
                    raise ValueError("agent run snapshot must match the council snapshot")
                if mission_id is not None and run.mission_id != mission_id:
                    raise ValueError("agent run mission must match the council mission")
                if snapshot.artifact_id not in run.input_artifact_ids:
                    raise ValueError("agent run must record the council snapshot as an input")
            for evidence in result.evidence:
                graph.add(evidence, factor_family=role)
            if result.agent_run is None and mission_id is not None:
                run = AgentRun(
                    mission_id=mission_id,
                    role=role,
                    mode="council",
                    snapshot_id=snapshot.artifact_id,
                    provider=result.provider,
                    model_route=result.model_route,
                    prompt_version=result.prompt_version,
                    tool_versions=result.tool_versions,
                    input_artifact_ids=(snapshot.artifact_id,),
                    output_artifact_ids=tuple(item.artifact_id for item in result.evidence),
                    latency_ms=0,
                )
                result = replace(result, agent_run=run)
            results.append(result)
        return tuple(results)


def run_adaptive_waves(
    *,
    council: EvidenceCouncil,
    snapshot: Snapshot,
    mission: RoutedMission,
    graph: EvidenceGraph,
    initial_roles: Sequence[str],
    optional_roles: Sequence[str] = (),
    minimum_source_families: int = 2,
    minimum_factor_families: int = 3,
) -> tuple[RoleResult, ...]:
    if mission.role_budget < 1:
        return ()
    initial = tuple(dict.fromkeys(role.strip() for role in initial_roles if role.strip()))[
        : mission.role_budget
    ]
    results = list(
        council.run_wave(
            snapshot=snapshot,
            roles=initial,
            graph=graph,
            mission_id=mission.mission_id,
        )
    )
    gate = graph.gate(
        minimum_source_families=minimum_source_families,
        minimum_factor_families=minimum_factor_families,
        cutoff=snapshot.as_of,
    )
    unresolved = any(result.unresolved or result.dissent for result in results)
    if (not gate.passed or unresolved) and mission.mode.value in {"deep", "standard"}:
        # The budget limits admitted role attempts, not only roles that happen
        # to have a registered function.  Missing specialists are explicit
        # evidence gaps and must not free budget for unbounded optional work.
        remaining_budget = mission.role_budget - len(initial)
        remaining = [
            role.strip() for role in optional_roles if role.strip() and role.strip() not in initial
        ]
        remaining = tuple(dict.fromkeys(remaining))[: max(0, remaining_budget)]
        results.extend(
            council.run_wave(
                snapshot=snapshot,
                roles=remaining,
                graph=graph,
                mission_id=mission.mission_id,
            )
        )
    return tuple(results)
