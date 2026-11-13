"""The Research Scout stores bounded metadata with repeatable source evidence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from urllib.parse import parse_qs, urlsplit

import pytest

from advisorai.integrations import HttpClientConfig, SafeHttpClient
from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.research import (
    JobState,
    PaperCard,
    ResearchCoordinator,
    ResearchLicenseEvidenceSource,
    ResearchMetadataProvider,
    ResearchMetadataScout,
    ResearchRegistry,
    ResearchSourceFamily,
    ResearchSourceUse,
)

_ARXIV_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/cs.LG/0301001v2</id>
    <title>Metadata only title</title>
    <published>2024-01-02T00:00:00Z</published>
    <author><name>A. Researcher</name></author>
    <arxiv:license>unknown</arxiv:license>
    <summary>ABSTRACT BODY MUST NOT BE STORED</summary>
  </entry>
</feed>
"""
_FRED_API_KEY = "a" * 32


def _paper(source_url: str, *, use=ResearchSourceUse.CITATION_ONLY) -> PaperCard:
    return PaperCard(
        source_url=source_url,
        claimed_result="researcher supplied source claim",
        relevance="factor research",
        requested_use=use,
    )


def _scout(
    tmp_path,
    *,
    provider: ResearchMetadataProvider,
    host: str,
    response_body: bytes,
    response_sequence: list[bytes] | None = None,
    ledgers: SqliteLedgers | None = None,
    on_request=None,
    on_source_request=None,
    arxiv_version_page_body: bytes | None = None,
    arxiv_document_body: bytes | None = None,
):
    calls: list[str] = []
    responses = list(response_sequence or [])

    def requester(method, url, headers, body, timeout):
        calls.append(url)
        if on_request is not None:
            on_request()
        body = responses.pop(0) if responses else response_body
        return 200, body, (("Content-Type", "application/json"),)

    client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=(host,),
            max_retries=0,
            max_response_bytes=512 * 1024,
            requests_per_second=100,
            user_agent=(
                "AdvisorAI test suite research@example.org"
                if provider is ResearchMetadataProvider.SEC_EDGAR_API
                else "advisorai-v3/research-metadata-scout"
            ),
        ),
        requester=requester,
        sleeper=lambda _delay: None,
    )

    def source_requester(method, url, headers, body, timeout):
        calls.append(url)
        if on_source_request is not None:
            on_source_request()
        if urlsplit(url).path.startswith("/abs/"):
            return (
                200 if arxiv_version_page_body is not None else 404,
                arxiv_version_page_body or b"",
                (("Content-Type", "text/html; charset=utf-8"),),
            )
        return (
            200 if arxiv_document_body is not None else 404,
            arxiv_document_body or b"",
            (("Content-Type", "application/pdf"),),
        )

    source_clients = {
        "arxiv_version_page": SafeHttpClient(
            HttpClientConfig(
                allowed_hosts=("arxiv.org",),
                max_retries=0,
                max_response_bytes=512 * 1024,
                requests_per_second=100,
            ),
            requester=source_requester,
            sleeper=lambda _delay: None,
        ),
        "arxiv_document": SafeHttpClient(
            HttpClientConfig(
                allowed_hosts=("arxiv.org",),
                max_retries=0,
                max_response_bytes=64 * 1024 * 1024,
                requests_per_second=100,
            ),
            requester=source_requester,
            sleeper=lambda _delay: None,
        ),
    }
    coordinator = ResearchCoordinator(ledgers, max_workers=2)
    scout = ResearchMetadataScout(
        registry=ResearchRegistry(tmp_path / "research.sqlite3"),
        coordinator=coordinator,
        clients={provider: client},
        source_clients=source_clients,
        fred_api_key=_FRED_API_KEY if provider is ResearchMetadataProvider.FRED_API else None,
    )
    return scout, coordinator, calls


