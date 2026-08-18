from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.materialize_phase4_v3core_forward_input import MaterializationRefused, materialize


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
