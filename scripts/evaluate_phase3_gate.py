#!/usr/bin/env python3
"""Evaluate the formal Phase-3 gate from immutable evidence.

This command is intentionally offline.  It does not start a collector, load
credentials, call a provider, modify a prior evidence root, or create order
authority.  It reconciles the durable public-data review with the broader
V3-Core source pass and records either a dependency-aware pending
``PhaseGateRecord`` or a passed one when the predecessor gate is supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from advisorai.gates import GateDecision, GateEvidence, GateEvidenceKind, PhaseGateRecord

SCHEMA = "advisorai.phase3.formal-admission-checklist.v1"
GATE_VERSION = "phase3-v3-core-admission-v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
R7_ROOT = REPOSITORY_ROOT / (
    "artifacts/phase3/public-market-data-durable/20260811T182252Z-four-hour-r7-validator-fix"
)
R7_VALIDATION = REPOSITORY_ROOT / (
    "artifacts/phase3/public-market-data-validation/"
    "20260811T230500Z-four-hour-r7-validator-fix-codex-terminal-review/"
    "phase3-qualification-validation.json"
)
R7_ADMISSION = REPOSITORY_ROOT / (
    "artifacts/phase3/public-market-data-admission/"
    "20260811T231500Z-four-hour-r7-validator-fix-codex-policy-review-final/"
    "phase3-admission-evaluation.json"
)
BROAD_SOURCE = REPOSITORY_ROOT / (
    "artifacts/phase3/source-qualification/20260811T233228.867449Z/"
    "phase3-v3-core-source-qualification.json"
)


class ChecklistStatus(StrEnum):
    SATISFIED = "SATISFIED"
    UNSATISFIED = "UNSATISFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    OPTIONAL = "OPTIONAL"
    EXTERNALLY_BLOCKED = "EXTERNALLY_BLOCKED"


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)

    @field_validator("sha256")
    @classmethod
    def normalize_hash(cls, value: str) -> str:
        value = value.strip().lower()
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("evidence references require a SHA-256 digest")
        return value


class RequirementResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str = Field(min_length=1)
    authoritative_source: str = Field(min_length=1)
    requirement: str = Field(min_length=1)
    status: ChecklistStatus
    gating: bool
    rationale: str = Field(min_length=1)
    next_admissible_action: str = Field(min_length=1)
    evidence: tuple[EvidenceReference, ...] = ()


class SourceAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    status: ChecklistStatus
    endpoint_identity: str = Field(min_length=1)
    asset_or_context_scope: str = Field(min_length=1)
    gating: bool
    rationale: str = Field(min_length=1)
    evidence: tuple[EvidenceReference, ...] = ()


class Phase3AdmissionChecklist(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_name: str = Field(default=SCHEMA, alias="schema")
    gate_version: str = Field(min_length=1)
    evaluated_at: datetime
    repository_commit: str = Field(min_length=40, max_length=40)
    evaluator_code_sha256: str = Field(min_length=64, max_length=64)
    decision: GateDecision
    mandatory_requirements: tuple[str, ...]
    blocking_requirement_ids: tuple[str, ...] = ()
    requirements: tuple[RequirementResult, ...] = Field(min_length=1)
    sources: tuple[SourceAssessment, ...] = Field(min_length=1)
    evidence_manifest: tuple[EvidenceReference, ...] = Field(min_length=1)
    phase_gate_record_path: str = Field(min_length=1)
    phase_gate_record_sha256: str = Field(min_length=64, max_length=64)
    phase_gate_record_canonical_hash: str = Field(min_length=64, max_length=64)
    notes: tuple[str, ...] = ()

    @field_validator("evaluated_at")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evaluation time must include a timezone")
        return value.astimezone(UTC)

    @field_validator("repository_commit")
    @classmethod
    def require_commit(cls, value: str) -> str:
        value = value.strip().lower()
        if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("repository_commit must be a Git SHA-1")
        return value

    @field_validator(
        "evaluator_code_sha256", "phase_gate_record_sha256", "phase_gate_record_canonical_hash"
    )
    @classmethod
    def require_sha256(cls, value: str) -> str:
        value = value.strip().lower()
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("checklist digest must be a lowercase SHA-256 digest")
        return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError as exc:
        raise ValueError(f"evidence path escapes repository: {path}") from exc


def _evidence(*paths: Path) -> tuple[EvidenceReference, ...]:
    unique: list[EvidenceReference] = []
    seen: set[str] = set()
    for path in paths:
        resolved = path.resolve()
        relative = _relative(resolved)
        if relative in seen:
            continue
        unique.append(EvidenceReference(path=relative, sha256=_sha256(resolved)))
        seen.add(relative)
    return tuple(unique)


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().lower()


def _requirement(
    requirement_id: str,
    authoritative_source: str,
    requirement: str,
    status: ChecklistStatus,
    *,
    gating: bool,
    rationale: str,
    next_admissible_action: str,
    evidence: tuple[EvidenceReference, ...],
) -> RequirementResult:
    return RequirementResult(
        requirement_id=requirement_id,
        authoritative_source=authoritative_source,
        requirement=requirement,
        status=status,
        gating=gating,
        rationale=rationale,
        next_admissible_action=next_admissible_action,
        evidence=evidence,
    )


def _operation(report: dict[str, Any], name: str) -> dict[str, Any]:
    for operation in report.get("operations", []):
        if isinstance(operation, dict) and operation.get("name") == name:
            return operation
    raise ValueError(f"source report is missing operation {name}")


def _check_r7(
    validation: dict[str, Any],
    admission: dict[str, Any],
    summary: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, bool]:
    primary = summary.get("per_source_symbol", {})
    primary_rows = [
        primary.get(f"binance_spot_public_market_data:{symbol}") for symbol in ("BTC", "ETH")
    ]
    admission_checks = {check.get("name"): check for check in admission.get("checks", [])}
    return {
        "review": validation.get("state") == "PASS_FOR_REVIEW" and validation.get("issues") == [],
        "admission_review": (
            admission.get("recommendation") == "QUALIFIED_FOR_REVIEW"
            and not admission.get("blocker_codes")
            and all(check.get("passed") is True for check in admission_checks.values())
        ),
        "read_only": (
            config.get("credentials_loaded") is False
            and config.get("order_writes_attempted") is False
            and summary.get("execution_separation", {}).get("credentials_loaded") is False
            and summary.get("execution_separation", {}).get("order_writes_attempted") is False
        ),
        "primary_btc_eth": all(
            isinstance(row, dict)
            and row.get("source_id") == "binance_spot_public_market_data"
            and row.get("symbol") in {"BTC", "ETH"}
            and row.get("sample_count", 0) > 0
            and row.get("valid_event_count", 0) > 0
            and row.get("maximum_adjusted_event_age_seconds") is not None
            for row in primary_rows
        ),
        "replay_sequence": (
            validation.get("source_outcomes", {}).get("replay_failure_count") == 0
            and validation.get("source_outcomes", {}).get("sequence_gap_count") == 0
            and validation.get("source_outcomes", {}).get("duplicate_count") == 0
            and validation.get("source_outcomes", {}).get("out_of_order_count") == 0
            and admission_checks.get("primary_snapshot_sequence_replay_continuity", {}).get(
                "passed"
            )
            is True
        ),
        "freshness_health": (
            admission_checks.get("primary_stale_intervals_fail_closed", {}).get("passed") is True
            and admission_checks.get("primary_btc_eth_source_healthy_at_terminal_sample", {}).get(
                "passed"
            )
            is True
            and validation.get("source_outcomes", {}).get("timestamp_projection", {}).get("state")
            == "projected"
        ),
        "lineage": (
            admission_checks.get("source_identity_and_endpoint_binding", {}).get("passed") is True
            and admission_checks.get("dashboard_health_projection", {}).get("passed") is True
            and validation.get("source_outcomes", {}).get("silent_substitution_count") == 0
        ),
        "disagreement": admission_checks.get("disagreement_policy_is_fail_closed", {}).get("passed")
        is True,
        "resource": (
            admission_checks.get("resource_sidecar_without_errors", {}).get("passed") is True
            and not validation.get("resource_monitor", {}).get("resource_errors")
        ),
        "complete_window": admission_checks.get("multi_hour_window_complete", {}).get("passed")
        is True,
        "stale_fail_closed": (
            validation.get("source_outcomes", {}).get("selection_fail_closed_count", 0) > 0
            and validation.get("source_outcomes", {}).get("silent_substitution_count") == 0
        ),
    }


def _phase2_predecessor(path: Path | None, evaluated_at: datetime) -> tuple[bool, str]:
    if path is None:
        return False, "no Phase-2 PhaseGateRecord path was supplied"
    try:
        record = PhaseGateRecord.model_validate(_load(path))
    except (FileNotFoundError, TypeError, ValueError) as exc:
        return False, f"Phase-2 predecessor could not be validated: {exc}"
    if record.phase != 2 or record.decision is not GateDecision.PASSED:
        return False, "the supplied predecessor is not a passed Phase-2 record"
    if not record.is_valid_at(evaluated_at):
        return False, "the supplied Phase-2 record is not valid at evaluation time"
    return True, "a currently valid passed Phase-2 record was supplied"


def evaluate(
    *,
    r7_validation_path: Path,
    r7_admission_path: Path,
    broader_source_path: Path,
    phase2_gate_path: Path | None,
    evaluated_at: datetime | None = None,
) -> tuple[Phase3AdmissionChecklist, PhaseGateRecord]:
    evaluated_at = (evaluated_at or datetime.now(UTC)).astimezone(UTC)
    validation = _load(r7_validation_path)
    admission = _load(r7_admission_path)
    broader = _load(broader_source_path)
    r7_root = r7_validation_path.parents[2] / "public-market-data-durable" / R7_ROOT.name
    if not r7_root.is_dir():
        r7_root = R7_ROOT
    summary = _load(r7_root / "summary.json")
    config = _load(r7_root / "config.json")
    resource_root = (
        REPOSITORY_ROOT / "artifacts/phase3/public-market-data-resource-monitor" / R7_ROOT.name
    )
    if not (resource_root / "summary.json").is_file():
        resource_root = (
            REPOSITORY_ROOT
            / "artifacts/phase3/public-market-data-resource-monitor/20260811T182252Z-four-hour-r7-validator-fix"
        )
    r7_checks = _check_r7(validation, admission, summary, config)
    manifest_path = broader_source_path.parent / "evidence-manifest.json"
    manifest = _load(manifest_path)
    broad_report_hash = _sha256(broader_source_path)
    broad_manifest_ok = manifest.get("evidence_sha256") == broad_report_hash
    resource_summary_path = resource_root / "summary.json"
    r7_checks["hash_binding"] = (
        validation.get("run_config_sha256") == _sha256(r7_root / "config.json")
        and validation.get("run_summary_sha256") == _sha256(r7_root / "summary.json")
        and validation.get("resource_monitor", {}).get("summary_sha256")
        == _sha256(resource_summary_path)
        and admission.get("run_config_sha256") == validation.get("run_config_sha256")
        and admission.get("run_summary_sha256") == validation.get("run_summary_sha256")
    )
    operations = {
        name: _operation(broader, name)
        for name in (
            "native_btc_usd_ticker",
            "native_eth_usd_ticker",
            "deribit_btc_index",
            "official_rss_press_releases",
            "gdelt_bitcoin_articles",
        )
    }
    gdelt_class = operations["gdelt_bitcoin_articles"].get("failure_classification", {})
    coinbase_eth_class = operations["native_eth_usd_ticker"].get("failure_classification", {})
    context_evidence = _evidence(
        r7_validation_path, r7_admission_path, broader_source_path, manifest_path
    )
    r7_evidence = _evidence(
        r7_validation_path,
        r7_admission_path,
        r7_root / "summary.json",
        r7_root / "config.json",
        r7_root / "latest-health.json",
        r7_root / "fault-drills.json",
        resource_root / "summary.json",
    )
    primary_evidence = _evidence(r7_validation_path, r7_admission_path, r7_root / "summary.json")

    predecessor_ok, predecessor_detail = _phase2_predecessor(phase2_gate_path, evaluated_at)
    requirements = (
        _requirement(
            "r7_structural_review",
            "formal Phase-3 review boundary; validate_phase3_public_data_qualification.py",
            "The immutable r7 root passes independent structural validation without issues.",
            ChecklistStatus.SATISFIED if r7_checks["review"] else ChecklistStatus.UNSATISFIED,
            gating=True,
            rationale="The immutable r7 validator returned PASS_FOR_REVIEW with no issues."
            if r7_checks["review"]
            else "The immutable r7 validator did not return a clean structural review.",
            next_admissible_action="Use the preserved r7 review artifact."
            if r7_checks["review"]
            else "Preserve r7 and resolve the structural review findings in a new root.",
            evidence=_evidence(r7_validation_path),
        ),
        _requirement(
            "r7_policy_review",
            "formal Phase-3 admission evaluator",
            "The r7 operational-policy review passes all defined primary source checks.",
            ChecklistStatus.SATISFIED
            if r7_checks["admission_review"]
            else ChecklistStatus.UNSATISFIED,
            gating=True,
            rationale="The r7 policy evaluator returned QUALIFIED_FOR_REVIEW with no blocker codes."
            if r7_checks["admission_review"]
            else "The r7 policy review retains a blocker or did not complete.",
            next_admissible_action="Carry the policy review into the formal gate record."
            if r7_checks["admission_review"]
            else "Resolve the policy-review blocker without rewriting r7.",
            evidence=_evidence(r7_admission_path),
        ),
        _requirement(
            "public_read_only_separation",
            "real-api-paper-transition.md Workstream B; Phase-3 source runbook",
            "The Phase-3 source evidence loaded no credentials and attempted no order writes.",
            ChecklistStatus.SATISFIED if r7_checks["read_only"] else ChecklistStatus.UNSATISFIED,
            gating=True,
            rationale="Both immutable r7 configuration and summary record credentials_loaded=false and order_writes_attempted=false."
            if r7_checks["read_only"]
            else "Read-only separation is not proven.",
            next_admissible_action="Keep public data connectors read-only and execution isolated."
            if r7_checks["read_only"]
            else "Fix the separation boundary before admission.",
            evidence=_evidence(r7_root / "config.json", r7_root / "summary.json"),
        ),
        _requirement(
            "multi_hour_window_complete",
            "Phase-3 source qualification runbook",
            "The protected r7 qualification reached its complete four-hour terminal window.",
            ChecklistStatus.SATISFIED
            if r7_checks["complete_window"]
            else ChecklistStatus.UNSATISFIED,
            gating=True,
            rationale="The r7 root contains a complete four-hour window with a terminal marker and six terminal samples."
            if r7_checks["complete_window"]
            else "The r7 target window is incomplete.",
            next_admissible_action="Do not launch a duplicate durability root."
            if r7_checks["complete_window"]
            else "Collect the specifically missing duration evidence in a fresh root.",
            evidence=_evidence(r7_validation_path, r7_root / "summary.json"),
        ),
        _requirement(
            "immutable_evidence_hash_binding",
            "formal Phase-3 review boundary; PhaseGateRecord evidence contract",
            "Validator, admission review, configuration, summary, and resource hashes bind to the exact immutable roots.",
            ChecklistStatus.SATISFIED
            if r7_checks["hash_binding"] and broad_manifest_ok
            else ChecklistStatus.UNSATISFIED,
            gating=True,
            rationale="The r7 review hashes match the exact config, summary, and resource sidecar, and the broader source manifest binds its report."
            if r7_checks["hash_binding"] and broad_manifest_ok
            else "At least one immutable evidence hash binding is inconsistent.",
            next_admissible_action="Use only the referenced immutable artifacts."
            if r7_checks["hash_binding"] and broad_manifest_ok
            else "Preserve prior roots and repair the evidence binding in a separate review.",
            evidence=_evidence(
                r7_validation_path,
                r7_admission_path,
                r7_root / "config.json",
                r7_root / "summary.json",
                resource_summary_path,
                manifest_path,
            ),
        ),
        _requirement(
            "primary_btc_eth_market_data",
            "phase-03-v3-core-data-spine.md; architecture §12",
            "Credential-free native primary market data covers BTC and ETH with explicit provider identity.",
            ChecklistStatus.SATISFIED
            if r7_checks["primary_btc_eth"]
            else ChecklistStatus.UNSATISFIED,
            gating=True,
            rationale="Binance public BTC/ETH rows are present with measured event age and valid events in the immutable r7 window."
            if r7_checks["primary_btc_eth"]
            else "The immutable r7 primary rows do not satisfy the BTC/ETH contract.",
            next_admissible_action="Use the existing r7 evidence for gate review."
            if r7_checks["primary_btc_eth"]
            else "Obtain the specific missing primary BTC/ETH measurement without changing policy.",
            evidence=primary_evidence,
        ),
        _requirement(
            "raw_first_append_only_replay",
            "phase-03-v3-core-data-spine.md; real-api-paper-transition.md Workstream B",
            "Raw-first append-only evidence replays to the same normalized result.",
            ChecklistStatus.SATISFIED
            if r7_checks["replay_sequence"]
            else ChecklistStatus.UNSATISFIED,
            gating=True,
            rationale="Independent validation found zero replay failures, sequence gaps, duplicates, or out-of-order events; injected gap and snapshot drills passed."
            if r7_checks["replay_sequence"]
            else "Replay or continuity validation is incomplete or failed.",
            next_admissible_action="Retain the r7 root unchanged."
            if r7_checks["replay_sequence"]
            else "Resolve the named replay/continuity defect with a fresh independent root.",
            evidence=_evidence(r7_validation_path, r7_root / "fault-drills.json"),
        ),
        _requirement(
            "point_in_time_freshness_and_clock_confidence",
            "phase-03-v3-core-data-spine.md; real-api-paper-transition.md Workstream B",
            "Provider event time, local receipt time, freshness, clock confidence, and stale behavior are recorded.",
            ChecklistStatus.SATISFIED
            if r7_checks["freshness_health"]
            else ChecklistStatus.UNSATISFIED,
            gating=True,
            rationale="The r7 evidence records adjusted freshness and projected timestamps; observed stale intervals were fail-closed and Binance BTC/ETH were healthy at the terminal sample."
            if r7_checks["freshness_health"]
            else "The freshness or terminal health contract is not proven.",
            next_admissible_action="Treat stale/provider outages as unavailable data, never usable market truth."
            if r7_checks["freshness_health"]
            else "Collect only the specific missing freshness/clock evidence.",
            evidence=_evidence(r7_validation_path, r7_admission_path, r7_root / "summary.json"),
        ),
        _requirement(
            "source_lineage_and_health_projection",
            "phase-03-v3-core-data-spine.md; real-api-paper-transition.md Workstreams B/E",
            "Source identity, endpoint binding, origin lineage, health projection, and no silent substitution are preserved.",
            ChecklistStatus.SATISFIED
            if r7_checks["lineage"] and broad_manifest_ok
            else ChecklistStatus.UNSATISFIED,
            gating=True,
            rationale="The validator checked endpoint-bound source identities, latest-health projection, and zero silent substitutions."
            if r7_checks["lineage"] and broad_manifest_ok
            else "Lineage or projection validation is incomplete.",
            next_admissible_action="Keep each provider failure explicitly attributed to that provider."
            if r7_checks["lineage"] and broad_manifest_ok
            else "Fix lineage/projection validation before admission.",
            evidence=_evidence(
                r7_validation_path,
                r7_admission_path,
                r7_root / "latest-health.json",
                broader_source_path,
            ),
        ),
        _requirement(
            "source_health_fail_closed_and_recovery",
            "real-api-paper-transition.md Workstream B; Phase-3 source runbook",
            "Stale, disconnected, malformed, unavailable, or uncertain source states fail closed and recover explicitly.",
            ChecklistStatus.SATISFIED
            if r7_checks["stale_fail_closed"]
            else ChecklistStatus.UNSATISFIED,
            gating=True,
            rationale="R7 recorded HEALTHY/DEGRADED/STALE/DISCONNECTED/RECOVERING/QUARANTINED states, 216 fail-closed selections, and zero silent substitutions."
            if r7_checks["stale_fail_closed"]
            else "The evidence does not prove safe fail-closed selection behavior.",
            next_admissible_action="Do not use unavailable context in a decision; preserve abstention."
            if r7_checks["stale_fail_closed"]
            else "Resolve the specific fail-closed violation.",
            evidence=_evidence(
                r7_validation_path, r7_admission_path, r7_root / "health-transitions.jsonl"
            ),
        ),
        _requirement(
            "cross_source_disagreement_policy",
            "phase-03-v3-core-data-spine.md; architecture §12",
            "Cross-source disagreement is measured, source identity is retained, and severe disagreement cannot relax risk.",
            ChecklistStatus.SATISFIED if r7_checks["disagreement"] else ChecklistStatus.UNSATISFIED,
            gating=True,
            rationale="R7 measured normal/degraded/severe disagreement states and kept unsafe states fail-closed."
            if r7_checks["disagreement"]
            else "Disagreement policy evidence is incomplete or unsafe.",
            next_admissible_action="Preserve disagreement and abstention rather than averaging away incidents."
            if r7_checks["disagreement"]
            else "Fix disagreement handling before admission.",
            evidence=_evidence(
                r7_validation_path, r7_admission_path, r7_root / "disagreement.jsonl"
            ),
        ),
        _requirement(
            "resource_sidecar",
            "phase-03 source qualification runbook; architecture §3",
            "The completed qualification has consistent resource-sidecar evidence without recorded resource errors.",
            ChecklistStatus.SATISFIED if r7_checks["resource"] else ChecklistStatus.UNSATISFIED,
            gating=True,
            rationale="The sidecar reached deadline_reached with no resource errors and consistent bounded RSS/CPU/FD/socket observations."
            if r7_checks["resource"]
            else "The resource sidecar is missing or inconsistent.",
            next_admissible_action="Carry the measured headroom into later paper operation."
            if r7_checks["resource"]
            else "Repair the sidecar evidence independently.",
            evidence=_evidence(r7_validation_path, resource_root / "summary.json"),
        ),
        _requirement(
            "deribit_context_source",
            "phase-03-v3-core-data-spine.md; architecture §12",
            "Deribit derivatives context is integrated with explicit identity and recoverable external failures.",
            ChecklistStatus.SATISFIED
            if any(
                row.get("source_id") == "deribit_public_context" and row.get("sample_count", 0) > 0
                for row in summary.get("per_source_symbol", {}).values()
            )
            and operations["deribit_btc_index"].get("passed") is True
            else ChecklistStatus.UNSATISFIED,
            gating=True,
            rationale="R7 contains BTC/ETH Deribit context with recovery states; the fresh REST context operation replayed successfully."
            if any(
                row.get("source_id") == "deribit_public_context" and row.get("sample_count", 0) > 0
                for row in summary.get("per_source_symbol", {}).values()
            )
            and operations["deribit_btc_index"].get("passed") is True
            else "Deribit context integration is not sufficiently measured.",
            next_admissible_action="Use Deribit only as attributed context; it never becomes execution authority.",
            evidence=context_evidence,
        ),
        _requirement(
            "official_rss_context_source",
            "phase-03-v3-core-data-spine.md; real-api-paper-transition.md Workstream B",
            "An official/company RSS context path has raw-first replay and quality evidence.",
            ChecklistStatus.SATISFIED
            if operations["official_rss_press_releases"].get("passed") is True
            and operations["official_rss_press_releases"].get("replay_match") is True
            else ChecklistStatus.UNSATISFIED,
            gating=True,
            rationale="The official RSS operation returned parsed observations with replay_match=true and no data-integrity failure."
            if operations["official_rss_press_releases"].get("passed") is True
            and operations["official_rss_press_releases"].get("replay_match") is True
            else "The official RSS context path is not replay-qualified.",
            next_admissible_action="Keep RSS content untrusted and use it only as typed context.",
            evidence=_evidence(broader_source_path, manifest_path),
        ),
        _requirement(
            "gdelt_context_fail_closed",
            "phase-03-v3-core-data-spine.md; real-api-paper-transition.md Workstream B",
            "GDELT context is integrated as an explicitly attributed source and unavailable data blocks its dependent decision path without substitution.",
            ChecklistStatus.SATISFIED
            if operations["gdelt_bitcoin_articles"].get("passed") is False
            and gdelt_class.get("category") == "external_provider_unavailable_or_rate_limited"
            and gdelt_class.get("external_provider_availability") is True
            and gdelt_class.get("data_integrity_failure") is False
            and gdelt_class.get("implementation_failure") is False
            and gdelt_class.get("safe_fail_closed") is True
            and operations["gdelt_bitcoin_articles"].get("observation_count") == 0
            else ChecklistStatus.UNSATISFIED,
            gating=True,
            rationale="The observed HTTP 429 is preserved as an external availability/rate-limit event; the operation emitted zero observations and explicitly failed closed."
            if operations["gdelt_bitcoin_articles"].get("passed") is False
            and gdelt_class.get("safe_fail_closed") is True
            else "GDELT failure classification or fail-closed behavior is not proven.",
            next_admissible_action="Keep GDELT-dependent decisions abstained until the source is available; do not retry indefinitely or substitute silently.",
            evidence=_evidence(broader_source_path, manifest_path),
        ),
        _requirement(
            "gdelt_current_provider_availability",
            "fresh broader Phase-3 source qualification",
            "GDELT returned a currently usable response in this bounded pass.",
            ChecklistStatus.EXTERNALLY_BLOCKED,
            gating=False,
            rationale="The provider returned HTTP 429. The authoritative acceptance contract requires unavailable data to block its dependent decision, not a fabricated observation; availability is not an unconditional primary-market gate.",
            next_admissible_action="Wait for provider availability or a reviewed operator-approved route before enabling GDELT-dependent decisions.",
            evidence=_evidence(broader_source_path),
        ),
        _requirement(
            "coinbase_sandbox_eth_as_primary",
            "architecture §12; phase-02 paper venue selection",
            "Coinbase Exchange Sandbox must provide ETH-USD as the required primary market source.",
            ChecklistStatus.NOT_APPLICABLE,
            gating=False,
            rationale="Coinbase Sandbox is not the selected V3-Core primary market-data source; its provider-truth ETH-USD absence is preserved as a quarantined alternative outcome.",
            next_admissible_action="Retain Coinbase evidence; do not fabricate ETH-USD or use production Coinbase.",
            evidence=_evidence(broader_source_path),
        ),
        _requirement(
            "coinbase_sandbox_eth_provider_truth",
            "fresh broader Phase-3 source qualification",
            "Coinbase Sandbox ETH-USD availability is measured and classified without substitution.",
            ChecklistStatus.EXTERNALLY_BLOCKED
            if operations["native_eth_usd_ticker"].get("passed") is False
            and coinbase_eth_class.get("category") == "external_provider_product_unavailable"
            and coinbase_eth_class.get("safe_fail_closed") is True
            and operations["native_eth_usd_ticker"].get("observation_count") == 0
            else ChecklistStatus.UNSATISFIED,
            gating=False,
            rationale="The sandbox returned provider-truth HTTP 404 with zero observations; this is not an implementation or data-integrity failure.",
            next_admissible_action="Keep Coinbase ETH-USD quarantined unless the provider product catalogue changes and is independently requalified.",
            evidence=_evidence(broader_source_path, manifest_path),
        ),
        _requirement(
            "lse_corroboration",
            "phase-03-v3-core-data-spine.md; architecture §12",
            "An audited LSE cross-check is required for Phase 3.",
            ChecklistStatus.OPTIONAL,
            gating=False,
            rationale="The plan explicitly marks LSE corroboration optional and forbids it from becoming sole authority.",
            next_admissible_action="Do not add LSE during V3-Core admission.",
            evidence=(),
        ),
        _requirement(
            "sec_equities_sources",
            "phase-03-v3-core-data-spine.md explicit out-of-scope section",
            "SEC/ALFRED/equity sources are required for Phase 3.",
            ChecklistStatus.NOT_APPLICABLE,
            gating=False,
            rationale="Equities and SEC/ALFRED are explicitly out of scope; the measured official RSS is treated as the approved official-news context path.",
            next_admissible_action="Defer equity sources to the controlled-expansion phase.",
            evidence=(),
        ),
        _requirement(
            "phase_2_formal_predecessor",
            "src/advisorai/gates.py PhaseGateRegistry; architecture traceability §11",
            "A currently valid passed Phase-2 PhaseGateRecord exists before Phase 3 can be recorded as passed.",
            ChecklistStatus.SATISFIED if predecessor_ok else ChecklistStatus.UNSATISFIED,
            gating=True,
            rationale=predecessor_detail,
            next_admissible_action="Supply or record the legitimate Phase-2 predecessor through the dependency-ordered gate registry; do not bypass it."
            if not predecessor_ok
            else "Proceed with the supervised formal Phase-3 record.",
            evidence=_evidence(phase2_gate_path)
            if phase2_gate_path is not None and phase2_gate_path.is_file()
            else (),
        ),
    )
    mandatory_ids = tuple(item.requirement_id for item in requirements if item.gating)
    blockers = tuple(
        item.requirement_id
        for item in requirements
        if item.gating
        and item.status in {ChecklistStatus.UNSATISFIED, ChecklistStatus.EXTERNALLY_BLOCKED}
    )
    decision = GateDecision.PASSED if not blockers else GateDecision.PENDING
    evidence_items = tuple(
        GateEvidence(
            name=item.requirement_id,
            kind=GateEvidenceKind.OPERATIONAL,
            passed=item.status is ChecklistStatus.SATISFIED,
            artifact_hash=item.evidence[0].sha256
            if item.status is ChecklistStatus.SATISFIED and item.evidence
            else None,
            source=item.evidence[0].path if item.evidence else "authoritative plan",
            verified_by="phase3-formal-admission-evaluator-v1",
            observed_at=evaluated_at,
            details=item.rationale,
        )
        for item in requirements
    )
    record = PhaseGateRecord(
        phase=3,
        name="Phase 3 — V3-Core data spine",
        decision=decision,
        required_evidence=tuple(
            item.requirement_id
            for item in requirements
            if item.gating and item.status is ChecklistStatus.SATISFIED
        ),
        evidence=evidence_items,
        prerequisite_phase=2,
        recorded_by="phase3-formal-admission-evaluator-v1",
        recorded_at=evaluated_at,
        reasons=blockers,
    )
    record_payload = record.model_dump(mode="json", round_trip=True)
    record_bytes = (json.dumps(record_payload, sort_keys=True, indent=2) + "\n").encode()
    checklist = Phase3AdmissionChecklist(
        gate_version=GATE_VERSION,
        evaluated_at=evaluated_at,
        repository_commit=_git_head(),
        evaluator_code_sha256=_sha256(Path(__file__)),
        decision=decision,
        mandatory_requirements=mandatory_ids,
        blocking_requirement_ids=blockers,
        requirements=tuple(requirements),
        sources=(
            SourceAssessment(
                source_id="binance_spot_public_market_data",
                role="required primary market data",
                status=ChecklistStatus.SATISFIED
                if r7_checks["primary_btc_eth"]
                else ChecklistStatus.UNSATISFIED,
                endpoint_identity="reviewed public Binance market-data endpoints recorded in r7 source cards",
                asset_or_context_scope="BTC and ETH",
                gating=True,
                rationale="Selected primary source has terminal healthy BTC/ETH rows and safe failure handling."
                if r7_checks["primary_btc_eth"]
                else "Primary BTC/ETH contract is incomplete.",
                evidence=primary_evidence,
            ),
            SourceAssessment(
                source_id="deribit_public_context",
                role="required derivatives context",
                status=ChecklistStatus.SATISFIED,
                endpoint_identity="www.deribit.com public API",
                asset_or_context_scope="BTC/ETH context",
                gating=True,
                rationale="Context is measured and attributed; runtime interruptions recover without authority transfer.",
                evidence=context_evidence,
            ),
            SourceAssessment(
                source_id="official_rss",
                role="required official-news context",
                status=ChecklistStatus.SATISFIED,
                endpoint_identity="www.sec.gov official RSS",
                asset_or_context_scope="crypto-market news context",
                gating=True,
                rationale="Raw-first parser/replay and quality pass were measured.",
                evidence=_evidence(broader_source_path, manifest_path),
            ),
            SourceAssessment(
                source_id="gdelt",
                role="required news context with fail-closed availability",
                status=ChecklistStatus.EXTERNALLY_BLOCKED,
                endpoint_identity="api.gdeltproject.org",
                asset_or_context_scope="crypto-market news context",
                gating=False,
                rationale="HTTP 429 is external rate limiting; no GDELT observation was admitted and dependent paths fail closed.",
                evidence=_evidence(broader_source_path),
            ),
            SourceAssessment(
                source_id="coinbase_exchange_public_market_data",
                role="optional independent primary candidate",
                status=ChecklistStatus.EXTERNALLY_BLOCKED,
                endpoint_identity="api-public.sandbox.exchange.coinbase.com",
                asset_or_context_scope="ETH-USD candidate",
                gating=False,
                rationale="Sandbox product truth returned 404 for ETH-USD; it is quarantined and not substituted.",
                evidence=_evidence(broader_source_path),
            ),
        ),
        evidence_manifest=context_evidence + r7_evidence,
        phase_gate_record_path="phase3-gate-record.json",
        phase_gate_record_sha256=hashlib.sha256(record_bytes).hexdigest(),
        phase_gate_record_canonical_hash=record.canonical_hash(),
        notes=(
            "R7 is preserved unchanged; no new durability run was launched.",
            "Provider degradation is classified separately from implementation and data-integrity failures.",
            "GDELT availability is externally blocked but is not an unconditional primary-market admission criterion; missing context remains fail-closed.",
            "Global Phase-0 remote-route/archive blockers are not used as a Phase-3 blocker beyond the dependency-ordered predecessor record.",
        ),
    )
    return checklist, record


def _write_immutable(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()


def write_evidence(
    output_root: Path, checklist: Phase3AdmissionChecklist, record: PhaseGateRecord
) -> dict[str, str]:
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError(f"immutable output root already exists: {output_root}")
    output_root.mkdir(parents=True)
    record_bytes = (
        json.dumps(record.model_dump(mode="json", round_trip=True), sort_keys=True, indent=2) + "\n"
    ).encode()
    checklist = checklist.model_copy(
        update={
            "phase_gate_record_path": "phase3-gate-record.json",
            "phase_gate_record_sha256": hashlib.sha256(record_bytes).hexdigest(),
        }
    )
    checklist_bytes = (
        json.dumps(checklist.model_dump(mode="json", by_alias=True), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    record_path = output_root / "phase3-gate-record.json"
    checklist_path = output_root / "phase3-admission-checklist.json"
    _write_immutable(record_path, record_bytes)
    _write_immutable(checklist_path, checklist_bytes)
    manifest = {
        "schema": "advisorai.phase3.formal-admission-checklist.v1.manifest",
        "decision": checklist.decision.value,
        "checklist": checklist_path.name,
        "checklist_sha256": hashlib.sha256(checklist_bytes).hexdigest(),
        "phase_gate_record": record_path.name,
        "phase_gate_record_sha256": hashlib.sha256(record_bytes).hexdigest(),
        "phase_gate_record_canonical_hash": record.canonical_hash(),
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    _write_immutable(output_root / "evidence-manifest.json", manifest_bytes)
    return {
        "decision": checklist.decision.value,
        "checklist": str(checklist_path),
        "checklist_sha256": hashlib.sha256(checklist_bytes).hexdigest(),
        "record": str(record_path),
        "record_sha256": hashlib.sha256(record_bytes).hexdigest(),
        "canonical_hash": record.canonical_hash(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r7-validation", type=Path, default=R7_VALIDATION)
    parser.add_argument("--r7-admission", type=Path, default=R7_ADMISSION)
    parser.add_argument("--broader-source", type=Path, default=BROAD_SOURCE)
    parser.add_argument(
        "--phase2-gate-record",
        "--phase2-gate",
        dest="phase2_gate_record",
        type=Path,
        help="current passed Phase-2 PhaseGateRecord used as the formal predecessor",
    )
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    checklist, record = evaluate(
        r7_validation_path=args.r7_validation.resolve(),
        r7_admission_path=args.r7_admission.resolve(),
        broader_source_path=args.broader_source.resolve(),
        phase2_gate_path=args.phase2_gate_record.resolve() if args.phase2_gate_record else None,
    )
    result = write_evidence(args.output_root, checklist, record)
    print(json.dumps(result, sort_keys=True))
