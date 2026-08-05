from decimal import Decimal

from advisorai.models import LexicalNewsClassifier


def test_cpu_news_triage_records_reliability_and_abstention():
    signal = LexicalNewsClassifier().classify(
        "exchange upgrade after growth",
        source_reliability=Decimal("0.8"),
        novelty=Decimal("0.7"),
    )
    assert signal.label == "positive"
    assert not signal.abstained
    assert signal.novelty == Decimal("0.7")
