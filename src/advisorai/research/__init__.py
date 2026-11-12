"""Research validity gates, challenger lifecycle, and Research Brain records."""

from .cards import (
    ArtifactStatus,
    CandidateFactorArtifact,
    ExperimentArtifact,
    HypothesisCard,
    PaperCard,
    PromotionDecision,
    ValidationReport,
)
from .strategy import StrategyValidation, validate_strategy
from .validity import (
    MultipleTestingAudit,
    PurgedWalkForward,
    RegimeSplit,
    SensitivityResult,
    evaluate_regime,
    evaluate_sensitivity,
)

__all__ = [
    "ArtifactStatus",
    "CandidateFactorArtifact",
    "ExperimentArtifact",
    "HypothesisCard",
    "MultipleTestingAudit",
    "PaperCard",
    "PromotionDecision",
    "PurgedWalkForward",
    "RegimeSplit",
    "SensitivityResult",
    "ValidationReport",
    "evaluate_regime",
    "evaluate_sensitivity",
    "StrategyValidation",
    "validate_strategy",
]
