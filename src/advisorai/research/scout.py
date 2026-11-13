"""Bounded, read-only citation metadata Scout for reviewed paper sources."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urljoin, urlsplit
from uuid import UUID, uuid4

from advisorai.integrations.http import HttpClientConfig, SafeHttpClient

from .cards import (
    PaperCard,
    ResearchLicenseEvidenceSource,
    ResearchMetadataProvider,
    ResearchSourceFamily,
    ResearchSourceUse,
)
from .jobs import JobState, ResearchCoordinator, ResearchJob, ResearchRole
from .registry import ResearchRegistry
from .source_policy import ResearchSourcePolicy

_MAX_METADATA_RESPONSE_BYTES = 512 * 1024
_MAX_SOURCE_DOCUMENT_BYTES = 64 * 1024 * 1024
_MAX_SCOUT_JOB_CARD_BYTES = 64 * 1024
_METADATA_TIMEOUT_SECONDS = 15.0
_ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
_ARXIV_NAMESPACE = "http://arxiv.org/schemas/atom"
_ARXIV_VERSION_SUFFIX = re.compile(r"v[1-9]\d*$")


@dataclass(frozen=True, slots=True)
class ResearchScoutRun:
    job: ResearchJob
    paper: PaperCard | None


@dataclass(frozen=True, slots=True)
class _SourceMetadata:
    provider: ResearchMetadataProvider
    title: str
    authors: tuple[str, ...]
    publication_date: str
    license_claim: str
    response_sha256: str
    response_size_bytes: int
    observed_at: datetime
    version_reference: str | None
    version_label: str | None
    license_evidence_source: ResearchLicenseEvidenceSource | None
    license_evidence_reference: str | None


@dataclass(frozen=True, slots=True)
class _VerifiedArxivSource:
    license_claim: str
    license_evidence_sha256: str
    license_evidence_reference: str
    license_evidence_observed_at: datetime
    document_sha256: str
    document_size_bytes: int
    document_observed_at: datetime


class ResearchMetadataScout:
    """Fetch one citation record inside the coordinator's bounded job model.

    Metadata is size-capped, hashed, and reduced to typed fields. Explicit
    arXiv content-use jobs first verify the exact-version page and then hash a
    bounded PDF; neither response body is saved. The coordinator's durable job
    detail is sufficient to resume after a restart, and registry observation
    keys prevent duplicate records when a completed fetch is replayed after a
    process interruption.
    """

    _MIN_REQUEST_INTERVAL_SECONDS = {
        ResearchMetadataProvider.ARXIV_API: 3.0,
        ResearchMetadataProvider.OPENALEX_API: 1.0,
        ResearchMetadataProvider.CROSSREF_API: 1.0,
        ResearchMetadataProvider.GITHUB_RELEASE_API: 60.0,
    }

    def __init__(
        self,
        *,
        registry: ResearchRegistry,
        coordinator: ResearchCoordinator,
        clients: dict[ResearchMetadataProvider, SafeHttpClient] | None = None,
        source_clients: dict[str, SafeHttpClient] | None = None,
    ) -> None:
        self.registry = registry
        self.coordinator = coordinator
        self.source_policy = ResearchSourcePolicy()
        self.clients = clients or self._build_clients()
        self.source_clients = source_clients or self._build_source_clients()
        self._rate_lock = threading.Lock()
        self._next_request_at: dict[ResearchMetadataProvider, float] = {}

    def submit(
        self,
        paper: PaperCard,
        *,
        execution_budget_seconds: float = 60.0,
    ) -> ResearchJob:
        source_assessment = self.source_policy.assess(
            source_url=paper.source_url,
            license_claim=paper.license,
            requested_use=ResearchSourceUse.CITATION_ONLY,
        )
        if (
            paper.requested_use is ResearchSourceUse.CONTENT_USE
            and source_assessment.family is not ResearchSourceFamily.ARXIV
        ):
            raise ValueError(
                "content use is not enabled without a first-party source-version verifier"
            )
        if (
            paper.requested_use is ResearchSourceUse.CONTENT_USE
            and _ARXIV_VERSION_SUFFIX.search(urlsplit(paper.source_url).path) is None
        ):
            raise ValueError("arXiv content use requires an explicitly versioned citation URL")
        if not 0 < execution_budget_seconds <= 60:
            raise ValueError("metadata Scout execution budget must be within 60 seconds")
        detail = paper.model_dump_json(round_trip=True)
        if len(detail.encode("utf-8")) > _MAX_SCOUT_JOB_CARD_BYTES:
            raise ValueError("metadata Scout card exceeds its durable job size limit")
        return self.coordinator.submit(
            ResearchRole.SCOUT,
            budget_units=1,
            execution_budget_seconds=execution_budget_seconds,
            detail=detail,
        )

    def run(self, job_id: UUID) -> ResearchScoutRun:
        job = next((item for item in self.coordinator.jobs() if item.job_id == job_id), None)
        if job is None:
            raise KeyError(f"unknown metadata Scout job {job_id}")
        if job.role is not ResearchRole.SCOUT:
            raise ValueError("metadata Scout can run only Scout-role jobs")
        if job.state is JobState.CANCELLED:
            return ResearchScoutRun(job=job, paper=None)
        try:
            checkpoint_or_detail = job.checkpoint_detail or job.detail
            paper = _paper_from_job_detail(checkpoint_or_detail)
        except Exception as exc:
            raise ValueError("durable Scout job does not contain a valid PaperCard") from exc

        stored: dict[str, PaperCard] = {}

        def worker(_running: ResearchJob, cancelled) -> str:
            if cancelled():
                return "cancelled_before_fetch"
            self.coordinator.checkpoint(
                job_id,
                detail=_checkpoint_payload(paper, stage="metadata_fetch_started"),
            )
            remaining = _running.execution_budget_seconds - _running.budget_consumed_seconds
            deadline = time.monotonic() + max(0.0, remaining)
            try:
                metadata = self._fetch_metadata(paper, cancelled=cancelled, deadline=deadline)
            except _RateBudgetExpired:
                if cancelled():
                    return "cancelled_before_fetch"
                raise
            if cancelled():
                return "cancelled_before_registry_write"
            self.coordinator.checkpoint(
                job_id,
                detail=_checkpoint_payload(
                    paper,
                    stage="metadata_fetched",
                    response_sha256=metadata.response_sha256,
                ),
            )
            if cancelled():
                return "cancelled_before_registry_write"
            verified_source = None
            if paper.requested_use is ResearchSourceUse.CONTENT_USE:
                try:
                    verified_source = self._verify_arxiv_source(
                        paper,
                        metadata,
                        cancelled=cancelled,
                        deadline=deadline,
                    )
                except _RateBudgetExpired:
                    if cancelled():
                        return "cancelled_before_source_verification"
                    raise
            if cancelled():
                return "cancelled_before_registry_write"
            candidate = self._enrich(paper, metadata, verified_source=verified_source)
            accepted = self.registry.store(candidate, record_type="paper")
            stored["paper"] = accepted
            return (
                f"paper_artifact_id={accepted.artifact_id};"
                f"source_metadata_sha256={metadata.response_sha256}"
            )

        completed = self.coordinator.run(job_id, worker)
        return ResearchScoutRun(job=completed, paper=stored.get("paper"))

    def execute(
        self,
        paper: PaperCard,
        *,
        execution_budget_seconds: float = 45.0,
    ) -> ResearchScoutRun:
        """Submit and run a single source card, retaining durable job state."""
        job = self.submit(paper, execution_budget_seconds=execution_budget_seconds)
        return self.run(job.job_id)

    def _fetch_metadata(self, paper: PaperCard, *, cancelled, deadline: float) -> _SourceMetadata:
        assessment = self.source_policy.assess(
            source_url=paper.source_url,
            license_claim=paper.license,
            requested_use=ResearchSourceUse.CITATION_ONLY,
        )
        provider, endpoint, headers = self._metadata_endpoint(paper.source_url, assessment.family)
        client = self.clients.get(provider)
        if client is None:
            raise ValueError(f"metadata Scout has no reviewed client for {provider.value}")
        if not self._claim_request_slot(provider, cancelled=cancelled, deadline=deadline):
            raise _RateBudgetExpired("provider rate window exceeds the remaining Scout budget")
        response = client.get(
            endpoint,
            headers=headers,
            max_retries=0,
            timeout_seconds=_METADATA_TIMEOUT_SECONDS,
        )
        if len(response.body) > _MAX_METADATA_RESPONSE_BYTES:
            raise ValueError("metadata Scout response exceeds its configured size limit")
        if response.status_code != 200:
            raise ValueError("metadata Scout received an unexpected status")
        if _has_xml_declaration(response.body):
            raise ValueError("metadata Scout refuses XML document/entity declarations")
        try:
            parsed = self._parse_response(provider, response.body)
        except (ET.ParseError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            raise ValueError("metadata Scout response failed typed parsing") from None
        title = _required_text(parsed.get("title"), "title", maximum=2048)
        authors = _authors(parsed.get("authors"))
        publication_date = _optional_text(parsed.get("publication_date"), maximum=128)
        license_claim = _optional_text(parsed.get("license"), maximum=512) or "unknown"
        version_reference = _optional_text(parsed.get("version_reference"), maximum=2048)
        version_label = _optional_text(parsed.get("version_label"), maximum=128)
        if provider is ResearchMetadataProvider.OPENALEX_API and version_reference is not None:
            version_reference = _safe_https_reference(version_reference)
        if version_reference is None and version_label is not None:
            version_reference = paper.source_url
        if provider is ResearchMetadataProvider.ARXIV_API and version_reference is not None:
            version_reference, version_label = _validate_arxiv_version(
                paper.source_url,
                version_reference,
            )
        evidence_source_value = parsed.get("license_evidence_source")
        evidence_reference = _optional_text(parsed.get("license_evidence_reference"), maximum=2048)
        if evidence_source_value is None and license_claim != "unknown":
            evidence_source_value = {
                ResearchMetadataProvider.ARXIV_API: ResearchLicenseEvidenceSource.ARXIV_METADATA,
                ResearchMetadataProvider.OPENALEX_API: (
                    ResearchLicenseEvidenceSource.OPENALEX_AGGREGATED_METADATA
                ),
                ResearchMetadataProvider.CROSSREF_API: (
                    ResearchLicenseEvidenceSource.CROSSREF_DEPOSIT_METADATA
                ),
            }.get(provider)
        if evidence_source_value is not None and evidence_reference is None:
            evidence_reference = endpoint
        evidence_source = (
            ResearchLicenseEvidenceSource(evidence_source_value)
            if evidence_source_value is not None and evidence_reference is not None
            else None
        )
        response_sha256 = hashlib.sha256(response.body).hexdigest()
        return _SourceMetadata(
            provider=provider,
            title=title,
            authors=authors,
            publication_date=publication_date or "unknown",
            license_claim=license_claim,
            response_sha256=response_sha256,
            response_size_bytes=len(response.body),
            observed_at=response.fetched_at,
            version_reference=version_reference,
            version_label=version_label,
            license_evidence_source=evidence_source,
            license_evidence_reference=evidence_reference if evidence_source is not None else None,
        )

    def _verify_arxiv_source(
        self,
        paper: PaperCard,
        metadata: _SourceMetadata,
        *,
        cancelled,
        deadline: float,
    ) -> _VerifiedArxivSource:
        if metadata.version_reference is None or metadata.version_label is None:
            raise ValueError("content use requires an exact arXiv version reference")
        page_client = self.source_clients.get("arxiv_version_page")
        document_client = self.source_clients.get("arxiv_document")
        if page_client is None or document_client is None:
            raise ValueError("metadata Scout has no reviewed arXiv source verifier")
        if cancelled() or not self._claim_request_slot(
            ResearchMetadataProvider.ARXIV_API,
            cancelled=cancelled,
            deadline=deadline,
        ):
            raise _RateBudgetExpired("arXiv verification exceeds the remaining Scout budget")
        page = page_client.get(
            metadata.version_reference,
            headers={"Accept": "text/html"},
            max_retries=0,
            timeout_seconds=_METADATA_TIMEOUT_SECONDS,
        )
        if page.status_code != 200 or not _header_value(page.headers, "content-type").startswith(
            "text/html"
        ):
            raise ValueError("arXiv version page returned an unexpected response")
        license_claim = _arxiv_license_claim(page.body, metadata.version_reference)
        self.source_policy.assess(
            source_url=paper.source_url,
            license_claim=license_claim,
            requested_use=ResearchSourceUse.CONTENT_USE,
        )
        if cancelled() or not self._claim_request_slot(
            ResearchMetadataProvider.ARXIV_API,
            cancelled=cancelled,
            deadline=deadline,
        ):
            raise _RateBudgetExpired("arXiv document hash exceeds the remaining Scout budget")
        document_url = _arxiv_document_url(metadata.version_reference)
        document = document_client.get(
            document_url,
            headers={"Accept": "application/pdf"},
            max_retries=0,
            timeout_seconds=_METADATA_TIMEOUT_SECONDS,
        )
        if (
            document.status_code != 200
            or not _header_value(document.headers, "content-type").startswith("application/pdf")
            or not document.body.startswith(b"%PDF-")
        ):
            raise ValueError("arXiv exact-version document returned an unexpected response")
        return _VerifiedArxivSource(
            license_claim=license_claim,
            license_evidence_sha256=hashlib.sha256(page.body).hexdigest(),
            license_evidence_reference=metadata.version_reference,
            license_evidence_observed_at=page.fetched_at,
            document_sha256=hashlib.sha256(document.body).hexdigest(),
            document_size_bytes=len(document.body),
            document_observed_at=document.fetched_at,
        )

    def _claim_request_slot(self, provider, *, cancelled, deadline: float) -> bool:
        interval = self._MIN_REQUEST_INTERVAL_SECONDS[provider]
        while True:
            if cancelled() or time.monotonic() >= deadline:
                return False
            with self._rate_lock:
                now = time.monotonic()
                next_at = self._next_request_at.get(provider, 0.0)
                wait_seconds = max(0.0, next_at - now)
                if wait_seconds == 0:
                    self._next_request_at[provider] = now + interval
                    return True
                if now + wait_seconds >= deadline:
                    return False
            time.sleep(min(wait_seconds, 0.1))

    @staticmethod
    def _metadata_endpoint(
        source_url: str,
        family: ResearchSourceFamily,
    ) -> tuple[ResearchMetadataProvider, str, dict[str, str]]:
        path = urlsplit(source_url).path
        if family is ResearchSourceFamily.ARXIV:
            identifier = "/".join(path.split("/")[2:])
            query = urlencode({"id_list": identifier, "max_results": "1"})
            return (
                ResearchMetadataProvider.ARXIV_API,
                f"https://export.arxiv.org/api/query?{query}",
                {"Accept": "application/atom+xml"},
            )
        if family is ResearchSourceFamily.OPENALEX:
            identifier = path.removeprefix("/")
            return (
                ResearchMetadataProvider.OPENALEX_API,
                f"https://api.openalex.org/works/{identifier}",
                {"Accept": "application/json"},
            )
        if family is ResearchSourceFamily.CROSSREF:
            doi = path.removeprefix("/")
            return (
                ResearchMetadataProvider.CROSSREF_API,
                f"https://api.crossref.org/works/{quote(doi, safe='')}",
                {"Accept": "application/vnd.crossref-api-message+json, application/json"},
            )
        if family is ResearchSourceFamily.GITHUB_RELEASE:
            _, owner, repository, _releases, *tail = path.split("/")
            release_path = "latest" if tail == ["latest"] else f"tags/{tail[1]}"
            return (
                ResearchMetadataProvider.GITHUB_RELEASE_API,
                f"https://api.github.com/repos/{owner}/{repository}/releases/{release_path}",
                {
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        raise ValueError("metadata Scout does not support this reviewed citation family")

    @staticmethod
    def _parse_response(
        provider: ResearchMetadataProvider,
        body: bytes,
    ) -> dict[str, object]:
        if provider is ResearchMetadataProvider.ARXIV_API:
            root = ET.fromstring(body)
            entries = root.findall(f"{{{_ATOM_NAMESPACE}}}entry")
            if len(entries) != 1:
                raise ValueError("arXiv metadata response must contain exactly one entry")
            entry = entries[0]
            authors = [
                author.findtext(f"{{{_ATOM_NAMESPACE}}}name", default="")
                for author in entry.findall(f"{{{_ATOM_NAMESPACE}}}author")[:64]
            ]
            license_value = entry.findtext(f"{{{_ARXIV_NAMESPACE}}}license", default="")
            return {
                "title": entry.findtext(f"{{{_ATOM_NAMESPACE}}}title"),
                "authors": authors,
                "publication_date": entry.findtext(f"{{{_ATOM_NAMESPACE}}}published"),
                "license": license_value,
                "version_reference": entry.findtext(f"{{{_ATOM_NAMESPACE}}}id"),
                "license_evidence_reference": license_value,
            }

        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("metadata API response must be an object")
        if provider is ResearchMetadataProvider.OPENALEX_API:
            authorships = data.get("authorships")
            authors = []
            if isinstance(authorships, list):
                for authorship in authorships[:64]:
                    author = authorship.get("author") if isinstance(authorship, dict) else None
                    name = author.get("display_name") if isinstance(author, dict) else None
                    if isinstance(name, str):
                        authors.append(name)
            location = data.get("best_oa_location")
            if not isinstance(location, dict):
                locations = data.get("locations")
                location = locations[0] if isinstance(locations, list) and locations else None
            license_value = location.get("license") if isinstance(location, dict) else None
            version_label = location.get("version") if isinstance(location, dict) else None
            return {
                "title": data.get("title"),
                "authors": authors,
                "publication_date": data.get("publication_date"),
                "license": license_value,
                "version_label": version_label,
                "version_reference": (
                    location.get("landing_page_url") if isinstance(location, dict) else None
                ),
            }
        if provider is ResearchMetadataProvider.CROSSREF_API:
            message = data.get("message")
            if not isinstance(message, dict):
                raise ValueError("Crossref response has no message object")
            authors = []
            raw_authors = message.get("author")
            if isinstance(raw_authors, list):
                for author in raw_authors[:64]:
                    if not isinstance(author, dict):
                        continue
                    name = " ".join(
                        part for part in (author.get("given"), author.get("family")) if part
                    )
                    if name:
                        authors.append(name)
            licenses = message.get("license")
            license_value = None
            version_label = None
            license_entries = (
                [item for item in licenses if isinstance(item, dict)]
                if isinstance(licenses, list)
                else []
            )
            license_claims = {
                (item.get("URL"), item.get("content-version")) for item in license_entries
            }
            if len(license_claims) == 1:
                license_value, version_label = next(iter(license_claims))
            if not isinstance(version_label, str):
                resources = message.get("link")
                resource_versions = (
                    {
                        item.get("content-version")
                        for item in resources
                        if isinstance(item, dict) and isinstance(item.get("content-version"), str)
                    }
                    if isinstance(resources, list)
                    else set()
                )
                version_label = (
                    next(iter(resource_versions)) if len(resource_versions) == 1 else None
                )
            return {
                "title": _first_string(message.get("title")),
                "authors": authors,
                "publication_date": _crossref_publication_date(message),
                "license": license_value,
                "version_label": version_label,
            }
        if provider is ResearchMetadataProvider.GITHUB_RELEASE_API:
            author = data.get("author")
            authors = [author["login"]] if isinstance(author, dict) and author.get("login") else []
            return {
                "title": data.get("name") or data.get("tag_name"),
                "authors": authors,
                "publication_date": data.get("published_at"),
                "license": None,
                "version_label": str(data.get("id") or data.get("tag_name"))
                if data.get("id") or data.get("tag_name")
                else None,
            }
        raise ValueError("metadata Scout provider parser is unavailable")

    @staticmethod
    def _enrich(
        paper: PaperCard,
        metadata: _SourceMetadata,
        *,
        verified_source: _VerifiedArxivSource | None = None,
    ) -> PaperCard:
        payload = paper.model_dump(mode="python")
        version_hash = None
        if metadata.version_reference and metadata.version_label:
            version_hash = hashlib.sha256(
                json.dumps(
                    {
                        "provider": metadata.provider.value,
                        "reference": metadata.version_reference,
                        "label": metadata.version_label,
                        "metadata_sha256": metadata.response_sha256,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        payload.update(
            artifact_id=uuid4(),
            created_at=metadata.observed_at,
            title=metadata.title,
            authors=metadata.authors,
            publication_date=metadata.publication_date,
            license=(verified_source.license_claim if verified_source else metadata.license_claim),
            source_family=None,
            permitted_use=None,
            source_policy_version=None,
            source_metadata_provider=metadata.provider,
            source_metadata_sha256=metadata.response_sha256,
            source_metadata_size_bytes=metadata.response_size_bytes,
            source_metadata_observed_at=metadata.observed_at,
            source_version_reference=metadata.version_reference,
            source_version_label=metadata.version_label,
            source_version_identity_sha256=version_hash,
            source_license_evidence_source=(
                ResearchLicenseEvidenceSource.ARXIV_FIRST_PARTY_VERSION_PAGE
                if verified_source
                else metadata.license_evidence_source
            ),
            source_license_evidence_reference=(
                verified_source.license_evidence_reference
                if verified_source
                else metadata.license_evidence_reference
            ),
            source_license_evidence_sha256=(
                verified_source.license_evidence_sha256
                if verified_source
                else (
                    metadata.response_sha256
                    if metadata.license_evidence_source is not None
                    else None
                )
            ),
            source_license_evidence_observed_at=(
                verified_source.license_evidence_observed_at
                if verified_source
                else (
                    metadata.observed_at if metadata.license_evidence_source is not None else None
                )
            ),
            source_document_sha256=(verified_source.document_sha256 if verified_source else None),
            source_document_size_bytes=(
                verified_source.document_size_bytes if verified_source else None
            ),
            source_document_observed_at=(
                verified_source.document_observed_at if verified_source else None
            ),
        )
        return PaperCard.model_validate(payload)

    @staticmethod
    def _build_clients() -> dict[ResearchMetadataProvider, SafeHttpClient]:
        api_hosts = {
            ResearchMetadataProvider.ARXIV_API: "export.arxiv.org",
            ResearchMetadataProvider.OPENALEX_API: "api.openalex.org",
            ResearchMetadataProvider.CROSSREF_API: "api.crossref.org",
            ResearchMetadataProvider.GITHUB_RELEASE_API: "api.github.com",
        }
        return {
            provider: SafeHttpClient(
                HttpClientConfig(
                    allowed_hosts=(host,),
                    timeout_seconds=_METADATA_TIMEOUT_SECONDS,
                    max_retries=0,
                    # The Scout applies cancellation-aware per-provider pacing
                    # before calling the transport. Keep the client rate gate
                    # from adding a second, non-cancellable wait.
                    requests_per_second=100,
                    max_response_bytes=_MAX_METADATA_RESPONSE_BYTES,
                    user_agent="advisorai-v3/research-metadata-scout",
                )
            )
            for provider, host in api_hosts.items()
        }

    @staticmethod
    def _build_source_clients() -> dict[str, SafeHttpClient]:
        return {
            "arxiv_version_page": SafeHttpClient(
                HttpClientConfig(
                    allowed_hosts=("arxiv.org",),
                    timeout_seconds=_METADATA_TIMEOUT_SECONDS,
                    max_retries=0,
                    requests_per_second=100,
                    max_response_bytes=_MAX_METADATA_RESPONSE_BYTES,
                    user_agent="advisorai-v3/research-metadata-scout",
                )
            ),
            "arxiv_document": SafeHttpClient(
                HttpClientConfig(
                    allowed_hosts=("arxiv.org",),
                    timeout_seconds=_METADATA_TIMEOUT_SECONDS,
                    max_retries=0,
                    requests_per_second=100,
                    max_response_bytes=_MAX_SOURCE_DOCUMENT_BYTES,
                    user_agent="advisorai-v3/research-metadata-scout",
                )
            ),
        }


class _RateBudgetExpired(RuntimeError):
    """No provider slot fits inside the running job's remaining time budget."""


