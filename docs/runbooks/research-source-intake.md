# Research source intake policy

`ResearchRegistry.store(..., record_type="paper")` applies a local, versioned
source policy before writing a `PaperCard`. This is a metadata-intake boundary;
it does not fetch source pages or store source bodies.

## Accepted citation URLs

The current policy version is `research-source-policy-v3`. URLs must use
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
allowlist and source-specific, exact-version evidence:

| Source family | Accepted license identifiers for `content_use` |
|---|---|
| arXiv, OpenAlex, Crossref DOI | `CC0-1.0`, `CC-BY-4.0` |
| GitHub release | `MIT`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause` |
| SEC EDGAR, FRED | None in this policy version |

The license table is necessary but not sufficient. The first-party version and
document verifier supports explicitly versioned arXiv papers and OpenAlex and
Crossref records with a reviewed source-host allowlist. For OpenAlex, the Scout
requires the exact work ID, a DOI and version label on the selected
`best_oa_location`, then fetches that location's landing page and PDF from
operator-allowlisted hosts. For Crossref, it requires the exact DOI and exactly
one matching `vor` or `am` license/PDF version, then fetches the deposited
publisher URL and PDF from operator-allowlisted hosts. In either case, the page
must identify the same DOI, link the exact selected PDF, and link one allowlisted
Creative Commons license. The Scout hashes the page and PDF under the 512 KiB
and 64 MiB response caps and stores neither body. Aggregated OpenAlex and
Crossref license fields are locators/claims, not first-party rights evidence.
OpenAlex describes each location as a particular hosted copy and assigns its
version and license fields per location; the selected `best_oa_location` is
therefore only the locator for the copy checked by the Scout ([OpenAlex
locations documentation](https://help.openalex.org/data/locations/)).
Crossref's per-version license and full-text link metadata is also used only to
select an unambiguous copy; ambiguous or mismatched versions fail closed. The
publisher page and document are checked directly. See Crossref's [license
metadata guidance](https://www.crossref.org/documentation/principles-practices/best-practices/license/)
and [full-text link guidance](https://www.crossref.org/documentation/retrieve-metadata/rest-api/text-and-data-mining-for-researchers/).
GitHub content use still fails closed until equivalent source-specific
verification.

The `PaperCard.license` value is a submitted or provider-reported claim. The
local policy does not make a legal determination or grant redistribution
rights. For citation-only use, the license remains a claim from the caller or
metadata provider. For arXiv content use, the first-party version page provides
version-bound evidence that the selected license link was present at observation
time. For OpenAlex, the configured host list is an operator review boundary;
OpenAlex's location/version choice is only a locator for the copy, and the
publisher or repository page supplies the observed license and document links.
For Crossref, the configured host list is also an operator review boundary; a
Crossref member deposit selects a versioned link but cannot replace the
publisher-page and PDF checks. This evidence is not a legal determination. Do
not store source text in
`claimed_result`, `limitations`, or other metadata fields.

## Metadata Scout

`ResearchMetadataScout` runs a lookup as a durable `ResearchRole.SCOUT` job
through `ResearchCoordinator`. It supports arXiv, OpenAlex, Crossref DOI,
GitHub release, SEC EDGAR filing, and FRED series citations. Most citation-only
jobs use one metadata request; an SEC filing outside the current submission
index may use up to three additional, index-listed historical submission
files. An explicit arXiv `content_use` request adds exact-version page and PDF
requests. An OpenAlex `content_use` request adds the `best_oa_location` page and
PDF requests; a Crossref `content_use` request adds the selected publisher page
and PDF. Both require an exact host allowlist in Scout configuration. SEC
filing documents and FRED observations are not fetched or stored.

The Scout uses exact API hosts, HTTPS, no retries, a 15-second request timeout,
and cancellation-aware provider pacing. Generic metadata and source-page
responses are capped at 512 KiB. SEC submission metadata is capped at 4 MiB
across the current index and any historical files used to verify an accession.
Exact-version PDF hashing uses a 64 MiB response cap. Provider pacing must fit
inside the job's remaining execution budget. SEC requests require a configured
User-Agent containing a contact email. FRED requests require an explicitly
provided 32-character API key; its transport errors redact that key, and the
key is never placed in the durable job or PaperCard.
The response is parsed for title, authors, publication date, and license
metadata; raw bodies, abstracts, and release notes are not written to the
registry. Each stored PaperCard includes the API provider, response SHA-256,
response size, and observation time. When an upstream record exposes a version,
the card also records its reference, label, and an identity SHA-256 bound to
that provider response. License evidence records identify whether the claim
came from arXiv metadata, OpenAlex aggregation, a Crossref member deposit, or
the checked first-party version page.
Replayed identical metadata observations are idempotent even when a prior
process stopped before the job completion record.

The metadata SHA-256 identifies the API response; the version identity SHA-256
binds a provider, version reference and label, and that metadata hash. For
OpenAlex and Crossref content use, it also binds the selected DOI and PDF
reference. Neither
is a hash of the cited document bytes. Upstream license provenance remains a
claim, not an authoritative rights determination. The arXiv content-use path
checks the first-party version page and hashes the exact PDF without retaining
it. The OpenAlex path checks the exact best-OA location page and hashes its
PDF without retaining it; the aggregated license remains untrusted. The
Crossref path checks one exact version selected by the Crossref deposit, then
requires the publisher page to confirm the DOI, PDF link, and license before
hashing and discarding the PDF. Crossref deposit claims alone are not accepted.
First-party license verification for GitHub remains software work. SEC
citations are resolved against the SEC submissions API
using the CIK and accession in the cited archive path. The Scout requires the
filing's primary document name to match the citation before recording issuer,
form, filing date, and accession metadata. FRED citations resolve one series by
its series ID and record its title, last-updated timestamp, metadata response
hash, and FRED page reference. These fields identify metadata snapshots; they
do not establish source-data rights or point-in-time observation availability.
The Scout does not download source archives, SEC filings, or FRED observations;
SEC/FRED content use remains closed.

Stored PaperCards include their derived source family, requested and permitted
use, and policy version. Caller-supplied assessment fields are overwritten at
intake. Any source that cannot be classified remains outside the canonical
registry.
