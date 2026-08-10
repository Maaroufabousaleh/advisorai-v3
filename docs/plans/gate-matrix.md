# AdvisorAI V3 gate matrix

Checkpoint regenerated 2026-08-10 from `main` at
`c2198ea05bdf6b7c999a3c57bc84403ea9f10bbd` after PR #43 merged.
This matrix separates implementation, tests, local measurements, external
measurements, qualification, and admission. A passing test suite does not open
an external, timed, or human gate.

| Stage / requirement | Authoritative source | Implementation present? | Automated tests? | Local deterministic evidence? | Real external evidence? | Timed evidence? | Human action? | Current gate state | Blocker | Next admissible action |
|---|---|---:|---:|---:|---:|---:|---:|---|---|---|
| Phase 0 contracts, ports, policy gateway, model/runtime harness | architecture §11; phase-00 plan | yes | yes | yes | no | no | no | TESTED / LOCALLY MEASURED | none for local boundary | Preserve accepted local records; do not treat them as admission |
| Phase 0 selected-model stability: TTM-R2, Finance DeBERTa-v3, FinBERT-MiniLM | phase-00 plan; model-runtime runbook | yes | yes | partial | no | pending | no | PENDING_STABILITY | the prior run was interrupted after 31 passing cycles; the fresh 20260809 run is healthy but has not reached 24 hours | Inspect PID 9456 and its append-only evidence; do not restart or concatenate runs |
| Phase 0 remote route bake-off | phase-00 plan; remote-model runbook | yes, including resumable stability runner | yes | short live route evidence plus current DigitalOcean samples | yes, exact provider/model/endpoint identity | pending 24-hour window | no | EXTERNALLY MEASURED / PENDING_STABILITY | Novita trial failed closed on upstream shared-pool 429 and is quarantined; DigitalOcean window is still short | Inspect detached DigitalOcean PID 33057 and its immutable hash chain; do not promote until the full duration passes |
| Phase 0 Nautilus / Prefect / Hamilton seams | phase-00 plan | yes | yes | yes, credential-free component drill | no provider-specific evidence | no | no | TESTED / QUARANTINED | external Nautilus qualification and operational use remain governed by Phase 0 | Keep local seam evidence; qualify only through the selected gate |
| Phase 0 Parquet-manifest vs DuckLake comparison | architecture §4.2; phase-00 plan | manifest/DuckDB baseline yes | baseline yes | yes | yes, isolated challenger review | no | no | QUALIFIED / REJECTED | DuckLake snapshot/reopen worked, but the second catalog added measurable footprint and relocation override complexity without enough incremental value | Keep manifest-managed Parquet + DuckDB + SQLite WAL; preserve the immutable comparison report |
| Phase 0 external Hermes coordinator/subagent review | architecture §8; phase-00 plan | repository harness and pinned external runtime reviewed | local security tests yes | yes | yes, synthetic loopback route only | no | no | EXTERNALLY MEASURED / QUARANTINED | real provider/model route and complete native/filesystem OS attestation remain absent | Preserve the pinned review; formal admission remains closed and no runtime enters AdvisorAI core |
| Phase 0 rclone-crypt upload/verify/restore | architecture §4.2; phase-00 plan | typed adapter and fixture yes | adapter tests yes | in-memory restore yes | no | no | operator configuration may be needed | PENDING_OPERATOR_ACTION / EXTERNAL_EVIDENCE | `rclone` and real provider remotes are not configured; two-provider restore unavailable | Complete one configured provider if available; require a second provider for the full gate |
| Phase 0 resource/privacy/failure behavior | phase-00 plan; resource and gateway runbooks | yes | yes | yes | partial route observations | stability pending | no | TESTED / PENDING_STABILITY | selected runtime duration and real route repetition remain incomplete | Continue durable stability and bounded route evidence |
| Phase 1 deterministic foundation and local rollback/Bronze rebuild | phase-01 plan | yes | yes | immutable local report | no provider deployment | no | no | QUALIFIED LOCALLY | real paper deployment rollback and archive restore remain external | Preserve local report; run provider-specific drill only after venue setup |
| Phase 2 deterministic paper core and Coinbase Exchange Sandbox transport | phase-02 plan; real-api-paper-transition.md | yes; Coinbase-specific `CB-ACCESS-*` signer, schema mapper, exact sandbox host guard, and read-only smoke runner | yes, including `tests/integrations/test_coinbase_exchange.py` | replay/failure fixtures plus signer/product/OMS boundary tests | partial: real Coinbase `/time`, `/products`, `/accounts`, `/orders`, and `/fills` requests reached the reviewed sandbox; account/balance/position/open-order reads passed, but product mapping and fills did not | no | provider catalogue/profile and fills-permission action | EXTERNALLY MEASURED / PENDING_OPERATOR_ACTION | the returned 13-product sandbox catalogue contained `BTC-USD` but not the required `ETH-USD`; the product-filtered fills read returned HTTP 401; no order writes were attempted | Use a reviewed Coinbase Sandbox profile/catalogue that genuinely exposes both required products and grants the required fills read permission, then rerun the Coinbase-specific read-only smoke; never fall back to the generic smoke or production |
| Phase 3 V3-Core source spine | phase-03 plan | yes | yes | parser/replay fixtures | no continuous source operation | no | venue/source configuration | TESTED / PENDING_EXTERNAL_EVIDENCE | live native/Deribit/RSS/GDELT availability and disagreement evidence absent | Run the approved read-only source operation and freshness soak |
| Phase 4 quantitative baseline council | phase-04 plan | yes | yes | public-data bake-off and roster | no paper utility | stability pending | no | LOCALLY MEASURED / PENDING_STABILITY | role winners require stability; paper net utility is later | Finish Phase 0 stability, then collect real paper outcomes |
| Phase 5 typed evidence council | phase-05 plan | yes | yes | independence/authority fixtures | no real V3-Core scored council | no | no | TESTED / PENDING_EXTERNAL_EVIDENCE | real source/model/provider route and data are absent | Exercise with admitted real V3-Core data after earlier gates |
| Phase 6 institutional controls and attribution | phase-06 plan | yes | yes | deterministic risk/attribution fixtures | no real paper order sample | no | no | TESTED / PENDING_EXTERNAL_EVIDENCE | real paper fills and residual incidents are absent | Run the complete paper chain and reconcile exact attribution |
| Phase 7 unattended paper soak and recovery | phase-07 plan | yes | yes | local soak/recovery fixtures | no | 60 calendar days required | operator supervision | PENDING_TIME_GATE | Phase 0–6 real prerequisites and venue operation are not ready | Prepare durable runner; launch only after prerequisites are real |
| Phase 8 Hermes capability lifecycle | phase-08 plan | yes | yes | immutable fixture active-read report plus external runtime review | partial: external runtime/synthetic task; no real model route | no | review required for active-write only | QUARANTINED / PENDING_EXTERNAL_EVIDENCE | native syscall/C-extension/filesystem containment not attested; earlier gates closed | Evaluate a complete host boundary only when admission permits; never admit the synthetic route |
| Phase 9 controlled expansion | phase-09 plan | yes | yes | challenger/source boundaries | no marginal-value challenger evidence | no | no | QUARANTINED | Phase 0–7 and E0 are not satisfied | Keep additions quarantined; reject challengers with evidence when evaluated |
| Phase 10 bounded-live readiness guards | phase-10 plan | yes | yes | readiness/AI-offline fixtures | no live validation | no | explicit human approval required | PENDING_OPERATOR_ACTION | Phase 7 and all prerequisites incomplete; no human authorization | Keep live closed; do not create approval or enable production |
| Real API/paper transition bridge | real-api-paper-transition.md; coinbase-exchange-sandbox runbook | yes | yes | offline config/adapter evidence and Coinbase contract tests | partial: exact sandbox endpoint reached; authenticated account/balance/position/open-order reads passed, but required ETH-USD mapping and fills read failed | no | provider catalogue/profile and fills-permission review | EXTERNALLY MEASURED / PENDING_OPERATOR_ACTION | Coinbase Sandbox returned no `ETH-USD` product and `/fills?product_id=BTC-USD` returned HTTP 401; paper lifecycle is prohibited until the complete read-only gate passes | Rerun `scripts/smoke_coinbase_exchange_sandbox.py` after the reviewed sandbox product set exposes both BTC-USD and ETH-USD and the scoped key can read fills |
| Alpha E0 — V3-Core prerequisite | alpha-team-extension.md | plan-only | none | none beyond base evidence | no | inherits Phase 0–7 | no | BLOCKED | Phase 0–7 paper/recovery/data/risk/resource gates are not complete | Do not implement Alpha runtime; continue base gates |
| Alpha E1 — Research Brain | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E0 and Phase 7 prerequisite | Wait; only maintain plan/traceability |
| Alpha E2 — Controlled Alpha Lab | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E0/E1 gates | Do not build DSL or candidate runtime early |
| Alpha E3 — first V3 strategy challenger | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E2 plus real V3-Core paper evidence | Wait for admission |
| Alpha E4 — optional capability adapters | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E3 and Phase 8/9 authority | Keep external challengers quarantined |
| Alpha E5 — equities / long horizon | alpha-team-extension.md | no, plan-only | no | no | no | no | no | BLOCKED | E4 and point-in-time equity gate | No equity expansion |
| Alpha E6 — controlled candidate expansion | alpha-team-extension.md | no, plan-only | no | no | 60+ healthy paper days required | no | BLOCKED | E5 and per-scope soak | No candidate activation |
| Alpha E7 — bounded-live scope | alpha-team-extension.md | no, plan-only | no | no | no | human approval required | BLOCKED | Phase 10 explicit go-live review | No live scope or approval |

