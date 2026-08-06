from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from advisorai.models import (
    AbstentionPolicy,
    ForecastSeries,
    GpuModelLease,
    LightGBMForecastAdapter,
    ModelUnavailable,
    OptionalForecastAdapter,
    RollingCalibrator,
    TSPulseAdapter,
    TTMR2ForecastAdapter,
    build_forecast,
    evaluate_forecasts,
)
from advisorai.models.forecasting import DriftForecaster, SeriesPoint


def _series(btc_usdt, timestamp):
    return ForecastSeries(
        instrument=btc_usdt,
        target="one_hour_return",
        points=tuple(
            SeriesPoint(at=timestamp + timedelta(minutes=index), value=Decimal(str(index)))
            for index in range(1, 6)
        ),
        snapshot_id=uuid4(),
        training_cutoff=timestamp + timedelta(minutes=5),
    )


def test_baseline_forecast_is_typed_and_reproducible(btc_usdt, timestamp):
    series = _series(btc_usdt, timestamp)
    first = build_forecast(
        series=series,
        model=DriftForecaster(),
        horizon_seconds=3600,
        calibration_version="cal-v1",
        code_version="code-v1",
    )
    second = build_forecast(
        series=series,
        model=DriftForecaster(),
        horizon_seconds=3600,
        calibration_version="cal-v1",
        code_version="code-v1",
    )
    assert first.point_forecast == second.point_forecast
    assert first.model_version == "drift"


def test_forecast_rejects_training_cutoff_before_latest_input(btc_usdt, timestamp):
    with pytest.raises(ValueError, match="latest forecast input"):
        build_forecast(
            series=_series(btc_usdt, timestamp),
            model=DriftForecaster(),
            horizon_seconds=3600,
            calibration_version="cal-v1",
            code_version="code-v1",
            training_cutoff=timestamp + timedelta(minutes=4),
        )


def test_forecast_separates_decision_cutoff_from_training_cutoff(btc_usdt, timestamp):
    forecast = build_forecast(
        series=_series(btc_usdt, timestamp),
        model=DriftForecaster(),
        horizon_seconds=3600,
        calibration_version="cal-v1",
        code_version="code-v1",
        training_cutoff=timestamp + timedelta(minutes=5),
        cutoff=timestamp + timedelta(minutes=10),
    )
    assert forecast.training_cutoff < forecast.cutoff


def test_calibration_and_abstention_are_past_only():
    calibrator = RollingCalibrator(window=3)
    calibrator.update(Decimal("1"), Decimal("2"))
    calibrator.update(Decimal("2"), Decimal("4"))
    assert calibrator.scale == Decimal("1.5")
    assert AbstentionPolicy(max_scale=Decimal("1")).should_abstain(
        history_length=5, calibration_scale=calibrator.scale
    )


def test_evaluation_requires_net_marginal_value():
    report = evaluate_forecasts(
        model_name="naive",
        predictions=(Decimal("1"), Decimal("-1"), Decimal("1")),
        actuals=(Decimal("2"), Decimal("-2"), Decimal("-1")),
        baseline_utility=Decimal("4"),
    )
    assert report.observations == 3
    assert not report.adds_marginal_value


def test_evaluation_accepts_signed_correlation_metrics():
    report = evaluate_forecasts(
        model_name="candidate",
        predictions=(Decimal("1"), Decimal("-1")),
        actuals=(Decimal("1"), Decimal("-1")),
        baseline_utility=Decimal("0"),
        rank_ic=Decimal("-0.2"),
        error_correlation=Decimal("-0.5"),
    )
    assert report.rank_ic == Decimal("-0.2")


def test_optional_model_is_quarantined_when_dependency_missing():
    model = OptionalForecastAdapter("chronos-2-small", "module_that_does_not_exist", gpu=True)
    with pytest.raises(ModelUnavailable, match="unavailable"):
        model.predict((Decimal("1"),), 1)


def test_lightgbm_baseline_does_not_silently_substitute_when_unavailable():
    model = LightGBMForecastAdapter()
    if not model.available:
        with pytest.raises(ModelUnavailable, match="unavailable"):
            model.predict((Decimal("1"), Decimal("2")), 1)
    else:
        with pytest.raises(ModelUnavailable, match="pinned checkpoint"):
            model.predict((Decimal("1"), Decimal("2")), 1)


def test_named_model_adapters_require_pinned_runner_and_checkpoint():
    model = TTMR2ForecastAdapter()
    if not model.available:
        with pytest.raises(ModelUnavailable, match="unavailable"):
            model.predict((Decimal("1"), Decimal("2")), 1)


def test_optional_forecast_runner_is_typed_and_checkpoint_bound():
    model = TTMR2ForecastAdapter(
        runner=lambda values, horizon: [
            values[-1] + Decimal(index) for index in range(1, horizon + 1)
        ],
        checkpoint_hash="a" * 64,
    )
    model.available = True
    assert model.predict((Decimal("1"), Decimal("2")), 2) == (Decimal("3"), Decimal("4"))


def test_tspulse_runner_can_emit_typed_integrity_features():
    model = TSPulseAdapter(
        runner=lambda values, _horizon: (sum(values) / Decimal(len(values)),),
        checkpoint_hash="b" * 64,
    )
    model.available = True
    assert model.extract_features((Decimal("1"), Decimal("3"))) == (Decimal("2"),)


def test_tspulse_is_integrity_feature_only():
    model = TSPulseAdapter()
    with pytest.raises(ModelUnavailable, match="integrity/regime"):
        model.predict((Decimal("1"), Decimal("2")), 1)


def test_gpu_lease_is_global():
    with GpuModelLease("chronos"):
        with pytest.raises(RuntimeError, match="GPU lease"):
            with GpuModelLease("kronos"):
                pass
    with GpuModelLease("kronos"):
        pass
