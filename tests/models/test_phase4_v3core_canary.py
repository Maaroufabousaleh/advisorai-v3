from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from advisorai.collectors.sources import HttpResponse
from advisorai.phase4 import (
    CANARY_EVIDENCE_CLASS,
    CANARY_SOURCE_CONTEXT_UNAVAILABLE,
    CANARY_WAITING_FOR_CONTEXT,
    CANARY_WARMUP_NOT_ELIGIBLE,
    CanaryFinalityTracker,
    CanaryFinalityViolation,
    CanaryPredictionLedger,
    CanaryPredictionRecord,
    CanaryPreflightReport,
    ForwardNormalizedBarSpool,
    ForwardPredictionRecord,
    ForwardRawSpool,
    bar_content_hash,
    derive_first_mandatory_cutoff,
    derive_mandatory_cutoffs,
    require_canary_artifact,
)
from advisorai.phase4.v3core_cadence import sha256_json
from advisorai.phase4.v3core_forward import parse_binance_klines
from scripts.run_phase4_v3core_canary_chronos import cutoff_eligibility_status

HASH = "a" * 64
START = datetime(2026, 8, 22, 0, 0, tzinfo=UTC)


def _row(index: int, *, close: str = "100") -> list[object]:
    interval_start = START + timedelta(minutes=5 * index)
    interval_end = interval_start + timedelta(minutes=5)
    return [
        int(interval_start.timestamp() * 1000),
        "99",
        "101",
        "98",
        close,
        "2",
        int(interval_end.timestamp() * 1000) - 1,
        "200",
        4,
        "1",
        "100",
    ]


def _response(body: bytes, fetched_at: datetime) -> HttpResponse:
    return HttpResponse(
        status_code=200,
        body=body,
        fetched_at=fetched_at,
        url="https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT",
    )


def test_canary_finality_waits_for_guard_and_two_distinct_receipts(tmp_path: Path) -> None:
    normalized = ForwardNormalizedBarSpool(tmp_path / "normalized.jsonl")
    tracker = CanaryFinalityTracker(normalized, tmp_path / "revisions.jsonl")
    body = json.dumps([_row(0)]).encode()
    raw = ForwardRawSpool(tmp_path / "raw.jsonl")

    first = raw.append(
        _response(body, START + timedelta(minutes=6, seconds=1)),
        symbol="BTCUSDT",
        request_url="https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT",
    )
    first_bar = parse_binance_klines(
        body,
        symbol="BTCUSDT",
        collected_at=first.collected_at,
        source_snapshot_hash=HASH,
    )
    assert tracker.observe(first, first_bar) == ()
    assert not normalized.bars

    second = raw.append(
        _response(body, START + timedelta(minutes=6, seconds=2)),
        symbol="BTCUSDT",
        request_url="https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT",
    )
    second_bar = parse_binance_klines(
        body,
        symbol="BTCUSDT",
        collected_at=second.collected_at,
        source_snapshot_hash=HASH,
    )
    admitted = tracker.observe(second, second_bar)
    assert len(admitted) == 1
    assert (
        normalized.bars["BTCUSDT", START + timedelta(minutes=5)].collected_at == second.collected_at
    )
    assert tracker.metrics()["post_admission_revision_count"] == 0


