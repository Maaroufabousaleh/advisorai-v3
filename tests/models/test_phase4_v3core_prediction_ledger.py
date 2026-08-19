from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from advisorai.phase4 import (
    ForwardPredictionLedger,
    ForwardPredictionOutcomeLinkLedger,
    ForwardPredictionRecord,
)

HASH = "a" * 64
CUTOFF = datetime(2026, 8, 13, 1, 0, tzinfo=UTC)


def _prediction(model: str = "naive") -> ForwardPredictionRecord:
    return ForwardPredictionRecord(
        prediction_id=f"BTCUSDT:{CUTOFF.isoformat()}:{model}",
        instrument="BTCUSDT",
        model=model,
        model_identity_hash=HASH,
        cutoff=CUTOFF,
        input_snapshot_hash=HASH,
        predicted_return_bps=Decimal("12.5"),
        generated_at=CUTOFF - timedelta(seconds=1),
        runtime_latency_ms=Decimal("1.25"),
    )


def test_prediction_ledger_is_append_only_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "predictions.jsonl"
    ledger = ForwardPredictionLedger(path)
    prediction = _prediction()
    assert ledger.append(prediction)
    assert not ledger.append(prediction)
    reopened = ForwardPredictionLedger(path)
    assert len(reopened.records) == 1
    assert reopened.records[0].prediction.prediction_id == prediction.prediction_id

    line = json.loads(path.read_text(encoding="utf-8"))
    line["prediction"]["predicted_return_bps"] = "99"
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="corrupt"):
        ForwardPredictionLedger(path)


def test_prediction_must_be_generated_before_cutoff() -> None:
    with pytest.raises(ValueError, match="after its cutoff"):
        ForwardPredictionRecord(
            **{
                **_prediction().model_dump(),
                "generated_at": CUTOFF + timedelta(seconds=1),
            }
        )


def test_candidate_runtime_metadata_round_trips_through_ledger(tmp_path: Path) -> None:
    prediction = ForwardPredictionRecord(
        **{
            **_prediction("chronos-2-small").model_dump(),
            "source_snapshot_hash": HASH,
            "checkpoint_hash": HASH,
            "runner_hash": HASH,
            "preprocessing_identity": "v3core-chronos",
            "preprocessing_hash": HASH,
            "dependency_lock_hash": HASH,
            "runtime_environment_hash": HASH,
            "device": "cuda",
            "native_interval_lower_bps": Decimal("-10"),
            "native_interval_upper_bps": Decimal("20"),
            "native_confidence": Decimal("0.8"),
            "resource_peak_rss_mib": Decimal("100"),
            "resource_peak_cpu_percent": Decimal("20"),
            "resource_sample_count": 3,
            "inference_started_at": CUTOFF - timedelta(seconds=3),
            "inference_finished_at": CUTOFF - timedelta(seconds=1),
            "ledger_persisted_at": CUTOFF,
            "provenance": (("source", "binance_public"),),
        }
    )
    path = tmp_path / "candidate-predictions.jsonl"
    ledger = ForwardPredictionLedger(path)
    assert ledger.append(prediction)
    reopened = ForwardPredictionLedger(path)
    restored = reopened.records[0].prediction
    assert restored.model_dump(mode="json") == prediction.model_dump(mode="json")
    assert restored.checkpoint_hash == HASH
    assert restored.native_interval_lower_bps == Decimal("-10")
    assert restored.native_confidence == Decimal("0.8")
    assert restored.inference_started_at == CUTOFF - timedelta(seconds=3)
    assert restored.inference_finished_at == CUTOFF - timedelta(seconds=1)
    assert restored.ledger_persisted_at == CUTOFF
    assert restored.provenance == (("source", "binance_public"),)


def test_candidate_runtime_metadata_must_be_complete() -> None:
    with pytest.raises(ValueError, match="metadata must be complete"):
        ForwardPredictionRecord(
            **{
                **_prediction("chronos-2-small").model_dump(),
                "checkpoint_hash": HASH,
            }
        )


def test_native_prediction_interval_requires_ordered_pair() -> None:
    with pytest.raises(ValueError, match="bounds are inconsistent"):
        ForwardPredictionRecord(
            **{
                **_prediction("chronos-2-small").model_dump(),
                "native_interval_lower_bps": Decimal("20"),
                "native_interval_upper_bps": Decimal("10"),
            }
        )


def test_candidate_timing_requires_completion_order_and_generated_at_match() -> None:
    with pytest.raises(ValueError, match="finished before it started"):
        ForwardPredictionRecord(
            **{
                **_prediction("chronos-2-small").model_dump(),
                "inference_started_at": CUTOFF - timedelta(seconds=1),
                "inference_finished_at": CUTOFF - timedelta(seconds=2),
                "ledger_persisted_at": CUTOFF,
            }
        )

    with pytest.raises(ValueError, match="generated_at must equal inference completion"):
        ForwardPredictionRecord(
            **{
                **_prediction("chronos-2-small").model_dump(),
                "inference_started_at": CUTOFF - timedelta(seconds=3),
                "inference_finished_at": CUTOFF - timedelta(seconds=2),
                "ledger_persisted_at": CUTOFF,
            }
        )


def test_outcome_link_is_separate_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "outcome-links.jsonl"
    ledger = ForwardPredictionOutcomeLinkLedger(path)
    assert ledger.append(
        prediction_id=_prediction().prediction_id,
        outcome_case_id="BTCUSDT:outcome",
        linked_at=CUTOFF + timedelta(hours=1),
    )
    assert not ledger.append(
        prediction_id=_prediction().prediction_id,
        outcome_case_id="BTCUSDT:outcome",
        linked_at=CUTOFF + timedelta(hours=1),
    )
    assert len(ForwardPredictionOutcomeLinkLedger(path).records) == 1
