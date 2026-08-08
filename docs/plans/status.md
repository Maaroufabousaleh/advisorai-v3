# Implementation status and gate evidence

This record distinguishes implementation coverage from an architecture gate that
requires external, time-based evidence. A green unit test does not claim a 24-hour
or 60-day operational gate.

| Phase | Implementation | Automated evidence | Gate status |
|---|---|---|---|
| 0 | Harness, ports, policy-enforced model gateway, exact model acquisition, isolated/attested local runtimes, real public-data local bake-off, role roster, append-only stability runner, and durable Phase-0 gate records | `tests/phase0`, gateway/port tests, immutable local bake-off reports, and the component evidence drill | Local component probes passed in `artifacts/phase0/component-bakeoff/20260808T031144.840248Z/phase0-component-bakeoff.json`; selected TTM-R2/DeBERTa/MiniLM roles still require 24-hour stability, remote route evidence, DuckLake comparison, external Hermes review, and real rclone-provider restore |
| 1 | Contracts, PIT lake, DuckDB/Polars query, ledgers, typed V3-Core YAML admission, config rollback, resources, traces, FTS5-first memory with optional deterministic hashing recall, durable flows/incidents, and explicit service ownership/mode boundaries | contracts/data/config/recovery/resource/orchestration/memory/service tests plus the local rebuild drill | Local rollback/Bronze rebuild evidence passed in `artifacts/phase1/local-rebuild/20260808T024709.706561Z/phase1-local-rebuild.json`; provider-specific paper deployment rollback remains external |
| 2 | Paper event spool/replay, typed native market events, account and margin/borrow/FX/corporate-action accounting, durable-first account/OMS retries, signed target constraints, combined-state-hash RiskKernel/OMS binding, paper/native testnet boundary with read-only account projection, venue-projection reconciliation, TCA, cadence-gated runtime admission | `tests/execution`, `tests/integrations`, `tests/runtime` | Paper failure fixtures pass; Nautilus remains Phase 0 governed despite being installed and locally tested |
| 3 | Native/Deribit/RSS/GDELT/official-vintage parsers, raw-first REST/WSS replay, typed trade/book/bar/funding/open-interest normalization, origin/revision/availability, quality monitor | `tests/data` | Parser/lineage fixtures pass; source availability dashboards need live soak |
| 4 | Naive/statistical/LightGBM boundary, isolated ModernFinBERT/MiniLM/DeBERTa and TTM-R2/R3/TSPulse/Chronos/Kronos runtimes, calibration, GPU lease, public walk-forward/finance-sentiment measurements, and evidence-bound roster | `tests/models`, `tests/phase0`, measured local roster | Role winners are pending stability; point-in-time paper utility remains a later admission gate |
| 5 | Router, typed roles, bounded adaptive waves, evidence graph, independence gates, DecisionBundle and expiry/cutoff binding | `tests/agents`, `tests/api` | Correlated-evidence and target-only boundaries pass |
| 6 | Portfolio comparisons, risk analytics/stress, validation, attribution incidents | `tests/institutional` | Deterministic controls pass |
| 7 | Soak records/gate, incident-ledger rebuild, and immutable recovery rebuild | `tests/recovery/test_soak.py` | Requires actual 60-day paper operation and restore drills |
| 8 | Hermes policy, bounded isolation runner, enforced child socket/DNS and read-only filesystem policy, sensitive-environment scrubbing, artifact/capability lifecycle and broker | `tests/capabilities`, immutable active-read capability evidence | Local Hermes-to-active-read collector lifecycle passed in `artifacts/phase8/capability-evidence/20260808T041257.837542Z/phase8-capability-evidence.json`; formal Phase-8 admission remains pending behind earlier gates, and the feed remains fixture-only |
| 9 | Vintaged official releases, equity corporate-action/daily-council boundary, challenger registry, browser ladder, archive verification | `tests/expansion` | Requires one-at-a-time live data/challenger evidence |
| 10 | Human approval, bounded live readiness, AI-offline invariant, order guard | `tests/live` | Must remain closed until Phase 7 and explicit human approval pass |
| 0–7 bridge | Typed secret loader/redaction, reviewed connector cards, HTTPS/WSS transport guards, direct typed LLM adapter, durable gateway-call records, paper/testnet HMAC venue transport with signed open-order reconciliation and read-only account/fill/position/balance projection, raw-first native market normalization/replay, cadence-gated closed-cutoff `PaperRuntime` with durable kill-switch/dashboard control hydration and terminal per-order risk rejection, durable resource measurements/leases, refreshing/deduplicated ledger-backed dashboard/config projection, incident/replay/post-horizon scorecards | `tests/config/test_secrets.py`, `tests/integrations`, `tests/runtime`, `tests/learning`, `tests/resources`, `tests/data/test_market_events.py`, `tests/api/test_dashboard.py` | Local contracts pass; provider-specific endpoint smoke, continuous operation, Phase 0 stability, Phase 7 soak, and human decisions remain external |
| Alpha Team extension | Plan-only integration for optional E0-E7 research work | None | E0 and all later gates remain closed; no Alpha Team implementation or admission evidence is claimed |

