from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from advisorai.capabilities.sandbox import (
    BackendMeasurement,
    DestinationClass,
    HardenedDockerSandboxBackend,
    HostCapabilityReport,
    KataSandboxBackend,
    MountKind,
    NetworkMode,
    NetworkPolicy,
    ResourceLimits,
    SandboxMission,
    SandboxMount,
    SandboxPolicy,
    SandboxPolicyError,
    SandboxProcessRunner,
    SandboxUnavailable,
    SecretReference,
    TrustClass,
    adversarial_probe_names,
    select_sandbox_backend,
    unavailable_backend_measurement,
)

DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64


def _host(*, kata: bool = False, docker: bool = True) -> HostCapabilityReport:
    return HostCapabilityReport(
        measured_at=datetime.now(UTC),
        virtualization_backend="wsl",
        kvm_available=True,
        mshv_available=False,
        nested_virt_available=True,
        vsock_available=True,
        vhost_net_available=True,
        containerd_available=True,
        docker_available=docker,
        kata_runtime_available=kata,
        kata_runtime="kata-qemu" if kata else None,
        kata_host_feasible=kata,
        limitations=() if kata else ("Kata runtime unavailable",),
    )


def _mission(
    tmp_path: Path, *, trust_class: TrustClass = TrustClass.UNTRUSTED_RESEARCH
) -> SandboxMission:
    task_root = tmp_path / "mission"
    output_dir = task_root / "output"
    input_dir = tmp_path / "input"
    task_root.mkdir()
    output_dir.mkdir()
    input_dir.mkdir()
    return SandboxMission(
        mission_id="mission-test-001",
        workload_type="generated-research",
        trust_class=trust_class,
        image=f"registry.example/research@{DIGEST}",
        image_digest=DIGEST,
        rootfs_digest=OTHER_DIGEST,
        task_command=("/bin/true",),
        task_root=task_root,
        input_mounts=(
            SandboxMount(
                source=input_dir,
                target="/task/input",
                kind=MountKind.INPUT,
            ),
        ),
        output_dir=output_dir,
        resources=ResourceLimits(
            cpu_cores=1,
            memory_mib=256,
            pid_limit=64,
            output_mib=8,
            wall_time_seconds=3,
        ),
        advisorai_commit=DIGEST,
        dependency_lock_hash=OTHER_DIGEST,
        input_artifact_hashes=(DIGEST,),
    )


def _backends(*, kata: bool = False, docker: bool = True):
    host = _host(kata=kata, docker=docker)
    return (
        KataSandboxBackend(runtime="kata-qemu", host_report=host),
        HardenedDockerSandboxBackend(host_report=host),
    )


def test_untrusted_native_fails_closed_when_kata_is_unavailable():
    kata, docker = _backends(kata=False)

    with pytest.raises(SandboxUnavailable, match="UNTRUSTED_NATIVE requires Kata"):
        select_sandbox_backend(
            TrustClass.UNTRUSTED_NATIVE,
            policy=SandboxPolicy(),
            kata=kata,
            docker=docker,
        )


def test_untrusted_research_docker_fallback_is_explicit_and_recorded():
    kata, docker = _backends(kata=False)
    policy = SandboxPolicy(
        allow_docker_fallback=True,
        fallback_authorization_reference="security-review-2026-09-19",
    )

    selection = select_sandbox_backend(
        TrustClass.UNTRUSTED_RESEARCH,
        policy=policy,
        kata=kata,
        docker=docker,
    )

    assert selection.selected_backend.value == "hardened_docker"
    assert selection.fallback_used
    assert selection.policy_authorization == "security-review-2026-09-19"
    assert selection.fallback_reason


def test_docker_cannot_be_selected_as_an_implicit_native_fallback():
    kata, docker = _backends(kata=False)
    policy = SandboxPolicy(requested_backend="hardened_docker")

    with pytest.raises(SandboxPolicyError, match="explicit fallback authorization"):
        select_sandbox_backend(
            TrustClass.UNTRUSTED_RESEARCH,
            policy=policy,
            kata=kata,
            docker=docker,
        )
    with pytest.raises(SandboxUnavailable, match="requires Kata"):
        select_sandbox_backend(
            TrustClass.UNTRUSTED_NATIVE,
            policy=SandboxPolicy(
                allow_docker_fallback=True,
                fallback_authorization_reference="should-not-help-native",
            ),
            kata=kata,
            docker=docker,
        )


