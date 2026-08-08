import os
import time
from pathlib import Path

import pytest

from advisorai.capabilities import (
    CapabilityBroker,
    CapabilityFoundry,
    CapabilityRegistry,
    EnvironmentManifest,
    HermesIsolationRunner,
    HermesSandboxPolicy,
)
from advisorai.contracts import CapabilityCard, CapabilityLifecycle, SourceGrade
from advisorai.ledger import LedgerNamespace, SqliteLedgers


def _hermes_task_output():
    return {"artifact": "quarantined", "broker_key_visible": os.getenv("BROKER_API_KEY")}


def _slow_hermes_task():
    time.sleep(2)
    return {"done": True}


def _caught_network_task():
    import socket

    try:
        socket.create_connection(("example.invalid", 443), timeout=0.1)
    except Exception as exc:
        return {"caught": type(exc).__name__}
    return {"caught": None}


def _direct_socket_network_task():
    import socket

    connection = socket.socket()
    try:
        connection.connect(("127.0.0.1", 9))
    except Exception as exc:
        return {"caught": type(exc).__name__}
    finally:
        connection.close()
    return {"caught": None}


def _udp_network_task():
    import socket

    connection = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        connection.sendto(b"blocked", ("127.0.0.1", 9))
    except Exception as exc:
        return {"caught": type(exc).__name__}
    finally:
        connection.close()
    return {"caught": None}


def _dns_network_task():
    import socket

    try:
        socket.gethostbyname("example.invalid")
    except Exception as exc:
        return {"caught": type(exc).__name__}
    return {"caught": None}


def _caught_filesystem_write_task():
    try:
        Path("hermes-write-attempt.txt").write_text("must be rejected", encoding="utf-8")
    except Exception as exc:
        return {"caught": type(exc).__name__}
    return {"caught": None}


def _caught_os_write_task():
    import os

    try:
        descriptor = os.open("hermes-os-write-attempt.txt", os.O_WRONLY | os.O_CREAT)
    except Exception as exc:
        return {"caught": type(exc).__name__}
    else:
        os.close(descriptor)
    return {"caught": None}


def _read_filesystem_task():
    return {"bytes": len(Path("pyproject.toml").read_bytes())}


def _caught_sensitive_path_task():
    try:
        Path("secrets.env").read_text(encoding="utf-8")
    except Exception as exc:
        return {"caught": type(exc).__name__}
    return {"caught": None}


def _caught_process_environment_task():
    try:
        Path("/proc/self/environ").read_bytes()
    except Exception as exc:
        return {"caught": type(exc).__name__}
    return {"caught": None}


def _caught_ssh_key_task():
    try:
        Path.home().joinpath(".ssh", "id_rsa").read_bytes()
    except Exception as exc:
        return {"caught": type(exc).__name__}
    return {"caught": None}


def _read_sensitive_symlink_task():
    try:
        Path("hermes-safe-link").read_bytes()
    except Exception as exc:
        return {"caught": type(exc).__name__}
    return {"caught": None}


def _environment():
    return EnvironmentManifest(image_digest="sha256:image", lock_hash="a" * 64, seed=7)


def _card():
    return CapabilityCard(
        name="deterministic-collector",
        capability_version="v1",
        inputs=("url",),
        outputs=("observations",),
        allowed_actions=("read_source",),
        resource_envelope="small",
        latency_class="bounded",
        deterministic=True,
        source_grade=SourceGrade.RESEARCH,
        test_references=("contract-test", "security-test", "performance-test"),
    )


def test_hermes_policy_isolated_and_forbids_trade_actions():
    policy = HermesSandboxPolicy(
        mode="builder", cpu_seconds=60, memory_mib=512, wall_time_seconds=120
    )
    assert policy.read_only_snapshot
    with pytest.raises(ValueError, match="only in"):
        HermesSandboxPolicy(mode="standard", cpu_seconds=60, memory_mib=512, wall_time_seconds=120)
    with pytest.raises(ValueError, match="secrets"):
        HermesSandboxPolicy(
            mode="builder",
            cpu_seconds=60,
            memory_mib=512,
            wall_time_seconds=120,
            allowed_secrets=("BROKER_API_KEY",),
        )


