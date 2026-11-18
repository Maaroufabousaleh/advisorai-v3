"""Code discovery: paper→code leads, code→evidence pins, typed dispositions.

Proves lead extraction is pure and deduplicated, release pins require full
commit SHAs, relationships distinguish author/independent/unverified,
dispositions gate on license and replication evidence separately, and
implementation records persist and retrieve through the registry.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from advisorai.research import ResearchRegistry
from advisorai.research.cards import (
    ImplementationDisposition,
    ImplementationRecord,
    ImplementationRelationship,
    PaperCard,
)
from advisorai.research.code_discovery import (
    assess_release_pin,
    build_implementation_record,
    classify_relationship,
    discovery_fingerprint,
    extract_code_leads,
    hash_source_bytes,
    recommend_disposition,
)
from advisorai.research.retrieval import ResearchRetrieval


def _paper() -> PaperCard:
    return PaperCard(
        source_url="https://doi.org/10.1016/j.jfineco.2011.11.003",
        title="Time series momentum",
        authors=("Tobias J. Moskowitz", "Yao Hua Ooi", "Lasse Heje Pedersen"),
        claimed_result="TSM persists",
    )


def test_extract_code_leads_from_locations_excerpt_and_author_pages():
    leads = extract_code_leads(
        _paper(),
        openalex_locations=(
            "https://github.com/moskowitz/tsm",
            "https://publisher.example/article",
        ),
        full_text_excerpt="Code availability: see https://github.com/moskowitz/tsm and supplement.",
        author_page_urls=("https://github.com/moskowitz/tsm-helpers",),
    )
    urls = sorted(lead.repository_url for lead in leads)
    assert urls == ["https://github.com/moskowitz/tsm", "https://github.com/moskowitz/tsm-helpers"]
    via = {lead.repository_url: lead.found_via for lead in leads}
    assert via["https://github.com/moskowitz/tsm"] == "openalex_location"
    assert via["https://github.com/moskowitz/tsm-helpers"] == "author_page"


def test_release_pin_requires_full_commit_sha():
    pin = assess_release_pin("https://github.com/moskowitz/tsm", tag="v1.0", commit_sha="a" * 40)
    assert pin["archive_url"].endswith("/archive/refs/tags/v1.0.zip")
    with pytest.raises(ValueError, match="full 40-char"):
        assess_release_pin("https://github.com/moskowitz/tsm", tag="v1.0", commit_sha="abc123")
    with pytest.raises(ValueError, match="github.com"):
        assess_release_pin("https://example.com/repo", tag="v1.0", commit_sha="a" * 40)


def test_source_hash_rejects_empty_bytes():
    assert len(hash_source_bytes(b"payload")) == 64
    with pytest.raises(ValueError, match="empty source bytes"):
        hash_source_bytes(b"")


def test_relationship_classification_is_explicit():
    assert (
        classify_relationship(
            repository_owner="moskowitz",
            paper_authors=("Tobias J. Moskowitz",),
            claims_independent=False,
        )
        is ImplementationRelationship.AUTHOR_IMPLEMENTATION
    )
    assert (
        classify_relationship(
            repository_owner="stranger",
            paper_authors=("Tobias J. Moskowitz",),
            claims_independent=True,
        )
        is ImplementationRelationship.INDEPENDENT_REPRODUCTION
    )
    assert (
        classify_relationship(
            repository_owner="stranger",
            paper_authors=("Tobias J. Moskowitz",),
            claims_independent=False,
        )
        is ImplementationRelationship.UNVERIFIED_LEAD
    )


def test_disposition_gates_link_license_and_evidence_separately():
    disposition, reason = recommend_disposition(
        relationship=ImplementationRelationship.UNVERIFIED_LEAD,
        license_id="MIT",
        replication_evidence="reproduced table 1",
        small_reviewed_scope=True,
    )
    assert disposition is ImplementationDisposition.REFERENCE_ONLY
    assert "unverified" in reason

    disposition, reason = recommend_disposition(
        relationship=ImplementationRelationship.AUTHOR_IMPLEMENTATION,
        license_id="GPL-3.0-only",
        replication_evidence="reproduced table 1",
        small_reviewed_scope=True,
    )
    assert disposition is ImplementationDisposition.REIMPLEMENT_FROM_SPEC
    assert "copyleft" in reason

    disposition, _ = recommend_disposition(
        relationship=ImplementationRelationship.INDEPENDENT_REPRODUCTION,
        license_id="MIT",
        replication_evidence="none",
        small_reviewed_scope=True,
    )
    assert disposition is ImplementationDisposition.ADAPT_IN_ISOLATION

    disposition, _ = recommend_disposition(
        relationship=ImplementationRelationship.INDEPENDENT_REPRODUCTION,
        license_id="MIT",
        replication_evidence="reproduced table 1 within 1%",
        small_reviewed_scope=True,
    )
    assert disposition is ImplementationDisposition.REUSE_COMPONENT


def test_implementation_record_validation_and_registry_round_trip(tmp_path):
    paper_id = uuid4()
    record = build_implementation_record(
        repository_url="https://github.com/moskowitz/tsm",
        revision="v1.0@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        source_hash="ab" * 32,
        license_id="MIT",
        relationship=ImplementationRelationship.AUTHOR_IMPLEMENTATION,
        paper_card_id=paper_id,
        dependencies=("numpy>=1.24",),
        data_assumptions=("daily closes, dividend-adjusted",),
        entry_point="python run_tsm.py --config baseline.yaml",
        replication_evidence="reproduced table 1 within 1%",
        small_reviewed_scope=True,
    )
    assert record.disposition is ImplementationDisposition.REUSE_COMPONENT
    registry = ResearchRegistry(tmp_path / "code.sqlite3")
    stored = registry.store(record, record_type="implementation")
    assert stored.artifact_id == record.artifact_id
    assert isinstance(registry.get(record.artifact_id), ImplementationRecord)
    registry.link(paper_id, record.artifact_id, "paper_implemented_by")
    assert ResearchRetrieval(tmp_path / "code.sqlite3").rebuild() >= 1
    with pytest.raises(ValueError, match="must link its paper"):
        build_implementation_record(
            repository_url="https://github.com/moskowitz/tsm",
            revision="v1.0",
            source_hash="ab" * 32,
            license_id="MIT",
            relationship=ImplementationRelationship.AUTHOR_IMPLEMENTATION,
            paper_card_id=None,
            dependencies=(),
            data_assumptions=(),
            entry_point="run.sh",
            replication_evidence="none",
            small_reviewed_scope=False,
        )
    with pytest.raises(ValueError, match="cannot be reused"):
        ImplementationRecord(
            repository_url="https://github.com/stranger/tsm",
            revision="main",
            source_hash="ab" * 32,
            license="MIT",
            relationship=ImplementationRelationship.UNVERIFIED_LEAD,
            entry_point="run.sh",
            disposition=ImplementationDisposition.REUSE_COMPONENT,
            disposition_rationale="invalid",
        )


def test_discovery_fingerprint_is_stable(tmp_path):
    paper_id = uuid4()
    record = build_implementation_record(
        repository_url="https://github.com/moskowitz/tsm",
        revision="v1.0",
        source_hash="ab" * 32,
        license_id="MIT",
        relationship=ImplementationRelationship.UNVERIFIED_LEAD,
        paper_card_id=paper_id,
        dependencies=(),
        data_assumptions=(),
        entry_point="run.sh",
        replication_evidence="none",
        small_reviewed_scope=False,
    )
    from advisorai.research.code_discovery import CodeDiscoveryReport, CodeLead

    report = CodeDiscoveryReport(
        paper_card_id=paper_id,
        leads=(CodeLead("https://github.com/moskowitz/tsm", "openalex_location", "ctx"),),
        records=(record,),
    )
    assert discovery_fingerprint(report) == discovery_fingerprint(report)
    assert len(discovery_fingerprint(report)) == 64
    assert isinstance(tmp_path, Path)
