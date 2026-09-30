# ADR 0009: Typed Decision Model boundary

Status: accepted target architecture; contract and adapters are planned, not
implemented or admitted by this ADR.

Insert a generic `DecisionModelPort` between calibrated evidence and portfolio
construction:

```text
validated PIT snapshot
  -> Chronos / TTM-R2 / Kronos / LightGBM / FinBERT / deterministic features
  -> EvidenceGraph and calibrated evidence bundle
  -> DecisionModelPort
  -> typed DecisionProposal
  -> deterministic Portfolio Constructor
  -> RiskKernel -> OMS -> NautilusTrader -> venue
  -> reconciliation -> TCA/attribution -> outcome memory
```

`DecisionModelArtifact` identifies exact model, adapter, code, configuration,
snapshot, and evidence bundle. A typed proposal may include LONG/SHORT/FLAT/
ABSTAIN, a probability distribution, calibrated confidence, decision score,
horizon, optional regime/strategy class, uncertainty, evidence references,
missing-evidence flags, disagreement, and expiry. Prose is optional and never
required to consume the contract.

The Decision Model synthesizes evidence only. It cannot change `RiskPolicy`,
submit or cancel an order, read broker secrets, override EvidenceGraph failure,
stale/missing-data rejection, or portfolio construction, bypass RiskKernel, or
self-promote. Model failure, late output, invalid schema, or quarantined status
must lead to missing evidence, a separately permitted fallback, or abstention.

`LayaAdapter` is the initial local reference; `KevAdapter`, optional remote
`JevAdapter`, and a future `AdvisorCDMAdapter` implement the same port. Jev is
never mandatory. All outputs retain model/version and evidence ancestry for
replay and admission review.
