# Phase 0 local model-runtime qualification

This workstream qualifies local model runtimes after the reviewed
`transformers==5.5.4` / `huggingface-hub==1.26.1` migration. It is isolated
from `PolicyGateway`, secrets, broker/execution, `RiskKernel`, and OMS code.
No model weights are stored in the repository.

## Run a short smoke qualification

From the repository root:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python \
  scripts/qualify_model_runtimes.py \
  --output artifacts/phase0/model-runtime-qualification
```

The command runs the five mandatory baselines, writes one sanitized manifest
per candidate, writes a forecast-baseline comparison, and projects the results
into the existing pending `BakeoffGate`. It never downloads a checkpoint. A
missing runner, missing cache, missing artifact hash, prohibited/gated terms or
runtime, incompatible dependency, or invalid output is recorded as
`quarantined`/`failed`; it is never replaced by another model family.

Evidence is ignored local evidence under:

```text
artifacts/phase0/model-runtime-qualification/<run-id>/
```

Each run directory includes `index.json`, candidate manifests,
`bakeoff-gate.json`, and `forecast-baseline-benchmark.json`. The parent
directory contains only mutable `latest.json`/`index.json` pointers; prior run
directories are never overwritten. The run index explicitly records that a
24-hour stability run has not been performed. Treat run files as immutable:
the writer refuses to overwrite changed evidence.

## Initial pinned roster

The registry in `advisorai.phase0.runtime_qualification` records the Hub/Git
repository ID, immutable revision, complete repository file list, license,
external cache path, and explicit task role:

| Candidate | Repository | Revision | Role |
| --- | --- | --- | --- |
| ModernFinBERT | `tabularisai/ModernFinBERT` | `6c6de8332ea7f6824c0f8917358dce1e669c1710` | primary general finance sentiment candidate |
| FinBERT-MiniLM | `9mark9/finbert-minilm-sentiment` | `fdbfec0cd09610bd5af26da8998507fe7838e838` | fast CPU sentiment candidate |
| Finance DeBERTa-v3 | `anabdd/finsentiment-deberta-v3-base` | `f2312de96d6cfe6251da37afb0e99b8e29885bdd` | higher-capacity sentiment challenger |
| IBM TTM-R3 | `ibm-granite/granite-timeseries-ttm-r3` | `ea17cfd2e3edcaea21eb8dcecd18bf88971482fa` | primary lightweight forecast candidate |
| IBM TTM-R2 | `ibm-granite/granite-timeseries-ttm-r2` | `d6a79570cac0f33d526601cd3a0fc7c80a8f9a2f` | previous-generation forecast control |
| TSPulse | `ibm-granite/granite-timeseries-tspulse-r1` | `2e64fcdc2a06d3565dfadaf0065c0ab5055f80f2` | anomaly/integrity/regime features only |
| Chronos-2-small | `autogluon/chronos-2-small` | `ddec01313e50b6bc58ebaa92ede81bc24a3d9f9a` | GPU forecast challenger |
| Kronos-mini | `NeoQuasar/Kronos-mini` | `f4e68697d9d5aed55cef5c96aabc3376bcad9f81` | GPU forecast challenger |
| Kronos-small | `NeoQuasar/Kronos-small` | `901c26c1332695a2a8f243eb2f37243a37bea320` | GPU forecast challenger |
| Kronos tokenizers | `NeoQuasar/Kronos-Tokenizer-2k` / `-base` | pinned separately in registry | tokenizer pins |
| TabPFN-TS (later) | `PriorLabs/tabpfn-time-series` | `a756ae3fb3af82c903c39e1cd71864ff5252bc4d` | later GPU challenger |

External caches default to `~/.cache/advisorai-v3/models/<candidate>` and must
remain outside the repository. Before a model can be measured, any known
private-use terms must be non-blocking, and its isolated execution `RuntimePin`,
immutable lock-artifact SHA-256,
exact worker interpreter/executable hash, worker hash, and every declared
artifact SHA-256 must pass. External candidates are never imported into the
AdvisorAI core interpreter: the parent sends a sanitized JSON request to the
exact `RuntimePin` Python executable and validates the worker's reported
`sys.executable`, package versions, lock identity, environment fingerprint,
and runner identity. Worker processes set `HF_HUB_OFFLINE=1` and
`TRANSFORMERS_OFFLINE=1` and install a socket guard; any network attempt is a
failed privacy result. Runtime artifacts and provenance artifacts are
separate; unexpected loadable files (including `.py`, `.so`, `.dll`, `.sh`,
and unreviewed Pickle-style model files) are rejected. `cached_artifact_inventory()`
hashes every regular cached file for the sanitized manifest.

## Exact-revision acquisition

Acquire one active candidate at a time. The acquisition process reads only the
`MODEL_REGISTRY` credential scope, uses anonymous access when possible, and
never passes a Hub token to the offline runtime worker:

```bash
uv run python scripts/acquire_model_artifacts.py ttm-r3
```

The command downloads the reviewed file list at the frozen 40-character
revision into a temporary directory under
`~/.cache/advisorai-v3/staging/`, hashes every file, and atomically promotes an
exact clean closure to
`~/.cache/advisorai-v3/models/<candidate>/<revision>/`. It does not qualify
from a raw Hugging Face cache. Existing immutable cache content is reused only
when every file hash is identical; conflicting content is never overwritten.
Cross-host redirects do not receive the registry authorization header.

Sanitized acquisition and checkpoint-pin evidence is written beneath a new
run directory under `artifacts/phase0/model-runtime-qualification/`. Model
weights and machine caches remain outside Git and outside the repository.

AdvisorAI is currently a private, personal installation. License declarations
are retained as provenance and do not affect technical scores or selection.
Only authoritative terms that explicitly prohibit this private use, or gated
terms that require the user to accept them, block a candidate. Missing or
ambiguous metadata is recorded as `unknown` and qualification continues.
ProsusAI/finbert is no longer an active runtime candidate; ModernFinBERT
replaces it. Existing compatibility identifiers may remain for historical
evidence, but smoke and acquisition workflows do not schedule its weights.

Granite TTM-R3 Lite is described upstream as a variant within the single R3
family artifact. The current repository exposes one config/weight closure and
no separately immutable Lite checkpoint or configuration identity, so
AdvisorAI does not fabricate a separate `TTM-R3 Lite` candidate. It will be
added only if an independently reproducible upstream identity becomes
available.

## Qualification boundary

`run_runtime_qualification()` measures clean load, one inference, repeated
inference, task-typed small-batch inference, cold/warm latency, background RSS,
optional CUDA VRAM, output schema, NaN/Inf rejection, repeatability policy,
unload and post-unload recovery, offline-only execution, and resource ceilings.
GPU candidates use the existing `GpuModelLease`, so at most one GPU family is
resident at once.

`run_finbert_qualification()` uses a fixed public finance-text fixture and
requires labels from `{positive, negative, neutral}` plus normalized
`confidence`/Hugging Face `score` in `[0, 1]`. `run_tspulse_qualification()` uses the feature dataset and cannot
construct a price-forecast task. Forecast records compare naive, drift,
seasonal, linear, and LightGBM through the existing `ForecastEvaluation`
contract. One synthetic series is explicitly insufficient for superiority or
promotion claims; point-in-time AdvisorAI datasets can implement the same
versioned `BenchmarkDataset` interface later.

The short run is not a stability admission. Phase-0/Phase-7 time gates and any
model promotion remain pending until separately reviewed evidence exists. A
single synthetic forecast series cannot establish superiority. Stochastic
forecast candidates are characterized under their declared repeatability
policy rather than being incorrectly required to produce byte-identical paths.

## Real public-data bake-off (2026-08-07)

The current machine-readable result is
`configs/models/phase0_local_roster.json`. It binds the immutable report at
`artifacts/phase0/model-runtime-qualification/local-bakeoff/20260807T223542.052232Z/local-model-bakeoff.json`
with report hash
`73c8e69e8a6993c11bff64387e55ad20220be67eae007bd6f31957186a253f84`.
The forecast snapshot contains 2022–2025 daily AAPL, MSFT, NVDA, BTCUSDT,
ETHUSDT, and SOLUSDT observations. Evaluation uses 24 chronological
walk-forward cases, 512 observations of context, a 30-observation horizon, and
no future-fitted preprocessing. The sentiment snapshot is the exact
`gtfintechlab/financial_phrasebank_sentences_allagree` revision
`e0ecd7f315af02460bbb107d7588c5a6fa5df573`; the fixed evaluation subset is
balanced across 180 public phrases.

The measured role decisions are evidence-led rather than name-led:

| Role | Current candidate | Evidence state | Key result |
| --- | --- | --- | --- |
| forecast primary/fast | TTM-R2 | pending 24h stability | MASE 5.7045; lowest measured MASE |
| lightweight challenger | TTM-R3 | qualified | MASE 6.1802 |
| probabilistic forecast | Chronos-2-small | qualified | MASE 6.2321; 10–90% coverage 0.7764 |
| forecast challengers | Kronos-mini / Kronos-small | qualified | MASE 10.7781 / 17.0319 |
| feature/regime | TSPulse | qualified | six finite features over 24 cases; no price forecast |
| financial sentiment primary | finance DeBERTa-v3 | pending 24h stability | macro-F1 0.9889 |
| financial sentiment fast | FinBERT-MiniLM | pending 24h stability | macro-F1 0.9722; 120.15 items/s |
| financial sentiment challenger | ModernFinBERT | qualified | macro-F1 0.7645 |
| later forecast challenger | TabPFN-TS | waiting for user acceptance | gated upstream acquisition returned 401 |

The seasonal-7 baseline achieved MASE 5.8996, so TTM-R2 is the only external
candidate that beat the best mandatory baseline on the primary scale-normalized
metric in this snapshot. This is a model-role decision, not a profitability or
live-capital claim. ModernFinBERT remains the active ModernBERT-family
challenger, but measured AdvisorAI evidence selected DeBERTa for quality and
MiniLM for the CPU fast path. ProsusAI/finbert is inactive and was not
downloaded.

Reproduce a full short bake-off from already-acquired caches and admissions:

```bash
uv run python scripts/run_local_model_bakeoff.py \
  --forecast-snapshot ~/.cache/advisorai-v3/benchmark-data/public-daily-0f84a34fb0537ecb/forecast-snapshot.json \
  --sentiment-snapshot ~/.cache/advisorai-v3/benchmark-data/phrasebank-4a48c245f5260c96/sentiment-snapshot.json \
  --admission-root artifacts/phase0/model-runtime-qualification/runtime-admission-final-20260808T020000Z
