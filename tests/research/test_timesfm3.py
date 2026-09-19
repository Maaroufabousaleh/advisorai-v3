from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from advisorai.contracts import AssetClass, InstrumentIdentity
from advisorai.models.forecasting import GpuModelLease
from advisorai.research.challengers.timesfm3 import (
    TIMESFM3_CHECKPOINT,
    CovariateRole,
    KnownFutureBasis,
    KnownFutureValueKind,
    TimesFM3Ablation,
    TimesFM3ActivationError,
    TimesFM3ActivationScope,
    TimesFM3CovariatePoint,
    TimesFM3CovariateSeries,
    TimesFM3EvaluationRequest,
    TimesFM3ModelUnavailable,
    TimesFM3RawOutput,
    TimesFM3ResearchAdapter,
    TimesFM3ResearchInput,
    TimesFM3TargetSeries,
    assert_timesfm3_activation_allowed,
    assert_timesfm3_cannot_self_promote,
    build_timesfm3_qualification_matrix,
    evaluate_timesfm3,
    load_timesfm3_governance,
)

UTC_CUTOFF = datetime(2026, 9, 18, 12, tzinfo=UTC)


def _instrument(symbol: str) -> InstrumentIdentity:
    return InstrumentIdentity(
        canonical_id=f"binance:{symbol}",
        asset_class=AssetClass.CRYPTO,
        venue="binance",
        venue_symbol=symbol,
    )


def _point(
    role: CovariateRole,
    timestamp: datetime,
    *,
    value: str = "100",
    known_at_cutoff: bool = False,
    basis: KnownFutureBasis | None = None,
    value_kind: KnownFutureValueKind | None = None,
    first_available_at: datetime | None = None,
) -> TimesFM3CovariatePoint:
    available = first_available_at or timestamp - timedelta(minutes=1)
    return TimesFM3CovariatePoint(
        role=role,
        event_at=timestamp,
        value=Decimal(value),
        as_of=UTC_CUTOFF,
        first_available_at=available,
        ingested_at=available + timedelta(seconds=1),
        source_identity="binance-public-5m",
        lineage=("receipt:fixture-1", "parser:v1"),
        known_at_cutoff=known_at_cutoff,
        known_future_basis=basis,
        known_future_value_kind=value_kind,
    )


def _research_input(
    *, covariates: tuple[TimesFM3CovariateSeries, ...] = ()
) -> TimesFM3ResearchInput:
    points = tuple(
        _point(
            CovariateRole.TARGET_VARIATE,
            UTC_CUTOFF - timedelta(minutes=5 * index),
            value=str(index),
        )
        for index in range(5, 0, -1)
    )
    return TimesFM3ResearchInput(
        snapshot_id=uuid4(),
        cutoff=UTC_CUTOFF,
        horizon_bars=2,
        interval_seconds=300,
        target_variates=(
            TimesFM3TargetSeries(
                name="BTCUSDT",
                instrument=_instrument("BTCUSDT"),
                points=points,
            ),
        ),
        covariates=covariates,
        calibration_version="timesfm3-cal-v1",
    )


def test_governance_is_machine_readable_and_research_only():
    governance = load_timesfm3_governance()
    assert governance.model_dump_json() == load_timesfm3_governance().model_dump_json()
    assert governance.model == TIMESFM3_CHECKPOINT
    assert governance.role == "research_challenger"
    assert governance.priority_class == "PRIORITY_CHALLENGER"
    assert governance.deployment_class == "RESEARCH_ONLY"
    assert governance.production_class == "NON_PRODUCTION"
    assert governance.weights_class == "NON_COMMERCIAL_WEIGHTS"
    assert governance.commercial_use is False
    assert governance.production_use is False
    assert governance.execution_use is False
    assert governance.automatic_promotion is False
    assert governance.license_class == "research_only_nonproduction"


@pytest.mark.parametrize(
    "scope",
    (
        TimesFM3ActivationScope.PRODUCTION,
        TimesFM3ActivationScope.LIVE,
        TimesFM3ActivationScope.EXECUTION,
        TimesFM3ActivationScope.COMMERCIAL_DECISION_SERVICE,
        TimesFM3ActivationScope.CORE_TRADING_HOT_PATH,
    ),
)
def test_activation_fails_closed_outside_research(scope):
    with pytest.raises(TimesFM3ActivationError, match="cannot activate"):
        assert_timesfm3_activation_allowed(scope)


def test_research_activation_does_not_load_or_download():
    adapter = TimesFM3ResearchAdapter()
    adapter.activate()
    assert adapter.model_resident is False
    with pytest.raises(TimesFM3ModelUnavailable, match="local checkpoint or opt-in"):
        adapter.load()


def test_no_core_runtime_dependency_or_unconditional_optional_dependency():
    core_files = list(Path("src/advisorai/models").rglob("*.py")) + list(
        Path("src/advisorai/phase4").rglob("*.py")
    )
    assert all("timesfm3" not in path.read_text(encoding="utf-8").lower() for path in core_files)
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8").lower()
    assert "timesfm" not in pyproject
    assert "timesfm" not in Path("uv.lock").read_text(encoding="utf-8").lower()
    assert "timesfm" not in Path("configs/models/v3_core.yaml").read_text(encoding="utf-8").lower()


