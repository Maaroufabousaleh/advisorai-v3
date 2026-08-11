from __future__ import annotations

import hashlib
import json
import os
from argparse import Namespace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psutil

from scripts.monitor_phase3_process_resources import (
    _command_hash,
    _process_sample,
    _root_size,
    monitor,
)


def test_root_size_counts_files_and_excludes_directories(tmp_path: Path):
    root = tmp_path / "qualification"
    root.mkdir()
    (root / "one.jsonl").write_bytes(b"abc")
    nested = root / "nested"
    nested.mkdir()
    (nested / "two.jsonl").write_bytes(b"12345")

    assert _root_size(root) == (2, 8, ())


def test_process_sample_records_metrics_and_hash_chain(tmp_path: Path):
    process = psutil.Process(os.getpid())
    record = _process_sample(
        process,
        pid=os.getpid(),
        expected_start_time=process.create_time(),
        expected_command_sha256=_command_hash(process),
        target_root=tmp_path,
        previous_record_hash=None,
    )

    assert record.process_status == "running"
    assert record.rss_mib is not None
    assert record.file_descriptor_count is not None
    assert record.record_hash != "0" * 64


def test_process_sample_fails_closed_on_identity_mismatch(tmp_path: Path):
    process = psutil.Process(os.getpid())
    record = _process_sample(
        process,
        pid=os.getpid(),
        expected_start_time=process.create_time() + 1,
        expected_command_sha256=_command_hash(process),
        target_root=tmp_path,
        previous_record_hash=None,
    )

    assert record.process_status == "identity_mismatch"
    assert "process_identity_mismatch" in record.resource_errors
    assert record.rss_mib is None


def test_monitor_writes_separate_append_only_evidence(tmp_path: Path):
    process = psutil.Process(os.getpid())
    evidence = tmp_path / "monitor"
    target = tmp_path / "target"
    target.mkdir()
    (target / "raw.jsonl").write_text("sanitized\n", encoding="utf-8")
    args = Namespace(
        pid=os.getpid(),
        expected_start_time=process.create_time(),
        expected_command_sha256=_command_hash(process),
        target_root=target,
        evidence_dir=evidence,
        until=datetime.now(UTC) + timedelta(seconds=1.05),
        interval_seconds=1.0,
    )

    assert monitor(args) == 0
    config = json.loads((evidence / "config.json").read_text(encoding="utf-8"))
    status = json.loads((evidence / "status.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (evidence / "observations.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert config["credentials_loaded"] is False
    assert config["order_writes_attempted"] is False
    assert status["state"] == "deadline_reached"
    assert rows
    assert rows[0]["previous_record_hash"] is None
    assert all(row["record_hash"] for row in rows)
    previous = None
    for row in rows:
        assert row["previous_record_hash"] == previous
        unsigned = {key: value for key, value in row.items() if key != "record_hash"}
        expected = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert row["record_hash"] == expected
        previous = row["record_hash"]
    assert (
        hashlib.sha256((evidence / "summary.json").read_bytes()).hexdigest()
        == status["summary_sha256"]
    )