```

## Stability runner

`scripts/run_model_stability.py` repeatedly qualifies only the pending role
candidates through their pinned isolated interpreters. It stores an fsync'd,
hash-chained JSONL log, verifies the immutable benchmark hashes, supports a
fixed run directory for safe restart, and uses the existing `StabilityWindow`
contract. A short smoke remains explicitly distinct from the 24-hour gate.

Admission manifests also pin the worker source hash. Any source or formatting
change after a manifest is frozen must fail closed: stop the old supervisor,
preserve its append-only cycles and interrupted status, attest a new admission
root with `scripts/freeze_model_runtime.py`, run a one-cycle smoke, and start a
new run directory. Never edit an old admission manifest or append new samples
to a run whose source hash no longer matches.

```bash
setsid nohup bash -c 'cd /mnt/c/projects/advisorai-v3 || exit 1; exec /mnt/c/projects/advisorai-v3/.venv/bin/python /mnt/c/projects/advisorai-v3/scripts/run_model_stability.py \
  --forecast-snapshot /home/maaro/.cache/advisorai-v3/benchmark-data/public-daily-0f84a34fb0537ecb/forecast-snapshot.json \
  --sentiment-snapshot /home/maaro/.cache/advisorai-v3/benchmark-data/phrasebank-4a48c245f5260c96/sentiment-snapshot.json \
  --report /mnt/c/projects/advisorai-v3/artifacts/phase0/model-runtime-qualification/local-bakeoff/20260807T223542.052232Z/local-model-bakeoff.json \
  --admission-root /mnt/c/projects/advisorai-v3/artifacts/phase0/model-runtime-qualification/runtime-admission-post-cwd-fix-20260810 \
  --run-directory /mnt/c/projects/advisorai-v3/artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-terminal-sample-20260810-r3 \
  --repository-root /mnt/c/projects/advisorai-v3' \
  > /mnt/c/projects/advisorai-v3/artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-terminal-sample-20260810-r3.nohup.log 2>&1 < /dev/null &
