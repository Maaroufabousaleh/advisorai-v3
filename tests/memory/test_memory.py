from decimal import Decimal

import pytest

from advisorai.memory import (
    HashingEmbedder,
    MemoryLayer,
    MemoryRecord,
    MemoryStore,
    Scorecard,
    ScorecardStore,
)


def test_memory_is_append_only_evidence_linked_and_fts_searchable(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite")
    first = store.append(
        MemoryRecord(
            layer=MemoryLayer.EVIDENCE,
            title="negative result",
            body="model failed calibration",
            negative_result=True,
        )
    )
    second = store.append(
        MemoryRecord(
            layer=MemoryLayer.EPISODIC,
            title="superseding note",
            body="retry with abstention",
            supersedes=first.record_id,
        )
    )
    assert store.get(second.record_id).supersedes == first.record_id
    assert store.search("calibration")[0].negative_result


def test_memory_layer_filter_is_applied_before_limit(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite")
    store.append(MemoryRecord(layer=MemoryLayer.EVIDENCE, title="shared", body="shared term"))
    target = store.append(
        MemoryRecord(layer=MemoryLayer.TRADING, title="shared", body="shared term")
    )
    assert store.search("shared", layer=MemoryLayer.TRADING, limit=1) == (target,)
    with pytest.raises(ValueError, match="cannot be blank"):
        store.search(" ")
    with pytest.raises(ValueError, match="existing record"):
        store.append(
            MemoryRecord(
                layer=MemoryLayer.EVIDENCE,
                title="dangling",
                body="invalid supersession",
                supersedes=__import__("uuid").uuid4(),
            )
        )


def test_optional_hashing_semantic_recall_is_deterministic_and_non_authoritative(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite")
    first = store.append(
        MemoryRecord(layer=MemoryLayer.SEMANTIC, title="market", body="BTC market volatility")
    )
    store.append(
        MemoryRecord(
            layer=MemoryLayer.SEMANTIC, title="accounting", body="quarterly revenue filing"
        )
    )
    embedder = HashingEmbedder(dimension=32)
    first_hits = store.semantic_search("BTC volatility", embedder=embedder)
    second_hits = store.semantic_search("BTC volatility", embedder=embedder)
    assert first_hits == second_hits
    assert first_hits[0].record.record_id == first.record_id
    assert -1 <= first_hits[0].score <= 1
    with pytest.raises(ValueError, match="dimension"):
        embedder.similarity((1.0,), (1.0,))


def test_scorecard_store_keeps_latest_routing_measurement(tmp_path):
    store = ScorecardStore(tmp_path / "scores.sqlite")
    scorecard = Scorecard(
        subject="technical-agent",
        subject_version="v1",
        role="technical_flow",
        asset="BTC",
        horizon="1h",
        regime="normal",
        factual_precision=Decimal("0.9"),
        calibration=Decimal("0.8"),
        abstention_quality=Decimal("0.8"),
        contradiction_detection=Decimal("0.7"),
        net_utility=Decimal("0.1"),
        latency_ms=20,
        api_cost_usd=Decimal("0"),
        failure_rate=Decimal("0"),
        eligible_for_routing=True,
    )
    store.append(scorecard)
    assert store.latest("technical-agent", "v1").scorecard_id == scorecard.scorecard_id
