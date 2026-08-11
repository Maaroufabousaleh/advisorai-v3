# Implementation status and gate evidence

This record distinguishes implementation coverage from an architecture gate that
requires external, time-based evidence. A green unit test does not claim a 24-hour
or 60-day operational gate.

## Current source-scope checkpoint — 2026-08-11T23:33:32Z

Clean `main` is `27f4783171c03cf2cc606231689f7a4d22b7ca9b` after PR #177. The
Phase-3 bounded source qualifier now records whether a failed operation is an
external provider/product failure, stale or clock-uncertain provider data, or
replay/data-integrity evidence, while marking every failed path fail-closed.
It does not infer an implementation defect from an HTTP availability failure,
and it does not open a phase gate.

The fresh seven-call public source pass is at
`artifacts/phase3/source-qualification/20260811T233228.867449Z/phase3-v3-core-source-qualification.json`
with evidence SHA-256
`d6d1bbde354df87b8e4f3407e91839b5cf6dc9e10d432a2ed7557d7fd061ca73`; its
manifest carries the same evidence digest and has SHA-256
`c3c3afd53c7bfb18c282fc2e1b0bfd3f6f331631a3d2e2b1f305973938a110a8`.
Coinbase Sandbox BTC-USD, Deribit BTC index, and SEC official RSS passed
raw-spool replay and quality checks. Coinbase Sandbox ETH-USD returned
provider-truth HTTP 404 (`external_provider_product_unavailable`), while GDELT
returned HTTP 429 (`external_provider_unavailable_or_rate_limited`); both were
explicitly safe-fail-closed, with no silent substitution and no implementation
or data-integrity failure claimed. The report remains
`EXTERNALLY_MEASURED / PENDING_EXTERNAL_EVIDENCE`.

The immutable r7 public BTC/ETH source-health component remains
`QUALIFIED_FOR_REVIEW`, but the broader Phase-3 source spine still has no
passed formal `PhaseGateRecord`; therefore Phase 3 is not admitted and no real
Phase-4 utility evidence has been run. Phase-0 selected-model roles remain
qualified independently, while global Phase 0, the deferred archive gate, and
the private route remain separate. No r8 root, Binance order, production call,
or model/LLM execution authority was introduced.

## Current terminal-review checkpoint — 2026-08-11T23:17:07Z

Current executable main anchor is `d5bfde76ed3cacaba365f3d7981db5a756eaf314`
after PR #174; this review started from clean `main`
`6913f2b4feaf71f4fada05a5e9611d7601dd5e8d`.
The selected-model Phase-0 r3 terminal evidence remains unchanged and qualifies
`ttm-r2`, `finsentiment-deberta-v3`, and `finbert-minilm` independently; overall
Phase 0 remains pending its separate private-route and archive prerequisites.
PID `70598` is terminal and was not reopened or modified.
After this review-boundary change, full pytest passes `663` tests with 28
warnings; all eleven acceptance suites pass
`134/152/126/111/27/34/10/11/27/18/5`. Ruff, format, lock, compilation,
dashboard build, diff hygiene, and tracked-secret/weight checks also pass.

The protected Phase-3 r7 root
`artifacts/phase3/public-market-data-durable/20260811T182252Z-four-hour-r7-validator-fix`
completed a genuine four-hour window from
`2026-08-11T18:23:11.356614Z` through the target
`2026-08-11T22:23:11.356614Z`; its final sample ended at
`2026-08-11T22:23:45.037199Z`. It contains 129 contiguous cycles and 774
samples, with config SHA-256
`87abe16e4f16c24bd34e49381915005e0c73f826239c0412c3a81922debaf4c6`, summary
SHA-256
`8839afbdeae42cf587ae9522d2119943f8f5e431fcd4bd6a17a0d26e2449bfde`, and
runner code identity
`b928650c279502b1f759c1584769f881f6c9a7f015ba34896d18c0501463fd0b`.
Credentials and order writes were false throughout. The independent validator
review is `PASS_FOR_REVIEW` with no structural issues at
`artifacts/phase3/public-market-data-validation/20260811T230500Z-four-hour-r7-validator-fix-codex-terminal-review/phase3-qualification-validation.json`
(SHA-256
`dbd9bdc6c96af82ef33ccfbb22557786de6c9d3e72cbfdc97a956cb91c7f32e4`).

R7 recorded zero sequence gaps, duplicates, out-of-order events, and replay
failures. It recorded three Binance stale samples (two BTC and one ETH), 41
severe and two degraded disagreement observations, and 216 of 258 selections
explicitly fail-closed with zero silent substitutions. Binance BTC/ETH were
healthy at the terminal sample; Coinbase remained quarantined and Deribit
disconnect/recovery failures remained classified as external runtime/provider
evidence. The resource sidecar reached `deadline_reached` with no resource
errors across 337 observations; its maximum RSS was 391.7265625 MiB, maximum
VMS 820.6015625 MiB, maximum CPU 9%, maximum file descriptors 18, maximum
internet connections 4, and maximum target-root size 3,068,521,795 bytes.

The original admission evaluator incorrectly treated a stale interval as a
sequence/replay failure. The evaluator fix now keeps those concerns separate
and requires every stale selected source to fail closed or use an explicit
identity-bound, quality-recomputed failover. Focused Phase-3 tests pass 87;
the corrected r7 policy review is `QUALIFIED_FOR_REVIEW` with all checks true
at
`artifacts/phase3/public-market-data-admission/20260811T231500Z-four-hour-r7-validator-fix-codex-policy-review-final/phase3-admission-evaluation.json`
(SHA-256
`d4ad647e1f668b88c78604bd9cd75b94ae925bb9b943117e6adb07cfc8ae7aaa`). The
review used evaluator source SHA-256
`0ea2a57e1a4d7135d8a65bbcf87f4ea5eb288d9b131ec6a56178431d5a4d235e`.
This is a review artifact, not a rewritten r7 root. The measured public
BTC/ETH market-data component is therefore qualified for formal review; the
broader Phase-3 V3-Core source spine and formal `PhaseGateRecord` remain
pending, so no Phase-4 utility evidence has been run or claimed. Archive/rclone
remains externally deferred.

## Current terminal-review checkpoint — 2026-08-11T18:46:29Z

Clean `main` is `ced5d9301a816d89428616d1eb6ce0de48318cf7` after merged PR #171.
PR #169 corrects Phase-3 source-selection validation semantics and adds
positive/negative regression coverage; PR #171 updates both model rosters to
bind the terminal per-role results. Neither change modifies prior evidence.
Full pytest passes `660` tests with 28 warnings; all eleven acceptance suites
pass `133/152/126/108/27/34/10/11/27/18/5`, and repository quality checks
remain green.

The protected Phase-0 r3 terminal review completed at least 24 hours with
`269` cycles and `24.003634` elapsed hours. Its immutable review report is
`PASS_FOR_REVIEW` (SHA-256
`1a6ab92c4f28d456776eac0c89ab099b0c1ef579c729fa8e458e4d5192b06949`) and
qualifies each requested role independently: `ttm-r2`,
`finsentiment-deberta-v3`, and `finbert-minilm` are all `QUALIFIED`. This is
model-runtime evidence only; overall Phase 0 remains pending because the
private route and archive prerequisites are not admitted. PID `70598` was not
restarted or changed.

The protected Phase-3 r6 terminal evidence remains preserved and non-admitted.
The corrected validator audit is `PASS_FOR_REVIEW` (SHA-256
`3ac2d22f3629ff97f08e8172d1fb4aa1a3044251f32b3f0419c7fb1d1feca6d8`), while
the admission evaluator remains `phase3_admission=false` (SHA-256
`e72d0bba6ad8ec9eb204ea5f0892c6972d06362db6ceeaf8b99f6b93436d510b`) because
real Binance BTC/ETH stale intervals caused the continuity blocker. The
original r6 validator failure is preserved as a validator-defect incident;
PR #169 fixes the semantics for future roots rather than rewriting r6.

The fresh four-hour r7 qualification is active at
`artifacts/phase3/public-market-data-durable/20260811T182252Z-four-hour-r7-validator-fix`
(PID `32321`) with resource sidecar PID `32574`. It is public read-only,
credential-free, and write-free; its terminal result is pending. Phase 3 is
therefore still `PENDING_EXTERNAL_EVIDENCE`, and Phase 4–7 have not been
started or admitted. Archive/rclone remains explicitly deferred.

## Current recovery checkpoint — 2026-08-11T13:44:48Z

