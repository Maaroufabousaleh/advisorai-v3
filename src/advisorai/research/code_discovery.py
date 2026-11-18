"""Code discovery: paper→code and code→evidence search for the Research Scout.

Extends the metadata Scout without changing it: given a paper (DOI/arXiv ID),
find linked implementations through OpenAlex locations, arXiv source links,
and code-availability statements; given a repository, assess it as evidence
through release pins, license files, and independent-reproduction signals.
Every finding becomes a typed ImplementationRecord with a reasoned
disposition — link, license check, and reproduced result stay distinct.

Read-only, bounded, offline-testable: HTTP goes through injected clients and
every live fetch is behind the same allowlist/timeout discipline as the
metadata Scout.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from advisorai.research.cards import (
    ImplementationDisposition,
    ImplementationRecord,
    ImplementationRelationship,
    PaperCard,
)

CODE_DISCOVERY_VERSION = "advisorai.research.code-discovery.v1"

_GITHUB_REPO = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
_GITHUB_RELEASE_PATH = re.compile(
    r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/releases/tag/([A-Za-z0-9._+-]{1,128})/?$"
)
_CODE_URL_HINTS = (
    "github.com/",
    "gitlab.com/",
    "bitbucket.org/",
    "code availability",
    "source code",
)
_REUSE_LICENSES = frozenset(
    {
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "CC0-1.0",
        "Unlicense",
        "MPL-2.0",
        "LGPL-3.0-only",
    }
)
_COPYLEFT_LICENSES = frozenset(
    {"GPL-3.0-only", "GPL-3.0-or-later", "AGPL-3.0-only", "GPL-2.0-only"}
)


@dataclass(frozen=True, slots=True)
class CodeLead:
    """One unverified code pointer found during discovery."""

    repository_url: str
    found_via: str
    context: str


@dataclass(frozen=True, slots=True)
class CodeDiscoveryReport:
    paper_card_id: UUID | None
    leads: tuple[CodeLead, ...]
    records: tuple[ImplementationRecord, ...]


def extract_code_leads(
    paper: PaperCard,
    *,
    openalex_locations: tuple[str, ...] = (),
    full_text_excerpt: str = "",
    author_page_urls: tuple[str, ...] = (),
) -> tuple[CodeLead, ...]:
    """Paper→code: collect repository pointers from metadata and excerpts.

    Pure function over already-fetched strings: OpenAlex alternate locations,
    a bounded full-text excerpt (code-availability section, supplement links),
    and author-page URLs. No fetching here, so this is trivially testable.
    """
    leads: dict[str, CodeLead] = {}
    for location in openalex_locations:
        normalized = _normalize_repo_url(location)
        if normalized is not None:
            leads.setdefault(
                normalized,
                CodeLead(
                    repository_url=normalized,
                    found_via="openalex_location",
                    context=f"alternate location for {paper.source_url}",
                ),
            )
    for match in _GITHUB_REPO.finditer(full_text_excerpt):
        normalized = f"https://github.com/{match.group(1)}/{match.group(2)}"
        leads.setdefault(
            normalized,
            CodeLead(
                repository_url=normalized,
                found_via="full_text_excerpt",
                context="repository URL in code-availability text or supplement",
            ),
        )
    for page in author_page_urls:
        normalized = _normalize_repo_url(page)
        if normalized is not None:
            leads.setdefault(
                normalized,
                CodeLead(
                    repository_url=normalized,
                    found_via="author_page",
                    context=f"author page for {paper.source_url}",
                ),
            )
    return tuple(leads.values())


def _normalize_repo_url(value: str) -> str | None:
    match = _GITHUB_REPO.match(value.strip().rstrip("/"))
    if match is None:
        return None
    return f"https://github.com/{match.group(1)}/{match.group(2)}"


def assess_release_pin(repository_url: str, *, tag: str, commit_sha: str) -> dict[str, str]:
    """Code→evidence: pin an exact revision; tag alone is not a pin."""
    if not _GITHUB_REPO.match(repository_url):
        raise ValueError("release pins require a github.com repository URL")
    if not re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", tag):
        raise ValueError("release tag is not a valid tag name")
    if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise ValueError("release pin requires a full 40-char commit SHA")
    archive = f"{repository_url.rstrip('/')}/archive/refs/tags/{tag}.zip"
    return {"tag": tag, "commit_sha": commit_sha, "archive_url": archive}


def hash_source_bytes(payload: bytes) -> str:
    """Content hash for a fetched archive or file set."""
    if not payload:
        raise ValueError("cannot hash empty source bytes")
    return hashlib.sha256(payload).hexdigest()


def classify_relationship(
    *,
    repository_owner: str,
    paper_authors: tuple[str, ...],
    claims_independent: bool,
) -> ImplementationRelationship:
    """Author code, independent reproduction, or unverified lead.

    Owner-name overlap with a paper author is weak evidence of authorship and
    is recorded as such; only an explicit author statement or same-account
    publication upgrades a lead beyond unverified.
    """
    owner_fold = repository_owner.casefold()
    author_hit = any(
        owner_fold and owner_fold in author.casefold().replace(" ", "") for author in paper_authors
    )
    if author_hit and not claims_independent:
        return ImplementationRelationship.AUTHOR_IMPLEMENTATION
    if claims_independent:
        return ImplementationRelationship.INDEPENDENT_REPRODUCTION
    return ImplementationRelationship.UNVERIFIED_LEAD


def recommend_disposition(
    *,
    relationship: ImplementationRelationship,
    license_id: str,
    replication_evidence: str,
    small_reviewed_scope: bool,
) -> tuple[ImplementationDisposition, str]:
    """Reasoned disposition: reuse, adapt, reimplement, or reference-only.

    A link is not a license check; a license check is not a reproduced result;
    a reproduced headline is not proof on AdvisorAI data. Each gate is explicit.
    """
    normalized_license = license_id.strip()
    if relationship is ImplementationRelationship.UNVERIFIED_LEAD:
        return (
            ImplementationDisposition.REFERENCE_ONLY,
            "unverified lead: link recorded, authorship and behavior unconfirmed",
        )
    if normalized_license in _COPYLEFT_LICENSES:
        return (
            ImplementationDisposition.REIMPLEMENT_FROM_SPEC,
            f"{normalized_license} is copyleft: reimplement from the specification without copying",
        )
    if normalized_license not in _REUSE_LICENSES:
        return (
            ImplementationDisposition.REFERENCE_ONLY,
            f"license {normalized_license or 'unknown'} is not cleared for reuse",
        )
    if replication_evidence.strip().lower() in {"none", ""}:
        return (
            ImplementationDisposition.ADAPT_IN_ISOLATION,
            "permissive license but no replication evidence: adapt in isolation and verify",
        )
    if small_reviewed_scope:
        return (
            ImplementationDisposition.REUSE_COMPONENT,
            "permissive license with replication evidence: reuse a small reviewed component",
        )
    return (
        ImplementationDisposition.ADAPT_IN_ISOLATION,
        "replication evidence exists but scope is large: adapt in isolation",
    )


def build_implementation_record(
    *,
    repository_url: str,
    revision: str,
    source_hash: str,
    license_id: str,
    relationship: ImplementationRelationship,
    paper_card_id: UUID | None,
    dependencies: tuple[str, ...],
    data_assumptions: tuple[str, ...],
    entry_point: str,
    replication_evidence: str,
    small_reviewed_scope: bool,
) -> ImplementationRecord:
    """Assemble a validated record with a reasoned disposition."""
    disposition, rationale = recommend_disposition(
        relationship=relationship,
        license_id=license_id,
        replication_evidence=replication_evidence,
        small_reviewed_scope=small_reviewed_scope,
    )
    return ImplementationRecord(
        repository_url=repository_url,
        revision=revision,
        source_hash=source_hash,
        license=license_id,
        relationship=relationship,
        paper_card_id=paper_card_id,
        dependencies=dependencies,
        data_assumptions=data_assumptions,
        entry_point=entry_point,
        replication_evidence=replication_evidence,
        disposition=disposition,
        disposition_rationale=rationale,
        observed_at=datetime.now(UTC),
    )


def discovery_fingerprint(report: CodeDiscoveryReport) -> str:
    """Stable identity for a discovery result set."""
    payload = json.dumps(
        {
            "paper": str(report.paper_card_id) if report.paper_card_id else None,
            "leads": sorted(lead.repository_url for lead in report.leads),
            "records": sorted(str(item.artifact_id) for item in report.records),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


__all__ = [
    "CODE_DISCOVERY_VERSION",
    "CodeDiscoveryReport",
    "CodeLead",
    "assess_release_pin",
    "build_implementation_record",
    "classify_relationship",
    "discovery_fingerprint",
    "extract_code_leads",
    "hash_source_bytes",
    "recommend_disposition",
]
