from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from advisorai.phase4.v3core_cadence import (
    V3_CORE_MARKET_DATA_REST_ENDPOINT,
    V3CoreBar,
    V3CoreBarProvenance,
)
from advisorai.phase4.v3core_chronos import (
    CHRONOS_CONTEXT_BARS,
    CHRONOS_MODEL,
    CHRONOS_OUTPUT_BARS,
    ChronosInferenceResult,
    ChronosRuntimeIdentity,
    _request_for_inference,
    build_chronos_prediction,
    context_for_cutoff,
    validate_resume_manifest,
)

HASH = "a" * 64
CUTOFF = datetime(2026, 8, 17, 21, 0, tzinfo=UTC)


def _identity(*, native_context_min: int = 32) -> ChronosRuntimeIdentity:
    return ChronosRuntimeIdentity(
        candidate_name=CHRONOS_MODEL,
        checkpoint_repository="autogluon/chronos-2-small",
        checkpoint_revision="ddec01313e50b6bc58ebaa92ede81bc24a3d9f9a",
        cache_path="/tmp/chronos-2-small",
        cache_subdir="model",
        config_hash=HASH,
        checkpoint_hash=HASH,
        dependencies=("chronos-forecasting==2.3.1",),
        environment_fingerprint=HASH,
        installed_environment_manifest_path="/tmp/environment.json",
        installed_environment_sha256=HASH,
        lock_artifact_path="/tmp/requirements.lock",
        lock_hash=HASH,
        python_constraint=">=3.12,<3.13",
        python_launcher="/tmp/python",
        python_launcher_hash=HASH,
        resolved_python_binary_hash=HASH,
        runner_version="advisorai-chronos-2-small-worker-v1",
        runner_hash=HASH,
        runner_script="/tmp/runtime_qualification_worker.py",
        runner_script_hash=HASH,
        admission_path="/tmp/admission.json",
        admission_sha256=HASH,
        qualification_evidence_path="/tmp/qualification.json",
        qualification_evidence_sha256=HASH,
        device="cuda",
        native_context_min=native_context_min,
    )


def _bar(
    interval_end: datetime,
    *,
    instrument: str = "BTCUSDT",
    health: str = "HEALTHY",
    source_hash: str = HASH,
    collected_at: datetime | None = None,
) -> V3CoreBar:
    collected = collected_at or interval_end + timedelta(seconds=1)
    provenance = V3CoreBarProvenance(
        interval_end=interval_end,
        provider_available_at=interval_end,
        collected_at=collected,
        availability_basis="forward_observed",
        evidence_class="forward_pit_admission",
        source_snapshot_hash=source_hash,
        raw_record_hash=HASH,
        normalized_record_hash=HASH,
        source_health_state=health,  # type: ignore[arg-type]
    )
    return V3CoreBar(
        instrument=instrument,
        provenance=provenance,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1"),
        source_id="binance_spot_public_market_data",
        provider_identity="binance_spot_public_market_data",
        endpoint=V3_CORE_MARKET_DATA_REST_ENDPOINT,
        source_snapshot_hash=source_hash,
    )


def _context(
    *,
    health: str = "HEALTHY",
    source_hash: str = HASH,
    last_collected_at: datetime | None = None,
) -> tuple[V3CoreBar, ...]:
    return tuple(
        _bar(
            CUTOFF - timedelta(minutes=5 * (CHRONOS_CONTEXT_BARS - index)),
            health=health,
            source_hash=source_hash,
            collected_at=(
                last_collected_at
                if index == CHRONOS_CONTEXT_BARS - 1 and last_collected_at is not None
                else CUTOFF
                - timedelta(minutes=5 * (CHRONOS_CONTEXT_BARS - index))
                + timedelta(seconds=1)
            ),
        )
        for index in range(CHRONOS_CONTEXT_BARS)
    )


def test_context_requires_exact_healthy_forward_bars() -> None:
    assert (
        context_for_cutoff(
            _context(last_collected_at=CUTOFF + timedelta(seconds=1)),
            instrument="BTCUSDT",
            cutoff=CUTOFF,
            now=CUTOFF - timedelta(seconds=1),
        )
        is None
    )
    assert (
        context_for_cutoff(
            _context(health="DEGRADED"),
            instrument="BTCUSDT",
            cutoff=CUTOFF,
            now=CUTOFF - timedelta(seconds=1),
        )
        is None
    )
    assert (
        context_for_cutoff(
            _context(),
            instrument="BTCUSDT",
            cutoff=CUTOFF,
            now=CUTOFF - timedelta(seconds=1),
        )
        is not None
    )