@pytest.mark.parametrize(
    ("source_url", "provider", "host", "body", "expected_path"),
    [
        (
            "https://arxiv.org/abs/cs.LG/0301001",
            ResearchMetadataProvider.ARXIV_API,
            "export.arxiv.org",
            _ARXIV_XML,
            "/api/query",
        ),
        (
            "https://openalex.org/W2741809807",
            ResearchMetadataProvider.OPENALEX_API,
            "api.openalex.org",
            json.dumps(
                {
                    "title": "OpenAlex title",
                    "publication_date": "2024-02-03",
                    "authorships": [{"author": {"display_name": "OpenAlex author"}}],
                    "open_access": {"license": "cc-by"},
                    "best_oa_location": {
                        "landing_page_url": "https://example.org/work",
                        "version": "publishedVersion",
                        "license": "CC-BY-4.0",
                    },
                    "abstract_inverted_index": {"secret abstract": [0]},
                }
            ).encode(),
            "/works/W2741809807",
        ),
        (
            "https://doi.org/10.1234/example.doi",
            ResearchMetadataProvider.CROSSREF_API,
            "api.crossref.org",
            json.dumps(
                {
                    "message": {
                        "title": ["Crossref title"],
                        "author": [{"given": "Crossref", "family": "Author"}],
                        "published-online": {"date-parts": [[2023, 4, 5]]},
                        "license": [
                            {
                                "URL": "https://creativecommons.org/licenses/by/4.0/",
                                "content-version": "vor",
                            }
                        ],
                    }
                }
            ).encode(),
            "/works/10.1234%2Fexample.doi",
        ),
        (
            "https://github.com/pandas-dev/pandas/releases/tag/v2.2.0",
            ResearchMetadataProvider.GITHUB_RELEASE_API,
            "api.github.com",
            json.dumps(
                {
                    "name": "Pandas 2.2.0",
                    "id": 123456,
                    "tag_name": "v2.2.0",
                    "published_at": "2024-01-29T12:00:00Z",
                    "author": {"login": "release-author"},
                    "body": "RELEASE BODY MUST NOT BE STORED",
                }
            ).encode(),
            "/repos/pandas-dev/pandas/releases/tags/v2.2.0",
        ),
        (
            "https://www.sec.gov/Archives/edgar/data/320193/000032019323000064/aapl-20230930.htm",
            ResearchMetadataProvider.SEC_EDGAR_API,
            "data.sec.gov",
            json.dumps(
                {
                    "cik": "320193",
                    "name": "Apple Inc.",
                    "filings": {
                        "recent": {
                            "accessionNumber": ["0000320193-23-000064"],
                            "form": ["10-K"],
                            "filingDate": ["2023-11-03"],
                            "primaryDocument": ["aapl-20230930.htm"],
                        },
                        "files": [],
                    },
                }
            ).encode(),
            "/submissions/CIK0000320193.json",
        ),
        (
            "https://fred.stlouisfed.org/series/UNRATE",
            ResearchMetadataProvider.FRED_API,
            "api.stlouisfed.org",
            json.dumps(
                {
                    "seriess": [
                        {
                            "id": "UNRATE",
                            "title": "Unemployment Rate",
                            "last_updated": "2024-05-03 07:45:01-05",
                            "realtime_end": "2024-05-03",
                        }
                    ]
                }
            ).encode(),
            "/fred/series",
        ),
    ],
)
def test_metadata_scout_uses_pinned_endpoints_and_persists_only_hashes(
    tmp_path, source_url, provider, host, body, expected_path
):
    ledger_path = tmp_path / "mission.sqlite3"
    ledgers = SqliteLedgers(ledger_path)
    scout, coordinator, calls = _scout(
        tmp_path,
        provider=provider,
        host=host,
        response_body=body,
        ledgers=ledgers,
    )
    submitted = scout.submit(_paper(source_url))
    # The durable coordinator can be reconstructed before this job runs.
    restarted = ResearchCoordinator(ledgers, max_workers=2)
    scout = ResearchMetadataScout(
        registry=scout.registry,
        coordinator=restarted,
        clients=scout.clients,
        fred_api_key=_FRED_API_KEY if provider is ResearchMetadataProvider.FRED_API else None,
    )

    result = scout.run(submitted.job_id)

    assert result.job.state is JobState.COMPLETED
    assert result.paper is not None
    assert calls[0].startswith(f"https://{host}/")
    assert urlsplit(calls[0]).path == expected_path
    assert result.paper.source_metadata_provider is provider
    assert result.paper.source_metadata_sha256 is not None
    assert result.paper.source_metadata_size_bytes == len(body)
    assert result.paper.source_metadata_observed_at is not None
    assert result.paper.source_version_reference is not None
    assert result.paper.source_version_label is not None
    assert result.paper.source_version_identity_sha256 is not None
    assert (
        result.paper.source_family
        is {
            ResearchMetadataProvider.ARXIV_API: ResearchSourceFamily.ARXIV,
            ResearchMetadataProvider.OPENALEX_API: ResearchSourceFamily.OPENALEX,
            ResearchMetadataProvider.CROSSREF_API: ResearchSourceFamily.CROSSREF,
            ResearchMetadataProvider.GITHUB_RELEASE_API: ResearchSourceFamily.GITHUB_RELEASE,
            ResearchMetadataProvider.SEC_EDGAR_API: ResearchSourceFamily.SEC_EDGAR,
            ResearchMetadataProvider.FRED_API: ResearchSourceFamily.FRED,
        }[provider]
    )
    if provider is ResearchMetadataProvider.SEC_EDGAR_API:
        assert result.paper.title == "Apple Inc. 10-K filing"
        assert result.paper.publication_date == "2023-11-03"
        assert result.paper.source_version_label == "0000320193-23-000064"
        assert "@example.org" in scout.clients[provider].config.user_agent
    if provider is ResearchMetadataProvider.FRED_API:
        assert result.paper.title == "Unemployment Rate"
        assert result.paper.source_version_reference == "https://fred.stlouisfed.org/series/UNRATE"
        assert _FRED_API_KEY not in result.job.detail
        assert _FRED_API_KEY not in result.paper.model_dump_json()
    assert result.paper.title != "untitled"
    assert "RESEARCHER SUPPLIED" in result.paper.claimed_result.upper()
    assert "ABSTRACT BODY MUST NOT BE STORED" not in result.paper.model_dump_json()
    assert "RELEASE BODY MUST NOT BE STORED" not in result.paper.model_dump_json()
    assert coordinator.jobs()  # The original process retains its queued snapshot.
    assert ledgers.events(LedgerNamespace.MISSION)

    if provider is ResearchMetadataProvider.ARXIV_API:
        query = parse_qs(urlsplit(calls[0]).query)
        assert query == {"id_list": ["cs.LG/0301001"], "max_results": ["1"]}
        assert result.paper.source_version_reference == ("https://arxiv.org/abs/cs.LG/0301001v2")
        assert result.paper.source_version_label == "v2"
        assert result.paper.source_license_evidence_sha256 is None
    elif provider is ResearchMetadataProvider.OPENALEX_API:
        assert result.paper.source_version_reference == "https://example.org/work"
        assert result.paper.source_version_label == "publishedVersion"
        assert result.paper.source_license_evidence_source.value == "openalex_aggregated_metadata"
    elif provider is ResearchMetadataProvider.CROSSREF_API:
        assert result.paper.source_version_label == "vor"
        assert result.paper.source_license_evidence_source.value == "crossref_deposit_metadata"
    elif provider is ResearchMetadataProvider.GITHUB_RELEASE_API:
        assert result.paper.source_version_label == "123456"
        assert result.paper.source_license_evidence_sha256 is None

    with sqlite3.connect(scout.registry.database_path) as connection:
        stored_payload = connection.execute(
            "SELECT payload_json FROM research_records WHERE artifact_id = ?",
            (str(result.paper.artifact_id),),
        ).fetchone()[0]
    assert body.decode("utf-8", errors="ignore")[:64] not in stored_payload


