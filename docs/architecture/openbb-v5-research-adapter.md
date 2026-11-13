# Optional OpenBB V5 research adapter

Status: design evaluation only. The adapter is not enabled, is not a dependency,
and has no credentials. Direct AdvisorAI providers remain the execution-grade
feeds and the only operational source of truth.

## Intended role

OpenBB V5 can provide a discovery and normalization layer for research agents:
Hermes, Research Scout, Evidence Council, and Research Brain may request
candidate sources through an explicit allowlisted catalog. A thin AdvisorAI
port should translate those results into source candidates with provider,
dataset, retrieval time, license/terms reference, raw source URL, source
revision, and independent-source family. The existing evidence and temporal
validation pipeline remains responsible for acceptance and durable truth.

## Guardrails

- Keep provider packages optional and opt in per provider/dataset. Do not load
  all OpenBB providers or install provider plugins by default.
- Prefer anonymous, genuinely free, read-only endpoints. Credentialed adapters
  must use provider-specific scopes and the trusted CredentialResolver path.
- Do not route orders, balances, account state, or trading decisions through
  OpenBB. Do not use it as a substitute for direct exchange, market, or official
  macro feeds.
- Store source provenance and raw evidence references; do not merge normalized
  values across providers without preserving each observation's identity,
  units, timestamp semantics, revisions, and lineage.
- Independent corroboration requires independent upstream publishers. Two
  OpenBB routes backed by the same provider count as one source family.
- Apply rate limits, cache policy, terms/license checks, and free-tier budgets
  at the provider boundary. If a free tier or terms are unclear, mark the
  provider unavailable until reviewed; never allow an SDK default to trigger a
  paid service.
- Any future integration must be behind a ResearchDataAdapter protocol and
  pass source-policy, provenance, and offline contract tests before opt-in.

## Suggested phased evaluation

1. Pin a V5-compatible OpenBB core/provider set in an isolated research extra;
   verify current API shape, licenses, and runtime dependencies.
2. Prototype one keyless metadata/data provider and compare its output against
   the existing direct connector for timestamps, units, revisions, and source
   identity.
3. Benchmark coverage, latency, limits, and maintenance cost. Keep the direct
   connector authoritative; add OpenBB only where it improves discovery or
   provides genuinely independent corroboration.
4. Require explicit config allowlisting per provider. Do not enable the adapter
   in Hermes or any other role until isolation, cost controls, and data lineage
   are reviewed.

No OpenBB package was activated or installed as part of this change.
