# Third-party notices

AdvisorAI's original source is proprietary under the repository `LICENSE`.
This file does not relicense any third-party component. Each component below
remains governed by its own license, model card, dataset terms, or service
terms.

The repository currently contains source, configuration, manifests, and
tests. It does not track third-party model-weight files or a vendored source
tree. Package-managed dependencies are not bundled by this source repository;
their own license files must be preserved when a wheel, container, executable,
or other distribution includes them.

## Runtime libraries

| Component | Pinned version | License | Use and distribution status |
| --- | ---: | --- | --- |
| Pydantic | 2.13.4 | MIT | Core typed contracts; package-managed dependency, not vendored |
| PyArrow | 25.0.0 | Apache-2.0 (with upstream notices for included portions) | Core columnar/data support; package-managed dependency, not vendored |
| DuckDB | 1.5.5 | MIT | Local analytical storage/query support; package-managed dependency |
| Polars | 1.43.2 | MIT, with upstream notices for included portions | Data-frame support; package-managed dependency |
| PyYAML | 6.0.3 | MIT | Configuration parsing; package-managed dependency |
| psutil | 7.2.2 | BSD-3-Clause | Resource/process observation; package-managed dependency |

## Optional runtime and dashboard libraries

| Component | Pinned version | License | Use and distribution status |
| --- | ---: | --- | --- |
| PydanticAI / pydantic-graph | 2.24.0 | MIT | Optional typed agent/graph boundary; not required by the core install |
| Prefect | 3.8.1 | Apache-2.0 | Optional orchestration boundary; not vendored |
| Hamilton (`sf-hamilton`) | 1.90.0 | Apache-2.0 | Optional data-flow boundary; not vendored |
| LiteLLM | 1.95.0 | MIT for the open-source package scope audited | Optional gateway boundary; enterprise components have separate terms and are not included here |
| NautilusTrader | 1.231.0 | LGPL-3.0-or-later | Optional execution/replay dependency; not vendored or modified; see the dedicated review below |
| argon2-cffi | 25.1.0 | MIT | Optional dashboard password hashing |
| FastAPI | 0.141.1 | MIT | Optional dashboard API |
| Uvicorn | 0.52.1 | BSD-3-Clause | Optional dashboard server |
| websockets | 16.1.1 | BSD-3-Clause | Optional transition/integration transport |
| LightGBM | 4.7.0 | MIT | Optional model/baseline runtime |
| Transformers | 5.5.4 | Apache-2.0 | Optional model runtime support |

The dashboard's direct JavaScript dependencies are also package-managed:
`@vitejs/plugin-react` (MIT), `lucide-react` (ISC), `react` (MIT),
`react-dom` (MIT), `vite` (MIT), `@types/react` (MIT), `@types/react-dom`
(MIT), and `typescript` (Apache-2.0). The locked dashboard transitive closure
contains 122 package records and must be rechecked by the distribution builder
if it is bundled; the package-lock audit observed MIT, ISC, Apache-2.0,
CC-BY-4.0, and BSD-3-Clause notices.

## Model libraries and checkpoints

Model library licenses do not automatically license model checkpoints or the
data used to train them. The exact revisions and unresolved provenance items
are in `configs/compliance/third-party-licenses.yaml` and
`docs/compliance/license-audit.md`.

| Model/checkpoint | Exact revision | Observed checkpoint license | Current distribution status |
| --- | --- | --- | --- |
| IBM Granite TTM-R2 | `d6a79570cac0f33d526601cd3a0fc7c80a8f9a2f` | Apache-2.0 | Referenced/qualified; weights not tracked or bundled |
| IBM Granite TTM-R3 | `ea17cfd2e3edcaea21eb8dcecd18bf88971482fa` | Apache-2.0 | Referenced/research-only; weights not tracked or bundled |
| IBM Granite TSPulse R1 | `2e64fcdc2a06d3565dfadaf0065c0ab5055f80f2` | Apache-2.0 | Role-restricted reference; weights not tracked or bundled |
| Chronos-2-small | `ddec01313e50b6bc58ebaa92ede81bc24a3d9f9a` | Apache-2.0 | Runtime candidate reference; weights not tracked or bundled |
| Kronos-mini | `f4e68697d9d5aed55cef5c96aabc3376bcad9f81` | MIT (model/source card) | Research reference; tokenizer and weights not bundled |
| Kronos-small | `901c26c1332695a2a8f243eb2f37243a37bea320` | MIT (model/source card) | Research reference; tokenizer and weights not bundled |
| ModernFinBERT | `6c6de8332ea7f6824c0f8917358dce1e669c1710` | Apache-2.0 (model card) | Weights not bundled; training-data provenance needs review |
| FinBERT-MiniLM | `fdbfec0cd09610bd5af26da8998507fe7838e838` | MIT (model card) | Weights not bundled; training-data provenance needs review |
| Finance DeBERTa-v3 | `f2312de96d6cfe6251da37afb0e99b8e29885bdd` | Apache-2.0 (model card) | Weights not bundled; training-data provenance needs review |
| TabPFN-TS | `a756ae3fb3af82c903c39e1cd71864ff5252bc4d` | Gated Apache-2.0 declaration | Not acquired; gated terms must be accepted and reviewed before use or distribution |

The model table is not permission to redistribute any checkpoint. A model
card's code/weight license must be reconciled with its gated terms, tokenizer,
base-model terms, and training-data provenance before commercial bundling.

## External tools and integrations

NautilusTrader is the only materially relevant copyleft-licensed runtime
dependency currently declared by AdvisorAI. AdvisorAI imports/probes its
optional boundary; it does not contain NautilusTrader source, modify it, or
silently replace the authoritative OMS/RiskKernel.

Prefect, Hamilton, LiteLLM, PydanticAI, and the optional dashboard stack are
separate package dependencies. Architecture references to Qlib, QuantLib,
vectorbt, LEAN, CCXT, Hummingbot, Freqtrade, Hermes, Inalpha, or other alpha
ecosystems are not evidence that their source is incorporated or distributed.

## Data and API sources

Binance public/testnet surfaces, Coinbase Sandbox, Deribit, GDELT, SEC/RSS,
ALFRED/FRED, Hugging Face, and other providers are service/data sources, not
software licenses. An MIT/Apache client library does not grant redistribution
rights for the data returned by that service. Raw provider responses, raw news,
model-weight files, and other ignored Phase-4 evidence are not tracked in this
source repository. Any publication of cached responses, derived datasets, or
provider-specific artifacts requires a separate terms/copyright review.

## Datasets and training-data provenance

The Financial PhraseBank card identifies CC-BY-NC-SA-3.0 terms, including
non-commercial/share-alike conditions and a commercial-contact path. The
Twitter Financial News Sentiment dataset is identified as MIT. The
`nickmuchi/financial-classification` provenance referenced by the Finance
DeBERTa card was not assigned a permissive license in this audit. These
datasets are not tracked or bundled; their relationship to checkpoint
commercial distribution remains a review item.

## Required distribution actions

Before distributing a wheel, container, executable, model bundle, or cached
data, use the profile matrix and audit report. Preserve package license texts
and notices, satisfy LGPL replacement/relinking requirements where applicable,
exclude unapproved weights and restricted data, and resolve every
`REVIEW_REQUIRED` item. This source-visible repository is not a claim that a
future binary or commercial bundle is ready.
