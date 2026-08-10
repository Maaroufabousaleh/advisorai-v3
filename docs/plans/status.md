# Implementation status and gate evidence

This record distinguishes implementation coverage from an architecture gate that
requires external, time-based evidence. A green unit test does not claim a 24-hour
or 60-day operational gate.

| Phase | Implementation | Automated evidence | Gate status |
|---|---|---|---|
| 0 | Harness, ports, policy-enforced model gateway, exact model acquisition, isolated/attested local runtimes, real public-data local bake-off, role roster, append-only stability runner, durable Phase-0 gate records, and scoped two-provider rclone-crypt qualification runner | `tests/phase0`, gateway/port tests, immutable local bake-off reports, component drill, isolated DuckLake comparison, pinned external Hermes review, exact-route stability runner, `tests/expansion/test_rclone.py`, `tests/config/test_secrets.py` | Latest local component probe passed in `artifacts/phase0/component-bakeoff/20260810T000406.852454Z/phase0-component-bakeoff.json` with SHA-256 `6914b9e1ba508777a3c3edd47433c5a340be06f73857ae600b84c68510fdf4b7`; DuckLake was measured and rejected; the upstream Hermes runtime was reviewed in a disposable namespace with a synthetic route; the latest DigitalOcean exact-route root is quarantined after three upstream shared-pool HTTP 429 gateway abstentions; selected local roles still require 24-hour stability; the controlled archive runner is implemented and fixture-tested but the first real attempt found no populated scoped archive values, made zero network calls, and remains pending |
| 1 | Contracts, PIT lake, DuckDB/Polars query, ledgers, typed V3-Core YAML admission, config rollback, resources, traces, FTS5-first memory with optional deterministic hashing recall, durable flows/incidents, and explicit service ownership/mode boundaries | contracts/data/config/recovery/resource/orchestration/memory/service tests plus the local rebuild drill | Local rollback/Bronze rebuild evidence passed in `artifacts/phase1/local-rebuild/20260808T024709.706561Z/phase1-local-rebuild.json`; provider-specific paper deployment rollback remains external |
| 2 | Paper event spool/replay, typed native market events, account and margin/borrow/FX/corporate-action accounting, durable-first account/OMS retries, signed target constraints, combined-state-hash RiskKernel/OMS binding, paper/native testnet boundary with read-only account projection, venue-projection reconciliation, TCA, cadence-gated runtime admission, and Coinbase Exchange Sandbox-specific CB-ACCESS signer/schema transport | `tests/execution`, `tests/integrations`, `tests/runtime`, `tests/integrations/test_coinbase_exchange.py` | Local Coinbase signer/product/OMS boundary tests pass. Real smoke reached the reviewed sandbox `/time` and `/products`; the returned catalogue omitted required `ETH-USD`, while authenticated account/balance/position/open-order reads passed and the product-filtered fills read returned sanitized HTTP 401. The read-only gate and paper lifecycle remain pending. Nautilus remains Phase 0 governed despite being installed and locally tested |
| 3 | Native/Deribit/RSS/GDELT/official-vintage parsers, raw-first REST/WSS replay, typed trade/book/bar/funding/open-interest normalization, origin/revision/availability, quality monitor | `tests/data` | Parser/lineage fixtures pass; source availability dashboards need live soak |
| 4 | Naive/statistical/LightGBM boundary, isolated ModernFinBERT/MiniLM/DeBERTa and TTM-R2/R3/TSPulse/Chronos/Kronos runtimes, calibration, GPU lease, public walk-forward/finance-sentiment measurements, and evidence-bound roster | `tests/models`, `tests/phase0`, measured local roster | Role winners are pending stability; point-in-time paper utility remains a later admission gate |
| 5 | Router, typed roles, bounded adaptive waves, evidence graph, independence gates, DecisionBundle and expiry/cutoff binding | `tests/agents`, `tests/api` | Correlated-evidence and target-only boundaries pass |
| 6 | Portfolio comparisons, risk analytics/stress, validation, attribution incidents | `tests/institutional` | Deterministic controls pass |
| 7 | Soak records/gate, incident-ledger rebuild, and immutable recovery rebuild | `tests/recovery/test_soak.py` | Requires actual 60-day paper operation and restore drills |
| 8 | Hermes policy, bounded isolation runner, enforced child socket/DNS, read-only filesystem, conventional sensitive-path and process-environment metadata policies, common process-spawn denial, sensitive-environment scrubbing, artifact/capability lifecycle and broker | `tests/capabilities`, immutable active-read capability evidence, and a disposable pinned upstream runtime review | Local Hermes-to-active-read collector lifecycle passed in `artifacts/phase8/capability-evidence/20260808T050150.878842Z/phase8-capability-evidence.json`; the pinned upstream runtime completed a synthetic loopback coordinator/subagent probe, but filesystem/native-syscall containment and a real provider route remain unattested; formal Phase-8 admission remains pending behind earlier gates |
| 9 | Vintaged official releases, equity corporate-action/daily-council boundary, challenger registry, browser ladder, archive verification | `tests/expansion` | Requires one-at-a-time live data/challenger evidence |
| 10 | Human approval, bounded live readiness, AI-offline invariant, order guard | `tests/live` | Must remain closed until Phase 7 and explicit human approval pass |
| 0–7 bridge | Typed secret loader/redaction, reviewed connector cards, HTTPS/WSS transport guards, direct typed LLM adapter, durable gateway-call records, generic paper/testnet HMAC venue transport, Coinbase Exchange Sandbox transport with exact host guard and scoped credential binding, signed open-order reconciliation and provider-specific account/fill/position/balance projection, raw-first native market normalization/replay, cadence-gated closed-cutoff `PaperRuntime` with durable kill-switch/dashboard control hydration and terminal per-order risk rejection, durable resource measurements/leases, refreshing/deduplicated ledger-backed dashboard/config projection, incident/replay/post-horizon scorecards | `tests/config/test_secrets.py`, `tests/integrations`, `tests/runtime`, `tests/learning`, `tests/resources`, `tests/data/test_market_events.py`, `tests/api/test_dashboard.py` | Local contracts pass. Coinbase evidence is partial/external: `/time`, `/products`, authenticated `/accounts` projections, `/orders`, and product-filtered `/fills` were attempted; account/balance/position/open-order reads passed, but `ETH-USD` was absent and fills returned HTTP 401. No order was sent. Continuous operation, Phase 0 stability, Phase 7 soak, and human decisions remain external |
| Alpha Team extension | Plan-only integration for optional E0-E7 research work | None | E0 and all later gates remain closed; no Alpha Team implementation or admission evidence is claimed |

