from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from advisorai.collectors.sources import HttpResponse
from advisorai.phase4 import (
    FORWARD_INTERVAL_MILLISECONDS,
    ForwardFailureSpool,
    ForwardHealthLedger,
    ForwardNormalizedBarSpool,
    ForwardRawSpool,
    ForwardRejectionSpool,
    build_forward_cases,
    parse_binance_klines,
)

HASH = "a" * 64
START = datetime(2026, 8, 12, 0, 0, tzinfo=UTC)


def _row(index: int, *, close: str | None = None) -> list[object]:
    interval_start = START + timedelta(minutes=5 * index)
    interval_end = interval_start + timedelta(minutes=5)
    close_value = close or str(100 + index)
    close_decimal = float(close_value)
    return [
        int(interval_start.timestamp() * 1000),
        str(close_decimal - 1),
        str(close_decimal + 1),
        str(close_decimal - 2),
        close_value,
        "2",
        int(interval_end.timestamp() * 1000) - 1,
        "200",
        4,
        "1",
        "100",
    ]


def _response(body: bytes, *, received_at: datetime) -> HttpResponse:
    return HttpResponse(
        status_code=200,
        body=body,
        fetched_at=received_at,
        url="https://data-api.binance.vision/api/v3/klines?interval=5m&limit=2&symbol=BTCUSDT",
    )