## Current external blockers

- Coinbase Exchange Sandbox configuration is present in the local ignored
  `secrets.env` and passed the zero-network reviewed-host check with configuration
  hash `138042cd88c96e9d3079493beee740ba1e96def1ea748c361e51bd8ea88094cf`.
  The adapter uses only the `PAPER_VENUE` credential scope; secret values were
  not printed or persisted.
- The latest real Coinbase read-only attempt reached `/time`, `/products`,
  authenticated `/accounts` projections, `/orders`, and a product-filtered
  `/fills` read, then failed closed because the returned catalogue had `BTC-USD`
  but no required `ETH-USD`; account/balance/position/open-order reads passed,
  while fills returned sanitized HTTP 401. Immutable sanitized evidence is at
  `artifacts/phase2/coinbase-exchange-sandbox/read-only-smoke/20260809T235254.999504Z/coinbase-read-only-smoke.json`
  with SHA-256 `79c359996cb8d330739495117730924c13ff29f909359e0c189dfea02498fdc7`.
  No order, cancel, transfer, or withdrawal was attempted.
- No real archive remote is configured for `rclone crypt`; the two-provider
  restore remains unavailable until the operator configures providers.
- The previous Phase-0 24-hour worker was interrupted by the laptop shutdown;
  its evidence is preserved. A fresh detached run is active at
  `artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-post-format-final-20260809`
  under PID `9456`; it remains a time gate, not a pass.
- DuckLake comparison is complete and rejected with measured evidence at
  `artifacts/phase0/ducklake-comparison/20260809T162300Z/ducklake-comparison.json`.
- The pinned upstream Hermes review is complete as partial external-runtime
  evidence at
  `artifacts/phase0/external-hermes-review/20260809T162031Z/external-hermes-review.json`;
  it used a synthetic loopback provider and does not open Phase 8.
- The exact Novita route stability trial is preserved as a failed/quarantined
  run after an upstream shared-pool HTTP 429. Earlier DigitalOcean roots were
  quarantined for runner-integrity defects. The fresh immutable DigitalOcean
  route window is active at
  `artifacts/phase0/remote-route-stability/20260809T173237.710604Z` under PID
  `33057` with passing samples; it remains a time gate.
- Phase 7 requires real paper/testnet operation plus an actual 60-day duration.
- Phase 10 requires explicit human approval and remains closed.

## Safety truth

No model, LLM route, Hermes task, browser task, dashboard, or Alpha Team plan
has trading authority. `RiskKernel` remains the deterministic veto and `OMS`
remains authoritative. Live-capital deployment is not approved.
