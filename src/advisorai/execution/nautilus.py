"""NautilusTrader ownership boundary for replay and execution.

The production adapter must be backed by the pinned NautilusTrader runtime. A
small deterministic replay double is available only when explicitly requested by
tests; it never creates live credentials or a live venue connection.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Iterable

from advisorai.execution.events import MarketEvent, ReplayEngine
from advisorai.gates import PhaseGateRegistry


class NautilusRuntimeError(RuntimeError):
    pass


class NautilusTraderPipeline:
    engine_name = "nautilus_trader"

    def __init__(
        self,
        *,
        test_double: bool = False,
        phase0_admitted: bool = False,
        gate_registry: PhaseGateRegistry | None = None,
        replay_runner: Callable[[Iterable[MarketEvent], Callable[[MarketEvent], None]], int]
        | None = None,
    ) -> None:
        self.test_double = test_double
        self.phase0_admitted = phase0_admitted
        self.runtime_available = importlib.util.find_spec("nautilus_trader") is not None
        if phase0_admitted and (gate_registry is None or not gate_registry.is_admitted(0)):
            raise NautilusRuntimeError(
                "NautilusTrader admission requires a recorded passing Phase 0 gate and replay runner"
            )
        if not test_double and not phase0_admitted:
            raise NautilusRuntimeError(
                "NautilusTrader is quarantined until the Phase 0 replay/resource gate passes"
            )
        if not self.runtime_available and not test_double:
            raise NautilusRuntimeError(
                "NautilusTrader is not installed; Phase 0 replay gate must pass before production use"
            )
        if phase0_admitted and replay_runner is None:
            raise NautilusRuntimeError(
                "an admitted Nautilus runtime requires an injected pinned replay runner"
            )
        self._replay_runner = replay_runner

    def replay(self, events: Iterable[MarketEvent], handler: Callable[[MarketEvent], None]) -> int:
        if not self.runtime_available and not self.test_double:
            raise NautilusRuntimeError("NautilusTrader runtime unavailable")
        if self.phase0_admitted:
            if self._replay_runner is None:
                raise NautilusRuntimeError("admitted Nautilus replay runner is missing")
            count = self._replay_runner(events, handler)
            if not isinstance(count, int) or count < 0:
                raise NautilusRuntimeError("Nautilus replay runner returned an invalid count")
            return count
        return ReplayEngine().replay(events, handler)