def test_pit_accepts_only_explicit_known_future_schedule():
    known = _point(
        CovariateRole.KNOWN_FUTURE_COVARIATE,
        UTC_CUTOFF + timedelta(hours=1),
        value="1",
        known_at_cutoff=True,
        basis=KnownFutureBasis.CALENDAR,
        first_available_at=UTC_CUTOFF - timedelta(days=1),
        value_kind=KnownFutureValueKind.CALENDAR,
    )
    assert known.event_at > known.as_of
    with pytest.raises(ValueError, match="known-future"):
        _point(
            CovariateRole.KNOWN_FUTURE_COVARIATE,
            UTC_CUTOFF + timedelta(hours=1),
            known_at_cutoff=False,
            first_available_at=UTC_CUTOFF - timedelta(days=1),
        )
    with pytest.raises(ValueError, match="future event"):
        _point(
            CovariateRole.PAST_ONLY_COVARIATE,
            UTC_CUTOFF + timedelta(hours=1),
            first_available_at=UTC_CUTOFF - timedelta(days=1),
        )


def test_known_future_result_cannot_use_schedule_semantics():
    with pytest.raises(ValueError, match="value kind"):
        TimesFM3CovariatePoint(
            role=CovariateRole.KNOWN_FUTURE_COVARIATE,
            event_at=UTC_CUTOFF + timedelta(hours=1),
            value=Decimal("250"),
            as_of=UTC_CUTOFF,
            first_available_at=UTC_CUTOFF - timedelta(days=1),
            ingested_at=UTC_CUTOFF - timedelta(hours=23),
            source_identity="future-cpi-result-fixture",
            lineage=("receipt:fixture-result",),
            known_at_cutoff=True,
            known_future_basis=KnownFutureBasis.SCHEDULED_CPI_TIMESTAMP,
            known_future_value_kind=KnownFutureValueKind.CALENDAR,
        )


def test_pit_rejects_value_first_available_after_cutoff():
    with pytest.raises(ValueError, match="future leakage"):
        _point(
            CovariateRole.PAST_ONLY_COVARIATE,
            UTC_CUTOFF - timedelta(minutes=1),
            first_available_at=UTC_CUTOFF + timedelta(seconds=1),
        )


def test_input_hash_and_validation_are_deterministic():
    first = _research_input()
    second = first.model_copy(deep=True)
    assert first.canonical_pit_hash() == second.canonical_pit_hash()
    assert first.snapshot_id == second.snapshot_id


def test_injected_runner_emits_forecast_compatible_research_artifact():
    seen_resident: list[bool] = []

    def runner(research_input: TimesFM3ResearchInput) -> TimesFM3RawOutput:
        seen_resident.append(adapter.model_resident)
        return TimesFM3RawOutput(
            point_forecasts=((Decimal("101"), Decimal("102")),),
            quantile_forecasts=(
                (
                    tuple(Decimal(str(index)) for index in range(9)),
                    tuple(Decimal(str(index + 1)) for index in range(9)),
                ),
            ),
        )

    adapter = TimesFM3ResearchAdapter(runner=runner)
    artifact = adapter.forecast(_research_input())
    assert seen_resident == [True]
    assert artifact.forecasts[0].model_version == TIMESFM3_CHECKPOINT
    assert artifact.forecasts[0].point_forecast == Decimal("101")
    assert artifact.production_eligible is False
    assert artifact.execution_eligible is False
    assert adapter.model_resident is False


def test_gpu_family_boundary_is_enforced():
    adapter = TimesFM3ResearchAdapter(
        runner=lambda _input: TimesFM3RawOutput(point_forecasts=((Decimal("1"), Decimal("2")),))
    )
    with GpuModelLease("chronos"):
        with pytest.raises(RuntimeError, match="GPU lease"):
            adapter.forecast(_research_input())


def test_challenger_cannot_self_promote():
    with pytest.raises(TimesFM3ActivationError, match="self-promotion"):
        assert_timesfm3_cannot_self_promote()


def test_qualification_matrix_and_evidence_are_non_blocking():
    matrix = build_timesfm3_qualification_matrix()
    assert [item.ablation for item in matrix] == list(TimesFM3Ablation)
    assert matrix[2].past_only_covariates == ("realized_volatility", "volume")
    assert matrix[4].known_future_covariates == ("calendar",)
    evidence = evaluate_timesfm3(
        TimesFM3EvaluationRequest(
            model_variant="timesfm-3-univariate",
            ablation=TimesFM3Ablation.A,
            predictions=(Decimal("1"), Decimal("1")),
            actuals=(Decimal("1"), Decimal("1")),
            baseline_utility=Decimal("0"),
            past_only=True,
            calibrated=True,
            regime_robust=True,
            realistic_costs_applied=True,
            resource_limit_passed=True,
        )
    )
    assert evidence.eligible_for_external_review is True
    assert evidence.automatic_promotion is False
    assert evidence.production_eligible is False
    assert evidence.execution_eligible is False
