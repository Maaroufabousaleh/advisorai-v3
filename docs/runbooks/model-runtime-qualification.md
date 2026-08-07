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
missing runner, missing cache, missing artifact hash, unapproved license or
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
