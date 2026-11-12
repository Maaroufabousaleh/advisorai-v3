# Technology and integration decision register

**Evaluation snapshot:** 2026-11-11. This is a planning register, not an
implementation inventory or an operational admission record. A listed tool is
not installed, trusted, or authorized by virtue of appearing here.

The accepted near-term milestone remains **AdvisorAI Integrated Alpha**: a
safe owner session that recovers and reconciles state, processes point-in-time
evidence through the frozen reference fabric, passes a typed proposal through
deterministic portfolio construction, RiskKernel, OMS, and paper/testnet
execution, records outcomes, then stops and resumes safely. No entry here
changes that milestone, the frozen model fabric, or the live-capital gate.

## Decision and evidence vocabulary

| Decision | Meaning |
|---|---|
| ADOPTED-IN-PLAN | A durable architecture or roadmap boundary is accepted. It does not assert code or admission. |
| PROTOTYPE/EXPERIMENT | One bounded, removable, quarantined experiment may be proposed behind the named port. |
| DEFERRED | No work until the stated trigger occurs. |
| REJECTED | Do not add this project to the current architecture; reopen only on the stated evidence. |
| REQUIRES-MORE-INFORMATION | Do not integrate until the missing upstream, rights, privacy, or operating facts are authoritative. |

Implementation and admission are separate axes:

| Axis | Values used here |
|---|---|
| Implementation | NOT PRESENT; PLAN ONLY; BRANCH ONLY; MAIN; TESTED |
| Admission | NOT APPLICABLE; RESEARCH ONLY; SHADOW; PAPER; BOUNDED CANARY; ADMITTED |

“Tested” describes evidence for an exact version and scope. It does not imply
promotion. Current operational evidence remains in the
[status dossier](../plans/status.md) and [gate matrix](../plans/gate-matrix.md).

## Common qualification contract

Every prototype must identify its exact repository or provider, pinned version
or revision, code license, separate model/weight/data terms, upstream activity
signal, dependencies, and removal path. Review its marginal value against the
existing owner before adding it. The target workstation is 16 GB RAM, an RTX
4060 with 8 GB VRAM, and WSL2; resource fit must be measured on the actual host.
No hosted service may receive private data or credentials until its terms,
retention, training-on-input, region, rate, cost, and failure behavior are
reviewed.

The evaluation checked current upstream identity, license metadata, and
repository activity where available on 2026-11-11. It does not pin a candidate
revision. Recheck and record the exact release/commit and last-push signal when
a prototype is approved; a recent commit or release is not evidence of value,
rights, or admission.

Every data or model experiment must preserve source identity, content hash
where practical, publication/effective and retrieval timestamps, point-in-time
cutoff, revision history, transformation lineage, exact code/model/environment
identity, and all failed trials. The test must use an immutable snapshot,
known-failure fixtures, bounded resources, and a reproducible output artifact.
Results enter AdvisorAI through typed artifacts and independent validation.
No external tool may write canonical market history, decisions, orders, fills,
reconciliation, RiskKernel policy, or outcomes.

## Durable architecture decisions

