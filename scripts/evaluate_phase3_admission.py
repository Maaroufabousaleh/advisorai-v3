#!/usr/bin/env python3
"""Evaluate Phase-3 operational evidence without opening the phase gate.

The durable public-data runner proves that evidence was collected safely.  This
offline evaluator applies the operational outcome checks separately so a
structurally valid, fail-closed run is not mistaken for an admitted source.
It never performs network I/O, changes the qualification root, or records a
formal ``PhaseGateRecord``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.collectors.public_market_data import reviewed_public_market_data_sources

if __package__ in {None, ""}:
    # Direct `python scripts/evaluate_phase3_admission.py` entrypoints do not
    # put the repository root on sys.path; keep the documented offline command
    # equivalent to importing the module through the package.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.validate_phase3_public_data_qualification import (
    CHAIN_LOGS,
    _load_chain,
    _validate_health_snapshot,
    _validate_resource_monitor,
)

SCHEMA = "advisorai.phase3.public-market-data-admission.v1"
REQUIRED_ASSETS = ("BTC", "ETH")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


class AdmissionCheck(BaseModel):
    """One deterministic operational-admission assertion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    passed: bool
    details: str = Field(min_length=1)
    blocker_code: str | None = None

    @field_validator("name", "details", "blocker_code")
    @classmethod
    def nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("admission check text cannot be blank")
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_blocker_for_failure(self) -> AdmissionCheck:
        if not self.passed and not self.blocker_code:
            raise ValueError("failed admission checks require a blocker code")
        if self.passed and self.blocker_code is not None:
            raise ValueError("passed admission checks cannot retain a blocker code")
        return self


