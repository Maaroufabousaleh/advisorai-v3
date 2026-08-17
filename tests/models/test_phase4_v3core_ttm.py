from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from advisorai.phase4.v3core_cadence import (
    V3_CORE_MARKET_DATA_REST_ENDPOINT,
    V3CoreBar,
    V3CoreBarProvenance,
)
from advisorai.phase4.v3core_ttm import (
    QUALIFIED_TTM_R2_RUNNER_CONTEXT_BARS,
    TTM_R2_CONTEXT_BARS,
    TTM_R2_MODEL,
    TTMR2InferenceResult,
    TTMR2RuntimeIdentity,
    _request_for_inference,
    build_ttm_prediction,
    context_for_cutoff,
    run,
    validate_resume_manifest,
)

HASH = "a" * 64
CUTOFF = datetime(2026, 8, 13, 1, 0, tzinfo=UTC)


def _identity(*, runner_context_bars: int = 48) -> TTMR2RuntimeIdentity:
    return TTMR2RuntimeIdentity(
        candidate_name=TTM_R2_MODEL,
        checkpoint_repository="ibm-granite/granite-timeseries-ttm-r2",
        checkpoint_revision="revision",
        cache_path="/tmp/ttm-r2",
        config_hash=HASH,
        generation_config_hash=HASH,
        checkpoint_hash=HASH,
        dependencies=("granite-tsfm==0.3.8",),
        environment_fingerprint=HASH,
        installed_environment_manifest_path="/tmp/environment.json",
        installed_environment_sha256=HASH,
        lock_artifact_path="/tmp/requirements.lock",
        lock_hash=HASH,
        python_constraint=">=3.12,<3.13",
        python_executable="/tmp/python",
        python_executable_hash=HASH,
        python_launcher="/tmp/python",
        python_launcher_hash=HASH,
        resolved_python_binary_hash=HASH,
        runner_version="runner-v1",
        runner_hash=HASH,
        runner_script="/tmp/worker.py",
        runner_script_hash=HASH,
        worker_kind=TTM_R2_MODEL,
        runner_context_bars=runner_context_bars,
    )


def _bar(interval_end: datetime, *, collected_at: datetime | None = None) -> V3CoreBar:
    collected = collected_at or interval_end + timedelta(seconds=1)
    provenance = V3CoreBarProvenance(
        interval_end=interval_end,
        provider_available_at=interval_end,
        collected_at=collected,
        availability_basis="forward_observed",
        evidence_class="forward_pit_admission",
        source_snapshot_hash=HASH,
        raw_record_hash=HASH,
        normalized_record_hash=HASH,
        source_health_state="HEALTHY",
    )
    return V3CoreBar(
        instrument="BTCUSDT",
        provenance=provenance,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1"),
        source_id="binance_spot_public_market_data",
        provider_identity="binance_spot_public_market_data",
        endpoint=V3_CORE_MARKET_DATA_REST_ENDPOINT,
        source_snapshot_hash=HASH,
    )


def _context(*, last_collected_at: datetime | None = None) -> tuple[V3CoreBar, ...]:
    return tuple(
        _bar(
            CUTOFF - timedelta(minutes=5 * (TTM_R2_CONTEXT_BARS - index)),
            collected_at=(
                last_collected_at
                if index == TTM_R2_CONTEXT_BARS - 1 and last_collected_at is not None
                else CUTOFF
                - timedelta(minutes=5 * (TTM_R2_CONTEXT_BARS - index))
                + timedelta(seconds=1)
            ),
        )
        for index in range(TTM_R2_CONTEXT_BARS)
    )


def test_context_requires_forward_observed_bars_available_by_cutoff() -> None:
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
            _context(), instrument="BTCUSDT", cutoff=CUTOFF, now=CUTOFF - timedelta(seconds=1)
        )
        is not None
    )


def test_prediction_contains_exact_candidate_identity_and_no_outcome() -> None:
    prediction = build_ttm_prediction(
        identity=_identity(),
        instrument="BTCUSDT",
        cutoff=CUTOFF,
        generated_at=CUTOFF - timedelta(seconds=1),
        context=_context(),
        result=TTMR2InferenceResult(
            forecast=tuple(Decimal("100") for _ in range(96)),
            latency_ms=Decimal("12.5"),
        ),
    )
    assert prediction.model == TTM_R2_MODEL
    assert prediction.checkpoint_hash == HASH
    assert prediction.runner_hash == HASH
    assert prediction.outcome_case_id is None
    assert prediction.input_snapshot_hash != HASH


def test_current_qualified_runner_contract_fails_closed_for_48_bars() -> None:
    identity = _identity(runner_context_bars=QUALIFIED_TTM_R2_RUNNER_CONTEXT_BARS)
    with pytest.raises(ValueError, match="context contract mismatch"):
        _request_for_inference(identity, _context())


def test_ttm_resume_manifest_rejects_frozen_identity_change() -> None:
    expected = {field: index for index, field in enumerate(("schema", "runner_hash"))}
    with pytest.raises(ValueError, match="resume identity mismatch"):
        validate_resume_manifest(
            {"schema": expected["schema"], "runner_hash": "changed"},
            {**expected, "source_root": "missing"},
        )


def test_worker_quarantines_current_runner_without_generating_predictions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "manifest.json").write_text(
        json.dumps(
            {
                "source_snapshot_hash": HASH,
                "provider_identity": "binance_spot_public_market_data",
                "credentials_loaded": False,
                "order_writes_attempted": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    admission = tmp_path / "admission.json"
    admission.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        TTMR2RuntimeIdentity,
        "from_admission",
        lambda _path, verify_files=True: _identity(
            runner_context_bars=QUALIFIED_TTM_R2_RUNNER_CONTEXT_BARS
        ),
    )
    result = run(
        admission_path=admission,
        source_root=source_root,
        run_root=tmp_path / "run",
        repository_root=Path("/mnt/c/projects/advisorai-v3-ttm-worker"),
        preregistration_sha256="b" * 64,
        phase3_gate_sha256="c" * 64,
        until=datetime.now(UTC) + timedelta(minutes=1),
    )

    assert result["state"] == "quarantined_context_contract_mismatch"
    assert result["prediction_count"] == 0
    failure = json.loads((tmp_path / "run" / "failure.json").read_text(encoding="utf-8"))
    assert failure["required_runner_context_bars"] == QUALIFIED_TTM_R2_RUNNER_CONTEXT_BARS
    assert failure["configured_v3core_context_bars"] == TTM_R2_CONTEXT_BARS
    assert not (tmp_path / "run" / "predictions.jsonl").exists()
