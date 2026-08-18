from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from advisorai.phase4 import ForwardPredictionLedger, ForwardPredictionRecord
from scripts.run_phase4_v3core_baseline_predictions import (
    RESUME_IDENTITY_FIELDS,
    _expected_manifest,
    _pending_baselines,
    _predict_prices,
    _prediction_id,
    _validate_resume_manifest,
)


def test_all_mandatory_baselines_produce_one_hour_price_paths() -> None:
    values = tuple(Decimal(100 + index) for index in range(48))
    for model in ("naive", "drift", "seasonal-7", "linear", "lightgbm"):
        predictions = _predict_prices(model, values)
        assert len(predictions) == 12
        assert all(value.is_finite() and value > 0 for value in predictions)


def test_baseline_identity_is_stable_and_existing_models_are_not_recomputed(tmp_path: Path) -> None:
    cutoff = datetime(2026, 8, 18, 0, tzinfo=UTC)
    ledger = ForwardPredictionLedger(tmp_path / "predictions.jsonl")
    prediction = ForwardPredictionRecord(
        prediction_id=_prediction_id(symbol="BTCUSDT", cutoff=cutoff, model="naive"),
        instrument="BTCUSDT",
        model="naive",
        model_identity_hash="a" * 64,
        cutoff=cutoff,
        input_snapshot_hash="b" * 64,
        predicted_return_bps=Decimal("0"),
        generated_at=cutoff - timedelta(seconds=1),
        runtime_latency_ms=Decimal("1"),
    )
    assert prediction.prediction_id == _prediction_id(
        symbol="BTCUSDT", cutoff=cutoff, model="naive"
    )
    assert ledger.append(prediction)
    assert "naive" not in _pending_baselines(ledger, symbol="BTCUSDT", cutoff=cutoff)


def _resume_fixture(tmp_path: Path) -> dict[str, object]:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "manifest.json").write_text(
        '{"source_snapshot_hash":"' + "a" * 64 + '"}\n', encoding="utf-8"
    )
    return _expected_manifest(
        source_root=source_root,
        source_manifest={"source_snapshot_hash": "a" * 64},
        repository_root=Path.cwd(),
        preregistration_sha256="b" * 64,
        phase3_gate_sha256="c" * 64,
    )


def test_baseline_resume_requires_exact_frozen_identity(tmp_path: Path) -> None:
    expected = _resume_fixture(tmp_path)

    _validate_resume_manifest(copy.deepcopy(expected), expected)
    assert set(expected["model_identity_hashes"]) == {
        "naive",
        "drift",
        "seasonal-7",
        "linear",
        "lightgbm",
    }


@pytest.mark.parametrize("field", RESUME_IDENTITY_FIELDS)
def test_baseline_resume_rejects_identity_change(tmp_path: Path, field: str) -> None:
    expected = _resume_fixture(tmp_path)
    changed = copy.deepcopy(expected)
    if isinstance(changed[field], list):
        changed[field] = ["different"]
    elif isinstance(changed[field], int):
        changed[field] += 1
    else:
        changed[field] = "different"

    with pytest.raises(ValueError, match="resume identity mismatch"):
        _validate_resume_manifest(changed, expected)


def test_baseline_resume_rejects_missing_frozen_identity(tmp_path: Path) -> None:
    expected = _resume_fixture(tmp_path)
    missing = copy.deepcopy(expected)
    del missing["context_bars"]

    with pytest.raises(ValueError, match="resume identity mismatch.*context_bars"):
        _validate_resume_manifest(missing, expected)
