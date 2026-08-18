from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from advisorai.phase4 import (
    ForwardPredictionLedger,
    ForwardPredictionRecord,
    V3CoreBar,
    V3CoreBarProvenance,
)
from scripts.run_phase4_v3core_baseline_predictions import (
    RESUME_IDENTITY_FIELDS,
    _context_for_cutoff,
    _expected_manifest,
    _missed_cutoff_reason,
    _pending_baselines,
    _predict_prices,
    _prediction_id,
    _validate_resume_manifest,
)

HASH = "a" * 64
CUTOFF = datetime(2026, 8, 18, 4, tzinfo=UTC)


def _bar(
    interval_end: datetime,
    *,
    source_health_state: str = "HEALTHY",
    collected_at: datetime | None = None,
) -> V3CoreBar:
    collected = collected_at or interval_end + timedelta(seconds=1)
    provenance = V3CoreBarProvenance(
        interval_end=interval_end,
        provider_available_at=interval_end,
        collected_at=collected,
        availability_basis="forward_observed",
        evidence_class="forward_pit_admission",
        source_snapshot_hash=HASH,
        raw_record_hash=HASH,
        normalized_record_hash=HASH,
        source_health_state=source_health_state,
    )
    return V3CoreBar(
        instrument="BTCUSDT",
        provenance=provenance,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1"),
        source_id="binance_spot_public_market_data",
        provider_identity="binance_spot_public_market_data",
        endpoint="https://data-api.binance.vision/api/v3/klines",
        source_snapshot_hash=HASH,
    )


def _context_bars(*, missing_index: int | None = None, unhealthy_index: int | None = None):
    bars = []
    for index in range(48):
        if index == missing_index:
            continue
        bars.append(
            _bar(
                CUTOFF - timedelta(minutes=5 * (48 - index)),
                source_health_state="DEGRADED" if index == unhealthy_index else "HEALTHY",
            )
        )
    return tuple(bars)


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


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"bars": _context_bars()[:47]}, "INSUFFICIENT_CONTEXT"),
        (
            {"bars": _context_bars(missing_index=10) + (_bar(CUTOFF - timedelta(minutes=245)),)},
            "MISSING_BAR",
        ),
        ({"bars": _context_bars(unhealthy_index=10)}, "SOURCE_HEALTH_FAILURE"),
        (
            {"bars": _context_bars(), "worker_started_at": CUTOFF + timedelta(seconds=1)},
            "WORKER_STARTED_TOO_LATE",
        ),
        (
            {"bars": _context_bars(), "worker_started_at": CUTOFF - timedelta(minutes=1)},
            "SCHEDULER_DELAY",
        ),
        ({"bars": _context_bars(), "inference_failed": True}, "INFERENCE_RUNTIME_FAILURE"),
    ],
)
def test_missed_cutoff_reason_is_deterministic(kwargs: dict[str, object], expected: str) -> None:
    assert (
        _missed_cutoff_reason(
            kwargs.pop("bars"),
            symbol="BTCUSDT",
            cutoff=CUTOFF,
            now=CUTOFF + timedelta(seconds=2),
            worker_started_at=kwargs.pop("worker_started_at", CUTOFF - timedelta(minutes=1)),
            **kwargs,
        )
        == expected
    )


def test_context_requires_healthy_forward_observed_bars() -> None:
    assert (
        _context_for_cutoff(
            _context_bars(unhealthy_index=10),
            symbol="BTCUSDT",
            cutoff=CUTOFF,
            now=CUTOFF - timedelta(seconds=1),
        )
        is None
    )
