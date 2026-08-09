#!/usr/bin/env python3
"""Run a resumable exact-provider remote-route stability window.

This runner reuses the Phase-0 PolicyGateway route admission. It sends only a
synthetic typed research probe, never exposes broker/order credentials, and
records route identity, provider response identity, cost, latency, failures,
and structured-output status in an append-only hash chain.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shlex
import sys
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

try:
    import run_remote_model_bakeoff as bakeoff
except ModuleNotFoundError:  # pragma: no cover - exercised when imported as a module
    from scripts import run_remote_model_bakeoff as bakeoff

from advisorai.config import CredentialResolver, CredentialScope, SecretSettings
from advisorai.gateway import ProviderEndpointAdmission
from advisorai.integrations.http import HttpClientConfig, SafeHttpClient
from advisorai.integrations.llm import OpenAICompatibleGatewayAdapter
from advisorai.phase0.remote_bakeoff import (
    RemoteInvocation,
    RemoteProbeResult,
    RemoteProbeStatus,
)
from advisorai.phase0.remote_stability import (
    SCHEMA_VERSION,
    append_record,
    make_record,
    read_records,
    summarize_records,
)
from advisorai.ports import GatewayRoute

DEFAULT_CANDIDATE = "private-ling-novita"
MAX_BILLED_PER_CALL_USD = 0.001
MAX_RUN_BUDGET_USD = 0.25


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _canonical_json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _implementation_sha256() -> str:
    digest = sha256()
    for source in (
        Path(__file__).resolve(),
        Path(__file__).resolve().parents[1] / "src/advisorai/phase0/remote_stability.py",
    ):
        digest.update(source.name.encode("utf-8"))
        digest.update(source.read_bytes())
    return digest.hexdigest()


def _attest_config(config_path: Path, config_hash_path: Path) -> str:
    config_sha256 = sha256(config_path.read_bytes()).hexdigest()
    if config_hash_path.exists():
        recorded = config_hash_path.read_text(encoding="utf-8").strip()
        if recorded != config_sha256:
            raise RuntimeError("stability config hash changed; quarantine this run")
        return config_sha256
    try:
        with config_hash_path.open("x", encoding="utf-8") as handle:
            handle.write(config_sha256 + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise RuntimeError("stability config hash appeared during initialization") from exc
    return config_sha256


def _route_identity(candidate) -> str:
    resolved = candidate.allowed_resolved_models[0] if candidate.allowed_resolved_models else ""
    return ":".join(
        (
            candidate.gateway,
            candidate.provider_selector or "",
            candidate.requested_model,
            candidate.requested_endpoint_selector or "",
            resolved,
        )
    )


def _route_summary(candidate) -> dict[str, object]:
    return {
        "candidate": candidate.candidate,
        "gateway": candidate.gateway,
        "provider_selector": candidate.provider_selector,
        "requested_model": candidate.requested_model,
        "requested_endpoint_selector": candidate.requested_endpoint_selector,
        "observed_provider_names": list(candidate.observed_provider_names),
        "allowed_resolved_models": list(candidate.allowed_resolved_models),
        "zdr": candidate.zdr,
        "data_collection": candidate.data_collection,
        "allow_fallbacks": candidate.allow_fallbacks,
        "identity_key": _route_identity(candidate),
    }


def _load_expected_roster(path: Path, candidate_name: str) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("roles", {}).get("private_worker", [])
    if not isinstance(entries, list):
        raise ValueError("remote roster private_worker entries are missing")
    for entry in entries:
        if isinstance(entry, dict) and entry.get("candidate") == candidate_name:
            if entry.get("state") != "pending_quality_and_stability":
                raise ValueError("remote route is not eligible for a new stability window")
            return entry
    raise ValueError(f"remote roster does not contain candidate {candidate_name!r}")


def _build_route(candidate, client: SafeHttpClient, key: str):
    admission = ProviderEndpointAdmission(
        provider_selector_slug=candidate.provider_selector or "",
        allowed_provider_display_names=candidate.observed_provider_names,
        requested_model=candidate.requested_model,
        allowed_top_level_models=candidate.allowed_top_level_models,
        allowed_resolved_models=candidate.allowed_resolved_models,
        gateway=candidate.gateway,
        zdr=True,
        data_collection="deny",
        input_price_per_million=candidate.input_price_per_million or 0,
        output_price_per_million=candidate.output_price_per_million or 0,
        request_price=candidate.request_price or 0,
        inventory_artifact_hash=candidate.inventory_artifact_hash,
        inventory_timestamp=datetime.now(UTC),
        terms_reference=candidate.terms_reference,
        admission_version="remote-route-stability-v1",
        supports_tools=candidate.supports_tools,
        supports_tool_choice_required=candidate.supports_tool_choice_required,
        supports_structured_output=candidate.supports_structured_output,
        allow_response_format_with_tools=candidate.allow_response_format_with_tools,
    )
    route = GatewayRoute(
        provider=candidate.provider_selector or candidate.requested_model,
        model=candidate.requested_model,
        gateway=candidate.gateway,
        endpoint_variant=candidate.requested_endpoint_selector,
        retention_policy="zero",
        training_policy="no_training_zdr",
        terms_verified=True,
        terms_reference=candidate.terms_reference,
    )
    adapter = OpenAICompatibleGatewayAdapter(
        route,
        client,
        api_key=key,
        input_price_per_million=candidate.input_price_per_million,
        output_price_per_million=candidate.output_price_per_million,
        request_price_usd=candidate.request_price,
    )
    profile, config = bakeoff._make_profile(candidate, admission, adapter)
    return admission, adapter, config, profile


def _evaluate_probe(candidate, result: RemoteProbeResult) -> tuple[bool, str | None]:
    if result.status is not RemoteProbeStatus.MEASURED:
        return False, result.failure_class or "remote route probe did not measure"
    if result.actual_gateway != candidate.gateway:
        return False, "gateway identity changed"
    if result.requested_provider_selector != candidate.provider_selector:
        return False, "provider selector changed"
    if result.requested_model != candidate.requested_model:
        return False, "requested model changed"
    if result.requested_endpoint_selector != candidate.requested_endpoint_selector:
        return False, "endpoint selector changed"
    if result.observed_provider_name not in candidate.observed_provider_names:
        return False, "observed provider identity changed"
    if result.resolved_endpoint_model not in candidate.allowed_resolved_models:
        return False, "resolved endpoint model changed"
    if result.endpoint_selected is not True:
        return False, "endpoint was not selected"
    if result.routing_strategy != "direct":
        return False, "route did not use direct routing"
    if result.tool_execution_status != "not_executed":
        return False, "external tool execution was claimed"
    if result.tool_called:
        return False, "structured probe unexpectedly called a tool"
    if result.billed_cost_usd is None or result.billed_cost_usd > MAX_BILLED_PER_CALL_USD:
        return False, "billed cost exceeded the per-call ceiling"
    return True, None


def _probe_payload(
    candidate, result: RemoteProbeResult, *, passed: bool, reason: str | None
) -> dict[str, object]:
    return {
        "route": _route_summary(candidate),
        "probe": result.model_dump(mode="json"),
        "structured_output_valid": result.status is RemoteProbeStatus.MEASURED,
        "passed": passed,
        "failure_reason": reason,
        "secret_scope": "DIRECT_LLM only",
        "broker_order_credentials_exposed": False,
    }


def _inventory_and_candidate(
    *,
    client: SafeHttpClient,
    key: str,
    candidate_name: str,
    inventory_path: Path,
    expected: dict[str, object] | None,
):
    candidates, inventory = bakeoff._fetch_candidates(
        client, key, inventory_timestamp=datetime.now(UTC)
    )
    current = next((item for item in candidates if item.candidate == candidate_name), None)
    if current is None or not current.reproducible:
        raise RuntimeError("live inventory does not expose an exact reproducible route")
    if expected is not None:
        expected_identity = expected.get("route")
        if not isinstance(expected_identity, dict) or _route_summary(current) != expected_identity:
            raise RuntimeError("live route identity drifted from the immutable stability config")
        inventory_bytes = inventory_path.read_bytes()
        inventory_hash = sha256(inventory_bytes).hexdigest()
        if inventory_hash != str(expected["inventory_sha256"]):
            raise RuntimeError("stability inventory evidence hash changed on resume")
    else:
        inventory_bytes = _canonical_json_bytes(inventory)
        inventory_hash = sha256(inventory_bytes).hexdigest()
        if inventory_path.exists() and inventory_path.read_bytes() != inventory_bytes:
            raise RuntimeError("stability inventory path already contains different evidence")
        if not inventory_path.exists():
            inventory_path.parent.mkdir(parents=True, exist_ok=True)
            inventory_path.write_bytes(inventory_bytes)
            with inventory_path.open("rb") as handle:
                os.fsync(handle.fileno())
    return current.model_copy(
        update={
            "inventory_artifact_hash": inventory_hash,
            "inventory_reference": str(inventory_path),
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--secrets", type=Path, default=Path("/home/maaro/.config/advisorai-v3/secrets.env")
    )
    parser.add_argument(
        "--roster", type=Path, default=Path("configs/models/phase0_remote_roster.json")
    )
    parser.add_argument("--run-directory", type=Path)
    parser.add_argument("--candidate", default=DEFAULT_CANDIDATE)
    parser.add_argument("--duration-hours", type=float, default=24)
    parser.add_argument("--interval-seconds", type=float, default=600)
    parser.add_argument("--max-cycles", type=int)
    args = parser.parse_args()
    if args.duration_hours <= 0 or args.interval_seconds <= 0:
        raise ValueError("duration and interval must be positive")
    if args.max_cycles is not None and args.max_cycles < 1:
        raise ValueError("max cycles must be positive")
    expected_roster = _load_expected_roster(args.roster, args.candidate)
    resolver = CredentialResolver.from_env_file(args.secrets)
    key = resolver.get(CredentialScope.DIRECT_LLM, "ADVISORAI_LLM_API_KEY")
    if not key:
        raise SystemExit("ADVISORAI_LLM_API_KEY is not populated in the direct_llm scope")
    settings = SecretSettings.from_env_file(args.secrets)
    if settings.llm_provider.lower() != "openrouter":
        raise SystemExit("remote route stability requires the configured OpenRouter route")
    client = SafeHttpClient(
        HttpClientConfig(
            allowed_hosts=(bakeoff.OPENROUTER_HOST,), user_agent="advisorai-v3/remote-stability"
        ),
        base_url=bakeoff.OPENROUTER_BASE,
        secret_values={"ADVISORAI_LLM_API_KEY": key},
    )
    key_payload = bakeoff._get_json(client, "/key", key=key)
    key_data = key_payload.get("data", key_payload) if isinstance(key_payload, dict) else {}
    remaining = float(key_data.get("limit_remaining", 0)) if isinstance(key_data, dict) else 0.0
    budget_cap = min(MAX_RUN_BUDGET_USD, max(0.0, remaining * 0.25))
    if budget_cap <= 0:
        raise SystemExit("remote route stability has no remaining read-only budget")

    run_directory = args.run_directory
    if run_directory is None:
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        run_directory = Path("artifacts/phase0/remote-route-stability") / run_id
    run_directory.mkdir(parents=True, exist_ok=True)
    config_path = run_directory / "config.json"
    config_hash_path = run_directory / "config.sha256"
    code_sha256 = _implementation_sha256()
    stored_config: dict[str, Any] | None = None
    if config_path.exists():
        config_sha256 = _attest_config(config_path, config_hash_path)
        stored_config = json.loads(config_path.read_text(encoding="utf-8"))
        run_id = str(stored_config["run_id"])
        started_at = datetime.fromisoformat(str(stored_config["started_at"]))
        if stored_config.get("candidate") != args.candidate:
            raise ValueError("stability run candidate cannot change on resume")
        if float(stored_config["duration_hours"]) != args.duration_hours:
            raise ValueError("stability duration cannot change on resume")
        if float(stored_config["interval_seconds"]) != args.interval_seconds:
            raise ValueError("stability interval cannot change on resume")
        if stored_config.get("code_sha256") != code_sha256:
            raise RuntimeError("stability implementation hash changed; quarantine this run")
    else:
        run_id = run_directory.name
        started_at = datetime.now(UTC)

    inventory_path = run_directory / "inventory.json"
    candidate = _inventory_and_candidate(
        client=client,
        key=key,
        candidate_name=args.candidate,
        inventory_path=inventory_path,
        expected=stored_config,
    )
    expected_provider = expected_roster.get("provider_selector")
    expected_model = expected_roster.get("requested_model")
    expected_revision = expected_roster.get("revision")
    if (
        candidate.provider_selector != expected_provider
        or candidate.requested_model != expected_model
    ):
        raise RuntimeError("live route no longer matches the reviewed remote roster")
    if isinstance(expected_revision, list) and expected_revision:
        if tuple(expected_revision) != candidate.allowed_resolved_models:
            raise RuntimeError("live resolved model no longer matches the reviewed remote roster")
    if stored_config is None:
        route = _route_summary(candidate)
        config_payload = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "started_at": started_at.isoformat(),
            "duration_hours": args.duration_hours,
            "interval_seconds": args.interval_seconds,
            "candidate": args.candidate,
            "route": route,
            "inventory_sha256": candidate.inventory_artifact_hash,
            "inventory_reference": str(inventory_path),
            "budget_cap_usd": budget_cap,
            "max_billed_per_call_usd": MAX_BILLED_PER_CALL_USD,
            "tool_execution": "forbidden/not_executed",
            "code_sha256": code_sha256,
            "command": [shlex.quote(argument) for argument in sys.argv],
            "evidence_root": str(run_directory),
        }
        _write_json(config_path, config_payload)
        config_sha256 = _attest_config(config_path, config_hash_path)
        stored_config = config_payload
    else:
        budget_cap = float(stored_config["budget_cap_usd"])

    admission, adapter, gateway_config, _profile = _build_route(candidate, client, key)
    log_path = run_directory / "cycles.jsonl"
    lock_handle = (run_directory / "runner.lock").open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError("another remote route stability runner owns this run") from exc
    lock_handle.seek(0)
    lock_handle.truncate()
    lock_handle.write(str(os.getpid()))
    lock_handle.flush()
    cycles = read_records(log_path)
    if any(record.get("run_id") != run_id for record in cycles):
        raise ValueError("remote stability log contains another run identity")
    target_end = started_at + timedelta(hours=args.duration_hours)
    reserved_budget = len(cycles) * MAX_BILLED_PER_CALL_USD
    state = "running"
    try:
        while datetime.now(UTC) < target_end:
            if args.max_cycles is not None and len(cycles) >= args.max_cycles:
                break
            if reserved_budget + MAX_BILLED_PER_CALL_USD > budget_cap:
                state = "budget_exhausted"
                break
            result = bakeoff._probe(
                candidate,
                admission,
                adapter,
                gateway_config,
                invocation=RemoteInvocation.STRUCTURED_OUTPUT,
            )
            passed, reason = _evaluate_probe(candidate, result)
            record = make_record(
                run_id=run_id,
                sequence=len(cycles),
                sampled_at=datetime.now(UTC),
                identity_key=_route_identity(candidate),
                passed=passed,
                probe=_probe_payload(candidate, result, passed=passed, reason=reason),
                previous_record_hash=(str(cycles[-1]["record_hash"]) if cycles else None),
                config_sha256=config_sha256,
            )
            append_record(log_path, record)
            cycles = read_records(log_path)
            reserved_budget += MAX_BILLED_PER_CALL_USD
            summary = summarize_records(
                run_id=run_id,
                started_at=started_at,
                duration_hours=args.duration_hours,
                records=cycles,
                now=datetime.now(UTC),
            )
            _write_json(
                run_directory / "status.json",
                {
                    **summary,
                    "pid": os.getpid(),
                    "state": "running",
                    "heartbeat_at": datetime.now(UTC).isoformat(),
                    "started_at": started_at.isoformat(),
                    "config_sha256": config_sha256,
                    "code_sha256": code_sha256,
                    "command": [shlex.quote(argument) for argument in sys.argv],
                    "evidence_root": str(run_directory),
                    "reserved_budget_usd": reserved_budget,
                    "actual_billed_cost_usd": sum(
                        float(record.get("probe", {}).get("probe", {}).get("billed_cost_usd") or 0)
                        for record in cycles
                    ),
                },
            )
            if not passed:
                state = "failed"
            remaining = (target_end - datetime.now(UTC)).total_seconds()
            if remaining <= 0 or (args.max_cycles is not None and len(cycles) >= args.max_cycles):
                break
            time.sleep(min(args.interval_seconds, remaining))
    finally:
        fcntl.flock(lock_handle, fcntl.LOCK_UN)
        lock_handle.close()

    summary = summarize_records(
        run_id=run_id,
        started_at=started_at,
        duration_hours=args.duration_hours,
        records=cycles,
        now=datetime.now(UTC),
    )
    if state == "budget_exhausted":
        summary["status"] = "budget_exhausted"
    _write_json(run_directory / "summary.json", summary)
    _write_json(
        run_directory / "status.json",
        {
            **summary,
            "pid": os.getpid(),
            "state": summary["status"],
            "started_at": started_at.isoformat(),
            "config_sha256": config_sha256,
            "code_sha256": code_sha256,
            "command": [shlex.quote(argument) for argument in sys.argv],
            "evidence_root": str(run_directory),
            "reserved_budget_usd": reserved_budget,
            "actual_billed_cost_usd": sum(
                float(record.get("probe", {}).get("probe", {}).get("billed_cost_usd") or 0)
                for record in cycles
            ),
        },
    )
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["status"] in {"passed", "short_smoke_complete"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
