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
and authoritative license provenance are tracked separately below.

## Metadata Scout

`ResearchMetadataScout` runs one lookup as a durable `ResearchRole.SCOUT` job
through `ResearchCoordinator`. It supports arXiv, OpenAlex, Crossref DOI, and
GitHub release citations. SEC and FRED citation records pass the URL policy,
but do not yet have Scout adapters.

The Scout uses exact API hosts, HTTPS, one request per job, no retries, a
15-second request timeout, and a 512 KiB response cap. Provider pacing is
cancellation-aware and must fit inside the job's remaining execution budget.
The response is parsed for title, authors, publication date, and license
metadata; raw bodies, abstracts, and release notes are not written to the
registry. Each stored PaperCard includes the API provider, response SHA-256,
response size, and observation time. When an upstream record exposes a version,
the card also records its reference, label, and an identity SHA-256 bound to
that provider response. License evidence records identify whether the claim
came from arXiv metadata, OpenAlex aggregation, or a Crossref member deposit.
Replayed identical metadata observations are idempotent even when a prior
process stopped before the job completion record.

The metadata SHA-256 identifies the API response; the version identity SHA-256
binds a provider, version reference and label, and that metadata hash. Neither
is a hash of the cited document bytes. License provenance remains an upstream
claim and is not an authoritative rights determination. Source-document hashes
and first-party, exact-version license verification remain software work. The
Scout does not download full papers, source archives, SEC filings, or FRED data.

Stored PaperCards include their derived source family, requested and permitted
use, and policy version. Caller-supplied assessment fields are overwritten at
intake. Any source that cannot be classified remains outside the canonical
registry.
