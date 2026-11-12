from __future__ import annotations

import json
import os
from datetime import timedelta

import psutil

from advisorai.phase0 import (
    ChronosStabilitySummary,
    append_chronos_cycle,
    make_chronos_cycle,
    summarize_chronos_stability,
    write_immutable_json,
)
from scripts import validate_chronos_runtime_stability as validator
from tests.phase0.test_chronos_stability import _config, _sample


def _write_status(path, state: str, *, alive: bool = False) -> None:
    process = psutil.Process(os.getpid()) if alive else None
    path.write_text(
        json.dumps(
            {
                "state": state,
                "process": {
                    "pid": process.pid if process else 99999999,
                    "process_create_time": process.create_time() if process else 0,
                    "command_identity": "a" * 64,
                },
                "credentials_loaded": False,
                "order_writes_attempted": False,
                "execution_authority_present": False,
            }
        ),
        encoding="utf-8",
    )


def _write_pass_run(tmp_path):
    config = _config(tmp_path)
    write_immutable_json(tmp_path / "config.json", config.model_dump(mode="json"))
    first = make_chronos_cycle(config, _sample(config.started_at), sequence=0)
    second = make_chronos_cycle(
        config,
        _sample(config.started_at + timedelta(hours=24)),
        sequence=1,
        previous_record_hash=first.record_hash,
    )
    append_chronos_cycle(tmp_path / "cycles.jsonl", first)
    append_chronos_cycle(tmp_path / "cycles.jsonl", second)
    summary = summarize_chronos_stability(config, (first, second))
    assert isinstance(summary, ChronosStabilitySummary)
    write_immutable_json(tmp_path / "summary.json", summary.model_dump(mode="json"))
    _write_status(tmp_path / "status.json", "passed")
    return config


def test_terminal_validator_accepts_only_complete_terminal_pass(monkeypatch, tmp_path):
    _write_pass_run(tmp_path)
    monkeypatch.setattr(validator, "_identity_issues", lambda _config: [])

    state, report_path, report_sha = validator.validate_run(
        run_directory=tmp_path,
        output_path=tmp_path / "terminal-validation.json",
    )

    assert state == "PASS"
    assert report_path.exists()
    assert len(report_sha) == 64
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["issues"] == []


def test_terminal_validator_rejects_failed_cycle_and_interruption(monkeypatch, tmp_path):
    config = _config(tmp_path)
    write_immutable_json(tmp_path / "config.json", config.model_dump(mode="json"))
    failed = make_chronos_cycle(
        config,
        _sample(config.started_at, failure_reason="worker_crash"),
        sequence=0,
    )
    append_chronos_cycle(tmp_path / "cycles.jsonl", failed)
    _write_status(tmp_path / "status.json", "interrupted")
    monkeypatch.setattr(validator, "_identity_issues", lambda _config: [])

    state, report_path, _ = validator.validate_run(run_directory=tmp_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert state == "FAIL"
    assert "failed_cycle_present" in report["issues"]
    assert "run_interrupted" in report["issues"]


def test_terminal_validator_reports_pending_before_boundary(monkeypatch, tmp_path):
    config = _config(tmp_path)
    write_immutable_json(tmp_path / "config.json", config.model_dump(mode="json"))
    cycle = make_chronos_cycle(
        config,
        _sample(config.started_at + timedelta(hours=1)),
        sequence=0,
    )
    append_chronos_cycle(tmp_path / "cycles.jsonl", cycle)
    _write_status(tmp_path / "status.json", "running", alive=True)
    monkeypatch.setattr(validator, "_identity_issues", lambda _config: [])

    state, report_path, _ = validator.validate_run(run_directory=tmp_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert state == "PENDING_STABILITY"
    assert "terminal_sample_not_reached" in report["issues"]


def test_terminal_validator_rejects_tampered_chain(monkeypatch, tmp_path):
    config = _config(tmp_path)
    write_immutable_json(tmp_path / "config.json", config.model_dump(mode="json"))
    cycle = make_chronos_cycle(
        config,
        _sample(config.started_at + timedelta(hours=23)),
        sequence=0,
    )
    append_chronos_cycle(tmp_path / "cycles.jsonl", cycle)
    raw = json.loads((tmp_path / "cycles.jsonl").read_text(encoding="utf-8"))
    raw["sample"]["rss_after_unload_mib"] = 999
    (tmp_path / "cycles.jsonl").write_text(json.dumps(raw) + "\n", encoding="utf-8")
    _write_status(tmp_path / "status.json", "failed")
    monkeypatch.setattr(validator, "_identity_issues", lambda _config: [])

    state, report_path, _ = validator.validate_run(run_directory=tmp_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert state == "FAIL"
    assert any(issue.startswith("cycle_chain_invalid:") for issue in report["issues"])
    assert "cycle_missing_or_empty" in report["issues"]


def test_terminal_validator_rejects_premature_terminal_state(monkeypatch, tmp_path):
    config = _config(tmp_path)
    write_immutable_json(tmp_path / "config.json", config.model_dump(mode="json"))
    cycle = make_chronos_cycle(
        config,
        _sample(config.started_at + timedelta(hours=23)),
        sequence=0,
    )
    append_chronos_cycle(tmp_path / "cycles.jsonl", cycle)
    _write_status(tmp_path / "status.json", "failed")
    monkeypatch.setattr(validator, "_identity_issues", lambda _config: [])

    state, report_path, _ = validator.validate_run(run_directory=tmp_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert state == "FAIL"
    assert "premature_terminal_state" in report["issues"]


def test_terminal_validator_rejects_mid_run_identity_change(monkeypatch, tmp_path):
    config = _config(tmp_path)
    write_immutable_json(tmp_path / "config.json", config.model_dump(mode="json"))
    changed = make_chronos_cycle(
        config,
        _sample(config.started_at, identity="d" * 64),
        sequence=0,
    )
    append_chronos_cycle(tmp_path / "cycles.jsonl", changed)
    _write_status(tmp_path / "status.json", "failed")
    monkeypatch.setattr(validator, "_identity_issues", lambda _config: [])

    state, report_path, _ = validator.validate_run(run_directory=tmp_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert state == "FAIL"
    assert "cycle_runtime_identity_mismatch" in report["issues"]