| Area | Decision and owner boundary | Implementation / admission |
|---|---|---|
| Research Brain truth boundary | **ADOPTED-IN-PLAN.** Parquet, DuckDB, and SQLite/WAL ledgers remain empirical truth. Papers, filings, methods, citations, hypotheses, and prior work form a source-linked knowledge layer. Graph, embedding, wiki, and generated-summary indexes are derived, rebuildable retrieval only. See [ADR 0010](../decisions/0010-research-brain-truth-boundary.md). | Existing storage is MAIN; the Research Brain workflow is PLAN ONLY. Retrieval packages are NOT PRESENT / RESEARCH ONLY. |
| Provider/source authority | **ADOPTED-IN-PLAN.** Every feed is classified as OFFICIAL PRIMARY, QUALIFIED SECONDARY, DISCOVERY / CONTEXT ONLY, or UNQUALIFIED. Authority, licensing, freshness, and point-in-time suitability are reviewed per provider and feed, not inherited from an SDK. See [ADR 0011](../decisions/0011-provider-source-authority-and-pit.md). | Registry/policy is PLAN ONLY; source admission remains per-source and scope-specific. |
| Agent permission model | **ADOPTED-IN-PLAN.** Trust classes and permissions are independent of Docker, gVisor, Kata, or Firecracker. Backend presence is not a security qualification. See [ADR 0012](../decisions/0012-agent-permission-and-sandbox-boundary.md). | Policy is PLAN ONLY. PR #207 is open and draft; Kata runtime and sandbox security evidence are not admitted. |
| Human governance | **ADOPTED-IN-PLAN.** Higher-risk scope changes require an explicit human decision, bounded scope, expiry, revocation, and immutable audit record. Documentation does not grant approval. PRs #196–#198 remain open drafts; see the dated status below. | Governance feature code is BRANCH ONLY in stacked draft PRs; not current-main implementation or authorization. |
| Workstation security and release lifecycle | **ADOPTED-IN-PLAN.** Define practical threat, secret, dependency/model provenance, backup/restore, rollback, and incident procedures for the owner workstation. See [Security](../../SECURITY.md) and [ADR 0007](../decisions/0007-session-oriented-operating-model.md). | Security controls exist in part on MAIN; end-to-end release and recovery lifecycle remains PLAN ONLY. |

## Research memory and experiment tools