Clean `main` is `48d913d1ac1c78549b9d1c6115550308cacced19` after PR #167, a
docs-only live Phase-3 checkpoint. The direct Phase-3 runner entrypoint works
under a detached supervisor without an inherited `PYTHONPATH`; failed launch
roots remain preserved and quarantined. Full pytest passed `657` tests with 28
warnings; all eleven acceptance suites passed
`133/152/126/106/27/34/10/11/27/18/5`. Ruff, format, lock, compilation,
dashboard build, diff hygiene, and tracked-secret/weight checks also passed.

The prior r5 window completed truthfully with `774` samples and `6` terminal
samples. Its immutable summary SHA-256 is
`d79a00a980af649e9de228b5d56e5123fb547691f728db57821de0216de90c7c`;
validation is `PASS_FOR_REVIEW` (report SHA-256
`33919b778ca079b916ec61fba7faa6d32bf5555177cbdfc9828b4ae907da9e00`) but
admission is still pending with no healthy primary BTC/ETH source and replay/
continuity failures (admission report SHA-256
`2e38ff83eee96791010182c0997bcd6d5aacd6817fe114dc1e779c02f95e10db`).

The corrected r6 root is active under PID `2943` at
`artifacts/phase3/public-market-data-durable/20260811T124600Z-four-hour-r6-clock-confidence-setsid`,
with resource sidecar PID `4807` under the v3 monitor root. It is a fresh
four-hour run; no prior samples were concatenated. At this checkpoint it has
`192` samples and no terminal sample. All observed Binance BTC/ETH rows are
replay-equivalent and sequence-continuous; Coinbase remains explicitly
quarantined, while Deribit disconnect/recovery events and severe cross-source
disagreement remain preserved as measured fail-closed states. The separate
post-run watcher PID `28569` will write only fresh read-only
validation/evaluation roots after the protected runner and sidecar finish.
Selected-model stability PID `70598` remains untouched at cycle `218` with
terminal target `2026-08-11T18:07:25.593600Z`. Archive/rclone remains
externally deferred.

## Current repository anchor — 2026-08-11

Clean `main` is `fbb010809598d6096b42309f16b5e13dd3e1acb8`, aligned with
`origin/main`, after PR #160. The Phase-3 validator now verifies the sanitized
`latest-health.json` projection against the latest append-only samples,
including source identity, state, freshness, counters, fail-closed state, and
payload-field boundaries. This is `IMPLEMENTED / TESTED`; it does not open
Phase-3 admission. Full pytest passed `655` tests with 28 warnings and all
eleven acceptance suites passed `133/152/126/104/27/34/10/11/27/18/5`.

At `2026-08-11T11:49:14Z`, selected-model PID `70598` remained untouched and
`running` at cycle `196`, with terminal target
`2026-08-11T18:07:25.593600Z`. Phase-3 r5 PID `46864` remained untouched and
`running` at `612` samples with no terminal sample; its target is
`2026-08-11T12:35:59.156509Z`, and resource sidecar PID `47392` remains
active. The r5 post-run watcher PID `91664`, r6 controller, and model terminal
validator remain separate and waiting. Archive/rclone remains externally
deferred and untouched.

Clean `main` is `513a4fae65ddbf8f15b00eac52b8ebc390c6b5b1`, aligned with
`origin/main`, after PR #158. The attachment checkpoint naming PID `13339` and
the two-hour r3 root is stale relative to the live host; the currently
protected processes are model stability PID `70598` and Phase-3 r5 PID `46864`
with resource sidecar PID `47392`. No process was restarted or modified.

## Current continuation update — PR #158 validator hardening

PR #158 merged offline validation for the Phase-3 provider/local timestamp
projection at `513a4fae65ddbf8f15b00eac52b8ebc390c6b5b1`. Future projected roots
must contain complete timezone-aware provider and receipt timestamps with a
consistent provider-timestamp count. Existing roots are classified explicitly
as `legacy_unprojected` and are not rewritten. The change is
`IMPLEMENTED / TESTED`; full pytest passed 653 tests and no Phase-3 admission
state changed.

At `2026-08-11T11:33:35Z`, r5 remained active at 564 samples with no terminal
sample and target `2026-08-11T12:35:59.156509Z`. Model stability PID `70598`
remained untouched at 193 cycles with terminal target
`2026-08-11T18:07:25.593600Z`. Corrected r6 has not started. Archive/rclone
remains externally deferred and untouched.

## Current continuation update — PR #156 timestamp projection

PR #156 merged the Phase-3 durable sample projection at
`85724aa5f3dece9ef7eb6f51a455d9ef03f6e871`. Future roots use code identity
`f9f8c20aa33db840f1a930cc7a04f56b1c06b4a3a382130503e42462fa7e27c1` and
explicitly persist the latest provider event timestamp, latest local receipt
timestamp, and provider timestamp count for every source/symbol sample. The
change is `IMPLEMENTED / TESTED`; no Phase-3 admission state changed.

At `2026-08-11T11:22:33Z`, r5 remained active at 528 samples with no terminal
sample and target `2026-08-11T12:35:59.156509Z`. Model stability PID `70598`
remained untouched at 191 cycles with terminal target
`2026-08-11T18:07:25.593600Z`. Corrected r6 has not started. Archive/rclone
remains externally deferred and untouched.

## Current continuation update — PR #154 provider freshness evidence

PR #154 merged the Phase-3 provider-event freshness propagation fix at
`c1444364148be231585a96ced91ace223a8f989a`. Future durable source roots use
code identity
`55562365731ff8e079d3b28cdb283464134c164b54c48a35351bbea2ef3a4d47` and record
the freshness-age difference between independent sources when both provide
usable event timestamps. Missing timestamps remain unmeasured rather than
being replaced by local time. The change is `IMPLEMENTED / TESTED`; focused
Phase-3 coverage passed 33 tests, full pytest passed 650 tests, and no Phase-3
or later admission gate changed.

Read-only inspection at `2026-08-11T11:09:45Z` found r5 PID `46864` and its
sidecar PID `47392` still running with exact prior identities; the r5 root had
486 samples, no terminal sample, and target
`2026-08-11T12:35:59.156509Z`. Model stability PID `70598` remained untouched
at 188 cycles, with latest sample `2026-08-11T11:04:52.237058Z` and terminal
target `2026-08-11T18:07:25.593600Z`. Corrected-code r6 has not started.
Archive/rclone remains externally deferred and untouched.

## Current continuation update — Phase-4 offline measurement boundary

PR #152 adds the offline, fail-closed
[`scripts/run_phase4_paper_utility.py`](../../scripts/run_phase4_paper_utility.py)
runner and its
[`phase4-paper-utility.md`](../runbooks/phase4-paper-utility.md) runbook. The
runner requires a currently valid passed Phase-3 `PhaseGateRecord` and strict
typed observations/predictions, then writes immutable
`measured_pending_review` evidence. It cannot load credentials or weights,
make network calls, create a gate record, promote a model, open Phase-4
admission, or submit an order. No real Phase-4 measurement has been produced;
Phase 3 remains unadmitted.

The `2026-08-11T10:48:08Z` read-only checkpoint found model PID `70598`
running at cycle 184 and the preserved r5 public-data root running at 414
samples with no terminal marker. Their exact targets remain
`2026-08-11T18:07:25.593600Z` and `2026-08-11T12:35:59.156509Z`; no process or
evidence root was touched. PR #152 verification passed full pytest `649`
tests with 28 warnings, all eleven acceptance suites
`133/152/126/98/27/34/10/11/27/18/5`, Ruff, format, compileall, lock, dashboard,
diff, tracked-secret, and tracked-weight checks.

## Current continuation observation — protected timed roots

At `2026-08-11T09:54:45Z`, PID `70598` remained `running` with 175 passing
cycles and latest sample `2026-08-11T09:54:45.558492Z`; the 24-hour target is
`2026-08-11T18:07:25.593600Z`. The terminal sample and summary do not yet
exist, so Phase-0 remains `PENDING_STABILITY`.

At `2026-08-11T10:13:34Z`, the preserved Phase-3 r5 root had 306 samples and
48 health transitions, remained `running`, and had no terminal summary. Its
target is `2026-08-11T12:35:59.156509Z`. It remains immutable under runner
identity `f90489cf21267a748514db7ae3c72d86835044b29771d2af87dbde321511a8b8`;
Phase-3 admission remains closed pending terminal review and a fresh
corrected-code measurement if required.

## Current continuation update — provider-clock confidence hardening

