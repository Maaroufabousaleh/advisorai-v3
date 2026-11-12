"""Research validity gates, challenger lifecycle, and Research Brain."""

from .cards import (
    ArtifactStatus,
    CandidateFactorArtifact,
    ExperimentArtifact,
    HypothesisCard,
    PaperCard,
    PromotionDecision,
    ValidationReport,
)
from .factors import (
    ALLOWED_OPS,
    FactorDslError,
    FactorEvaluation,
)
from .factors import (
    canonical_hash as factor_hash,
)
from .factors import (
    duplicates as factor_duplicates,
)
from .factors import (
    evaluate as evaluate_factor,
)
from .factors import (
    parse as parse_factor,
)
from .factors import (
    structural_fingerprint as factor_fingerprint,
)
from .jobs import JobState, ResearchCoordinator, ResearchJob, ResearchRole
from .registry import ResearchRegistry
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
    "ALLOWED_OPS",
    "FactorDslError",
    "FactorEvaluation",
    "JobState",
    "ResearchCoordinator",
    "ResearchJob",
    "ResearchRegistry",
    "ResearchRole",
    "evaluate_factor",
    "factor_duplicates",
    "factor_fingerprint",
    "factor_hash",
    "parse_factor",
]
