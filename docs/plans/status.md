# Implementation status and gate evidence

This record distinguishes implementation coverage from an architecture gate that
requires external, time-based evidence. A green unit test does not claim a 24-hour
or 60-day operational gate.

| Phase | Implementation | Automated evidence | Gate status |
|---|---|---|---|
| 0 | Harness, ports, policy-enforced model gateway, exact model acquisition, isolated/attested local runtimes, real public-data local bake-off, role roster, append-only stability runner, durable Phase-0 gate records, and scoped two-provider rclone-crypt qualification runner | `tests/phase0`, gateway/port tests, immutable local bake-off reports, component drill, isolated DuckLake comparison, pinned external Hermes review, exact-route stability runner, `tests/expansion/test_rclone.py`, `tests/config/test_secrets.py`, `tests/phase0/test_rclone_qualification.py` | Latest local component probe passed in `artifacts/phase0/component-bakeoff/20260810T000406.852454Z/phase0-component-bakeoff.json` with SHA-256 `6914b9e1ba508777a3c3edd47433c5a340be06f73857ae600b84c68510fdf4b7`; DuckLake was measured and rejected; the upstream Hermes runtime was reviewed in a disposable namespace with a synthetic route; DigitalOcean replacement roots `20260810T034500Z` and corrected `20260810T053600Z` are quarantined after immutable external route failures (HTTP 429/shared-pool capacity and deadline exhaustion); selected local roles still require 24-hour stability; the latest real archive root measured independent A/B crypt restores and equal hashes, but Provider B raw recursive enumeration failed, so archive admission remains closed |
| 1 | Contracts, PIT lake, DuckDB/Polars query, ledgers, typed V3-Core YAML admission, config rollback, resources, traces, FTS5-first memory with optional deterministic hashing recall, durable flows/incidents, and explicit service ownership/mode boundaries | contracts/data/config/recovery/resource/orchestration/memory/service tests plus the local rebuild drill | Local rollback/Bronze rebuild evidence passed in `artifacts/phase1/local-rebuild/20260808T024709.706561Z/phase1-local-rebuild.json`; provider-specific paper deployment rollback remains external |
| 2 | Paper event spool/replay, typed native market events, account and margin/borrow/FX/corporate-action accounting, durable-first account/OMS retries, signed target constraints, combined-state-hash RiskKernel/OMS binding, paper/native testnet boundary with read-only account projection, venue-projection reconciliation, TCA, cadence-gated runtime admission, Coinbase Exchange Sandbox-specific CB-ACCESS signer/schema transport, and Binance Spot Testnet-specific HMAC/schema transport | `tests/execution`, `tests/integrations`, `tests/runtime`, `tests/integrations/test_coinbase_exchange.py`, `tests/integrations/test_binance_spot.py` | Coinbase real smoke remains partial: `ETH-USD` was absent and fills returned sanitized HTTP 401. Binance public Testnet truth measured both required `BTCUSDT` and `ETHUSDT`; its adapter and private read-only runner are fixture-tested, but authenticated Binance reads and the paper lifecycle remain pending the reviewed single `PAPER_VENUE` profile. Nautilus remains Phase 0 governed despite being installed and locally tested |
| 3 | Native/Deribit/RSS/GDELT/official-vintage parsers, raw-first REST/WSS replay, typed trade/book/bar/funding/open-interest normalization, origin/revision/availability, quality monitor, and bounded real-source qualification runners with freshness measurement | `tests/data`, `tests/phase3/test_source_qualification.py`, `tests/phase3/test_coinbase_wss_qualification.py`, `tests/phase3/test_coinbase_level2_qualification.py`, `scripts/qualify_phase3_sources.py`, `scripts/qualify_phase3_coinbase_wss.py`, `scripts/qualify_phase3_coinbase_level2.py` | Real source evidence is partial: REST replay passed for Coinbase BTC-USD ticker, Deribit BTC index, and SEC official RSS; Coinbase ETH-USD returned 404 and GDELT returned 429. Two real Coinbase Sandbox WSS connections replayed 29 ticker events and 23 heartbeats with freshness passing, but both observed provider sequence gaps. The public `level2_batch` path then delivered one BTC-USD snapshot, 79 updates, and 12 heartbeats; book-state replay matched, validation passed, and freshness passed. Continuous freshness soak/recovery/disagreement evidence remains pending and no Phase-3 admission is claimed |
| 4 | Naive/statistical/LightGBM boundary, isolated ModernFinBERT/MiniLM/DeBERTa and TTM-R2/R3/TSPulse/Chronos/Kronos runtimes, calibration, GPU lease, public walk-forward/finance-sentiment measurements, and evidence-bound roster | `tests/models`, `tests/phase0`, measured local roster | Role winners are pending stability; point-in-time paper utility remains a later admission gate |
| 5 | Router, typed roles, bounded adaptive waves, evidence graph, independence gates, DecisionBundle and expiry/cutoff binding | `tests/agents`, `tests/api` | Correlated-evidence and target-only boundaries pass |
| 6 | Portfolio comparisons, risk analytics/stress, validation, attribution incidents | `tests/institutional` | Deterministic controls pass |
| 7 | Soak records/gate, incident-ledger rebuild, and immutable recovery rebuild | `tests/recovery/test_soak.py` | Requires actual 60-day paper operation and restore drills |
| 8 | Hermes policy, bounded isolation runner, enforced child socket/DNS, read-only filesystem, conventional sensitive-path and process-environment metadata policies, common process-spawn denial, sensitive-environment scrubbing, artifact/capability lifecycle and broker, and disposable Docker OS-boundary probe | `tests/capabilities`, `tests/capabilities/test_os_sandbox_probe.py`, immutable active-read capability evidence, and a disposable pinned upstream runtime review | Local Hermes-to-active-read collector lifecycle passed; a real local Docker boundary measured root-identity read-only-root denial, constrained tmpfs write, zero effective capabilities, denied unshare/mount probes, network denial, and bounded process controls at `artifacts/phase8/os-sandbox-probe/20260810T050947.907604Z/phase8-os-sandbox-probe.json` with SHA-256 `1671cd03a821a5751ff046d3732c009cb5a727b6b59d8e1bc89dc829196a7b1a`; universal native syscall/C-extension containment, credential/production-tree isolation, and a real provider route remain unattested; formal Phase-8 admission remains pending behind earlier gates |
| 9 | Vintaged official releases, equity corporate-action/daily-council boundary, challenger registry, browser ladder, archive verification | `tests/expansion` | Requires one-at-a-time live data/challenger evidence |
| 10 | Human approval, bounded live readiness, AI-offline invariant, order guard | `tests/live` | Must remain closed until Phase 7 and explicit human approval pass |
| 0–7 bridge | Typed secret loader/redaction, reviewed connector cards, HTTPS/WSS transport guards, direct typed LLM adapter, durable gateway-call records, generic paper/testnet HMAC venue transport, Coinbase Exchange Sandbox transport, Binance Spot Testnet transport with exact host guard and scoped credential binding, signed open-order reconciliation and provider-specific account/fill/position/balance projection, raw-first native market normalization/replay, cadence-gated closed-cutoff `PaperRuntime` with durable kill-switch/dashboard control hydration and terminal per-order risk rejection, durable resource measurements/leases, refreshing/deduplicated ledger-backed dashboard/config projection, incident/replay/post-horizon scorecards | `tests/config/test_secrets.py`, `tests/integrations`, `tests/runtime`, `tests/learning`, `tests/resources`, `tests/data/test_market_events.py`, `tests/api/test_dashboard.py` | Local contracts pass. Coinbase evidence is partial/external and Binance public product truth is measured; neither venue has an admitted authenticated BTC/ETH read-only gate or paper order lifecycle. Continuous operation, Phase 0 stability, Phase 7 soak, and human decisions remain external |
| Alpha Team extension | Plan-only integration for optional E0-E7 research work | None | E0 and all later gates remain closed; no Alpha Team implementation or admission evidence is claimed |

