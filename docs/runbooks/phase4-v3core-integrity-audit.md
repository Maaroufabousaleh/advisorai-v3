# Phase-4 V3-Core raw/normalized integrity audit

This runbook describes the offline terminal audit for a forward V3-Core
evidence generation. It is deliberately separate from collection,
normalization, prediction, and scoring. The auditor reads the evidence surfaces
and writes only a new report and, optionally, a new exclusion overlay.

It has no credential resolver, network client, account operation, order
operation, model-selection side effect, or OMS/RiskKernel dependency.

## Frozen terminal rule

The current reviewed rule is:

```text
closed_terminal_repeat_v1
```

For each `(instrument, interval_end)`:

1. retain every raw kline observation, including rows received before the
   interval closed;
2. consider an observation terminal-eligible only when
   `receipt_at >= interval_end`;
3. require the final eligible OHLCV version to repeat at least twice;
4. compare that terminal version with the first normalized canonical version;
5. classify the interval as `STABLE`,
   `REVISED_BUT_CANONICAL_FINAL`,
   `REVISED_CANONICAL_DISAGREES`, or `UNRESOLVED`.

Open-row observations are preserved for diagnosis but do not establish final
content. The rule is fixed before utility scoring and is not selected from
performance results. The active forward generation remains governed by its
original collector contract; this auditor does not retroactively change that
contract or its normalized spool.

## Terminal command

Run only after the root has been sealed. The command refuses a `running`
`status.json` unless `--allow-unsealed` is explicitly supplied for a diagnostic
read; an unsealed diagnostic is not admission evidence.

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/audit_phase4_v3core_integrity.py \
  --run-directory artifacts/phase4/v3core-forward/<sealed-root> \
  --terminal-observed-at 2026-08-22T20:00:00Z \
  --repository-commit <sealed-root-code-commit> \
  --prediction-ledger artifacts/phase4/v3core-forward-predictions/<root>/predictions.jsonl \
  --prediction-manifest artifacts/phase4/v3core-forward-predictions/<root>/manifest.json \
  --outcome-link-ledger artifacts/phase4/v3core-forward-predictions/<root>/outcome-links.jsonl \
  --output artifacts/phase4/v3core-integrity/<generation>/integrity-audit.json \
  --exclusion-output artifacts/phase4/v3core-integrity/<generation>/exclusion-overlay.json
