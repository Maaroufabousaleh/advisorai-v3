from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from advisorai.phase4 import (
    V3_CORE_BASELINES,
    CausalBaselineRegeneration,
    V3CoreBar,
    V3CoreBarProvenance,
    build_v3core_cases,
    regenerate_causal_baselines,
)

HASH = "a" * 64
START = datetime(2026, 8, 5, 1, 0, tzinfo=UTC)
ENDPOINT = "https://data-api.binance.vision/api/v3/klines"
SOURCE = "binance_spot_public_market_data"
MATERIALIZED_AT = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
REPOSITORY_COMMIT = "b" * 40


def _bars() -> tuple[V3CoreBar, ...]:
    result = []
    for index in range(61):
        close = Decimal("10000") + Decimal(index)
        interval_end = START + timedelta(minutes=5 * index)
        result.append(
            V3CoreBar(
                instrument="BTCUSDT",
                provenance=V3CoreBarProvenance(
                    interval_end=interval_end,
                    provider_available_at=interval_end,
                    collected_at=interval_end + timedelta(seconds=1),
                    provider_event_at=interval_end,
                    availability_basis="forward_observed",
                    evidence_class="forward_pit_admission",
                    source_snapshot_hash=HASH,
                    raw_record_hash=HASH,
                    normalized_record_hash=HASH,
                    source_health_state="HEALTHY",
                ),
                open=close - Decimal("1"),
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=Decimal("1"),
                source_id=SOURCE,
                provider_identity=SOURCE,
                endpoint=ENDPOINT,
                source_snapshot_hash=HASH,
            )
        )
    return tuple(result)


def _case():
    return build_v3core_cases(
        _bars(),
        evidence_class="forward_pit_admission",
        source_id=SOURCE,
        provider_identity=SOURCE,
        endpoint=ENDPOINT,
        source_snapshot_hash=HASH,
        phase3_admitted=True,
    ).cases[0]


def test_regeneration_covers_every_mandatory_baseline_and_is_explicitly_retrospective() -> None:
    report = regenerate_causal_baselines(
        (_case(),),
        repository_root=Path.cwd(),
        repository_commit=REPOSITORY_COMMIT,
        materialized_at=MATERIALIZED_AT,
    )
    assert len(report.predictions) == len(V3_CORE_BASELINES)
    assert {item.model for item in report.predictions} == set(V3_CORE_BASELINES)
    assert all(
        item.evidence_class == "post_seal_causal_regeneration" for item in report.predictions
    )
    assert all(item.future_outcome_used is False for item in report.predictions)
    assert all(item.materialized_at == MATERIALIZED_AT for item in report.predictions)
    assert report.network_calls == 0
    assert report.credentials_loaded is False
    assert report.order_writes_attempted is False


def test_regeneration_ignores_future_outcome_content() -> None:
    original = _case()
    changed_future = tuple(
        bar.model_copy(update={"close": bar.close + Decimal("500")}) for bar in original.future_bars
    )
    changed = original.model_copy(update={"future_bars": changed_future})
    first = regenerate_causal_baselines(
        (original,),
        repository_root=Path.cwd(),
        repository_commit=REPOSITORY_COMMIT,
        materialized_at=MATERIALIZED_AT,
    )
    second = regenerate_causal_baselines(
        (changed,),
        repository_root=Path.cwd(),
        repository_commit=REPOSITORY_COMMIT,
        materialized_at=MATERIALIZED_AT,
    )
    first_scientific = [
        item.model_dump(exclude={"runtime_latency_ms"}) for item in first.predictions
    ]
    second_scientific = [
        item.model_dump(exclude={"runtime_latency_ms"}) for item in second.predictions
    ]
    assert first_scientific == second_scientific


def test_regeneration_refuses_unadmitted_or_historical_cases() -> None:
    case = _case()
    with pytest.raises(ValueError, match="admitted forward PIT"):
        regenerate_causal_baselines(
            (case.model_copy(update={"phase3_admitted": False}),),
            repository_root=Path.cwd(),
            repository_commit=REPOSITORY_COMMIT,
            materialized_at=MATERIALIZED_AT,
        )

    with pytest.raises(ValueError, match="admitted forward PIT"):
        regenerate_causal_baselines(
            (case.model_copy(update={"evidence_class": "historical_development"}),),
            repository_root=Path.cwd(),
            repository_commit=REPOSITORY_COMMIT,
            materialized_at=MATERIALIZED_AT,
        )


def test_regeneration_refuses_mixed_source_identity() -> None:
    with pytest.raises(ValueError, match="source identity substitution"):
        regenerate_causal_baselines(
            (
                _case(),
                _case().model_copy(
                    update={"case_id": "different-case", "source_snapshot_hash": "b" * 64}
                ),
            ),
            repository_root=Path.cwd(),
            repository_commit=REPOSITORY_COMMIT,
            materialized_at=MATERIALIZED_AT,
        )


def test_regeneration_requires_a_git_commit_identity() -> None:
    with pytest.raises(ValueError, match="repository_commit"):
        regenerate_causal_baselines(
            (_case(),),
            repository_root=Path.cwd(),
            repository_commit="development-fixture",
            materialized_at=MATERIALIZED_AT,
        )


def test_batch_validation_rejects_duplicate_prediction_records() -> None:
    report = regenerate_causal_baselines(
        (_case(),),
        repository_root=Path.cwd(),
        repository_commit=REPOSITORY_COMMIT,
        materialized_at=MATERIALIZED_AT,
    )
    payload = report.model_dump(mode="json")
    payload["predictions"].append(payload["predictions"][0])
    with pytest.raises(ValueError, match="unique identities"):
        CausalBaselineRegeneration.model_validate(payload)


def test_batch_validation_rejects_unexpected_prediction_identity() -> None:
    report = regenerate_causal_baselines(
        (_case(),),
        repository_root=Path.cwd(),
        repository_commit=REPOSITORY_COMMIT,
        materialized_at=MATERIALIZED_AT,
    )
    payload = report.model_dump(mode="json")
    payload["predictions"][0]["prediction_id"] = "wrong-case:naive"
    with pytest.raises(ValueError, match="cover every case"):
        CausalBaselineRegeneration.model_validate(payload)


def test_batch_validation_binds_prediction_id_to_case_and_model() -> None:
    report = regenerate_causal_baselines(
        (_case(),),
        repository_root=Path.cwd(),
        repository_commit=REPOSITORY_COMMIT,
        materialized_at=MATERIALIZED_AT,
    )
    payload = report.model_dump(mode="json")
    payload["predictions"][0]["case_id"] = "wrong-case"
    with pytest.raises(ValueError, match="bind case and model"):
        CausalBaselineRegeneration.model_validate(payload)


def test_regeneration_requires_post_cutoff_materialization_time() -> None:
    with pytest.raises(ValueError, match="after every case cutoff"):
        regenerate_causal_baselines(
            (_case(),),
            repository_root=Path.cwd(),
            repository_commit=REPOSITORY_COMMIT,
            materialized_at=datetime(2026, 8, 5, 4, 0, tzinfo=UTC),
        )
