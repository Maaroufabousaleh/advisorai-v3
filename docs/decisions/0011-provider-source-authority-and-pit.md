# ADR 0011: Provider authority, data rights, and point-in-time evidence

**Status:** accepted planning authority. Source admission remains per provider,
feed, dataset, and operating scope.

## Context

A client library does not confer authority or rights on the data it retrieves.
Official exchange or issuer feeds may have delay, entitlement, retention, and
redistribution limits. Aggregators may transform timestamps or revise history.
Point-in-time suitability depends on source semantics and a replayable
availability history, not on a provider's marketing category.

## Decision

Classify each provider feed independently:

- **OFFICIAL PRIMARY:** published by the issuer, regulator, exchange, or data
  owner. This identifies provenance; it does not waive terms or PIT checks.
- **QUALIFIED SECONDARY:** intermediary with reviewed rights, source lineage,
  timestamp, revision, latency, and quality behavior.
- **DISCOVERY / CONTEXT ONLY:** useful for leads and broad event discovery; not
  authoritative for issuer facts or empirical truth.
- **UNQUALIFIED:** default-deny until identity, rights, timestamp, quality, and
  failure behavior are reviewed.

Each provider registration must capture provider and source owner, data
category, exact API/feed, auth scope, rate limit, latency/delay, price and
free-tier limits, access/retention/redistribution rights, source and retrieval
timestamps, first-available and effective/publication times, revision model,
identity keys, fallback, quality checks, failure policy, and review date.

For point-in-time use, preserve raw source identity, retrieved payload or
permitted immutable reference, content hash where practical, source timestamps,
retrieval time, revision/vintage identifiers, transformation lineage, and
cutoff. Missing or ambiguous availability times fail closed for PIT claims.
Derived or corrected observations cannot silently rewrite the earlier
availability history.

Prefer the lowest-cost source that satisfies rights, provenance, latency, and
quality requirements. “Free” and “official” are not substitutes for a
source-specific entitlement review. A feed that fails a requirement is
discovery-only or unqualified until resolved.

## Candidate source classification

- SEC EDGAR public filings are a strong official-primary U.S. issuer candidate;
  respect the SEC fair-access policy and preserve filing/acceptance lineage.
- FRED/ALFRED are official macro-data candidates; ALFRED vintage and revision
  fields are required when historical decisions depend on what was known then.
- GDELT is discovery/context only. Its event extraction is derived and cannot
  become issuer truth without primary-source confirmation.
- Exchange feeds and issuer investor-relations feeds are qualified one feed at
  a time; no global free-data assumption is permitted.

These candidates do not expand Phase 3's current BTC/ETH scope. Equity and
macro source work remains controlled expansion under Phase 9 unless a newer
accepted plan says otherwise.

## Consequences

OpenBB may be prototyped only behind AdvisorAI provider ports, with each
provider separately reviewed. Unofficial TradingView interfaces remain
discovery-only or deferred. Event intelligence begins with official text;
audio and third-party transcripts require source-specific access, recording,
storage, transformation, redistribution, latency, and retention review.

No source adapter writes canonical market history or has portfolio, RiskKernel,
OMS, broker, or execution authority. A source's presence in a register is not
source admission.

## References

- [Technology and integration decision register](../reference/technology-decision-register.md)
- [Phase 1 safety, data truth, and resource skeleton](../plans/phase-01-safety-data-resources.md)
- [Phase 3 V3-Core data spine](../plans/phase-03-v3-core-data-spine.md)
- [Data flow](../concepts/data-flow.md)