def test_sec_metadata_scout_follows_bounded_historical_submissions_reference(tmp_path):
    index = json.dumps(
        {
            "cik": "320193",
            "name": "Apple Inc.",
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-24-000001"],
                    "form": ["8-K"],
                    "filingDate": ["2024-01-02"],
                    "primaryDocument": ["apple-8k.htm"],
                },
                "files": [
                    {
                        "name": "CIK0000320193-submissions-001.json",
                        "filingFrom": "2020-01-01",
                        "filingTo": "2023-12-31",
                    }
                ],
            },
        }
    ).encode()
    historical = json.dumps(
        {
            "cik": "320193",
            "name": "Apple Inc.",
            "accessionNumber": ["0000320193-23-000064"],
            "form": ["10-K"],
            "filingDate": ["2023-11-03"],
            "primaryDocument": ["aapl-20230930.htm"],
        }
    ).encode()
    scout, _coordinator, calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.SEC_EDGAR_API,
        host="data.sec.gov",
        response_body=index,
        response_sequence=[index, historical],
    )

    result = scout.execute(
        _paper(
            "https://www.sec.gov/Archives/edgar/data/320193/000032019323000064/aapl-20230930.htm"
        )
    )

    assert result.paper is not None
    assert result.paper.title == "Apple Inc. 10-K filing"
    assert len(calls) == 2
    assert calls[1] == "https://data.sec.gov/submissions/CIK0000320193-submissions-001.json"
    assert result.paper.source_metadata_size_bytes == len(index) + len(historical)