def test_hermes_isolation_runner_records_bounded_quarantined_task(monkeypatch):
    monkeypatch.setenv("BROKER_API_KEY", "must-not-cross-boundary")
    policy = HermesSandboxPolicy(mode="builder", cpu_seconds=2, memory_mib=512, wall_time_seconds=1)
    result = HermesIsolationRunner(policy).run(
        task_name="collector-contract-test", task=_hermes_task_output
    )
    assert result.passed
    assert result.output == {"artifact": "quarantined", "broker_key_visible": None}
    assert result.output_hash is not None
    assert result.peak_memory_mib < policy.memory_mib


def test_hermes_isolation_runner_terminates_wall_time_overrun():
    policy = HermesSandboxPolicy(mode="builder", cpu_seconds=2, memory_mib=512, wall_time_seconds=1)
    result = HermesIsolationRunner(policy).run(task_name="slow-task", task=_slow_hermes_task)
    assert not result.passed
    assert result.timed_out
    assert result.error == "wall_time_budget_exceeded"


@pytest.mark.parametrize(
    "task",
    [_caught_network_task, _direct_socket_network_task, _udp_network_task, _dns_network_task],
)
def test_hermes_isolation_runner_rejects_network_even_when_task_catches_error(task):
    policy = HermesSandboxPolicy(mode="builder", cpu_seconds=2, memory_mib=512, wall_time_seconds=1)
    result = HermesIsolationRunner(policy).run(task_name="network-attempt", task=task)
    assert not result.passed
    assert result.network_access_attempted
    assert result.error == "network_access_attempted"
    assert result.output is None


def test_hermes_isolation_runner_requires_an_allowlisted_network_host():
    policy = HermesSandboxPolicy(
        mode="builder",
        allowed_network_hosts=("127.0.0.1",),
        cpu_seconds=2,
        memory_mib=512,
        wall_time_seconds=1,
    )
    result = HermesIsolationRunner(policy).run(
        task_name="network-attempt", task=_caught_network_task
    )
    assert not result.passed
    assert result.network_access_attempted
    assert result.error == "network_access_attempted"


@pytest.mark.parametrize("task", [_caught_filesystem_write_task, _caught_os_write_task])
def test_hermes_isolation_runner_rejects_filesystem_mutation(task):
    policy = HermesSandboxPolicy(mode="builder", cpu_seconds=2, memory_mib=512, wall_time_seconds=1)
    result = HermesIsolationRunner(policy).run(task_name="filesystem-attempt", task=task)
    assert not result.passed
    assert result.filesystem_write_attempted
    assert result.error == "filesystem_write_attempted"
    assert result.output is None


def test_hermes_isolation_runner_allows_read_only_snapshot_access():
    policy = HermesSandboxPolicy(mode="builder", cpu_seconds=2, memory_mib=512, wall_time_seconds=1)
    result = HermesIsolationRunner(policy).run(
        task_name="filesystem-read", task=_read_filesystem_task
    )
    assert result.passed
    assert result.output == {"bytes": Path("pyproject.toml").stat().st_size}
    assert not result.filesystem_write_attempted


def test_hermes_isolation_runner_rejects_sensitive_path_reads():
    policy = HermesSandboxPolicy(mode="builder", cpu_seconds=2, memory_mib=512, wall_time_seconds=1)
    result = HermesIsolationRunner(policy).run(
        task_name="sensitive-path-attempt", task=_caught_sensitive_path_task
    )
    assert not result.passed
    assert result.sensitive_path_access_attempted
    assert result.error == "sensitive_path_access_attempted"
    assert result.output is None


def test_hermes_isolation_runner_rejects_process_environment_reads():
    policy = HermesSandboxPolicy(mode="builder", cpu_seconds=2, memory_mib=512, wall_time_seconds=1)
    result = HermesIsolationRunner(policy).run(
        task_name="process-environment-attempt", task=_caught_process_environment_task
    )
    assert not result.passed
    assert result.sensitive_path_access_attempted
    assert result.error == "sensitive_path_access_attempted"
    assert result.output is None


