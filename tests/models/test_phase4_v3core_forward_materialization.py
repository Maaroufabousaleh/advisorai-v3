from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from advisorai.collectors.sources import HttpResponse
from advisorai.phase4 import (
    ForwardNormalizedBarSpool,
    ForwardRawSpool,
    audit_forward_root,
    build_exclusion_overlay,
    parse_binance_klines,
)
from scripts.materialize_phase4_v3core_forward_input import MaterializationRefused, materialize

HASH = "a" * 64
START = datetime(2026, 8, 18, 0, tzinfo=UTC)
ENDPOINT = "https://data-api.binance.vision/api/v3/klines"


def _row() -> list[object]:
    end = START + timedelta(minutes=5)
    return [
        int(START.timestamp() * 1000),
        "99",
        "101",
        "98",
        "100",
        "1",
        int(end.timestamp() * 1000) - 1,
        "100",
        1,
        "1",
        "10",
    ]


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_materializer_refuses_incomplete_root(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    _write(run / "manifest.json", {"state": "running"})
    _write(run / "status.json", {"state": "running", "minimum_reached": False})
    prereg = tmp_path / "prereg.json"
    _write(prereg, {"measurement_status": "PENDING_FRESH_PIT_DATA"})

    with pytest.raises(MaterializationRefused, match="sample minimum"):
        materialize(
            run_directory=run,
            preregistration=prereg,
            output_root=tmp_path / "out",
            phase3_gate_sha256="a" * 64,
        )


def test_materializer_rejects_network_or_write_flags(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    _write(
        run / "manifest.json",
        {
            "state": "target_reached",
            "minimum_reached": True,
            "credentials_loaded": True,
            "order_writes_attempted": False,
        },
    )
    _write(run / "status.json", {"state": "target_reached", "minimum_reached": True})
    prereg = tmp_path / "prereg.json"
    _write(prereg, {"measurement_status": "PENDING_FRESH_PIT_DATA", "network_calls": 0})
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    manifest["preregistration_sha256"] = hashlib.sha256(prereg.read_bytes()).hexdigest()
    manifest["phase3_gate_record_sha256"] = "a" * 64
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(MaterializationRefused, match="credential-free"):
        materialize(
            run_directory=run,
            preregistration=prereg,
            output_root=tmp_path / "out",
            phase3_gate_sha256="a" * 64,
        )


def test_integrity_report_and_overlay_must_be_paired(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    _write(
        run / "manifest.json",
        {
            "preregistration_sha256": "a" * 64,
            "phase3_gate_record_sha256": "b" * 64,
            "credentials_loaded": False,
            "order_writes_attempted": False,
        },
    )
    _write(run / "status.json", {"state": "target_reached", "minimum_reached": True})
    prereg = tmp_path / "prereg.json"
    _write(
        prereg,
        {
            "measurement_status": "PENDING_FRESH_PIT_DATA",
            "network_calls": 0,
        },
    )
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    manifest["preregistration_sha256"] = hashlib.sha256(prereg.read_bytes()).hexdigest()
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    report = tmp_path / "integrity.json"
    _write(report, {})

    with pytest.raises(MaterializationRefused, match="must be supplied together"):
        materialize(
            run_directory=run,
            preregistration=prereg,
            output_root=tmp_path / "out",
            phase3_gate_sha256="b" * 64,
            integrity_report_path=report,
        )


def test_materializer_refuses_existing_output_root(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    _write(run / "manifest.json", {})
    _write(run / "status.json", {})
    prereg = tmp_path / "prereg.json"
    _write(prereg, {})
    output = tmp_path / "out"
    output.mkdir()

    with pytest.raises(MaterializationRefused, match="output root must be new"):
        materialize(
            run_directory=run,
            preregistration=prereg,
            output_root=output,
            phase3_gate_sha256="a" * 64,
        )


def test_materializer_binds_prediction_manifest_hashes(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    raw_path = run / "raw-responses.jsonl"
    normalized_path = run / "normalized-bars.jsonl"
    raw = ForwardRawSpool(raw_path)
    row = _row()
    raw.append(
        HttpResponse(
            status_code=200,
            body=json.dumps([row]).encode(),
            fetched_at=START + timedelta(minutes=6),
            url=ENDPOINT,
        ),
        symbol="BTCUSDT",
        request_url=ENDPOINT,
    )
    bar = parse_binance_klines(
        json.dumps([row]).encode(),
        symbol="BTCUSDT",
        collected_at=START + timedelta(minutes=6),
        source_snapshot_hash=HASH,
    )[0]
    ForwardNormalizedBarSpool(normalized_path).append(bar)
    cases_path = run / "completed-cases.jsonl"
    cases_path.write_text("", encoding="utf-8")
    prereg = tmp_path / "prereg.json"
    _write(
        prereg,
        {
            "measurement_status": "PENDING_FRESH_PIT_DATA",
            "network_calls": 0,
            "plan": {"minimum_cases_per_symbol": 0, "minimum_total_cases": 0},
        },
    )
    manifest = {
        "preregistration_sha256": hashlib.sha256(prereg.read_bytes()).hexdigest(),
        "phase3_gate_record_sha256": "b" * 64,
        "source_snapshot_hash": HASH,
        "provider_identity": "binance_spot_public_market_data",
        "endpoint": ENDPOINT,
        "evidence_class": "forward_pit_admission",
        "interval": "5m",
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "market_data_only": True,
        "credentials_loaded": False,
        "order_writes_attempted": False,
    }
    _write(run / "manifest.json", manifest)
    _write(run / "status.json", {"state": "target_reached", "minimum_reached": True})
    _write(run / "config.json", {"schema": "test"})
    prediction_manifest = tmp_path / "prediction-manifest.json"
    _write(prediction_manifest, {"models": []})
    report = audit_forward_root(
        raw_path,
        normalized_path,
        completed_cases_path=cases_path,
        prediction_manifest_paths=(prediction_manifest,),
        terminal_observed_at=START + timedelta(minutes=10),
        minimum_cases_per_symbol=1,
        source_manifest_path=run / "manifest.json",
        source_status_path=run / "status.json",
        source_config_path=run / "config.json",
        terminal_evidence_eligible=True,
        auditor_repository_commit="a" * 40,
    )
    report = report.model_copy(
        update={
            "sample_minimum_met": True,
            "integrity_ready": True,
            "admission_evidence_ready": True,
            "admission_minimum_met": True,
        }
    )
    report_path = tmp_path / "integrity.json"
    report_bytes = json.dumps(report.model_dump(mode="json"), sort_keys=True).encode()
    report_path.write_bytes(report_bytes)
    overlay = build_exclusion_overlay(
        report,
        report_sha256=hashlib.sha256(report_bytes).hexdigest(),
    )
    overlay_path = tmp_path / "overlay.json"
    overlay_path.write_text(json.dumps(overlay.model_dump(mode="json")), encoding="utf-8")

    with pytest.raises(MaterializationRefused, match="prediction manifest hash mismatch"):
        materialize(
            run_directory=run,
            preregistration=prereg,
            output_root=tmp_path / "out",
            phase3_gate_sha256="b" * 64,
            integrity_report_path=report_path,
            exclusion_overlay_path=overlay_path,
        )