def test_sec_metadata_scout_rejects_accession_document_mismatch(tmp_path):
    body = json.dumps(
        {
            "cik": "320193",
            "name": "Apple Inc.",
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-23-000064"],
                    "form": ["10-K"],
                    "filingDate": ["2023-11-03"],
                    "primaryDocument": ["different-filing.htm"],
                },
                "files": [],
            },
        }
    ).encode()
    scout, _coordinator, calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.SEC_EDGAR_API,
        host="data.sec.gov",
        response_body=body,
    )

    result = scout.execute(
        _paper(
            "https://www.sec.gov/Archives/edgar/data/320193/000032019323000064/aapl-20230930.htm"
        )
    )

    assert result.job.state is JobState.INTERRUPTED
    assert result.paper is None
    assert len(calls) == 1
    assert scout.registry.count("paper") == 0


def test_fred_requires_an_explicit_key_and_never_admits_content_use(tmp_path):
    with pytest.raises(ValueError, match="explicitly configured API key"):
        ResearchMetadataScout._metadata_endpoint(
            "https://fred.stlouisfed.org/series/UNRATE",
            ResearchSourceFamily.FRED,
        )

    body = json.dumps({"seriess": [{"id": "UNRATE", "title": "Unemployment Rate"}]}).encode()
    scout, _coordinator, calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.FRED_API,
        host="api.stlouisfed.org",
        response_body=body,
    )
    with pytest.raises(ValueError, match="first-party source-version verifier"):
        scout.submit(
            _paper(
                "https://fred.stlouisfed.org/series/UNRATE",
                use=ResearchSourceUse.CONTENT_USE,
            )
        )
    assert not calls


def test_repeated_scout_observation_is_idempotent_across_job_restart(tmp_path):
    body = json.dumps(
        {
            "title": "same metadata",
            "publication_date": "2025-01-02",
            "authorships": [],
            "open_access": {"license": None},
            "best_oa_location": {"version": "publishedVersion", "license": None},
        }
    ).encode()
    scout, coordinator, calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.OPENALEX_API,
        host="api.openalex.org",
        response_body=body,
    )
    source = _paper("https://openalex.org/W2741809807")
    first = scout.execute(source)
    second = scout.execute(source)

    assert first.paper is not None and second.paper is not None
    assert first.paper.artifact_id == second.paper.artifact_id
    assert first.paper.source_metadata_sha256 == second.paper.source_metadata_sha256
    assert scout.registry.count("paper") == 1
    assert len(calls) == 2
    assert len(coordinator.jobs()) == 2


