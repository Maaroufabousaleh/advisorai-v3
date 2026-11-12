"""Fail-closed governance for the TimesFM 3 research challenger.

This module deliberately lives below ``advisorai.research``.  The core runtime
does not import it and the governance record never grants execution authority.
The checkpoint restriction is recorded as an engineering control; it is not a
legal interpretation of the upstream license.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_GOVERNANCE_PATH = REPOSITORY_ROOT / "configs/research/timesfm3.yaml"
TIMESFM3_CHECKPOINT = "google/timesfm-3.0-pytorch"
TIMESFM3_LICENSE_CLASS = "research_only_nonproduction"


class TimesFM3ActivationScope(StrEnum):
    """Execution contexts in which a checkpoint activation may be attempted."""

    RESEARCH = "research"
    PRODUCTION = "production"
    LIVE = "live"
    EXECUTION = "execution"
    COMMERCIAL_DECISION_SERVICE = "commercial_decision_service"
    CORE_TRADING_HOT_PATH = "core_trading_hot_path"


class TimesFM3ActivationError(PermissionError):
    """Raised whenever the research-only checkpoint crosses its boundary."""


class TimesFM3Governance(BaseModel):
    """Machine-readable checkpoint policy loaded from the reviewed YAML file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: Literal[TIMESFM3_CHECKPOINT] = TIMESFM3_CHECKPOINT
    role: Literal["research_challenger"] = "research_challenger"
    priority_class: Literal["PRIORITY_CHALLENGER"] = "PRIORITY_CHALLENGER"
    deployment_class: Literal["RESEARCH_ONLY"] = "RESEARCH_ONLY"
    production_class: Literal["NON_PRODUCTION"] = "NON_PRODUCTION"
    weights_class: Literal["NON_COMMERCIAL_WEIGHTS"] = "NON_COMMERCIAL_WEIGHTS"
    commercial_use: Literal[False] = False
    production_use: Literal[False] = False
    execution_use: Literal[False] = False
    automatic_promotion: Literal[False] = False
    license_class: Literal[TIMESFM3_LICENSE_CLASS] = TIMESFM3_LICENSE_CLASS
    allowed_activation_scope: Literal["research_only"] = "research_only"
    order_authority: Literal[False] = False
    oms_authority: Literal[False] = False
    riskkernel_authority: Literal[False] = False
    credential_access: Literal[False] = False
    self_promotion: Literal[False] = False
    model_download_at_startup: Literal[False] = False
    permanent_gpu_residency: Literal[False] = False
    one_gpu_family_at_a_time: Literal[True] = True


class TimesFM3GovernanceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["advisorai.research.timesfm3-governance.v1"]
    model: TimesFM3Governance
    runtime: dict[str, object] = Field(default_factory=dict)


def load_timesfm3_governance(path: Path = DEFAULT_GOVERNANCE_PATH) -> TimesFM3Governance:
    """Load and validate the immutable governance record without network access."""

    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TimesFM3ActivationError(f"cannot read TimesFM3 governance: {path}") from exc
    if not isinstance(document, dict):
        raise TimesFM3ActivationError("TimesFM3 governance document must be a mapping")
    try:
        parsed = TimesFM3GovernanceDocument.model_validate(document)
    except Exception as exc:  # Pydantic gives the useful field-level failure.
        raise TimesFM3ActivationError("invalid TimesFM3 governance document") from exc
    return parsed.model


def assert_timesfm3_activation_allowed(
    scope: TimesFM3ActivationScope | str,
    *,
    governance: TimesFM3Governance | None = None,
) -> None:
    """Allow only an explicitly named research scope and reject everything else."""

    policy = governance or load_timesfm3_governance()
    raw_scope = scope.value if isinstance(scope, TimesFM3ActivationScope) else str(scope)
    try:
        normalized = TimesFM3ActivationScope(raw_scope.strip().lower())
    except ValueError as exc:
        raise TimesFM3ActivationError(f"unknown TimesFM3 activation scope: {scope!r}") from exc
    if normalized is not TimesFM3ActivationScope.RESEARCH:
        raise TimesFM3ActivationError(
            f"TimesFM3 is {policy.license_class} and cannot activate in {normalized.value}"
        )
    if policy.role != "research_challenger" or policy.production_use or policy.execution_use:
        raise TimesFM3ActivationError("TimesFM3 governance does not permit research activation")


def assert_timesfm3_cannot_self_promote(governance: TimesFM3Governance | None = None) -> None:
    """Keep promotion as an external evidence-council/operator decision."""

    policy = governance or load_timesfm3_governance()
    if policy.automatic_promotion or policy.self_promotion:
        raise TimesFM3ActivationError("TimesFM3 self-promotion is forbidden")
    raise TimesFM3ActivationError("TimesFM3 has no self-promotion authority")