class Phase3AdmissionReport(BaseModel):
    """Review recommendation; this model intentionally cannot represent admission."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_name: Literal["advisorai.phase3.public-market-data-admission.v1"] = Field(
        default=SCHEMA, alias="schema"
    )
    evaluated_at: datetime
    run_directory: str = Field(min_length=1)
    run_config_sha256: str = Field(min_length=64, max_length=64)
    run_summary_sha256: str = Field(min_length=64, max_length=64)
    resource_monitor_summary_sha256: str | None = None
    health_snapshot_sha256: str | None = None
    recommendation: Literal["PENDING_EXTERNAL_EVIDENCE", "QUALIFIED_FOR_REVIEW"]
    qualification_state: Literal["evidence_for_review_only"] = "evidence_for_review_only"
    phase3_admission: Literal[False] = False
    formal_gate_recorded: Literal[False] = False
    checks: tuple[AdmissionCheck, ...] = Field(min_length=1)
    blocker_codes: tuple[str, ...] = ()
    next_admissible_action: str = Field(min_length=1)
    counts: dict[str, int] = {}

    @field_validator("evaluated_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        return _aware(value, "evaluated_at")

    @field_validator("run_config_sha256", "run_summary_sha256", "resource_monitor_summary_sha256")
    @classmethod
    def sha256_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.lower().strip()
        if len(normalized) != 64 or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise ValueError("evidence references must be lowercase SHA-256 digests")
        return normalized

    @model_validator(mode="after")
    def validate_recommendation(self) -> Phase3AdmissionReport:
        actual_blockers = tuple(
            check.blocker_code for check in self.checks if not check.passed and check.blocker_code
        )
        if actual_blockers != self.blocker_codes:
            raise ValueError("blocker_codes must match failed checks in order")
        if self.recommendation == "QUALIFIED_FOR_REVIEW" and self.blocker_codes:
            raise ValueError("review-qualified recommendation cannot retain blockers")
        if self.recommendation == "PENDING_EXTERNAL_EVIDENCE" and not self.blocker_codes:
            raise ValueError("pending recommendation requires blockers")
        return self


def _check(
    name: str,
    passed: bool,
    details: str,
    blocker_code: str,
) -> AdmissionCheck:
    return AdmissionCheck(
        name=name,
        passed=passed,
        details=details,
        blocker_code=None if passed else blocker_code,
    )


def _records_by_source_symbol(
    samples: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    records: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in samples:
        source_id = row.get("source_id")
        symbol = row.get("symbol")
        if isinstance(source_id, str) and isinstance(symbol, str):
            records.setdefault((source_id, symbol), []).append(row)
    return records


def _sample_end(row: dict[str, Any]) -> datetime:
    value = row.get("cycle_ended_at")
    if not isinstance(value, str):
        raise ValueError("sample cycle_ended_at must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _aware(parsed, "sample cycle_ended_at")


def _primary_source_ids() -> set[str]:
    return {
        source.source_id
        for source in reviewed_public_market_data_sources()
        if source.role == "primary_candidate"
    }


def _evaluate_checks(
    *,
    config: dict[str, Any],
    status: dict[str, Any],
    summary: dict[str, Any],
    logs: dict[str, list[dict[str, Any]]],
    resource_monitor: dict[str, Any] | None,
    health_snapshot: dict[str, Any],
) -> tuple[AdmissionCheck, ...]:
    samples = logs["samples.jsonl"]
    selections = logs["source-selection.jsonl"]
    disagreements = logs["disagreement.jsonl"]
    records_by_pair = _records_by_source_symbol(samples)
    latest = {pair: max(rows, key=_sample_end) for pair, rows in records_by_pair.items()}
    primary_ids = _primary_source_ids()
    policy = config.get("source_health_policy", {})
    stale_after = policy.get("stale_after_seconds", 5.0)

    checks: list[AdmissionCheck] = []
    start_at = status.get("started_at")
    target_end_at = status.get("target_end_at")
    updated_at = status.get("updated_at")
    try:
        start_timestamp = _aware(
            datetime.fromisoformat(str(start_at).replace("Z", "+00:00")),
            "qualification started_at",
        )
        target_timestamp = _aware(
            datetime.fromisoformat(str(target_end_at).replace("Z", "+00:00")),
            "qualification target_end_at",
        )
        updated_timestamp = _aware(
            datetime.fromisoformat(str(updated_at).replace("Z", "+00:00")),
            "qualification updated_at",
        )
        requested_duration = float(config.get("duration_hours", 0.0)) * 3600.0
        measured_duration = (target_timestamp - start_timestamp).total_seconds()
        last_sample_timestamp = max((_sample_end(row) for row in samples), default=None)
        terminal_sample_count = summary.get("terminal_sample_count")
        terminal_marker_present = (
            isinstance(terminal_sample_count, int) and terminal_sample_count > 0
        )
        terminal_sample_reached = (
            measured_duration >= requested_duration
            and updated_timestamp >= target_timestamp
            and last_sample_timestamp is not None
            and last_sample_timestamp >= target_timestamp
            and terminal_marker_present
        )
    except (TypeError, ValueError):
        measured_duration = 0.0
        requested_duration = 0.0
        terminal_marker_present = False
        terminal_sample_reached = False
    complete = (
        status.get("state") == "multi_hour_window_complete"
        and summary.get("state") == "multi_hour_window_complete"
        and terminal_sample_reached
    )
    checks.append(
        _check(
            "multi_hour_window_complete",
            complete,
            "runner state="
            f"{status.get('state')!r}, summary state={summary.get('state')!r}, "
            f"measured_duration_seconds={measured_duration:.3f}, "
            f"requested_duration_seconds={requested_duration:.3f}, "
            f"terminal_marker_present={terminal_marker_present}, "
            f"terminal_sample_reached={terminal_sample_reached}",
            "qualification_window_incomplete",
        )
    )

    public_only = (
        config.get("credentials_loaded") is False
        and config.get("order_writes_attempted") is False
        and all(
            row.get("credentials_loaded") is False and row.get("order_writes_attempted") is False
            for row in samples
        )
    )
    checks.append(
        _check(
            "public_read_only_separation",
            public_only,
            "qualification config and every sample deny credentials and order writes",
            "public_read_only_invariant_failed",
        )
    )

    health_projection_ok = health_snapshot.get("state") == "validated" and not health_snapshot.get(
        "issues"
    )
    checks.append(
        _check(
            "dashboard_health_projection",
            health_projection_ok,
            "latest-health.json matches the latest append-only source samples"
            if health_projection_ok
            else "latest-health.json is missing, invalid, or does not match source samples",
            "health_snapshot_invalid",
        )
    )

    source_cards = {source.source_id: source for source in reviewed_public_market_data_sources()}
    identity_ok = all(
        isinstance(row.get("provider_identity"), str)
        and row.get("provider_identity") == row.get("source_id")
        and row.get("source_id") in source_cards
        and row.get("endpoint") == source_cards[row["source_id"]].ws_url
        for row in samples
    )
    checks.append(
        _check(
            "source_identity_and_endpoint_binding",
            identity_ok,
            "every sample binds provider identity and endpoint to a reviewed source card",
            "source_identity_or_endpoint_mismatch",
        )
    )

    selection_safe = all(
        row.get("silent_substitution") is not True
        and (
            row.get("fail_closed") is True
            or (
                row.get("selected_source_id") is not None
                and row.get("selected_source_id") == row.get("actual_source_identity")
                and row.get("selected_provider_identity") == row.get("actual_source_identity")
            )
        )
        for row in selections
    )
    checks.append(
        _check(
            "selection_identity_and_fail_closed_behavior",
            selection_safe and bool(selections),
            f"{sum(row.get('fail_closed') is True for row in selections)}/{len(selections)} selections explicitly fail closed when no eligible source exists",
            "source_selection_safety_invariant_failed",
        )
    )

    primary_pairs = {
        pair: rows
        for pair, rows in records_by_pair.items()
        if pair[0] in primary_ids and pair[1] in REQUIRED_ASSETS
    }
    healthy_candidates: list[str] = []
    for source_id in sorted(primary_ids):
        rows = [primary_pairs.get((source_id, asset), []) for asset in REQUIRED_ASSETS]
        if all(
            pair
            and latest[(source_id, asset)].get("health_state") == "HEALTHY"
            and latest[(source_id, asset)].get("source_contract_valid") is True
            and latest[(source_id, asset)].get("last_valid_event_age_seconds") is not None
            and float(latest[(source_id, asset)]["last_valid_event_age_seconds"])
            <= float(stale_after)
            for asset, pair in zip(REQUIRED_ASSETS, rows, strict=True)
        ):
            healthy_candidates.append(source_id)
    checks.append(
        _check(
            "primary_btc_eth_source_healthy_at_terminal_sample",
            bool(healthy_candidates),
            "healthy primary candidates=" + (", ".join(healthy_candidates) or "none"),
            "no_healthy_primary_source_for_btc_eth",
        )
    )

    continuity_failures: list[str] = []
    for source_id in healthy_candidates or sorted(primary_ids):
        for asset in REQUIRED_ASSETS:
            rows = primary_pairs.get((source_id, asset), [])
            for row in rows:
                if not row.get("replay_equivalent"):
                    continuity_failures.append(f"{source_id}:{asset}:replay")
                if row.get("sequence_gap_count", 0) or row.get("duplicate_count", 0):
                    continuity_failures.append(f"{source_id}:{asset}:sequence")
                if row.get("out_of_order_count", 0):
                    continuity_failures.append(f"{source_id}:{asset}:ordering")
                if int(row.get("valid_event_count", 0)) == 0:
                    continuity_failures.append(f"{source_id}:{asset}:no_events")
    checks.append(
        _check(
            "primary_snapshot_sequence_replay_continuity",
            not continuity_failures and bool(primary_pairs),
            "continuity findings=" + (", ".join(continuity_failures[:8]) or "none"),
            "primary_snapshot_sequence_or_replay_failure",
        )
    )

    # Staleness is a source-health outcome, not sequence/replay corruption.
    # A stale source must be removed from the decision path, either by an
    # explicit fail-closed selection or by an identity-bound, quality-
    # recomputed failover to another reviewed source.  Keeping this assertion
    # separate prevents an ordinary provider outage from being misreported as
    # a broken raw-spool/replay boundary while preserving the fail-closed
    # safety invariant.
    selections_by_cycle_asset: dict[tuple[int, str], dict[str, Any]] = {}
    stale_selection_findings: list[str] = []
    for selection in selections:
        cycle = selection.get("cycle")
        asset = selection.get("asset")
        if isinstance(cycle, int) and isinstance(asset, str):
            key = (cycle, asset)
            if key in selections_by_cycle_asset:
                stale_selection_findings.append(f"selection_duplicate:{cycle}:{asset}")
            else:
                selections_by_cycle_asset[key] = selection

    stale_interval_count = 0
    for source_id, asset in sorted(primary_pairs):
        for row in primary_pairs[(source_id, asset)]:
            if not row.get("stale_interval_count"):
                continue
            stale_interval_count += int(row.get("stale_interval_count", 0))
            cycle = row.get("cycle")
            selection = (
                selections_by_cycle_asset.get((cycle, asset)) if isinstance(cycle, int) else None
            )
            if selection is None:
                stale_selection_findings.append(f"{source_id}:{asset}:selection_missing")
                continue
            # A reviewed source can be stale while another source is the
            # explicitly selected provider.  Only a stale source that was the
            # prior selected identity must be removed from the decision path;
            # an unselected quarantined source is not a failover violation.
            if selection.get("previous_source_id") != source_id:
                continue
            if selection.get("fail_closed") is True:
                if any(
                    selection.get(field) is not None
                    for field in (
                        "selected_source_id",
                        "selected_provider_identity",
                        "actual_source_identity",
                    )
                ):
                    stale_selection_findings.append(f"{source_id}:{asset}:fail_closed_identity")
                continue
            selected = selection.get("selected_source_id")
            actual = selection.get("actual_source_identity")
            if (
                not isinstance(selected, str)
                or selected == source_id
                or selected != actual
                or selection.get("selected_provider_identity") != actual
                or selection.get("quality_recomputed") is not True
                or selection.get("silent_substitution") is True
            ):
                stale_selection_findings.append(f"{source_id}:{asset}:failover_not_recomputed")
    checks.append(
        _check(
            "primary_stale_intervals_fail_closed",
            not stale_selection_findings,
            f"stale intervals observed={stale_interval_count}, "
            f"fail-closed/failover violations={len(stale_selection_findings)}",
            "stale_source_selection_not_fail_closed",
        )
    )

    unsafe_disagreements = sum(
        str(row.get("state", "")).upper() in {"SEVERE", "DEGRADED"}
        and row.get("fail_closed") is not True
        for row in disagreements
    )
    disagreement_safe = unsafe_disagreements == 0
    checks.append(
        _check(
            "disagreement_policy_is_fail_closed",
            disagreement_safe and bool(disagreements),
            f"disagreement observations={len(disagreements)}, "
            f"unsafe severe/degraded observations={unsafe_disagreements}",
            "disagreement_policy_not_fail_closed",
        )
    )

    resource_ok = (
        resource_monitor is not None
        and resource_monitor.get("state") == "deadline_reached"
        and int(resource_monitor.get("sample_count", 0)) > 0
        and not resource_monitor.get("resource_errors")
    )
    checks.append(
        _check(
            "resource_sidecar_without_errors",
            resource_ok,
            "resource sidecar supplied with no recorded resource errors"
            if resource_ok
            else "a completed, error-free OS resource sidecar is required",
            "resource_sidecar_missing_or_failed",
        )
    )
    return tuple(checks)


def evaluate(
    run_directory: Path,
    *,
    resource_monitor: Path | None = None,
) -> Phase3AdmissionReport:
    run_directory = run_directory.resolve()
    config_path = run_directory / "config.json"
    status_path = run_directory / "status.json"
    summary_path = run_directory / "summary.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    status = json.loads(status_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    logs = {name: _load_chain(run_directory / name)[0] for name in CHAIN_LOGS}
    resource_summary = (
        _validate_resource_monitor(resource_monitor.resolve())
        if resource_monitor is not None
        else None
    )
    health_snapshot = _validate_health_snapshot(
        run_directory / "latest-health.json",
        logs["samples.jsonl"],
        expected_run_id=str(config.get("run_id", run_directory.name)),
    )
    checks = _evaluate_checks(
        config=config,
        status=status,
        summary=summary,
        logs=logs,
        resource_monitor=resource_summary,
        health_snapshot=health_snapshot,
    )
    blockers = tuple(
        check.blocker_code for check in checks if not check.passed and check.blocker_code
    )
    recommendation = "QUALIFIED_FOR_REVIEW" if not blockers else "PENDING_EXTERNAL_EVIDENCE"
    next_action = (
        "Create a supervised Phase-3 gate record only after reviewing the complete evidence and prerequisites."
        if not blockers
        else "Preserve this root; obtain a fresh provider-available window that resolves the named blockers without relaxing policy."
    )
    return Phase3AdmissionReport(
        evaluated_at=datetime.now(UTC),
        run_directory=str(run_directory),
        run_config_sha256=_sha256(config_path.read_bytes()),
        run_summary_sha256=_sha256(summary_path.read_bytes()),
        resource_monitor_summary_sha256=(
            resource_summary.get("summary_sha256") if resource_summary is not None else None
        ),
        health_snapshot_sha256=health_snapshot.get("sha256"),
        recommendation=recommendation,
        checks=checks,
        blocker_codes=blockers,
        next_admissible_action=next_action,
        counts={
            "sample_count": len(logs["samples.jsonl"]),
            "selection_count": len(logs["source-selection.jsonl"]),
            "disagreement_count": len(logs["disagreement.jsonl"]),
            "health_transition_count": len(logs["health-transitions.jsonl"]),
            "primary_source_count": len(_primary_source_ids()),
        },
    )


def _write_report(output_root: Path, report: Phase3AdmissionReport) -> tuple[Path, str]:
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    payload = (
        json.dumps(report.model_dump(mode="json", by_alias=True), sort_keys=True, indent=2) + "\n"
    ).encode()
    path = output_root / "phase3-admission-evaluation.json"
    path.write_bytes(payload)
    digest = _sha256(payload)
    (output_root / "phase3-admission-evaluation.sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii"
    )
    return path, digest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--resource-monitor", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    report = evaluate(args.run_directory, resource_monitor=args.resource_monitor)
    path, digest = _write_report(args.output_root, report)
    print(
        json.dumps(
            {
                "recommendation": report.recommendation,
                "blocker_codes": report.blocker_codes,
                "report": str(path),
                "sha256": digest,
                "phase3_admission": report.phase3_admission,
            },
            sort_keys=True,
        )
    )