def test_source_version_identity_hash_cannot_be_detached_from_its_metadata(tmp_path):
    body = json.dumps(
        {
            "title": "versioned work",
            "authorships": [],
            "best_oa_location": {"version": "publishedVersion"},
        }
    ).encode()
    scout, _coordinator, _calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.OPENALEX_API,
        host="api.openalex.org",
        response_body=body,
    )
    result = scout.execute(_paper("https://openalex.org/W2741809807"))
    assert result.paper is not None

    detached = result.paper.model_copy(update={"source_version_label": "submittedVersion"})
    with pytest.raises(ValueError, match="identity hash does not match"):
        scout.registry.store(detached, record_type="paper")


def test_arxiv_versioned_request_rejects_a_different_returned_version(tmp_path):
    scout, _coordinator, calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.ARXIV_API,
        host="export.arxiv.org",
        response_body=_ARXIV_XML,
    )

    result = scout.execute(_paper("https://arxiv.org/abs/cs.LG/0301001v1"))

    assert result.job.state is JobState.INTERRUPTED
    assert result.paper is None
    assert parse_qs(urlsplit(calls[0]).query)["id_list"] == ["cs.LG/0301001v1"]
    assert scout.registry.count("paper") == 0


@pytest.mark.parametrize("path_kind", ["abs", "pdf", "html"])
def test_arxiv_version_identity_normalizes_all_reviewed_citation_path_kinds(tmp_path, path_kind):
    scout, _coordinator, calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.ARXIV_API,
        host="export.arxiv.org",
        response_body=_ARXIV_XML,
    )

    result = scout.execute(_paper(f"https://arxiv.org/{path_kind}/cs.LG/0301001v2"))

    assert result.job.state is JobState.COMPLETED
    assert result.paper is not None
    assert result.paper.source_version_reference == "https://arxiv.org/abs/cs.LG/0301001v2"
    assert parse_qs(urlsplit(calls[0]).query)["id_list"] == ["cs.LG/0301001v2"]


def test_crossref_ambiguous_versioned_license_is_not_collapsed(tmp_path):
    body = json.dumps(
        {
            "message": {
                "title": ["Ambiguous license"],
                "license": [
                    {
                        "URL": "https://creativecommons.org/licenses/by/4.0/",
                        "content-version": "vor",
                    },
                    {
                        "URL": "https://creativecommons.org/publicdomain/zero/1.0/",
                        "content-version": "am",
                    },
                ],
            }
        }
    ).encode()
    scout, _coordinator, _calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.CROSSREF_API,
        host="api.crossref.org",
        response_body=body,
    )

    result = scout.execute(_paper("https://doi.org/10.1234/example.doi"))

    assert result.job.state is JobState.COMPLETED
    assert result.paper is not None
    assert result.paper.license == "unknown"
    assert result.paper.source_license_evidence_source is None
    assert result.paper.source_version_label is None


def test_content_use_requires_a_first_party_verifier_for_the_source_family(tmp_path):
    body = json.dumps({"title": "unlicensed", "authorships": []}).encode()
    scout, _coordinator, calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.OPENALEX_API,
        host="api.openalex.org",
        response_body=body,
    )
    with pytest.raises(ValueError, match="first-party source-version verifier"):
        scout.submit(_paper("https://openalex.org/W2741809807", use=ResearchSourceUse.CONTENT_USE))
    assert calls == []
    assert scout.registry.count("paper") == 0


def test_arxiv_content_use_requires_an_explicit_version_in_the_citation(tmp_path):
    scout, _coordinator, calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.ARXIV_API,
        host="export.arxiv.org",
        response_body=_ARXIV_XML,
    )

    with pytest.raises(ValueError, match="explicitly versioned citation URL"):
        scout.submit(
            _paper("https://arxiv.org/abs/cs.LG/0301001", use=ResearchSourceUse.CONTENT_USE)
        )

    assert calls == []


def test_arxiv_content_use_hashes_exact_document_only_after_license_verification(tmp_path):
    metadata_body = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v2</id>
    <title>Versioned licensed paper</title>
    <published>2024-01-01T00:00:00Z</published>
    <author><name>Example Author</name></author>
    <arxiv:license>unknown</arxiv:license>
  </entry>