def test_canary_finality_detects_post_admission_revision_without_rewrite(tmp_path: Path) -> None:
    normalized = ForwardNormalizedBarSpool(tmp_path / "normalized.jsonl")
    tracker = CanaryFinalityTracker(normalized, tmp_path / "revisions.jsonl")
    raw = ForwardRawSpool(tmp_path / "raw.jsonl")
    original = json.dumps([_row(0)]).encode()
    changed_row = _row(0, close="102")
    changed_row[2] = "103"
    changed = json.dumps([changed_row]).encode()
    for offset in (61, 62):
        record = raw.append(
            _response(original, START + timedelta(minutes=5, seconds=offset)),
            symbol="BTCUSDT",
            request_url="https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT",
        )
        tracker.observe(
            record,
            parse_binance_klines(
                original,
                symbol="BTCUSDT",
                collected_at=record.collected_at,
                source_snapshot_hash=HASH,
            ),
        )
    admitted_before = normalized.read()[0]
    revised_record = raw.append(
        _response(changed, START + timedelta(minutes=7)),
        symbol="BTCUSDT",
        request_url="https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT",
    )
    with pytest.raises(CanaryFinalityViolation, match="POST_ADMISSION_REVISION"):
        tracker.observe(
            revised_record,
            parse_binance_klines(
                changed,
                symbol="BTCUSDT",
                collected_at=revised_record.collected_at,
                source_snapshot_hash=HASH,
            ),
        )
    assert normalized.read()[0] == admitted_before
    assert len(tracker.revisions) == 1
    assert set(tracker.revisions[0].changed_fields) == {"high", "close"}


def test_pre_admission_variation_is_not_post_admission_revision(tmp_path: Path) -> None:
    """A changed receipt before admission is allowed and recorded separately."""

    normalized = ForwardNormalizedBarSpool(tmp_path / "normalized.jsonl")
    tracker = CanaryFinalityTracker(normalized, tmp_path / "revisions.jsonl")
    raw = ForwardRawSpool(tmp_path / "raw.jsonl")
    original = json.dumps([_row(0)]).encode()
    changed_row = _row(0)
    changed_row[5] = "3"
    changed = json.dumps([changed_row]).encode()
    records = (
        raw.append(
            _response(original, START + timedelta(minutes=5, seconds=1)),
            symbol="ETHUSDT",
            request_url="https://data-api.binance.vision/api/v3/klines?symbol=ETHUSDT",
        ),
        raw.append(
            _response(changed, START + timedelta(minutes=5, seconds=32)),
            symbol="ETHUSDT",
            request_url="https://data-api.binance.vision/api/v3/klines?symbol=ETHUSDT",
        ),
        raw.append(
            _response(changed, START + timedelta(minutes=6, seconds=4)),
            symbol="ETHUSDT",
            request_url="https://data-api.binance.vision/api/v3/klines?symbol=ETHUSDT",
        ),
    )
    for record, body in zip(records, (original, changed, changed), strict=True):
        tracker.observe(
            record,
            parse_binance_klines(
                body,
                symbol="ETHUSDT",
                collected_at=record.collected_at,
                source_snapshot_hash=HASH,
            ),
        )

    assert not tracker.revisions
    metrics = tracker.metrics()
    assert metrics["post_admission_revision_count"] == 0
    assert metrics["pre_admission_variation_intervals"] == 1
    admission = metrics["admissions"][0]
    assert admission["instrument"] == "ETHUSDT"
    assert admission["admission_raw_sequence"] == 3
    assert admission["supporting_receipt_sequences"] == [2, 3]
    assert admission["pre_admission_variation_count"] == 1


def test_receipt_after_admission_at_equal_timestamp_is_a_revision(tmp_path: Path) -> None:
    normalized = ForwardNormalizedBarSpool(tmp_path / "normalized.jsonl")
    tracker = CanaryFinalityTracker(normalized, tmp_path / "revisions.jsonl")
    raw = ForwardRawSpool(tmp_path / "raw.jsonl")
    original = json.dumps([_row(0)]).encode()
    changed_row = _row(0)
    changed_row[4] = "102"
    changed_row[2] = "103"
    changed = json.dumps([changed_row]).encode()
    admission_time = START + timedelta(minutes=6)
    for body in (original, original):
        record = raw.append(
            _response(body, admission_time),
            symbol="ETHUSDT",
            request_url="https://data-api.binance.vision/api/v3/klines?symbol=ETHUSDT",
        )
        tracker.observe(
            record,
            parse_binance_klines(
                body,
                symbol="ETHUSDT",
                collected_at=record.collected_at,
                source_snapshot_hash=HASH,
            ),
        )
    revised = raw.append(
        _response(changed, admission_time),
        symbol="ETHUSDT",
        request_url="https://data-api.binance.vision/api/v3/klines?symbol=ETHUSDT",
    )
    with pytest.raises(CanaryFinalityViolation, match="POST_ADMISSION_REVISION"):
        tracker.observe(
            revised,
            parse_binance_klines(
                changed,
                symbol="ETHUSDT",
                collected_at=revised.collected_at,
                source_snapshot_hash=HASH,
            ),
        )


