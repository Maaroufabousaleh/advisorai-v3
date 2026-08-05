from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from advisorai.attribution import AttributionIncident, AttributionReconciler
from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.observability import IncidentLedger
from advisorai.portfolio import PortfolioComparator
from advisorai.research import MultipleTestingAudit, PurgedWalkForward
from advisorai.risk import RiskAnalytics, StressScenario


def test_portfolio_comparison_rejects_unstable_challenger():
    result = PortfolioComparator().compare(
        name="challenger",
        expected_return=Decimal("10"),
        expected_cost=Decimal("1"),
        turnover=Decimal("2"),
        max_concentration=Decimal("0.8"),
        stable_out_of_sample=False,
        risk_limit=Decimal("0.5"),
    )
    assert not result.admitted
    assert set(result.rejection_reasons) == {"unstable_out_of_sample", "concentration_limit"}


def test_portfolio_comparator_requires_all_simple_benchmarks():
    comparator = PortfolioComparator()
    comparisons = tuple(
        comparator.compare(
            name=name,
            expected_return=Decimal("1"),
            expected_cost=Decimal("0.1"),
            turnover=Decimal("0"),
            max_concentration=Decimal("0.2"),
            stable_out_of_sample=True,
            risk_limit=Decimal("0.5"),
        )
        for name in comparator.REQUIRED_BENCHMARKS
    )
    assert [item.name for item in comparator.require_benchmark_set(comparisons)] == list(
        comparator.REQUIRED_BENCHMARKS
    )


def test_risk_analytics_covariance_capacity_margin_and_stress():
    analytics = RiskAnalytics()
    volatility = analytics.volatility(
        instrument="BTC",
        returns=(Decimal("0.01"), Decimal("-0.02"), Decimal("0.03")),
    )
    assert volatility.annualized_volatility > 0
    assert (
        analytics.beta(
            returns=(Decimal("0.01"), Decimal("0.02"), Decimal("0.00")),
            benchmark_returns=(Decimal("0.01"), Decimal("0.01"), Decimal("0.00")),
        )
        > 0
    )
    tail = analytics.tail_risk(
        returns=(Decimal("0.01"), Decimal("-0.04"), Decimal("-0.02"), Decimal("0.01"))
    )
    assert tail.expected_shortfall_loss >= tail.var_loss
    names, covariance = analytics.robust_covariance(
        {
            "BTC": (Decimal("0.01"), Decimal("0.02"), Decimal("-0.01")),
            "ETH": (Decimal("0.02"), Decimal("0.01"), Decimal("-0.02")),
        }
    )
    assert names == ("BTC", "ETH")
    assert covariance[0][0] > 0
    capacity = analytics.capacity(
        instrument="BTC",
        daily_volume=Decimal("100000"),
        participation_limit=Decimal("0.1"),
        order_notional=Decimal("20000"),
        spread_bps=Decimal("5"),
    )
    assert not capacity.capacity_passed
    margin = analytics.margin(
        equity=Decimal("1000"),
        gross_notional=Decimal("5000"),
        initial_rate=Decimal("0.2"),
        maintenance_rate=Decimal("0.1"),
    )
    assert margin.passed
    stress = analytics.stress(
        scenario="gap",
        notionals={"BTC": Decimal("5000")},
        price_shock=Decimal("-0.3"),
        liquidity_multiplier=Decimal("0.2"),
        equity=Decimal("1000"),
        maintenance_rate=Decimal("0.1"),
    )
    assert not stress.passed


def test_purged_walk_forward_and_multiple_testing():
    start = __import__("datetime").datetime(2026, 8, 1, tzinfo=__import__("datetime").UTC)
    split = PurgedWalkForward.make(
        train_start=start,
        train_end=start + timedelta(days=10),
        validation_start=start + timedelta(days=12),
        validation_end=start + timedelta(days=14),
        embargo_seconds=86400,
    )
    assert split.passed
    audit = MultipleTestingAudit.bonferroni(
        tests_run=10, raw_p_value=Decimal("0.01"), alpha=Decimal("0.05")
    )
    assert not audit.passed


def test_attribution_residual_becomes_incident():
    reconciler = AttributionReconciler(tolerance=Decimal("0.01"))
    with pytest.raises(AttributionIncident, match="residual"):
        reconciler.reconcile(
            reconciliation_id=uuid4(),
            total_pnl=Decimal("10"),
            data_forecast=Decimal("1"),
            allocation_selection=Decimal("1"),
            risk_overlay=Decimal("1"),
            execution_financing=Decimal("1"),
            regime_capacity=Decimal("1"),
            currency="USD",
        )


def test_attribution_residual_is_durable_when_an_incident_ledger_is_configured(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "attribution.sqlite")
    reconciler = AttributionReconciler(incident_ledger=IncidentLedger(ledgers))
    with pytest.raises(AttributionIncident):
        reconciler.reconcile(
            reconciliation_id=uuid4(),
            total_pnl=Decimal("10"),
            data_forecast=Decimal("1"),
            allocation_selection=Decimal("1"),
            risk_overlay=Decimal("1"),
            execution_financing=Decimal("1"),
            regime_capacity=Decimal("1"),
            currency="USD",
        )
    assert len(ledgers.events(LedgerNamespace.INCIDENT)) == 1


def test_attribution_artifact_carries_a_self_reconciling_total():
    total = Decimal("5")
    artifact = AttributionReconciler().reconcile(
        reconciliation_id=uuid4(),
        total_pnl=total,
        data_forecast=Decimal("1"),
        allocation_selection=Decimal("1"),
        risk_overlay=Decimal("1"),
        execution_financing=Decimal("1"),
        regime_capacity=Decimal("1"),
        currency="usd",
    )
    assert artifact.total_pnl == total
    assert artifact.total_pnl == sum(
        (
            artifact.data_forecast,
            artifact.allocation_selection,
            artifact.risk_overlay,
            artifact.execution_financing,
            artifact.regime_capacity,
            artifact.unexplained_residual,
        ),
        Decimal("0"),
    )


def test_stress_suite_covers_required_operational_and_market_scenarios():
    results = RiskAnalytics().stress_suite(
        notionals={"BTC": Decimal("5000")},
        equity=Decimal("1000"),
        maintenance_rate=Decimal("0.1"),
    )
    assert len(results) == len(tuple(StressScenario))
    assert {result.scenario for result in results} >= {
        "venue_outage",
        "stablecoin_depeg",
        "price_gap",
    }
