from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from advisorai.soak import (
    DurablePaperSoakRunner,
    FailureScenario,
    SoakRunConfig,
    SoakRunSummary,
    SoakSample,
    read_soak_records,
)


def _config() -> SoakRunConfig:
    return SoakRunConfig(
        run_id="paper-soak-fixture",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        code_sha256="a" * 64,
        configuration_sha256="b" * 64,
        policy_sha256="c" * 64,
        model_roster_sha256="d" * 64,
        source_roster_sha256="e" * 64,
        venue_identity="binance_spot_testnet",
        command="python -m operator.paper_soak",
    )


def _sample(at: datetime) -> SoakSample:
    return SoakSample(
        at=at,
        decision_count=2,
        trade_count=1,
        net_utility_after_costs=Decimal("0.1"),
        resource_stable=True,
        reconciliation_clean=True,
        safety_clean=True,
        adverse_scenarios=(FailureScenario.VENUE_OUTAGE,),
    )


def test_durable_runner_resumes_hash_chain_and_requires_terminal_sample(tmp_path):
    config = _config()
    first_times = iter(
        (
            config.started_at + timedelta(hours=1),
            config.started_at + timedelta(hours=2),
        )
    )
    root = tmp_path / "soak"
    first = DurablePaperSoakRunner(
        config=config,
        evidence_root=root,
        sample_factory=_sample,
    )
    short = first.run(clock=lambda: next(first_times), sleep=lambda _seconds: None, max_samples=2)

    assert short.status == "short_smoke_complete"
    assert short.terminal_sample_at is None
    records = read_soak_records(root / "samples.jsonl")
    assert len(records) == 2
    assert records[1].previous_record_hash == records[0].record_hash
    assert json.loads((root / "status.json").read_text())["state"] == "short_smoke_complete"

    terminal_time = config.target_end + timedelta(minutes=1)
    resumed = DurablePaperSoakRunner(
        config=config,
        evidence_root=root,
        sample_factory=_sample,
    ).run(clock=lambda: terminal_time, sleep=lambda _seconds: None)

    assert resumed.status == "completed_60_calendar_days"
    assert resumed.elapsed_hours >= 60 * 24
    assert resumed.terminal_sample_at == terminal_time
    assert resumed.phase7_admission is False
    assert len(read_soak_records(root / "samples.jsonl")) == 3
    terminal_status = json.loads((root / "status.json").read_text())
    assert len(terminal_status["summary_sha256"]) == 64


def test_durable_runner_rejects_tampered_record_and_config(tmp_path):
    config = _config()
    root = tmp_path / "soak"
    DurablePaperSoakRunner(config=config, evidence_root=root, sample_factory=_sample).run(
        clock=lambda: config.started_at + timedelta(hours=1),
        sleep=lambda _seconds: None,
        max_samples=1,
    )

    lines = (root / "samples.jsonl").read_text().splitlines()
    tampered = json.loads(lines[0])
    tampered["sample"]["decision_count"] = 99
    (root / "samples.jsonl").write_text(json.dumps(tampered) + "\n")
    with pytest.raises(ValidationError, match="record hash"):
        read_soak_records(root / "samples.jsonl")

    with pytest.raises(FileExistsError, match="immutable evidence differs"):
        DurablePaperSoakRunner(
            config=config.model_copy(update={"code_sha256": "f" * 64}),
            evidence_root=root,
            sample_factory=_sample,
        )


def test_durable_runner_preserves_sanitized_failure_status(tmp_path):
    config = _config()

    def fail(_at: datetime) -> SoakSample:
        raise TimeoutError("provider details must not be persisted")

    root = tmp_path / "failed-soak"
    with pytest.raises(TimeoutError):
        DurablePaperSoakRunner(config=config, evidence_root=root, sample_factory=fail).run(
            clock=lambda: config.started_at + timedelta(hours=1),
            sleep=lambda _seconds: None,
        )

    status = json.loads((root / "status.json").read_text())
    assert status["state"] == "failed"
    assert status["failure_class"] == "TimeoutError"
    assert "provider details" not in (root / "status.json").read_text()
    assert not (root / "summary.json").exists()


def test_soak_summary_cannot_open_phase7_admission():
    with pytest.raises(ValidationError, match="cannot open Phase-7 admission"):
        SoakRunSummary(
            run_id="fixture",
            config_hash="a" * 64,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            ended_at=datetime(2026, 3, 2, tzinfo=UTC),
            elapsed_hours=60 * 24,
            record_count=1,
            terminal_sample_at=datetime(2026, 3, 2, tzinfo=UTC),
            status="completed_60_calendar_days",
            phase7_admission=True,
        )
