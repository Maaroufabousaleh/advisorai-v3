"""Promotion contract for reproducible strategy candidates."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class StrategyValidation:
    economic_rationale: bool
    baseline_beaten: bool
    past_only: bool
    costs_capacity_passed: bool
    stress_passed: bool
    independent_implementation: bool
    no_trade_comparison: bool
    event_replay_passed: bool
    statistical_audit_passed: bool
    admitted: bool
    reasons: tuple[str, ...] = ()


def validate_strategy(
    *,
    economic_rationale: str,
    baseline_net_utility: Decimal,
    candidate_net_utility: Decimal,
    past_only: bool,
    costs_capacity_passed: bool,
    stress_passed: bool,
    implementation_hashes: tuple[str, ...],
    no_trade_comparison: bool,
    event_replay_passed: bool = True,
    statistical_audit_passed: bool = True,
) -> StrategyValidation:
    reasons: list[str] = []
    if any(not value.is_finite() for value in (baseline_net_utility, candidate_net_utility)):
        raise ValueError("strategy utility metrics must be finite")
    normalized_hashes = tuple(value.strip() for value in implementation_hashes)
    if any(not value for value in normalized_hashes):
        raise ValueError("strategy implementations require non-blank identities")
    rationale = bool(economic_rationale.strip())
    beaten = candidate_net_utility > baseline_net_utility
    independent = len(set(normalized_hashes)) >= 2
    checks = {
        "economic_rationale_missing": rationale,
        "baseline_not_beaten": beaten,
        "not_past_only": past_only,
        "cost_capacity_failed": costs_capacity_passed,
        "stress_failed": stress_passed,
        "independent_implementation_missing": independent,
        "no_trade_comparison_missing": no_trade_comparison,
        "event_replay_failed": event_replay_passed,
        "statistical_audit_failed": statistical_audit_passed,
    }
    reasons.extend(code for code, passed in checks.items() if not passed)
    return StrategyValidation(
        economic_rationale=rationale,
        baseline_beaten=beaten,
        past_only=past_only,
        costs_capacity_passed=costs_capacity_passed,
        stress_passed=stress_passed,
        independent_implementation=independent,
        no_trade_comparison=no_trade_comparison,
        event_replay_passed=event_replay_passed,
        statistical_audit_passed=statistical_audit_passed,
        admitted=not reasons,
        reasons=tuple(reasons),
    )
