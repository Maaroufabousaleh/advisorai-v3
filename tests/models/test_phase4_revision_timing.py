from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from advisorai.collectors.sources import HttpResponse
from advisorai.phase4 import ForwardRawSpool
from scripts.analyze_phase4_v3core_revision_timing import _validate_run_status, analyze
from tests.models.test_phase4_v3core_integrity import ENDPOINT, START, _row


def _response(row: list[object], received_at):
    import json

    return HttpResponse(
        status_code=200,
        body=json.dumps([row]).encode(),
        fetched_at=received_at,
        url=ENDPOINT,
    )


def test_revision_timing_reports_statistics_without_selecting_grace(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.jsonl"
    raw = ForwardRawSpool(raw_path)
    raw.append(
        _response(_row(), START + timedelta(minutes=6)),
        symbol="BTCUSDT",
        request_url=ENDPOINT,
    )
    raw.append(
        _response(_row(close="101"), START + timedelta(minutes=6, seconds=1)),
        symbol="BTCUSDT",
        request_url=ENDPOINT,
    )
    raw.append(
        _response(_row(close="101"), START + timedelta(minutes=6, seconds=2)),
        symbol="BTCUSDT",
        request_url=ENDPOINT,
    )
    before = raw_path.read_bytes()
    report = analyze(
        raw_responses_path=raw_path,
        terminal_observed_at=START + timedelta(minutes=10),
    )
    interval = report["intervals"][0]
    assert interval["first_post_close_receipt_lag_seconds"] == 60.0
    assert interval["first_revision_lag_seconds"] == 61.0
    assert interval["last_revision_lag_seconds"] == 62.0
    assert interval["first_repeated_version_lag_seconds"] == 62.0
    assert interval["second_terminal_confirmation_lag_seconds"] == 62.0
    assert report["selection_status"] == "STATISTICS_ONLY_NO_GRACE_SELECTED"
    assert report["terminal_evidence_eligible"] is True
    assert report["credentials_loaded"] is False
    assert report["order_writes_attempted"] is False
    assert raw_path.read_bytes() == before


def test_revision_timing_rejects_terminal_boundary_before_receipt(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.jsonl"
    raw = ForwardRawSpool(raw_path)
    raw.append(
        _response(_row(), START + timedelta(minutes=6)),
        symbol="BTCUSDT",
        request_url=ENDPOINT,
    )
    with pytest.raises(ValueError, match="terminal boundary"):
        analyze(
            raw_responses_path=raw_path,
            terminal_observed_at=START + timedelta(minutes=5, seconds=59),
        )


def test_revision_timing_diagnostic_is_explicitly_unsealed(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.jsonl"
    raw = ForwardRawSpool(raw_path)
    raw.append(
        _response(_row(), START + timedelta(minutes=6)),
        symbol="BTCUSDT",
        request_url=ENDPOINT,
    )
    report = analyze(
        raw_responses_path=raw_path,
        terminal_observed_at=START + timedelta(minutes=10),
        terminal_evidence_eligible=False,
    )
    assert report["terminal_evidence_eligible"] is False


def test_revision_timing_status_requires_supported_sealed_state() -> None:
    with pytest.raises(ValueError, match="running root"):
        _validate_run_status({"state": "running"}, allow_unsealed=False)
    assert _validate_run_status({"state": "running"}, allow_unsealed=True) is False
    with pytest.raises(ValueError, match="terminal state"):
        _validate_run_status({"state": "operator_stopped"}, allow_unsealed=False)
    with pytest.raises(ValueError, match="frozen minimum"):
        _validate_run_status(
            {"state": "target_reached", "minimum_reached": False}, allow_unsealed=False
        )
