"""Fail-closed source and use policy for Research Brain literature intake."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .cards import (
    PaperCard,
    ResearchLicenseEvidenceSource,
    ResearchMetadataProvider,
    ResearchSourceFamily,
    ResearchSourceUse,
)

SOURCE_POLICY_VERSION = "research-source-policy-v3"

_ARXIV_PATH = re.compile(
    r"^/(?:abs|pdf|html)/(?:\d{4}\.\d{4,5}|[A-Za-z][A-Za-z0-9.-]*/\d{7})(?:v\d+)?$"
)
_OPENALEX_PATH = re.compile(r"^/W\d+$")
_CROSSREF_PATH = re.compile(r"^/10\.\d{4,9}/[A-Za-z0-9._;()/:+-]+$")
_GITHUB_RELEASE_PATH = re.compile(
    r"^/[A-Za-z0-9][A-Za-z0-9.-]{0,38}/[A-Za-z0-9._-]+/releases/(?:latest|tag/[A-Za-z0-9._+-]+)$"
)
_SEC_EDGAR_PATH = re.compile(
    r"^/Archives/edgar/data/\d{1,10}/\d{18}/[A-Za-z0-9._-]+\.(?:htm|html|txt|pdf)$",
    re.IGNORECASE,
)
_FRED_PATH = re.compile(r"^/series/[A-Za-z0-9_-]+$")
_ARXIV_VERSION_SUFFIX = re.compile(r"v[1-9]\d*$")

_CONTENT_LICENSES: dict[ResearchSourceFamily, frozenset[str]] = {
    ResearchSourceFamily.ARXIV: frozenset({"CC0-1.0", "CC-BY-4.0"}),
    ResearchSourceFamily.OPENALEX: frozenset({"CC0-1.0", "CC-BY-4.0"}),
    ResearchSourceFamily.CROSSREF: frozenset({"CC0-1.0", "CC-BY-4.0"}),
    ResearchSourceFamily.GITHUB_RELEASE: frozenset(
        {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause"}
    ),
    ResearchSourceFamily.SEC_EDGAR: frozenset(),
    ResearchSourceFamily.FRED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ResearchSourceAssessment:
    family: ResearchSourceFamily
    requested_use: ResearchSourceUse
    permitted_use: ResearchSourceUse
    policy_version: str = SOURCE_POLICY_VERSION


class ResearchSourcePolicy:
    """Classify reviewed HTTPS source paths and constrain their recorded use.

    Citation-only intake validates a submitted claim without asserting rights.
    Content use additionally requires first-party, exact-version license
    evidence and a hash of that exact source document. Unknown or unreviewed
    sources cannot enter the canonical registry.
    """

    version = SOURCE_POLICY_VERSION

    def assess(
        self,
        *,
        source_url: str,
        license_claim: str,
        requested_use: ResearchSourceUse,
    ) -> ResearchSourceAssessment:
        try:
            requested_use = ResearchSourceUse(requested_use)
        except ValueError:
            raise ValueError("research source use must be a supported policy value") from None
        family = self._source_family(source_url)
        if requested_use is ResearchSourceUse.CONTENT_USE:
            allowed_licenses = _CONTENT_LICENSES[family]
            if license_claim not in allowed_licenses:
                raise ValueError(
                    f"content use requires an allowlisted license identifier for {family.value}"
                )
            permitted_use = ResearchSourceUse.CONTENT_USE
        else:
            permitted_use = ResearchSourceUse.CITATION_ONLY
        return ResearchSourceAssessment(
            family=family,
            requested_use=requested_use,
            permitted_use=permitted_use,
        )

    def assess_card(self, card: PaperCard) -> PaperCard:
        """Stamp a card with this policy's derived source and use assessment."""
        assessment = self.assess(
            source_url=card.source_url,
            license_claim=card.license,
            requested_use=card.requested_use,
        )
        metadata_family = {
            ResearchMetadataProvider.ARXIV_API: ResearchSourceFamily.ARXIV,
            ResearchMetadataProvider.OPENALEX_API: ResearchSourceFamily.OPENALEX,
            ResearchMetadataProvider.CROSSREF_API: ResearchSourceFamily.CROSSREF,
            ResearchMetadataProvider.GITHUB_RELEASE_API: ResearchSourceFamily.GITHUB_RELEASE,
            ResearchMetadataProvider.SEC_EDGAR_API: ResearchSourceFamily.SEC_EDGAR,
            ResearchMetadataProvider.FRED_API: ResearchSourceFamily.FRED,
        }.get(card.source_metadata_provider)
        if metadata_family is not None and metadata_family is not assessment.family:
            raise ValueError("metadata provider does not match the cited source family")
        if card.source_version_identity_sha256 is not None:
            version_identity = json.dumps(
                {
                    "provider": card.source_metadata_provider.value,
                    "reference": card.source_version_reference,
                    "label": card.source_version_label,
                    "metadata_sha256": card.source_metadata_sha256,
                    "doi": card.source_doi,
                    "document_reference": card.source_document_reference
                    if (
                        assessment.family is ResearchSourceFamily.OPENALEX
                        and card.requested_use is ResearchSourceUse.CONTENT_USE
                    )
                    else None,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            expected_version_hash = hashlib.sha256(version_identity.encode("utf-8")).hexdigest()
            if card.source_version_identity_sha256 != expected_version_hash:
                raise ValueError(
                    "source version identity hash does not match its metadata evidence"
                )
        evidence_family = {
            ResearchLicenseEvidenceSource.ARXIV_METADATA: ResearchSourceFamily.ARXIV,
            ResearchLicenseEvidenceSource.ARXIV_FIRST_PARTY_VERSION_PAGE: ResearchSourceFamily.ARXIV,
            ResearchLicenseEvidenceSource.OPENALEX_AGGREGATED_METADATA: (
                ResearchSourceFamily.OPENALEX
            ),
            ResearchLicenseEvidenceSource.OPENALEX_FIRST_PARTY_VERSION_PAGE: (
                ResearchSourceFamily.OPENALEX
            ),
            ResearchLicenseEvidenceSource.CROSSREF_DEPOSIT_METADATA: (
                ResearchSourceFamily.CROSSREF
            ),
        }.get(card.source_license_evidence_source)
        if evidence_family is not None and evidence_family is not assessment.family:
            raise ValueError("license evidence source does not match the cited source family")
        if assessment.permitted_use is ResearchSourceUse.CONTENT_USE:
            if (
                card.source_version_reference is None
                or card.source_document_sha256 is None
                or card.source_document_observed_at is None
                or card.source_document_reference is None
                or card.source_license_evidence_reference != card.source_version_reference
            ):
                raise ValueError(
                    "content use requires first-party exact-version license evidence "
                    "and a hash of the verified source document"
                )
            if assessment.family is ResearchSourceFamily.ARXIV:
                if (
                    card.source_license_evidence_source
                    is not ResearchLicenseEvidenceSource.ARXIV_FIRST_PARTY_VERSION_PAGE
                ):
                    raise ValueError(
                        "arXiv content use requires first-party exact-version license evidence"
                    )
                if not self._same_explicit_arxiv_version(
                    card.source_url,
                    card.source_version_reference,
                ):
                    raise ValueError("content use requires the explicitly cited arXiv version")
                if card.source_document_reference != self._arxiv_pdf_reference(
                    card.source_version_reference
                ):
                    raise ValueError("arXiv source document reference does not match its version")
            elif assessment.family is ResearchSourceFamily.OPENALEX:
                if (
                    card.source_metadata_provider is not ResearchMetadataProvider.OPENALEX_API
                    or card.source_license_evidence_source
                    is not ResearchLicenseEvidenceSource.OPENALEX_FIRST_PARTY_VERSION_PAGE
                    or not self._canonical_doi(card.source_doi)
                    or not self._canonical_https_reference(card.source_version_reference)
                    or not self._canonical_https_reference(card.source_document_reference)
                ):
                    raise ValueError(
                        "OpenAlex content use requires first-party DOI, page, and document evidence"
                    )
            else:
                raise ValueError("content use requires first-party exact-version license evidence")
        payload = card.model_dump(mode="python")
        payload.update(
            source_family=assessment.family,
            permitted_use=assessment.permitted_use,
            source_policy_version=assessment.policy_version,
        )
        return PaperCard.model_validate(payload)

    @classmethod
    def _same_explicit_arxiv_version(cls, source_url: str, version_reference: str) -> bool:
        try:
            if cls._source_family(version_reference) is not ResearchSourceFamily.ARXIV:
                return False
            source_path = urlsplit(source_url).path
            version_path = urlsplit(version_reference).path
        except ValueError:
            return False
        source_id = "/".join(source_path.split("/")[2:])
        version_id = "/".join(version_path.split("/")[2:])
        source_version = _ARXIV_VERSION_SUFFIX.search(source_id)
        version = _ARXIV_VERSION_SUFFIX.search(version_id)
        if source_version is None or version is None:
            return False
        return (
            _ARXIV_VERSION_SUFFIX.sub("", source_id) == _ARXIV_VERSION_SUFFIX.sub("", version_id)
            and source_version.group() == version.group()
        )

    @staticmethod
    def _arxiv_pdf_reference(version_reference: str) -> str | None:
        try:
            if (
                ResearchSourcePolicy._source_family(version_reference)
                is not ResearchSourceFamily.ARXIV
            ):
                return None
            path = urlsplit(version_reference).path
        except ValueError:
            return None
        if not path.startswith("/abs/"):
            return None
        return f"https://arxiv.org/pdf/{path.removeprefix('/abs/')}"

    @staticmethod
    def _canonical_https_reference(value: str | None) -> bool:
        if value is None:
            return False
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            return False
        return bool(
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and port is None
            and parsed.netloc.lower() == parsed.hostname.lower()
            and not parsed.netloc.endswith(".")
            and not parsed.query
            and not parsed.fragment
            and "\\" not in value
            and "//" not in parsed.path
            and not any(segment in {".", ".."} for segment in parsed.path.split("/"))
            and not any(ord(character) < 0x20 or character.isspace() for character in value)
        )

    @classmethod
    def _canonical_doi(cls, value: str | None) -> bool:
        if not cls._canonical_https_reference(value):
            return False
        try:
            parsed = urlsplit(value)
        except ValueError:
            return False
        return (
            parsed.hostname == "doi.org"
            and _CROSSREF_PATH.fullmatch(parsed.path) is not None
            and value == f"https://doi.org{parsed.path.casefold()}"
        )

    @staticmethod
    def _source_family(source_url: str) -> ResearchSourceFamily:
        if (
            not source_url
            or len(source_url) > 2048
            or source_url != source_url.strip()
            or any(ord(character) < 0x20 or character.isspace() for character in source_url)
            or "\\" in source_url
            or "?" in source_url
            or "#" in source_url
        ):
            raise ValueError("research source URL is malformed or contains query/fragment data")
        try:
            parsed = urlsplit(source_url)
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            raise ValueError("research source URL is malformed") from None
        if (
            parsed.scheme.lower() != "https"
            or host is None
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.netloc.lower() != host.lower()
            or parsed.netloc.endswith(".")
            or "%" in parsed.path
            or "//" in parsed.path
            or any(segment in {".", ".."} for segment in parsed.path.split("/"))
        ):
            raise ValueError("research source URL is not a canonical allowlisted HTTPS URL")

        host = host.lower()
        path = parsed.path
        exact_rules = (
            (host == "arxiv.org" and _ARXIV_PATH.fullmatch(path), ResearchSourceFamily.ARXIV),
            (
                host == "openalex.org" and _OPENALEX_PATH.fullmatch(path),
                ResearchSourceFamily.OPENALEX,
            ),
            (host == "doi.org" and _CROSSREF_PATH.fullmatch(path), ResearchSourceFamily.CROSSREF),
            (
                host == "github.com" and _GITHUB_RELEASE_PATH.fullmatch(path),
                ResearchSourceFamily.GITHUB_RELEASE,
            ),
            (
                host in {"sec.gov", "www.sec.gov"} and _SEC_EDGAR_PATH.fullmatch(path),
                ResearchSourceFamily.SEC_EDGAR,
            ),
            (
                host == "fred.stlouisfed.org" and _FRED_PATH.fullmatch(path),
                ResearchSourceFamily.FRED,
            ),
        )
        for matches, family in exact_rules:
            if matches:
                return family
        raise ValueError("research source URL host or path is not in the reviewed allowlist")


__all__ = [
    "ResearchSourceAssessment",
    "ResearchSourcePolicy",
    "SOURCE_POLICY_VERSION",
]