The current repository therefore has broad executable coverage, but does not claim
Phase 0, Phase 7, or Phase 10 gates without the external evidence explicitly named
above.

## Coinbase Exchange Sandbox evidence

The zero-network transition configuration check passed for
`coinbase_exchange_sandbox` at
`https://api-public.sandbox.exchange.coinbase.com` with reviewed host
`api-public.sandbox.exchange.coinbase.com`, configuration hash
`138042cd88c96e9d3079493beee740ba1e96def1ea748c361e51bd8ea88094cf`, and only
the `PAPER_VENUE` credential references bound to the adapter. The real,
provider-specific smoke runner is
[`scripts/smoke_coinbase_exchange_sandbox.py`](../../scripts/smoke_coinbase_exchange_sandbox.py).
The later strict resolver recheck failed closed on a non-allowlisted inventory
variable before constructing the scoped resolver; no value was logged or
persisted. That local configuration prerequisite must be corrected before a
future smoke rerun.

Its latest immutable 2026-08-09 attempt is at
`artifacts/phase2/coinbase-exchange-sandbox/read-only-smoke/20260809T235254.999504Z/coinbase-read-only-smoke.json`
with SHA-256
`79c359996cb8d330739495117730924c13ff29f909359e0c189dfea02498fdc7`. Seven
bounded network calls reached the reviewed host: `/time`, `/products`,
authenticated `/accounts` projections, `/orders`, and a product-filtered
`/fills` read. The returned 13-product catalogue contained `BTC-USD` but not
`ETH-USD`; account, balance, position, and open-order reads passed, while the
BTC-USD fills read returned sanitized HTTP 401. The required symbol and complete
read-only gates therefore failed closed, and no order, cancel, transfer, or
withdrawal was attempted. This is `EXTERNALLY MEASURED /
PENDING_OPERATOR_ACTION`, not venue admission. See the
[`Coinbase Exchange Sandbox runbook`](../runbooks/coinbase-exchange-sandbox.md)
for the exact rerun and next action.

