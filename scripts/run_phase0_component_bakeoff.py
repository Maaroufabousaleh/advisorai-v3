#!/usr/bin/env python3
"""Run bounded, credential-free Phase-0 component evidence.

This drill measures the repository's local component boundaries without
starting a provider, scheduler service, browser, archive remote, or venue. It
records installed dependency availability separately from deterministic
fixture probes. A passing local probe is evidence for review; it is never a
Phase-0 admission or a paper/live-capital authorization.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from advisorai.archive import RcloneCryptBackend
from advisorai.capabilities import HermesIsolationRunner, HermesSandboxPolicy
from advisorai.execution import MarketEvent, NautilusRuntimeError, NautilusTraderPipeline
from advisorai.expansion.archive import ArchiveAutomation
from advisorai.gates import (
    GateDecision,
    GateEvidence,
    GateEvidenceKind,
    PhaseGateRecord,
    PhaseGateRegistry,
)
from advisorai.lake import DataLake
from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.orchestration import HamiltonRuntime, PrefectRuntime, PydanticRuntime
from advisorai.phase0.bakeoffs import (
    BakeoffResult,
    ComponentKind,
    benchmark_callable,
    default_candidates,
    run_availability_inventory,
)

SCHEMA = "advisorai.phase0.component-bakeoff-evidence.v1"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_immutable_json(path: Path, payload: object) -> Path:
    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise FileExistsError(f"immutable evidence differs: {path}")
        return path
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return path


def _write_latest_pointer(path: Path, payload: object) -> Path:
    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return path


def _fixture_gate() -> PhaseGateRegistry:
    """Return an in-memory synthetic gate used only to exercise adapter seams."""

    observed_at = datetime(2026, 8, 8, 0, 0, tzinfo=UTC)
    registry = PhaseGateRegistry()
    registry.record(
        PhaseGateRecord(
            phase=0,
            name="phase-0-component-fixture",
            decision=GateDecision.PASSED,
            required_evidence=("fixture",),
            evidence=(
                GateEvidence(
                    name="fixture",
                    kind=GateEvidenceKind.EXTERNAL_TIMED,
                    passed=True,
                    artifact_hash="a" * 64,
                    source="local-component-bakeoff-fixture",
                    verified_by="local-test-harness",
                    observed_at=observed_at,
                ),
            ),
            recorded_by="local-test-harness",
            recorded_at=observed_at,
        )
    )
    return registry


def _availability_payload(items: tuple[Any, ...]) -> dict[str, dict[str, Any]]:
    return {item.candidate.name: item.model_dump(mode="json") for item in items}


def _measure(
    *,
    name: str,
    kind: ComponentKind,
    version: str,
    route: str,
    runner,
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def capture() -> object:
        output = runner()
        captured["output"] = output
        return output

    result: BakeoffResult = benchmark_callable(
        candidate_name=name,
        kind=kind,
        runner=capture,
        version=version,
        route_identity=route,
    )
    return {
        "status": result.status,
        "passed": result.status == "measured",
        "version": result.version,
        "route_identity": result.route_identity,
        "privacy_passed": result.privacy_passed,
        "failure_handling_passed": result.failure_handling_passed,
        "benchmark_hash": result.benchmark_hash,
        "resource_samples": [sample.model_dump(mode="json") for sample in result.resource_samples],
        "notes": list(result.notes),
        "probe_output": captured.get("output"),
    }


def _quarantine(
    *,
    name: str,
    availability: dict[str, Any] | None,
    reason: str,
    required_for_core: bool = False,
) -> dict[str, Any]:
    return {
        "status": "quarantined",
        "passed": False,
        "required_for_core": required_for_core,
        "availability_status": availability.get("status") if availability else None,
        "version": availability.get("version") if availability else None,
        "reason": reason,
        "production_admitted": False,
    }


def _import_probe(import_name: str) -> dict[str, str]:
    module = importlib.import_module(import_name)
    return {"import_name": import_name, "module": module.__name__}


def _nautilus_output() -> dict[str, Any]:
    timestamp = datetime(2026, 8, 8, 0, 0, tzinfo=UTC)
    events = tuple(
        MarketEvent.from_raw(
            event_type="trade",
            instrument_id="BTCUSDT",
            occurred_at=timestamp,
            sequence=sequence,
            raw_payload=f"trade-{sequence}".encode(),
            price=100 + sequence,
            quantity=1,
        )
        for sequence in (1, 2)
    )
    seen: list[MarketEvent] = []
    test_double = NautilusTraderPipeline(test_double=True)
    replay_count = test_double.replay(events, seen.append)

    guard_rejected = False
    try:
        NautilusTraderPipeline(phase0_admitted=True)
    except NautilusRuntimeError:
        guard_rejected = True

    fixture_seen: list[MarketEvent] = []

    def pinned_fixture_runner(items, handler) -> int:
        count = 0
        for item in items:
            handler(item)
            count += 1
        return count

    admitted_fixture = NautilusTraderPipeline(
        phase0_admitted=True,
        gate_registry=_fixture_gate(),
        replay_runner=pinned_fixture_runner,
    )
    fixture_count = admitted_fixture.replay(events, fixture_seen.append)
    return {
        "replay_count": replay_count,
        "sequences": [event.sequence for event in seen],
        "event_hashes": [_sha256(event.model_dump_json().encode()) for event in seen],
        "admission_guard_rejected_without_gate": guard_rejected,
        "fixture_gate_injection_count": fixture_count,
        "fixture_gate_seen_sequences": [event.sequence for event in fixture_seen],
        "production_admitted": False,
    }


def _runtime_output(name: str) -> dict[str, Any]:
    registry = _fixture_gate()
    if name == "pydantic-ai":
        runtime = PydanticRuntime(phase0_admitted=True, gate_registry=registry)
        return {
            **_import_probe("pydantic_ai"),
            "typed_result": runtime.run_typed_agent(
                lambda payload: {"accepted": payload["value"]}, {"value": "fixture"}
            ),
            "fixture_gate_only": True,
            "production_admitted": False,
        }
    if name == "prefect":
        runtime = PrefectRuntime(phase0_admitted=True, gate_registry=registry)
        return {
            **_import_probe("prefect"),
            "flow_result": runtime.run_flow(lambda: "flow-fixture-ok"),
            "fixture_gate_only": True,
            "production_admitted": False,
        }
    if name == "hamilton":
        runtime = HamiltonRuntime(phase0_admitted=True, gate_registry=registry)
        return {
            **_import_probe("hamilton"),
            "feature_result": runtime.compute_features(
                lambda payload: {"feature": payload["value"]}, {"value": 7}
            ),
            "fixture_gate_only": True,
            "production_admitted": False,
        }
    raise ValueError(f"unsupported runtime component: {name}")


def _parquet_output(root: Path) -> dict[str, Any]:
    timestamp = datetime(2026, 8, 8, 0, 0, tzinfo=UTC)
    payload = b'{"event":"trade","price":"100.00","symbol":"BTCUSDT"}'
    source = DataLake(root / "source")
    rebuilt = DataLake(root / "rebuilt")
    kwargs = {
        "dataset": "native_market_messages",
        "payload": payload,
        "source_family": "native_venue",
        "origin": "approved-venue-fixture",
        "first_available_at": timestamp,
        "ingested_at": timestamp,
        "parser_version": "native-v1",
    }
    original = source.write_bronze(**kwargs)
    copy = rebuilt.write_bronze(**kwargs)
    original_rows = source.read_rows(original)
    copy_rows = rebuilt.read_rows(copy)
    original_manifest = (root / "source" / original.manifest_uri).read_bytes()
    copy_manifest = (root / "rebuilt" / copy.manifest_uri).read_bytes()
    original_parquet = (root / "source" / original.uri).read_bytes()
    copy_parquet = (root / "rebuilt" / copy.uri).read_bytes()

    import duckdb

    with duckdb.connect(":memory:") as connection:
        duckdb_rows = int(
            connection.execute(
                "SELECT count(*) FROM read_parquet(?)", [str(root / "source" / original.uri)]
            ).fetchone()[0]
        )
    return {
        "artifact_id": str(original.artifact_id),
        "content_hash": original.content_hash,
        "manifest_sha256": _sha256(original_manifest),
        "parquet_sha256": _sha256(original_parquet),
        "manifest_bytes_equal": original_manifest == copy_manifest,
        "parquet_bytes_equal": original_parquet == copy_parquet,
        "rows_equal": original_rows == copy_rows,
        "duckdb_row_count": duckdb_rows,
        "production_admitted": False,
    }


def _hermes_coordinator_task() -> dict[str, Any]:
    return {
        "task": "coordinator",
        "subagent": "collector-fixture",
        "allowed_actions": ["read_snapshot", "emit_report"],
        "network_calls": 0,
        "credentials_seen": 0,
    }


def _hermes_subagent_task() -> dict[str, Any]:
    return {
        "task": "subagent",
        "artifact": "deterministic-fixture",
        "allowed_actions": ["read_snapshot", "emit_report"],
        "network_calls": 0,
        "credentials_seen": 0,
    }


def _hermes_output() -> tuple[dict[str, Any], dict[str, Any]]:
    policy = HermesSandboxPolicy(
        mode="builder",
        allowed_network_hosts=(),
        allowed_secrets=(),
        cpu_seconds=2,
        memory_mib=512,
        wall_time_seconds=2,
    )
    runner = HermesIsolationRunner(policy)
    coordinator = runner.run(task_name="phase0-coordinator-fixture", task=_hermes_coordinator_task)
    subagent = runner.run(task_name="phase0-subagent-fixture", task=_hermes_subagent_task)

    def summary(result) -> dict[str, Any]:
        return {
            "passed": result.passed,
            "timed_out": result.timed_out,
            "policy_hash": result.policy_hash,
            "output_hash": result.output_hash,
            "elapsed_ms": result.elapsed_ms,
            "cpu_seconds": str(result.cpu_seconds),
            "peak_memory_mib": result.peak_memory_mib,
            "error": result.error,
        }

    stable = {
        "coordinator": {
            "passed": coordinator.passed,
            "timed_out": coordinator.timed_out,
            "policy_hash": coordinator.policy_hash,
            "output_hash": coordinator.output_hash,
            "error": coordinator.error,
        },
        "subagent": {
            "passed": subagent.passed,
            "timed_out": subagent.timed_out,
            "policy_hash": subagent.policy_hash,
            "output_hash": subagent.output_hash,
            "error": subagent.error,
        },
        "write_authority": False,
        "live_capital_authority": False,
    }
    measurements = {
        "policy": policy.model_dump(mode="json"),
        "coordinator": summary(coordinator),
        "subagent": summary(subagent),
    }
    return stable, measurements


class _FixtureRcloneBackend(RcloneCryptBackend):
    """In-memory runner for the adapter contract; it never invokes rclone."""

    def __init__(self, provider: str) -> None:
        self._provider = provider
        self._objects: dict[str, bytes] = {}
        super().__init__(f"{provider}:archive", runner=self._run)
        self.name = f"rclone-crypt-{provider}"

    def _run(self, args, **_kwargs):
        class Result:
            returncode = 0

        if args[:2] != ["rclone", "copyto"]:
            raise AssertionError("unexpected rclone command")
        source, destination = args[2], args[3]
        remote_prefix = f"{self.remote}/"
        if source.startswith(remote_prefix):
            Path(destination).write_bytes(self._objects[source])
        else:
            self._objects[destination] = Path(source).read_bytes()
        return Result()


def _rclone_output(root: Path) -> dict[str, Any]:
    first = _FixtureRcloneBackend("provider-a")
    second = _FixtureRcloneBackend("provider-b")
    ledgers = SqliteLedgers(root / "archive-ledger.sqlite3")
    verification = ArchiveAutomation((first, second), ledgers=ledgers).archive(
        key="phase0/component/state.bin", payload=b"deterministic-archive-fixture"
    )
    return {
        "verification": {
            "providers": list(verification.providers),
            "upload_verified": verification.upload_verified,
            "restore_verified": verification.restore_verified,
            "passed": verification.passed,
            "reasons": list(verification.reasons),
        },
        "ledger_events": len(ledgers.events(LedgerNamespace.INCIDENT)),
        "real_rclone_invoked": False,
        "production_admitted": False,
    }


def _new_run_directory(output_root: Path) -> tuple[str, Path]:
    run_id_base = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_id = run_id_base
    suffix = 1
    while (output_root / run_id).exists():
        suffix += 1
        run_id = f"{run_id_base}-{suffix}"
    return run_id, output_root / run_id


def run_evidence(output_root: Path) -> tuple[Path, dict[str, Any]]:
    """Write one immutable local component evidence run."""

    output_root = output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    run_id, run_directory = _new_run_directory(output_root)
    run_directory.mkdir(parents=True)
    inventory = run_availability_inventory(default_candidates())
    by_name = _availability_payload(inventory)
    components: dict[str, dict[str, Any]] = {}
    required_failures: list[str] = []

    for name, kind, route in (
        ("pydantic-ai", ComponentKind.ORCHESTRATION, "local/pydantic-ai"),
        ("prefect", ComponentKind.ORCHESTRATION, "local/prefect"),
        ("hamilton", ComponentKind.FEATURE_COMPUTE, "local/hamilton"),
    ):
        availability = by_name[name]
        if availability["status"] != "available":
            components[name] = _quarantine(
                name=name,
                availability=availability,
                reason="required local runtime dependency is unavailable",
                required_for_core=True,
            )
            required_failures.append(name)
            continue
        components[name] = _measure(
            name=name,
            kind=kind,
            version=availability["version"] or "installed",
            route=route,
            runner=lambda name=name: _runtime_output(name),
        )
        components[name]["fixture_gate_only"] = True
        components[name]["production_admitted"] = False

    nautilus_availability = by_name["nautilus-trader"]
    nautilus = _measure(
        name="nautilus-trader",
        kind=ComponentKind.REPLAY,
        version=nautilus_availability.get("version") or "installed",
        route="local/nautilus-replay-fixture",
        runner=_nautilus_output,
    )
    nautilus["runtime_available"] = nautilus_availability["status"] == "available"
    nautilus["production_admitted"] = False
    components["nautilus-trader"] = nautilus
    if not nautilus["runtime_available"] or not nautilus["passed"]:
        required_failures.append("nautilus-trader")

    parquet = _measure(
        name="parquet-manifest",
        kind=ComponentKind.LAKE_CATALOG,
        version="pyarrow-local",
        route="local/parquet-manifest-duckdb",
        runner=lambda: _parquet_output(run_directory / "lake-fixture"),
    )
    components["parquet-manifest"] = parquet
    components["parquet-manifest"]["production_admitted"] = False

    ducklake_availability = by_name["ducklake"]
    components["ducklake"] = _quarantine(
        name="ducklake",
        availability=ducklake_availability,
        reason=(
            "DuckLake dependency is unavailable; Parquet manifest evidence is recorded without "
            "a silent substitute"
            if ducklake_availability["status"] != "available"
            else "no DuckLake provider/catalog was configured for this credential-free drill"
        ),
    )

    hermes_stable, hermes_measurements = _hermes_output()
    hermes = _measure(
        name="hermes-sandbox",
        kind=ComponentKind.RESEARCH_RUNTIME,
        version="advisorai-hermes-boundary-v1",
        route="local/hermes-isolation-fixture",
        runner=lambda: hermes_stable,
    )
    hermes["task_measurements"] = hermes_measurements
    hermes["external_runtime_available"] = by_name["hermes-agent"]["status"] == "available"
    hermes["production_admitted"] = False
    components["hermes-sandbox"] = hermes
    components["hermes-agent"] = _quarantine(
        name="hermes-agent",
        availability=by_name["hermes-agent"],
        reason="external Hermes package is not installed; repository sandbox boundary remains fixture-tested",
    )

    rclone_stable = _rclone_output(run_directory / "archive-fixture")
    rclone = _measure(
        name="rclone-crypt-contract",
        kind=ComponentKind.ARCHIVE,
        version="advisorai-rclone-adapter-v1",
        route="local/rclone-crypt-fixture",
        runner=lambda: rclone_stable,
    )
    rclone["external_binary_available"] = shutil.which("rclone") is not None
    rclone["production_admitted"] = False
    components["rclone-crypt-contract"] = rclone
    components["rclone-crypt"] = _quarantine(
        name="rclone-crypt",
        availability=by_name["rclone-crypt"],
        reason=(
            "rclone binary/provider configuration is unavailable; the injected adapter contract "
            "does not claim real archive-provider restore"
        ),
    )

    local_probe_names = (
        "pydantic-ai",
        "prefect",
        "hamilton",
        "nautilus-trader",
        "parquet-manifest",
        "hermes-sandbox",
        "rclone-crypt-contract",
    )
    local_probes_passed = not required_failures and all(
        components[name].get("passed") is True for name in local_probe_names
    )
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "run_id": run_id,
        "measured_at": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "network_calls": 0,
        "credentials_used": False,
        "paper_orders": 0,
        "live_capital": False,
        "availability": by_name,
        "components": components,
        "local_probes_passed": local_probes_passed,
        "phase0_gate_decision": "pending",
        "phase0_gate_eligible": False,
        "phase0_gate_recorded": False,
        "external_gates_required": [
            "24-hour stability/resource evidence is not supplied by a short probe",
            "DuckLake catalog comparison, the external Hermes package, and real rclone archive restore remain unverified",
            "model and gateway stability evidence is recorded by separate supervised runners",
        ],
        "live_capital_statement": "LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.",
    }
    report_path = _write_immutable_json(run_directory / "phase0-component-bakeoff.json", report)
    pointer = {
        "schema": f"{SCHEMA}.latest",
        "run_id": run_id,
        "report_sha256": _sha256(report_path.read_bytes()),
    }
    _write_latest_pointer(output_root / "latest.json", pointer)
    return report_path, {**report, "report_sha256": pointer["report_sha256"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/phase0/component-bakeoff"),
        help="ignored local evidence root",
    )
    args = parser.parse_args()
    report_path, report = run_evidence(args.output)
    print(json.dumps({"report": str(report_path), **report}, sort_keys=True))
    return 0 if report["local_probes_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
