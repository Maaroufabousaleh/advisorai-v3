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
  --prediction-ledger artifacts/phase4/v3core-forward-predictions/<root>/predictions.jsonl \
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

The report also validates raw and prediction hash chains, prediction timing,
normalized-record identity hashes, repeated values, changed OHLCV fields,
receipt timestamps, raw record hashes, and the normalized record hash. An
orphan normalized record or broken immutable input causes the audit to fail
closed rather than inventing a preferred value.

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