def test_context_rejects_source_change_and_missing_bar() -> None:
    changed = list(_context())
    changed[-1] = _bar(changed[-1].interval_end, source_hash="b" * 64)
    assert (
        context_for_cutoff(
            changed,
            instrument="BTCUSDT",
            cutoff=CUTOFF,
            now=CUTOFF - timedelta(seconds=1),
        )
        is None
    )
    assert (
        context_for_cutoff(
            _context()[:-1],
            instrument="BTCUSDT",
            cutoff=CUTOFF,
            now=CUTOFF - timedelta(seconds=1),
        )
        is None
    )


def test_prediction_uses_preregistered_12th_output_and_native_bounds() -> None:
    result = ChronosInferenceResult(
        forecast=tuple(Decimal(100 + index) for index in range(CHRONOS_OUTPUT_BARS)),
        native_lower=tuple(Decimal(90 + index) for index in range(CHRONOS_OUTPUT_BARS)),
        native_upper=tuple(Decimal(110 + index) for index in range(CHRONOS_OUTPUT_BARS)),
        latency_ms=Decimal("12.5"),
        device="cuda",
        resource_peak_rss_mib=Decimal("100"),
        resource_peak_cpu_percent=Decimal("20"),
        resource_sample_count=3,
    )
    prediction = build_chronos_prediction(
        identity=_identity(),
        instrument="BTCUSDT",
        cutoff=CUTOFF,
        generated_at=CUTOFF - timedelta(seconds=1),
        context=_context(),
        result=result,
    )
    assert prediction.model == CHRONOS_MODEL
    assert prediction.predicted_return_bps == Decimal("1100")
    assert prediction.native_interval_lower_bps == Decimal("100")
    assert prediction.native_interval_upper_bps == Decimal("2100")
    assert prediction.outcome_case_id is None
    assert prediction.provenance


def test_prediction_rejects_generation_after_cutoff() -> None:
    result = ChronosInferenceResult(
        forecast=tuple(Decimal("100") for _ in range(CHRONOS_OUTPUT_BARS)),
        latency_ms=Decimal("1"),
        device="cuda",
        resource_peak_rss_mib=Decimal("1"),
        resource_peak_cpu_percent=Decimal("1"),
        resource_sample_count=1,
    )
    with pytest.raises(ValueError, match="after its cutoff"):
        build_chronos_prediction(
            identity=_identity(),
            instrument="BTCUSDT",
            cutoff=CUTOFF,
            generated_at=CUTOFF + timedelta(seconds=1),
            context=_context(),
            result=result,
        )


def test_request_requires_exact_48_values_and_never_pads() -> None:
    with pytest.raises(ValueError, match="exactly 48"):
        _request_for_inference(_identity(), _context()[:-1])
    request = _request_for_inference(_identity(), _context())
    assert len(request["sample_input"]) == 48
    assert len(request["batch_input"][0]) == 48
    assert request["family"] == CHRONOS_MODEL
    assert request["local_files_only"] is True


def test_runtime_identity_rejects_incompatible_native_context() -> None:
    with pytest.raises(ValueError, match="incompatible"):
        _identity(native_context_min=64)


def test_resume_manifest_requires_every_frozen_identity() -> None:
    expected = {
        "schema": "schema",
        "source_root": "source",
        "target_end_at": "2026-08-18T00:00:00Z",
    }
    changed = copy.deepcopy(expected)
    changed["target_end_at"] = "changed"
    with pytest.raises(ValueError, match="resume identity mismatch.*target_end_at"):
        validate_resume_manifest(changed, expected)


def test_native_intervals_must_have_matching_bounds() -> None:
    with pytest.raises(ValueError, match="require both bounds"):
        ChronosInferenceResult(
            forecast=(Decimal("100"),) * CHRONOS_OUTPUT_BARS,
            native_lower=(Decimal("90"),) * CHRONOS_OUTPUT_BARS,
            latency_ms=Decimal("1"),
            device="cuda",
            resource_peak_rss_mib=Decimal("1"),
            resource_peak_cpu_percent=Decimal("1"),
            resource_sample_count=1,
        )