PR #148 merged the Phase-3 public-data safety patch at
`3f7bfa26f0db1f15680576d09e0246525f3db8fd`. The current runner identity is
`9fc1875357437df27dfe3d0ae3a64c7e6ec957c6ba770c0569197281322ee684`; it now
propagates the measured provider server-time result into cross-source quote
confidence. Missing or out-of-policy clock evidence produces severe
fail-closed disagreement and no-trade abstention. This is
`IMPLEMENTED / TESTED` only. The active r5 root still uses its immutable
earlier identity and remains pending terminal review; no external gate was
opened.

## Current continuation update — offline model-stability validator

PR #145 merged `scripts/validate_model_stability.py` at
`f8802a8a5e4b1d605b07843e27a0d19a28d7e55f`. The validator independently checks
the immutable model cycle chain, exact terminal duration, recomputed summary,
and checkpoint/runtime admission identities, then writes a separate review
artifact with truthful per-role results. It does not alter PID `70598`, its
root, or the Phase-0 gate. Phase-0 remains `PENDING_STABILITY`; Phase-0
acceptance passed `133` tests.

## Current continuation update — Phase-3 non-negative age metrics

PR #143 merged the Phase-3 reporting fix at
`3404a0b649eeff960de0dced95efe5f7c1593bea`. Future durable roots use code
identity
`30d9fb147e6ce4a7204aaa0f2867d8c8e8e5200ec2d7a34ff2c3e037100d36eb`; signed
future-event counts remain visible as degraded clock evidence, while persisted
age durations are never negative. The active r5 root remains immutable under
its earlier code identity and needs a fresh corrected-code window after
terminal review. This is tested implementation evidence only; no gate state
changed. Full pytest passed `640` tests and the focused Phase-3 suite passed
`72`.

## Current continuation update — Phase-3 measurement boundary

PR #140 merged into main at `335114ba73156cb75e44465a4d21ff27f86299e1` with
runner code identity
`17bed912495868062c6a7a79e515d5a29a8b65b40cf138b8845e837ba3ec280d`. The
Binance public-data collector now records the end of the measured feed window
before asynchronous WebSocket close cleanup and uses that boundary for source
freshness; the later teardown timestamp remains separately recorded. This
prevents local close-time latency from being misclassified as provider staleness
without changing source identity, failover, execution isolation, or the
fail-closed policy. The no-network regression and full local verification pass.
The active r5 root remains immutable under its earlier code identity and was not
restarted or rewritten; no Phase-3 admission state changed. A fresh external
qualification root is required to measure this implementation.

## Current continuation update — r4 terminal review and r5 active root

The immutable r4 four-hour root
`artifacts/phase3/public-market-data-durable/20260811T042355Z-four-hour-r4-fixed`
completed at `2026-08-11T08:24:45.638125Z` with 810 samples and state
`multi_hour_window_complete`, but no terminal marker because its recorded
runner predates that boundary. Summary SHA-256:
`53b1b77192dc77360b63b12208a445cc889c6bd0ff570fe4bd08ef37d8753fe2`.
Its resource sidecar summary SHA-256 is
`75cc73ca44400d59df9f28037d5037ff8ca3c456c459f7d5e34ffe06e3168d47`; offline
chain validation found an invalid final sparse exit-observation hash and
`process:FileNotFoundError`. This is preserved as a quarantined operational
incident, not repaired in place.

PR #138 merged the sealing regression fix at
`1335bfabe93bdd990f9512430ae843a9795a7ebf`. A fresh four-hour root is active
under PID `46864` at
`artifacts/phase3/public-market-data-durable/20260811T083600Z-four-hour-r5-reconnect-hashfixed`
with current runner code identity
`f90489cf21267a748514db7ae3c72d86835044b29771d2af87dbde321511a8b8`; its
sidecar is PID `47392` under the matching `...-v2` resource-monitor root.
The first real cycle contains BTC/ETH public data with valid snapshot/replay
and remains fail-closed under the existing health policy. Phase-3 admission is
still pending.

## Current continuation update — bounded Binance public-data reconnect

PR #136 merged at `350d6b55ac36251750e0459dc4e24b3507ca865c`. Future durable
Phase-3 roots bind to runner code identity
`f90489cf21267a748514db7ae3c72d86835044b29771d2af87dbde321511a8b8` and make
one bounded second Binance public WSS attempt after a failed first-message or
snapshot window. The evidence retains both attempts, records reconnect counts,
and rebuilds a recovered local book only from the latest successful provider
snapshot and its own updates. This is `IMPLEMENTED / TESTED`; it does not
promote Phase 3 or change the active r4 root, which was launched under the
earlier runner identity.

## Current continuation update — Binance provider read-only recovery

PR #128 merged at `aa4cdcb86a9bd0c1ca749f0ded5524b8cb842c9c`; PR #133 is the
current executable anchor `083798403323e18f2cc6577103d7b81c36454279`, with
subsequent documentation-only follow-ups. The provider-specific Binance
recovery qualification uses only the scoped `PAPER_VENUE` resolver and the
existing Binance Spot Testnet transport. It activated a non-secret immutable
configuration revision, rolled back to the original bundle, reopened the
active pointer in a fresh subprocess, and repeated the authenticated read
contract. It never submitted, canceled, transferred, withdrew, or mutated OMS
state.

The real report is
`artifacts/phase1/binance-spot-testnet/recovery/20260811T064829.840702Z/binance-spot-testnet-recovery.json`
with SHA-256
`acf025287f717277552e3744b059dab3b2c1e35bda16f7c3db8d9eafcbe62e83`. It
passed with provider-truth `BTCUSDT`/`ETHUSDT`, 18 total read-only calls,
configuration hash
`b41638ffc13149796f29676826b54097d2e7c417d9e4b1ff4d72be6d12f87286`, and
matching initial/restored bundle hash
`0a44fe86c6cd7a65c316886f93848147aa3b75fd3a1eb3c31ae2579eaf7dc691`.
The state is `EXTERNALLY MEASURED / PROVIDER_READ_ONLY_RESTART_AND_CONFIG_ROLLBACK_MEASURED`
with `admission=NOT_ADMITTED`; full provider deployment rollback, open-order
recovery, Bronze rebuild, archive restore, and later gates remain pending.
Locked verification for the merged runtime work passed full pytest `634 passed`
with 28 warnings and all eleven acceptance suites
`129/152/126/90/24/34/10/11/27/18/5`; Ruff, format, lock, compilation,
dashboard build, diff, tracked-secret, and tracked-weight checks also passed.

PR #133 then fixed direct execution of the offline Phase-3 admission evaluator
and added a zero-network subprocess help regression. The executable main
anchor is now `083798403323e18f2cc6577103d7b81c36454279`; no admission logic or
evidence root changed. Locked verification passed full pytest `635 passed`
with 28 warnings and acceptance suites
`129/152/126/91/24/34/10/11/27/18/5`; all repository quality checks passed.
The fixed evaluator also produced the immutable offline recheck
`artifacts/phase3/public-market-data-admission/20260811T072500Z-two-hour-r3-entrypoint-recheck/phase3-admission-evaluation.json`
with SHA-256
`8c308ec39497ef962ea9dcb8fbbea611797bb2f0a488d08585923ae2fe7d131f`;
it remains pending with the existing three blockers and
`phase3_admission=false`.

## Current active timed roots

The selected-model stability root
`artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-terminal-sample-20260810-r3`
remains untouched under PID `70598`. Its config started at
`2026-08-10T18:07:25.593600Z`; the latest read-only status had 144 passing
cycles at `2026-08-11T07:04:06.519724Z`, and no terminal `summary.json` exists.
It remains `PENDING_STABILITY`.

The independent Phase-3 root
`artifacts/phase3/public-market-data-durable/20260811T042355Z-four-hour-r4-fixed`
remains untouched under PID `87421` toward
`2026-08-11T08:24:40.271709Z`; its read-only status had 570 samples, 2,126
valid events, 41 successful connections, 529 disconnects, 177 snapshot
recovery attempts, and `phase3_admission_opened=false` at
`2026-08-11T07:05:35.082784Z`. Its resource sidecar remains PID `88019`.
The root is still running and must be judged only from its own terminal
evidence.

## Current continuation update — Phase-3 source identity integrity

Candidate commit `4abf2ce` hardens `SourceHealthLedger`: a source/symbol stream
must retain its provider identity and endpoint, and each transition must declare
the state recorded immediately before it. Focused Phase-3 coverage passes `64`
tests with one FastAPI-dependent dashboard test skipped only because the default
`.venv` does not contain FastAPI. The active four-hour r4 process (PID `87421`),
its resource sidecar (PID `88019`), and selected-model stability process (PID
`70598`) were read-only inspected and not changed. No external or timed gate was
opened; Phase-3 remains pending terminal operational evidence.

