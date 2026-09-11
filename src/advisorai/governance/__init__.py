"""Typed human-governance policy infrastructure.

This package is deliberately separate from order submission.  It can produce
an auditable governance decision, but it cannot create, route, cancel, or
authorize an order and it cannot loosen the deterministic RiskKernel.
"""

from .authorization import (
    HUMAN_ONLY_ACTIONS,
    ActorType,
    AuthorizationExpiryMode,
    HumanAuthorization,
    authorization_is_valid,
    is_human_only_action,
)
from .decisions import (
    ActionDirection,
    CalibratedConfidenceEvidence,
    CertaintyClass,
    DecisionImpact,
    DecisionOutcome,
    EquitySnapshot,
    GovernanceDecision,
    GovernanceEvidence,
    GovernanceRequest,
    GovernanceRiskSnapshot,
    ReasonCode,
    RiskState,
    TimingClass,
    evaluate_governance,
    evaluate_live_activation,
)
from .policy import (
    AggregateGroupExposureLimit,
    AllocationStage,
    CorrelatedExposureGroup,
    GovernancePolicy,
    LiveActivationInput,
    PositionSizingInput,
    PositionSizingResult,
    apply_quarter_kelly,
    load_governance_policy,
)

__all__ = [
    "ActionDirection",
    "ActorType",
    "AggregateGroupExposureLimit",
    "AllocationStage",
    "AuthorizationExpiryMode",
    "CalibratedConfidenceEvidence",
    "CertaintyClass",
    "CorrelatedExposureGroup",
    "DecisionImpact",
    "DecisionOutcome",
    "EquitySnapshot",
    "GovernanceDecision",
    "GovernanceEvidence",
    "GovernancePolicy",
    "GovernanceRequest",
    "GovernanceRiskSnapshot",
    "HUMAN_ONLY_ACTIONS",
    "HumanAuthorization",
    "LiveActivationInput",
    "PositionSizingInput",
    "PositionSizingResult",
    "ReasonCode",
    "RiskState",
    "TimingClass",
    "apply_quarter_kelly",
    "authorization_is_valid",
    "evaluate_governance",
    "evaluate_live_activation",
    "load_governance_policy",
    "is_human_only_action",
]