def test_v2_eth_receipt_chronology_is_pre_admission_variation(tmp_path: Path) -> None:
    """Regression fixture for ETH 2026-08-23 15:45Z from the immutable canary."""

    interval_start = datetime(2026, 8, 23, 15, 40, tzinfo=UTC)

    def row(volume: str) -> list[object]:
        interval_end = interval_start + timedelta(minutes=5)
        return [
            int(interval_start.timestamp() * 1000),
            "2444.28000000",
            "2452.86000000",
            "2443.37000000",
            "2446.68000000",
            volume,
            int(interval_end.timestamp() * 1000) - 1,
            "200",
            4,
            "1",
            "100",
        ]

    normalized = ForwardNormalizedBarSpool(tmp_path / "normalized.jsonl")
    tracker = CanaryFinalityTracker(normalized, tmp_path / "revisions.jsonl")
    raw = ForwardRawSpool(tmp_path / "raw.jsonl")
    original = json.dumps([row("1891.64530000")]).encode()
    final = json.dumps([row("1892.05400000")]).encode()
    chronology = (
        (original, datetime(2026, 8, 23, 15, 45, 0, 970963, tzinfo=UTC)),
        (final, datetime(2026, 8, 23, 15, 45, 32, 668159, tzinfo=UTC)),
        (final, datetime(2026, 8, 23, 15, 46, 4, 696743, tzinfo=UTC)),
    )
    for body, collected_at in chronology:
        record = raw.append(
            _response(body, collected_at),
            symbol="ETHUSDT",
            request_url="https://data-api.binance.vision/api/v3/klines?symbol=ETHUSDT",
        )
        tracker.observe(
            record,
            parse_binance_klines(
                body,
                symbol="ETHUSDT",
                collected_at=collected_at,
                source_snapshot_hash=HASH,
            ),
        )

    assert not tracker.revisions
    admission = tracker.metrics()["admissions"][0]
    assert admission["interval_end"] == "2026-08-23T15:45:00+00:00"
    assert admission["admission_observed_at"] == "2026-08-23T15:46:04.696743+00:00"
    assert admission["supporting_receipt_sequences"] == [2, 3]
    assert admission["pre_admission_variation_count"] == 1


def test_canary_prediction_envelope_and_resume_round_trip(tmp_path: Path) -> None:
    cutoff = START + timedelta(hours=1)
    prediction = ForwardPredictionRecord(
        prediction_id="BTCUSDT:canary:1",
        instrument="BTCUSDT",
        model="chronos-2-small",
        model_identity_hash=HASH,
        cutoff=cutoff,
        input_snapshot_hash=HASH,
        predicted_return_bps="1",
        generated_at=cutoff - timedelta(seconds=1),
        runtime_latency_ms="1",
        provenance=(("experiment_evidence_class", CANARY_EVIDENCE_CLASS),),
    )
    ledger = CanaryPredictionLedger(tmp_path / "predictions.jsonl")
    record = ledger.append(prediction)
    assert record.admission_eligible is False
    assert ledger.append(prediction) == record
    reopened = CanaryPredictionLedger(tmp_path / "predictions.jsonl")
    assert reopened.records == [record]
    assert reopened.for_cutoff("BTCUSDT", cutoff) == record
    bad_prediction = prediction.model_copy(
        update={"provenance": (("experiment_evidence_class", "forward_pit_admission"),)}
    )
    bad_unsigned = {
        "schema": "advisorai.phase4.v3-core.prospective-canary.prediction.v1",
        "sequence": 1,
        "evidence_class": CANARY_EVIDENCE_CLASS,
        "admission_eligible": False,
        "prediction": bad_prediction.model_dump(mode="json"),
        "previous_record_hash": None,
    }
    with pytest.raises(ValueError, match="evidence class"):
        CanaryPredictionRecord(**bad_unsigned, record_hash=sha256_json(bad_unsigned))