## Current continuation update — post-PR #125 anchor

Clean `main` is now `3d3242cd07d55b2099b247b1d593a1701685f829` after PR #125.
The source-health identity/state-chain enforcement is merged and verified; it
does not alter the in-flight evidence roots. At the latest read-only check,
selected-model r3 PID `70598` remained running at cycle `137`, while Phase-3 r4
PID `87421` remained running at cycle `73` with `438` samples toward its
`2026-08-11T08:24:40.271709Z` terminal sample. Resource sidecar PID `88019`
remained active. No summary, terminal marker, or admission record exists yet.

## Current continuation update — Phase-3 resumable configuration bounds

Candidate commit `49b3283` closes a restart-integrity gap: the durable runner
now rejects a resumed root when its persisted `max_cycles` differs, and a
no-network test proves same-configuration hydration does not duplicate
append-only samples or health transitions. The focused Phase-3 suite passes
`66` tests. This does not alter the active r4 root or open any external/timed
gate.

## Current continuation update — post-PR #120 Phase-3 review evidence

Current clean `main` is `22370e95a85b0cffbf104e867ac59dee5ac4c2f6` after PR #122;
the source implementation anchor remains PR #120.
The complete Phase-3 acceptance coverage correction is merged; all eleven
acceptance suites remain green at `129/152/126/87/24/34/10/11/27/18/5`, and
locked full pytest remains `626 passed` with 28 warnings.

The hardened validator was rerun offline against the immutable two-hour r3 root
at `artifacts/phase3/public-market-data-durable/20260811T011500Z-two-hour-r3`.
The fresh report is
`artifacts/phase3/public-market-data-validation/20260811T060000Z-two-hour-r3-v3/phase3-qualification-validation.json`
with SHA-256
`40b08077112092df4531175063d8c514ab58a65d31c317bb562e0d14ad8f1753`.
It is `PASS_FOR_REVIEW`, has no validator issues, and records 378 samples,
126 fail-closed selections, zero silent substitutions, and no failure-label
fields because the immutable r3 runner predates that projection.

The corresponding fresh offline admission review is
`artifacts/phase3/public-market-data-admission/20260811T060000Z-two-hour-r3-v3/phase3-admission-evaluation.json`
with SHA-256
`26ae8d2e7a209b71ce36fb1707a3183dff0840f14220d7df617874a1e8a80a26`.
It remains `PENDING_EXTERNAL_EVIDENCE` with `phase3_admission=false`; its
blockers are the pre-terminal-marker qualification window, absence of a healthy
BTC/ETH primary, and primary snapshot/sequence/replay failure. No admission
was opened.

The active four-hour r4 public-data process remains PID `87421` with resource
sidecar PID `88019`; the selected-model stability process remains PID `70598`.
Both are untouched and must be evaluated only from their own immutable terminal
evidence. Archive/rclone remains externally deferred.

## Current continuation update — active Phase-3 r4 window

Read-only observation at `2026-08-11T06:11:35Z` confirms that the active
four-hour root
`artifacts/phase3/public-market-data-durable/20260811T042355Z-four-hour-r4-fixed`
is still running under PID `87421`, with target end
`2026-08-11T08:24:40.271709Z`. Its recorded code identity is
`c45b6e6ae3417cb7555d726c819a7835b05e9b76d3c58fe7c99c4de0e0e4795b`.
It has 384 samples, 384 connection attempts and disconnects, 128 snapshot
recovery attempts, zero successful connections, zero valid events, and all six
BTC/ETH source pairs are fail-closed (`DISCONNECTED` or `QUARANTINED`). This is
an in-flight operational failure observation, not a terminal qualification or
Phase-3 admission.

The separate sidecar
`artifacts/phase3/public-market-data-resource-monitor/20260811T042355Z-four-hour-r4-fixed-v2`
is still running under PID `88019`; its latest observation at
`2026-08-11T06:11:27.619492Z` recorded 239.37890625 MiB RSS, 1.8% CPU, four
file descriptors, zero internet connections, and no resource error. Neither
process has been restarted or modified.

## Current continuation update — Phase-4 utility preparation boundary

The Phase-4 preparation work in PR #114 is based on clean-main anchor
`dea0d4e832143d9ae1ab6515a255e25ee1377b3f`. It adds the strict offline
[`src/advisorai/phase4/paper_utility.py`](../../src/advisorai/phase4/paper_utility.py)
contract and
[`scripts/prepare_phase4_utility_evaluation.py`](../../scripts/prepare_phase4_utility_evaluation.py).
The boundary requires an immutable Phase-3 gate hash and `phase3_admitted=true`
observations before measuring utility; it records directional accuracy,
interval/confidence calibration, regime slices, turnover, spread/slippage,
fee-schedule cost, net utility, and incremental utility against the current
mandatory baseline set (`naive`, `drift`, `seasonal-7`, `linear`, `lightgbm`).

The preparation manifest is
`artifacts/phase4/utility-evaluation-preparation/20260811T051344.190783212Z-offline-contract-v1/phase4-utility-preparation.json`
with SHA-256
`620f2ce32bb19aed8ce64ed0c12cddd4e0684db9f5b78add11e5b8ce6445456b`.
Its state is `ready_for_admitted_input`; `phase4_admission_opened=false`.
It made no network calls, loaded no credentials or model weights, created no
gate record, and grants no model, dashboard, or research order authority.
Real Phase-4 utility remains `PENDING_STABILITY / PENDING_EXTERNAL_EVIDENCE`
until Phase 0 and Phase 3 are genuinely admitted and real paper observations
exist. Locked verification for this work package passed full pytest `622
passed` with 28 warnings, the Phase-4/model suite `24 passed`, and the focused
utility tests `5 passed`.

## Current continuation update — Phase-3 failure-class evidence projection

PR #116 makes the durable Phase-3 runner promote only sanitized exception-class and
failure-layer identifiers from its REST/WebSocket collectors into each
source/symbol sample, summary bucket, and read-only dashboard/API snapshot. It
does not copy provider response bodies, headers, exception messages, or
credentials into the diagnostic fields. The focused regression coverage passes
`14` tests. Existing roots are immutable: the active four-hour r4 process PID
`87421` was launched with the earlier code identity and its samples remain
unchanged; their failure fields are absent because that version predates this
projection. Future roots will preserve the failure classification required for
Phase-3 review. This improves evidence completeness only; it does not open
Phase-3 admission or alter fail-closed source selection.

Locked repository verification for this continuation passed full pytest `624
passed` with 28 warnings and all eleven acceptance suites with results
`129/152/126/69/24/34/10/11/27/18/5`. Ruff, repository format, `uv lock
--check`, compilation, dashboard build, diff hygiene, tracked-secret, and
tracked-weight checks also passed. These are implementation checks only; the
active timed Phase-0 root and Phase-3 external admission remain pending.
The machine-readable model roster now points to the active r3 evidence root;
this metadata correction did not touch PID `70598` or its append-only files.

The Phase-3 validator now checks optional sanitized failure labels and reports
their coverage while preserving compatibility with older immutable roots. The
acceptance runner now includes every test under `tests/phase3` rather than the
previous incomplete explicit subset. Post-#119 verification passed full pytest
`626 passed` with 28 warnings and acceptance suites
`129/152/126/87/24/34/10/11/27/18/5`; no external gate was opened.

## Historical continuation update — terminal Phase-3 sample boundary

At the preceding checkpoint, clean `main` was
`647cb3a65b19ed088fda9ff1e86d6a25ae6139aa` after PRs #109, #110, and #112. PR #110
fixes the durable Phase-3 runner boundary exposed by the r3 review: future
windows collect one explicit cycle starting at or after the configured target,
mark its records `terminal_sample=true`, and publish
`terminal_sample_count` in the summary. The active r4 process was launched
before this fix with its recorded code hash and remains untouched; its evidence
will be judged as recorded, not retroactively upgraded.

PR #112 makes the offline evaluator require that explicit terminal marker, so
timestamp proximity alone cannot qualify a pre-fix root.

The post-PR #112 verification passed full pytest `617 passed` and all eleven
acceptance suites with results
`128/152/126/68/19/34/10/11/27/18/5`. Ruff, repository format, `uv lock
--check`, compilation, dashboard build, diff hygiene, tracked-secret, and
tracked-weight checks also passed. PID `70598` remains untouched, and
archive/rclone remains externally deferred and untouched.