</feed>
"""
    license_page = (
        b'<html><a href="https://creativecommons.org/licenses/by/4.0/">View license</a></html>'
    )
    document = b"%PDF-1.7\nshort fake source document\n"
    scout, _coordinator, calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.ARXIV_API,
        host="export.arxiv.org",
        response_body=metadata_body,
        arxiv_version_page_body=license_page,
        arxiv_document_body=document,
    )

    result = scout.execute(
        _paper("https://arxiv.org/abs/2401.00001v2", use=ResearchSourceUse.CONTENT_USE)
    )

    assert result.job.state is JobState.COMPLETED
    assert result.paper is not None
    assert result.paper.license == "CC-BY-4.0"
    assert result.paper.permitted_use is ResearchSourceUse.CONTENT_USE
    assert result.paper.source_license_evidence_source is (
        ResearchLicenseEvidenceSource.ARXIV_FIRST_PARTY_VERSION_PAGE
    )
    assert result.paper.source_license_evidence_reference == result.paper.source_version_reference
    assert result.paper.source_license_evidence_sha256 == hashlib.sha256(license_page).hexdigest()
    assert result.paper.source_document_sha256 == hashlib.sha256(document).hexdigest()
    assert result.paper.source_document_size_bytes == len(document)
    assert [urlsplit(url).path for url in calls] == [
        "/api/query",
        "/abs/2401.00001v2",
        "/pdf/2401.00001v2",
    ]
    assert document.decode() not in result.paper.model_dump_json()
    assert license_page.decode() not in result.paper.model_dump_json()
    with sqlite3.connect(scout.registry.database_path) as connection:
        stored_payload = connection.execute(
            "SELECT payload_json FROM research_records WHERE artifact_id = ?",
            (str(result.paper.artifact_id),),
        ).fetchone()[0]
    assert document.decode() not in stored_payload
    assert license_page.decode() not in stored_payload
    mismatched = result.paper.model_copy(
        update={"source_url": "https://arxiv.org/abs/2401.00001v1"}
    )
    with pytest.raises(ValueError, match="explicitly cited arXiv version"):
        scout.registry.store(mismatched, record_type="paper")


def test_arxiv_content_use_does_not_fetch_document_for_unapproved_license(tmp_path):
    metadata_body = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v2</id>
    <title>Unlicensed paper</title>
    <published>2024-01-01T00:00:00Z</published>
  </entry>
</feed>
"""
    license_page = b'<html><a href="https://arxiv.org/licenses/nonexclusive-distrib/1.0/">View license</a></html>'
    scout, _coordinator, calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.ARXIV_API,
        host="export.arxiv.org",
        response_body=metadata_body,
        arxiv_version_page_body=license_page,
        arxiv_document_body=b"%PDF-must-not-be-fetched",
    )

    result = scout.execute(
        _paper("https://arxiv.org/abs/2401.00001v2", use=ResearchSourceUse.CONTENT_USE)
    )

    assert result.job.state is JobState.INTERRUPTED
    assert result.paper is None
    assert [urlsplit(url).path for url in calls] == ["/api/query", "/abs/2401.00001v2"]
    assert scout.registry.count("paper") == 0


def test_arxiv_content_use_honors_cancellation_before_document_fetch(tmp_path):
    metadata_body = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v2</id>
    <title>Licensed paper</title>
    <published>2024-01-01T00:00:00Z</published>
  </entry>