def test_parser_keeps_raw_open_bar_out_of_forward_normalized_plane() -> None:
    first = _row(0)
    second = _row(2)
    collected_at = START + timedelta(minutes=10, seconds=1)

    bars = parse_binance_klines(
        json.dumps([first, second]).encode(),
        symbol="BTCUSDT",
        collected_at=collected_at,
        source_snapshot_hash=HASH,
    )

    assert [bar.interval_end for bar in bars] == [START + timedelta(minutes=5)]
    assert bars[0].collected_at == collected_at
    assert bars[0].provider_available_at == START + timedelta(minutes=5)
    assert bars[0].evidence_class == "forward_pit_admission"
    assert (
        bars[0].provenance.raw_record_hash
        == sha256(json.dumps(first, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    )


def test_parser_does_not_backdate_a_bar_received_before_interval_end() -> None:
    bars = parse_binance_klines(
        json.dumps([_row(0)]).encode(),
        symbol="BTCUSDT",
        collected_at=START + timedelta(minutes=4, seconds=59),
        source_snapshot_hash=HASH,
    )
    assert not bars


def test_parser_rejects_provider_close_semantics_drift() -> None:
    row = _row(0)
    row[6] = int((START + timedelta(minutes=4, seconds=59)).timestamp() * 1000)
    with pytest.raises(ValueError, match="close semantics"):
        parse_binance_klines(
            json.dumps([row]).encode(),
            symbol="BTCUSDT",
            collected_at=START + timedelta(minutes=6),
            source_snapshot_hash=HASH,
        )


def test_parser_rejects_duplicate_provider_intervals() -> None:
    with pytest.raises(ValueError, match="duplicate interval"):
        parse_binance_klines(
            json.dumps([_row(0), _row(0)]).encode(),
            symbol="BTCUSDT",
            collected_at=START + timedelta(minutes=6),
            source_snapshot_hash=HASH,
        )


def test_raw_spool_preserves_repeated_receipts_and_hash_chain(tmp_path: Path) -> None:
    body = json.dumps([_row(0)]).encode()
    spool = ForwardRawSpool(tmp_path / "raw.jsonl")
    first = spool.append(
        _response(body, received_at=START + timedelta(minutes=6)),
        symbol="BTCUSDT",
        request_url="https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT",
    )
    second = spool.append(
        _response(body, received_at=START + timedelta(minutes=7)),
        symbol="BTCUSDT",
        request_url="https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT",
    )

    assert (first.sequence, second.sequence) == (1, 2)
    assert second.previous_record_hash == first.record_hash
    reopened = ForwardRawSpool(tmp_path / "raw.jsonl")
    assert len(reopened.read()) == 2

    lines = (tmp_path / "raw.jsonl").read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["payload_b64"] = "eA=="
    (tmp_path / "raw.jsonl").write_text(
        "\n".join((lines[0], json.dumps(tampered))) + "\n", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="corrupt"):
        ForwardRawSpool(tmp_path / "raw.jsonl")


def test_completed_case_requires_contiguous_context_and_future_outcome(tmp_path: Path) -> None:
    bars = []
    for index in range(-1, 60):
        end = START + timedelta(minutes=5 * (index + 1))
        bars.extend(
            parse_binance_klines(
                json.dumps([_row(index)]).encode(),
                symbol="BTCUSDT",
                collected_at=end + timedelta(seconds=1),
                source_snapshot_hash=HASH,
            )
        )
    build = build_forward_cases(
        bars,
        source_snapshot_hash=HASH,
        phase3_gate_record_sha256="b" * 64,
    )
    assert len(build.cases) == 1
    assert build.cases[0].realized_at > build.cases[0].cutoff

    missing = [bar for bar in bars if bar.interval_end != START + timedelta(minutes=5 * 55)]
    incomplete = build_forward_cases(
        missing,
        source_snapshot_hash=HASH,
        phase3_gate_record_sha256="b" * 64,
    )
    assert not incomplete.cases
    assert any(
        item.reason == "missing_one_hour_outcome_bars" for item in incomplete.rejected_cutoffs
    )

    rejection_spool = ForwardRejectionSpool(tmp_path / "rejections.jsonl")
    for item in incomplete.rejected_cutoffs:
        rejection_spool.append(instrument=item.instrument, cutoff=item.cutoff, reason=item.reason)
    assert rejection_spool.records
    assert not rejection_spool.append(
        instrument=incomplete.rejected_cutoffs[0].instrument,
        cutoff=incomplete.rejected_cutoffs[0].cutoff,
        reason=incomplete.rejected_cutoffs[0].reason,
    )


def test_normalized_spool_rejects_conflicting_bar_identity(tmp_path: Path) -> None:
    bar = parse_binance_klines(
        json.dumps([_row(0)]).encode(),
        symbol="BTCUSDT",
        collected_at=START + timedelta(minutes=6),
        source_snapshot_hash=HASH,
    )[0]
    spool = ForwardNormalizedBarSpool(tmp_path / "bars.jsonl")
    assert spool.append(bar)
    assert not spool.append(bar)
    later_receipt = bar.model_copy(
        update={
            "provenance": bar.provenance.model_copy(
                update={"collected_at": bar.collected_at + timedelta(seconds=30)}
            )
        }
    )
    assert not spool.append(later_receipt)
    changed = bar.model_copy(update={"close": bar.close + 1})
    with pytest.raises(RuntimeError, match="changed"):
        spool.append(changed)


def test_failure_and_health_ledgers_are_sanitized_and_append_only(tmp_path: Path) -> None:
    failures = ForwardFailureSpool(tmp_path / "failures.jsonl")
    failure = failures.append(
        symbol="ETHUSDT",
        observed_at=START,
        failure_class="http_429",
        status_code=429,
        retriable=True,
    )
    assert failure.status_code == 429
    assert "secret" not in failure.model_dump_json().lower()

    health = ForwardHealthLedger(tmp_path / "health.jsonl")
    assert health.append(symbol="ETHUSDT", observed_at=START, to_state="HEALTHY", reason="ok")
    assert health.append(
        symbol="ETHUSDT",
        observed_at=START + timedelta(minutes=1),
        to_state="DISCONNECTED",
        reason="http_429",
    )
    assert (
        health.append(
            symbol="ETHUSDT",
            observed_at=START + timedelta(minutes=2),
            to_state="DISCONNECTED",
            reason="same_state",
        )
        is None
    )


def test_binance_interval_contract_is_five_minutes() -> None:
    assert FORWARD_INTERVAL_MILLISECONDS == 300_000
