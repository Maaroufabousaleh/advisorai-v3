# AdvisorAI V3 distribution matrix

Statuses below are engineering readiness findings from the 2026-08-21 audit,
not legal opinions.

| Profile | Current status | Must include/preserve | Must exclude or resolve | Legal review |
| --- | --- | --- | --- | --- |
| A. Private internal use | **Ready with terms controls** | Proprietary `LICENSE`; dependency/model/provider terms; internal access controls | Do not assume private use permits unrestricted model/data use | Recommended for commercial/regulated activity |
| B. Public GitHub source-visible | **Prepared with platform limitation** | `LICENSE`, notices, inventory, policy; no unapproved weights/raw data | GitHub public-repository viewing/forking rights cannot be overridden by the proprietary text | Recommended; use private visibility for strict access control |
| C. Selected source archive | **Review required** | Proprietary license, exact third-party notices, selected SBOM/inventory | Unlicensed/copied source, restricted weights/data, unresolved transitive closure | Yes before distribution |
| D. Python wheel | **Not currently ready** | Exact dependency closure notices; Apache NOTICE; LGPL handling if NautilusTrader included; SBOM | Do not silently bundle REVIEW_REQUIRED models/data | Yes for bundled optional/runtime components |
| E. Docker/container | **Not currently ready** | OS/package/native notices, SBOM, model/runtime notices, LGPL replacement/relinking path | Unapproved weights, provider cache, restricted data, unknown transitive licenses | Yes |
| F. Commercial executable/service | **Not currently cleared** | All applicable software/model/data/provider notices and contracts; compliance records | Components with commercial restrictions or unknown rights | Required, plus separate financial/regulatory review |

## Profile decision procedure

1. Enumerate the actual files, wheels, native libraries, model checkpoints,
   tokenizers, datasets, and cached data in the release.
2. Set `redistributed: true` only for components actually included in that
   release; do not mark a source-only dependency as bundled for convenience.
3. Run `scripts/check_license_policy.py --profile <profile>` against a
   profile-specific inventory revision.
4. Generate an SPDX or CycloneDX SBOM and collect exact upstream license/
   `NOTICE` texts for every included package.
5. Resolve all `REVIEW_REQUIRED`, `UNKNOWN`, restrictive, and conditional
   obligations. A source-only pass does not waive these requirements.
6. Exclude model weights/data unless their exact rights are documented.
7. Obtain professional legal review before commercial distribution, especially
   when NautilusTrader, model weights, provider data, or copyleft/source-
   available components are bundled.

## Model/data bundling rule

The current repository is intentionally source-only with respect to model
weights, raw provider data, and cached Phase-4 evidence. A future bundle must
not infer permission from a checkpoint's Apache/MIT model-card label alone. The
Financial PhraseBank CC-BY-NC-SA-3.0 finding and the unresolved
`nickmuchi/financial-classification` provenance are current examples of items
that block an unqualified commercial model bundle.