The current repository therefore has broad executable coverage, but does not claim
Phase 0, Phase 7, or Phase 10 gates without the external evidence explicitly named
above.

## Coinbase Exchange Sandbox evidence

The zero-network transition configuration check passes for
`coinbase_exchange_sandbox` at
`https://api-public.sandbox.exchange.coinbase.com` with reviewed host
`api-public.sandbox.exchange.coinbase.com`, configuration hash
`138042cd88c96e9d3079493beee740ba1e96def1ea748c361e51bd8ea88094cf`, and only
the `PAPER_VENUE` credential references bound to the adapter. The current
repository-local secrets inventory is the one used by the real,
provider-specific smoke runner:
[`scripts/smoke_coinbase_exchange_sandbox.py`](../../scripts/smoke_coinbase_exchange_sandbox.py).
No second secrets inventory is required or maintained.

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

## Binance Spot Testnet candidate evidence

Binance Spot Testnet is the selected BTC/ETH replacement candidate. The
provider-specific adapter is
[`src/advisorai/integrations/binance_spot.py`](../../src/advisorai/integrations/binance_spot.py);
it uses only the `PAPER_VENUE` resolver scope, the exact reviewed testnet REST
host, Binance's HMAC-SHA256 query signing, and the existing `NativeTransport`
boundary. The Coinbase adapter remains preserved and quarantined for the
required V3-Core execution gate; no generic exchange abstraction was added.