```

The first fresh root
`phase0-selected-24h-terminal-sample-20260810` recorded seven passing cycles
but exited at cycle execution with a sanitized `FileNotFoundError` because its
worker cwd was unavailable. The r2 root
`phase0-selected-24h-terminal-sample-20260810-r2` recorded eight passing
cycles before the same cwd failure; its interruption record SHA-256 is
`4b1c33ba1762fcbad67ce6b9a54ed82ba7531bb6d93a2d1585c35fd20e29c5ac`. Both
roots are preserved and must not be resumed, repaired, or concatenated. The
runner now resolves all startup inputs to absolute paths and accepts an
explicit `--repository-root`; the one-cycle cwd-fix smoke passed in
`phase0-selected-24h-cwd-fix-smoke-20260810` with all candidates measured and
status `short_smoke_complete`. The fresh real-duration replacement is active
under PID `70598` at
`phase0-selected-24h-terminal-sample-20260810-r3` using the new immutable
runtime-admission root.

As of the latest checkpoint, r3 has completed seven passing cycles. Its last
record SHA-256 is
`2daa086e41031e93a2ac268056beb54dbd655f66ac85b68e939e3b938d8b69b9`.
The 24-hour result does not yet exist; preserve the process and do not
concatenate predecessor roots.

The 24-hour result must exist and pass before changing roster entries from
`pending_stability` to `selected`. It does not approve paper execution or live
capital.

On 2026-08-08, the pre-format run was interrupted after the finalized
repository formatting made its old worker hashes invalid. Its failed cycles
remain at
`artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h`.
The fresh post-format admission root passed a one-cycle smoke for TTM-R2,
Finance DeBERTa-v3, and FinBERT-MiniLM. The supervised 24-hour replacement was
run at
`artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-post-format-final-20260809`;
all 273 cycles passed, but its terminal summary ended at
`23.968570833055555` hours because the runner did not sample at/after the
duration boundary. Its summary SHA-256 is
`ec8208a4419aef1f1a85dc0d43e984feb6bb6f45b92a65fd67b1be956bad1661`.
The runner now requires a real terminal sample at/after the target. The
interrupted r1 and r2 roots and their failed-process logs remain separate from
the active fresh root
`artifacts/phase0/model-runtime-qualification/stability/phase0-selected-24h-terminal-sample-20260810-r3`
under PID `70598`. The prior 20260808 root and all predecessor 20260810 roots
must not be concatenated.

The pre-merge two-cycle smoke completed with all three candidates passing. Its
append-only log is
`artifacts/phase0/model-runtime-qualification/stability/20260807T225158.694501Z/cycles.jsonl`
(SHA-256 `30a387c9ee2a824c45babe9047691fc23126aef67fa39fd84227bffe9f61cf23`).
Its status is correctly `short_smoke_complete`, never a 24-hour pass.