```

The report contains raw and integrity-eligible case counts. The Phase-4
minimum is applied only to the integrity-eligible counts. A case using an
interval classified `REVISED_CANONICAL_DISAGREES` or `UNRESOLVED` is listed in
the overlay with its affected `context` or `outcome` segment. Linked
predictions remain in their original append-only ledger and are represented in
the overlay as `EXCLUDED_DATA_INTEGRITY`; no prediction or completed case is
deleted or rewritten.

The repository commit argument is required. It must be the exact code commit
used for the sealed generation and auditor review; omission or a non-SHA-1
value is refused.

The report also validates raw, source-health, and prediction hash chains,
per-symbol source-health state continuity, prediction timing and context
identity, normalized-record identity hashes, repeated values, changed OHLCV
fields, receipt timestamps, and source identity. A normalized bar's recorded
source-health state must match the latest source-health transition available at
its collection time. Hash fields are kept
semantically distinct:

- `raw_response_record_hash` identifies the raw HTTP-response ledger record;
- `raw_response_payload_sha256` identifies its response payload;
- `raw_row_content_hash` is the hash used by the collector in normalized-bar
  provenance;
- `raw_ohlcv_hash` identifies only the normalized OHLCV values;
- `normalized_record_hash` identifies the complete normalized collector record.

Raw versions are grouped by the complete raw kline row content hash, not only
by OHLCV. A provider revision to trade-count, quote-volume, or other raw-row
metadata is therefore retained as a distinct version even when the normalized
OHLCV is unchanged; `changed_ohlcv_fields` remains empty for that case.

The normalized raw-row hash must correspond to an actual raw row for the same
instrument and interval, with matching OHLCV. Duplicate normalized intervals
are invalid even when their content is identical. A terminal repeat must come
from distinct raw HTTP response sequences; duplicate rows inside one response
cannot establish stability. Receipt and response ordering are checked rather
than inferred from file order. A syntactically valid HTTP-200 response with
non-numeric or impossible OHLCV bounds is rejected fail-closed rather than
being treated as a market-data version.

The report separates `sample_minimum_met`, `integrity_ready`, and
`admission_evidence_ready`. The legacy `admission_minimum_met` field is a
fail-closed compatibility alias for the latter and is not a Phase-4 model
admission decision. An orphan normalized record, broken immutable input, bad
prediction context, or failed identity check causes the audit to fail closed
rather than inventing a preferred value.
Reports also expose `terminal_evidence_eligible`. The normal sealed workflow
sets it true; `--allow-unsealed` diagnostic reads set it false and therefore
cannot produce `admission_evidence_ready` or `admission_minimum_met`, even if
the other content checks happen to pass.
Admission-grade library calls additionally require an explicit sealed source
status, a `target_reached` status must attest `minimum_reached=true`, and an
exact auditor repository commit. The source manifest must attest the reviewed
credential-free Binance market-data-only REST surface, the frozen 5m BTC/ETH
scope, and zero order writes; normalized bars must match that manifest identity.
These checks prevent a self-consistent but substituted normalized source from
being treated as the protected V3-Core data plane.

Each prediction ledger must be paired, in argument order, with its frozen
prediction-run manifest through `--prediction-manifest`. The auditor verifies
the manifest's model identity and candidate runtime fields when present. A
missing or unverifiable prediction manifest is an explicit integrity
limitation and prevents `admission_evidence_ready`; it is never treated as
implicit identity evidence.
When prediction entries exist, each record must also carry its own
`source_snapshot_hash`; a run-level source manifest does not silently supply a
missing per-record binding. Missing per-record source identity is reported as
an integrity limitation and prevents readiness.
An admission-ready report also requires at least one non-empty prediction
ledger; a data-only audit without prospective predictions remains
`integrity_ready=false`.
When prediction entries exist, every prediction must also have exactly one
valid outcome link before `integrity_ready` or `admission_evidence_ready` can
be true. An unlinked prediction is preserved but blocks admission readiness.

Completed cases are content-bound to the audited normalized bar evidence, not
only to their own case-file hash. For every context and realized-outcome bar,
the auditor compares the normalized-record hash, raw-row identity,
source-health state, and OHLCV content against the corresponding audited
normalized record. A self-consistent case ledger with substituted bar content
therefore remains non-admissible; the report records the exact case/segment/bar
limitations and `completed_case_content_valid=false`.

## Post-outcome prediction links

Prediction records are written before outcomes and are never edited afterward.
Once the sealed root contains the completed cases, create a separate immutable
outcome-link ledger from exact `(instrument, cutoff)` matches:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/link_phase4_v3core_prediction_outcomes.py \
  --prediction-ledger artifacts/phase4/v3core-forward-predictions/<generation>/predictions.jsonl \
  --completed-cases artifacts/phase4/v3core-forward/<sealed-root>/completed-cases.jsonl \
  --output artifacts/phase4/v3core-integrity/<generation>/outcome-links.jsonl
```

The linker validates both input chains, refuses missing outcomes or duplicate
identities, uses the case's deterministic `realized_at` as `linked_at`, and
creates no output on failure. The sealed-root workflow performs this linking
automatically for a target-reached root when no outcome-link ledger was
provided; incomplete/deadline roots are preserved without attempting it.

## Sealed-root workflow

The individual auditors can be composed through the offline terminal workflow
after the collector and resource sidecar have both reached terminal states. It
refuses a `running` root before creating an output directory, requires
prediction-ledger/manifest pairing, audits the resource sidecar, and invokes
the existing materializer only when the collector minimum, integrity report,
and resource report all pass. It never invokes utility scoring:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/qualify_phase4_v3core_forward.py \
  --run-directory artifacts/phase4/v3core-forward/<sealed-root> \
  --resource-root artifacts/phase4/v3core-forward-resource/<sealed-root> \
  --preregistration artifacts/phase4/v3core-cadence-preregistration/<frozen>/phase4-v3core-cadence-preregistration.json \
  --phase3-gate-sha256 <passed-phase3-gate-sha256> \
  --terminal-observed-at 2026-08-22T20:00:00Z \
  --prediction-ledger artifacts/phase4/v3core-forward-predictions/<generation>/predictions.jsonl \
  --prediction-manifest artifacts/phase4/v3core-forward-predictions/<generation>/manifest.json \
  --outcome-link-ledger artifacts/phase4/v3core-forward-predictions/<generation>/outcome-links.jsonl \
  --output-root artifacts/phase4/v3core-terminal-review/<generation>