The credential-free public qualifier measured Binance server time and live
product truth for both `BTCUSDT` and `ETHUSDT`. Evidence is at
`artifacts/phase2/binance-spot-testnet/public-truth/20260810T165904.357047Z/binance-spot-testnet-public-truth.json`
with SHA-256
`34af4ef5649c0d0b92635507b422d7217c8a83f72156a6e2d99561e6da6d56e6`.
This is `EXTERNALLY MEASURED` public truth only. The authenticated
read-only smoke is implemented at
[`scripts/smoke_binance_spot_testnet.py`](../../scripts/smoke_binance_spot_testnet.py)
but remains `PENDING_OPERATOR_ACTION` until the one canonical repo-local
`PAPER_VENUE` profile is reviewed for Binance's exact testnet host and
restricted fake-funds credentials. No order, cancel, transfer, or withdrawal
has been attempted.

See the [Binance Spot Testnet runbook](../runbooks/binance-spot-testnet.md)
and the [venue selection decision](paper-venue-selection.md). The private
runner takes an explicit `--secrets /mnt/c/projects/advisorai-v3/secrets.env`
path, persists credential reference names only, and cannot fall back to a
production endpoint.

The complete checkpoint matrix is maintained in
[`gate-matrix.md`](gate-matrix.md). It preserves the distinction between
`TESTED`, `LOCALLY MEASURED`, `EXTERNALLY MEASURED`, `QUALIFIED`,
`QUARANTINED`, `PENDING_STABILITY`, and `PENDING_OPERATOR_ACTION`.

## Phase 3 real source evidence

The bounded read-only runner is
[`scripts/qualify_phase3_sources.py`](../../scripts/qualify_phase3_sources.py).
It uses only reviewed public HTTPS endpoints, persists raw response bytes before
parsing, replays successful records, rejects duplicate raw appends, and records
quality findings without persisting response bodies in the summary report. It
does not load credentials, call a production Coinbase host, substitute another
venue, or open any trading authority.

The latest machine-generated evidence is at
`artifacts/phase3/source-qualification/20260810T044558.818461Z/phase3-v3-core-source-qualification.json`
with evidence SHA-256
`d435e99b59d815700ccfc5d75e309632ecc91fa1aea3cd3b6c7157a02df272bf`.
Seven bounded public calls were made. The native BTC-USD ticker, Deribit BTC
index, and SEC official RSS operations passed raw-spool replay and quality
checks. The native ETH-USD operation failed with HTTP 404 because the current
Coinbase Sandbox product set has no ETH-USD market; GDELT failed with HTTP 429.
The report records those failures and remains
`EXTERNALLY MEASURED / PENDING_EXTERNAL_EVIDENCE`. Earlier runner defects are
preserved as quarantined incidents under the preceding Phase-3 evidence roots;
they are not used as gate evidence.

