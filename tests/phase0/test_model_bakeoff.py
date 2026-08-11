from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from advisorai.phase0 import (
    AssetClass,
    ForecastBenchmarkSnapshot,
    ForecastSeriesSnapshot,
    LocalModelRosterEntry,
    MarketBar,
    RosterState,
    StabilityState,
    build_walk_forward_cases,
    forecast_metrics,
    load_local_model_roster,
    mandatory_baseline_metrics,
    parse_binance_klines,
    parse_financial_phrasebank_pages,
    parse_financial_phrasebank_text,
    parse_nasdaq_history,
    sentiment_metrics,
    snapshot_content_hash,
)


def _bars(offset: float = 0) -> tuple[MarketBar, ...]:
    start = datetime(2022, 1, 1, tzinfo=UTC)
    return tuple(
        MarketBar(
            at=start + timedelta(days=index),
            open=100 + offset + index * 0.1,
            high=101 + offset + index * 0.1,
            low=99 + offset + index * 0.1,
            close=100.5 + offset + index * 0.1,
            volume=1000 + index,
        )
        for index in range(620)
    )


def _snapshot() -> ForecastBenchmarkSnapshot:
    series = tuple(
        ForecastSeriesSnapshot(
            instrument=f"T{index}",
            asset_class=AssetClass.EQUITY if index < 2 else AssetClass.CRYPTO,
            source=f"https://example.com/{index}",
            raw_sha256=f"{index + 1:064x}",
            bars=_bars(float(index)),
        )
        for index in range(4)
    )
    return ForecastBenchmarkSnapshot(
        acquired_at=datetime(2026, 8, 7, tzinfo=UTC),
        start_date="2022-01-01",
        end_date="2025-12-31",
        series=series,
        content_hash=snapshot_content_hash(series, "2022-01-01", "2025-12-31"),
    )


def test_nasdaq_parser_reorders_rows_and_hashes_raw_payload():
    body = json.dumps(
        {
            "data": {
                "tradesTable": {
                    "rows": [
                        {
                            "date": "01/02/2024",
                            "open": "$11.00",
                            "high": "$12.00",
                            "low": "$10.00",
                            "close": "$11.50",
                            "volume": "1,234",
                        },
                        {
                            "date": "01/01/2024",
                            "open": "$10.00",
                            "high": "$11.00",
                            "low": "$9.00",
                            "close": "$10.50",
                            "volume": "1,000",
                        },
                    ]
                    * 300
                }
            }
        }
    ).encode()
    with pytest.raises(ValidationError, match="strictly time ordered"):
        parse_nasdaq_history(body, symbol="AAPL", source="https://api.nasdaq.com/test")


def test_binance_parser_deduplicates_paginated_boundary():
    rows = [
        [1_640_995_200_000 + index * 86_400_000, "10", "12", "9", "11", "5"] for index in range(600)
    ]
    body = json.dumps(rows).encode()
    parsed = parse_binance_klines(
        (body, json.dumps(rows[-1:]).encode()),
        symbol="BTCUSDT",
        source="https://api.binance.com/api/v3/klines",
    )
    assert len(parsed.bars) == 600
    assert parsed.asset_class == AssetClass.CRYPTO


def test_snapshot_hash_and_walk_forward_are_fail_closed_and_past_only():
    snapshot = _snapshot()
    cases = build_walk_forward_cases(snapshot, cases_per_series=3)
    assert len(cases) == 12
    assert all(len(case.context) == 512 and len(case.actual) == 30 for case in cases)
    assert cases[0].context[-1] < cases[0].actual[-1]
    with pytest.raises(ValidationError, match="content hash"):
        ForecastBenchmarkSnapshot.model_validate(
            {**snapshot.model_dump(), "content_hash": "0" * 64}
        )