```

The workflow writes create-new integrity, exclusion, resource, and workflow
artifacts. A target-reached root with all checks passing is the only path that
creates the nested materialization output. A deadline or incomplete root is
audited and preserved but cannot be materialized. A refusal after output-root
creation leaves a separate `workflow-refusal.json`; source evidence remains
unchanged. The workflow is an input-preparation boundary, not a
`PhaseGateRecord`, utility result, or model-promotion mechanism.

## Integrity-aware materialization

After the root is sealed and the auditor reports
`admission_evidence_ready=true`, the existing offline forward materializer may
consume the report and its separate overlay. It filters only the case list used
by the new evaluation input; it never edits the source case or prediction
ledgers. The report and overlay must bind the exact raw, normalized, case,
prediction, outcome-link, manifest, and status hashes supplied to the command.

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/materialize_phase4_v3core_forward_input.py \
  --run-directory artifacts/phase4/v3core-forward/<sealed-root> \
  --preregistration artifacts/phase4/v3core-cadence-preregistration/<frozen>/phase4-v3core-cadence-preregistration.json \
  --phase3-gate-sha256 <passed-phase3-gate-sha256> \
  --integrity-report artifacts/phase4/v3core-integrity/<generation>/integrity-audit.json \
  --exclusion-overlay artifacts/phase4/v3core-integrity/<generation>/exclusion-overlay.json \
  --prediction-ledger artifacts/phase4/v3core-forward-predictions/<generation>/predictions.jsonl \
  --prediction-manifest artifacts/phase4/v3core-forward-predictions/<generation>/manifest.json \
  --outcome-link-ledger artifacts/phase4/v3core-forward-predictions/<generation>/outcome-links.jsonl \
  --output-root artifacts/phase4/v3core-materialized/<generation>
```

The materializer refuses an unready audit, a mismatched overlay, a hash
mismatch, an incomplete pair of integrity inputs, or an existing output root.
Its resulting metadata records raw and integrity-eligible counts and binds the
audit/overlay fingerprints and ledger hashes. It remains an input-preparation
boundary, not a PhaseGateRecord or Phase-4 admission decision.

## Terminal resource-sidecar audit

The resource sidecar is a separate append-only process/resource measurement
surface. Audit it only after its `status.json` is terminal; the auditor refuses
`state=running` and writes a new report without changing the sidecar:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/audit_phase4_v3core_resources.py \
  --resource-root artifacts/phase4/v3core-forward-resource/<sealed-root> \
  --output artifacts/phase4/v3core-resource-audit/<generation>/resource-audit.json
```

The report verifies the resource observation hash chain, process PID/start-tick
and command identity continuity, monotonic sample timestamps, terminal
status/summary bindings, resource-error absence, and the sidecar's recorded
credentials/order-write invariants. It reports maxima and observed growth for
RSS, virtual memory, CPU, threads, file descriptors, sockets, and target-root
files/bytes. It does not invent percentile estimates and does not treat a
running sidecar as terminal evidence.

## Revision-timing statistics

After sealing, revision timing can be measured independently of the integrity
classification and without selecting a future grace period:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/analyze_phase4_v3core_revision_timing.py \
  --run-directory artifacts/phase4/v3core-forward/<sealed-root> \
  --terminal-observed-at 2026-08-22T20:00:00Z \
  --output artifacts/phase4/v3core-revision-timing/<generation>.json
```

The artifact reports first post-close, first/last revision, first repeated
version, and second terminal-confirmation lags by symbol and interval. It is
marked `STATISTICS_ONLY_NO_GRACE_SELECTED`; it cannot alter the current
collector contract or make a finality/admission decision.

Every report binds the auditor module hash, optional CLI hash and repository
commit, input hashes, terminal boundary, frozen rule, and a deterministic
`audit_fingerprint`. Report and overlay paths must be new paths: the CLI uses
exclusive creation and refuses to overwrite an existing artifact or any input
evidence directory/file.

## Current revision incident

The BTCUSDT interval ending `2026-08-17T22:15:00Z` was observed with differing
closed content in the active generation. Its immutable incident evidence is
outside the active run and remains pending terminal audit. Do not use this
runbook to overwrite the canonical normalized record, extend the run, backfill
predictions, or score a potentially contaminated case.

Incident evidence SHA-256:

```text
0d6c402160d8bc09a763375f7e267a8ecd2afb2f5c1c7aa2f0e9fc90069004a5
```

## Future collector generation

The current collector's `receipt_at >= interval_end` admission rule is frozen
for its generation. A future generation may introduce a deterministic
post-close finality guard, but only after the terminal audit and a separately
reviewed provider-semantics decision. Before launching that generation, freeze
the exact grace period and record:

- observed revision timing and safety margin;
- provider close/finality semantics;
- the latest context bar's admissibility before each hourly cutoff;
- the new collector and preregistration hashes.

The future rule must use actual observed availability/admission timestamps for
the normalized record. It must not replace `interval_end` with a fabricated
collection time, and it must not be applied retroactively to an older root.

LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.