The separate Coinbase Sandbox WSS evidence is at
`artifacts/phase3/coinbase-wss-qualification/20260810T044142.351959Z/phase3-coinbase-wss-qualification.json`
with SHA-256
`a41fa2367a7f940e8197d5f8e0188765f9c522086091f93df988e0b2abbde702`. Both
12-second public connections received subscription acknowledgements and
replayed their ticker events from raw bytes. Freshness passed on both
connections, with maximum provider-event age 2.078 seconds and maximum
heartbeat interval 1.015 seconds. Provider sequence gaps were observed on both
connections, so this is real partial measurement, not WSS qualification or
Phase-3 admission. The WSS runner does not load credentials or create
execution authority.

The delivery-guaranteeing level-2 qualification is implemented in
[`scripts/qualify_phase3_coinbase_level2.py`](../../scripts/qualify_phase3_coinbase_level2.py)
with focused coverage in
[`tests/phase3/test_coinbase_level2_qualification.py`](../../tests/phase3/test_coinbase_level2_qualification.py).
The direct `level2` channel delivered heartbeats but no snapshot during its
bounded run and remains incomplete. The public `level2_batch` channel produced
the measured evidence at
`artifacts/phase3/coinbase-level2-qualification/20260810T052805.696329Z/phase3-coinbase-level2-qualification.json`
with SHA-256
`dc620a8fa41458fa4f89396e33687b13750461a3cd643be1b18d0588092e23de`.
It recorded one snapshot, 79 updates, 12 heartbeats, zero validation failures,
matching live/replay book-state SHA-256
`170a4fb548279355bb307404013cb10cc8e421d465b59a552dc65b5a8c1231b9`, maximum
event age 0.576 seconds, and maximum heartbeat interval 1.081 seconds. This is
bounded external source evidence only; continuous recovery, source
disagreement, and Phase-3 admission remain pending.

Latest local verification (2026-08-10) used an isolated locked environment
created with the repository's declared optional extras:
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 <verify-env>/bin/python scripts/verify_acceptance.py`
passed all eleven phase suites, with suite results of
Phase 0/1/2/3/4/5/6/7/8/9/10 = 127/152/107/44/19/34/10/7/27/18/5. Suite totals
overlap a few shared contract tests. A single-process
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 <verify-env>/bin/python -m pytest -q`
passes all 548 tests with every declared optional extra active in the isolated
locked verification environment. The acceptance runner stops at the first failed
phase, so later suites are never counted as evidence after an earlier gate
failure. The Phase 0 inventory was regenerated at
`artifacts/phase0/availability.json` (ignored runtime output), and remains an
availability record rather than an admission decision. The local static and
reproducibility checks pass for Ruff lint, dependency locking, bytecode compilation,
diff hygiene, tracked secret/model-weight checks, and the dashboard TypeScript/Vite
build. The recent scoped code changes are formatted. A repository-wide Ruff
format check passes with all 254 Python files formatted.
The dashboard build passes with `npm run build` from `dashboard/`. The complete
verification environment was isolated under `/tmp` so the active selected-model
stability worker continued using its original environment unchanged; no remote
route retry was started.

