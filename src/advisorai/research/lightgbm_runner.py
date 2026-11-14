"""Trained LightGBM shadow runner: real inference behind the existing adapter.

Trains one tiny gradient-boosted tree model per forecast cutoff on past-only
features (lagged returns + formation momentum, all strictly before the
cutoff), then predicts the next-holding return through the existing
LightGBMForecastAdapter. Training provenance (dependency version, feature
spec, seed, per-cutoff sample counts) and the checkpoint SHA-256 are
recorded on the shadow experiment; a missing dependency or invalid
checkpoint quarantines instead of falling back.

Model output is a forecast consumed by the same typed Forecast path,
windows, and costs as the deterministic comparison. No order authority.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from advisorai.models.forecasting import LightGBMForecastAdapter, ModelUnavailable

LIGHTGBM_RUNNER_VERSION = "advisorai.research.lightgbm-runner.v1"
LIGHTGBM_SEED = 20261114
LIGHTGBM_PARAMS = {
    "objective": "regression",
    "num_leaves": 7,
    "min_data_in_leaf": 5,
    "learning_rate": 0.1,
    "num_iterations": 50,
    "seed": LIGHTGBM_SEED,
    "deterministic": True,
    "num_threads": 1,
    "verbose": -1,
}
# Past-only features per training row: 5/21/63-day returns + 252-bar momentum.
FEATURE_NAMES = ("ret_5", "ret_21", "ret_63", "mom_252")


def lightgbm_dependency_version() -> str:
    """Recorded dependency identity; raises ModelUnavailable when missing."""
    try:
        import lightgbm
    except ImportError as exc:
        raise ModelUnavailable("lightgbm dependency 'lightgbm' is unavailable") from exc
    return str(lightgbm.__version__)


def past_only_features(closes: Sequence[float], end: int) -> tuple[float, ...]:
    """Features for the bar at `end`, reading closes at or before `end` only."""
    if end < 252:
        raise ValueError("features require 252 bars of history")
    price = closes[end]
    if price <= 0:
        raise ValueError("features require positive prices")
    return (
        price / closes[end - 5] - 1.0,
        price / closes[end - 21] - 1.0,
        price / closes[end - 63] - 1.0,
        price / closes[end - 251] - 1.0,
    )


def train_cutoff_model(
    closes: Sequence[float], cutoff: int, *, holding_bars: int = 21
) -> tuple[bytes, dict[str, Any]]:
    """Train on month-end rows strictly before `cutoff`; return model bytes + provenance.

    Each training row uses features at a past month-end and the realized
    next-holding return as its label — the same target the forecast predicts.
    """
    import lightgbm

    month_ends: list[int] = []
    for index in range(252, cutoff):
        month_ends.append(index)
    # Every past bar with a completed label window is a training row; tiny
    # trees train in milliseconds even at full density.
    rows = month_ends
    labels: list[float] = []
    features: list[tuple[float, ...]] = []
    for row in rows:
        if row + holding_bars > cutoff:
            continue  # label window must complete before the cutoff
        entry = closes[row + 1]
        exit_ = closes[row + holding_bars]
        labels.append(exit_ / entry - 1.0 if entry > 0 else 0.0)
        features.append(past_only_features(closes, row))
    if len(features) < 10:
        raise ModelUnavailable(
            f"lightgbm has {len(features)} past-only training rows at cutoff {cutoff}"
        )
    import numpy

    dataset = lightgbm.Dataset(
        numpy.array(features, dtype=float),
        label=numpy.array(labels, dtype=float),
        feature_name=list(FEATURE_NAMES),
    )
    booster = lightgbm.train(dict(LIGHTGBM_PARAMS), dataset)
    model_bytes = booster.model_to_string().encode()
    provenance = {
        "runner_version": LIGHTGBM_RUNNER_VERSION,
        "dependency": f"lightgbm=={lightgbm.__version__}",
        "seed": LIGHTGBM_SEED,
        "training_rows": len(features),
        "cutoff_index": cutoff,
        "params": dict(LIGHTGBM_PARAMS),
    }
    return model_bytes, provenance


@dataclass(frozen=True, slots=True)
class LightGBMShadowRunner:
    """Callable runner closing over one exposure's closes; trains per cutoff.

    The adapter passes the warmup history ending at the cutoff bar. The
    runner resolves the absolute cutoff by matching the trailing history,
    then trains strictly before it. Ambiguous matches fail closed.
    """

    closes: tuple[float, ...]
    holding_bars: int = 21

    def resolve_cutoff(self, values: Sequence[Decimal]) -> int:
        tail = tuple(float(value) for value in values[-5:])
        candidates = [
            index
            for index in range(4, len(self.closes))
            if tuple(self.closes[index - 4 : index + 1]) == tail
        ]
        if len(candidates) != 1:
            raise ModelUnavailable(
                f"lightgbm shadow history matches {len(candidates)} cutoffs, need exactly 1"
            )
        return candidates[0]

    def __call__(self, values: Sequence[Decimal], horizon: int) -> Sequence[Decimal]:
        import lightgbm

        if horizon != 1:
            raise ValueError("lightgbm shadow runner forecasts one holding window")
        if len(values) < 5:
            raise ModelUnavailable("lightgbm shadow history is too short to resolve")
        cutoff = self.resolve_cutoff(values)
        if cutoff < 252 or cutoff >= len(self.closes):
            raise ModelUnavailable("lightgbm shadow history does not cover the cutoff")
        model_bytes, _ = train_cutoff_model(self.closes, cutoff, holding_bars=self.holding_bars)
        booster = lightgbm.Booster(model_str=model_bytes.decode())
        predicted = float(booster.predict([list(past_only_features(self.closes, cutoff))])[0])
        last_close = float(values[-1])
        if last_close <= 0 or predicted != predicted:  # NaN guard
            raise ModelUnavailable("lightgbm runner produced a non-finite forecast")
        return (Decimal(str(last_close * (1.0 + predicted))),)


def build_lightgbm_adapter(
    closes: Sequence[Decimal], *, holding_bars: int = 21
) -> tuple[LightGBMForecastAdapter, dict[str, Any]]:
    """Construct the adapter with a pinned checkpoint hash and real runner.

    The checkpoint is the model trained at the last in-sample cutoff; its
    SHA-256 pins the training code path, dependency, and seed. Per-cutoff
    retraining inside the runner keeps every forecast past-only.
    """
    version = lightgbm_dependency_version()
    floats = tuple(float(value) for value in closes)
    model_bytes, provenance = train_cutoff_model(floats, len(floats) - 1, holding_bars=holding_bars)
    checkpoint_hash = hashlib.sha256(model_bytes).hexdigest()
    runner = LightGBMShadowRunner(closes=floats, holding_bars=holding_bars)
    adapter = LightGBMForecastAdapter(runner=runner, checkpoint_hash=checkpoint_hash)
    identity = {
        "candidate": "lightgbm",
        "role": "mandatory_forecast_baseline",
        "runtime_class": "advisorai-local-model",
        "repository_id": "lightgbm-pypi",
        "revision": version,
        "device": "cpu",
        "checkpoint_hash": checkpoint_hash,
        "provenance": provenance,
    }
    return adapter, identity


def identity_json(identity: dict[str, Any]) -> str:
    return json.dumps(identity, sort_keys=True)


__all__ = [
    "FEATURE_NAMES",
    "LIGHTGBM_PARAMS",
    "LIGHTGBM_RUNNER_VERSION",
    "LIGHTGBM_SEED",
    "LightGBMShadowRunner",
    "build_lightgbm_adapter",
    "identity_json",
    "lightgbm_dependency_version",
    "past_only_features",
    "train_cutoff_model",
]