| Candidate and upstream | Decision, role, and boundary | Rights, PIT, resources, and go/no-go | Current state |
|---|---|---|---|
| [Cognee](https://github.com/topoteretes/cognee) | **PROTOTYPE/EXPERIMENT.** Compare as an optional source-linked knowledge graph/index behind a Research Brain retrieval port. It does not own empirical truth or write to canonical stores. | Upstream Python project declares Apache-2.0. Optional graph/vector services and model/provider calls can exceed the workstation budget or disclose content; test a local minimal setup, source-linked retrieval, index deletion/rebuild, and removal. Reject if it requires an always-on graph service or cannot preserve citation lineage. | NOT PRESENT; RESEARCH ONLY. |
| Karpathy's [LLM Wiki method](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f); example implementation [lucasastorian/llmwiki](https://github.com/lucasastorian/llmwiki) | **PROTOTYPE/EXPERIMENT, method only.** Evaluate source-linked pages and revision history as a retrieval interface. There is no single canonical LLM Wiki product upstream selected for AdvisorAI. | The linked implementation declares Apache-2.0 and supports remote-model/MCP workflows; another repository using the name, [CinderZhang/Karpathy_llm_wiki](https://github.com/CinderZhang/Karpathy_llm_wiki), has no license assertion in GitHub metadata. Review exact code and provider terms, retention, and credentials before any isolated trial. Test source URL/hash, citation anchors, generated-model identity, and rebuild from original documents. | NOT PRESENT; RESEARCH ONLY. |
| [Mem0](https://github.com/mem0ai/mem0) | **DEFERRED.** Its personalized agent-memory role overlaps conversational memory and does not fill a distinct empirical Research Brain need. Reconsider only if a measured use case remains after the source-linked knowledge layer. | Upstream declares Apache-2.0. Hosted and self-hosted configurations have different data flows and infrastructure. Any reconsideration requires a local/offline path, retention and deletion rules, and proof that records remain non-authoritative. | NOT PRESENT; NOT ADMITTED. |
| [Qanat](https://github.com/fidetolabs/qanat) | **PROTOTYPE/EXPERIMENT.** Optional Hermes MCP research adapter that emits AdvisorAI-native ExperimentArtifact and ValidationReport records. It is not a canonical data source, backtester, portfolio/risk owner, broker adapter, or execution path. | Upstream declares MIT. Its README documents limits in sealing the ideation agent from future information. Require known-future and timestamp-window fixtures, deterministic seed/replay where supported, fee/slippage parity, input/output hashes, independent validation, and failed/aborted trial accounting. No credentials or canonical writes. | NOT PRESENT; RESEARCH ONLY; Phase 8/9. |
| FlockMTL (current upstream [dais-labo/flock](https://github.com/dais-labo/flock)) | **PROTOTYPE/EXPERIMENT, read-only only.** Evaluate SQL-native LLM/RAG exploration only on disposable research copies. Never use model calls in canonical transforms, ledgers, risk, or execution queries. | Current upstream source declares MIT; remote model providers and data retain their own terms. Require no writes, explicit model/provider/cost metadata, read-only credentials, and reproducible results. Reject if inference is implicit in a canonical query or needs secrets in a research process. | NOT PRESENT; RESEARCH ONLY; Phase 9. |

## Provider, source, and event intelligence

The source registry uses these tiers:

| Tier | Use |
|---|---|
| OFFICIAL PRIMARY | The issuer, regulator, exchange, or data owner publishes the source. This does not waive feed terms, rate limits, or point-in-time checks. |
| QUALIFIED SECONDARY | A reviewed intermediary with documented provenance, rights, latency, revisions, and quality checks. |
| DISCOVERY / CONTEXT ONLY | Useful for finding events or leads; cannot establish issuer facts or empirical truth. |
| UNQUALIFIED | Default-deny until identity, rights, timestamps, quality, and operating conditions are established. |

“Free-first” means choosing the lowest-cost source that satisfies rights,
provenance, latency, and quality requirements. It does not mean using an
unofficial source because it is free. A provider registration must record the
provider and source owner; data category and tier; exact API/feed; authentication
scope; rate and delay; cost/free-tier limits; access, retention, and
redistribution rights; publication, effective, retrieval, and first-available
timestamps; revision/vintage model; identity keys; fallback; quality checks;
failure policy; and last review date.

| Candidate and upstream | Decision, role, and boundary | Requirements and go/no-go | Current state |
|---|---|---|---|
| [OpenBB Open Data Platform](https://github.com/openbq-org/OpenBB) | **PROTOTYPE/EXPERIMENT.** Use only as a bounded adapter for selected research/enrichment sources behind AdvisorAI provider ports. It does not supply canonical data, portfolio state, operator control, or execution authority. | V5 upstream reports Apache-2.0; older V4 releases were AGPL-3.0-only, so pin the exact release. Each provider/feed needs separate rights, timestamp/PIT, rate-limit, quality, privacy, cost, and fallback review. Reject if it creates another data lake or source trust is inferred from the client library. | NOT PRESENT; RESEARCH ONLY; Phase 3/9. |
| SEC EDGAR APIs and public filings ([data.sec.gov](https://data.sec.gov/), [access policy](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)) | **ADOPTED-IN-PLAN as an OFFICIAL PRIMARY candidate** for U.S. issuer filings and structured disclosures, outside the current BTC/ETH Phase 3 scope. | SEC says public filing content is freely accessible and reusable and documents fair-access limits and a declared User-Agent. Retain accession, issuer identity, filing/acceptance time, retrieval time, form, source document, and content hash. Recheck endpoint policy and document-specific rights on implementation. | NOT PRESENT as an integrated equity source; NOT ADMITTED; later Phase 9. |
| FRED / ALFRED ([API documentation](https://fred.stlouisfed.org/docs/api/fred/overview.html)) | **ADOPTED-IN-PLAN as an OFFICIAL PRIMARY candidate** for macro series and vintage-aware research. | Preserve series identity, release date, ALFRED real-time/vintage fields, retrieval time, and revision history. API credentials and data-use/attribution terms are scoped; check terms per series and endpoint. Reject any feed lacking vintage-aware replay where the research question requires it. | NOT PRESENT as an integrated source; NOT ADMITTED; later Phase 9. |
| [GDELT](https://www.gdeltproject.org/data.html) | **ADOPTED-IN-PLAN as DISCOVERY / CONTEXT ONLY.** It can surface broad event/news leads; its derived event interpretation is not issuer truth. | Feeds have their own publication/update cadence, and a 15-minute batch cadence is not event-time truth. Preserve article/source URL and all source, publication, ingestion, and extraction timestamps. Verify redistribution and source-content rights per underlying item. Cross-check material claims against official sources. | Current docs name GDELT in Phase 3; no authority beyond discovery/context is implied. |
| Official exchange feeds | **ADOPTED-IN-PLAN as OFFICIAL PRIMARY per qualified venue/feed.** | “Official” does not mean free or redistributable. Qualify each feed's entitlement, delay, rate, price, retention, redistribution, timestamp, sequence/gap, and fallback terms. No venue-wide blanket approval. | Existing venue-specific candidates remain separately gated; not globally admitted. |
| Issuer investor-relations pages, releases, and RSS | **ADOPTED-IN-PLAN as OFFICIAL PRIMARY per issuer/source.** | Record issuer identity, canonical URL, publication/effective and retrieval times, revisions, and rights. Deduplicate against filings and releases; do not treat webpage scrape time as publication time. | NOT PRESENT as a generalized source registry; later Phase 9. |
| [tvscreener](https://github.com/deepentropy/tvscreener) | **DEFERRED / DISCOVERY ONLY.** The upstream describes this as an unofficial third-party TradingView interface, not an official TradingView source or canonical PIT provider. | Upstream declares Apache-2.0, but that does not grant rights to the underlying TradingView data. No use in canonical collection until usage rights, permitted retention/redistribution, provenance, rate policy, and PIT coverage are explicitly cleared. | NOT PRESENT; NOT ADMITTED. |
| OpenTerminal ([JamievanRiel/OpenTerminal](https://github.com/JamievanRiel/openterminal)); OpenTerminalUI candidates [Hitheshkaranth/OpenTerminalUI](https://github.com/Hitheshkaranth/OpenTerminalUI) and [laanito/OpenTerminalUI](https://github.com/laanito/OpenTerminalUI) | **REJECTED as runtime or operator-console replacements.** Existing React/FastAPI console, provider ports, portfolio, and execution owners already cover those responsibilities. | The linked repositories currently declare MIT. No second portfolio store, operator control plane, canonical cache, alert authority, or execution path. UX and provider ideas may be reviewed without importing a runtime. Revisit only for a named, non-duplicative component with exact upstream and license. | NOT PRESENT; NOT ADMITTED. |
| Official-text-first event intelligence | **PROTOTYPE/EXPERIMENT after Integrated Alpha.** Add an EventEvidencePort or existing equivalent for SEC filings, issuer earnings releases, official statements, and IR event metadata. | Every item carries source identity, publication/retrieval time, extraction model/version, confidence, and deduplication identity. Verify numerical claims against filings or official structured releases. Third-party transcripts/audio require source-specific rights to access, record, store, transform, and redistribute, plus latency/retention review. Only afterward consider [OpenAI Whisper](https://github.com/openai/whisper), whose code repository declares MIT; review checkpoint/model assets separately. No event-to-order path. | NOT PRESENT; RESEARCH ONLY; Phase 9. |

## Decision models and forecast challengers

ADR 0009 remains the vendor-neutral authority for DecisionModelPort. The
reference forecast fabric remains Chronos-2-small, TTM-R2, Kronos-small,
LightGBM, compact financial sentiment/FinBERT, a local typed Decision Model
reference, and an on-demand reasoning LLM. ADR 0008 owns that roster.

| Candidate | Decision and boundary | Qualification and revisit trigger | Current state |
|---|---|---|---|
| Laya | **PROTOTYPE/EXPERIMENT** behind DecisionModelPort, local and shadow-only. | Pin exact upstream/checkpoint and license; measure within the 8 GB VRAM/16 GB RAM envelope; validate schema, timeout, abstention, malformed output, calibration, privacy, fallback, replay, and zero authority. | Existing planning reference only; no admission implied. Phase 5. |
| Kev | **PROTOTYPE/EXPERIMENT** with the smallest feasible local checkpoint only. | Same typed-port checks as Laya. Measure exact peak RAM/VRAM, load latency, and failure rate; do not infer large-checkpoint feasibility. | NOT PRESENT or admitted unless separately evidenced. Phase 5. |
| Jev | **REQUIRES-MORE-INFORMATION.** No official API is assumed. | Require authoritative provider identity, current API/model docs, pricing, terms, data retention, privacy/training-on-input, region, rate limits, availability, and commercial-use rights before any adapter plan. Third-party compatibility servers do not establish provider terms. | NOT PRESENT; NOT ADMITTED. |
| TimesFM 3 | **DEFERRED isolated challenger.** Do not reopen the core bake-off or duplicate PR #205. | Google source code is Apache-2.0, while TimesFM 3 pretrained weights use a separate non-commercial license. Require permitted intended use, exact weight revision, a preregistered forecast deficiency, PIT snapshot, baseline, latency/RAM/VRAM/load/failure measurements, and positive incremental system value. | PR #205 is OPEN, non-draft, against main; BRANCH ONLY and research-only. No core roster change. |
| TTM-R3 | **DEFERRED challenger.** Keep TTM-R2 in the frozen reference fabric. | The existing license audit treats checkpoint/model-card rights separately and says exact artifact and revision must be reviewed before use or bundling. Reopen only for a measured roster deficiency and an exact rights-cleared checkpoint. | Existing historical research remains research-only; no promotion from this entry. |
| [Kronos-base](https://github.com/NeoQuasar/Kronos) | **DEFERRED challenger.** Keep Kronos-small in the reference fabric. | Require exact upstream/checkpoint/license review, a measured deficiency, same-snapshot comparison, resource profile, and positive marginal value after costs. | NOT PRESENT in the admitted roster; Phase 9 only if triggered. |
| [TabPFN-TS](https://huggingface.co/PriorLabs/tabpfn-time-series) | **DEFERRED / REQUIRES-MORE-INFORMATION.** | The current license audit says the checkpoint is gated and was not acquired. Before access, review local/cloud terms, privacy, checkpoint license, hardware, commercial use, PIT behavior, and data retention. | NOT ACQUIRED; NOT ADMITTED. |

No candidate is promoted because of release novelty. A challenger sheet must
name the deficiency, PIT dataset, horizon, baseline, preregistered metrics,
system-level incrementality, latency, peak RAM/VRAM, load time, failure rate,
license, reproducibility, and rollback.

## Agent sandbox and research frameworks

| Candidate and upstream | Decision, role, and boundary | Rights, security, resource fit, and acceptance | Current state |
|---|---|---|---|
| Hardened Docker | **ADOPTED-IN-PLAN as a baseline backend**, not as a VM-equivalent trust claim. | Pin image digests, default-deny network, scoped read-only mounts, no implicit host secrets, explicit export directory, CPU/RAM/time limits, audit identity, crash cleanup, and adversarial tests. | Main contains prior local Docker evidence; not Phase 8 admission. |
| [gVisor](https://gvisor.dev/docs/) ([code](https://github.com/google/gvisor)) | **PROTOTYPE/EXPERIMENT** as an alternative backend after host compatibility checks. | Upstream declares Apache-2.0. Measure syscall/application compatibility, resource use, adversarial behavior, filesystem/network policy, and cleanup. It narrows host-kernel exposure but does not replace AdvisorAI's permission policy. | NOT PRESENT as admitted backend; Phase 8. |
| [Kata Containers](https://github.com/kata-containers/kata-containers) | **PROTOTYPE/EXPERIMENT** only for higher-isolation untrusted native work after host qualification. | Upstream declares Apache-2.0 and requires supported virtualization. Require guest startup, filesystem/network/secret adversarial probes, resource-exhaustion, crash cleanup, sandbox identity, and no broker/RiskKernel/OMS authority. | PR #207 is OPEN DRAFT. Its description records no Kata runtime installed, no guest or adversarial runtime probes, and no Phase 8 admission. |
| [Firecracker](https://github.com/firecracker-microvm/firecracker) | **DEFERRED.** Upstream declares Apache-2.0. Consider only if a concrete native workload needs a microVM and no simpler qualified backend meets its threat model. | Additional KVM, image, orchestration, lifecycle, and operator cost must be measured against the workload. | NOT PRESENT; NOT ADMITTED. |
| [elizaOS](https://github.com/elizaOS/eliza) | **REJECTED as a second agent runtime** for the current roadmap. Hermes/Skill Foundry remains the agent boundary. | Upstream declares MIT. Revisit only for an essential capability Hermes cannot expose through a narrow adapter, with exact revision, permission, provenance, resource, and authority review. | NOT PRESENT; NOT ADMITTED. |
| [Optuna](https://github.com/optuna/optuna) | **ADOPTED-IN-PLAN** for bounded hyperparameter/experiment search inside the Alpha Team, if the existing workflow has no equivalent. | Upstream declares MIT and documents a lightweight search framework. Require preregistered search space, trial budget, seed/snapshot, no test-set optimization, immutable trial ledger, pruning/cost limits, and predeclared best-trial rule. It emits AdvisorAI-native artifacts only. | NOT PRESENT as a dependency; PLAN ONLY; Phase 9. |
| [Qlib](https://github.com/microsoft/qlib) | **PROTOTYPE/EXPERIMENT** as an external comparison/validation environment, never a V3 data, backtest, or execution authority. | Upstream declares MIT. Pin dependencies and compare on frozen snapshots; reproduce finalists independently in AdvisorAI's canonical Nautilus pipeline. | NOT PRESENT; RESEARCH ONLY; Phase 9. |
| [QuantaAlpha](https://github.com/QuantaAlpha/QuantaAlpha) | **DEFERRED / quarantined benchmark only.** Its factor-mining role overlaps Alpha Team. | GitHub reported no SPDX license assertion and a last-push date of 2026-06-29 on the evaluation date. Do not import or depend on it until an explicit license, exact revision, remote model/provider dependencies, credential flow, data lineage, PIT behavior, and resource use are reviewed. | NOT PRESENT; NOT ADMITTED. |
| [AlphaAgent](https://arxiv.org/abs/2502.16789) and related repositories | **RESEARCHED as a methods reference; no framework adoption.** Multiple repositories use the AlphaAgent name, so no single code upstream is selected. | Use published concepts only until the exact paper-linked upstream, license, data rights, dependencies, and marginal value are verified. Any trial must emit AdvisorAI-native artifacts and pass the same PIT/red-team review. | NOT PRESENT; RESEARCH ONLY. |
| [WorldQuant BRAIN](https://www.worldquant.com/terms-and-conditions/) / referenced WorldQuant Miner workflow | **REQUIRES-MORE-INFORMATION / DEFERRED.** Treat as an external platform, not an SDK or canonical validator. | Obtain authoritative BRAIN product/API terms, account permissions, data and submission rights. Do not scrape, automate submissions, put credentials in agents, or treat platform scores as independent validation. | NOT PRESENT; NOT ADMITTED. |
| [DEAP](https://github.com/DEAP/deap) | **DEFERRED.** Add only for a defined evolutionary-search need that current bounded search cannot meet. | Upstream is LGPL-3.0; release and distribution obligations need review. Its evolutionary-search role overlaps Optuna and Alpha Team. | NOT PRESENT; NOT ADMITTED. |
| [Google Colab](https://colab.research.google.com/), [Kaggle Notebooks](https://www.kaggle.com/code), or other cloud workers | **DEFERRED optional escape hatch.** Local-first remains canonical. | Never a runtime dependency, secrets store, live worker, or canonical artifact store. Review the selected service's terms, quotas, retention, region, and data handling before a job. If later used, send an immutable job bundle and appropriately redacted data, with pinned code/environment/seed; verify and register outputs locally. Trigger only when a measured job exceeds local capacity. | NOT PRESENT; NOT ADMITTED. |

## Other evaluated ideas

| Candidate | Decision and reason | Revisit trigger |
|---|---|---|
| [PYNQ-Z2](https://www.pynq.io/boards.html) | **REJECTED for the current hardware roadmap.** No measured workload establishes an FPGA advantage over the RTX 4060 workstation. | Profiling identifies a stable, FPGA-suitable bottleneck with a measured benefit. |
| [OctoBot](https://github.com/Drakkar-Software/OctoBot) | **REJECTED as a runtime.** It duplicates exchange, strategy, portfolio, and execution responsibilities held by NautilusTrader, OMS, and RiskKernel. Its GPL-3.0 license is an additional distribution consideration. | No current trigger; a future review must identify a separable non-trading component and license fit. |
| [OpenNews](https://github.com/IUnlimit/opennews) and the previously identified nivalume/open-news-mcp candidate | **REJECTED as additional runtime stacks.** They duplicate the planned Research Brain/event pipeline and add another store/graph/queue surface. | IUnlimit/opennews declares MIT and was last pushed 2026-04-09 in the checked repository metadata; the nivalume/open-news-mcp repository was not resolvable through GitHub's repository API on the evaluation date, so its current license/status is unverified. Revisit a narrowly reusable parser only after exact license, source rights, provenance, and incremental value are verified. |
| Broad Rust rewrite | **REJECTED.** No repository profile demonstrates a broad Python bottleneck worth the rewrite and FFI/service cost. | A measured hot path has a narrowly scoped performance or safety gain. |
| Autonomous development VM | **DEFERRED developer tooling, not trading architecture.** | A concrete development isolation need with separate credentials/workspace, controlled mounts, and auditable artifact export. |

## Dated GitHub review snapshot

Live pull-request state was checked on 2026-11-11. These statuses are a dated
record, not a guarantee of future state. “Open” and “draft” work is not merged
into main; a branch implementation is not current-main implementation or
operational admission.

| PR | State and base | Scope represented here |
|---|---|---|
| [#196](https://github.com/Maaroufabousaleh/advisorai-v3/pull/196) | OPEN, DRAFT, base main | Human governance/live-risk policy; BRANCH ONLY. |
| [#197](https://github.com/Maaroufabousaleh/advisorai-v3/pull/197) | OPEN, DRAFT, stacked on #196's branch | Trading scope/authority; BRANCH ONLY. |
| [#198](https://github.com/Maaroufabousaleh/advisorai-v3/pull/198) | OPEN, DRAFT, stacked on #197's branch | Human notifications/approval; BRANCH ONLY. |
| [#205](https://github.com/Maaroufabousaleh/advisorai-v3/pull/205) | OPEN, non-draft, base main | Isolated TimesFM 3 challenger; no core roster change. |
| [#207](https://github.com/Maaroufabousaleh/advisorai-v3/pull/207) | OPEN, DRAFT, base main | Kata implementation only; PR states runtime unattested and Phase 8 not admitted. |
| [#208](https://github.com/Maaroufabousaleh/advisorai-v3/pull/208) | OPEN, DRAFT, stacked on a launch-fix branch | Optional Chronos stability profile; not a new workstation prerequisite. |
| [#209](https://github.com/Maaroufabousaleh/advisorai-v3/pull/209) | MERGED to main | Session-oriented roadmap rebaseline; accepted planning authority. |

Live-capital authorization remains disabled until Phase 10's separate explicit
human-approved gate. No documentation or open PR listed above grants it.

## Review sources

Project identities and mutable license/terms claims were checked against
upstream repositories or provider documentation on the evaluation date.
Recheck the exact revision before installation or distribution:

- [Cognee](https://github.com/topoteretes/cognee), [Mem0](https://github.com/mem0ai/mem0), [Qanat](https://github.com/fidetolabs/qanat), and [OpenBB V5](https://github.com/openbq-org/OpenBB).
- [OpenBB V4 and V5 licensing FAQ](https://github.com/OpenBB-finance/openbb-docs/blob/main/content-odp/python/faqs/license.mdx).
- [SEC EDGAR fair access](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data), [SEC reuse FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions), [FRED/ALFRED API](https://fred.stlouisfed.org/docs/api/fred/overview.html), and [GDELT data](https://www.gdeltproject.org/data.html).
- [TimesFM code and weight terms](https://github.com/google-research/timesfm), [IBM TTM-R3 model card](https://huggingface.co/ibm-granite/granite-timeseries-ttm-r3), and the repository's [model/data license audit](../compliance/license-audit.md).
- [Optuna](https://github.com/optuna/optuna), [Qlib](https://github.com/microsoft/qlib), [QuantaAlpha](https://github.com/QuantaAlpha/QuantaAlpha), [DEAP](https://github.com/DEAP/deap), and [WorldQuant terms](https://www.worldquant.com/terms-and-conditions/).
- [Kata Containers](https://github.com/kata-containers/kata-containers), [gVisor](https://gvisor.dev/docs/), [NIST SSDF](https://csrc.nist.gov/projects/ssdf), and the [OWASP Secrets Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet).
