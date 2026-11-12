"""Research inspection API: registry-backed, read-only, no authority."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from advisorai.api.dashboard import DashboardProjection
from advisorai.research import (
    HypothesisCard,
    PaperCard,
    ResearchRegistry,
)


def _seed(path: Path):
    registry = ResearchRegistry(path)
    paper = registry.store(
        PaperCard(
            source_url="https://arxiv.org/abs/2401.00001",
            authors=("Doe, J.",),
            claimed_result="momentum persists",
            limitations=("single venue",),
            relevance="BTC intraday",
        ),
        record_type="paper",
    )
    hypothesis = registry.store(
        HypothesisCard(
            economic_rationale="inventory mean reversion",
            universe="BTCUSDT",
            horizon="1h",
            baseline="naive",
            preregistered_plan="plan-v1",
            owner="research-lead",
            expires_at=datetime(2026, 9, 1, tzinfo=UTC),
            paper_card_ids=(paper.artifact_id,),
        ),
        record_type="hypothesis",
    )
    registry.link(paper.artifact_id, hypothesis.artifact_id, "paper_supports_hypothesis")
    return registry, paper, hypothesis


def test_research_records_project_summaries(tmp_path):
    path = tmp_path / "inspect.sqlite3"
    _seed(path)
    store = DashboardProjection(research_registry_path=path)
    records = store.research_records()
    assert len(records) == 2
    assert {record.record_type for record in records} == {"paper", "hypothesis"}
    assert all(record.summary for record in records)
    papers = store.research_records("paper")
    assert len(papers) == 1 and papers[0].record_type == "paper"


def test_research_lineage_walks_chain(tmp_path):
    path = tmp_path / "lineage.sqlite3"
    registry, paper, hypothesis = _seed(path)
    store = DashboardProjection(research_registry_path=path)
    chain = store.research_lineage(str(paper.artifact_id))
    assert {view.record_type for view in chain} == {"paper", "hypothesis"}
    assert store.research_lineage(str(uuid4())) == ()


def test_no_registry_returns_empty():
    store = DashboardProjection()
    assert store.research_records() == ()
    assert store.research_lineage(str(uuid4())) == ()