## Current continuation update — Phase-3 admission review boundary

Current clean-main anchor before this work package was
`c14287ba4b84becf0356a9a36dc79c8c683791f0` after PR #108. The new offline
[`scripts/evaluate_phase3_admission.py`](../../scripts/evaluate_phase3_admission.py)
and focused
[`tests/phase3/test_phase3_admission.py`](../../tests/phase3/test_phase3_admission.py)
make the Phase-3 review boundary explicit without opening the gate. The
evaluator performs no network calls, does not mutate qualification roots, and
cannot represent a formal admission record. It checks timestamp-derived
window duration and terminal-sample presence, public/read-only separation,
reviewed source identity, all-cycle primary continuity, fail-closed source
selection/disagreement behavior, and a completed error-free resource sidecar.

The completed r3 root was evaluated at
`artifacts/phase3/public-market-data-admission/20260811T043711Z-two-hour-r3-v2/phase3-admission-evaluation.json`
with SHA-256
`cbb8ec53d793887f17ebeccab8db33a52051082cdd989ff780b7a5f854cf0c1b`.
Recommendation is `PENDING_EXTERNAL_EVIDENCE`; blockers are
`qualification_window_incomplete`, `no_healthy_primary_source_for_btc_eth`,
and `primary_snapshot_sequence_or_replay_failure`. The first blocker records
that the final sample preceded the target despite post-target process
finalization; no elapsed-time or health policy was relaxed.

An independent four-hour public-data qualification remains active under PID
`87421` at
`artifacts/phase3/public-market-data-durable/20260811T042355Z-four-hour-r4-fixed`
until `2026-08-11T08:24:40.271709Z`; its read-only OS resource sidecar is PID
`88019` at
`artifacts/phase3/public-market-data-resource-monitor/20260811T042355Z-four-hour-r4-fixed-v2`.
The selected-model stability PID `70598` remains untouched. Archive/rclone is
externally deferred and was not touched.

## Current continuation update — durable Phase-7 runner boundary

The Phase-7 implementation now includes
[`src/advisorai/soak/durable.py`](../../src/advisorai/soak/durable.py), a
restartable evidence runner that binds code/configuration/policy/model/source/
venue hashes, serializes one owner, persists fsync'd hash-chained samples, and
maintains an atomic PID/heartbeat status file. It refuses non-paper
environments, requires a real terminal sample at or beyond the configured
60-calendar-day boundary, and cannot represent Phase-7 admission. Bounded
resume/tamper/failure tests pass in
[`tests/recovery/test_durable_soak.py`](../../tests/recovery/test_durable_soak.py).
No real soak root was launched; Phase 0–6 admission prerequisites remain open.

## Current continuation update — completed Phase-3 qualification validation

PRs #86–#105 are merged on clean main
`7d68304320107c9f9382a48173193988387707f8`. PR #103 adds the offline validator
for the durable Phase-3 qualification root. The validator report is
`artifacts/phase3/public-market-data-validation/20260811T011500Z-two-hour-r3-v2/phase3-qualification-validation.json`
with SHA-256
`efa926a1f5264caf5fb5bdcfd8ca268d77f6d98aeb2d5504cbe5a90484a3b7ca`; its
state is `PASS_FOR_REVIEW`, `qualification_state=evidence_for_review_only`,
`phase3_admission=false`, and `issues=[]`.

The corrected Phase-3 root
`artifacts/phase3/public-market-data-durable/20260811T011500Z-two-hour-r3`
completed its two-hour target at `2026-08-11T03:14:39.940009Z` after 63 cycles
and 378 samples across six source/symbol pairs. Its config, status, summary,
and heartbeat SHA-256 values are respectively
`eb09ac0aa008c5a42c7e318178c79421bdf4d471b5649ddf65baa50a59f12398`,
`df8a7aa57aa95205636ce0e800882f6ccca0647b386a29488c83b7bba97ed5da`,
`eb33cb5939feb5126bef3eff210c3710a95d6fbf3d85b3433bc2ad024a191ed7`, and
`5d44ef77d3bf459f75c8141c53dbb45e6275489399d42616a1ad20ddd1fcb66`.
The run recorded maximum event age `17.584741s`, p95 event age `2.254148s`,
9.2593% downtime, 35 disconnects, 25 reconnects, 252 resubscriptions, 126
stale intervals, three snapshot-recovery attempts, 20,744 valid events, one
out-of-order event, zero sequence gaps, and zero duplicates. Final source
states were Binance BTC/ETH `STALE`, Coinbase BTC/ETH `QUARANTINED`, and
Deribit BTC/ETH `DEGRADED`. All 126 source selections failed closed, silent
substitution remained zero, disagreement was normal 104 times and severe 22
times, and three replay failures were preserved. This is real operational
evidence of deterministic safety behavior, not Phase-3 admission.

The separate corrected v2 OS-resource sidecar completed at
`artifacts/phase3/public-market-data-resource-monitor/20260811T025102Z-pid13339-v2`
with config/status/summary/observation SHA-256 values
`3202ad6c45f750a9b1c250336a0d7819cdcfa78486a6ee5bd78d645c544d3e08`,
`d8766963d45f6f0e4b49ff84205b8c4fdc02c318ee35b07eb4e7a1ea34a5371a`,
`42203ff04e875b3e1bc13a0c35dae9daa9a72e1c8be3e85892d1ccb3eeed7bbd`, and
`ee472ad80818389f359c783b370518badd1fc92aff2e09d4477ce648aa1d39bf`.
It reached `deadline_reached` with 32 observations, no resource errors, peak
RSS 360.36 MiB, peak VMS 777.59 MiB, peak CPU 2.4%, eight threads, 17 file
descriptors, four internet connections, and 573 target-root files. The first
v1 sidecar remains preserved as failed hash evidence; neither sidecar modified
the qualification root. PID `13339` and its sidecar are no longer running.

PID `70598` remains the untouched selected-model stability process at
`artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-terminal-sample-20260810-r3`.
At a read-only inspection it had sequence 107 at `2026-08-11T03:47:10.345140Z`
with record SHA-256
`c3e9e65afe59a78c80687ca19243e28cbf70f227131e4f207c1a05c8bd34b02f`; the
24-hour gate remains `PENDING_STABILITY`, and this is not the terminal sample.
It has not been stopped, restarted, modified, or concatenated with another
root.

Archive/rclone remains externally deferred and was not touched. No Phase-4–7
admission is opened from this evidence; the next legal gate is the genuine
Phase-0 terminal sample, followed by sufficient admitted Phase-3 evidence.

### Latest Phase-3 availability recheck

An independent credential-free one-cycle recheck at
`artifacts/phase3/public-market-data-durable/20260811T034114Z-one-cycle-recheck`
made six public connections, received 503 valid events, made no credentialed
or order calls, and ended `evidence_for_review_only` with
`phase3_admission_opened=false`. Its config/status/summary/heartbeat SHA-256
values are respectively
`077317f1b764db41b9bd157b7129289d6b781e20cd86cebf8112498f9cf66711`,
`187730cb97e522ecede7eaa9c6315ad84fc27301e1f82dd595e9e8ff7f6d307c`,
`698ad40af908757a398d19c6df83e4bfc50209bca541fe8b3acd6c314d6eff1e`, and
`2e0ac99f11b341cf9610a9daed84a85a5c89a25dd1d9f1d75d9072930ec9924b`.
Binance BTC/ETH again ended `STALE` with last-event ages `5.096588s` and
`5.011760s` against the immutable 5-second policy, despite adjusted event-age
maxima below zero; Coinbase remained `QUARANTINED`, Deribit `DEGRADED`, and no
source substitution or write occurred. This corroborates the full-window
blocker and does not open Phase 3.

## Prior continuation update — durable Phase-3 source qualification

PRs #89–#101 are merged on clean main
`af7a31b95d48545ac62a9b7ac54bd59ca42138dd`. PRs #89–#94 add the restartable
append-only runner, deterministic source-health/failover/recovery controls, a
read-only dashboard projection, bounded Binance recovery snapshots, concurrent
BTC/ETH collection, and accurate disconnect/reconnect accounting. Public
connectors load no credentials and have no order/write method; PRs #95–#96
refresh the checkpoint and verification records only; PR #98 adds the separate
OS-resource evidence monitor and PR #101 fixes its WSL process identity guard.

