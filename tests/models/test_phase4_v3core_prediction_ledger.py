from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
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


def test_duplicate_prediction_id_with_changed_payload_is_rejected(tmp_path: Path) -> None:
    ledger = ForwardPredictionLedger(tmp_path / "predictions.jsonl")
    original = _prediction()
    assert ledger.append(original)
    changed = original.model_copy(update={"predicted_return_bps": Decimal("2")})

    with pytest.raises(RuntimeError, match="conflicting payload"):
        ledger.append(changed)


def test_ledger_reloads_legacy_prediction_payload_after_schema_extension(tmp_path: Path) -> None:
    path = tmp_path / "predictions.jsonl"
    ledger = ForwardPredictionLedger(path)
    assert ledger.append(_prediction())
    payload = json.loads(path.read_text(encoding="utf-8"))
    for field in (
        "source_snapshot_hash",
        "checkpoint_hash",
        "runner_hash",
        "preprocessing_identity",
        "preprocessing_hash",
        "dependency_lock_hash",
        "runtime_environment_hash",
        "device",
        "native_interval_lower_bps",
        "native_interval_upper_bps",
        "native_confidence",
        "resource_peak_rss_mib",
        "resource_peak_cpu_percent",
        "resource_sample_count",
        "provenance",
    ):
        payload["prediction"].pop(field, None)
    unsigned = {key: value for key, value in payload.items() if key != "record_hash"}
    payload["record_hash"] = sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert len(ForwardPredictionLedger(path).records) == 1


def test_prediction_record_keeps_candidate_identity_and_no_outcome_mutation() -> None:
    record = _prediction().model_copy(
        update={
            "source_snapshot_hash": "a" * 64,
            "checkpoint_hash": "b" * 64,
            "runner_hash": "c" * 64,
            "preprocessing_identity": "v3core-raw-close-48-direct-v1",
            "preprocessing_hash": "d" * 64,
            "dependency_lock_hash": "e" * 64,
            "runtime_environment_hash": "f" * 64,
            "device": "cpu",
            "provenance": (("model", "ttm-r2"), ("source", "forward")),
        }
    )
    assert record.checkpoint_hash == "b" * 64
    assert record.provenance == (("model", "ttm-r2"), ("source", "forward"))

    with pytest.raises(ValueError, match="cannot be mutated"):
        ForwardPredictionRecord(**{**record.model_dump(), "outcome_case_id": "case-1"})


def test_prediction_must_be_generated_before_cutoff() -> None:
    with pytest.raises(ValueError, match="after its cutoff"):
        ForwardPredictionRecord(
            **{
                **_prediction().model_dump(),
                "generated_at": CUTOFF + timedelta(seconds=1),
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
