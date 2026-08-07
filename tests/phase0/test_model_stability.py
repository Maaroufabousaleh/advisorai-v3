from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from advisorai.phase0 import (
    CandidateStabilitySample,
    ModelStabilityConfig,
    append_cycle,
    make_cycle,
    read_cycles,
    summarize_stability,
)


def _config() -> ModelStabilityConfig:
    return ModelStabilityConfig(
        run_id="fixture-stability",
        started_at=datetime(2026, 8, 7, tzinfo=UTC),
        duration_hours=24,
        interval_seconds=300,
        candidates=("ttm-r2", "finbert-minilm"),
        forecast_dataset_hash="a" * 64,
        sentiment_dataset_hash="b" * 64,
        benchmark_report_hash="c" * 64,
    )


def _sample(candidate: str, *, rss: float = 100) -> CandidateStabilitySample:
    return CandidateStabilitySample(
        candidate=candidate,
        status="measured",
        qualification_manifest_hash=("1" if candidate == "ttm-r2" else "2") * 64,
        privacy_passed=True,
        resource_limit_passed=True,
        memory_released=True,
        current_rss_after_unload_mib=rss,
        peak_rss_mib=rss + 100,
        peak_vram_mib=0,
    )


def test_stability_log_is_append_only_and_hash_chained(tmp_path):
    config = _config()
    path = tmp_path / "cycles.jsonl"
    first = make_cycle(
        config,
        (_sample("ttm-r2"), _sample("finbert-minilm")),
        sequence=0,
        sampled_at=config.started_at + timedelta(minutes=1),
    )
    append_cycle(path, first)
    second = make_cycle(
        config,
        (_sample("ttm-r2", rss=101), _sample("finbert-minilm", rss=101)),
        sequence=1,
        sampled_at=config.started_at + timedelta(hours=24, minutes=1),
        previous_record_hash=first.record_hash,
    )
    append_cycle(path, second)

    assert read_cycles(path) == (first, second)
    with pytest.raises(ValueError, match="append-only"):
        append_cycle(path, second)

    out_of_order = make_cycle(
        config,
        (_sample("ttm-r2"), _sample("finbert-minilm")),
        sequence=2,
        sampled_at=second.sampled_at,
        previous_record_hash=second.record_hash,
    )
    with pytest.raises(ValueError, match="time order"):
        append_cycle(path, out_of_order)

    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["samples"][0]["current_rss_after_unload_mib"] = 999
    lines[0] = json.dumps(tampered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="record hash"):
        read_cycles(path)


def test_stability_summary_cannot_pass_before_twenty_four_hours():
    config = _config()
    short = make_cycle(
        config,
        (_sample("ttm-r2"), _sample("finbert-minilm")),
        sequence=0,
        sampled_at=config.started_at + timedelta(hours=1),
    )
    summary = summarize_stability(config, (short,))

    assert summary.status == "short_smoke_complete"
    assert summary.all_cycles_passed
    assert not summary.stability_24h_passed
    assert all(window is None for window in summary.candidate_windows.values())


def test_complete_stability_uses_existing_window_contract():
    config = _config()
    first = make_cycle(
        config,
        (_sample("ttm-r2"), _sample("finbert-minilm")),
        sequence=0,
        sampled_at=config.started_at,
    )
    second = make_cycle(
        config,
        (_sample("ttm-r2", rss=110), _sample("finbert-minilm", rss=105)),
        sequence=1,
        sampled_at=config.started_at + timedelta(hours=24),
        previous_record_hash=first.record_hash,
    )
    summary = summarize_stability(config, (first, second))

    assert summary.status == "passed"
    assert summary.stability_24h_passed
    assert all(window is not None and window.passed for window in summary.candidate_windows.values())


def test_failed_sample_requires_reason_and_blocks_stability():
    with pytest.raises(ValidationError, match="sanitized reason"):
        CandidateStabilitySample(
            candidate="ttm-r2",
            status="failed",
            qualification_manifest_hash="a" * 64,
            privacy_passed=False,
            resource_limit_passed=False,
            memory_released=False,
            current_rss_after_unload_mib=0,
            peak_rss_mib=0,
            peak_vram_mib=0,
        )
