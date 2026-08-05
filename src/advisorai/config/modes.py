"""Resource mode configuration loaded from reviewed YAML files."""

from __future__ import annotations

from enum import StrEnum
from math import isfinite
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class MissionMode(StrEnum):
    TRADE_FAST = "trade_fast"
    STANDARD = "standard"
    DEEP = "deep"
    BUILDER = "builder"
    RECOVERY = "recovery"


class ModeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: MissionMode
    memory_ceiling_gib: float = Field(gt=0)
    minimum_headroom_gib: float = Field(ge=1.5, le=2.0)
    remote_llm_limit: int = Field(ge=0)
    hermes_limit: int = Field(ge=0)
    browser_limit: int = Field(ge=0)
    heavy_duckdb_limit: int = Field(ge=0)
    cpu_worker_limit: int = Field(ge=0)
    gpu_jobs_limit: int = Field(ge=0, le=1)
    load_shedding: tuple[str, ...]

    @model_validator(mode="after")
    def validate_limits(self) -> ModeConfig:
        if not isfinite(self.memory_ceiling_gib) or not isfinite(self.minimum_headroom_gib):
            raise ValueError("mode memory limits must be finite")
        if self.mode is MissionMode.TRADE_FAST and any(
            (self.remote_llm_limit, self.hermes_limit, self.browser_limit)
        ):
            raise ValueError("Trade/Fast may not admit remote LLM, Hermes, or browser work")
        memory_bounds = {
            MissionMode.TRADE_FAST: (6.5, 6.5),
            MissionMode.STANDARD: (8.0, 8.5),
            MissionMode.DEEP: (8.5, 9.0),
            MissionMode.BUILDER: (8.5, 9.0),
            MissionMode.RECOVERY: (6.5, 8.0),
        }[self.mode]
        lower, upper = memory_bounds
        if not lower <= self.memory_ceiling_gib <= upper:
            raise ValueError(
                f"{self.mode.value} memory ceiling must remain within {lower:.1f}-{upper:.1f} GiB"
            )
        remote_bounds = {
            MissionMode.TRADE_FAST: 0,
            MissionMode.STANDARD: 2,
            MissionMode.DEEP: 4,
            MissionMode.BUILDER: 0,
            MissionMode.RECOVERY: 0,
        }
        if self.remote_llm_limit > remote_bounds[self.mode]:
            raise ValueError("remote LLM concurrency exceeds the mode envelope")
        if self.hermes_limit > (
            1 if self.mode in {MissionMode.DEEP, MissionMode.BUILDER, MissionMode.RECOVERY} else 0
        ):
            raise ValueError("Hermes concurrency exceeds the mode envelope")
        if self.browser_limit:
            raise ValueError("browser work uses a separate bounded browser profile")
        if self.browser_limit > 1 or self.heavy_duckdb_limit > 2 or self.cpu_worker_limit > 2:
            raise ValueError("initial global concurrency caps were exceeded")
        prescribed = (
            "archive",
            "browser",
            "hermes_skill_foundry",
            "low_priority_research",
            "training_backtests",
            "optional_model_challengers",
            "noncritical_collectors",
        )
        if self.load_shedding != prescribed:
            raise ValueError("mode load shedding must preserve the prescribed authority order")
        if len(self.load_shedding) != len(set(self.load_shedding)):
            raise ValueError("mode load-shedding entries must be unique")
        return self


def load_mode_configs(config_dir: Path) -> dict[MissionMode, ModeConfig]:
    result: dict[MissionMode, ModeConfig] = {}
    for path in sorted(config_dir.glob("*.yaml")):
        with path.open(encoding="utf-8") as handle:
            config = ModeConfig.model_validate(yaml.safe_load(handle))
        if config.mode in result:
            raise ValueError(f"duplicate mode config: {config.mode}")
        result[config.mode] = config
    required = set(MissionMode)
    missing = required.difference(result)
    if missing:
        raise ValueError(f"missing mode configs: {sorted(item.value for item in missing)}")
    return result