def test_hermes_isolation_runner_rejects_ssh_key_reads():
    policy = HermesSandboxPolicy(mode="builder", cpu_seconds=2, memory_mib=512, wall_time_seconds=1)
    result = HermesIsolationRunner(policy).run(
        task_name="ssh-key-attempt", task=_caught_ssh_key_task
    )
    assert not result.passed
    assert result.sensitive_path_access_attempted
    assert result.error == "sensitive_path_access_attempted"
    assert result.output is None


def test_hermes_isolation_runner_rejects_symlinked_sensitive_paths(tmp_path, monkeypatch):
    sensitive_file = tmp_path / "id_rsa"
    sensitive_file.write_bytes(b"fixture-private-material")
    (tmp_path / "hermes-safe-link").symlink_to(sensitive_file)
    monkeypatch.chdir(tmp_path)
    policy = HermesSandboxPolicy(mode="builder", cpu_seconds=2, memory_mib=512, wall_time_seconds=1)
    result = HermesIsolationRunner(policy).run(
        task_name="symlinked-sensitive-path-attempt", task=_read_sensitive_symlink_task
    )
    assert not result.passed
    assert result.sensitive_path_access_attempted
    assert result.error == "sensitive_path_access_attempted"
    assert result.output is None


def test_capability_lifecycle_stops_at_active_read_without_approval():
    registry = CapabilityRegistry()
    registry.register(_card())
    for target in (
        CapabilityLifecycle.SCOUT,
        CapabilityLifecycle.PIN,
        CapabilityLifecycle.INSPECT,
        CapabilityLifecycle.SANDBOX,
        CapabilityLifecycle.WRAP_BUILD,
        CapabilityLifecycle.CONTRACT_TESTED,
        CapabilityLifecycle.SECURITY_TESTED,
        CapabilityLifecycle.PERFORMANCE_BENCHMARKED,
        CapabilityLifecycle.SHADOW,
        CapabilityLifecycle.ACTIVE_READ,
    ):
        registry.promote(
            name="deterministic-collector",
            version="v1",
            target=target,
            actor="reviewer",
        )
    with pytest.raises(PermissionError, match="human approval"):
        registry.promote(
            name="deterministic-collector",
            version="v1",
            target=CapabilityLifecycle.ACTIVE_WRITE_LIMITED,
            actor="",
        )


def test_foundry_exports_hashes_and_broker_filters_permissions():
    foundry = CapabilityFoundry()
    bundle = foundry.export_capability(
        name="collector",
        capability_version="v1",
        interface="CollectorPort",
        code="return []",
        permissions=("read_source",),
        environment=_environment(),
    )
    assert len(bundle.artifact_hash) == 64
    reviewed = foundry.export_capability(
        name="collector",
        capability_version="v1",
        interface="CollectorPort",
        code="return []",
        permissions=("read_source",),
        environment=_environment(),
        review_references=("security-review-1",),
    )
    assert reviewed.artifact_hash != bundle.artifact_hash
    registry = CapabilityRegistry()
    registry.register(_card())
    for target in (
        CapabilityLifecycle.SCOUT,
        CapabilityLifecycle.PIN,
        CapabilityLifecycle.INSPECT,
        CapabilityLifecycle.SANDBOX,
        CapabilityLifecycle.WRAP_BUILD,
        CapabilityLifecycle.CONTRACT_TESTED,
        CapabilityLifecycle.SECURITY_TESTED,
        CapabilityLifecycle.PERFORMANCE_BENCHMARKED,
        CapabilityLifecycle.SHADOW,
        CapabilityLifecycle.ACTIVE_READ,
    ):
        registry.promote(
            name="deterministic-collector", version="v1", target=target, actor="reviewer"
        )
    executor = CapabilityBroker(registry).expose(
        capability_name="deterministic-collector",
        capability_version="v1",
        requested_action="read_source",
        mode="builder",
        executor=lambda: "ok",
        available_resource_envelopes=("small",),
    )
    assert executor() == "ok"
    with pytest.raises(PermissionError, match="resource envelope"):
        CapabilityBroker(registry).expose(
            capability_name="deterministic-collector",
            capability_version="v1",
            requested_action="read_source",
            mode="builder",
            executor=lambda: "ok",
            available_resource_envelopes=("gpu-heavy",),
        )
    with pytest.raises(PermissionError):
        foundry.export_capability(
            name="unsafe",
            capability_version="v1",
            interface="x",
            code="x",
            permissions=("submit_order",),
            environment=_environment(),
        )