The complete checkpoint matrix is maintained in
[`gate-matrix.md`](gate-matrix.md). It preserves the distinction between
`TESTED`, `LOCALLY MEASURED`, `EXTERNALLY MEASURED`, `QUALIFIED`,
`QUARANTINED`, `PENDING_STABILITY`, and `PENDING_OPERATOR_ACTION`.

Latest local verification (2026-08-10):
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /tmp/advisorai-v3-full-verify-20260809/bin/python scripts/verify_acceptance.py`
passed all eleven phase suites, with suite results of
Phase 0/1/2/3/4/5/6/7/8/9/10 = 124/152/107/22/19/34/10/7/25/18/5. Suite totals
overlap a few shared contract tests. A single-process
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /tmp/advisorai-v3-full-verify-20260809/bin/python -m pytest -q`
passes all 521 tests with every declared optional extra active in the isolated
locked verification environment. The acceptance runner stops at the first failed
phase, so later suites are never counted as evidence after an earlier gate
failure. The Phase 0 inventory was regenerated at
`artifacts/phase0/availability.json` (ignored runtime output), and remains an
availability record rather than an admission decision. The local static and
reproducibility checks pass for Ruff lint, dependency locking, bytecode compilation,
diff hygiene, tracked secret/model-weight checks, and the dashboard TypeScript/Vite
build. The recent scoped code changes are formatted. A repository-wide Ruff
format check passes with all 244 Python files formatted.
The dashboard build passes with `npm run build` from `dashboard/`. The complete
verification environment was isolated under `/tmp` so the two durable Phase-0
stability workers continued using their original environment unchanged.

Model stability evidence is still external and pending. On 2026-08-08, a
pre-format 24-hour attempt was interrupted after the pinned worker hashes
failed closed against the finalized source; its failed/quarantined cycles are
preserved in the ignored stability evidence directory. A fresh admission root,
`artifacts/phase0/model-runtime-qualification/runtime-admission-post-format-20260808`,
was attested against the formatted worker and passed a one-cycle smoke for all
three pending role candidates. The supervised replacement run at
`artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-post-format-final-20260809`
is detached under PID `9456`, with passing current cycles and no roster entry
moved from `pending_stability` to `selected`. The prior 20260808 run remains
preserved as interrupted evidence and has not been concatenated.

The Phase-1 local operational report has SHA-256
`6e8cd86017dacea7b4a0fff8e9ea41901ec4bb7ee02961f5811dcbb7266342b2` and
records zero network calls, three auditable configuration activations with
restart-persistent rollback, and byte/row-identical Bronze rebuild output. It
does not satisfy the real venue, Phase-7, or Phase-10 gates.

The latest Phase-0 component evidence report has SHA-256
`6914b9e1ba508777a3c3edd47433c5a340be06f73857ae600b84c68510fdf4b7` and
records passing local probes for the guarded Nautilus replay seam, installed
PydanticAI/Prefect/Hamilton runtime seams, deterministic Parquet manifest plus
DuckDB reads, the repository Hermes isolation boundary, and two in-memory
rclone adapter restores. It records zero network calls, credentials, or paper
orders. Real rclone/provider restore remains quarantined; the report does not
record or imply a Phase-0 pass.

## Two-provider rclone-crypt evidence

The typed archive boundary now supports independent provider A/B raw and crypt
aliases while preserving the historical singular adapter contract. The runner
[`scripts/qualify_rclone_archive.py`](../../scripts/qualify_rclone_archive.py)
uses only the `ARCHIVE_RCLONE` scope and passes a minimal process environment to
`rclone`; it never sources `secrets.env` or persists command output.

The first controlled real opt-in attempt was recorded at
`artifacts/phase0/rclone-crypt-qualification/20260810T003430.872217Z/rclone-crypt-qualification.json`.
It generated a fresh harmless source artifact, recorded source SHA-256
`ee41a072488cf8c2982d1889a037078c3e65516a23c410c168ee794188e7ba31`, found no
populated `RCLONE_CONFIG`/`RCLONE_CONFIG_PASS`/provider-pair values through the
scoped resolver, and made zero network calls. The sanitized manifest SHA-256 is
`fde44ab7ed3e0572c999b6a749f6eeeb718e39251e070939e71ad045ccfe7aed`; the
canonical evidence SHA-256 is
`fb044389dbcb9bbe52a469c9993bf8cc45d1c11c83dcdcf259e2d6d4bc5bd67b`. This is
`IMPLEMENTED / FIXTURE-TESTED / PENDING_OPERATOR_ACTION`, not real provider
measurement or qualification. The operator must populate the scoped values
locally and rerun the explicit command in the rclone archive runbook. No manual
copy/restore statement is promoted into repository admission evidence.

