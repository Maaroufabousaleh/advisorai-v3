"""Runtime probes and narrow adapters for PydanticAI, Prefect, and Hamilton."""

from __future__ import annotations

import importlib.metadata
import importlib.util
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from advisorai.gates import PhaseGateRegistry


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    name: str
    import_name: str
    available: bool
    version: str | None
    production_admitted: bool
    reason: str


def _status(
    name: str,
    import_name: str,
    *,
    phase0_admitted: bool = False,
    gate_registry: PhaseGateRegistry | None = None,
) -> RuntimeStatus:
    available = importlib.util.find_spec(import_name) is not None
    version = None
    if available:
        aliases = {
            "pydantic_ai": "pydantic-ai",
            "pydantic_graph": "pydantic-graph",
            "Hamilton": "sf-hamilton",
        }
        distributions = (aliases.get(import_name, import_name),)
        version = next(
            (
                importlib.metadata.version(distribution)
                for distribution in distributions
                if _has_version(distribution)
            ),
            "installed",
        )
    admitted = phase0_admitted and gate_registry is not None and gate_registry.is_admitted(0)
    return RuntimeStatus(
        name=name,
        import_name=import_name,
        available=available,
        version=version,
        # Availability is an inventory fact, not an admission decision. The
        # Phase 0 gate must be recorded before a canonical runtime can execute.
        production_admitted=available and admitted,
        reason=(
            "runtime admitted by the recorded Phase 0 bake-off"
            if available and admitted
            else "runtime available but quarantined until pinned Phase 0 bake-off"
            if available
            else "runtime quarantined until pinned bake-off"
        ),
    )


def _has_version(distribution: str) -> bool:
    try:
        importlib.metadata.version(distribution)
        return True
    except importlib.metadata.PackageNotFoundError:
        return False


class PydanticRuntime:
    def __init__(
        self,
        *,
        phase0_admitted: bool = False,
        gate_registry: PhaseGateRegistry | None = None,
    ) -> None:
        self.status = _status(
            "pydantic-ai",
            "pydantic_ai",
            phase0_admitted=phase0_admitted,
            gate_registry=gate_registry,
        )

    def run_typed_agent(
        self,
        runner: Callable[[Mapping[str, object]], Mapping[str, object]],
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        if not self.status.production_admitted:
            raise RuntimeError(self.status.reason)
        result = runner(payload)
        if not isinstance(result, Mapping):
            raise TypeError("typed agent runner must return a mapping payload")
        return result


class PrefectRuntime:
    def __init__(
        self,
        *,
        phase0_admitted: bool = False,
        gate_registry: PhaseGateRegistry | None = None,
    ) -> None:
        self.status = _status(
            "prefect",
            "prefect",
            phase0_admitted=phase0_admitted,
            gate_registry=gate_registry,
        )

    def run_flow(self, flow: Callable[[], object]) -> object:
        if not self.status.production_admitted:
            raise RuntimeError(self.status.reason)
        return flow()


class HamiltonRuntime:
    def __init__(
        self,
        *,
        phase0_admitted: bool = False,
        gate_registry: PhaseGateRegistry | None = None,
    ) -> None:
        self.status = _status(
            "hamilton",
            "Hamilton",
            phase0_admitted=phase0_admitted,
            gate_registry=gate_registry,
        )

    def compute_features(
        self,
        feature_fn: Callable[[Mapping[str, object]], Mapping[str, object]],
        inputs: Mapping[str, object],
    ) -> Mapping[str, object]:
        if not self.status.production_admitted:
            raise RuntimeError(self.status.reason)
        result = feature_fn(inputs)
        if not isinstance(result, Mapping):
            raise TypeError("Hamilton feature runner must return a mapping")
        return result