def test_canary_artifacts_are_explicitly_non_admission() -> None:
    require_canary_artifact(
        {
            "evidence_class": CANARY_EVIDENCE_CLASS,
            "admission_eligible": False,
            "phase4_materialization_eligible": False,
        }
    )
    with pytest.raises(ValueError, match="admission eligible"):
        require_canary_artifact(
            {
                "evidence_class": CANARY_EVIDENCE_CLASS,
                "admission_eligible": True,
            }
        )


def test_canary_preflight_hash_is_typed_and_immutable() -> None:
    unsigned = {
        "schema": "advisorai.phase4.v3-core.prospective-canary.preflight.v1",
        "decision": "REFUSE_CANARY",
        "canary_id": "test",
        "evidence_class": CANARY_EVIDENCE_CLASS,
        "admission_eligible": False,
        "checks": [],
        "refusal_reasons": ["gpu_lease_free:occupied"],
    }
    report = CanaryPreflightReport(**unsigned, report_hash=sha256_json(unsigned))
    assert report.decision == "REFUSE_CANARY"
    with pytest.raises(ValueError, match="hash"):
        CanaryPreflightReport(**unsigned, report_hash=HASH)


def test_bar_content_hash_excludes_receipt_timestamps() -> None:
    body = json.dumps([_row(0)]).encode()
    first = parse_binance_klines(
        body,
        symbol="BTCUSDT",
        collected_at=START + timedelta(minutes=6),
        source_snapshot_hash=HASH,
    )[0]
    second = first.model_copy(
        update={
            "provenance": first.provenance.model_copy(
                update={"collected_at": first.collected_at + timedelta(seconds=30)}
            )
        }
    )
    assert bar_content_hash(first) == bar_content_hash(second)


def test_warmup_schedule_derives_first_legal_hourly_cutoff() -> None:
    start = datetime(2026, 8, 22, 23, 0, tzinfo=UTC)
    first = derive_first_mandatory_cutoff(start)
    assert first == datetime(2026, 8, 23, 4, 0, tzinfo=UTC)
    assert derive_mandatory_cutoffs(start) == tuple(
        datetime(2026, 8, 23, hour, 0, tzinfo=UTC) for hour in range(4, 8)
    )


@pytest.mark.parametrize("hour", (0, 1, 2, 3))
def test_old_canary_early_cutoffs_are_warmup_not_failures(hour: int) -> None:
    start = datetime(2026, 8, 22, 23, 0, tzinfo=UTC)
    first = derive_first_mandatory_cutoff(start)
    cutoff = datetime(2026, 8, 23, hour, 0, tzinfo=UTC)
    assert (
        cutoff_eligibility_status(
            cutoff=cutoff,
            now=cutoff + timedelta(seconds=1),
            first_mandatory_cutoff=first,
            context_available=False,
        )
        == CANARY_WARMUP_NOT_ELIGIBLE
    )


def test_first_mandatory_cutoff_distinguishes_eligibility_from_source_gap() -> None:
    start = datetime(2026, 8, 22, 23, 0, tzinfo=UTC)
    first = derive_first_mandatory_cutoff(start)
    assert (
        cutoff_eligibility_status(
            cutoff=first,
            now=first,
            first_mandatory_cutoff=first,
            context_available=True,
        )
        == "ELIGIBLE"
    )
    assert (
        cutoff_eligibility_status(
            cutoff=first,
            now=first,
            first_mandatory_cutoff=first,
            context_available=False,
        )
        == CANARY_SOURCE_CONTEXT_UNAVAILABLE
    )
    assert (
        cutoff_eligibility_status(
            cutoff=first,
            now=first - timedelta(seconds=1),
            first_mandatory_cutoff=first,
            context_available=False,
        )
        == CANARY_WAITING_FOR_CONTEXT
    )