| Archive evidence class | Current state | Evidence truth |
|---|---|---|
| Adapter fixture-tested | `IMPLEMENTED / TESTED` | In-memory adapter and two-provider automation tests pass; no external claim |
| Real Provider A upload/restore | `PENDING_OPERATOR_ACTION` | No scoped archive values were available; no Provider A call was made |
| Real Provider B upload/restore | `PENDING_OPERATOR_ACTION` | No scoped archive values were available; no Provider B call was made |
| Independent two-provider restore | `PENDING_OPERATOR_ACTION` | The manual statement is not promoted; controlled three-way SHA evidence is absent |
| Failure/recovery qualification | `IMPLEMENTED / PENDING_EXTERNAL_EVIDENCE` | The runner contains deterministic outage/interruption/integrity drills; real survivor restores and provider reads have not run |

The isolated DuckLake comparison is recorded at
`artifacts/phase0/ducklake-comparison/20260809T162300Z/ducklake-comparison.json`
with SHA-256
`77b88992a8dfd64d47ad4da0ee73d197644bb8a21a54d3199b254f4742026154`. It
validated snapshot/time-travel, reopen, and portable-copy recovery, then
rejected DuckLake because its catalog/extension/resource footprint and explicit
relocation override did not justify a second catalog for this laptop baseline.

The pinned upstream Hermes runtime review is recorded at
`artifacts/phase0/external-hermes-review/20260809T162031Z/external-hermes-review.json`
with SHA-256
`2fcfe86c151bffe2f4c59af0f7e0e029005a4ad94675c47fc3c18348a151b51c`. It
completed one synthetic loopback coordinator/subagent task inside WSL2 user,
mount, network, and PID namespaces, measured 126,508 KiB peak RSS, and denied
non-loopback networking. It explicitly does not attest filesystem restriction,
seccomp, direct native syscalls, C-extension escapes, or a real provider route;
formal Phase 8 remains closed.

The exact-route stability runner is implemented in
`scripts/run_remote_route_stability.py` with append-only contracts in
`src/advisorai/phase0/remote_stability.py` and tests in
`tests/phase0/test_remote_stability.py`. The Novita run at
`artifacts/phase0/remote-route-stability/20260809T162800Z` is failed/quarantined
after an upstream shared-pool HTTP 429; its incident report SHA-256 is
`825e78c3cf416df52ddd1e7b51b4df7801c6bde3adee08149158602ff183a9d6`. The
DigitalOcean run at
`artifacts/phase0/remote-route-stability/20260809T173237.710604Z` recorded 62
cycles, including three immutable upstream shared-pool HTTP 429 gateway
abstentions, and was stopped and quarantined. Its incident report is at
`artifacts/phase0/remote-route-stability/20260809T173237.710604Z/incident.json`
with SHA-256
`f58eee4632a644655d6f9edd563091740799beec40d3f1048394d6d5541410ea`.
A fresh exact-route 24-hour root is required; failed samples are not
concatenated.
The superseded pre-attestation roots remain preserved: the 20260809T171000Z
root is quarantined by incident SHA-256
`302220c0b2be692de953848d7cf2b8058baceb271581a776f52f82c3d13f8677`, and the
20260809T173059.039176Z schema-label smoke is quarantined by incident SHA-256
`a3f8a51aeb5a437b1dd5c570cf86ce2cc4eb47b86e108055fcbf0b0ae34a9f8e`.

The Phase-8 capability evidence report has SHA-256
`d6e44c90574c5209bd658319637605a00269fe49fe9cad7120766ecdc2cd79e5` and
records two identical Hermes child-process outputs, enforced child socket/DNS,
read-only filesystem, conventional sensitive-path and process-environment
metadata policies, secret scrubbing, untrusted RSS content preservation, an
11-event ledger lifecycle through `active_read`, restart hydration, read-only
broker execution, and rejection of forbidden/write authority. It records zero
network calls, credentials, and paper orders. The formal Phase-8 gate remains
pending and no capability is globally admitted.
The local process policy does not attest direct native syscalls or C-extension
escapes; OS-level sandbox evidence remains an external requirement.