def test_kata_is_preferred_when_present_and_core_is_not_sandboxed():
    kata, docker = _backends(kata=True)
    selection = select_sandbox_backend(
        TrustClass.UNTRUSTED_RESEARCH,
        policy=SandboxPolicy(),
        kata=kata,
        docker=docker,
    )
    assert selection.selected_backend.value == "kata"
    assert not selection.fallback_used

    trusted_selection = select_sandbox_backend(
        TrustClass.TRUSTED_RESEARCH,
        policy=SandboxPolicy(),
        kata=kata,
        docker=docker,
    )
    assert trusted_selection.selected_backend.value == "hardened_docker"
    assert not trusted_selection.fallback_used

    with pytest.raises(SandboxPolicyError):
        select_sandbox_backend(
            TrustClass.TRUSTED_CORE,
            policy=SandboxPolicy(),
            kata=kata,
            docker=docker,
        )


def test_kata_launch_spec_is_immutable_and_has_no_authority_or_host_mounts(tmp_path: Path):
    kata, _ = _backends(kata=True)
    mission = _mission(tmp_path)
    spec = kata.prepare(mission, SandboxPolicy())
    command = list(spec.command)

    assert "--runtime" in command
    assert command[command.index("--runtime") + 1] == "kata-qemu"
    assert command[command.index("--network") + 1] == "none"
    assert "--pull=never" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert "--privileged" not in command
    assert "--cap-add" not in command
    assert "--device" not in command
    assert not any("docker.sock" in item or "containerd.sock" in item for item in command)
    assert not any("/mnt/c" in item for item in command)
    assert spec.provenance.credentials_loaded is False
    assert spec.provenance.order_writes_attempted is False
    assert spec.provenance.execution_authority_present is False
    assert spec.provenance.sandbox_backend.value == "kata"
    assert not (mission.output_dir / "preflight-artifact").exists()


def test_host_and_repository_mounts_are_rejected(tmp_path: Path):
    kata, _ = _backends(kata=True)
    mission = _mission(tmp_path)
    policy = SandboxPolicy(repository_root=tmp_path / "repository")
    repository = tmp_path / "repository"
    repository.mkdir()
    bad_mount = SandboxMount(
        source=repository,
        target="/task/repository",
        kind=MountKind.INPUT,
    )
    bad_mission = mission.model_copy(update={"input_mounts": (bad_mount,)})

    with pytest.raises(SandboxPolicyError, match="outside the allowed mission scope"):
        kata.prepare(bad_mission, policy)

    absolute_host_mount = mission.model_copy(
        update={
            "input_mounts": (
                SandboxMount(source=Path("/mnt/c"), target="/task/host", kind=MountKind.INPUT),
            )
        }
    )
    with pytest.raises(SandboxPolicyError):
        kata.prepare(absolute_host_mount, SandboxPolicy())

    system_file_mount = mission.model_copy(
        update={
            "input_mounts": (
                SandboxMount(
                    source=Path("/etc/passwd"), target="/task/passwd", kind=MountKind.INPUT
                ),
            )
        }
    )
    with pytest.raises(SandboxPolicyError):
        kata.prepare(system_file_mount, SandboxPolicy())


def test_network_policy_is_default_deny_and_rejects_metadata_or_private_destinations():
    assert NetworkPolicy().mode is NetworkMode.NONE
    with pytest.raises(ValueError):
        NetworkPolicy(allowed_hosts=("169.254.169.254",))
    with pytest.raises(ValueError):
        NetworkPolicy(allowed_hosts=("10.0.0.1",))
    with pytest.raises(ValueError):
        NetworkPolicy(
            mode=NetworkMode.ALLOWLIST,
            allowed_hosts=("research.example",),
            destination_classes=(DestinationClass.BROKER_READ_ONLY,),
            enforcer_network="advisorai-egress-readonly",
        )
    with pytest.raises(ValueError, match="read-only policy"):
        NetworkPolicy(
            mode=NetworkMode.ALLOWLIST,
            allowed_hosts=("api.exchange.example",),
            destination_classes=(DestinationClass.PUBLIC_RESEARCH,),
            enforcer_network="advisorai-egress-research",
        )
    policy = NetworkPolicy(
        mode=NetworkMode.ALLOWLIST,
        allowed_hosts=("research.example",),
        destination_classes=(DestinationClass.PUBLIC_RESEARCH,),
        enforcer_network="advisorai-egress-research",
    )
    assert policy.identity.startswith("sha256:")