def test_forecast_metrics_and_mandatory_baselines_cover_multiple_series():
    cases = build_walk_forward_cases(_snapshot(), cases_per_series=2)
    predictions = tuple(tuple(case.actual) for case in cases)
    lower = tuple(tuple(value - 1 for value in case.actual) for case in cases)
    upper = tuple(tuple(value + 1 for value in case.actual) for case in cases)
    perfect = forecast_metrics(
        "perfect-fixture", cases, predictions, interval_lower=lower, interval_upper=upper
    )
    assert perfect.mae == 0
    assert perfect.rmse == 0
    assert perfect.mase == 0
    assert perfect.interval_coverage == 1
    baselines = mandatory_baseline_metrics(cases)
    assert tuple(item.model_name for item in baselines) == (
        "naive",
        "drift",
        "seasonal-7",
        "linear",
        "lightgbm",
    )
    assert all(item.observations == len(cases) * 30 for item in baselines)


def test_sentiment_metrics_include_classification_and_calibration():
    metrics = sentiment_metrics(
        "fixture",
        ("positive", "negative", "neutral", "positive"),
        (
            ("positive", 0.9),
            ("negative", 0.8),
            ("positive", 0.6),
            ("neutral", 0.55),
        ),
        latency_p50_ms=1,
        latency_p95_ms=2,
        throughput_per_second=100,
        peak_rss_mib=50,
    )
    assert metrics.observations == 4
    assert metrics.accuracy == 0.5
    assert len(metrics.confusion_matrix) == 3
    assert 0 <= metrics.expected_calibration_error <= 1


def test_financial_phrasebank_parser_rejects_malformed_and_small_snapshots():
    with pytest.raises(ValueError, match="malformed"):
        parse_financial_phrasebank_text(b"missing delimiter")
    body = "\n".join(f"sentence {index}@neutral" for index in range(101)).encode()
    assert len(parse_financial_phrasebank_text(body)) == 101


def test_financial_phrasebank_pages_freeze_label_mapping_and_revision():
    rows = [
        {
            "row_idx": index,
            "row": {
                "sentence": f"public phrase {index}",
                "label": index % 3,
                "__index_level_0__": index,
            },
        }
        for index in range(120)
    ]
    snapshot = parse_financial_phrasebank_pages(
        (json.dumps({"rows": rows}).encode(),),
        repository_id="publisher/dataset",
        revision="a" * 40,
        config="fixed",
        split="test",
        source="https://datasets-server.huggingface.co/rows",
        acquired_at=datetime(2026, 8, 7, tzinfo=UTC),
    )
    assert len(snapshot.examples) == 120
    assert tuple(item.label for item in snapshot.examples[:3]) == (
        "negative",
        "neutral",
        "positive",
    )
    with pytest.raises(ValidationError, match="content hash"):
        type(snapshot).model_validate({**snapshot.model_dump(), "content_hash": "0" * 64})


def test_committed_local_model_roster_is_role_oriented_and_keeps_live_closed():
    roster = load_local_model_roster(Path("configs/models/phase0_local_roster.json"))

    assert roster.forecast_primary.candidate == "ttm-r2"
    assert roster.forecast_primary.state == RosterState.QUALIFIED
    assert roster.forecast_primary.stability == StabilityState.PASSED
    assert roster.finance_sentiment_primary.candidate == "finsentiment-deberta-v3"
    assert roster.finance_sentiment_fast.candidate == "finbert-minilm"
    assert roster.feature_regime_model.candidate == "tspulse"
    assert "forecast" not in roster.feature_regime_model.role
    assert roster.live_capital_approved is False
    assert {entry.candidate for entry in roster.mandatory_baselines} == {
        "naive",
        "drift",
        "seasonal-7",
        "linear",
        "lightgbm",
    }


def test_roster_cannot_select_model_without_passed_stability():
    with pytest.raises(ValidationError, match="selected models require passed stability"):
        LocalModelRosterEntry(
            role="forecast_primary",
            candidate="fixture",
            declared_license="unknown",
            runtime_class="fixture",
            device="cpu",
            state=RosterState.SELECTED,
            stability=StabilityState.NOT_STARTED,
            qualification_status="measured",
        )
