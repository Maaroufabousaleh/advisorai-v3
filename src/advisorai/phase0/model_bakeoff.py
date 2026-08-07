"""Frozen public datasets and evidence contracts for the Phase-0 model bake-off.

The module deliberately separates acquisition from evaluation.  Network-enabled
collectors create an immutable public snapshot; model runtimes only consume the
validated local snapshot while their own network guards remain active.
"""

from __future__ import annotations

import io
import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.models.forecasting import (
    DriftForecaster,
    LinearForecaster,
    NaiveForecaster,
    SeasonalForecaster,
)
from advisorai.phase0.runtime_qualification import LightGBMBaseline

HEX = frozenset("0123456789abcdef")


def _sha256_bytes(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class AssetClass(StrEnum):
    EQUITY = "equity"
    CRYPTO = "crypto"


class RosterState(StrEnum):
    CANDIDATE = "candidate"
    QUALIFIED = "qualified"
    SELECTED = "selected"
    QUARANTINED = "quarantined"
    PENDING_STABILITY = "pending_stability"
    INACTIVE = "inactive"


class MarketBar(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_bar(self) -> MarketBar:
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("market bar timestamp must include a timezone")
        values = (self.open, self.high, self.low, self.close, self.volume)
        if any(not math.isfinite(value) for value in values):
            raise ValueError("market bar values must be finite")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("market prices must be positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("market bar OHLC bounds are inconsistent")
        return self


class ForecastSeriesSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument: str
    asset_class: AssetClass
    source: str
    raw_sha256: str
    bars: tuple[MarketBar, ...] = Field(min_length=542)

    @field_validator("raw_sha256")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if len(value) != 64 or any(character not in HEX for character in value):
            raise ValueError("raw source hash must be SHA-256")
        return value

    @model_validator(mode="after")
    def validate_series(self) -> ForecastSeriesSnapshot:
        if not self.instrument.strip() or not self.source.startswith("https://"):
            raise ValueError("forecast series requires an instrument and HTTPS source")
        timestamps = tuple(bar.at for bar in self.bars)
        if timestamps != tuple(sorted(timestamps)) or len(timestamps) != len(set(timestamps)):
            raise ValueError("forecast bars must be strictly time ordered")
        return self


class ForecastBenchmarkSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "advisorai.phase0.forecast-benchmark-snapshot.v1"
    dataset_id: str = "advisorai-public-daily-markets"
    version: str = "1.0.0"
    acquired_at: datetime
    start_date: str
    end_date: str
    series: tuple[ForecastSeriesSnapshot, ...] = Field(min_length=4)
    content_hash: str

    @model_validator(mode="after")
    def validate_snapshot(self) -> ForecastBenchmarkSnapshot:
        if self.acquired_at.tzinfo is None or self.acquired_at.utcoffset() is None:
            raise ValueError("snapshot acquisition time must include a timezone")
        if len(self.content_hash) != 64 or any(character not in HEX for character in self.content_hash):
            raise ValueError("snapshot content hash must be SHA-256")
        names = tuple(item.instrument for item in self.series)
        if len(names) != len(set(names)):
            raise ValueError("snapshot instruments must be unique")
        expected = snapshot_content_hash(self.series, self.start_date, self.end_date)
        if expected != self.content_hash:
            raise ValueError("snapshot content hash is inconsistent")
        return self


class ForecastCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    instrument: str
    asset_class: AssetClass
    cutoff: datetime
    context: tuple[float, ...] = Field(min_length=512, max_length=512)
    actual: tuple[float, ...] = Field(min_length=30, max_length=30)
    context_ohlcv: tuple[tuple[float, float, float, float, float], ...] = Field(
        min_length=512, max_length=512
    )
    context_timestamps: tuple[datetime, ...] = Field(min_length=512, max_length=512)
    future_timestamps: tuple[datetime, ...] = Field(min_length=30, max_length=30)

    @model_validator(mode="after")
    def validate_case(self) -> ForecastCase:
        if self.cutoff.tzinfo is None or self.cutoff.utcoffset() is None:
            raise ValueError("walk-forward cutoff must include a timezone")
        if any(not math.isfinite(value) or value <= 0 for value in (*self.context, *self.actual)):
            raise ValueError("walk-forward prices must be finite and positive")
        if any(
            not math.isfinite(value) or value < 0
            for row in self.context_ohlcv
            for value in row
        ):
            raise ValueError("walk-forward OHLCV values must be finite and non-negative")
        if tuple(self.context_timestamps) != tuple(sorted(self.context_timestamps)):
            raise ValueError("walk-forward context timestamps must be ordered")
        if tuple(self.future_timestamps) != tuple(sorted(self.future_timestamps)):
            raise ValueError("walk-forward future timestamps must be ordered")
        if self.context_timestamps[-1] != self.cutoff:
            raise ValueError("walk-forward cutoff must match the final context timestamp")
        if self.future_timestamps[0] <= self.cutoff:
            raise ValueError("walk-forward future timestamps must follow the cutoff")
        return self


class ForecastBenchmarkMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_name: str
    cases: int = Field(ge=1)
    observations: int = Field(ge=1)
    mae: float = Field(ge=0)
    rmse: float = Field(ge=0)
    mase: float = Field(ge=0)
    directional_accuracy: float = Field(ge=0, le=1)
    interval_coverage: float | None = Field(default=None, ge=0, le=1)
    latency_p50_ms: float = Field(default=0, ge=0)
    latency_p95_ms: float = Field(default=0, ge=0)
    cold_load_ms: float = Field(default=0, ge=0)
    peak_rss_mib: float = Field(default=0, ge=0)
    peak_vram_mib: float = Field(default=0, ge=0)
    resource_limit_passed: bool = True
    past_only: bool = True
    failures: tuple[str, ...] = ()


class SentimentBenchmarkMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_name: str
    observations: int = Field(ge=1)
    accuracy: float = Field(ge=0, le=1)
    macro_f1: float = Field(ge=0, le=1)
    per_label_precision: tuple[tuple[str, float], ...]
    per_label_recall: tuple[tuple[str, float], ...]
    per_label_f1: tuple[tuple[str, float], ...]
    confusion_matrix: tuple[tuple[int, ...], ...]
    expected_calibration_error: float = Field(ge=0, le=1)
    brier_score: float = Field(ge=0)
    negative_log_likelihood: float = Field(ge=0)
    latency_p50_ms: float = Field(ge=0)
    latency_p95_ms: float = Field(ge=0)
    throughput_per_second: float = Field(ge=0)
    peak_rss_mib: float = Field(ge=0)


class SentimentExample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    example_id: int = Field(ge=0)
    text: str = Field(min_length=1)
    label: str

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        if value not in {"negative", "neutral", "positive"}:
            raise ValueError("sentiment example label is invalid")
        return value


class SentimentBenchmarkSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "advisorai.phase0.sentiment-benchmark-snapshot.v1"
    dataset_id: str = "financial-phrasebank-all-agree"
    repository_id: str
    revision: str
    config: str
    split: str
    source: str
    acquired_at: datetime
    examples: tuple[SentimentExample, ...] = Field(min_length=100)
    raw_sha256: str
    content_hash: str

    @model_validator(mode="after")
    def validate_snapshot(self) -> SentimentBenchmarkSnapshot:
        if self.acquired_at.tzinfo is None or self.acquired_at.utcoffset() is None:
            raise ValueError("sentiment acquisition timestamp must include a timezone")
        for value in (self.raw_sha256, self.content_hash):
            if len(value) != 64 or any(character not in HEX for character in value):
                raise ValueError("sentiment snapshot hashes must be SHA-256")
        if len(self.revision) != 40 or any(character not in HEX for character in self.revision):
            raise ValueError("sentiment dataset revision must be an immutable commit")
        ids = tuple(item.example_id for item in self.examples)
        if len(ids) != len(set(ids)):
            raise ValueError("sentiment example identities must be unique")
        expected = _sha256_bytes(
            _canonical_bytes([item.model_dump(mode="json") for item in self.examples])
        )
        if expected != self.content_hash:
            raise ValueError("sentiment snapshot content hash is inconsistent")
        return self


def snapshot_content_hash(
    series: Sequence[ForecastSeriesSnapshot], start_date: str, end_date: str
) -> str:
    payload = {
        "start_date": start_date,
        "end_date": end_date,
        "series": [item.model_dump(mode="json") for item in series],
    }
    return _sha256_bytes(_canonical_bytes(payload))


def parse_nasdaq_history(
    body: bytes, *, symbol: str, source: str
) -> ForecastSeriesSnapshot:
    decoded = json.loads(body)
    rows = (((decoded.get("data") or {}).get("tradesTable") or {}).get("rows") or [])
    bars: list[MarketBar] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("Nasdaq history row is malformed")

        def number(name: str, *, _row: Mapping[str, Any] = row) -> float:
            return float(str(_row[name]).replace("$", "").replace(",", ""))

        bars.append(
            MarketBar(
                at=datetime.strptime(str(row["date"]), "%m/%d/%Y").replace(tzinfo=UTC),
                open=number("open"),
                high=number("high"),
                low=number("low"),
                close=number("close"),
                volume=number("volume"),
            )
        )
    bars.sort(key=lambda item: item.at)
    return ForecastSeriesSnapshot(
        instrument=symbol,
        asset_class=AssetClass.EQUITY,
        source=source,
        raw_sha256=_sha256_bytes(body),
        bars=tuple(bars),
    )


def parse_binance_klines(
    bodies: Sequence[bytes], *, symbol: str, source: str
) -> ForecastSeriesSnapshot:
    rows: list[Any] = []
    for body in bodies:
        decoded = json.loads(body)
        if not isinstance(decoded, list):
            raise ValueError("Binance kline response is malformed")
        rows.extend(decoded)
    by_timestamp: dict[int, MarketBar] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            raise ValueError("Binance kline row is malformed")
        timestamp = int(row[0])
        by_timestamp[timestamp] = MarketBar(
            at=datetime.fromtimestamp(timestamp / 1000, tz=UTC),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
    raw_hash = _sha256_bytes(b"".join(bodies))
    return ForecastSeriesSnapshot(
        instrument=symbol,
        asset_class=AssetClass.CRYPTO,
        source=source,
        raw_sha256=raw_hash,
        bars=tuple(by_timestamp[key] for key in sorted(by_timestamp)),
    )


def build_walk_forward_cases(
    snapshot: ForecastBenchmarkSnapshot, *, cases_per_series: int = 4
) -> tuple[ForecastCase, ...]:
    if cases_per_series < 2:
        raise ValueError("walk-forward benchmark requires at least two cases per series")
    cases: list[ForecastCase] = []
    for series in snapshot.series:
        earliest = 512
        latest = len(series.bars) - 30
        if latest <= earliest:
            raise ValueError("series is too short for walk-forward cases")
        positions = tuple(
            round(earliest + index * (latest - earliest) / (cases_per_series - 1))
            for index in range(cases_per_series)
        )
        for position in positions:
            history = series.bars[position - 512 : position]
            future = series.bars[position : position + 30]
            cases.append(
                ForecastCase(
                    case_id=f"{series.instrument}:{future[0].at.date().isoformat()}",
                    instrument=series.instrument,
                    asset_class=series.asset_class,
                    cutoff=history[-1].at,
                    context=tuple(bar.close for bar in history),
                    actual=tuple(bar.close for bar in future),
                    context_ohlcv=tuple(
                        (bar.open, bar.high, bar.low, bar.close, bar.volume) for bar in history
                    ),
                    context_timestamps=tuple(bar.at for bar in history),
                    future_timestamps=tuple(bar.at for bar in future),
                )
            )
    return tuple(cases)


def forecast_metrics(
    model_name: str,
    cases: Sequence[ForecastCase],
    predictions: Sequence[Sequence[float]],
    *,
    latency_p50_ms: float = 0,
    latency_p95_ms: float = 0,
    cold_load_ms: float = 0,
    peak_rss_mib: float = 0,
    peak_vram_mib: float = 0,
    resource_limit_passed: bool = True,
    interval_lower: Sequence[Sequence[float]] = (),
    interval_upper: Sequence[Sequence[float]] = (),
) -> ForecastBenchmarkMetrics:
    if len(cases) != len(predictions) or not cases:
        raise ValueError("forecast cases and predictions must be non-empty and aligned")
    errors: list[float] = []
    squared: list[float] = []
    scaled: list[float] = []
    direction_matches: list[bool] = []
    interval_hits: list[bool] = []
    if bool(interval_lower) != bool(interval_upper):
        raise ValueError("forecast interval bounds must be supplied together")
    if interval_lower and (
        len(interval_lower) != len(cases) or len(interval_upper) != len(cases)
    ):
        raise ValueError("forecast interval batches must align with cases")
    for case_index, (case, prediction) in enumerate(zip(cases, predictions, strict=True)):
        if len(prediction) < len(case.actual):
            raise ValueError("forecast horizon is shorter than the benchmark horizon")
        forecast = tuple(float(value) for value in prediction[: len(case.actual)])
        if any(not math.isfinite(value) for value in forecast):
            raise ValueError("forecast contains a non-finite value")
        naive_scale = sum(
            abs(right - left) for left, right in zip(case.context, case.context[1:], strict=False)
        ) / (len(case.context) - 1)
        if naive_scale <= 0:
            raise ValueError("MASE scale must be positive")
        for predicted, actual in zip(forecast, case.actual, strict=True):
            error = predicted - actual
            errors.append(abs(error))
            squared.append(error * error)
            scaled.append(abs(error) / naive_scale)
        previous = case.context[-1]
        for predicted, actual in zip(forecast, case.actual, strict=True):
            direction_matches.append((predicted - previous >= 0) == (actual - previous >= 0))
            previous = actual
        if interval_lower:
            lower = tuple(float(value) for value in interval_lower[case_index][: len(case.actual)])
            upper = tuple(float(value) for value in interval_upper[case_index][: len(case.actual)])
            if len(lower) != len(case.actual) or len(upper) != len(case.actual):
                raise ValueError("forecast interval horizon is incomplete")
            interval_hits.extend(
                left <= actual <= right
                for left, actual, right in zip(lower, case.actual, upper, strict=True)
            )
    return ForecastBenchmarkMetrics(
        model_name=model_name,
        cases=len(cases),
        observations=len(errors),
        mae=sum(errors) / len(errors),
        rmse=math.sqrt(sum(squared) / len(squared)),
        mase=sum(scaled) / len(scaled),
        directional_accuracy=sum(direction_matches) / len(direction_matches),
        interval_coverage=(sum(interval_hits) / len(interval_hits) if interval_hits else None),
        latency_p50_ms=latency_p50_ms,
        latency_p95_ms=latency_p95_ms,
        cold_load_ms=cold_load_ms,
        peak_rss_mib=peak_rss_mib,
        peak_vram_mib=peak_vram_mib,
        resource_limit_passed=resource_limit_passed,
    )


def mandatory_baseline_metrics(
    cases: Sequence[ForecastCase],
) -> tuple[ForecastBenchmarkMetrics, ...]:
    models = (
        NaiveForecaster(),
        DriftForecaster(),
        SeasonalForecaster(period=7),
        LinearForecaster(),
        LightGBMBaseline(),
    )
    results: list[ForecastBenchmarkMetrics] = []
    for model in models:
        predictions = tuple(
            tuple(float(value) for value in model.predict(tuple(Decimal(str(x)) for x in case.context), 30))
            for case in cases
        )
        results.append(forecast_metrics(model.name, cases, predictions))
    return tuple(results)


def sentiment_metrics(
    model_name: str,
    expected: Sequence[str],
    predicted: Sequence[tuple[str, float]],
    *,
    latency_p50_ms: float,
    latency_p95_ms: float,
    throughput_per_second: float,
    peak_rss_mib: float,
) -> SentimentBenchmarkMetrics:
    labels = ("negative", "neutral", "positive")
    if len(expected) != len(predicted) or not expected:
        raise ValueError("sentiment labels and predictions must align")
    matrix = [[0 for _ in labels] for _ in labels]
    confidences: list[float] = []
    correct: list[bool] = []
    for truth, (guess, confidence) in zip(expected, predicted, strict=True):
        if truth not in labels or guess not in labels or not 0 <= confidence <= 1:
            raise ValueError("sentiment benchmark item is invalid")
        matrix[labels.index(truth)][labels.index(guess)] += 1
        confidences.append(confidence)
        correct.append(truth == guess)
    precision: list[tuple[str, float]] = []
    recall: list[tuple[str, float]] = []
    f1s: list[tuple[str, float]] = []
    for index, label in enumerate(labels):
        true_positive = matrix[index][index]
        predicted_total = sum(row[index] for row in matrix)
        actual_total = sum(matrix[index])
        p = true_positive / predicted_total if predicted_total else 0.0
        r = true_positive / actual_total if actual_total else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        precision.append((label, p))
        recall.append((label, r))
        f1s.append((label, f1))
    bins = 10
    ece = 0.0
    for index in range(bins):
        lower, upper = index / bins, (index + 1) / bins
        members = [
            item_index
            for item_index, value in enumerate(confidences)
            if lower <= value and (value <= upper if index == bins - 1 else value < upper)
        ]
        if members:
            accuracy = sum(correct[i] for i in members) / len(members)
            confidence = sum(confidences[i] for i in members) / len(members)
            ece += len(members) / len(expected) * abs(accuracy - confidence)
    epsilon = 1e-12
    nll = -sum(
        math.log(max(epsilon, confidence if is_correct else (1 - confidence) / 2))
        for confidence, is_correct in zip(confidences, correct, strict=True)
    ) / len(expected)
    brier = sum((confidence - float(is_correct)) ** 2 for confidence, is_correct in zip(confidences, correct, strict=True)) / len(expected)
    return SentimentBenchmarkMetrics(
        model_name=model_name,
        observations=len(expected),
        accuracy=sum(correct) / len(correct),
        macro_f1=sum(value for _label, value in f1s) / len(labels),
        per_label_precision=tuple(precision),
        per_label_recall=tuple(recall),
        per_label_f1=tuple(f1s),
        confusion_matrix=tuple(tuple(row) for row in matrix),
        expected_calibration_error=ece,
        brier_score=brier,
        negative_log_likelihood=nll,
        latency_p50_ms=latency_p50_ms,
        latency_p95_ms=latency_p95_ms,
        throughput_per_second=throughput_per_second,
        peak_rss_mib=peak_rss_mib,
    )


def write_immutable_json(path: Path, payload: object) -> Path:
    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != encoded:
        raise FileExistsError(f"immutable evidence differs: {path}")
    path.write_bytes(encoded)
    return path


def parse_financial_phrasebank_text(body: bytes) -> tuple[tuple[str, str], ...]:
    """Parse the public Financial PhraseBank ``Sentences_AllAgree`` format."""

    rows: list[tuple[str, str]] = []
    for raw_line in io.StringIO(body.decode("latin-1")):
        line = raw_line.strip()
        if not line:
            continue
        text, separator, label = line.rpartition("@")
        if not separator or label not in {"negative", "neutral", "positive"}:
            raise ValueError("Financial PhraseBank line is malformed")
        rows.append((text, label))
    if len(rows) < 100:
        raise ValueError("Financial PhraseBank snapshot is unexpectedly small")
    return tuple(rows)


def parse_financial_phrasebank_pages(
    bodies: Sequence[bytes],
    *,
    repository_id: str,
    revision: str,
    config: str,
    split: str,
    source: str,
    acquired_at: datetime,
) -> SentimentBenchmarkSnapshot:
    label_names = {0: "negative", 1: "neutral", 2: "positive"}
    examples: dict[int, SentimentExample] = {}
    for body in bodies:
        decoded = json.loads(body)
        rows = decoded.get("rows") if isinstance(decoded, Mapping) else None
        if not isinstance(rows, list):
            raise ValueError("Financial PhraseBank rows response is malformed")
        for item in rows:
            if not isinstance(item, Mapping) or not isinstance(item.get("row"), Mapping):
                raise ValueError("Financial PhraseBank example is malformed")
            row = item["row"]
            label = label_names.get(int(row["label"]))
            if label is None:
                raise ValueError("Financial PhraseBank label is unknown")
            example_id = int(row["__index_level_0__"])
            examples[example_id] = SentimentExample(
                example_id=example_id,
                text=str(row["sentence"]),
                label=label,
            )
    ordered = tuple(examples[key] for key in sorted(examples))
    return SentimentBenchmarkSnapshot(
        repository_id=repository_id,
        revision=revision,
        config=config,
        split=split,
        source=source,
        acquired_at=acquired_at,
        examples=ordered,
        raw_sha256=_sha256_bytes(b"".join(bodies)),
        content_hash=_sha256_bytes(
            _canonical_bytes([item.model_dump(mode="json") for item in ordered])
        ),
    )
