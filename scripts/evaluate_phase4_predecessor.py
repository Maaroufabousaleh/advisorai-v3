#!/usr/bin/env python3
"""Resolve the Phase-4 predecessor contract without opening Phase-4 admission.

The Phase-4 plan requires an admitted Phase-3 data plane and qualified model
roles.  This evaluator makes that dependency explicit and keeps the unrelated
global Phase-0 route/archive state separate.  It is offline: it loads only
typed records/configuration, never secrets, weights, network clients, or order
authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from advisorai.gates import GateDecision, PhaseGateRecord
from advisorai.phase0 import load_local_model_roster

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "advisorai.phase4.predecessor-dependency.v1"
GATE_VERSION = "phase4-predecessor-contract-v1"
PHASE3_GATE = REPOSITORY_ROOT / (
    "artifacts/phase3/formal-admission/20260812T013505Z-with-passed-phase2-post-phase2-commit/"
    "phase3-gate-record.json"
)
LOCAL_ROSTER = REPOSITORY_ROOT / "configs/models/phase0_local_roster.json"
PHASE4_PLAN = REPOSITORY_ROOT / "docs/plans/phase-04-quant-baselines.md"
TRACEABILITY = REPOSITORY_ROOT / "docs/plans/traceability.md"
PHASE4_RUNNER = REPOSITORY_ROOT / "scripts/run_phase4_paper_utility.py"
PHASE4_CONTRACT = REPOSITORY_ROOT / "src/advisorai/phase4/paper_utility.py"


class DependencyStatus(StrEnum):
    SATISFIED = "SATISFIED"
    EXTERNALLY_BLOCKED = "EXTERNALLY_BLOCKED"
    UNSATISFIED = "UNSATISFIED"


class DependencyCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str = Field(min_length=1)
    requirement: str = Field(min_length=1)
    status: DependencyStatus
    gating: bool
    rationale: str = Field(min_length=1)
    evidence: tuple[str, ...] = ()
    evidence_sha256: tuple[str, ...] = ()

    @field_validator("evidence_sha256")
    @classmethod
    def require_hashes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError("dependency evidence must use SHA-256 digests")
        return values


class Phase4DependencyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_name: str = Field(default=SCHEMA, alias="schema")
    gate_version: str = Field(min_length=1)
    evaluated_at: datetime
    repository_commit: str = Field(min_length=40, max_length=40)
    decision: str
    phase4_admission_opened: bool = False
    measurement_allowed: bool
    phase4_predecessors: tuple[str, ...]
    global_phase0_status: str
    checks: tuple[DependencyCheck, ...] = Field(min_length=1)
    phase3_gate_path: str
    phase3_gate_sha256: str
    local_roster_path: str
    local_roster_sha256: str
    notes: tuple[str, ...] = ()

    @field_validator("evaluated_at")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("dependency evaluation time must include a timezone")
        return value.astimezone(UTC)

    @field_validator("repository_commit")
    @classmethod
    def require_commit(cls, value: str) -> str:
        value = value.strip().lower()
        if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("repository_commit must be a Git SHA-1")
        return value

    @field_validator("phase3_gate_sha256", "local_roster_sha256")
    @classmethod
    def require_digest(cls, value: str) -> str:
        value = value.strip().lower()
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("dependency identity must be a SHA-256 digest")
        return value


class Phase4DependencyRefused(ValueError):
    """Raised when the Phase-4 predecessor contract is not satisfied."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError as exc:
        raise Phase4DependencyRefused(
            f"dependency evidence path escapes repository: {path}"
        ) from exc


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().lower()


def _load_gate(path: Path, *, at: datetime) -> PhaseGateRecord:
    try:
        record = PhaseGateRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Phase4DependencyRefused("Phase-3 predecessor record is invalid") from exc
    if record.phase != 3 or record.decision is not GateDecision.PASSED:
        raise Phase4DependencyRefused("a passed Phase-3 predecessor record is required")
    if not record.is_valid_at(at):
        raise Phase4DependencyRefused(
            "the Phase-3 predecessor record is not valid at evaluation time"
        )
    return record


def _check(
    requirement_id: str,
    requirement: str,
    status: DependencyStatus,
    rationale: str,
    *paths: Path,
    gating: bool = True,
) -> DependencyCheck:
    references = tuple(_relative(path) for path in paths)
    return DependencyCheck(
        requirement_id=requirement_id,
        requirement=requirement,
        status=status,
        gating=gating,
        rationale=rationale,
        evidence=references,
        evidence_sha256=tuple(_sha256(path) for path in paths),
    )


