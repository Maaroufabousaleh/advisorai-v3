from decimal import Decimal

from advisorai.research.strategy import validate_strategy
from advisorai.research.validity import evaluate_regime, evaluate_sensitivity


def test_strategy_requires_independent_implementations_and_baseline_value():
    result = validate_strategy(
        economic_rationale="liquidity premium",
        baseline_net_utility=Decimal("1"),
        candidate_net_utility=Decimal("2"),
        past_only=True,
        costs_capacity_passed=True,
        stress_passed=True,
        implementation_hashes=("a", "b"),
        no_trade_comparison=True,
    )
    assert result.admitted


def test_sensitivity_and_regime_validity_are_explicit():
    sensitivity = evaluate_sensitivity(
        parameter="lookback",
        values_tested=(Decimal("5"), Decimal("10"), Decimal("20")),
        utilities=(Decimal("1"), Decimal("1.1"), Decimal("0.95")),
        maximum_utility_range=Decimal("0.2"),
    )
    assert sensitivity.stable
    regime = evaluate_regime(
        regime="high_volatility",
        utilities=(Decimal("0.2"), Decimal("0.1")),
        minimum_observations=2,
    )
    assert regime.passed
