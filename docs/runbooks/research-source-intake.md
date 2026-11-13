# Research source intake policy

`ResearchRegistry.store(..., record_type="paper")` applies a local, versioned
source policy before writing a `PaperCard`. This is a metadata-intake boundary;
it does not fetch source pages or store source bodies.

## Accepted citation URLs

The current policy version is `research-source-policy-v1`. URLs must use
canonical HTTPS without credentials, a port, query, fragment, whitespace,
backslashes, or percent-encoded paths. Both the hostname and path must match
one of these reviewed families exactly:

| Family | Accepted host and path shape |
|---|---|
| arXiv | `arxiv.org/(abs|pdf|html)/<modern-or-legacy-id>[vN]` |
| OpenAlex | `openalex.org/W<digits>` |
| Crossref DOI | `doi.org/10.<registrant>/<DOI-suffix>` |
| GitHub release | `github.com/<owner>/<repo>/releases/(latest|tag/<tag>)` |
| SEC EDGAR | `sec.gov` or `www.sec.gov/Archives/edgar/data/<CIK>/<18-digit-accession>/<filing-file>` |
| FRED | `fred.stlouisfed.org/series/<series-id>` |

Unknown hosts, API routes outside these citation shapes, query-based URLs, and
suffix-host lookalikes are rejected. Extending the list requires a code change
and tests for the exact host/path rule.

## Permitted use

PaperCards request `citation_only` by default. They remain eligible when the
license is unknown; the registry stores no source body. A caller can request
`content_use` only with an exact license identifier in the source-family
allowlist:

| Source family | Accepted license identifiers for `content_use` |
|---|---|
| arXiv, OpenAlex, Crossref DOI | `CC0-1.0`, `CC-BY-4.0` |
| GitHub release | `MIT`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause` |
| SEC EDGAR, FRED | None in this policy version |

The `PaperCard.license` value is a submitted license claim. The current local
policy does not fetch an authoritative license record or establish that the
claim applies to the cited version. A recorded `content_use` result is
therefore a policy classification, not source qualification or a grant of
redistribution rights. Do not store source text in `claimed_result`,
`limitations`, or other metadata fields. Source acquisition, response hashes,
and authoritative license provenance are tracked as remaining Research Brain
software work.

Stored PaperCards include their derived source family, requested and permitted
use, and policy version. Caller-supplied assessment fields are overwritten at
intake. Any source that cannot be classified remains outside the canonical
registry.