def evaluate(
    *,
    phase3_gate_path: Path = PHASE3_GATE,
    local_roster_path: Path = LOCAL_ROSTER,
    evaluated_at: datetime | None = None,
) -> Phase4DependencyDecision:
    evaluated_at = (evaluated_at or datetime.now(UTC)).astimezone(UTC)
    phase3_gate_path = phase3_gate_path.resolve()
    local_roster_path = local_roster_path.resolve()
    _load_gate(phase3_gate_path, at=evaluated_at)
    try:
        roster = load_local_model_roster(local_roster_path)
    except (OSError, ValueError) as exc:
        raise Phase4DependencyRefused("the local model roster is invalid") from exc

    role_expectations = {
        "forecast_primary": "ttm-r2",
        "finance_sentiment_primary": "finsentiment-deberta-v3",
        "finance_sentiment_fast": "finbert-minilm",
    }
    role_checks = []
    for role, expected_candidate in role_expectations.items():
        entry = getattr(roster, role)
        passed = (
            entry.candidate == expected_candidate
            and entry.state.value == "qualified"
            and entry.stability.value == "passed"
        )
        role_checks.append(
            _check(
                f"qualified_model_role:{role}",
                f"The selected Phase-0 role {role} is qualified before Phase-4 measurement.",
                DependencyStatus.SATISFIED if passed else DependencyStatus.UNSATISFIED,
                (
                    f"Roster binds {entry.candidate} as qualified with passed stability."
                    if passed
                    else "Roster role, candidate, or stability is not independently qualified."
                ),
                local_roster_path,
            )
        )

    expected_baselines = {"naive", "drift", "seasonal-7", "linear", "lightgbm"}
    actual_baselines = {entry.candidate for entry in roster.mandatory_baselines}
    baselines_passed = actual_baselines == expected_baselines and all(
        entry.state.value == "qualified" for entry in roster.mandatory_baselines
    )
    checks = [
        _check(
            "phase3_formal_predecessor",
            "A currently valid passed Phase-3 PhaseGateRecord is present.",
            DependencyStatus.SATISFIED,
            "The preserved Phase-3 formal record validates as passed at evaluation time.",
            phase3_gate_path,
        ),
        *role_checks,
        _check(
            "mandatory_baseline_roster",
            "The mandatory baseline set remains complete before candidate utility measurement.",
            DependencyStatus.SATISFIED if baselines_passed else DependencyStatus.UNSATISFIED,
            (
                "The local roster contains qualified naive, drift, seasonal-7, linear, and LightGBM baselines."
                if baselines_passed
                else "The local roster does not contain the complete qualified baseline set."
            ),
            local_roster_path,
        ),
        _check(
            "phase4_contract_requires_phase3_only",
            "The executable Phase-4 measurement boundary requires Phase 3, not global Phase-0 admission.",
            DependencyStatus.SATISFIED,
            "The Phase-4 runner validates only a current passed Phase-3 record; the plan requires qualified roles and baseline comparison, while global Phase-0 route/archive evidence remains separate.",
            PHASE4_PLAN,
            TRACEABILITY,
            PHASE4_RUNNER,
            PHASE4_CONTRACT,
            gating=False,
        ),
        _check(
            "global_phase0_route_archive",
            "Global Phase-0 private-route and archive prerequisites are complete.",
            DependencyStatus.EXTERNALLY_BLOCKED,
            "Global Phase-0 remains pending separate private-route/archive evidence; this is not a Phase-4 predecessor under the executable contract and is preserved without weakening that global gate.",
            gating=False,
        ),
    ]
    gating_blockers = tuple(
        item.requirement_id
        for item in checks
        if item.gating and item.status is not DependencyStatus.SATISFIED
    )
    return Phase4DependencyDecision(
        gate_version=GATE_VERSION,
        evaluated_at=evaluated_at,
        repository_commit=_git_head(),
        decision="OPEN_FOR_MEASUREMENT" if not gating_blockers else "BLOCKED",
        measurement_allowed=not gating_blockers,
        phase4_predecessors=("phase3_formal_admission", "selected_model_role_qualification"),
        global_phase0_status="PENDING_SEPARATE_PRIVATE_ROUTE_AND_ARCHIVE",
        checks=tuple(checks),
        phase3_gate_path=_relative(phase3_gate_path),
        phase3_gate_sha256=_sha256(phase3_gate_path),
        local_roster_path=_relative(local_roster_path),
        local_roster_sha256=_sha256(local_roster_path),
        notes=(
            "This decision opens measurement only; it does not create a Phase-4 PhaseGateRecord or promote a model.",
            "Finance sentiment roles remain context/evidence roles and are not coerced into price forecasts.",
            "TSPulse remains restricted to anomaly, integrity, representation, and regime features.",
            "LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.",
        ),
    )


def _write_immutable(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()


def write_evidence(output_root: Path, decision: Phase4DependencyDecision) -> dict[str, str]:
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError(f"dependency output root already exists: {output_root}")
    output_root.mkdir(parents=True)
    payload = (
        json.dumps(decision.model_dump(mode="json", by_alias=True), sort_keys=True, indent=2) + "\n"
    )
    encoded = payload.encode()
    decision_path = output_root / "phase4-predecessor-dependency.json"
    _write_immutable(decision_path, encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    manifest = {
        "schema": "advisorai.phase4.predecessor-dependency.v1.manifest",
        "decision": decision.decision,
        "measurement_allowed": decision.measurement_allowed,
        "dependency": decision_path.name,
        "dependency_sha256": digest,
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    _write_immutable(output_root / "evidence-manifest.json", manifest_bytes)
    return {
        "decision": decision.decision,
        "measurement_allowed": str(decision.measurement_allowed).lower(),
        "dependency": str(decision_path),
        "dependency_sha256": digest,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase3-gate-record", type=Path, default=PHASE3_GATE)
    parser.add_argument("--local-roster", type=Path, default=LOCAL_ROSTER)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    print(
        json.dumps(
            write_evidence(
                args.output_root,
                evaluate(
                    phase3_gate_path=args.phase3_gate_record,
                    local_roster_path=args.local_roster,
                ),
            ),
            sort_keys=True,
        )
    )