The current repository therefore has broad executable coverage, but does not claim
Phase 0, Phase 7, or Phase 10 gates without the external evidence explicitly named
above.

Latest local verification (2026-08-08):
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python scripts/verify_acceptance.py`
passed all eleven phase suites, with suite results of
Phase 0/1/2/3/4/5/6/7/8/9/10 = 118/151/96/22/19/34/10/7/19/11/5. Suite totals
overlap a few shared contract tests. A single-process
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/pytest -q` passes all 490 collected
tests with the optional Nautilus runtime active. The acceptance runner stops at the
first failed phase, so later suites are never counted as evidence after an
earlier gate failure. The Phase 0 inventory was regenerated at
`artifacts/phase0/availability.json` (ignored runtime output), and remains an
availability record rather than an admission decision. The local static and
reproducibility checks pass for Ruff lint, dependency locking, bytecode compilation,
diff hygiene, and the dashboard TypeScript/Vite build. The recent scoped code
changes are formatted. A repository-wide
`./.venv/bin/ruff format --check .` passes with all 232 Python files formatted.
The test collection check reports 490 collected tests. The dashboard build passes
with `npm run build`
from `dashboard/`.

Model stability evidence is still external and pending. On 2026-08-08, a
pre-format 24-hour attempt was interrupted after the pinned worker hashes
failed closed against the finalized source; its failed/quarantined cycles are
preserved in the ignored stability evidence directory. A fresh admission root,
`artifacts/phase0/model-runtime-qualification/runtime-admission-post-format-20260808`,
was attested against the formatted worker and passed a one-cycle smoke for all
three pending role candidates. The supervised replacement run at
`artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-post-format-final-20260808`
has passing cycles and remains in progress; no roster entry has moved from
`pending_stability` to `selected`.

The Phase-1 local operational report has SHA-256
`6e8cd86017dacea7b4a0fff8e9ea41901ec4bb7ee02961f5811dcbb7266342b2` and
records zero network calls, three auditable configuration activations with
restart-persistent rollback, and byte/row-identical Bronze rebuild output. It
does not satisfy the real venue, Phase-7, or Phase-10 gates.

The Phase-0 component evidence report has SHA-256
`2a5c9d07be845b7222a065edc4d20a4a8d272bf7780918d3f27ad42abbb0523c` and
records passing local probes for the guarded Nautilus replay seam, installed
PydanticAI/Prefect/Hamilton runtime seams, deterministic Parquet manifest plus
DuckDB reads, the repository Hermes isolation boundary, and two in-memory
rclone adapter restores. It records zero network calls, credentials, or paper
orders. DuckLake, the external Hermes package, and real rclone/provider restore
remain quarantined; the report does not record or imply a Phase-0 pass.

The Phase-8 capability evidence report has SHA-256
`fad59563bf477a41d64007175c53637a170dae64cdf82bd89672f35b796dc9e9` and
records two identical Hermes child-process outputs, enforced child socket/DNS
and read-only filesystem policies, secret scrubbing, untrusted RSS content
preservation, an 11-event ledger lifecycle through `active_read`, restart
hydration, read-only broker execution, and rejection of forbidden/write
authority. It records zero network calls, credentials, and paper orders. The
formal Phase-8 gate remains pending and no capability is globally admitted.
