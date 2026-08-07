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
missing runner, missing cache, missing artifact hash, incompatible dependency,
or invalid output is recorded as `quarantined`/`failed`; it is never replaced
by another model family.

Evidence is ignored local evidence under:

```text
artifacts/phase0/model-runtime-qualification/
```

The bundle includes `index.json`, candidate manifests, `bakeoff-gate.json`,
and `forecast-baseline-benchmark.json`. The index explicitly records that a
24-hour stability run has not been performed. Treat the files as immutable:
the writer refuses to overwrite a changed manifest.

## Initial pinned roster

The registry in `advisorai.phase0.runtime_qualification` records the Hub/Git
repository ID, immutable revision, complete repository file list, license,
external cache path, and explicit task role:

| Candidate | Repository | Revision | Role |
| --- | --- | --- | --- |
| FinBERT-family | `ProsusAI/finbert` | `4556d13015211d73dccd3fdd39d39232506f3e43` | CPU finance sentiment |
| IBM TTM-R2 | `ibm-granite/granite-timeseries-ttm-r2` | `d6a79570cac0f33d526601cd3a0fc7c80a8f9a2f` | CPU/lightweight forecast |
| TSPulse | `ibm-granite/granite-timeseries-tspulse-r1` | `2e64fcdc2a06d3565dfadaf0065c0ab5055f80f2` | anomaly/integrity/regime features only |
| Chronos-2-small | `autogluon/chronos-2-small` | `ddec01313e50b6bc58ebaa92ede81bc24a3d9f9a` | GPU forecast challenger |
| Kronos-mini | `NeoQuasar/Kronos-mini` | `f4e68697d9d5aed55cef5c96aabc3376bcad9f81` | GPU forecast challenger |
| Kronos-small | `NeoQuasar/Kronos-small` | `901c26c1332695a2a8f243eb2f37243a37bea320` | GPU forecast challenger |
| Kronos tokenizers | `NeoQuasar/Kronos-Tokenizer-2k` / `-base` | pinned separately in registry | tokenizer pins |
| TabPFN-TS (later) | `PriorLabs/tabpfn-time-series` | `a756ae3fb3af82c903c39e1cd71864ff5252bc4d` | later GPU challenger |

External caches default to `~/.cache/advisorai-v3/models/<candidate>` and must
remain outside the repository. Before a model can be measured, every declared
artifact needs a recorded SHA-256 and `verify_checkpoint_artifacts()` must
pass. `cached_artifact_inventory()` hashes every regular cached file for the
sanitized manifest.

`ProsusAI/finbert` currently has no declared Hub license metadata, so it stays
quarantined until an operator records an acceptable license decision. The
other listed public cards record Apache-2.0 or MIT as captured in the pin.

## Qualification boundary

`run_runtime_qualification()` measures load, one inference, repeated inference,
small-batch inference, cold/warm latency, RSS, optional CUDA VRAM, output
schema, NaN/Inf rejection, deterministic repeatability, unload, offline-only
execution, and resource ceilings. GPU candidates use the existing
`GpuModelLease`, so at most one GPU family is resident at once.

`run_finbert_qualification()` uses a fixed public finance-text fixture and
requires labels from `{positive, negative, neutral}` plus confidence in
`[0, 1]`. `run_tspulse_qualification()` uses the feature dataset and cannot
construct a price-forecast task. Forecast records compare naive, drift,
seasonal, linear, and LightGBM through the existing `ForecastEvaluation`
contract. One synthetic series is explicitly insufficient for superiority or
promotion claims; point-in-time AdvisorAI datasets can implement the same
versioned `BenchmarkDataset` interface later.

The short run is not a stability admission. Phase-0/Phase-7 time gates and any
model promotion remain pending until separately reviewed evidence exists.

