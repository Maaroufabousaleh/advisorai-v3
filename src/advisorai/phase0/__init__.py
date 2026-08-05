"""Phase 0 reproducibility, availability, and component bake-off utilities."""

from .bakeoffs import (
    BakeoffGate,
    BakeoffResult,
    CandidateAvailability,
    ComponentCandidate,
    ComponentKind,
    StabilityWindow,
    benchmark_callable,
    benchmark_gateway_adapter,
    evaluate_stability,
    record_bakeoff_gate,
    recorded_bakeoff_gates,
    run_availability_inventory,
)

__all__ = [
    "BakeoffGate",
    "BakeoffResult",
    "CandidateAvailability",
    "ComponentCandidate",
    "ComponentKind",
    "StabilityWindow",
    "evaluate_stability",
    "benchmark_callable",
    "benchmark_gateway_adapter",
    "record_bakeoff_gate",
    "recorded_bakeoff_gates",
    "run_availability_inventory",
]
