#!/usr/bin/env python3
"""Evaluate the formal Phase-2 paper-core gate from immutable evidence.

This command is deliberately offline.  It reads the preserved Binance Spot
Testnet public/read-only/lifecycle reports and the checked-in adapter boundary,
then writes a requirement checklist and a typed ``PhaseGateRecord``.  It does
not load secrets, access a venue, submit an order, alter an existing artifact,
or claim Phase-6 fill evidence from the measured no-fill lifecycle.
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

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "advisorai.phase2.formal-admission-checklist.v1"
GATE_VERSION = "phase2-binance-spot-testnet-admission-v1"

PUBLIC_TRUTH = REPOSITORY_ROOT / (
    "artifacts/phase2/binance-spot-testnet/public-truth/"
    "20260810T165904.357047Z/binance-spot-testnet-public-truth.json"
)
READ_ONLY = REPOSITORY_ROOT / (
    "artifacts/phase2/binance-spot-testnet/read-only-smoke/"
    "20260810T193840.598161Z/binance-spot-testnet-read-only-smoke.json"
)
LIFECYCLE = REPOSITORY_ROOT / (
    "artifacts/phase2/binance-spot-testnet/paper-lifecycle/"
    "20260810T195818.312420Z/binance-spot-testnet-paper-lifecycle.json"
)
LOCAL_PHASE1 = REPOSITORY_ROOT / (
    "artifacts/phase1/local-rebuild/20260808T024709.706561Z/phase1-local-rebuild.json"
)
PHASE1_RECOVERY = REPOSITORY_ROOT / (
    "artifacts/phase1/binance-spot-testnet/recovery/"
    "20260811T064829.840702Z/binance-spot-testnet-recovery.json"
)
ADAPTER = REPOSITORY_ROOT / "src/advisorai/integrations/binance_spot.py"
ADAPTER_TEST = REPOSITORY_ROOT / "tests/integrations/test_binance_spot.py"
LIFECYCLE_TEST = REPOSITORY_ROOT / "tests/integrations/test_binance_spot_lifecycle.py"


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


class VenueAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    venue: str = Field(min_length=1)
    environment: str = Field(min_length=1)
    rest_endpoint: str = Field(min_length=1)
    reviewed_host: str = Field(min_length=1)
    symbols: tuple[str, ...]
    real_fill_observed: bool
    phase6_fill_scope: str = Field(min_length=1)
    evidence: tuple[EvidenceReference, ...]


class Phase2AdmissionChecklist(BaseModel):
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
    venue: VenueAssessment
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


class Phase2EvidenceRefused(ValueError):
    """Raised when the offline Phase-2 evidence contract is not admissible."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise Phase2EvidenceRefused(f"required evidence is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Phase2EvidenceRefused(f"required evidence is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise Phase2EvidenceRefused(f"required evidence must be a JSON object: {path}")
    return payload


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError as exc:
        raise Phase2EvidenceRefused(f"evidence path escapes repository: {path}") from exc


def _evidence(*paths: Path) -> tuple[EvidenceReference, ...]:
    references: list[EvidenceReference] = []
    seen: set[str] = set()
    for path in paths:
        resolved = path.resolve()
        relative = _relative(resolved)
        if relative in seen:
            continue
        references.append(EvidenceReference(path=relative, sha256=_sha256(resolved)))
        seen.add(relative)
    return tuple(references)


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().lower()


def _result(report: dict[str, Any]) -> dict[str, Any]:
    result = report.get("result")
    if not isinstance(result, dict):
        raise Phase2EvidenceRefused("Binance report is missing its result object")
    return result


def _operations(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    operations = report.get("operations")
    if not isinstance(operations, list):
        raise Phase2EvidenceRefused("Binance report operations must be an array")
    result: dict[str, dict[str, Any]] = {}
    for item in operations:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            result[item["name"]] = item
    return result


def _all_ok(operations: dict[str, dict[str, Any]], names: tuple[str, ...]) -> bool:
    return all(operations.get(name, {}).get("status") == "ok" for name in names)


def _source_controls() -> dict[str, bool]:
    source = ADAPTER.read_text(encoding="utf-8") if ADAPTER.is_file() else ""
    tests = (ADAPTER_TEST.read_text(encoding="utf-8") if ADAPTER_TEST.is_file() else "") + (
        LIFECYCLE_TEST.read_text(encoding="utf-8") if LIFECYCLE_TEST.is_file() else ""
    )
    required_source_tokens = (
        "BINANCE_SPOT_TESTNET_BASE_URL",
        "BINANCE_SPOT_TESTNET_HOST",
        "allowed_hosts",
        "_FORBIDDEN_PATH_PARTS",
        "withdraw",
        "transfer",
        "fapi",
        "dapi",
        "papi",
        "production",
        "max_retries=0 if write else None",
    )
    return {
        "adapter_boundary": bool(source)
        and all(token in source for token in required_source_tokens),
        "production_rejection_tests": "test_binance_adapter_rejects_production_and_non_spot_paths"
        in tests,
        "risk_oms_tests": "order_risk_approved" in tests and "order_created" in tests,
    }


def _phase2_checks(
    read_only: dict[str, Any],
    lifecycle: dict[str, Any],
    *,
    read_only_path: Path = READ_ONLY,
    phase1_local_path: Path = LOCAL_PHASE1,
) -> dict[str, bool]:
    """Return deterministic checks used by the formalizer and fixture tests."""

    ro = _result(read_only)
    lc = _result(lifecycle)
    ro_ops = _operations(ro)
    lc_ops = _operations(lc)
    source_controls = _source_controls()
    required_symbols = {"BTCUSDT", "ETHUSDT"}
    product_symbols = set(ro_ops.get("products", {}).get("required_symbols", ()))
    mapped_symbols = set(ro_ops.get("product_mapping_verification", {}).get("admitted_symbols", ()))
    target = lc.get("target", {})
    order = lc.get("order", {})
    writes = [
        item
        for item in lc.get("operations", ())
        if isinstance(item, dict) and item.get("write") is True
    ]
    forbidden_operations = [
        item
        for item in lc.get("operations", ())
        if isinstance(item, dict)
        and any(
            part in str(item.get("endpoint", "")).lower()
            for part in ("sapi", "fapi", "dapi", "papi", "transfer", "withdraw")
        )
    ]
    return {
        "phase2_exit_gate": lc.get("status") == "passed"
        and all(item.get("status") == "passed" for item in lc.get("failure_drills", {}).values()),
        "venue_identity_and_host": all(
            item.get(key) == expected
            for item in (ro, lc)
            for key, expected in (
                ("venue", "binance_spot_testnet"),
                ("environment", "paper_testnet"),
                ("endpoint", "https://testnet.binance.vision"),
                ("reviewed_host", "testnet.binance.vision"),
            )
        ),
        "production_host_rejection": source_controls["adapter_boundary"]
        and source_controls["production_rejection_tests"]
        and not forbidden_operations,
        "credential_scope": (
            ro.get("credential_refs")
            == [
                "ADVISORAI_VENUE_API_KEY",
                "ADVISORAI_VENUE_API_SECRET",
            ]
            and lc.get("credential_refs") == ro.get("credential_refs")
        ),
        "provider_truth_btc_eth": ro.get("status") == "passed"
        and required_symbols.issubset(product_symbols)
        and required_symbols.issubset(mapped_symbols),
        "provider_filters": (
            ro_ops.get("products", {}).get("status") == "ok"
            and all(
                field in ro_ops.get("products", {}).get("schema_fields", ())
                for field in ("filters", "status", "symbol")
            )
            and all(
                bool(target.get(field))
                for field in (
                    "base_increment",
                    "quote_increment",
                    "base_min_qty",
                    "min_notional",
                    "provider_filter_status",
                )
            )
        ),
        "authenticated_account_reads": ro.get("status") == "passed"
        and _all_ok(
            ro_ops,
            ("server_time", "account_state", "balances", "positions", "open_orders", "fills"),
        ),
        "deterministic_signing_and_scope": source_controls["adapter_boundary"]
        and lc.get("signed_submission_count") == 1
        and lc.get("signed_cancellation_count") == 1,
        "no_transfer_withdrawal_margin_futures": source_controls["adapter_boundary"]
        and not forbidden_operations
        and all(
            part not in str(item.get("endpoint", "")).lower()
            for item in writes
            for part in ("sapi", "fapi", "dapi", "papi", "transfer", "withdraw")
        ),
        "risk_kernel_approval": lc.get("risk_decision", {}).get("outcome") == "approved"
        and lc.get("order_risk_check", {}).get("approved") is True,
        "target_state_binding": target.get("symbol") == "BTCUSDT"
        and target.get("instrument") == "crypto:BTCUSDT:binance_spot_testnet:spot"
        and order.get("quantity_filter_validated") is True
        and order.get("price_filter_validated") is True
        and order.get("notional_filter_validated") is True,
        "oms_intent_before_submission": lc.get("intent_persisted_before_submission") is True
        and lc.get("oms_state_before_network_assertion") is True
        and lc.get("oms_state_before_submission") == "risk_approved",
        "deterministic_client_identity": order.get("deterministic_client_order_id") is not None
        and order.get("client_order_id_sha256") is not None,
        "signed_write_ack_handling": lc.get("signed_submission_count") == 1
        and lc.get("failure_drills", {}).get("ambiguous_acknowledgement", {}).get("automatic_retry")
        is False
        and lc.get("failure_drills", {}).get("ambiguous_acknowledgement", {}).get("submit_calls")
        == 1
        and lc.get("venue_acknowledgement", {}).get("accepted") is True,
        "venue_query_reconciliation": lc.get("authoritative_order_query", {}).get("status") == "ok"
        and lc.get("reconciliation", {}).get("reconciled") is True
        and lc.get("reconciliation", {}).get("discrepancy_count") == 0,
        "cancellation_terminal_state": lc.get("signed_cancellation_count") == 1
        and lc.get("terminal_oms_state") == "reconciled"
        and lc.get("reconciliation", {}).get("venue_open_order_count") == 0,
        "restart_hydration": lc.get("restart_recovery", {}).get("status") == "passed"
        and lc.get("restart_recovery", {}).get("duplicate_submission") is False
        and lc.get("restart_recovery", {}).get("signed_submissions_after_restart") == 0,
        "tca_attribution": lc.get("tca", {}).get("status") == "passed"
        and lc.get("attribution", {}).get("status") == "passed"
        and lc.get("attribution", {}).get("unexplained_residual") == "0",
        "failure_drills": all(
            isinstance(value, dict) and value.get("status") == "passed"
            for value in lc.get("failure_drills", {}).values()
        ),
        "no_production_execution": ro.get("writes_attempted") is False
        and lc.get("endpoint") == "https://testnet.binance.vision"
        and all(item.get("endpoint") == "/api/v3/order" for item in writes)
        and lc.get("signed_submission_count") == 1,
        "phase1_local_exit_context": _load(phase1_local_path).get("passed") is True,
        "phase2_fill_scope": lc.get("fill_ingestion", {}).get("status") == "no_fill_observed"
        and lc.get("fill_ingestion", {}).get("real_fill_count") == 0,
        "lifecycle_operation_sequence": lc.get("oms_event_sequence")
        == [
            "order_created",
            "order_risk_approved",
            "order_routed",
            "order_acknowledged",
            "order_cancel_pending",
            "order_cancelled",
            "order_reconciled",
            "reconciliation_recorded",
        ]
        and _all_ok(
            lc_ops, ("submit_order", "authoritative_order_query_after_submission", "cancel_order")
        ),
        "read_only_pointer": (
            lc.get("read_only_evidence", {}).get("manifest") == _relative(read_only_path)
            and lc.get("read_only_evidence", {}).get("manifest_sha256") == _sha256(read_only_path)
        ),
    }


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


def _build_requirements(
    checks: dict[str, bool], evidence: dict[str, tuple[EvidenceReference, ...]]
) -> tuple[RequirementResult, ...]:
    plan = "phase-02-paper-core.md; real-api-paper-transition.md Workstream D"
    rows: list[RequirementResult] = []

    def add(
        key: str,
        requirement: str,
        *,
        source: str = plan,
        evidence_key: str = "lifecycle",
        rationale_pass: str,
        rationale_fail: str,
    ) -> None:
        passed = checks[key]
        rows.append(
            _requirement(
                key,
                source,
                requirement,
                ChecklistStatus.SATISFIED if passed else ChecklistStatus.UNSATISFIED,
                gating=True,
                rationale=rationale_pass if passed else rationale_fail,
                next_admissible_action="Retain the immutable measured evidence."
                if passed
                else "Preserve the evidence and resolve this Phase-2 requirement before admission.",
                evidence=evidence[evidence_key],
            )
        )

    add(
        "phase2_exit_gate",
        "The Phase-2 safety, failure-drill, and reconciliation exit gate passes.",
        rationale_pass="The supervised lifecycle and all recorded failure drills passed without opening a second authority path.",
        rationale_fail="The lifecycle or one of its failure drills did not pass.",
    )
    add(
        "venue_identity_and_host",
        "The selected venue is Binance Spot Testnet on the exact reviewed paper host.",
        evidence_key="read_lifecycle",
        rationale_pass="Both real reports bind venue, paper environment, exact REST endpoint, and reviewed host.",
        rationale_fail="Venue identity, environment, endpoint, or reviewed host differs from the approved testnet boundary.",
    )
    add(
        "production_host_rejection",
        "The adapter rejects production/non-Spot/transfer/withdrawal paths.",
        source="Binance runbook; src/advisorai/integrations/binance_spot.py; adapter boundary tests",
        evidence_key="controls",
        rationale_pass="The source boundary and negative tests enforce exact testnet host/path restrictions.",
        rationale_fail="The checked-in adapter boundary or negative tests do not prove production/path rejection.",
    )
    add(
        "credential_scope",
        "Authenticated reads use only the scoped PAPER_VENUE credential references.",
        evidence_key="read_lifecycle",
        rationale_pass="The reports contain only the two Binance PAPER_VENUE credential reference names and no values.",
        rationale_fail="Credential references are missing, unexpected, or inconsistent.",
    )
    add(
        "provider_truth_btc_eth",
        "The live provider catalogue admits both BTCUSDT and ETHUSDT.",
        evidence_key="public_read_lifecycle",
        rationale_pass="Provider product truth and the authenticated smoke both record BTCUSDT and ETHUSDT.",
        rationale_fail="The required BTC/ETH provider mappings are incomplete or not live-measured.",
    )
    add(
        "provider_filters",
        "Provider filters and minimum practical order constraints are bound before submission.",
        evidence_key="public_read_lifecycle",
        rationale_pass="Product schema/filter fields and the lifecycle target’s quantity, price, notional, and status checks are present.",
        rationale_fail="Provider filters are not sufficiently bound to the lifecycle target.",
    )
    add(
        "authenticated_account_reads",
        "Server time, account state, balances, positions, open orders, and fills reads pass.",
        evidence_key="read_lifecycle",
        rationale_pass="All eight read-only smoke operations returned ok, including authenticated fills reads.",
        rationale_fail="One or more required read-only operations did not pass.",
    )
    add(
        "deterministic_signing_and_scope",
        "The provider-specific signer and one-write lifecycle are measured without automatic signed-write retry.",
        source="Binance runbook; Binance adapter and lifecycle evidence",
        evidence_key="controls_lifecycle",
        rationale_pass="The provider-specific adapter identity is bound and the lifecycle records exactly one submission and one cancellation.",
        rationale_fail="Signing/write-count or the provider-specific boundary is not proven.",
    )
    add(
        "no_transfer_withdrawal_margin_futures",
        "No transfer, withdrawal, margin, futures, or alternate account authority is used.",
        evidence_key="controls_lifecycle",
        rationale_pass="Forbidden path controls are present and no such endpoint appears in the measured lifecycle.",
        rationale_fail="A forbidden endpoint or missing path restriction was observed.",
    )
    add(
        "risk_kernel_approval",
        "The deterministic RiskKernel approves the target before transport submission.",
        evidence_key="lifecycle",
        rationale_pass="The immutable lifecycle records an approved order-level risk decision.",
        rationale_fail="RiskKernel approval is missing or not approved.",
    )
    add(
        "target_state_binding",
        "The target is provider-filter-valid and bound to the admitted BTCUSDT instrument.",
        evidence_key="lifecycle",
        rationale_pass="The target and order artifact record the provider instrument and all filter validations.",
        rationale_fail="Target identity or filter validation is incomplete.",
    )
    add(
        "oms_intent_before_submission",
        "Intent is persisted and OMS state is authoritative before network submission.",
        evidence_key="lifecycle",
        rationale_pass="The lifecycle records intent persistence and OMS risk-approved state before network activity.",
        rationale_fail="The pre-submit intent/OMS boundary is not proven.",
    )
    add(
        "deterministic_client_identity",
        "The write uses a deterministic client order identity.",
        evidence_key="lifecycle",
        rationale_pass="The lifecycle records deterministic client identity and its digest without exposing the value.",
        rationale_fail="Deterministic client identity evidence is missing.",
    )
    add(
        "signed_write_ack_handling",
        "The venue acknowledgement is handled once and ambiguous acknowledgement is reconciled before any retry.",
        evidence_key="lifecycle",
        rationale_pass="The venue acknowledged the order; the ambiguous-ack drill records no automatic retry and one submit call.",
        rationale_fail="Acknowledgement or no-retry behavior is not proven.",
    )
    add(
        "venue_query_reconciliation",
        "Authoritative venue query reconciles the OMS order with zero discrepancies.",
        evidence_key="lifecycle",
        rationale_pass="Post-submit venue query and reconciliation both passed with zero discrepancies.",
        rationale_fail="Authoritative query/reconciliation is incomplete or divergent.",
    )
    add(
        "cancellation_terminal_state",
        "The no-fill order is cancelled and reaches a reconciled terminal state.",
        evidence_key="lifecycle",
        rationale_pass="One cancellation was acknowledged, no open order remained, and OMS ended reconciled.",
        rationale_fail="Cancellation or terminal reconciliation is incomplete.",
    )
    add(
        "restart_hydration",
        "Restart hydration preserves terminal state without duplicate submission.",
        evidence_key="lifecycle",
        rationale_pass="Restart hydration passed with no duplicate submission and zero post-restart signed submissions.",
        rationale_fail="Restart recovery or duplicate prevention did not pass.",
    )
    add(
        "tca_attribution",
        "TCA and attribution complete with no unexplained residual for the observed no-fill path.",
        evidence_key="lifecycle",
        rationale_pass="TCA and attribution passed with an unexplained residual of zero.",
        rationale_fail="TCA/attribution is missing or has an unexplained residual.",
    )
    add(
        "failure_drills",
        "Duplicate, ambiguous acknowledgement, cancel race, divergence, interruption, and kill-switch drills fail safely.",
        evidence_key="lifecycle",
        rationale_pass="All recorded Phase-2 failure drills passed and preserved their no-retry/fail-closed outcomes.",
        rationale_fail="One or more required failure drills did not pass.",
    )
    add(
        "no_production_execution",
        "No production endpoint or production execution call was used.",
        evidence_key="controls_lifecycle",
        rationale_pass="The lifecycle used only the reviewed testnet endpoint and one signed fake-funds submission.",
        rationale_fail="Production endpoint contamination or unexpected writes were detected.",
    )
    add(
        "phase1_local_exit_context",
        "The Phase-1 deterministic foundation exit evidence is present as the predecessor context.",
        source="phase-01-safety-data-resources.md; local recovery runbook",
        evidence_key="phase1",
        rationale_pass="The immutable local Phase-1 drill passed rollback and deterministic Bronze rebuild.",
        rationale_fail="The local Phase-1 exit evidence is missing or failed.",
    )
    rows.append(
        _requirement(
            "phase2_real_fill",
            "phase-02-paper-core.md; real-api-paper-transition.md Workstream D",
            "A real filled order is required to pass the Phase-2 venue qualification.",
            ChecklistStatus.NOT_APPLICABLE,
            gating=False,
            rationale="Phase 2 qualifies the safe no-fill/cancel and failure lifecycle; real fill ingestion, TCA, and external attribution are Phase-6 evidence in the current plans.",
            next_admissible_action="Collect a real fill only from a legitimate later paper decision; do not churn orders to manufacture one.",
            evidence=evidence["lifecycle"],
        )
    )
    rows.append(
        _requirement(
            "phase1_provider_deployment_rollback",
            "phase-01-safety-data-resources.md; Binance recovery runbook",
            "Full provider deployment/open-order rollback and archive restore are required to pass Phase 2.",
            ChecklistStatus.EXTERNALLY_BLOCKED,
            gating=False,
            rationale="The provider recovery report explicitly measures read-only restart/configuration recovery only; full deployment rollback and archive restore are separate later evidence.",
            next_admissible_action="Keep the partial provider recovery evidence separate and qualify the later recovery gate when it becomes an actual predecessor.",
            evidence=evidence["phase1_recovery"]
            if "phase1_recovery" in evidence
            else evidence["phase1"],
        )
    )
    return tuple(rows)


def evaluate(
    *,
    public_truth_path: Path = PUBLIC_TRUTH,
    read_only_path: Path = READ_ONLY,
    lifecycle_path: Path = LIFECYCLE,
    phase1_local_path: Path = LOCAL_PHASE1,
    evaluated_at: datetime | None = None,
) -> tuple[Phase2AdmissionChecklist, PhaseGateRecord]:
    evaluated_at = (evaluated_at or datetime.now(UTC)).astimezone(UTC)
    public = _load(public_truth_path)
    read_only = _load(read_only_path)
    lifecycle = _load(lifecycle_path)
    checks = _phase2_checks(
        read_only,
        lifecycle,
        read_only_path=read_only_path,
        phase1_local_path=phase1_local_path,
    )
    evidence = {
        "public": _evidence(public_truth_path),
        "read_only": _evidence(read_only_path),
        "lifecycle": _evidence(lifecycle_path),
        "read_lifecycle": _evidence(read_only_path, lifecycle_path),
        "public_read_lifecycle": _evidence(public_truth_path, read_only_path, lifecycle_path),
        "controls": _evidence(ADAPTER, ADAPTER_TEST, LIFECYCLE_TEST),
        "controls_lifecycle": _evidence(ADAPTER, ADAPTER_TEST, LIFECYCLE_TEST, lifecycle_path),
        "phase1": _evidence(phase1_local_path),
        "phase1_recovery": _evidence(PHASE1_RECOVERY),
    }
    # Force validation of the public report and keep it part of the formal manifest.
    public_result = _result(public)
    checks["provider_truth_btc_eth"] = checks["provider_truth_btc_eth"] and (
        public_result.get("status") == "passed"
        and set(public_result.get("required_symbols", ())) == {"BTCUSDT", "ETHUSDT"}
    )
    requirements = _build_requirements(checks, evidence)
    mandatory = tuple(item.requirement_id for item in requirements if item.gating)
    blockers = tuple(
        item.requirement_id
        for item in requirements
        if item.gating
        and item.status in {ChecklistStatus.UNSATISFIED, ChecklistStatus.EXTERNALLY_BLOCKED}
    )
    decision = GateDecision.PASSED if not blockers else GateDecision.PENDING
    gate_evidence = tuple(
        GateEvidence(
            name=item.requirement_id,
            kind=GateEvidenceKind.OPERATIONAL,
            passed=item.status is ChecklistStatus.SATISFIED,
            artifact_hash=item.evidence[0].sha256
            if item.status is ChecklistStatus.SATISFIED and item.evidence
            else None,
            source=item.evidence[0].path if item.evidence else "authoritative plan",
            verified_by="phase2-formal-admission-evaluator-v1",
            observed_at=evaluated_at,
            details=item.rationale,
        )
        for item in requirements
    )
    record = PhaseGateRecord(
        phase=2,
        name="Phase 2 — Deterministic paper-trading core",
        decision=decision,
        required_evidence=tuple(
            item.requirement_id
            for item in requirements
            if item.gating and item.status is ChecklistStatus.SATISFIED
        ),
        evidence=gate_evidence,
        prerequisite_phase=1,
        recorded_by="phase2-formal-admission-evaluator-v1",
        recorded_at=evaluated_at,
        reasons=blockers,
    )
    record_bytes = (
        json.dumps(record.model_dump(mode="json", round_trip=True), sort_keys=True, indent=2) + "\n"
    ).encode()
    checklist = Phase2AdmissionChecklist(
        gate_version=GATE_VERSION,
        evaluated_at=evaluated_at,
        repository_commit=_git_head(),
        evaluator_code_sha256=_sha256(Path(__file__)),
        decision=decision,
        mandatory_requirements=mandatory,
        blocking_requirement_ids=blockers,
        requirements=requirements,
        venue=VenueAssessment(
            venue="binance_spot_testnet",
            environment="paper_testnet",
            rest_endpoint="https://testnet.binance.vision",
            reviewed_host="testnet.binance.vision",
            symbols=("BTCUSDT", "ETHUSDT"),
            real_fill_observed=False,
            phase6_fill_scope="real fill ingestion/TCA/attribution remains a Phase-6 requirement",
            evidence=evidence["public_read_lifecycle"],
        ),
        evidence_manifest=(
            evidence["public"]
            + evidence["read_only"]
            + evidence["lifecycle"]
            + evidence["phase1"]
            + evidence["phase1_recovery"]
            + evidence["controls"]
        ),
        phase_gate_record_path="phase2-gate-record.json",
        phase_gate_record_sha256=hashlib.sha256(record_bytes).hexdigest(),
        phase_gate_record_canonical_hash=record.canonical_hash(),
        notes=(
            "Existing Binance evidence was evaluated without a network call or additional order.",
            "The no-fill/cancel lifecycle is Phase-2 evidence; real fill attribution remains Phase 6.",
            "The record preserves prerequisite_phase=1 and does not claim global Phase-0 admission.",
            "Provider-specific read-only recovery and archive restore remain separate non-gating evidence here.",
            "LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.",
        ),
    )
    return checklist, record


def validate_phase2_record(path: Path, *, at: datetime | None = None) -> PhaseGateRecord:
    """Validate a currently usable passed Phase-2 record without side effects."""

    payload = _load(path)
    try:
        record = PhaseGateRecord.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise Phase2EvidenceRefused("Phase-2 gate record validation failed") from exc
    evaluated_at = (at or datetime.now(UTC)).astimezone(UTC)
    if record.phase != 2:
        raise Phase2EvidenceRefused("a Phase-2 gate record must have phase=2")
    if record.decision is not GateDecision.PASSED:
        raise Phase2EvidenceRefused("a passed Phase-2 gate record is required")
    if not record.is_valid_at(evaluated_at):
        raise Phase2EvidenceRefused("the Phase-2 gate record is not valid at evaluation time")
    return record


def _write_immutable(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()


def write_evidence(
    output_root: Path, checklist: Phase2AdmissionChecklist, record: PhaseGateRecord
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
            "phase_gate_record_path": "phase2-gate-record.json",
            "phase_gate_record_sha256": hashlib.sha256(record_bytes).hexdigest(),
        }
    )
    checklist_bytes = (
        json.dumps(checklist.model_dump(mode="json", by_alias=True), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    record_path = output_root / "phase2-gate-record.json"
    checklist_path = output_root / "phase2-admission-checklist.json"
    _write_immutable(record_path, record_bytes)
    _write_immutable(checklist_path, checklist_bytes)
    manifest = {
        "schema": "advisorai.phase2.formal-admission-checklist.v1.manifest",
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
    parser.add_argument("--public-truth", type=Path, default=PUBLIC_TRUTH)
    parser.add_argument("--read-only-evidence", type=Path, default=READ_ONLY)
    parser.add_argument("--lifecycle-evidence", type=Path, default=LIFECYCLE)
    parser.add_argument("--phase1-local-evidence", type=Path, default=LOCAL_PHASE1)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    checklist, record = evaluate(
        public_truth_path=args.public_truth.resolve(),
        read_only_path=args.read_only_evidence.resolve(),
        lifecycle_path=args.lifecycle_evidence.resolve(),
        phase1_local_path=args.phase1_local_evidence.resolve(),
    )
    print(json.dumps(write_evidence(args.output_root, checklist, record), sort_keys=True))
