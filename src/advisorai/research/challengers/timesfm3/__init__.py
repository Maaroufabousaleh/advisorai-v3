"""TimesFM 3 research-only challenger boundary.

Importing this package does not import ``timesfm3``, PyTorch, Hugging Face, or
any model checkpoint.  Model loading is explicit and scoped to a research
session in :mod:`advisorai.research.challengers.timesfm3.adapter`.
"""

from .adapter import (
    TIMESFM3_QUANTILE_LEVELS,
    TimesFM3ForecastArtifact,
    TimesFM3ModelUnavailable,
    TimesFM3RawOutput,
    TimesFM3ResearchAdapter,
    TimesFM3RuntimeConfig,
)
from .governance import (
    TIMESFM3_CHECKPOINT,
    TIMESFM3_LICENSE_CLASS,
    TimesFM3ActivationError,
    TimesFM3ActivationScope,
    TimesFM3Governance,
    assert_timesfm3_activation_allowed,
    assert_timesfm3_cannot_self_promote,
    load_timesfm3_governance,
)
from .pit import (
    CovariateRole,
    KnownFutureBasis,
    KnownFutureValueKind,
    PointInTimeViolation,
    TimesFM3CovariatePoint,
    TimesFM3CovariateSeries,
    TimesFM3ResearchInput,
    TimesFM3TargetSeries,
    validate_timesfm3_point_in_time_input,
)
from .qualification import (
    TIMESFM3_COMPARISON_SET,
    TIMESFM3_RESEARCH_TARGETS,
    TimesFM3Ablation,
    TimesFM3AblationSpec,
    TimesFM3EvaluationRequest,
    TimesFM3QualificationEvidence,
    build_timesfm3_qualification_matrix,
    evaluate_timesfm3,
)

__all__ = [
    "CovariateRole",
    "KnownFutureBasis",
    "KnownFutureValueKind",
    "PointInTimeViolation",
    "TIMESFM3_CHECKPOINT",
    "TIMESFM3_COMPARISON_SET",
    "TIMESFM3_LICENSE_CLASS",
    "TIMESFM3_QUANTILE_LEVELS",
    "TIMESFM3_RESEARCH_TARGETS",
    "TimesFM3ActivationError",
    "TimesFM3ActivationScope",
    "TimesFM3Ablation",
    "TimesFM3AblationSpec",
    "TimesFM3CovariatePoint",
    "TimesFM3CovariateSeries",
    "TimesFM3EvaluationRequest",
    "TimesFM3ForecastArtifact",
    "TimesFM3Governance",
    "TimesFM3ModelUnavailable",
    "TimesFM3QualificationEvidence",
    "TimesFM3RawOutput",
    "TimesFM3ResearchAdapter",
    "TimesFM3ResearchInput",
    "TimesFM3RuntimeConfig",
    "TimesFM3TargetSeries",
    "assert_timesfm3_activation_allowed",
    "assert_timesfm3_cannot_self_promote",
    "build_timesfm3_qualification_matrix",
    "evaluate_timesfm3",
    "load_timesfm3_governance",
    "validate_timesfm3_point_in_time_input",
]
