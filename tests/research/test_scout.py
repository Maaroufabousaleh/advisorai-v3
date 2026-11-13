"""The Research Scout stores bounded metadata with repeatable source evidence."""

from __future__ import annotations

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
    ledgers: SqliteLedgers | None = None,
    on_request=None,
):
    calls: list[str] = []

    def requester(method, url, headers, body, timeout):
        calls.append(url)
        if on_request is not None:
            on_request()
        return 200, response_body, (("Content-Type", "application/json"),)

    client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=(host,),
            max_retries=0,
            max_response_bytes=512 * 1024,
            requests_per_second=100,
        ),
        requester=requester,
        sleeper=lambda _delay: None,
    )
    coordinator = ResearchCoordinator(ledgers, max_workers=2)
    scout = ResearchMetadataScout(
        registry=ResearchRegistry(tmp_path / "research.sqlite3"),
        coordinator=coordinator,
        clients={provider: client},
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
        }[provider]
    )
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


def test_unknown_license_content_use_fails_closed_after_metadata_fetch(tmp_path):
    body = json.dumps({"title": "unlicensed", "authorships": []}).encode()
    scout, _coordinator, calls = _scout(
        tmp_path,
        provider=ResearchMetadataProvider.OPENALEX_API,
        host="api.openalex.org",
        response_body=body,
    )
    result = scout.execute(
        _paper("https://openalex.org/W2741809807", use=ResearchSourceUse.CONTENT_USE)
    )

    assert result.job.state is JobState.INTERRUPTED
    assert result.paper is None
    assert len(calls) == 1
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
