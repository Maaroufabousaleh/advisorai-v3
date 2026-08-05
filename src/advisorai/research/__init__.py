"""Research validity gates and challenger lifecycle primitives."""

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
    "MultipleTestingAudit",
    "PurgedWalkForward",
    "RegimeSplit",
    "SensitivityResult",
    "evaluate_regime",
    "evaluate_sensitivity",
    "StrategyValidation",
    "validate_strategy",
]
