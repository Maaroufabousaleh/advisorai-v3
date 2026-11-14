"""Research Brain typed records: literature stays separate from empirical truth.

Six immutable artifacts with SQLite WAL registries: PaperCard, HypothesisCard,
CandidateFactorArtifact, ExperimentArtifact, ValidationReport, PromotionDecision.
A vector index is retrieval only, never source of truth. Every material claim
traces to its source; failed and rejected work is retained.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.contracts.core import ArtifactModel


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("research timestamps must include a timezone")
    return value.astimezone(UTC)


class ArtifactStatus(StrEnum):
    PROPOSED = "proposed"
    SCREENED = "screened"
    VALIDATED = "validated"
    SHADOW = "shadow"
    PAPER = "paper"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    RETIRED = "retired"


class ResearchSourceFamily(StrEnum):
    ARXIV = "arxiv"
    OPENALEX = "openalex"
    CROSSREF = "crossref_doi"
    GITHUB_RELEASE = "github_release"
    SEC_EDGAR = "sec_edgar"
    FRED = "fred"


class ResearchSourceUse(StrEnum):
    CITATION_ONLY = "citation_only"
    CONTENT_USE = "content_use"


class ResearchMetadataProvider(StrEnum):
    ARXIV_API = "arxiv_api"
    OPENALEX_API = "openalex_api"
    CROSSREF_API = "crossref_api"
    GITHUB_RELEASE_API = "github_release_api"
    SEC_EDGAR_API = "sec_edgar_api"
    FRED_API = "fred_api"


class ImplementationRelationship(StrEnum):
    AUTHOR_IMPLEMENTATION = "author_implementation"
    INDEPENDENT_REPRODUCTION = "independent_reproduction"
    UNVERIFIED_LEAD = "unverified_lead"


class ImplementationDisposition(StrEnum):
    REUSE_COMPONENT = "reuse_small_reviewed_component"
    ADAPT_IN_ISOLATION = "adapt_in_isolation"
    REIMPLEMENT_FROM_SPEC = "reimplement_from_spec_without_copying"
    REFERENCE_ONLY = "reference_only"


class ResearchLicenseEvidenceSource(StrEnum):
    ARXIV_METADATA = "arxiv_metadata"
    ARXIV_FIRST_PARTY_VERSION_PAGE = "arxiv_first_party_version_page"
    OPENALEX_AGGREGATED_METADATA = "openalex_aggregated_metadata"
    OPENALEX_FIRST_PARTY_VERSION_PAGE = "openalex_first_party_version_page"
    CROSSREF_DEPOSIT_METADATA = "crossref_deposit_metadata"
    CROSSREF_FIRST_PARTY_VERSION_PAGE = "crossref_first_party_version_page"
    GITHUB_FIRST_PARTY_RELEASE_ARCHIVE = "github_first_party_release_archive"


class PaperCard(ArtifactModel):
    source_url: str = Field(min_length=1)
    title: str = Field(default="untitled", min_length=1, max_length=2048)
    authors: tuple[str, ...] = ()
    publication_date: str = Field(default="unknown", min_length=1)
    license: str = Field(default="unknown", min_length=1, max_length=512)
    requested_use: ResearchSourceUse = ResearchSourceUse.CITATION_ONLY
    source_family: ResearchSourceFamily | None = None
    permitted_use: ResearchSourceUse | None = None
    source_policy_version: str | None = None
    source_metadata_provider: ResearchMetadataProvider | None = None
    source_metadata_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_metadata_size_bytes: int | None = Field(default=None, ge=1, le=4 * 1024 * 1024)
    source_metadata_observed_at: datetime | None = None
    source_version_reference: str | None = Field(default=None, max_length=2048)
    source_version_label: str | None = Field(default=None, max_length=128)
    source_version_identity_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_doi: str | None = Field(default=None, max_length=2048)
    source_license_evidence_source: ResearchLicenseEvidenceSource | None = None
    source_license_evidence_reference: str | None = Field(default=None, max_length=2048)
    source_license_evidence_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_license_evidence_observed_at: datetime | None = None
    source_document_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_document_size_bytes: int | None = Field(default=None, ge=1, le=64 * 1024 * 1024)
    source_document_observed_at: datetime | None = None
    source_document_reference: str | None = Field(default=None, max_length=2048)
    claimed_result: str = Field(min_length=1)
    limitations: tuple[str, ...] = ()
    relevance: str = Field(default="", min_length=0)

    @model_validator(mode="after")
    def require_complete_source_assessment(self) -> PaperCard:
        assessed = (
            self.source_family is not None,
            self.permitted_use is not None,
            self.source_policy_version is not None,
        )
        if any(assessed) and not all(assessed):
            raise ValueError(
                "source family, permitted use, and policy version must be recorded together"
            )
        if (
            self.permitted_use is ResearchSourceUse.CONTENT_USE
            and self.requested_use is not ResearchSourceUse.CONTENT_USE
        ):
            raise ValueError("content use must be explicitly requested")
        provenance = (
            self.source_metadata_provider is not None,
            self.source_metadata_sha256 is not None,
            self.source_metadata_size_bytes is not None,
            self.source_metadata_observed_at is not None,
        )
        if any(provenance) and not all(provenance):
            raise ValueError(
                "source metadata provider, hash, size, and time must be recorded together"
            )
        version_provenance = (
            self.source_version_reference is not None,
            self.source_version_label is not None,
            self.source_version_identity_sha256 is not None,
        )
        if any(version_provenance) and not all(version_provenance):
            raise ValueError(
                "source version reference, label, and identity hash must be recorded together"
            )
        if any(version_provenance) and not all(provenance):
            raise ValueError("source version identity requires complete metadata provenance")
        license_provenance = (
            self.source_license_evidence_source is not None,
            self.source_license_evidence_reference is not None,
            self.source_license_evidence_sha256 is not None,
            self.source_license_evidence_observed_at is not None,
        )
        if any(license_provenance) and not all(license_provenance):
            raise ValueError(
                "source license evidence source, reference, hash, and time must be recorded together"
            )
        if any(license_provenance) and not all(provenance):
            raise ValueError("source license evidence requires complete metadata provenance")
        document_provenance = (
            self.source_document_sha256 is not None,
            self.source_document_size_bytes is not None,
            self.source_document_observed_at is not None,
            self.source_document_reference is not None,
        )
        if any(document_provenance) and not all(document_provenance):
            raise ValueError(
                "source document hash, size, and observation time must be recorded together"
            )
        if any(document_provenance) and not all(version_provenance):
            raise ValueError("source document hash requires an exact source version identity")
        return self

    @field_validator("source_metadata_observed_at")
    @classmethod
    def metadata_time_aware(cls, value: datetime | None) -> datetime | None:
        return _aware(value) if value is not None else None

    @field_validator("source_license_evidence_observed_at")
    @classmethod
    def license_evidence_time_aware(cls, value: datetime | None) -> datetime | None:
        return _aware(value) if value is not None else None

    @field_validator("source_document_observed_at")
    @classmethod
    def source_document_time_aware(cls, value: datetime | None) -> datetime | None:
        return _aware(value) if value is not None else None


class HypothesisCard(ArtifactModel):
    economic_rationale: str = Field(min_length=1)
    universe: str = Field(min_length=1)
    horizon: str = Field(min_length=1)
    point_in_time_inputs: tuple[str, ...] = ()
    baseline: str = Field(min_length=1)
    preregistered_plan: str = Field(min_length=1)
    failure_modes: tuple[str, ...] = ()
    owner: str = Field(min_length=1)
    budget: str = Field(default="unbounded_prohibited", min_length=1)
    expires_at: datetime
    paper_card_ids: tuple[UUID, ...] = ()

    _expires = field_validator("expires_at")(_aware)


class CandidateFactorArtifact(ArtifactModel):
    dsl_ast_hash: str = Field(min_length=64, max_length=64)
    dsl_source: str = Field(min_length=1)
    lineage: tuple[str, ...] = ()
    feature_versions: tuple[str, ...] = ()
    lookbacks: tuple[int, ...] = ()
    universe: str = Field(min_length=1)
    hypothesis_card_id: UUID | None = None
    status: ArtifactStatus = ArtifactStatus.PROPOSED


class ExperimentArtifact(ArtifactModel):
    candidate_id: UUID
    code_hash: str = Field(min_length=64, max_length=64)
    data_snapshot_hash: str = Field(min_length=64, max_length=64)
    environment_hash: str = Field(min_length=64, max_length=64)
    seeds: tuple[int, ...] = ()
    windows: tuple[str, ...] = ()
    metrics: dict[str, str] = Field(default_factory=dict)
    costs: dict[str, str] = Field(default_factory=dict)
    exposures: dict[str, str] = Field(default_factory=dict)
    regime_slices: tuple[str, ...] = ()
    trial_count: int = Field(ge=1)
    status: ArtifactStatus = ArtifactStatus.PROPOSED


class ValidationReport(ArtifactModel):
    experiment_id: UUID
    leakage_check: str = Field(min_length=1)
    temporal_validation: str = Field(min_length=1)
    multiple_testing: str = Field(min_length=1)
    independent_replay: str = Field(min_length=1)
    stability: str = Field(min_length=1)
    portfolio_incrementality: str = Field(min_length=1)
    approved: bool = False
    rationale: str = Field(min_length=1)


class PromotionDecision(ArtifactModel):
    experiment_id: UUID
    validation_id: UUID
    policy_version: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    expiry: datetime
    rollback_condition: str = Field(min_length=1)
    approver: str | None = None
    approved: bool = False

    _expiry = field_validator("expiry")(_aware)

    @model_validator(mode="after")
    def validate_approval(self) -> PromotionDecision:
        if self.approved and not self.approver:
            raise ValueError("promotion approval requires a recorded approver")
        return self


class ImplementationRecord(ArtifactModel):
    """A pinned, reviewable code finding linked to a paper or standing alone.

    A linked repository, a license check, and a reproduced headline result are
    three different things: this record keeps them separate. Nothing here
    claims the strategy works on AdvisorAI data — that verdict belongs to an
    independent replay recorded as an ExperimentArtifact + ValidationReport.
    """

    repository_url: str = Field(min_length=1, max_length=2048)
    revision: str = Field(min_length=1, max_length=256)
    source_hash: str = Field(min_length=64, max_length=64)
    license: str = Field(min_length=1, max_length=512)
    relationship: ImplementationRelationship
    paper_card_id: UUID | None = None
    dependencies: tuple[str, ...] = ()
    data_assumptions: tuple[str, ...] = ()
    entry_point: str = Field(min_length=1, max_length=1024)
    replication_evidence: str = Field(default="none", min_length=1, max_length=2048)
    disposition: ImplementationDisposition
    disposition_rationale: str = Field(min_length=1, max_length=2048)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _observed = field_validator("observed_at")(_aware)

    @model_validator(mode="after")
    def validate_implementation(self) -> ImplementationRecord:
        if len(self.source_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.source_hash
        ):
            raise ValueError("implementation source_hash must be a lowercase SHA-256 digest")
        if self.relationship is ImplementationRelationship.AUTHOR_IMPLEMENTATION and (
            self.paper_card_id is None
        ):
            raise ValueError("an author implementation must link its paper card")
        if (
            self.disposition is ImplementationDisposition.REUSE_COMPONENT
            and self.relationship is ImplementationRelationship.UNVERIFIED_LEAD
        ):
            raise ValueError("an unverified lead cannot be reused without verification")
        if not self.repository_url.startswith("https://"):
            raise ValueError("implementation repository must be an HTTPS URL")
        return self


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


__all__ = [
    "ArtifactStatus",
    "CandidateFactorArtifact",
    "ExperimentArtifact",
    "HypothesisCard",
    "ImplementationDisposition",
    "ImplementationRecord",
    "ImplementationRelationship",
    "PaperCard",
    "PromotionDecision",
    "ResearchSourceFamily",
    "ResearchMetadataProvider",
    "ResearchLicenseEvidenceSource",
    "ResearchSourceUse",
    "ValidationReport",
]