Model stability evidence is still external and pending. On 2026-08-08, a
pre-format 24-hour attempt was interrupted after the pinned worker hashes
failed closed against the finalized source; its failed/quarantined cycles are
preserved in the ignored stability evidence directory. A fresh admission root,
`artifacts/phase0/model-runtime-qualification/runtime-admission-post-format-20260808`,
was attested against the formatted worker and passed a one-cycle smoke for all
three pending role candidates. The supervised replacement run at
`artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-post-format-final-20260809`
completed with 273 passing cycles but only `23.968570833055555` elapsed hours;
its summary status is `short_smoke_complete`, not a 24-hour pass. Its summary
SHA-256 is `ec8208a4419aef1f1a85dc0d43e984feb6bb6f45b92a65fd67b1be956bad1661`.
The terminal-sample runner defect is fixed. The first fresh root
`artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-terminal-sample-20260810`
recorded seven passing cycles but exited at cycle execution with a sanitized
`FileNotFoundError` because the worker cwd was unavailable; its interruption
record and stderr-log hash are preserved and it is not resumed. A new immutable
runtime-admission root was attested, and replacement root
`artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-terminal-sample-20260810-r2`
is active under PID `40130`, started `2026-08-10T17:07:41.985884Z`, with cycle
1 passing. No cycles from any predecessor have been concatenated.

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
It generated a fresh harmless source artifact, found no populated scoped
archive values, and made zero network calls. After the operator populated the
same repo-local ignored file, the fresh root
`artifacts/phase0/rclone-crypt-qualification/20260810T152950.120379Z/`
measured independent Provider A/B crypt uploads/restores and three-way SHA-256
equality. All recovery drills passed. Provider A raw-layer enumeration passed,
but Provider B raw-layer recursive enumeration returned a sanitized provider
command failure, so this is real partial evidence rather than archive
qualification. The report SHA-256 is
`be61fd185821d2ee4b7f38c92694828f63d0b92e7e7667414e8807b1c9b0f7bf`; the
manifest SHA-256 is
`202e1564c1b56fcde7a50e2a0307cbd36a2e05771e6f308c1de51584d3ed9093`.
The runner fix applying the bounded timeout to raw listings is covered by
`tests/phase0/test_rclone_qualification.py`. The manual copy/restore statement
is still not promoted into repository admission evidence.

| Archive evidence class | Current state | Evidence truth |
|---|---|---|
| Adapter fixture-tested | `IMPLEMENTED / TESTED` | In-memory adapter and two-provider automation tests pass; no external claim |
| Real Provider A upload/restore | `EXTERNALLY MEASURED / PARTIAL` | Upload, crypt restore, three-way hash participation, and raw-layer opaque-object check passed in the latest root |
| Real Provider B upload/restore | `EXTERNALLY MEASURED / PARTIAL` | Upload, crypt restore, and three-way hash participation passed; raw-layer recursive enumeration returned a provider command failure |
| Independent two-provider restore | `EXTERNALLY MEASURED / NOT QUALIFIED` | Source SHA equaled both restored SHA values, but the required Provider B raw-layer backing check is incomplete |
| Failure/recovery qualification | `EXTERNALLY MEASURED / PARTIAL` | All listed injected and real survivor drills passed in the latest root; overall gate remains closed by Provider B raw enumeration |

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
DigitalOcean root
`artifacts/phase0/remote-route-stability/20260809T173237.710604Z` recorded 62
cycles, including three immutable upstream shared-pool HTTP 429 gateway
abstentions, and was stopped and quarantined. Its incident report SHA-256 is
`f58eee4632a644655d6f9edd563091740799beec40d3f1048394d6d5541410ea`.
The replacement root
`artifacts/phase0/remote-route-stability/20260810T034500Z` was active under PID
`13831`, recorded 11 passing cycles and then an immutable HTTP 429 gateway
abstention, and is quarantined with incident SHA-256
`805d763d69841515f7beb676ec2a0dea2e2043106dbb4dbc43b292bff4350e9f`. The
corrected runner was then started under user systemd at
`artifacts/phase0/remote-route-stability/20260810T053600Z`; its first bounded
probe ended in a sanitized `deadline_exhausted` gateway abstention, and the
root is quarantined with incident SHA-256
`5b6d5ffe9133811a664f24151b95fcd850f130cff718bc6ed1eae9289178cff1`. No
DigitalOcean route window is currently active; failed samples are not
concatenated. A future retry remains provider-availability/time-dependent.
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

The real local OS-boundary probe is recorded at
`artifacts/phase8/os-sandbox-probe/20260810T050947.907604Z/phase8-os-sandbox-probe.json`
with SHA-256
`1671cd03a821a5751ff046d3732c009cb5a727b6b59d8e1bc89dc829196a7b1a`. The
probe used a pre-existing pinned local image with the explicit local Docker
socket, network disabled, a root-identity read-only-root check, a constrained
writable tmpfs, dropped capabilities, no-new-privileges, CPU/memory/PID
ceilings, and no repository or credential mounts. The report's formal-admission
flag is false: the bounded unshare/mount probes were denied, but universal
native syscall and C-extension containment, production-tree isolation,
credential isolation, and a real Hermes capability task are still not attested.
