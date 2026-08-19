"""Strictly causal, post-seal V3-Core baseline regeneration.

The forward baseline worker is intentionally prospective.  This separate path
exists for the reviewed materialization contract, which permits mandatory
baselines to be regenerated after a sealed source root.  It uses only each
case's context bars; the future bars and realized return are never read by the
forecast loop.  Its records are explicitly retrospective and are not
prospective prediction-ledger records.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.models.forecasting import (
    DriftForecaster,
    LinearForecaster,
    NaiveForecaster,
    SeasonalForecaster,
)
from advisorai.phase0.runtime_qualification import LightGBMBaseline, QualificationError
from advisorai.phase4.v3core_cadence import V3_CORE_BASELINES, V3CoreForecastCase

CAUSAL_BASELINE_SCHEMA = "advisorai.phase4.v3-core-forward.causal-baseline.v1"
CAUSAL_BASELINE_IDENTITY_SCHEMA = "advisorai.phase4.v3-core-forward.baseline-identity.v1"
CAUSAL_BASELINE_EVIDENCE_CLASS = "post_seal_causal_regeneration"
CONTEXT_BARS = 48
HORIZON_BARS = 12
GIT_COMMIT_LENGTH = 40
HEX = frozenset("0123456789abcdef")


def _canonical(payload: object) -> bytes:
    import json

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field_name} must be a SHA-256 digest")
    return normalized


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _input_snapshot_hash(case: V3CoreForecastCase) -> str:
    return sha256(
        _canonical(
            {
                "schema": "advisorai.phase4.v3-core-forward.prediction-input.v1",
                "cutoff": case.cutoff.isoformat(),
                "context": [bar.model_dump(mode="json") for bar in case.context_bars],
            }
        )
    ).hexdigest()


def _identity_hash(
    *,
    model: str,
    repository_root: Path,
    forecasting_hash: str,
    lightgbm_hash: str,
) -> str:
    return sha256(
        _canonical(
            {
                "schema": CAUSAL_BASELINE_IDENTITY_SCHEMA,
                "model": model,
                "horizon_bars": HORIZON_BARS,
                "context_bars": CONTEXT_BARS,
                "forecasting_code_sha256": forecasting_hash,
                "lightgbm_code_sha256": lightgbm_hash,
                "repository_root": str(repository_root.resolve()),
            }
        )
    ).hexdigest()


def _predict_prices(model: str, values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    if model == "naive":
        forecaster = NaiveForecaster()
    elif model == "drift":
        forecaster = DriftForecaster()
    elif model == "seasonal-7":
        forecaster = SeasonalForecaster(7)
    elif model == "linear":
        forecaster = LinearForecaster()
    elif model == "lightgbm":
        forecaster = LightGBMBaseline()
    else:
        raise ValueError(f"unsupported baseline: {model}")
    try:
        return tuple(Decimal(str(value)) for value in forecaster.predict(values, HORIZON_BARS))
    except QualificationError:
        raise


class CausalBaselinePrediction(BaseModel):
    """One explicitly retrospective baseline prediction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = CAUSAL_BASELINE_SCHEMA
    prediction_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    instrument: str = Field(min_length=1)
    model: str = Field(min_length=1)
    model_identity_hash: str
    model_code_hash: str
    model_artifact_hash: str
    cutoff: datetime
    input_snapshot_hash: str
    source_snapshot_hash: str
    predicted_return_bps: Decimal
    materialized_at: datetime
    runtime_latency_ms: Decimal = Field(ge=0)
    evidence_class: str = CAUSAL_BASELINE_EVIDENCE_CLASS
    future_outcome_used: bool = False

    @field_validator("model_identity_hash", "model_code_hash", "model_artifact_hash")
    @classmethod
    def valid_model_hash(cls, value: str, info: object) -> str:
        return _digest(value, getattr(info, "field_name", "model hash"))

    @field_validator("input_snapshot_hash", "source_snapshot_hash")
    @classmethod
    def valid_snapshot_hash(cls, value: str, info: object) -> str:
        return _digest(value, getattr(info, "field_name", "snapshot hash"))

    @field_validator("cutoff", "materialized_at")
    @classmethod
    def aware_timestamp(cls, value: datetime, info: object) -> datetime:
        return _aware(value, getattr(info, "field_name", "timestamp"))

    @field_validator("predicted_return_bps", "runtime_latency_ms")
    @classmethod
    def finite_decimal(cls, value: Decimal, info: object) -> Decimal:
        if not value.is_finite():
            raise ValueError(f"{getattr(info, 'field_name', 'prediction')} must be finite")
        return value

    @model_validator(mode="after")
    def validate_record(self) -> CausalBaselinePrediction:
        if self.schema_version != CAUSAL_BASELINE_SCHEMA:
            raise ValueError("unsupported causal baseline schema")
        if self.model not in V3_CORE_BASELINES:
            raise ValueError("causal regeneration is restricted to mandatory baselines")
        if self.materialized_at < self.cutoff:
            raise ValueError("causal baseline materialization cannot precede its cutoff")
        if self.evidence_class != CAUSAL_BASELINE_EVIDENCE_CLASS:
            raise ValueError("causal baseline evidence must remain explicitly retrospective")
        if self.future_outcome_used:
            raise ValueError("causal baseline regeneration must not use future outcomes")
        return self