class _ArxivLicensePageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.license_links: list[str] = []
        self._active_href: str | None = None
        self._active_text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "a":
            self._active_href = dict(attrs).get("href")
            self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._active_href is not None:
            self._active_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._active_href is not None:
            if "license" in " ".join(self._active_text).casefold():
                self.license_links.append(self._active_href)
            self._active_href = None
            self._active_text = []


def _has_xml_declaration(body: bytes) -> bool:
    upper = body.upper()
    return b"<!DOCTYPE" in upper or b"<!ENTITY" in upper


def _arxiv_license_claim(body: bytes, version_reference: str) -> str:
    try:
        parser = _ArxivLicensePageParser()
        parser.feed(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("arXiv version page failed license parsing") from None
    claims = [
        _recognized_arxiv_license(urljoin(version_reference, href)) for href in parser.license_links
    ]
    if not claims or any(claim is None for claim in claims) or len(set(claims)) != 1:
        raise ValueError("arXiv version page does not establish one allowlisted license")
    return claims[0]


def _recognized_arxiv_license(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "creativecommons.org"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    path = parsed.path.rstrip("/")
    if path in {"/licenses/by/4.0", "/licenses/by/4.0/legalcode"}:
        return "CC-BY-4.0"
    if path in {
        "/publicdomain/zero/1.0",
        "/publicdomain/zero/1.0/legalcode",
    }:
        return "CC0-1.0"
    return None


def _arxiv_document_url(version_reference: str) -> str:
    parsed = urlsplit(version_reference)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "arxiv.org"
        or not parsed.path.startswith("/abs/")
    ):
        raise ValueError("arXiv source document reference is not canonical")
    return f"https://arxiv.org/pdf/{parsed.path.removeprefix('/abs/')}"


def _header_value(headers, name: str) -> str:
    return next((value.lower() for key, value in headers if key.lower() == name), "")


def _checkpoint_payload(
    paper: PaperCard,
    *,
    stage: str,
    response_sha256: str | None = None,
) -> str:
    return json.dumps(
        {
            "stage": stage,
            "paper": paper.model_dump(mode="json", round_trip=True),
            "response_sha256": response_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _paper_from_job_detail(detail: str) -> PaperCard:
    try:
        return PaperCard.model_validate_json(detail)
    except Exception:
        checkpoint = json.loads(detail)
        if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("paper"), dict):
            raise ValueError("Scout checkpoint does not contain a PaperCard") from None
        return PaperCard.model_validate(checkpoint["paper"])


def _validate_arxiv_version(
    source_url: str,
    returned_reference: str,
) -> tuple[str | None, str | None]:
    requested = urlsplit(source_url)
    returned = urlsplit(returned_reference)
    if (
        returned.scheme not in {"http", "https"}
        or returned.hostname != "arxiv.org"
        or returned.username is not None
        or returned.password is not None
        or returned.port is not None
        or returned.query
        or returned.fragment
        or not returned.path.startswith("/abs/")
    ):
        raise ValueError("arXiv returned an invalid version reference")
    requested_path_segments = requested.path.split("/", 2)
    requested_id = requested_path_segments[2] if len(requested_path_segments) == 3 else ""
    returned_id = returned.path.removeprefix("/abs/")
    requested_base = _ARXIV_VERSION_SUFFIX.sub("", requested_id)
    returned_base = _ARXIV_VERSION_SUFFIX.sub("", returned_id)
    if not requested_base or requested_base != returned_base:
        raise ValueError("arXiv response identifies a different source record")
    requested_version = _ARXIV_VERSION_SUFFIX.search(requested_id)
    returned_version = _ARXIV_VERSION_SUFFIX.search(returned_id)
    if (
        requested_version
        and returned_version
        and requested_version.group() != returned_version.group()
    ):
        raise ValueError("arXiv response version does not match the requested version")
    exact_id = returned_id
    if requested_version and not returned_version:
        exact_id = requested_id
        returned_version = requested_version
    if returned_version is None:
        return None, None
    return f"https://arxiv.org/abs/{exact_id}", returned_version.group()


def _safe_https_reference(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or parsed.netloc.lower() != parsed.hostname.lower()
        or parsed.netloc.endswith(".")
    ):
        return None
    return value


def _required_text(value: object, field: str, *, maximum: int) -> str:
    text = _optional_text(value, maximum=maximum)
    if text is None:
        raise ValueError(f"metadata response is missing {field}")
    return text


def _optional_text(value: object, *, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    return normalized[:maximum]


def _authors(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    normalized = tuple(
        text for item in value[:64] if (text := _optional_text(item, maximum=256)) is not None
    )
    return normalized


def _first_string(value: object) -> str | None:
    if isinstance(value, list) and value:
        return _optional_text(value[0], maximum=2048)
    return None


def _crossref_publication_date(message: dict[str, object]) -> str | None:
    for key in ("published-online", "published-print", "issued"):
        date_object = message.get(key)
        date_parts = date_object.get("date-parts") if isinstance(date_object, dict) else None
        if (
            not isinstance(date_parts, list)
            or not date_parts
            or not isinstance(date_parts[0], list)
        ):
            continue
        parts = date_parts[0]
        if not parts or len(parts) > 3 or not all(isinstance(part, int) for part in parts):
            continue
        return "-".join(
            f"{part:02d}" if index else f"{part:04d}" for index, part in enumerate(parts)
        )
    return None


__all__ = ["ResearchMetadataScout", "ResearchScoutRun"]