</feed>
"""
    license_page = (
        b'<html><a href="https://creativecommons.org/licenses/by/4.0/">View license</a></html>'
    )
    coordinator: ResearchCoordinator
    job_id = None

    def cancel_during_version_page():
        if job_id is not None:
            coordinator.cancel(job_id)

    scout, coordinator, calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.ARXIV_API,
        host="export.arxiv.org",
        response_body=metadata_body,
        arxiv_version_page_body=license_page,
        arxiv_document_body=b"%PDF-must-not-be-fetched",
        on_source_request=cancel_during_version_page,
    )
    job = scout.submit(
        _paper("https://arxiv.org/abs/2401.00001v2", use=ResearchSourceUse.CONTENT_USE)
    )
    job_id = job.job_id

    result = scout.run(job.job_id)

    assert result.job.state is JobState.CANCELLED
    assert result.paper is None
    assert [urlsplit(url).path for url in calls] == ["/api/query", "/abs/2401.00001v2"]
    assert scout.registry.count("paper") == 0


def test_cancelled_scout_does_not_fetch_or_store(tmp_path):
    body = json.dumps({"title": "cancelled", "authorships": []}).encode()
    scout, coordinator, calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.OPENALEX_API,
        host="api.openalex.org",
        response_body=body,
    )
    job = scout.submit(_paper("https://openalex.org/W2741809807"))
    coordinator.cancel(job.job_id)

    result = scout.run(job.job_id)

    assert result.job.state is JobState.CANCELLED
    assert result.paper is None
    assert calls == []
    assert scout.registry.count("paper") == 0


def test_scout_checks_cancellation_after_fetch_before_registry_write(tmp_path):
    body = json.dumps({"title": "cancelled after fetch", "authorships": []}).encode()
    coordinator: ResearchCoordinator

    def request_hook():
        coordinator.cancel(job.job_id)

    scout, coordinator, calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.OPENALEX_API,
        host="api.openalex.org",
        response_body=body,
        on_request=request_hook,
    )
    job = scout.submit(_paper("https://openalex.org/W2741809807"))

    result = scout.run(job.job_id)

    assert result.job.state is JobState.CANCELLED
    assert result.paper is None
    assert len(calls) == 1
    assert scout.registry.count("paper") == 0


def test_checkpointed_scout_job_resumes_after_process_crash(tmp_path):
    body = json.dumps(
        {
            "title": "resumed metadata",
            "publication_date": "2025-01-02",
            "authorships": [],
        }
    ).encode()
    ledgers = SqliteLedgers(tmp_path / "mission.sqlite3")

    def crash_during_fetch():
        raise SystemExit("simulated owner interruption")

    interrupted, interrupted_coordinator, _calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.OPENALEX_API,
        host="api.openalex.org",
        response_body=body,
        ledgers=ledgers,
        on_request=crash_during_fetch,
    )
    job = interrupted.submit(_paper("https://openalex.org/W2741809807"))
    with pytest.raises(SystemExit, match="simulated owner interruption"):
        interrupted.run(job.job_id)
    assert interrupted_coordinator.jobs()[0].state is JobState.CHECKPOINTED
    assert interrupted.registry.count("paper") == 0

    resumed, resumed_coordinator, _resumed_calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.OPENALEX_API,
        host="api.openalex.org",
        response_body=body,
        ledgers=ledgers,
    )
    assert resumed_coordinator.jobs()[0].state is JobState.CHECKPOINTED
    result = resumed.run(job.job_id)

    assert result.job.state is JobState.COMPLETED
    assert result.paper is not None
    assert resumed.registry.count("paper") == 1


def test_scout_rejects_oversized_metadata_response(tmp_path):
    body = b"x" * (512 * 1024 + 1)
    scout, _coordinator, calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.OPENALEX_API,
        host="api.openalex.org",
        response_body=body,
    )

    result = scout.execute(_paper("https://openalex.org/W2741809807"))

    assert result.job.state is JobState.INTERRUPTED
    assert result.paper is None
    assert len(calls) == 1
    assert scout.registry.count("paper") == 0


def test_scout_defers_provider_wait_that_exceeds_job_budget(tmp_path):
    body = json.dumps(
        {"name": "Pandas 2.2.0", "tag_name": "v2.2.0", "author": {"login": "pandas-dev"}}
    ).encode()
    scout, _coordinator, calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.GITHUB_RELEASE_API,
        host="api.github.com",
        response_body=body,
    )
    paper = _paper("https://github.com/pandas-dev/pandas/releases/tag/v2.2.0")

    first = scout.execute(paper)
    deferred = scout.execute(paper, execution_budget_seconds=0.05)

    assert first.job.state is JobState.COMPLETED
    assert deferred.job.state is JobState.INTERRUPTED
    assert deferred.paper is None
    assert len(calls) == 1
    assert scout.registry.count("paper") == 1
