"""Research registry intake enforces source paths and use policy."""

from __future__ import annotations

import pytest

from advisorai.research import (
    PaperCard,
    ResearchRegistry,
    ResearchSourceFamily,
    ResearchSourcePolicy,
    ResearchSourceUse,
)


@pytest.mark.parametrize(
    ("source_url", "family"),
    [
        ("https://arxiv.org/abs/2401.00001", ResearchSourceFamily.ARXIV),
        ("https://arxiv.org/abs/cs.LG/0301001", ResearchSourceFamily.ARXIV),
        ("https://openalex.org/W2741809807", ResearchSourceFamily.OPENALEX),
        ("https://doi.org/10.1234/example.doi", ResearchSourceFamily.CROSSREF),
        (
            "https://github.com/pandas-dev/pandas/releases/tag/v2.2.0",
            ResearchSourceFamily.GITHUB_RELEASE,
        ),
        (
            "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm",
            ResearchSourceFamily.SEC_EDGAR,
        ),
        ("https://fred.stlouisfed.org/series/DFF", ResearchSourceFamily.FRED),
    ],
)
def test_reviewed_exact_source_paths_are_classified(source_url, family):
    result = ResearchSourcePolicy().assess(
        source_url=source_url,
        license_claim="unknown",
        requested_use=ResearchSourceUse.CITATION_ONLY,
    )
    assert result.family is family
    assert result.permitted_use is ResearchSourceUse.CITATION_ONLY
    assert result.policy_version == "research-source-policy-v2"


def test_source_policy_rejects_unrecognized_requested_use():
    with pytest.raises(ValueError, match="supported policy value"):
        ResearchSourcePolicy().assess(
            source_url="https://arxiv.org/abs/2401.00001",
            license_claim="CC-BY-4.0",
            requested_use="content reuse",
        )


@pytest.mark.parametrize(
    "source_url",
    [
        "http://arxiv.org/abs/2401.00001",
        "https://arxiv.org.attacker.example/abs/2401.00001",
        "https://user@arxiv.org/abs/2401.00001",
        "https://arxiv.org/abs/2401.00001?download=1",
        "https://arxiv.org/abs/2401.00001#abstract",
        "https://arxiv.org.:443/abs/2401.00001",
        "https://arxiv.org:8443/abs/2401.00001",
        "https://arxiv.org/abs/../2401.00001",
        "https://arxiv.org/abs%2f2401.00001",
        "https://example.org/abs/2401.00001",
        "https://openalex.org/api/works/W2741809807",
        "https://github.com/pandas-dev/pandas/archive/refs/tags/v2.2.0.zip",
    ],
)
def test_unreviewed_or_ambiguous_source_urls_fail_closed(source_url):
    policy = ResearchSourcePolicy()
    with pytest.raises(ValueError):
        policy.assess(
            source_url=source_url,
            license_claim="unknown",
            requested_use=ResearchSourceUse.CITATION_ONLY,
        )


def test_registry_stamps_derived_assessment_and_idempotently_returns_it(tmp_path):
    registry = ResearchRegistry(tmp_path / "research.sqlite3")
    paper = PaperCard(
        source_url="https://arxiv.org/abs/2401.00001",
        claimed_result="short-horizon mean reversion",
        license="unknown",
    )

    stored = registry.store(paper, record_type="paper")
    assert stored.source_family is ResearchSourceFamily.ARXIV
    assert stored.requested_use is ResearchSourceUse.CITATION_ONLY
    assert stored.permitted_use is ResearchSourceUse.CITATION_ONLY
    assert stored.source_policy_version == "research-source-policy-v2"
    assert registry.store(paper, record_type="paper") == stored
    assert registry.get(stored.artifact_id) == stored
    assert registry.count("paper") == 1


def test_caller_supplied_assessment_cannot_override_registry_policy(tmp_path):
    registry = ResearchRegistry(tmp_path / "research.sqlite3")
    paper = PaperCard(
        source_url="https://arxiv.org/abs/2401.00001",
        claimed_result="short-horizon mean reversion",
        license="CC-BY-4.0",
        requested_use=ResearchSourceUse.CONTENT_USE,
        source_family=ResearchSourceFamily.GITHUB_RELEASE,
        permitted_use=ResearchSourceUse.CONTENT_USE,
        source_policy_version="caller-supplied-policy",
    )

    with pytest.raises(ValueError, match="first-party exact-version license evidence"):
        registry.store(paper, record_type="paper")
    assert registry.count("paper") == 0


def test_unknown_license_is_citation_only_and_cannot_authorize_content_use(tmp_path):
    registry = ResearchRegistry(tmp_path / "research.sqlite3")
    citation = PaperCard(
        source_url="https://arxiv.org/abs/2401.00003",
        claimed_result="a cited result",
        license="unknown",
    )
    stored = registry.store(citation, record_type="paper")
    assert stored.permitted_use is ResearchSourceUse.CITATION_ONLY

    content = PaperCard(
        source_url="https://arxiv.org/abs/2401.00004",
        claimed_result="a source summary",
        license="unknown",
        requested_use=ResearchSourceUse.CONTENT_USE,
    )
    with pytest.raises(ValueError, match="allowlisted license identifier"):
        registry.store(content, record_type="paper")
    assert registry.count("paper") == 1


@pytest.mark.parametrize(
    ("source_url", "license"),
    [
        ("https://arxiv.org/abs/2401.00005", "MIT"),
        ("https://github.com/pandas-dev/pandas/releases/tag/v2.2.0", "unknown"),
        ("https://fred.stlouisfed.org/series/DFF", "CC0-1.0"),
    ],
)
def test_content_use_requires_a_source_family_license_pair(tmp_path, source_url, license):
    registry = ResearchRegistry(tmp_path / "research.sqlite3")
    paper = PaperCard(
        source_url=source_url,
        claimed_result="a source summary",
        license=license,
        requested_use=ResearchSourceUse.CONTENT_USE,
    )
    with pytest.raises(ValueError, match="allowlisted license identifier"):
        registry.store(paper, record_type="paper")
    assert registry.count("paper") == 0


def test_content_use_license_claim_does_not_replace_versioned_source_evidence(tmp_path):
    registry = ResearchRegistry(tmp_path / "research.sqlite3")
    paper = PaperCard(
        source_url="https://github.com/pandas-dev/pandas/releases/tag/v2.2.0",
        claimed_result="release notes summary",
        license="MIT",
        requested_use=ResearchSourceUse.CONTENT_USE,
    )
    assessment = ResearchSourcePolicy().assess(
        source_url=paper.source_url,
        license_claim=paper.license,
        requested_use=ResearchSourceUse.CONTENT_USE,
    )
    assert assessment.family is ResearchSourceFamily.GITHUB_RELEASE
    with pytest.raises(ValueError, match="first-party exact-version license evidence"):
        registry.store(paper, record_type="paper")
    assert registry.count("paper") == 0
