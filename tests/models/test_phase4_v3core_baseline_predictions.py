from __future__ import annotations

from decimal import Decimal

from scripts.run_phase4_v3core_baseline_predictions import _predict_prices


def test_all_mandatory_baselines_produce_one_hour_price_paths() -> None:
    values = tuple(Decimal(100 + index) for index in range(48))
    for model in ("naive", "drift", "seasonal-7", "linear", "lightgbm"):
        predictions = _predict_prices(model, values)
        assert len(predictions) == 12
        assert all(value.is_finite() and value > 0 for value in predictions)
