from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from advisorai.phase0 import (
    CandidateStabilitySample,
    ModelStabilityConfig,
    append_cycle,
    make_cycle,
    summarize_stability,
    write_immutable_json,
)
from scripts.validate_model_stability import _role_report, validate


def _config() -> ModelStabilityConfig:
    return ModelStabilityConfig(
        run_id="validator-fixture",
        started_at=datetime(2026, 8, 10, tzinfo=UTC),
        duration_hours=24,
        interval_seconds=300,
        candidates=("ttm-r2", "finsentiment-deberta-v3", "finbert-minilm"),
        forecast_dataset_hash="a" * 64,
        sentiment_dataset_hash="b" * 64,
        benchmark_report_hash="c" * 64,
    )


def _samples() -> tuple[CandidateStabilitySample, ...]:
    return tuple(
        CandidateStabilitySample(
            candidate=name,
            status="measured",
            qualification_manifest_hash=(str(index) * 64),
            privacy_passed=True,
            resource_limit_passed=True,
            memory_released=True,
            current_rss_after_unload_mib=100 + index,
            peak_rss_mib=200 + index,
            peak_vram_mib=0,
        )
        for index, name in enumerate(
            ("ttm-r2", "finsentiment-deberta-v3", "finbert-minilm"), start=1
        )
    )


def _write_root(tmp_path: Path, *, terminal: bool) -> tuple[Path, Path]:
    config = _config()
    run = tmp_path / "run"
    run.mkdir()
    write_immutable_json(run / "config.json", config.model_dump(mode="json"))
    timestamp = config.started_at + (
        timedelta(hours=24, minutes=1) if terminal else timedelta(hours=1)
    )
    cycle = make_cycle(config, _samples(), sequence=0, sampled_at=timestamp)
    append_cycle(run / "cycles.jsonl", cycle)
    status = {
        "run_id": config.run_id,
        "state": "passed" if terminal else "running",
        "cycle_count": 1,
        "last_record_hash": cycle.record_hash,
        "last_sampled_at": cycle.sampled_at.isoformat(),
        "stability_24h_passed": terminal,
    }
    (run / "status.json").write_text(json.dumps(status), encoding="utf-8")
    if terminal:
        summary = summarize_stability(config, (cycle,))
        write_immutable_json(run / "summary.json", summary.model_dump(mode="json"))
    return run, tmp_path / "admissions"


def test_validator_keeps_preterminal_root_pending_without_mutating_it(tmp_path: Path):
    run, admissions = _write_root(tmp_path, terminal=False)
    before = (run / "cycles.jsonl").read_bytes()

    report = validate(run, admission_root=admissions)

    assert report["state"] == "PENDING_STABILITY"
    assert report["phase0_admission"] is False
    assert "terminal_sample_not_reached" in report["issues"]
    assert (run / "cycles.jsonl").read_bytes() == before


def test_validator_reports_terminal_failures_per_role(tmp_path: Path):
    run, admissions = _write_root(tmp_path, terminal=True)

    report = validate(run, admission_root=admissions)

    assert report["state"] == "FAIL"
    assert report["terminal_sample"] is True
    assert report["phase0_admission"] is False
    assert all(role["result"] == "REJECTED / QUARANTINED" for role in report["roles"])
    assert all(issue.startswith("admission_bundle_invalid:") for issue in report["issues"])


def test_validator_rejects_candidate_set_mismatch(tmp_path: Path):
    run, admissions = _write_root(tmp_path, terminal=False)
    config = json.loads((run / "config.json").read_text())
    config["candidates"] = ["ttm-r2"]
    (run / "config.json").write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ValueError, match="candidates"):
        validate(run, admission_root=admissions)


def test_role_is_not_qualified_when_one_cycle_sample_failed():
    config = _config()
    samples = list(_samples())
    samples[0] = samples[0].model_copy(
        update={
            "status": "failed",
            "privacy_passed": False,
            "failure_reason": "runtime_failure",
        }
    )
    cycle = make_cycle(
        config,
        tuple(samples),
        sequence=0,
        sampled_at=config.started_at + timedelta(hours=24, minutes=1),
    )
    summary = summarize_stability(config, (cycle,))

    role = _role_report(
        "ttm-r2",
        (cycle,),
        summary,
        {"admission_sha256": "a" * 64},
        terminal=True,
    )

    assert role["all_samples_passed"] is False
    assert role["result"] == "REJECTED / QUARANTINED"
