from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from advisorai.phase0 import build_walk_forward_cases
from scripts.prepare_phase4_real_utility_input import (
    DEFAULT_FORECAST_CANDIDATES,
    REQUIRED_SYMBOLS,
    SOURCE_ENDPOINT,
    SOURCE_ID,
    Phase4InputRefused,
    _load_snapshot,
    _observations,
    _parse_candidate_admission_roots,
)

SNAPSHOT = Path(
    "/home/maaro/.cache/advisorai-v3/benchmark-data/public-daily-0f84a34fb0537ecb/"
    "forecast-snapshot.json"
)
MANIFEST = Path(
    "artifacts/phase0/model-runtime-qualification/benchmark-data/"
    "public-daily-0f84a34fb0537ecb/forecast-snapshot-manifest.json"
)


def test_real_input_uses_frozen_binance_btc_eth_point_in_time_observations():
    snapshot = _load_snapshot(SNAPSHOT, MANIFEST)
    cases = tuple(
        item
        for item in build_walk_forward_cases(snapshot, cases_per_series=16)
        if item.instrument in REQUIRED_SYMBOLS
    )
    observations = _observations(cases, snapshot.content_hash, Decimal("2"), Decimal("2"))

    assert len(observations) == 32
    assert {item.instrument for item in observations} == set(REQUIRED_SYMBOLS)
    assert all(item.phase3_admitted for item in observations)
    assert all(item.source_id == SOURCE_ID for item in observations)
    assert all(item.endpoint == SOURCE_ENDPOINT for item in observations)
    assert {item.source_snapshot_hash for item in observations} == {snapshot.content_hash}


def test_real_input_rejects_manifest_hash_mismatch(tmp_path: Path):
    snapshot = _load_snapshot(SNAPSHOT, MANIFEST)
    payload = json.loads(MANIFEST.read_text())
    payload["snapshot"]["content_hash"] = "0" * 64
    manifest = tmp_path / "bad-manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Phase4InputRefused, match="content hash"):
        _load_snapshot(SNAPSHOT, manifest)

    assert snapshot.content_hash != "0" * 64


def test_real_input_default_candidate_preserves_ttm_r2_control():
    assert DEFAULT_FORECAST_CANDIDATES == ("ttm-r2",)


def test_real_input_candidate_admission_root_parser_is_explicit():
    assert _parse_candidate_admission_roots(["ttm-r3=/tmp/admission"]) == {
        "ttm-r3": Path("/tmp/admission")
    }
    with pytest.raises(Phase4InputRefused, match="NAME=PATH"):
        _parse_candidate_admission_roots(["ttm-r3"])
    with pytest.raises(Phase4InputRefused, match="duplicate"):
        _parse_candidate_admission_roots(["ttm-r3=/tmp/a", "ttm-r3=/tmp/b"])
