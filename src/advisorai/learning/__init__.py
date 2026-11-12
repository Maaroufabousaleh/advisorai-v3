"""Paper-trading problem remediation and post-horizon scorecards."""

from .monitor import (
    ChampionComparison,
    ExpiryFlag,
    PerformanceSlice,
    compare_champion,
    expiry_flags,
    load_outcomes,
    load_scorecards,
    summarize,
)
from .paper import (
    LearningProblem,
    PaperDecisionRecord,
    PaperLearningLoop,
    PaperOutcome,
    ProblemKind,
    ReplayResult,
)

__all__ = [
    "ChampionComparison",
    "ExpiryFlag",
    "LearningProblem",
    "PaperDecisionRecord",
    "PaperLearningLoop",
    "PaperOutcome",
    "PerformanceSlice",
    "ProblemKind",
    "ReplayResult",
    "compare_champion",
    "expiry_flags",
    "load_outcomes",
    "load_scorecards",
    "summarize",
]