The original two-hour root
`artifacts/phase3/public-market-data-durable/20260810T231500Z-two-hour-r2`
completed at `2026-08-11T01:13:21.437160Z` with summary SHA-256
`96aac309e23df24e090b97a99127b33d4dbb90e9b593cf76d909ef43e65f0283`.
Independent validation reloaded all five append-only logs, verified 336 samples
across 56 cycles, and passed the deterministic gap/recovery drills. Because
that root used the pre-fix sequential symbol collection and metrics, its
observed Binance BTC/ETH health remained fail-closed and its result is
`evidence_for_review_only`; it did not open Phase-3 admission.

A fresh corrected two-hour qualification is now active under PID `13339` at
`artifacts/phase3/public-market-data-durable/20260811T011500Z-two-hour-r3`.
Its immutable config records code SHA-256
`c45b6e6ae3417cb7555d726c819a7835b05e9b76d3c58fe7c99c4de0e0e4795b`, bounded
Binance snapshot limit `100`, `credentials_loaded=false`, and
`order_writes_attempted=false`; it started at `2026-08-11T01:14:37.205719Z`
and targets `2026-08-11T03:14:37.205719Z`. Its first concurrent Binance sample
records zero disconnects but remains fail-closed on measured stale age and
degraded clock confidence. This is `IMPLEMENTED / TESTED / EXTERNALLY MEASURED
/ RUNNING`, not Phase-3 admission.

The first v1 systemd sidecar attempt is preserved at
`artifacts/phase3/public-market-data-resource-monitor/20260811T023624Z-pid13339-systemd`;
its datetime hash canonicalization failed reload validation and it is not used
as a pass. The corrected v2 sidecar observes PID `13339` without modifying the
qualification root at
`artifacts/phase3/public-market-data-resource-monitor/20260811T025102Z-pid13339-v2`
with config SHA-256 `3202ad6c45f750a9b1c250336a0d7819cdcfa78486a6ee5bd78d645c544d3e08`;
the service is `advisorai-phase3-resource-monitor-20260811T025103Z-v2.service`.
It records sanitized RSS/CPU/thread/fd/socket and target-root growth metrics;
credentials and order writes remain false. This is resource evidence, not an
admission record.

PID `70598` remains the untouched selected-model stability process. Its latest
read-only sample was sequence 81 at `2026-08-11T01:26:03.661578Z`, record
SHA-256 `cd5525a4c0fe993999708fa10d0736623045604765e025a43e169340610c89fe`;
the 24-hour gate remains `PENDING_STABILITY`.

