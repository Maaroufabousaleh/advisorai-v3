# AdvisorAI V3 licensing and redistribution audit

Audit date: 2026-08-21
Audited repository commit: `8f8b7cb89c98191b0d0a06498aea5d2438018ccc`
Audit type: engineering compliance inventory, not a legal opinion

## Executive decision

| Surface | Finding |
| --- | --- |
| AdvisorAI original source | **PROPRIETARY LICENSING COMPATIBLE**. A top-level proprietary / All Rights Reserved license is appropriate for original AdvisorAI work, subject to copyright ownership and contributor review. |
| Current source-visible repository | **PREPARED WITH LIMITATIONS**. The new license identifies original work and preserves third-party rights. A public GitHub repository remains subject to GitHub platform terms, including viewing/forking functionality. |
| Private internal use | **ENGINEERING-READY, TERMS-CONDITIONAL**. Use does not itself redistribute the optional dependencies, model weights, or provider data; service, model, dataset, and commercial-use terms still apply. |
| Source archive to selected users | **NOT READY FOR BLANKET DISTRIBUTION** until the selected dependency closure and notices are audited for that exact archive. Exclude weights and restricted data unless separately cleared. |
| Python wheel | **REVIEW REQUIRED**. Decide whether dependencies are external or bundled, then produce package-level notices/SBOM and address NautilusTrader LGPL obligations if included. |
| Container/executable | **NOT CURRENTLY READY**. A bundle would redistribute a transitive runtime closure and may include copyleft runtime code, model runtimes, and native libraries. Build-specific notices, replacement/relinking analysis, and model/data clearance are required. |
| Commercial service/product | **NOT CURRENTLY CLEARED**. Model/data/provider terms and professional legal review remain required, in addition to separate financial/regulatory review. |

The audit found no evidence that AdvisorAI's entire repository must be GPL or
AGPL. It also found no tracked model-weight files, vendored `vendor/` or
`third_party/` source tree, or obvious copied third-party implementation. That
is evidence from the checked tree and history, not a guarantee that no
unrecorded provenance issue exists.

The main unresolved distribution blockers are:

1. the full transitive package closure is not individually represented with
   package-level notices in the source repository;
2. NautilusTrader is an optional LGPL-3.0-or-later dependency and requires a
   bundle-specific analysis if distributed;
3. several qualified model checkpoints have training-data, tokenizer, gated,
   or commercial-use questions that a model-card Apache/MIT label does not
   answer;
4. Financial PhraseBank is identified under CC-BY-NC-SA-3.0, and the license
   of the referenced `nickmuchi/financial-classification` dataset was not
   established in this audit;
5. provider data/API terms are separate from the licenses of ingestion
   libraries.

No blocker above requires changing AdvisorAI's original-code license. They do
mean that a future wheel, container, model bundle, cached-data release, or
commercial product must not be treated as automatically compliant merely
because the repository has a proprietary `LICENSE`.

## Audit method and boundaries

The audit used, in order of preference:

1. the exact pinned version/revision in `pyproject.toml`, `uv.lock`,
   `dashboard/package-lock.json`, runtime requirement files, and model
   registries;
2. the upstream repository license at the relevant project;
3. official model or dataset cards and their referenced revisions;
4. package-lock metadata and SPDX/package metadata where upstream source was
   not embedded in the repository.

The machine-readable inventory is
[`configs/compliance/third-party-licenses.yaml`](../../configs/compliance/third-party-licenses.yaml).
The repository scan covered tracked files, source imports, runtime manifests,
architecture references, model identities, ignored-artifact policy, and a
high-level commit-author/provenance review. The scan was intentionally
read-only with respect to AdvisorAI runtime/evidence state.

The repository has no tracked `LICENSE`, `COPYING`, or
`THIRD_PARTY_NOTICES.md` at the audited base. This change adds them. The
tracked tree contains no approved model-weight extension (`.safetensors`,
`.bin`, `.pt`, `.pth`, `.onnx`, `.ckpt`, or equivalent) and no committed raw
market/news dataset files identified by the scan. `artifacts/` is ignored apart
from its placeholder; ignored evidence must not be assumed redistributable.

## Component inventory summary

### Direct Python dependencies

The direct core pins are Pydantic 2.13.4 (MIT), PyArrow 25.0.0
(Apache-2.0 plus upstream notices), DuckDB 1.5.5 (MIT), Polars 1.43.2 (MIT),
PyYAML 6.0.3 (MIT), and psutil 7.2.2 (BSD-3-Clause). The optional groups pin
PydanticAI/pydantic-graph 2.24.0 (MIT), Prefect 3.8.1 (Apache-2.0), Hamilton
1.90.0 (Apache-2.0), LiteLLM 1.95.0 (MIT for the audited open-source package
scope), NautilusTrader 1.231.0 (LGPL-3.0-or-later), argon2-cffi 25.1.0 (MIT),
FastAPI 0.141.1 (MIT), Uvicorn 0.52.1 (BSD-3-Clause), websockets 16.1.1
(BSD-3-Clause), LightGBM 4.7.0 (MIT), and Transformers 5.5.4 (Apache-2.0).

