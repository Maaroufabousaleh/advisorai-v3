from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest

from scripts.regenerate_phase4_v3core_baselines import (
    _git_head,
    _parse_materialized_at,
    _validated_repository_commit,
)


def test_causal_baseline_cli_requires_timezone_aware_materialization_time() -> None:
    with pytest.raises(ValueError, match="timezone"):
        _parse_materialized_at("2026-08-19T12:00:00")

    parsed = _parse_materialized_at("2026-08-19T12:00:00Z")
    assert parsed.tzinfo is UTC


def test_causal_baseline_cli_binds_claimed_commit_to_repository_head() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    actual = _git_head(repository_root)
    assert _validated_repository_commit(repository_root, actual) == actual
    assert len(actual) == 40

    with pytest.raises(ValueError, match="does not match"):
        _validated_repository_commit(repository_root, "0" * 40)