| Phase | Implementation | Automated evidence | Gate status |
|---|---|---|---|
| 0 | Harness, ports, policy-enforced model gateway, exact model acquisition, isolated/attested local runtimes, real public-data local bake-off, role roster, append-only stability runner, durable Phase-0 gate records, and scoped two-provider rclone-crypt qualification runner | `tests/phase0`, gateway/port tests, immutable local bake-off reports, component drill, isolated DuckLake comparison, pinned external Hermes review, exact-route stability runner, `tests/expansion/test_rclone.py`, `tests/config/test_secrets.py`, `tests/phase0/test_rclone_qualification.py` | Latest local component probe passed in `artifacts/phase0/component-bakeoff/20260810T000406.852454Z/phase0-component-bakeoff.json` with SHA-256 `6914b9e1ba508777a3c3edd47433c5a340be06f73857ae600b84c68510fdf4b7`; DuckLake was measured and rejected; the upstream Hermes runtime was reviewed in a disposable namespace with a synthetic route; DigitalOcean replacement roots `20260810T034500Z` and corrected `20260810T053600Z` are quarantined after immutable external route failures (HTTP 429/shared-pool capacity and deadline exhaustion); the selected local roles are now bound to the 24-hour r3 terminal review (`1a6ab92c4f28d456776eac0c89ab099b0c1ef579c729fa8e458e4d5192b06949`), while the latest real archive root measured independent A/B crypt restores and equal hashes but Provider B raw recursive enumeration failed, so overall Phase-0 admission remains closed |
| 1 | Contracts, PIT lake, DuckDB/Polars query, ledgers, typed V3-Core YAML admission, config rollback, resources, traces, FTS5-first memory with optional deterministic hashing recall, durable flows/incidents, and explicit service ownership/mode boundaries | contracts/data/config/recovery/resource/orchestration/memory/service tests plus the local rebuild drill | Local rollback/Bronze rebuild evidence passed in `artifacts/phase1/local-rebuild/20260808T024709.706561Z/phase1-local-rebuild.json`; provider-specific paper deployment rollback remains external |
| 1 provider recovery | Scoped Binance Spot Testnet read-only restart/configuration rollback qualification | `scripts/qualify_binance_spot_testnet_recovery.py`, `tests/recovery/test_binance_readonly_recovery.py`, `tests/recovery/test_config_bundles.py`, `tests/integrations/test_binance_spot.py` | Real report `artifacts/phase1/binance-spot-testnet/recovery/20260811T064829.840702Z/binance-spot-testnet-recovery.json` (SHA-256 `acf025287f717277552e3744b059dab3b2c1e35bda16f7c3db8d9eafcbe62e83`) passed two authenticated read projections, immutable bundle rollback/reopen, and fresh subprocess hydration with 18 read-only calls and zero writes | EXTERNALLY MEASURED / PARTIAL / NOT ADMITTED; full paper deployment rollback, open-order/in-flight recovery, Bronze rebuild, and archive restore remain pending |
| 2 | Paper event spool/replay, typed native market events, account and margin/borrow/FX/corporate-action accounting, durable-first account/OMS retries, signed target constraints, combined-state-hash RiskKernel/OMS binding, paper/native testnet boundary with read-only account projection, venue-projection reconciliation, TCA, cadence-gated runtime admission, Coinbase Exchange Sandbox-specific CB-ACCESS signer/schema transport, and Binance Spot Testnet-specific HMAC/schema transport | `tests/execution`, `tests/integrations`, `tests/runtime`, `tests/integrations/test_coinbase_exchange.py`, `tests/integrations/test_binance_spot.py`, `tests/integrations/test_binance_spot_lifecycle.py`, `tests/integrations/test_paper_venue_bakeoff.py`, `scripts/qualify_paper_venue_candidates.py`, `scripts/qualify_binance_spot_testnet_lifecycle.py` | Coinbase real smoke remains partial: `ETH-USD` was absent and fills returned sanitized HTTP 401. Binance authenticated read-only evidence passed all required operations and one supervised fake-funds `LIMIT_MAKER` lifecycle passed through RiskKernel → OMS → provider transport → reconciliation. The real path observed no fill, so fill ingestion is fixture-tested and the measured lifecycle is qualified for the no-fill/cancel path; Phase 0 stability, longer source operation, and later paper/soak gates remain open. Nautilus remains Phase 0 governed despite being installed and locally tested |
| 3 | Native/Deribit/RSS/GDELT/official-vintage parsers, raw-first REST/WSS replay, typed trade/book/bar/funding/open-interest normalization, origin/revision/availability, quality monitor, and bounded real-source qualification runners with freshness measurement | `tests/data`, `tests/phase3/test_source_qualification.py`, `tests/phase3/test_coinbase_wss_qualification.py`, `tests/phase3/test_coinbase_level2_qualification.py`, `scripts/qualify_phase3_sources.py`, `scripts/qualify_phase3_coinbase_wss.py`, `scripts/qualify_phase3_coinbase_level2.py` | Real source evidence is partial: REST replay passed for Coinbase BTC-USD ticker, Deribit BTC index, and SEC official RSS; Coinbase ETH-USD returned 404 and GDELT returned 429. Two real Coinbase Sandbox WSS connections replayed 29 ticker events and 23 heartbeats with freshness passing, but both observed provider sequence gaps. The public `level2_batch` path then delivered one BTC-USD snapshot, 79 updates, and 12 heartbeats; book-state replay matched, validation passed, and freshness passed. The completed durable root below adds two-hour source-health, recovery, disagreement, failover, and resource evidence, but final Binance staleness, Coinbase quarantine, Deribit degradation, replay failures, and severe disagreement keep Phase-3 admission closed |
| 3 current evidence addendum | Binance Spot Testnet raw depth snapshot/update qualifier, WSS layer diagnosis, and reconnect observations | `tests/phase3/test_binance_depth_qualification.py`, `tests/phase3/test_binance_wss_diagnostic.py`, `scripts/qualify_phase3_binance_spot_testnet_depth.py`, `scripts/qualify_phase3_binance_wss_diagnostic.py` | The qualifier now has fixture-tested bounded provider/local clock-offset measurement and retains raw future-event counts. The latest root `artifacts/phase3/binance-spot-testnet-depth/20260810T211531.293435Z/phase3-binance-spot-testnet-depth.json` (SHA-256 `f75f4e25ba48d923df4cba4e29d7ccf4b45e7382a05b5f63bb3a500b8b59fcde`) captured ETH events with live/replay equality, while BTC and other streams failed closed on WSS/runtime or adjusted-future freshness. The layer diagnostic `artifacts/phase3/binance-wss-diagnostic/20260810T203747.511668Z/phase3-binance-wss-diagnostic.json` (SHA-256 `8690b776e6e4237de9f4fe5ff775eb4da1cb7e16efbd11e2c3bd1fd5f2789e1b`) separately proved DNS/TCP/TLS and classified the remaining issue as intermittent connection timeout; it is not labeled provider-unavailable. Phase-3 remains pending longer freshness, recovery, and independent source disagreement |
| 3 public market-data separation | Credential-free public source cards and raw-first source-selection runner | `src/advisorai/collectors/public_market_data.py`, `tests/data/test_public_market_data.py`, `scripts/qualify_phase3_public_market_data.py` | v2 public Binance primary candidate selected from real product/book/trade/server-time/WSS evidence for BTCUSDT and ETHUSDT at `artifacts/phase3/public-market-data-qualification/20260810T211233.301638Z/phase3-public-market-data-qualification.json` (SHA-256 `14df66c9cb142598c0cca98d653af2896bb08c6faea2dc6c7221ed71d5a51c41`). It completed four full read-only windows, two reconnects per symbol, adjusted freshness after clock-offset correction, and real Coinbase-vs-Binance BTC/ETH top-of-book observations. The connector is read-only, credential-free, and separate from execution; Coinbase remains unselected due incomplete product minimum-quantity fields and one adjusted-future session, while Deribit is context-only | EXTERNALLY MEASURED / PARTIAL / PENDING_EXTERNAL_EVIDENCE; no continuous source admission is claimed |
| 3 durable qualification | Restartable append-only runner, typed source-health state machine, provider-truth recovery, disagreement policy, explicit failover/fail-closed selection, read-only dashboard projection, offline validator, and separate OS-resource sidecar | `scripts/run_phase3_public_data_qualification.py`, `scripts/validate_phase3_public_data_qualification.py`, `scripts/monitor_phase3_process_resources.py`, `tests/phase3/test_source_health_controls.py`, `tests/phase3/test_phase3_qualification_validation.py`, `tests/phase3/test_phase3_resource_monitor.py` | Completed root `artifacts/phase3/public-market-data-durable/20260811T011500Z-two-hour-r3` produced 63 cycles/378 samples, 20,744 valid events, 35 disconnects, 25 reconnects, 252 resubscriptions, three snapshot-recovery attempts, zero gaps/duplicates, one out-of-order event, and three replay failures. All 126 selections failed closed with zero silent substitutions; 22 severe disagreement observations and final stale/quarantined/degraded source states were preserved. Validation report `artifacts/phase3/public-market-data-validation/20260811T011500Z-two-hour-r3-v2/phase3-qualification-validation.json` has SHA-256 `efa926a1f5264caf5fb5bdcfd8ca268d77f6d98aeb2d5504cbe5a90484a3b7ca` and is `PASS_FOR_REVIEW`, not admission. Corrected resource sidecar reached `deadline_reached` with no resource errors; its summary SHA-256 is `42203ff04e875b3e1bc13a0c35dae9daa9a72e1c8be3e85892d1ccb3eeed7bbd` | IMPLEMENTED / TESTED / EXTERNALLY MEASURED / QUALIFIED FOR REVIEW / NOT ADMITTED; preserve the immutable roots, keep source fail-closed behavior, and wait for Phase-0 stability plus the remaining Phase-3 admission criteria |
| 3 current availability addendum | Post-change Binance Spot Testnet depth qualifier | `scripts/qualify_phase3_binance_spot_testnet_depth.py` and existing depth tests | The clock-offset and fault-drill code remains fixture-tested | Post-offset root `artifacts/phase3/binance-spot-testnet-depth/20260810T185425.534127Z/phase3-binance-spot-testnet-depth.json` (SHA-256 `daee289fd1373477c5c22f4b792ff4e07b452c93e4544e21f757dde7080e9831`) used one BTCUSDT and one ETHUSDT connection, failed closed before first message with `WebSocketTransportError`, made zero REST calls, and captured no raw messages; deterministic drills passed | no | provider/runtime WSS availability and longer recovery window remain unmeasured | EXTERNALLY MEASURED / PENDING_EXTERNAL_EVIDENCE | no post-offset live WSS message has been obtained; no freshness or reconnect claim is made | Preserve this root; retry only after availability is reviewed and keep any new run independent |
| 4 | Naive/statistical/LightGBM boundary, isolated ModernFinBERT/MiniLM/DeBERTa and TTM-R2/R3/TSPulse/Chronos/Kronos runtimes, calibration, GPU lease, public walk-forward/finance-sentiment measurements, and evidence-bound roster | `tests/models`, `tests/phase0`, measured local roster | TTM-R2, Finance DeBERTa-v3, and FinBERT-MiniLM have independent 24-hour `QUALIFIED` role reviews bound to the roster; point-in-time paper utility remains a later admission gate |
| 5 | Router, typed roles, bounded adaptive waves, evidence graph, independence gates, DecisionBundle and expiry/cutoff binding | `tests/agents`, `tests/api` | Correlated-evidence and target-only boundaries pass |
| 6 | Portfolio comparisons, risk analytics/stress, validation, attribution incidents | `tests/institutional` | Deterministic controls pass |
| 7 | Soak records/gate, incident-ledger rebuild, and immutable recovery rebuild | `tests/recovery/test_soak.py` | Requires actual 60-day paper operation and restore drills |
| 8 | Hermes policy, bounded isolation runner, enforced child socket/DNS, read-only filesystem, conventional sensitive-path and process-environment metadata policies, common process-spawn denial, sensitive-environment scrubbing, artifact/capability lifecycle and broker, and disposable Docker OS-boundary probe | `tests/capabilities`, `tests/capabilities/test_os_sandbox_probe.py`, immutable active-read capability evidence, and a disposable pinned upstream runtime review | Local Hermes-to-active-read collector lifecycle passed; a real local Docker boundary measured root-identity read-only-root denial, constrained tmpfs write, zero effective capabilities, denied unshare/mount probes, network denial, and bounded process controls at `artifacts/phase8/os-sandbox-probe/20260810T050947.907604Z/phase8-os-sandbox-probe.json` with SHA-256 `1671cd03a821a5751ff046d3732c009cb5a727b6b59d8e1bc89dc829196a7b1a`; universal native syscall/C-extension containment, credential/production-tree isolation, and a real provider route remain unattested; formal Phase-8 admission remains pending behind earlier gates |
| 9 | Vintaged official releases, equity corporate-action/daily-council boundary, challenger registry, browser ladder, archive verification | `tests/expansion` | Requires one-at-a-time live data/challenger evidence |
| 10 | Human approval, bounded live readiness, AI-offline invariant, order guard | `tests/live` | Must remain closed until Phase 7 and explicit human approval pass |
| 0–7 bridge | Typed secret loader/redaction, reviewed connector cards, HTTPS/WSS transport guards, direct typed LLM adapter, durable gateway-call records, generic paper/testnet HMAC venue transport, Coinbase Exchange Sandbox transport, Binance Spot Testnet transport with exact host guard and scoped credential binding, signed open-order reconciliation and provider-specific account/fill/position/balance projection, raw-first native market normalization/replay, cadence-gated closed-cutoff `PaperRuntime` with durable kill-switch/dashboard control hydration and terminal per-order risk rejection, durable resource measurements/leases, refreshing/deduplicated ledger-backed dashboard/config projection, incident/replay/post-horizon scorecards | `tests/config/test_secrets.py`, `tests/integrations`, `tests/integrations/test_binance_spot_lifecycle.py`, `tests/runtime`, `tests/learning`, `tests/resources`, `tests/data/test_market_events.py`, `tests/api/test_dashboard.py` | Local contracts pass. Binance authenticated read-only and one supervised no-fill/cancel paper path are externally measured and qualified; Coinbase evidence remains partial. Continuous source operation, Phase 0 stability, Phase 7 soak, and human decisions remain external |
| Alpha Team extension | Plan-only integration for optional E0-E7 research work | None | E0 and all later gates remain closed; no Alpha Team implementation or admission evidence is claimed |

