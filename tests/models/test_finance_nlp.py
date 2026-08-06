from decimal import Decimal

import pytest

from advisorai.models import FinBERTAdapter, LexicalNewsClassifier


def test_cpu_news_triage_records_reliability_and_abstention():
    signal = LexicalNewsClassifier().classify(
        "exchange upgrade after growth",
        source_reliability=Decimal("0.8"),
        novelty=Decimal("0.7"),
    )
    assert signal.label == "positive"
    assert not signal.abstained
    assert signal.novelty == Decimal("0.7")


def test_finbert_adapter_accepts_only_a_pinned_typed_runner(monkeypatch):
    adapter = FinBERTAdapter(
        runner=lambda text: {
            "label": "positive",
            "score": "0.9",
            "source_reliability": "0.8",
            "novelty": "0.6",
        },
        checkpoint_hash="a" * 64,
    )
    monkeypatch.setattr(adapter, "available", True)
    signal = adapter.classify("approved growth")
    assert signal.label == "positive"
    assert signal.score == Decimal("0.9")

    invalid = FinBERTAdapter(runner=lambda _: {"label": "positive"}, checkpoint_hash="bad")
    monkeypatch.setattr(invalid, "available", True)
    with pytest.raises(RuntimeError, match="hash"):
        invalid.classify("text")


def test_finbert_adapter_fails_closed_for_untyped_runner_output(monkeypatch):
    adapter = FinBERTAdapter(
        runner=lambda _: {"label": "positive", "novelty": "not-a-number"}, checkpoint_hash="a" * 64
    )
    monkeypatch.setattr(adapter, "available", True)
    with pytest.raises(RuntimeError, match="malformed signal"):
        adapter.classify("text")