def test_foundry_exports_strategy_model_adapter_and_runbook_artifacts():
    foundry = CapabilityFoundry()
    strategy = foundry.export_strategy(
        name="bounded-breakout",
        strategy_version="v1",
        rationale="liquidity-aware continuation",
        experiment_spec="past-only walk-forward",
        source_artifact_ids=("snapshot-1",),
        code="return signal",
        required_tests=("reproducibility-test",),
        risk_notes=("paper-only",),
        environment=_environment(),
    )
    assert len(strategy.code_hash) == 64
    adapter = foundry.export_model_adapter(
        name="candidate-forecast",
        adapter_version="v1",
        role="forecast",
        adapter_code="return forecast",
        checkpoint_hash="b" * 64,
        contract_tests=("contract-test",),
        security_tests=("security-test",),
        performance_benchmark="benchmark-1",
        environment=_environment(),
    )
    assert adapter.checkpoint_hash == "b" * 64
    runbook = foundry.export_runbook(
        name="reconcile-restart",
        runbook_version="v1",
        trigger="ledger restart",
        steps=("stop optional workers", "rebuild from ledgers"),
        rollback="remain paper-only",
        environment=_environment(),
    )
    assert len(runbook.steps) == 2


def test_collector_candidate_reaches_active_read_only_through_every_lifecycle_stage():
    foundry = CapabilityFoundry()
    candidate = foundry.export_collector(
        name="official-rss",
        interface_version="v1",
        source_grade="research_grade",
        parser_code="parse rss",
        contract_tests=("rss-contract",),
        security_tests=("untrusted-content",),
        performance_benchmark="rss-benchmark",
        environment=_environment(),
    )
    registry = CapabilityRegistry()
    card = registry.promote_collector_to_active_read(
        candidate=candidate,
        foundry=foundry,
        actor="reviewer",
    )
    assert card.lifecycle is CapabilityLifecycle.ACTIVE_READ
    assert registry.get("official-rss", "v1").allowed_actions == ("read_source",)


def test_capability_registry_rebuilds_lifecycle_from_ledger(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "capabilities.sqlite")
    registry = CapabilityRegistry(ledgers)
    registry.register(_card())
    registry.promote(
        name="deterministic-collector",
        version="v1",
        target=CapabilityLifecycle.SCOUT,
        actor="reviewer",
    )
    restarted = CapabilityRegistry(ledgers)
    assert restarted.get("deterministic-collector", "v1").lifecycle is CapabilityLifecycle.SCOUT
    assert len(ledgers.events(LedgerNamespace.CAPABILITY)) == 2


def test_active_write_limited_transition_persists_explicit_human_approval(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "capability-write-approval.sqlite")
    registry = CapabilityRegistry(ledgers)
    registry.register(_card())
    for target in (
        CapabilityLifecycle.SCOUT,
        CapabilityLifecycle.PIN,
        CapabilityLifecycle.INSPECT,
        CapabilityLifecycle.SANDBOX,
        CapabilityLifecycle.WRAP_BUILD,
        CapabilityLifecycle.CONTRACT_TESTED,
        CapabilityLifecycle.SECURITY_TESTED,
        CapabilityLifecycle.PERFORMANCE_BENCHMARKED,
        CapabilityLifecycle.SHADOW,
        CapabilityLifecycle.ACTIVE_READ,
    ):
        registry.promote(
            name="deterministic-collector", version="v1", target=target, actor="reviewer"
        )
    registry.promote(
        name="deterministic-collector",
        version="v1",
        target=CapabilityLifecycle.ACTIVE_WRITE_LIMITED,
        actor="human-reviewer",
        human_approval=True,
    )
    assert (
        CapabilityRegistry(ledgers).get("deterministic-collector", "v1").lifecycle
        is CapabilityLifecycle.ACTIVE_WRITE_LIMITED
    )