The current repository therefore has broad executable coverage, but does not claim
Phase 0, Phase 7, or Phase 10 gates without the external evidence explicitly named
above.

## Latest Binance Spot Testnet depth availability evidence

A further bounded 20-second retry at
`artifacts/phase3/binance-spot-testnet-depth/20260810T201946.533716Z/phase3-binance-spot-testnet-depth.json`
failed closed before the first WSS message on all four connections, made zero
REST calls, and passed all deterministic drills. Its immutable evidence
SHA-256 is
`ce402b7bdd67513c90b1cc5bf744d0a8d455a6f1b7f927610a84f997699b8415`.
This confirms provider/runtime unavailability remains the blocker; it is not a
freshness, reconnect, or Phase-3 admission pass.

## Phase-3 WSS layer diagnosis and public market-data separation

The credential-free Binance Testnet layer diagnostic is preserved at
`artifacts/phase3/binance-wss-diagnostic/20260810T203747.511668Z/phase3-binance-wss-diagnostic.json`
with SHA-256
`8690b776e6e4237de9f4fe5ff775eb4da1cb7e16efbd11e2c3bd1fd5f2789e1b`.
DNS, TCP, and TLS passed in the locked transition runtime. Successful direct
BTC/ETH attempts received public messages and valid subscriptions were
acknowledged; BTC reconnect passed, while one ETH connection timed out. The
sanitized classification is `websocket_connection_timeout`, not a generic
provider-unavailable claim. The earlier `.venv` missing-library diagnostic is
separate and immutable.

The corrected depth evidence at
`artifacts/phase3/binance-spot-testnet-depth/20260810T211531.293435Z/phase3-binance-spot-testnet-depth.json`
has SHA-256
`f75f4e25ba48d923df4cba4e29d7ccf4b45e7382a05b5f63bb3a500b8b59fcde`.
An ETH stream was replay-equivalent; BTC and other streams include preserved
WSS/runtime or adjusted-future fail-closed results. This is partial source
evidence only.

The credential-free public market-data bake-off selected Binance public
BTCUSDT/ETHUSDT as the current primary candidate at
`artifacts/phase3/public-market-data-qualification/20260810T211233.301638Z/phase3-public-market-data-qualification.json`
with SHA-256
`14df66c9cb142598c0cca98d653af2896bb08c6faea2dc6c7221ed71d5a51c41`.
It passed public product/filter, book, trade, server-time, four full WSS
windows, two reconnects per symbol, adjusted freshness, and cross-source
top-of-book checks without credentials or write methods. The source card is separate from
Binance Spot Testnet execution. Coinbase public data remains unselected due
incomplete minimum-quantity fields; Deribit remains context-only. Continuous
freshness, recovery, disagreement, and explicit failover evidence remain open.

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
This is `EXTERNALLY MEASURED` public truth only. The fresh authenticated
read-only smoke then passed all required operations at
`artifacts/phase2/binance-spot-testnet/read-only-smoke/20260810T193840.598161Z/binance-spot-testnet-read-only-smoke.json`
with SHA-256
`c365d4042a67214a3ff1fe1f7bdca34f38e46e78bfff920146873e5ab4a80f72` and
configuration hash
`b41638ffc13149796f29676826b54097d2e7c417d9e4b1ff4d72be6d12f87286`.
After the final adapter source was settled, a second read-only-only smoke
passed the same gate at
`artifacts/phase2/binance-spot-testnet/read-only-smoke/20260810T201450.306674Z/binance-spot-testnet-read-only-smoke.json`
with SHA-256
`b3a8b54f446599b50547bab98240db0fe8e1380fd969a6a220fccac1c83fe8e7` and
adapter source SHA-256
`ec3077cc726a045420c714f99c5c2e026351190348fdc9779f96e21cff034e0d`.
It made nine read-only calls and no write; the earlier report remains the
immutable read-only record inspected before the signed lifecycle.
The supervised lifecycle runner then completed one provider-filtered,
minimum-size `LIMIT_MAKER` BTCUSDT order through the deterministic
RiskKernel → OMS chain, one signed submission, one cancellation, authoritative
reconciliation, restart hydration, TCA, and zero unexplained attribution
residuals. Its immutable report is at
`artifacts/phase2/binance-spot-testnet/paper-lifecycle/20260810T195818.312420Z/binance-spot-testnet-paper-lifecycle.json`
with SHA-256
`db52d6a3db56a742eb1b2e4dd47abe5e43884ef768c32d34dac2483f81c33c70`.
No real fill was observed; the fill-ingestion path and non-inducible failure
scenarios remain fixture-tested. This is `EXTERNALLY MEASURED / QUALIFIED /
PENDING_BASE_GATES`, not live admission.

See the [Binance Spot Testnet runbook](../runbooks/binance-spot-testnet.md)
and the [venue selection decision](paper-venue-selection.md). The private
runner takes an explicit `--secrets /mnt/c/projects/advisorai-v3/secrets.env`
path, persists credential reference names only, and cannot fall back to a
production endpoint.

The ordered credential-free candidate comparison is implemented by
[`scripts/qualify_paper_venue_candidates.py`](../../scripts/qualify_paper_venue_candidates.py).
It made no credential reads and no write calls. Binance and Bybit both passed
actual non-production server-time, BTC/ETH product/filter, order-book, and
public-trade checks. The immutable comparison is at
`artifacts/phase2/paper-venue-bakeoff/20260810T190539.057729Z/paper-venue-candidate-bakeoff.json`
with SHA-256
`78d8034c56a1b651da968129e463d73d23745a95565e1d9e80092a0bbd569b3a`.
Bybit is recorded as measured but unselected; no Bybit adapter or credentials
were added. This comparison opens neither the authenticated read-only gate nor
the paper lifecycle gate.

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

A fresh bounded retry on 2026-08-10 made seven public calls and independently
recorded the same safe outcomes at
`artifacts/phase3/source-qualification/20260810T201653.611706Z/phase3-v3-core-source-qualification.json`
with SHA-256
`60cac1ba77fa31735c87b02e29125985e9d4e69b2e592886e317b0ed61ecca01`:
Coinbase BTC-USD ticker, Deribit BTC index, and SEC RSS raw-spool replay
passed; Coinbase ETH-USD returned sanitized HTTP 404 and GDELT returned
sanitized HTTP 429. The root remains `EXTERNALLY MEASURED /
PENDING_EXTERNAL_EVIDENCE`; no source substitution or trading write occurred.

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

Latest local verification (2026-08-11 UTC, after the Phase-3 qualification-validator changes) used an isolated locked environment
created with the repository's declared optional extras:
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 <verify-env>/bin/python scripts/verify_acceptance.py`
passed all eleven phase suites, with suite results of
Phase 0/1/2/3/4/5/6/7/8/9/10 = 128/152/126/66/19/34/10/7/27/18/5. Suite totals
overlap a few shared contract tests. A single-process
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 <verify-env>/bin/python -m pytest -q`
passes all 607 tests with every declared optional extra active in the isolated
locked verification environment. The acceptance runner stops at the first failed
phase, so later suites are never counted as evidence after an earlier gate
failure. The Phase 0 inventory was regenerated at
`artifacts/phase0/availability.json` (ignored runtime output), and remains an
availability record rather than an admission decision. The local static and
reproducibility checks pass for Ruff lint, dependency locking, bytecode compilation,
diff hygiene, tracked secret/model-weight checks, and the dashboard TypeScript/Vite
build. The recent scoped code changes are formatted. A repository-wide Ruff
format check passes with all 281 Python files formatted.
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
The terminal-sample runner now resolves all startup inputs to absolute paths
and accepts an explicit `--repository-root` so a transient WSL working-directory
loss cannot terminate a long run. The first fresh root
`artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-terminal-sample-20260810`
recorded seven passing cycles but exited at cycle execution with a sanitized
`FileNotFoundError`. The r2 root recorded eight passing cycles before the same
failure; its immutable interruption record SHA-256 is
`4b1c33ba1762fcbad67ce6b9a54ed82ba7531bb6d93a2d1585c35fd20e29c5ac`. Neither
root is resumed or concatenated. A fresh immutable runtime-admission root was
attested, the one-cycle cwd-fix smoke passed with all candidates and status
`short_smoke_complete`, and replacement r3 is active under PID `70598` from
`2026-08-10T18:07:25.593600Z`; 79 cycles had passed at
`2026-08-11T01:15:16.975316Z`, the latest record hash was
`1130100d0cff6fd829d7546d147d1b7220f4d4a0e70c56672443e5c5e355c7d2`, and no
24-hour result exists yet.

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
