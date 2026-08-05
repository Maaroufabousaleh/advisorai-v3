"""CPU-friendly finance-news triage and optional FinBERT challenger."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class NewsSignal:
    label: str
    score: Decimal
    source_reliability: Decimal
    novelty: Decimal
    abstained: bool

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("news signals require a label")
        if not self.score.is_finite() or not self.source_reliability.is_finite():
            raise ValueError("news signal metrics must be finite")
        if not self.novelty.is_finite() or not Decimal("0") <= self.novelty <= Decimal("1"):
            raise ValueError("news novelty must be between zero and one")
        if not Decimal("0") <= self.source_reliability <= Decimal("1"):
            raise ValueError("source reliability must be between zero and one")


class LexicalNewsClassifier:
    """Deterministic high-volume triage baseline; no browser text becomes authority."""

    positive = frozenset({"beat", "growth", "approval", "surge", "partnership", "upgrade"})
    negative = frozenset({"fraud", "halt", "hack", "loss", "downgrade", "liquidation"})

    def classify(
        self,
        text: str,
        *,
        source_reliability: Decimal = Decimal("0.5"),
        novelty: Decimal = Decimal("0.5"),
    ) -> NewsSignal:
        tokens = {token.strip(".,:;!?()[]{}\"'").lower() for token in text.split()}
        score = Decimal(len(tokens & self.positive) - len(tokens & self.negative))
        label = "positive" if score > 0 else "negative" if score < 0 else "neutral"
        return NewsSignal(
            label=label,
            score=score,
            source_reliability=source_reliability,
            novelty=novelty,
            abstained=source_reliability < Decimal("0.2"),
        )


class FinBERTAdapter:
    name = "finbert-family"

    def __init__(self) -> None:
        self.available = importlib.util.find_spec("transformers") is not None

    def classify(self, text: str) -> NewsSignal:
        if not self.available:
            raise RuntimeError("FinBERT dependency is quarantined until a CPU benchmark passes")
        raise RuntimeError("FinBERT checkpoint and tokenizer must be pinned by Phase 0")
