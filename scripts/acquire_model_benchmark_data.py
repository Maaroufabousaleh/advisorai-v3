#!/usr/bin/env python3
"""Acquire frozen public Phase-0 market benchmark data without credentials."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

from advisorai.integrations.http import HttpClientConfig, SafeHttpClient
from advisorai.phase0.model_bakeoff import (
    ForecastBenchmarkSnapshot,
    SentimentBenchmarkSnapshot,
    parse_binance_klines,
    parse_financial_phrasebank_pages,
    parse_nasdaq_history,
    snapshot_content_hash,
    write_immutable_json,
)

START_DATE = "2022-01-01"
END_DATE = "2025-12-31"
START_MS = 1_640_995_200_000
END_MS = 1_767_225_599_999
PHRASEBANK_REPOSITORY = "gtfintechlab/financial_phrasebank_sentences_allagree"
PHRASEBANK_REVISION = "e0ecd7f315af02460bbb107d7588c5a6fa5df573"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path("~/.cache/advisorai-v3/benchmark-data").expanduser(),
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=Path("artifacts/phase0/model-runtime-qualification/benchmark-data"),
    )
    args = parser.parse_args()
    nasdaq = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=("api.nasdaq.com",),
            max_retries=2,
            requests_per_second=2,
            user_agent="Mozilla/5.0 AdvisorAI-V3-Phase0-Benchmark/1.0",
        )
    )
    binance = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=("api.binance.com",),
            max_retries=2,
            requests_per_second=2,
            user_agent="AdvisorAI-V3-Phase0-Benchmark/1.0",
        )
    )
    datasets_server = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=("datasets-server.huggingface.co",),
            max_retries=2,
            requests_per_second=2,
            user_agent="AdvisorAI-V3-Phase0-Benchmark/1.0",
        )
    )
    series = []
    raw_payloads: dict[str, bytes] = {}
    for symbol in ("AAPL", "MSFT", "NVDA"):
        query = urlencode(
            {
                "assetclass": "stocks",
                "fromdate": START_DATE,
                "todate": END_DATE,
                "limit": "5000",
            }
        )
        url = f"https://api.nasdaq.com/api/quote/{symbol}/historical?{query}"
        response = nasdaq.get(
            url,
            headers={"Accept": "application/json", "Referer": "https://www.nasdaq.com/"},
        )
        raw_payloads[f"nasdaq-{symbol}.json"] = response.body
        series.append(parse_nasdaq_history(response.body, symbol=symbol, source=url))
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        pages: list[bytes] = []
        cursor = START_MS
        source = "https://api.binance.com/api/v3/klines"
        while cursor <= END_MS:
            query = urlencode(
                {
                    "symbol": symbol,
                    "interval": "1d",
                    "startTime": cursor,
                    "endTime": END_MS,
                    "limit": 1000,
                }
            )
            response = binance.get(f"{source}?{query}")
            decoded = json.loads(response.body)
            pages.append(response.body)
            if not decoded:
                break
            next_cursor = int(decoded[-1][0]) + 86_400_000
            if next_cursor <= cursor or len(decoded) < 1000:
                break
            cursor = next_cursor
        for index, payload in enumerate(pages):
            raw_payloads[f"binance-{symbol}-{index:02d}.json"] = payload
        series.append(parse_binance_klines(pages, symbol=symbol, source=source))
    acquired_at = datetime.now(UTC)
    content_hash = snapshot_content_hash(series, START_DATE, END_DATE)
    snapshot = ForecastBenchmarkSnapshot(
        acquired_at=acquired_at,
        start_date=START_DATE,
        end_date=END_DATE,
        series=tuple(series),
        content_hash=content_hash,
    )
    snapshot_id = f"public-daily-{content_hash[:16]}"
    cache_directory = args.cache_root / snapshot_id
    cache_directory.mkdir(parents=True, exist_ok=True)
    existing_snapshot_path = cache_directory / "forecast-snapshot.json"
    if existing_snapshot_path.exists():
        existing_snapshot = ForecastBenchmarkSnapshot.model_validate_json(
            existing_snapshot_path.read_text(encoding="utf-8")
        )
        if existing_snapshot.content_hash != content_hash:
            raise FileExistsError("existing forecast snapshot identity differs")
        snapshot = existing_snapshot
        acquired_at = snapshot.acquired_at
    for name, payload in raw_payloads.items():
        target = cache_directory / name
        if target.exists() and target.read_bytes() != payload:
            raise FileExistsError(f"immutable raw benchmark evidence differs: {target}")
        target.write_bytes(payload)
    snapshot_path = write_immutable_json(
        existing_snapshot_path,
        snapshot.model_dump(mode="json"),
    )
    evidence_path = write_immutable_json(
        args.evidence_root / snapshot_id / "forecast-snapshot-manifest.json",
        {
            "schema": "advisorai.phase0.forecast-benchmark-evidence.v1",
            "snapshot": snapshot.model_dump(mode="json"),
            "cache_path": str(snapshot_path),
            "raw_files": sorted(raw_payloads),
        },
    )
    sentiment_pages: list[bytes] = []
    sentiment_source = "https://datasets-server.huggingface.co/rows"
    for offset in range(0, 680, 100):
        query = urlencode(
            {
                "dataset": PHRASEBANK_REPOSITORY,
                "config": "5768",
                "split": "test",
                "offset": offset,
                "length": min(100, 680 - offset),
                "revision": PHRASEBANK_REVISION,
            }
        )
        response = datasets_server.get(f"{sentiment_source}?{query}")
        sentiment_pages.append(response.body)
    sentiment = parse_financial_phrasebank_pages(
        sentiment_pages,
        repository_id=PHRASEBANK_REPOSITORY,
        revision=PHRASEBANK_REVISION,
        config="5768",
        split="test",
        source=sentiment_source,
        acquired_at=acquired_at,
    )
    sentiment_id = f"phrasebank-{sentiment.content_hash[:16]}"
    sentiment_directory = args.cache_root / sentiment_id
    sentiment_directory.mkdir(parents=True, exist_ok=True)
    existing_sentiment_path = sentiment_directory / "sentiment-snapshot.json"
    if existing_sentiment_path.exists():
        existing_sentiment = SentimentBenchmarkSnapshot.model_validate_json(
            existing_sentiment_path.read_text(encoding="utf-8")
        )
        if existing_sentiment.content_hash != sentiment.content_hash:
            raise FileExistsError("existing sentiment snapshot identity differs")
        sentiment = existing_sentiment
    for index, payload in enumerate(sentiment_pages):
        target = sentiment_directory / f"rows-{index:02d}.json"
        if target.exists() and target.read_bytes() != payload:
            raise FileExistsError(f"immutable sentiment evidence differs: {target}")
        target.write_bytes(payload)
    sentiment_path = write_immutable_json(
        existing_sentiment_path,
        sentiment.model_dump(mode="json"),
    )
    sentiment_evidence = write_immutable_json(
        args.evidence_root / sentiment_id / "sentiment-snapshot-manifest.json",
        {
            "schema": "advisorai.phase0.sentiment-benchmark-evidence.v1",
            "snapshot": sentiment.model_dump(mode="json"),
            "cache_path": str(sentiment_path),
            "raw_files": [f"rows-{index:02d}.json" for index in range(len(sentiment_pages))],
        },
    )
    print(
        json.dumps(
            {
                "snapshot_id": snapshot_id,
                "content_hash": content_hash,
                "series": [item.instrument for item in snapshot.series],
                "bars": {item.instrument: len(item.bars) for item in snapshot.series},
                "cache_path": str(snapshot_path),
                "evidence_path": str(evidence_path),
                "sentiment_snapshot_id": sentiment_id,
                "sentiment_examples": len(sentiment.examples),
                "sentiment_cache_path": str(sentiment_path),
                "sentiment_evidence_path": str(sentiment_evidence),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
