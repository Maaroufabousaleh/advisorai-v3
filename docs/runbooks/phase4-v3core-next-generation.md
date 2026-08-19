# Phase-4 V3-Core next-generation launch contract

This runbook is for a future fresh Phase-4 generation. It does not authorize a
launch while a protected source or CUDA worker is active, and it does not
modify an existing evidence root.

## Preconditions

Launch is refused unless all of the following are true:

- the prior collector, resource sidecar, and CUDA candidate have reached their
  own terminal states and their roots are immutable;
- the GPU lease is free and the resource budget has been measured;
- the new source root, candidate root, and resource root do not exist;
- the source, preregistration, Phase-3 gate, candidate checkpoint, runner,
  preprocessing, and runtime hashes are frozen;
- the source is the credential-free
  `binance_spot_public_market_data` surface at
  `https://data-api.binance.vision/api/v3/klines` and
  `wss://data-stream.binance.vision/ws`;
- the candidate is the independently qualified, V3-Core-compatible
  `chronos-2-small` path: 48 closed five-minute bars, 30 native outputs, and
  output 12 as the one-hour forecast;
- the candidate starts before the first eligible hourly cutoff; and
- no broker credential, order capability, transfer capability, or withdrawal
  capability is present in the source or candidate configuration.

The exact offline preflight is the launch boundary. A non-zero exit is a hard
refusal:

```bash
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/preflight_phase4_v3core_generation.py \
  --mode preflight \
  --input <new-generation>/preflight-input.json \
  --output <new-generation>/preflight-report.json
```

The report must be `READY_TO_LAUNCH`. The preflight command itself performs no
network call, credential load, model load, GPU acquisition, or order operation.

## Controlled start

After the preflight passes, start the source collector, sidecar, and corrected
Chronos worker under a host-supported durable supervisor with separate logs and
explicit PIDs. The repository does not provide a supervisor; the selected host
service must preserve these rules:

1. Start the public collector and resource sidecar first.
2. Start the candidate before the first eligible cutoff, using the exact
   frozen command recorded in the generation manifest.
3. Keep collector priority above candidate inference. A resource violation
   stops or quarantines candidate inference before the collector is affected.
4. Do not automatically restart a candidate across missed cutoffs. A restart
   may create only future predictions after exact manifest identity validation;
   it may never backfill a missed cutoff.
5. A process disappearance is an incident to preserve, not permission to
   rewrite a root or silently continue it with different code.

The mandatory baseline ledger is not required for live acquisition. After the
source root seals, regenerate the five baselines offline and causally with:

```bash
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/regenerate_phase4_v3core_baselines.py \
  --input <sealed-materialized-phase4-input>/phase4-v3core-evaluation-input.json \
  --output-root <new-causal-baseline-root> \
  --repository-root <frozen-code-root> \
  --repository-commit <frozen-commit> \
  --materialized-at <post-seal-utc-timestamp>
```

Those records are explicitly `post_seal_causal_regeneration`; they must not be
described as prospective predictions.

## Read-only health and feasibility checks

Use the readiness mode only with sanitized counts from the active generation:

```bash
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/preflight_phase4_v3core_generation.py \
  --mode readiness \
  --input <generation>/readiness-input.json \
  --output <generation>/readiness-report.json
```

The readiness result is diagnostic. It does not stop processes, extend a
deadline, admit Phase 4, or substitute a baseline for the required
non-baseline candidate. The formal path remains:

```text
fresh source root
  -> source seal
  -> terminal raw/normalized integrity audit
  -> integrity exclusion overlay
  -> >=64 eligible BTC and >=64 eligible ETH
  -> complete common baseline/candidate coverage
  -> utility measurement
  -> formal Phase-4 reviewer
  -> PhaseGateRecord
```

No Phase-5, Phase-6, or Phase-7 process may start without a formal passed
Phase-4 record. LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.