These are ordinary package-manager dependencies, not copied source. The
permissive licenses generally allow use in proprietary software if their
notices are preserved when the package is redistributed. “Generally” is
deliberate: the exact bundle and transitive closure still control the final
obligations.

The dashboard directly declares React 18.3.1 (MIT), React DOM 18.3.1 (MIT),
Vite 6.4.3 (MIT), `@vitejs/plugin-react` 4.7.0 (MIT), lucide-react 0.468.0
(ISC), TypeScript 5.9.3 (Apache-2.0), and the React type packages (MIT). Its
lockfile contains 122 records; the package-lock metadata observed MIT, ISC,
Apache-2.0, CC-BY-4.0, and BSD-3-Clause. This is a lockfile observation, not a
substitute for collecting exact notices in a bundle.

### Transitive dependencies

`uv.lock` contains 211 locked package records. The source repository does not
ship those installed packages, so the inventory records the Python runtime
closure as `REVIEW_REQUIRED` rather than pretending that every transitive
license is already individually audited. The same treatment is used for the
122-record dashboard closure.

This is intentionally fail-closed: the default source-visible profile passes
only because those packages are not redistributed by this repository. A wheel,
container, or executable release must resolve each included transitive package,
retain its license/notice, and produce an SBOM or equivalent release manifest.

### External OSS projects and architecture references

Actual optional package boundaries were found for NautilusTrader, Prefect,
Hamilton, LiteLLM, PydanticAI, FastAPI/Uvicorn, and model runtimes. AdvisorAI
does not vendor their source. The Nautilus adapter probes an optional package
boundary and preserves AdvisorAI's own OMS/RiskKernel authority; it is not a
copied Nautilus implementation.

The architecture and research documents mention projects including Qlib,
QuantLib, vectorbt, LEAN, CCXT, Hummingbot, Freqtrade, Hermes, Inalpha, and
other alpha ecosystems. Those references are classified as
`DOCUMENTATION_REFERENCE`, `EXTERNAL_PROCESS`, or planned integration—not as
incorporated code. Inalpha's AGPL reference is therefore not a GPL/AGPL
contamination finding. Directly importing, vendoring, modifying, or bundling
one of those projects would require a new audit.

### NautilusTrader: dedicated review

The pinned optional dependency is NautilusTrader 1.231.0 under
LGPL-3.0-or-later. Current incorporation mode is an ordinary optional Python
dependency/import boundary: no Nautilus source is tracked, no modified copy was
found, and the repository does not bundle its wheel or native artifacts.

* Internal/private use: the dependency can be used under its LGPL terms; keep
  the dependency's notices and do not misrepresent it as AdvisorAI code.
* Source distribution without the dependency bundled: the repository may keep
  the optional dependency declaration, but recipients who install it obtain it
  under its own LGPL terms. The distribution still should identify the
  dependency accurately.
* Wheel/container/executable that includes NautilusTrader: include the LGPL
  license and applicable notices, preserve the rights required by the LGPL,
  and provide the ability to replace/relink the LGPL component as required by
  the applicable form of distribution. The precise Python/native bundling
  arrangement needs release-specific legal review.
* Modified NautilusTrader: preserve the LGPL notices and provide the relevant
  corresponding source and modification information required by the LGPL.

This is a yellow, conditional item—not an automatic whole-project copyleft
finding. It becomes a release blocker if a proposed bundle cannot satisfy its
LGPL obligations.

### Models and checkpoints

The executable/runtime registry references pinned revisions for IBM TTM-R2,
TTM-R3, TSPulse, Chronos-2-small, Kronos-mini, Kronos-small, ModernFinBERT,
FinBERT-MiniLM, Finance DeBERTa-v3, and gated TabPFN-TS. The inventory
separates model library, model code, checkpoint, tokenizer, and training-data
questions.

Apache/MIT model-card labels were verified for the IBM TTM family, Chronos,
ModernFinBERT, FinBERT-MiniLM, Finance DeBERTa-v3, and Kronos references as
listed in the inventory. Those labels do **not** establish that AdvisorAI may
bundle every weight file commercially. In particular:

* TTM, Chronos, and TSPulse weights are not tracked. Before bundling, verify
  the model card, base model, tokenizer, runtime, and any gated terms at the
  exact revision.
