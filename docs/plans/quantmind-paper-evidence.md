# QuantMind paper evidence prototype

**Decision (2026-11-27).** Evaluate direct adaptation of a small QuantMind
paper-evidence component into AdvisorAI's Research Brain under
[I6](native-first-integration-roadmap.md#i6--hermes-and-research-brain-actual-research-factory)
and [Phase 9](phase-09-controlled-expansion.md). AdvisorAI will own and
maintain the selected code in its existing research package. This is a
research-only prototype.

## Upstream and scope

The reviewed upstream revision is
[`10e9dbd`](https://github.com/LLMQuant/quant-mind/commit/10e9dbd0c7254edc5a091b852122ebe72e06092a)
(August 15, 2026). Review page-aware chunks and source spans, typed
method/result/limitation drafts, and deterministic citation resolution.
Before bringing code in, record the exact files and revision and preserve
the upstream [MIT licence and copyright notice](https://github.com/LLMQuant/quant-mind/blob/10e9dbd0c7254edc5a091b852122ebe72e06092a/LICENSE)
with the adapted code and in the [third-party notices](../../THIRD_PARTY_NOTICES.md).
Check parser, dependency and PDF rights separately. Use the Scout, model
gateway, research coordinator, registry and retrieval already owned by
AdvisorAI. Keep the prototype scoped to PDFs and the existing research
boundary. Revisit news, filings and ETL individually after the pilot.

## First runnable slice

1. Start with one rights-cleared, explicitly versioned PDF. Use the existing
   Scout and source policy to admit content and record the document hash in
   its PaperCard. Bind parsing to those admitted bytes. Preserve physical
   pages and page-local spans even when a retrieval chunk crosses pages.
2. Run one bounded extraction job through the existing model gateway for
   methods, results and limitations. Treat model output as a draft. Resolve
   identifiers and page spans deterministically, and validate each quote
   against its cited span. Record source hash, parser and model revisions,
   prompt/config identity and job outcome.
3. Store accepted findings through the existing registry, linked to the
   PaperCard. Retrieve them with AdvisorAI's existing research access path.
   Show the source revision, physical page, and permitted excerpt so a
   reviewer can reproduce the citation. Submit hypotheses via the existing
   workflow for independent validation.

## Acceptance and promotion

- Reproduce the observed upstream
  [citation regression](https://github.com/LLMQuant/quant-mind/blob/10e9dbd0c7254edc5a091b852122ebe72e06092a/quantmind/flows/_paper_summary.py#L193).
  The reviewed validator accepted a page 2 quote cited on page 1 when both
  pages shared a chunk. AdvisorAI must reject that citation and accept the
  same exact quote on page 2. Also cover invented quotes, unknown pages,
  wrong document hashes and sources restricted to citation-only use.
- Demonstrate one real admitted PDF through extraction, persistence and
  retrieval. Verify a small reference set of methods, results and
  limitations by hand. Measure whether each cited passage supports the
  claim independently of exact quote matching. Record missing or
  unsupported findings.
- Report the runnable command, source and output hashes, budgets, latency,
  and observed results. Verify repeat runs keep one retrievable record per
  equivalent finding and that a failed job leaves a clear outcome. Review
  the effort and value of the port against the existing metadata-only
  workflow before expanding the scope.
