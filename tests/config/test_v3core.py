from datetime import UTC, datetime
from pathlib import Path

import pytest

from advisorai.config import (
    load_agent_config,
    load_execution_config,
    load_model_config,
    load_resource_config,
    load_risk_config,
    load_source_registry_config,
    load_v3_core_config,
)

ROOT = Path(__file__).parents[2]


def test_v3_core_configuration_enforces_the_authoritative_scope():
    config = load_v3_core_config(ROOT / "configs" / "v3_core.yaml")
    assert config.universe == ("BTC", "ETH")
    assert config.execution == "paper_testnet_only"
    assert config.storage.active == "local_parquet_duckdb_sqlite"
    assert config.gpu_model_selection.choose_one == (
        "chronos-2-small",
        "kronos-mini",
        "kronos-small",
    )


def test_risk_yaml_converts_to_an_immutable_policy():
    config = load_risk_config(ROOT / "configs" / "risk" / "v3_core.yaml")
    policy = config.to_policy(
        effective_at=datetime(2026, 8, 4, 12, 0, tzinfo=UTC), approved_by="operator"
    )
    assert policy.policy_version == "risk-v3-core-v1"
    assert {limit.name for limit in policy.hard_limits} >= {
        "max_order_notional",
        "max_leverage",
    }


def test_risk_configuration_cannot_enable_ai_limit_relaxation(tmp_path):
    path = tmp_path / "risk.yaml"
    path.write_text(
        """
policy_version: risk-v1
hard_limits:
  max_order_notional: 100
stale_data_rejects: true
kill_switch: independent
ai_can_loosen_limits: true
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="AI cannot loosen"):
        load_risk_config(path)


def test_all_reviewed_v3_core_yaml_boundaries_are_typed():
    config_root = ROOT / "configs"
    agents = load_agent_config(config_root / "agents" / "v3_core.yaml")
    models = load_model_config(config_root / "models" / "v3_core.yaml")
    execution = load_execution_config(config_root / "execution" / "v3_core.yaml")
    sources = load_source_registry_config(config_root / "sources" / "v3_core.yaml")
    resources = load_resource_config(config_root / "resources" / "v3_core.yaml")
    assert "synthesizer" in agents.roles
    assert "naive" in models.baselines
    assert execution.canonical_engine == "nautilus_trader"
    assert any(source.grade.value == "execution_grade" for source in sources.sources)
    assert resources.gpu_global_leases == 1


def test_typed_config_rejects_whitespace_collisions_and_normalizes_execution_values(tmp_path):
    model_path = tmp_path / "models.yaml"
    model_path.write_text(
        """
baselines: [naive, ' naive ']
cpu_candidates: [ttm-r3, ttm-r2, tspulse]
gpu_candidates: [chronos-2-small, kronos-mini, kronos-small, tabpfn-ts]
gpu_residency: one_family_at_a_time
promotion_metric: calibrated_past_only_net_utility_or_risk_information
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unique"):
        load_model_config(model_path)
    execution = load_execution_config(ROOT / "configs" / "execution" / "v3_core.yaml")
    assert execution.order_policies == ("immediate", "passive_limit")
    assert execution.time_in_force == ("GTC", "IOC")


def test_risk_config_rejects_normalized_duplicate_limit_names(tmp_path):
    path = tmp_path / "risk.yaml"
    path.write_text(
        """
policy_version: risk-v1
hard_limits:
  max_order_notional: 100
  ' max_order_notional ': 200
stale_data_rejects: true
kill_switch: independent
ai_can_loosen_limits: false
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unique"):
        load_risk_config(path)