* Kronos weights and tokenizers are not tracked. The MIT source/card finding is
  not a substitute for a separate tokenizer/checkpoint review.
* ModernFinBERT's checkpoint card is Apache-2.0, but the audit did not
  establish a complete commercial redistribution chain for its training data.
* FinBERT-MiniLM's checkpoint card is MIT, but the model-card/data lineage
  references Financial PhraseBank, whose card identifies CC-BY-NC-SA-3.0.
* Finance DeBERTa-v3's checkpoint card is Apache-2.0, but its model card
  references Financial PhraseBank and `nickmuchi/financial-classification`;
  the latter's license was not established here.
* TabPFN-TS is gated and was not acquired. It must not be downloaded or
  bundled until its access and use terms are accepted and reviewed.

No model weights are distributed with the current source repository. This
reduces redistribution exposure but does not remove restrictions on use of a
checkpoint obtained separately.

### Datasets, APIs, and provider data

The Financial PhraseBank dataset card identifies CC-BY-NC-SA-3.0, including
non-commercial/share-alike conditions and a commercial-contact path. The
Twitter Financial News Sentiment dataset card identifies MIT, but source-content
and provider terms still warrant review. The referenced
`nickmuchi/financial-classification` license remains unknown in this audit.
No rows from these datasets are tracked.

Binance public/testnet data, Coinbase Sandbox, Deribit, GDELT, SEC/RSS,
ALFRED/FRED, Hugging Face hosting, and other providers are service/data
relationships, not software licenses. An MIT/Apache client or SDK does not
grant a right to redistribute returned market, news, filing, or macro data.
The current source tree contains no committed raw provider responses. Any
release of cached responses, derived datasets, reports, or model outputs must
be reviewed against the relevant provider/data terms separately.

## Vendored/copied-source result

The scan found:

* no tracked `vendor/`, `vendors/`, `third_party/`, or `third-party/` source
  directory;
* no source file with an apparent copied license block or third-party copyright
  header requiring relicensing;
* no tracked model weights or raw external datasets;
* package imports and optional adapters rather than copied implementations;
* no additional substantive commit author in the reviewed history beyond the
  repository owner identities.

This supports licensing AdvisorAI's original source as proprietary. It is not a
copyright provenance certification; any contributor or copied-source discovery
must be added to the inventory before distribution.

## Answers to the required questions

### Q1 — Can original AdvisorAI code be proprietary?

Based on the audited tree, yes, subject to confirming copyright ownership and
contributor rights. The new `LICENSE` applies only to original AdvisorAI work
and expressly excludes third-party material.

### Q2 — Must the entire repository be GPL/AGPL?

No evidence supports that conclusion. An AGPL project appears as an
architecture/reference discussion only; it is not imported, vendored, or
distributed by the current tree. NautilusTrader is LGPL and optional, not GPL,
and its presence does not automatically relicense unrelated original source.

### Q3 — Is third-party source contained without redistribution permission?

No such source was identified by this audit. This remains a finding about the
checked tree, not a guarantee about untracked local files or future commits.

### Q4 — Which dependencies can remain ordinary proprietary dependencies?

The pinned MIT, BSD, and Apache package dependencies can generally remain
ordinary dependencies when their notices are preserved. NautilusTrader can also
remain an optional dependency, but its LGPL conditions must be satisfied if a
release bundles it. The exact list and evidence are in the inventory.

### Q5 — Which components require notices?

All redistributed package components should retain their upstream license and
copyright notices; Apache components also require their applicable NOTICE
information. LGPL NautilusTrader requires its LGPL notices and release-specific
replacement/relinking handling. Model cards, tokenizer licenses, datasets, and
provider terms may require attribution or additional notices.

### Q6 — Which components impose source/relinking obligations?

NautilusTrader can impose LGPL source/modification and replacement/relinking
obligations when distributed in a combined package/container/executable. A
future GPL/AGPL or other copyleft component would need its own analysis. No
such incorporated component was found today. Dataset share-alike or model
terms may impose separate obligations not equivalent to software source
disclosure.

### Q7 — Which models may not be bundled now?

No model checkpoint is currently approved for bundling solely from this audit.
Do not bundle TabPFN-TS (gated/not acquired), FinBERT-MiniLM or Finance
DeBERTa-v3 without resolving the Financial PhraseBank and unknown dataset
provenance, ModernFinBERT without complete training-data review, or any
Kronos/tokenizer/IBM/Chronos weight without exact revision-term review.

### Q8 — Which data/artifacts may not be redistributed now?

Do not redistribute raw provider responses, raw news/filings/market data,
Financial PhraseBank rows, unknown-license training data, ignored Phase-4
evidence, or cached model outputs as a default release artifact. Each requires
source-specific terms review. The repository's scientific artifacts must not be
mutated to perform this audit.