def test_secret_values_are_not_a_backend_input_and_secret_env_files_are_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="broker"):
        SecretReference(
            name="broker_api_key",
            purpose="research",
            allowed_destinations=("research.example",),
            lifetime_seconds=30,
        )
    reference = SecretReference(
        name="research_token",
        purpose="read-only dataset retrieval",
        allowed_destinations=("research.example",),
        lifetime_seconds=30,
    )
    mission = _mission(tmp_path).model_copy(update={"secret_references": (reference,)})
    kata, _ = _backends(kata=True)
    with pytest.raises(SandboxPolicyError, match="defaults to no secrets"):
        kata.prepare(mission, SandboxPolicy())

    authorized = SandboxPolicy(
        allow_secret_references=True,
        secret_authorization_reference="secret-review-1",
    )
    with pytest.raises(SandboxPolicyError, match="materializer"):
        kata.prepare(mission, authorized)


def test_measurement_does_not_fabricate_kata_security_evidence():
    measurement = unavailable_backend_measurement(
        backend="kata",
        reason="Kata runtime is not installed",
    )
    assert isinstance(measurement, BackendMeasurement)
    assert measurement.status == "unavailable"
    assert measurement.startup_ms is None
    assert all(value is None for value in measurement.isolation_attestations.values())
    assert set(measurement.isolation_attestations) == set(adversarial_probe_names())


class _Lease:
    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True


def test_runner_cleans_up_and_hashes_only_sanitized_process_outputs(tmp_path: Path):
    kata, _ = _backends(kata=True)
    mission = _mission(tmp_path)
    prepared = kata.prepare(mission, SandboxPolicy())
    spec = prepared.model_copy(
        update={"command": (sys.executable, "-c", "print('bounded sandbox smoke')")}
    )
    lease = _Lease()

    result = SandboxProcessRunner(poll_seconds=0.01).execute(spec, gpu_lease=lease)

    assert result.exit_reason.value == "completed"
    assert result.exit_code == 0
    assert result.process_alive_after_cleanup is False
    assert result.stdout_sha256.startswith("sha256:")
    assert result.stderr_sha256.startswith("sha256:")
    assert lease.released
    assert result.provenance.output_artifact_hashes == ()
    assert result.provenance.credentials_loaded is False


def test_runner_does_not_leave_a_preflight_process_or_write_outside_output(tmp_path: Path):
    kata, _ = _backends(kata=True)
    mission = _mission(tmp_path)
    declared_output = mission.output_dir / "result"
    spec = kata.prepare(mission, SandboxPolicy()).model_copy(
        update={
            "command": (
                sys.executable,
                "-c",
                f"open({str(declared_output)!r}, 'w', encoding='utf-8').write('ok')",
            )
        }
    )

    # The synthetic command writes only to the declared output directory; the
    # generated spec still has no host repository or credential mount.
    result = SandboxProcessRunner(poll_seconds=0.01).execute(spec)
    assert result.process_alive_after_cleanup is False
    assert not (tmp_path / "result").exists()
    assert declared_output.read_text(encoding="utf-8") == "ok"


def test_gpu_is_denied_without_a_separate_reviewed_capability(tmp_path: Path):
    mission = _mission(tmp_path)
    with pytest.raises(ValueError, match="GPU access"):
        SandboxMission.model_validate(
            {
                **mission.model_dump(),
                "resources": ResourceLimits(
                    cpu_cores=1,
                    memory_mib=256,
                    pid_limit=64,
                    output_mib=8,
                    wall_time_seconds=3,
                    gpu_access=True,
                ),
            }
        )
