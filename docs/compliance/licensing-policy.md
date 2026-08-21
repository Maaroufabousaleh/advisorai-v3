# AdvisorAI V3 licensing policy

This policy governs incorporation and redistribution decisions for AdvisorAI
V3. It is an engineering control and does not replace legal advice.

## Default policy

AdvisorAI original source is proprietary and All Rights Reserved. The
repository `LICENSE` does not relicense third-party software, model weights,
datasets, data, or external artifacts. Every incorporated component must retain
its own license and required notices.

The public repository policy is source-visible, not open-source. Public GitHub
hosting has platform-specific viewing/forking terms; a private repository is
the operational choice when source access must be restricted.

## License intake classes

### Preferred

MIT, BSD-2-Clause, BSD-3-Clause, and Apache-2.0 are preferred for ordinary
dependencies when the exact pinned version is verified and notice obligations
are preserved. Apache components require attention to NOTICE and attribution
requirements in the actual distribution.

### Case-by-case

LGPL, MPL, custom permissive model licenses, gated model terms, attribution
clauses, source-available terms, and separately licensed data require a
component-specific review. The result must describe whether the component is
an ordinary dependency, a modified/vendored source file, a separate process,
or a bundled binary.

### Explicit legal/compliance review

The following may not be silently incorporated or redistributed:

- GPL, AGPL, SSPL, or other copyleft/source-available code;
- non-commercial, research-only, no-redistribution, or no-derivatives terms;
- unknown/no-license code;
- custom restrictive model/checkpoint/tokenizer licenses;
- datasets or provider data with unclear redistribution rights;
- modified third-party source without a complete provenance and notice record.

An AGPL/GPL project used as a completely separate development tool is not
automatically a repository contamination finding. It becomes an incorporation
question when AdvisorAI imports, links, vendors, modifies, or distributes it
as part of a release.

## Mandatory intake fields

Each meaningful component belongs in
`configs/compliance/third-party-licenses.yaml` with:

- exact version/revision and source URL;
- component category and incorporation mode;
- license/SPDX evidence and evidence date;
- whether it is modified, dynamically imported, separate, optional, or
  redistributed;
- notice/source/relinking obligations;
- commercial and redistribution restrictions;
- current compliance state and action before distribution.

Unknown license information is never treated as permissive. The automated
check fails a component marked for redistribution if its license or required
notice/source metadata is unresolved.

## Source provenance rule

Importing a package is not the same as copying its source. Before committing
vendored or adapted code, search for license headers, copyright notices,
upstream URLs, and modification history. Unknown or incompatible source must be
quarantined until reviewed. Never place a top-level proprietary license over
third-party code to remove its original rights.

## Model and data rule

Model library, model code, checkpoint, tokenizer, training data, and output/data
provider terms are separate review objects. A permissive Python library does
not license model weights. A permissive model-card label does not necessarily
clear training-data provenance or gated access. A permissive API client does
not license returned market/news/filing data.

Weights and restricted data are not to be tracked in the source repository by
default. A release that includes them requires exact revision terms and a
separate clearance decision.

## Distribution rule

The source-visible profile is the default repository state. Any wheel,
container, executable, model bundle, source archive to third parties, or
commercial service must select a distribution profile, pass the policy checker,
produce the required notices, and resolve every review item for that profile.
The policy checker is intentionally offline and uses the pinned inventory; it
does not claim that a green source-only result clears an unbuilt binary.
