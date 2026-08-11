from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.validate_phase3_public_data_qualification import _load_chain


def _write_chain(path: Path) -> str:
    previous = None
    records = []
    for value in ("one", "two"):
        unsigned = {"previous_record_hash": previous, "value": value}
        record = {
            **unsigned,
            "record_hash": hashlib.sha256(
                json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
        records.append(record)
        previous = record["record_hash"]
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    return previous


def test_load_chain_validates_hashes_and_predecessors(tmp_path: Path):
    path = tmp_path / "records.jsonl"
    last_hash = _write_chain(path)

    records, actual_last_hash = _load_chain(path)

    assert len(records) == 2
    assert actual_last_hash == last_hash


def test_load_chain_rejects_tampering(tmp_path: Path):
    path = tmp_path / "records.jsonl"
    _write_chain(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["value"] = "changed"
    lines[1] = json.dumps(tampered, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid record hash"):
        _load_chain(path)