### Q9 — Can the source be public under proprietary terms?

Yes, as a proprietary source-visible repository, while third-party rights remain
separate. However, GitHub's official documentation states that public GitHub
repositories permit other GitHub users to view and fork through GitHub
functionality, and the GitHub Terms grant platform-use rights for public
content. A proprietary license cannot be used to pretend those platform terms
do not exist. Use a private repository when strict source access is required.

### Q10 — What changes before binary/package/container distribution?

Select the exact bundle, resolve every included transitive dependency, collect
license/NOTICE texts and an SBOM, satisfy NautilusTrader LGPL handling if
included, exclude or clear model weights/tokenizers, exclude restricted data,
review provider terms, verify contributor provenance, and obtain professional
legal review for commercial distribution. The distribution matrix turns this
into a release checklist.

## Action before distribution

- [ ] Confirm copyright ownership and contribution rights for all original code.
- [ ] Keep the proprietary `LICENSE` and third-party notice file in the release.
- [ ] Enumerate the exact source archive, wheel, container, executable, or
      service contents.
- [ ] Resolve every included transitive package license and required notice.
- [ ] Generate an SPDX or CycloneDX SBOM for a bundle, without treating it as a
      substitute for notices.
- [ ] Include Apache NOTICE material and preserve BSD/MIT/ISC notices where
      applicable.
- [ ] Complete the NautilusTrader LGPL replacement/relinking/modification
      analysis if it is bundled.
- [ ] Do not bundle model weights/tokenizers until exact checkpoint and model
      terms are cleared.
- [ ] Resolve Financial PhraseBank and unknown dataset provenance before
      commercial model distribution.
- [ ] Exclude raw/restricted provider data unless redistribution is expressly
      permitted.
- [ ] Resolve every `UNKNOWN` or `REVIEW_REQUIRED` inventory item for the
      selected profile.
- [ ] Obtain legal review before commercial distribution or regulated use.

## Governance and ownership

The reviewed history contains two author identities for the repository owner
and no other substantive author identity was found in the high-level scan. That
is evidence, not a legal conclusion. If external contributions are accepted,
contributors should represent that they have the right to submit the work and
the maintainer should choose an explicit inbound contribution policy or CLA
before accepting material under the proprietary license.

The repository currently does not make a broad inbound contribution promise.
`CONTRIBUTING.md` now flags this boundary rather than silently converting a
public repository into an open-source contribution program.

## Evidence sources

Representative authoritative sources used in the audit:

* [GitHub licensing guidance](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)
  and [GitHub Terms of Service](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service?apiVersion=2022-11-28)
  for public-repository platform rights.
* [Pydantic](https://github.com/pydantic/pydantic), [Apache Arrow](https://github.com/apache/arrow),
  [DuckDB](https://github.com/duckdb/duckdb), [PyYAML](https://github.com/yaml/pyyaml),
  [psutil](https://github.com/giampaolo/psutil), [Prefect](https://github.com/PrefectHQ/prefect),
  [Hamilton](https://github.com/DAGWorks-Inc/hamilton), [PydanticAI](https://github.com/pydantic/pydantic-ai),
  [NautilusTrader](https://github.com/nautechsystems/nautilus_trader), [LightGBM](https://github.com/microsoft/LightGBM),
  [Transformers](https://github.com/huggingface/transformers), and [FastAPI](https://github.com/fastapi/fastapi)
  upstream license repositories.
* Exact [TTM-R2](https://huggingface.co/ibm-granite/granite-timeseries-ttm-r2),
  [TTM-R3](https://huggingface.co/ibm-granite/granite-timeseries-ttm-r3),
  [TSPulse](https://huggingface.co/ibm-granite/granite-timeseries-tspulse-r1),
  [Chronos-2-small](https://huggingface.co/autogluon/chronos-2-small),
  [Kronos-mini](https://huggingface.co/NeoQuasar/Kronos-mini),
  [ModernFinBERT](https://huggingface.co/tabularisai/ModernFinBERT),
  [FinBERT-MiniLM](https://huggingface.co/9mark9/finbert-minilm-sentiment), and
  [Finance DeBERTa](https://huggingface.co/anabdd/finsentiment-deberta-v3-base)
  model cards.
* [Financial PhraseBank dataset card](https://huggingface.co/datasets/takala/financial_phrasebank),
  [Twitter Financial News Sentiment dataset card](https://huggingface.co/datasets/zeroshot/twitter-financial-news-sentiment),
  and the [referenced financial-classification dataset](https://huggingface.co/datasets/nickmuchi/financial-classification).