class CausalBaselineRegeneration(BaseModel):
    """Immutable-shaped output of a post-seal causal baseline pass."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = CAUSAL_BASELINE_SCHEMA
    generated_at: datetime
    repository_commit: str = Field(min_length=GIT_COMMIT_LENGTH, max_length=GIT_COMMIT_LENGTH)
    forecasting_code_sha256: str
    lightgbm_code_sha256: str
    case_ids: tuple[str, ...]
    predictions: tuple[CausalBaselinePrediction, ...]
    network_calls: int = 0
    credentials_loaded: bool = False
    order_writes_attempted: bool = False

    @field_validator("generated_at")
    @classmethod
    def aware_generated_at(cls, value: datetime) -> datetime:
        return _aware(value, "generated_at")

    @field_validator("forecasting_code_sha256", "lightgbm_code_sha256")
    @classmethod
    def valid_code_hash(cls, value: str, info: object) -> str:
        return _digest(value, getattr(info, "field_name", "code hash"))

    @field_validator("repository_commit")
    @classmethod
    def valid_repository_commit(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != GIT_COMMIT_LENGTH or any(
            character not in HEX for character in normalized
        ):
            raise ValueError("repository_commit must be a Git SHA-1")
        return normalized

    @model_validator(mode="after")
    def validate_batch(self) -> CausalBaselineRegeneration:
        if self.schema_version != CAUSAL_BASELINE_SCHEMA:
            raise ValueError("unsupported causal baseline schema")
        if self.network_calls != 0 or self.credentials_loaded or self.order_writes_attempted:
            raise ValueError("causal baseline regeneration must remain offline and write-free")
        if len(self.case_ids) != len(set(self.case_ids)):
            raise ValueError("causal baseline case identities must be unique")
        expected_ids = {
            f"{case_id}:{model}" for case_id in self.case_ids for model in V3_CORE_BASELINES
        }
        actual_ids = tuple(item.prediction_id for item in self.predictions)
        if len(actual_ids) != len(expected_ids) or len(set(actual_ids)) != len(actual_ids):
            raise ValueError("causal baseline predictions must have unique identities")
        if set(actual_ids) != expected_ids:
            raise ValueError("causal baseline predictions must cover every case and baseline")
        if any(item.prediction_id != f"{item.case_id}:{item.model}" for item in self.predictions):
            raise ValueError("causal baseline prediction identity must bind case and model")
        return self


def regenerate_causal_baselines(
    cases: Sequence[V3CoreForecastCase],
    *,
    repository_root: Path,
    repository_commit: str,
    materialized_at: datetime | None = None,
) -> CausalBaselineRegeneration:
    """Regenerate all mandatory baselines using context bars only.

    ``future_bars``, ``realized_return_bps``, and ``realized_at`` are not read
    by this function.  They remain part of the sealed case artifact for later
    evaluation, but cannot influence a regenerated prediction.
    """

    if not cases:
        raise ValueError("causal baseline regeneration requires sealed cases")
    repository_root = repository_root.resolve()
    forecasting_path = repository_root / "src/advisorai/models/forecasting.py"
    lightgbm_path = repository_root / "src/advisorai/phase0/runtime_qualification.py"
    if not forecasting_path.is_file() or not lightgbm_path.is_file():
        raise ValueError("baseline implementation sources are missing")
    forecasting_hash = _sha256_file(forecasting_path)
    lightgbm_hash = _sha256_file(lightgbm_path)
    timestamp = _aware(materialized_at or datetime.now(UTC), "materialized_at")
    ordered_cases = tuple(sorted(cases, key=lambda case: (case.cutoff, case.instrument)))
    case_ids = tuple(case.case_id for case in ordered_cases)
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("sealed cases contain duplicate identities")
    for case in ordered_cases:
        if not case.phase3_admitted or case.evidence_class != "forward_pit_admission":
            raise ValueError("causal regeneration requires admitted forward PIT cases")
        if timestamp < case.cutoff:
            raise ValueError("materialization timestamp must be after every case cutoff")

    predictions: list[CausalBaselinePrediction] = []
    for case in ordered_cases:
        context_values = tuple(bar.close for bar in case.context_bars)
        input_hash = _input_snapshot_hash(case)
        for model in V3_CORE_BASELINES:
            started = time.perf_counter()
            prices = _predict_prices(model, context_values)
            elapsed_ms = Decimal(str((time.perf_counter() - started) * 1000))
            predictions.append(
                CausalBaselinePrediction(
                    prediction_id=f"{case.case_id}:{model}",
                    case_id=case.case_id,
                    instrument=case.instrument,
                    model=model,
                    model_identity_hash=_identity_hash(
                        model=model,
                        repository_root=repository_root,
                        forecasting_hash=forecasting_hash,
                        lightgbm_hash=lightgbm_hash,
                    ),
                    model_code_hash=(lightgbm_hash if model == "lightgbm" else forecasting_hash),
                    model_artifact_hash=(
                        lightgbm_hash if model == "lightgbm" else forecasting_hash
                    ),
                    cutoff=case.cutoff,
                    input_snapshot_hash=input_hash,
                    source_snapshot_hash=case.source_snapshot_hash,
                    predicted_return_bps=(prices[-1] / context_values[-1] - Decimal("1"))
                    * Decimal("10000"),
                    materialized_at=timestamp,
                    runtime_latency_ms=elapsed_ms,
                )
            )
    return CausalBaselineRegeneration(
        generated_at=timestamp,
        repository_commit=repository_commit,
        forecasting_code_sha256=forecasting_hash,
        lightgbm_code_sha256=lightgbm_hash,
        case_ids=case_ids,
        predictions=tuple(predictions),
    )


__all__ = [
    "CAUSAL_BASELINE_EVIDENCE_CLASS",
    "CAUSAL_BASELINE_IDENTITY_SCHEMA",
    "CAUSAL_BASELINE_SCHEMA",
    "CausalBaselinePrediction",
    "CausalBaselineRegeneration",
    "regenerate_causal_baselines",
]
