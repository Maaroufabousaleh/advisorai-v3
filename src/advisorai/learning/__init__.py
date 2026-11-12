"""Paper-trading problem remediation and post-horizon scorecards."""

from .monitor import (
    ChampionComparison,
    ExpiryFlag,
    PerformanceSlice,
    StrategyComparison,
    compare_champion,
    compare_strategies,
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
    "StrategyComparison",
    "LearningProblem",
    "PaperDecisionRecord",
    "PaperLearningLoop",
    "PaperOutcome",
    "PerformanceSlice",
    "ProblemKind",
    "ReplayResult",
    "compare_champion",
    "compare_strategies",
    "expiry_flags",
    "load_outcomes",
    "load_scorecards",
    "summarize",
]
