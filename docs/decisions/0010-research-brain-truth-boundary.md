# ADR 0010: Canonical empirical truth and derived research retrieval

**Status:** accepted planning authority. Research Brain implementation and
admission remain separate.

## Context

AdvisorAI needs to retrieve papers, methods, filings, prior hypotheses,
experiments, failures, and explanatory notes. A graph, vector index, wiki, or
generated summary can improve retrieval, but those systems may be lossy,
mutable, probabilistic, remotely hosted, or difficult to reproduce. They must
not become a second truth store.

## Decision

1. Canonical empirical and operational truth remains in the existing
   point-in-time data and immutable artifact system: Parquet and manifests,
   DuckDB analytical access, and SQLite/WAL ledgers. Decisions, orders, fills,
   reconciliation, TCA/attribution, experiment outcomes, and promotion records
   remain in their authoritative typed records.
2. Research knowledge is a source-linked layer for papers, filings, methods,
   claims, citations, hypotheses, and explanatory material. It does not
   overwrite empirical records.
3. Search indexes, embeddings, graph projections, wiki pages, and generated
   summaries are derived retrieval. They must be rebuildable from preserved
   source records and explicitly labelled non-authoritative.
4. Every material retrieved claim retains source identity and URL/path,
   content hash where practical, ingestion time, publication/effective time
   where applicable, citation anchor, transformation lineage, and model and
   version identity for generated content.
5. Deleting or rebuilding a retrieval index cannot delete or mutate canonical
   empirical records. No retrieval component receives a write path to PIT
   history, ledgers, OMS, RiskKernel policy, or outcome truth.
6. Every optional retrieval dependency is removable. A lightweight local
   configuration is preferred; extra graph, vector, database, or hosted-model
   services require measured incremental value and a separate privacy/rights
   review.

## Consequences

Cognee and a source-linked LLM-Wiki approach may be prototyped as retrieval
adapters. Mem0 remains deferred until a distinct scientific-memory need is
identified. Qanat is a separate quarantined experiment adapter and does not
change the truth boundary.

Prototype acceptance includes source and hash lineage, cutoff-aware retrieval,
index rebuild/deletion without canonical data loss, no unauthorized writes,
known-future leakage fixtures, and successful removal. These checks do not
grant trading authority or operational admission.

## References

- [Technology and integration decision register](../reference/technology-decision-register.md)
- [Phase 9 controlled expansion](../plans/phase-09-controlled-expansion.md)
- [Alpha Team extension](../plans/alpha-team-extension.md)
- [Canonical trading authority](0001-canonical-trading-authority.md)
