"""FTS retrieval over the research registry: rebuildable index, ranked search."""

from __future__ import annotations

from datetime import UTC, datetime

from advisorai.research import (
    CandidateFactorArtifact,
    HypothesisCard,
    PaperCard,
    ResearchRegistry,
    ResearchRetrieval,
)


def _seed(tmp_path):
    registry = ResearchRegistry(tmp_path / "retrieval.sqlite3")
    paper = registry.store(
        PaperCard(
            source_url="https://arxiv.org/abs/2401.00001",
            authors=("Doe, J.",),
            claimed_result="short-horizon mean reversion in BTC after liquidity shocks",
            limitations=("single venue",),
            relevance="BTC intraday",
        ),
        record_type="paper",
    )
    hypothesis = registry.store(
        HypothesisCard(
            economic_rationale="inventory mean reversion after liquidity shocks",
            universe="BTCUSDT",
            horizon="1h",
            baseline="naive",
            preregistered_plan="plan-v1",
            owner="quant-lead",
            expires_at=datetime(2026, 9, 1, tzinfo=UTC),
            paper_card_ids=(paper.artifact_id,),
        ),
        record_type="hypothesis",
    )
    factor = registry.store(
        CandidateFactorArtifact(
            dsl_ast_hash="a" * 64,
            dsl_source='{"op": "ts_delta", "args": [{"field": "close"}, {"field": "open"}]}',
            universe="BTCUSDT",
        ),
        record_type="factor",
    )
    return registry, paper, hypothesis, factor


def test_rebuild_and_search_finds_relevant_records(tmp_path):
    registry, paper, hypothesis, factor = _seed(tmp_path)
    retrieval = ResearchRetrieval(registry.database_path)
    assert retrieval.search("liquidity") == ()
    assert retrieval.rebuild() == 3
    hits = retrieval.search("liquidity")
    assert {hit.record_type for hit in hits} == {"paper", "hypothesis"}
    assert all(hit.rank <= 0 for hit in hits)
    factors = retrieval.search("ts_delta", record_type="factor")
    assert len(factors) == 1 and factors[0].artifact_id == factor.artifact_id
    assert retrieval.search("nonexistentterm123") == ()
    assert retrieval.search("   ") == ()


def test_index_is_disposable_projection(tmp_path):
    registry, paper, hypothesis, factor = _seed(tmp_path)
    retrieval = ResearchRetrieval(registry.database_path)
    assert retrieval.rebuild() == 3
    assert registry.count() == 3
    # Dropping the index loses no evidence: rebuild restores full recall.
    import sqlite3

    with sqlite3.connect(registry.database_path) as connection:
        connection.execute("DROP TABLE research_fts")
    assert retrieval.search("liquidity") == ()
    assert retrieval.rebuild() == 3
    assert len(retrieval.search("liquidity")) == 2


def test_librarian_job_retrieves_relevant_records(tmp_path):
    from advisorai.research import JobState, ResearchCoordinator, ResearchJob, ResearchRole, librarian_search

    registry, paper, hypothesis, factor = _seed(tmp_path)
    coordinator = ResearchCoordinator(None, max_workers=2)
    job = coordinator.submit(ResearchRole.LIBRARIAN, detail="find mean reversion work")
    hits_box: dict[str, object] = {}

    def worker(running: ResearchJob, cancelled) -> str:
        hits = librarian_search(registry.database_path, "mean reversion")
        hits_box["hits"] = hits
        assert not cancelled()
        return f"found {len(hits)} records"

    done = coordinator.run(job.job_id, worker)
    assert done.state is JobState.COMPLETED
    hits = hits_box["hits"]
    assert isinstance(hits, tuple) and len(hits) >= 2
    assert {hit.record_type for hit in hits} >= {"paper", "hypothesis"}

