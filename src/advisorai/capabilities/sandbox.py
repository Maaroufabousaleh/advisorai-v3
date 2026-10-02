"""Governed per-mission isolation backends for untrusted capabilities.

This module is intentionally an integration boundary, not a claim that every
host has Kata installed.  Runtime discovery is lazy and side-effect free.  A
mission is never silently downgraded from Kata to ordinary Docker, and an
untrusted-native mission cannot run when the Kata runtime is unavailable.

The command builder does not pull images, acquire secrets, contact a network,
or mutate a repository.  Actual execution is explicit through
``SandboxProcessRunner`` and is still subject to the immutable mission and
policy checks in this module.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MISSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SECRET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_NETWORK_NAME_RE = re.compile(r"^advisorai-egress-[a-z0-9-]{1,64}$")
_FORBIDDEN_SECRET_TOKENS = frozenset(
    {
        "broker",
        "exchange",
        "execution",
        "live",
        "order",
        "private_key",
        "trade",
        "transfer",
        "withdraw",
    }
)
_SENSITIVE_HOSTS = frozenset(
    {
        "169.254.169.254",
        "100.100.100.200",
        "host.docker.internal",
        "instance-data",
        "instance-data.ec2.internal",
        "localhost",
        "metadata.google.internal",
    }
)
_BROKER_HOST_TOKENS = frozenset(
    {"binance", "coinbase", "kraken", "okx", "bybit", "exchange", "broker", "venue"}
)
_SENSITIVE_MOUNT_PATHS = frozenset(
    {
        "/dev",
        "/etc/shadow",
        "/etc/ssh",
        "/mnt/c",
        "/proc",
        "/run/containerd/containerd.sock",
        "/run/docker.sock",
        "/sys",
        "/var/run/containerd/containerd.sock",
        "/var/run/docker.sock",
    }
)


def _require_sha256(value: str, info: object) -> str:
    if not _SHA256_RE.fullmatch(value):
        field = getattr(info, "field_name", "digest")
        raise ValueError(f"{field} must be sha256:<64 lowercase hexadecimal characters>")
    return value


def _normalize_unique(values: tuple[str, ...], *, field: str) -> tuple[str, ...]:
    normalized = tuple(value.strip() for value in values)
    if any(not value for value in normalized):
        raise ValueError(f"{field} entries must be non-blank")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} entries must be unique")
    return normalized


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _safe_resolve(path: Path) -> Path:
    """Resolve a path without requiring it to exist.

    ``strict=False`` is important for a task output directory that will be
    created by the caller, while still resolving symlinked parents before a
    mount is admitted.
    """

    return path.expanduser().resolve(strict=False)


class SandboxError(RuntimeError):
    """Base class for fail-closed sandbox errors."""


class SandboxPolicyError(SandboxError, ValueError):
    """A mission or fallback violates the declared sandbox policy."""


class SandboxUnavailable(SandboxError):
    """The requested isolation backend is not available on this host."""


class SandboxExecutionError(SandboxError):
    """A sandbox process could not be started or cleaned up safely."""


class TrustClass(StrEnum):
    TRUSTED_CORE = "trusted_core"
    TRUSTED_RESEARCH = "trusted_research"
    UNTRUSTED_RESEARCH = "untrusted_research"
    UNTRUSTED_NATIVE = "untrusted_native"
    BLOCKED_EXECUTION = "blocked_execution"


class SandboxBackendKind(StrEnum):
    KATA = "kata"
    HARDENED_DOCKER = "hardened_docker"


class NetworkMode(StrEnum):
    NONE = "none"
    ALLOWLIST = "allowlist"


class DestinationClass(StrEnum):
    PUBLIC_RESEARCH = "public_research"
    BROKER_READ_ONLY = "broker_read_only"


class MountKind(StrEnum):
    INPUT = "input"
    DATASET = "dataset"
    OUTPUT = "output"
    SECRET = "secret"


class SandboxExitReason(StrEnum):
    COMPLETED = "completed"
    NONZERO_EXIT = "nonzero_exit"
    WALL_TIME_LIMIT = "wall_time_limit"
    OUTPUT_QUOTA = "output_quota"
    START_FAILURE = "start_failure"
    CLEANUP_FAILURE = "cleanup_failure"
    BACKEND_FAILURE = "backend_failure"


class ResourceLimits(BaseModel):
    """Hard limits attached to every mission."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cpu_cores: float = Field(gt=0, le=64)
    memory_mib: int = Field(gt=0, le=1_048_576)
    pid_limit: int = Field(gt=0, le=32_768)
    output_mib: int = Field(gt=0, le=1_048_576)
    wall_time_seconds: int = Field(gt=0, le=7 * 24 * 60 * 60)
    gpu_access: bool = False

    @field_validator("cpu_cores")
    @classmethod
    def require_finite_cpu(cls, value: float) -> float:
        if not value == value or value in {float("inf"), float("-inf")}:
            raise ValueError("cpu_cores must be finite")
        return value


class NetworkPolicy(BaseModel):
    """Default-deny egress policy with explicit destination restrictions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: NetworkMode = NetworkMode.NONE
    allowed_hosts: tuple[str, ...] = ()
    destination_classes: tuple[DestinationClass, ...] = ()
    broker_read_only: bool = False
    enforcer_network: str | None = None

    @field_validator("allowed_hosts")
    @classmethod
    def validate_hosts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip().lower().rstrip(".") for value in values)
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("network hosts must be unique and non-blank")
        for host in normalized:
            if host == "*" or host.startswith("*.") or "*" in host:
                raise ValueError("network hosts must be explicit; wildcards are forbidden")
            if host in _SENSITIVE_HOSTS or host.endswith(".local"):
                raise ValueError("metadata, host, and local-network destinations are forbidden")
            if host.endswith((".internal", ".lan", ".home.arpa")):
                raise ValueError("internal and local-network destinations are forbidden")
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                continue
            if (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_reserved
                or address.is_unspecified
            ):
                raise ValueError("private, metadata, or local IP destinations are forbidden")
        return normalized

    @field_validator("destination_classes")
    @classmethod
    def validate_destination_classes(
        cls, values: tuple[DestinationClass, ...]
    ) -> tuple[DestinationClass, ...]:
        if len(values) != len(set(values)):
            raise ValueError("destination classes must be unique")
        return values

    @field_validator("enforcer_network")
    @classmethod
    def validate_enforcer_network(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not _NETWORK_NAME_RE.fullmatch(normalized):
            raise ValueError("network enforcers must use an explicit AdvisorAI network name")
        return normalized

    @model_validator(mode="after")
    def validate_network_contract(self) -> NetworkPolicy:
        if self.mode is NetworkMode.NONE:
            if self.allowed_hosts or self.destination_classes or self.broker_read_only:
                raise ValueError("network-none policy cannot contain egress permissions")
            if self.enforcer_network is not None:
                raise ValueError("network-none policy cannot name an egress enforcer")
            return self
        if not self.allowed_hosts:
            raise ValueError("allowlist network policy requires explicit hosts")
        if not self.destination_classes:
            raise ValueError("allowlist network policy requires destination classes")
        if self.enforcer_network is None:
            raise ValueError("allowlist network policy requires a reviewed egress enforcer")
        if not self.broker_read_only and any(
            any(token in host.split(".") for token in _BROKER_HOST_TOKENS)
            for host in self.allowed_hosts
        ):
            raise ValueError("broker or exchange destinations require explicit read-only policy")
        if (
            DestinationClass.BROKER_READ_ONLY in self.destination_classes
            and not self.broker_read_only
        ):
            raise ValueError("broker destinations require an explicit read-only flag")
        if (
            self.broker_read_only
            and DestinationClass.BROKER_READ_ONLY not in self.destination_classes
        ):
            raise ValueError("broker_read_only requires the broker destination class")
        return self

    @property
    def identity(self) -> str:
        return _canonical_hash(self.model_dump(mode="json"))


class SecretReference(BaseModel):
    """A name-only secret request; secret values never enter this model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    purpose: str = Field(min_length=1)
    allowed_destinations: tuple[str, ...]
    lifetime_seconds: int = Field(gt=0, le=24 * 60 * 60)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not _SECRET_NAME_RE.fullmatch(normalized):
            raise ValueError("secret reference names must be stable non-secret identifiers")
        tokens = set(re.split(r"[_.-]+", normalized.lower()))
        if tokens.intersection(_FORBIDDEN_SECRET_TOKENS):
            raise ValueError("broker, execution, and transfer secrets are forbidden")
        return normalized

    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, value: str) -> str:
        normalized = value.strip()
        tokens = set(re.findall(r"[a-z0-9]+", normalized.lower()))
        if tokens.intersection(_FORBIDDEN_SECRET_TOKENS):
            raise ValueError("secret purpose cannot authorize trading or transfer activity")
        return normalized

    @field_validator("allowed_destinations")
    @classmethod
    def validate_destinations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = _normalize_unique(values, field="secret destinations")
        for destination in normalized:
            host = destination.lower().rstrip(".")
            if host in _SENSITIVE_HOSTS or host.endswith(
                (".local", ".internal", ".lan", ".home.arpa")
            ):
                raise ValueError("secret destinations may not be local or metadata endpoints")
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address is not None and (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_reserved
                or address.is_unspecified
            ):
                raise ValueError("secret destinations may not be private or local IPs")
            if any(token in host.split(".") for token in _BROKER_HOST_TOKENS):
                raise ValueError("secret destinations may not be broker or exchange endpoints")
        return normalized


class SandboxMount(BaseModel):
    """One explicit host-to-guest mount."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Path
    target: str
    kind: MountKind
    read_only: bool = True

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("sandbox mount sources must be absolute host paths")
        return value

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        normalized = os.path.normpath(value.strip())
        if (
            not normalized.startswith("/")
            or normalized == "/"
            or "\x00" in normalized
            or "," in normalized
        ):
            raise ValueError("sandbox mount targets must be non-root absolute paths")
        if any(part == ".." for part in Path(normalized).parts):
            raise ValueError("sandbox mount targets cannot traverse parents")
        return normalized

    @model_validator(mode="after")
    def validate_mount_contract(self) -> SandboxMount:
        if (
            self.kind in {MountKind.INPUT, MountKind.DATASET, MountKind.SECRET}
            and not self.read_only
        ):
            raise ValueError("input, dataset, and secret mounts must be read-only")
        if self.kind is MountKind.OUTPUT and self.read_only:
            raise ValueError("output mount must be writable")
        if self.kind is MountKind.SECRET and not self.target.startswith("/run/secrets/"):
            raise ValueError("secret mounts must live under /run/secrets")
        return self


class SandboxAuthorities(BaseModel):
    """Explicitly immutable authority denial attached to a mission."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    order_authority: bool = False
    risk_kernel_authority: bool = False
    oms_authority: bool = False
    live_repository_write: bool = False
    self_promotion: bool = False

    @model_validator(mode="after")
    def deny_all_authority(self) -> SandboxAuthorities:
        if any(self.model_dump().values()):
            raise SandboxPolicyError("sandbox missions cannot receive AdvisorAI authority")
        return self


class SandboxMission(BaseModel):
    """Immutable input needed to create one mission-scoped sandbox."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mission_id: str
    workload_type: str = Field(min_length=1)
    trust_class: TrustClass
    image: str = Field(min_length=1)
    image_digest: str
    rootfs_digest: str
    task_command: tuple[str, ...]
    task_root: Path
    input_mounts: tuple[SandboxMount, ...] = ()
    dataset_mounts: tuple[SandboxMount, ...] = ()
    output_dir: Path
    resources: ResourceLimits
    network: NetworkPolicy = NetworkPolicy()
    secret_references: tuple[SecretReference, ...] = ()
    capability_version: str = "unassigned"
    advisorai_commit: str
    dependency_lock_hash: str
    dataset_revisions: tuple[str, ...] = ()
    model_revisions: tuple[str, ...] = ()
    input_artifact_hashes: tuple[str, ...] = ()
    authorities: SandboxAuthorities = SandboxAuthorities()
    gpu_capability_reviewed: bool = False

    @field_validator("mission_id")
    @classmethod
    def validate_mission_id(cls, value: str) -> str:
        normalized = value.strip()
        if not _MISSION_ID_RE.fullmatch(normalized):
            raise ValueError("mission_id must be a stable non-blank identifier")
        return normalized

    @field_validator("image")
    @classmethod
    def validate_image(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not normalized
            or any(character.isspace() for character in normalized)
            or "\x00" in normalized
        ):
            raise ValueError("sandbox image must be one immutable image reference")
        return normalized

    @field_validator("task_command")
    @classmethod
    def validate_command(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values or any(not value or "\x00" in value for value in values):
            raise ValueError("task command must be a non-empty argv vector")
        return values

    @field_validator("task_root", "output_dir")
    @classmethod
    def validate_paths(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("mission paths must be absolute")
        return value

    @field_validator("image_digest", "rootfs_digest", "advisorai_commit", "dependency_lock_hash")
    @classmethod
    def validate_digests(cls, value: str, info: object) -> str:
        return _require_sha256(value, info)

    @field_validator("dataset_revisions", "model_revisions")
    @classmethod
    def validate_revisions(cls, values: tuple[str, ...], info: object) -> tuple[str, ...]:
        return _normalize_unique(values, field=getattr(info, "field_name", "revisions"))

    @field_validator("input_artifact_hashes")
    @classmethod
    def validate_artifact_hashes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            _require_sha256(
                value, type("DigestInfo", (), {"field_name": "input_artifact_hashes"})()
            )
        return _normalize_unique(values, field="input artifact hashes")

    @model_validator(mode="after")
    def validate_mission_contract(self) -> SandboxMission:
        if not self.image.endswith(f"@{self.image_digest}"):
            raise ValueError("sandbox image must be pinned by the declared digest")
        task_root = _safe_resolve(self.task_root)
        output_dir = _safe_resolve(self.output_dir)
        if output_dir != task_root and task_root not in output_dir.parents:
            raise ValueError("output_dir must be inside the mission task_root")
        if self.trust_class in {TrustClass.TRUSTED_CORE, TrustClass.BLOCKED_EXECUTION}:
            raise SandboxPolicyError("core and blocked workloads cannot use this sandbox mission")
        if self.resources.gpu_access and not self.gpu_capability_reviewed:
            raise SandboxPolicyError("GPU access requires a separate reviewed capability")
        if any(mount.kind is not MountKind.INPUT for mount in self.input_mounts):
            raise ValueError("input_mounts must contain only input mounts")
        if any(mount.kind is not MountKind.DATASET for mount in self.dataset_mounts):
            raise ValueError("dataset_mounts must contain only dataset mounts")
        mount_targets = [mount.target for mount in (*self.input_mounts, *self.dataset_mounts)]
        if len(mount_targets) != len(set(mount_targets)):
            raise ValueError("mission mount targets must be unique")
        if any(
            mount.kind is MountKind.OUTPUT for mount in (*self.input_mounts, *self.dataset_mounts)
        ):
            raise ValueError("output mounts must be supplied only through output_dir")
        secret_names = [reference.name for reference in self.secret_references]
        if len(secret_names) != len(set(secret_names)):
            raise ValueError("secret references must be unique")
        return self


class SandboxPolicy(BaseModel):
    """Selection and host-scope policy; fallback must be named and authorized."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_backend: SandboxBackendKind | None = None
    allow_docker_fallback: bool = False
    fallback_authorization_reference: str | None = None
    repository_root: Path | None = None
    host_home: Path | None = None
    allowed_host_roots: tuple[Path, ...] = ()
    allow_secret_references: bool = False
    secret_authorization_reference: str | None = None
    allow_gpu: bool = False

    @model_validator(mode="after")
    def validate_policy(self) -> SandboxPolicy:
        if self.allow_docker_fallback and not (self.fallback_authorization_reference or "").strip():
            raise ValueError("Docker fallback requires a named policy authorization")
        if self.allow_secret_references and not (self.secret_authorization_reference or "").strip():
            raise ValueError("secret references require a named policy authorization")
        if self.allow_gpu:
            raise ValueError("GPU access is not enabled by the default sandbox policy")
        if any(not path.is_absolute() for path in self.allowed_host_roots):
            raise ValueError("allowed host roots must be absolute")
        return self

    @property
    def identity(self) -> str:
        return _canonical_hash(self.model_dump(mode="json"))


class HostCapabilityReport(BaseModel):
    """Read-only host capability observation; it is not Kata security evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    measured_at: datetime
    virtualization_backend: str
    kvm_available: bool
    mshv_available: bool
    nested_virt_available: bool
    vsock_available: bool
    vhost_net_available: bool
    containerd_available: bool
    docker_available: bool
    kata_runtime_available: bool
    kata_runtime: str | None = None
    kata_host_feasible: bool
    limitations: tuple[str, ...] = ()

    @field_validator("measured_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("host capability timestamp must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_feasibility(self) -> HostCapabilityReport:
        prerequisites = (
            self.kvm_available
            and self.nested_virt_available
            and self.vsock_available
            and (self.containerd_available or self.docker_available)
        )
        if self.kata_host_feasible and not (prerequisites and self.kata_runtime_available):
            raise ValueError("KATA_HOST_FEASIBLE cannot be true without runtime prerequisites")
        return self

    @property
    def host_prerequisites_satisfied(self) -> bool:
        return (
            self.kvm_available
            and self.nested_virt_available
            and self.vsock_available
            and (self.containerd_available or self.docker_available)
        )


CommandProbe = Callable[[Sequence[str]], tuple[int, str]]


def _default_command_probe(command: Sequence[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"},
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return 127, ""
    return completed.returncode, (completed.stdout or "").strip()


def _path_available(path: str) -> bool:
    return Path(path).exists()


def _read_text(path: str) -> str:
    with suppress(OSError):
        return Path(path).read_text(encoding="utf-8", errors="replace")
    return ""


def _docker_runtime_names(command_probe: CommandProbe) -> tuple[str, ...]:
    code, output = command_probe(("docker", "info", "--format", "{{json .Runtimes}}"))
    if code != 0 or not output:
        return ()
    try:
        decoded = json.loads(output)
    except json.JSONDecodeError:
        return ()
    if not isinstance(decoded, dict):
        return ()
    return tuple(sorted(str(name) for name in decoded if "kata" in str(name).lower()))


def probe_host_capabilities(
    *,
    command_probe: CommandProbe | None = None,
    path_exists: Callable[[str], bool] = _path_available,
) -> HostCapabilityReport:
    """Measure local Kata prerequisites without installing or starting anything."""

    probe = command_probe or _default_command_probe
    virt_code, virt_output = probe(("systemd-detect-virt",))
    virtualization_backend = virt_output[:80] if virt_code == 0 and virt_output else "unknown"
    cpuinfo = _read_text("/proc/cpuinfo").lower()
    nested = _read_text("/sys/module/kvm_intel/parameters/nested").strip().lower()
    nested_amd = _read_text("/sys/module/kvm_amd/parameters/nested").strip().lower()
    nested_virt = bool(re.search(r"\b(vmx|svm)\b", cpuinfo)) and (
        nested in {"y", "1"} or nested_amd in {"y", "1"}
    )
    docker_available = bool(shutil.which("docker")) and probe(("docker", "info"))[0] == 0
    containerd_available = bool(shutil.which("containerd")) or bool(shutil.which("ctr"))
    kata_executable = shutil.which("kata-runtime")
    kata_runtime_names = _docker_runtime_names(probe) if docker_available else ()
    kata_runtime = (
        Path(kata_executable).name
        if kata_executable
        else (kata_runtime_names[0] if kata_runtime_names else None)
    )
    kata_runtime_available = kata_runtime is not None
    kvm = path_exists("/dev/kvm")
    mshv = path_exists("/dev/mshv")
    vsock = path_exists("/dev/vhost-vsock")
    vhost_net = path_exists("/dev/vhost-net")
    prerequisites = kvm and nested_virt and vsock and (containerd_available or docker_available)
    limitations: list[str] = []
    if not kata_runtime_available:
        limitations.append("Kata runtime is not installed or registered with Docker")
    if not mshv:
        limitations.append("/dev/mshv is absent; only a KVM-backed path can be considered")
    if virtualization_backend == "unknown":
        limitations.append("virtualization backend command was unavailable")
    if not prerequisites:
        limitations.append(
            "one or more KVM, nested-virtualization, vsock, or containerd prerequisites are absent"
        )
    return HostCapabilityReport(
        measured_at=datetime.now(UTC),
        virtualization_backend=virtualization_backend,
        kvm_available=kvm,
        mshv_available=mshv,
        nested_virt_available=nested_virt,
        vsock_available=vsock,
        vhost_net_available=vhost_net,
        containerd_available=containerd_available,
        docker_available=docker_available,
        kata_runtime_available=kata_runtime_available,
        kata_runtime=kata_runtime,
        kata_host_feasible=prerequisites and kata_runtime_available,
        limitations=tuple(limitations),
    )


class SecretMaterializer(Protocol):
    """Materialize individual task-scoped secret files without exposing values."""

    def materialize(
        self, references: tuple[SecretReference, ...], staging_root: Path
    ) -> tuple[SandboxMount, ...]: ...


class GpuLease(Protocol):
    """Minimal release hook for a separately reviewed GPU lease."""

    def release(self) -> None: ...


class SandboxProvenance(BaseModel):
    """Machine-readable provenance for a prepared or completed mission."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sandbox_backend: SandboxBackendKind
    kata_version: str | None
    runtime_rs_version: str | None
    hypervisor_backend: str | None
    guest_kernel_identity: str | None
    guest_image_digest: str
    rootfs_digest: str
    task_overlay_identity: str
    advisorai_commit: str
    dependency_lock_hash: str
    capability_version: str
    dataset_revisions: tuple[str, ...]
    model_revisions: tuple[str, ...]
    input_artifact_hashes: tuple[str, ...]
    output_artifact_hashes: tuple[str, ...] = ()
    resource_limits: ResourceLimits
    network_policy_hash: str
    secret_reference_names: tuple[str, ...] = ()
    started_at: datetime | None = None
    ended_at: datetime | None = None
    exit_reason: SandboxExitReason | None = None
    resource_usage: Mapping[str, int | float | str | None] = {}
    credentials_loaded: bool = False
    order_writes_attempted: bool = False
    execution_authority_present: bool = False

    _guest_image_digest = field_validator(
        "guest_image_digest", "rootfs_digest", "advisorai_commit", "dependency_lock_hash"
    )(_require_sha256)
    _network_policy_hash = field_validator("network_policy_hash")(_require_sha256)

    @model_validator(mode="after")
    def deny_authority_and_validate_times(self) -> SandboxProvenance:
        if (
            self.credentials_loaded
            or self.order_writes_attempted
            or self.execution_authority_present
        ):
            raise SandboxPolicyError(
                "sandbox provenance cannot attest credentials or execution authority"
            )
        if self.started_at is not None and (
            self.started_at.tzinfo is None or self.started_at.utcoffset() is None
        ):
            raise ValueError("started_at must be timezone-aware")
        if self.ended_at is not None and (
            self.ended_at.tzinfo is None or self.ended_at.utcoffset() is None
        ):
            raise ValueError("ended_at must be timezone-aware")
        if self.started_at and self.ended_at and self.ended_at < self.started_at:
            raise ValueError("ended_at cannot precede started_at")
        return self


class SandboxLaunchSpec(BaseModel):
    """Fully rendered launch request; no implicit host state is inherited."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: SandboxBackendKind
    trust_class: TrustClass
    fallback_authorization: str | None = None
    mission_id: str
    command: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    working_directory: Path
    output_directory: Path
    wall_time_seconds: int
    output_quota_bytes: int
    network_policy_hash: str
    policy_hash: str
    provenance: SandboxProvenance

    @model_validator(mode="after")
    def validate_launch_spec(self) -> SandboxLaunchSpec:
        if not self.command or any("\x00" in item for item in self.command):
            raise ValueError("launch command must be a non-empty argv vector")
        keys = [key for key, _ in self.environment]
        if len(keys) != len(set(keys)):
            raise ValueError("launch environment keys must be unique")
        allowed_keys = {
            "ADVISORAI_MISSION_ID",
            "ADVISORAI_NETWORK_POLICY_HASH",
            "ADVISORAI_SANDBOX_BACKEND",
            "HOME",
            "PATH",
            "PYTHONNOUSERSITE",
        }
        if any(key not in allowed_keys for key in keys):
            raise SandboxPolicyError("launch environment cannot contain secret values")
        if self.trust_class in {TrustClass.TRUSTED_CORE, TrustClass.BLOCKED_EXECUTION}:
            raise SandboxPolicyError("launch spec cannot execute core or blocked workloads")
        if self.backend is SandboxBackendKind.HARDENED_DOCKER:
            if self.trust_class is TrustClass.UNTRUSTED_NATIVE:
                raise SandboxPolicyError("UNTRUSTED_NATIVE launch specs require Kata")
            if (
                self.trust_class is TrustClass.UNTRUSTED_RESEARCH
                and not (self.fallback_authorization or "").strip()
            ):
                raise SandboxPolicyError(
                    "UNTRUSTED_RESEARCH Docker launch requires fallback authorization"
                )
        if self.provenance.sandbox_backend is not self.backend:
            raise ValueError("launch and provenance backend identities must match")
        return self


class SandboxRunResult(BaseModel):
    """Sanitized completion result; stdout/stderr payloads are never persisted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mission_id: str
    backend: SandboxBackendKind
    exit_code: int | None
    exit_reason: SandboxExitReason
    started_at: datetime
    ended_at: datetime
    process_alive_after_cleanup: bool
    output_bytes: int
    stdout_sha256: str
    stderr_sha256: str
    provenance: SandboxProvenance

    @model_validator(mode="after")
    def require_cleanup(self) -> SandboxRunResult:
        if self.process_alive_after_cleanup:
            raise SandboxExecutionError("sandbox process survived cleanup")
        return self


class BackendAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: SandboxBackendKind
    available: bool
    reason: str
    runtime: str | None = None
    host_report: HostCapabilityReport


class SandboxBackendSelectionRecord(BaseModel):
    """Serializable audit record for every policy backend decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_type: str = "advisorai.sandbox.backend-selection.v1"
    trust_class: TrustClass
    requested_backend: SandboxBackendKind | None
    selected_backend: SandboxBackendKind
    fallback_used: bool
    fallback_reason: str | None
    policy_authorization: str | None
    policy_hash: str

    _policy_hash = field_validator("policy_hash")(_require_sha256)


class SandboxBackend(ABC):
    """Typed backend seam.  Implementations must not add authority."""

    kind: SandboxBackendKind

    @abstractmethod
    def availability(self) -> BackendAvailability:
        raise NotImplementedError

    @abstractmethod
    def prepare(self, mission: SandboxMission, policy: SandboxPolicy) -> SandboxLaunchSpec:
        raise NotImplementedError


def _path_is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _validate_host_path(path: Path, *, policy: SandboxPolicy, purpose: str) -> Path:
    resolved = _safe_resolve(path)
    denied_roots = [
        Path("/boot"),
        Path("/dev"),
        Path("/etc"),
        Path("/lib"),
        Path("/lib64"),
        Path("/mnt/c"),
        Path("/opt"),
        Path("/proc"),
        Path("/root"),
        Path("/run"),
        Path("/sbin"),
        Path("/sys"),
        Path("/usr"),
        Path("/var/run"),
    ]
    if policy.host_home is not None:
        denied_roots.append(_safe_resolve(policy.host_home))
    else:
        denied_roots.append(_safe_resolve(Path.home()))
    if policy.repository_root is not None:
        denied_roots.append(_safe_resolve(policy.repository_root))
    if resolved == Path("/") or any(_path_is_within(resolved, root) for root in denied_roots):
        raise SandboxPolicyError(f"{purpose} is outside the allowed mission scope")
    allowed_roots = tuple(
        _safe_resolve(root)
        for root in (policy.allowed_host_roots or (Path("/tmp"), Path("/var/tmp")))
    )
    if not any(_path_is_within(resolved, root) for root in allowed_roots):
        raise SandboxPolicyError(f"{purpose} is outside the allowed mission scope")
    if "," in str(resolved):
        raise SandboxPolicyError(f"{purpose} contains a Docker mount delimiter")
    if resolved in {Path(item) for item in _SENSITIVE_MOUNT_PATHS}:
        raise SandboxPolicyError(f"{purpose} targets a forbidden host socket or system path")
    if resolved.name in {"secrets.env", ".env", "credentials.env"}:
        raise SandboxPolicyError(f"{purpose} may not mount a secret environment file")
    return resolved


def _validate_mission_paths(mission: SandboxMission, policy: SandboxPolicy) -> tuple[Path, Path]:
    task_root = _validate_host_path(mission.task_root, policy=policy, purpose="task root")
    output_dir = _validate_host_path(mission.output_dir, policy=policy, purpose="output directory")
    if not task_root.is_dir():
        raise SandboxPolicyError("task root must already be a directory")
    if not output_dir.is_dir():
        raise SandboxPolicyError("output directory must already be a directory")
    if output_dir != task_root and task_root not in output_dir.parents:
        raise SandboxPolicyError("output directory must remain inside the task root")
    for mount in (*mission.input_mounts, *mission.dataset_mounts):
        source = _validate_host_path(mount.source, policy=policy, purpose=f"{mount.kind} mount")
        if not source.exists():
            raise SandboxPolicyError(f"{mount.kind} mount source must exist")
    return task_root, output_dir


def _format_cpu(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _artifact_hashes(root: Path) -> tuple[str, ...]:
    """Hash a task output tree deterministically without following symlinks."""

    if not root.exists():
        return ()
    entries: list[bytes] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix().encode()
        if path.is_symlink():
            entries.append(b"L\0" + relative + b"\0" + os.readlink(path).encode())
            continue
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest().encode()
            entries.append(b"F\0" + relative + b"\0" + digest)
        elif path.is_dir():
            entries.append(b"D\0" + relative)
    if not entries:
        return ()
    manifest = b"\n".join(entries)
    return (f"sha256:{hashlib.sha256(manifest).hexdigest()}",)


def _new_provenance(
    mission: SandboxMission,
    *,
    backend: SandboxBackendKind,
    policy: SandboxPolicy,
    runtime_identity: Mapping[str, str | None],
) -> SandboxProvenance:
    overlay_identity = _canonical_hash(
        {
            "mission_id": mission.mission_id,
            "task_root": str(_safe_resolve(mission.task_root)),
            "output_dir": str(_safe_resolve(mission.output_dir)),
        }
    )
    return SandboxProvenance(
        sandbox_backend=backend,
        kata_version=runtime_identity.get("kata_version"),
        runtime_rs_version=runtime_identity.get("runtime_rs_version"),
        hypervisor_backend=runtime_identity.get("hypervisor_backend"),
        guest_kernel_identity=runtime_identity.get("guest_kernel_identity"),
        guest_image_digest=mission.image_digest,
        rootfs_digest=mission.rootfs_digest,
        task_overlay_identity=overlay_identity,
        advisorai_commit=mission.advisorai_commit,
        dependency_lock_hash=mission.dependency_lock_hash,
        capability_version=mission.capability_version,
        dataset_revisions=mission.dataset_revisions,
        model_revisions=mission.model_revisions,
        input_artifact_hashes=mission.input_artifact_hashes,
        resource_limits=mission.resources,
        network_policy_hash=mission.network.identity,
        secret_reference_names=tuple(reference.name for reference in mission.secret_references),
    )


class _DockerSandboxBackend(SandboxBackend):
    """Common command construction for Docker-runc and Docker-Kata runtimes."""

    def __init__(
        self,
        *,
        runtime: str,
        host_report: HostCapabilityReport | None = None,
        command_probe: CommandProbe | None = None,
        docker_binary: str = "docker",
        secret_materializer: SecretMaterializer | None = None,
        runtime_identity: Mapping[str, str | None] | None = None,
    ) -> None:
        if (
            not runtime.strip()
            or any(character.isspace() for character in runtime)
            or "\x00" in runtime
            or runtime.lower().startswith("kata")
            and self.kind is SandboxBackendKind.HARDENED_DOCKER
        ):
            raise ValueError("backend runtime name is invalid")
        self.runtime = runtime.strip()
        self._host_report = host_report
        self._command_probe = command_probe or _default_command_probe
        self.docker_binary = docker_binary
        self.secret_materializer = secret_materializer
        self.runtime_identity = dict(runtime_identity or {})

    def _host(self) -> HostCapabilityReport:
        return self._host_report or probe_host_capabilities(command_probe=self._command_probe)

    def _validate_backend_compatibility(
        self, mission: SandboxMission, policy: SandboxPolicy
    ) -> None:
        if mission.trust_class in {TrustClass.TRUSTED_CORE, TrustClass.BLOCKED_EXECUTION}:
            raise SandboxPolicyError(
                f"execution is prohibited for trust class {mission.trust_class.value}"
            )
        if self.kind is SandboxBackendKind.KATA:
            return
        if mission.trust_class is TrustClass.UNTRUSTED_NATIVE:
            raise SandboxPolicyError("UNTRUSTED_NATIVE requires Kata and cannot use Docker")
        if (
            mission.trust_class is TrustClass.UNTRUSTED_RESEARCH
            and not policy.allow_docker_fallback
        ):
            raise SandboxPolicyError(
                "UNTRUSTED_RESEARCH Docker fallback requires explicit policy authorization"
            )

    def _secret_mounts(
        self,
        mission: SandboxMission,
        policy: SandboxPolicy,
        task_root: Path,
    ) -> tuple[SandboxMount, ...]:
        if not mission.secret_references:
            return ()
        if not policy.allow_secret_references:
            raise SandboxPolicyError("mission requests secrets but policy defaults to no secrets")
        if self.secret_materializer is None:
            raise SandboxPolicyError("no reviewed task-scoped secret materializer is configured")
        staging_root = task_root / ".secret-staging"
        mounts = self.secret_materializer.materialize(mission.secret_references, staging_root)
        expected = {reference.name for reference in mission.secret_references}
        if {Path(mount.target).name for mount in mounts} != expected:
            raise SandboxPolicyError("secret materializer returned an unexpected reference set")
        for mount in mounts:
            if mount.kind is not MountKind.SECRET or not mount.read_only:
                raise SandboxPolicyError("secret materializer must return read-only secret mounts")
            source = _validate_host_path(mount.source, policy=policy, purpose="secret mount")
            if not source.is_file():
                raise SandboxPolicyError("secret materializer must provide one file per reference")
            if staging_root not in source.parents:
                raise SandboxPolicyError("secret mount must remain in the task-scoped staging root")
            if source.name in {"secrets.env", ".env", "credentials.env"}:
                raise SandboxPolicyError("secret environment files are never mounted")
        return mounts

    def _prepare_common(
        self,
        mission: SandboxMission,
        policy: SandboxPolicy,
        *,
        runtime: str,
        backend: SandboxBackendKind,
    ) -> SandboxLaunchSpec:
        task_root, output_dir = _validate_mission_paths(mission, policy)
        if mission.resources.gpu_access and not policy.allow_gpu:
            raise SandboxPolicyError("GPU access is disabled by the governing policy")
        secret_mounts = self._secret_mounts(mission, policy, task_root)
        mounts = (*mission.input_mounts, *mission.dataset_mounts, *secret_mounts)
        targets = [mount.target for mount in mounts]
        if len(targets) != len(set(targets)):
            raise SandboxPolicyError("mission mounts contain duplicate guest targets")
        command: list[str] = [
            self.docker_binary,
            "run",
            "--pull=never",
            "--rm",
            "--init",
            "--runtime",
            runtime,
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--ipc=private",
            "--user",
            "65532:65532",
            "--pids-limit",
            str(mission.resources.pid_limit),
            "--memory",
            f"{mission.resources.memory_mib}m",
            "--cpus",
            _format_cpu(mission.resources.cpu_cores),
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--workdir",
            "/task",
        ]
        if mission.network.mode is NetworkMode.NONE:
            command.extend(("--network", "none"))
        else:
            command.extend(("--network", mission.network.enforcer_network or ""))
        for mount in mounts:
            source = _validate_host_path(mount.source, policy=policy, purpose=f"{mount.kind} mount")
            read_only = ",readonly" if mount.read_only else ""
            command.extend(
                (
                    "--mount",
                    f"type=bind,src={source},dst={mount.target}{read_only}",
                )
            )
        output_mount = _validate_host_path(output_dir, policy=policy, purpose="output directory")
        environment = (
            ("ADVISORAI_MISSION_ID", mission.mission_id),
            ("ADVISORAI_SANDBOX_BACKEND", backend.value),
            ("ADVISORAI_NETWORK_POLICY_HASH", mission.network.identity),
            ("HOME", "/nonexistent"),
            ("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
            ("PYTHONNOUSERSITE", "1"),
        )
        for key, value in environment:
            command.extend(("--env", f"{key}={value}"))
        command.extend(("--mount", f"type=bind,src={output_mount},dst=/task/output"))
        command.append(mission.image)
        command.extend(mission.task_command)
        provenance = _new_provenance(
            mission,
            backend=backend,
            policy=policy,
            runtime_identity=self.runtime_identity,
        )
        return SandboxLaunchSpec(
            backend=backend,
            trust_class=mission.trust_class,
            fallback_authorization=(
                policy.fallback_authorization_reference
                if backend is SandboxBackendKind.HARDENED_DOCKER
                and mission.trust_class is TrustClass.UNTRUSTED_RESEARCH
                else None
            ),
            mission_id=mission.mission_id,
            command=tuple(command),
            environment=environment,
            working_directory=task_root,
            output_directory=output_dir,
            wall_time_seconds=mission.resources.wall_time_seconds,
            output_quota_bytes=mission.resources.output_mib * 1024 * 1024,
            network_policy_hash=mission.network.identity,
            policy_hash=policy.identity,
            provenance=provenance,
        )


class KataSandboxBackend(_DockerSandboxBackend):
    """Kata Containers through a registered Docker runtime, with no fallback."""

    kind = SandboxBackendKind.KATA

    def __init__(
        self,
        *,
        runtime: str | None = None,
        host_report: HostCapabilityReport | None = None,
        command_probe: CommandProbe | None = None,
        docker_binary: str = "docker",
        secret_materializer: SecretMaterializer | None = None,
        runtime_identity: Mapping[str, str | None] | None = None,
    ) -> None:
        probe = command_probe or _default_command_probe
        discovered = runtime or os.environ.get("ADVISORAI_KATA_RUNTIME")
        if not discovered:
            names = _docker_runtime_names(probe)
            discovered = names[0] if names else None
        self._configured_runtime = discovered
        super().__init__(
            runtime=discovered or "kata-unavailable",
            host_report=host_report,
            command_probe=probe,
            docker_binary=docker_binary,
            secret_materializer=secret_materializer,
            runtime_identity=runtime_identity,
        )

    def availability(self) -> BackendAvailability:
        report = self._host()
        available = (
            bool(self._configured_runtime)
            and report.kata_runtime_available
            and report.host_prerequisites_satisfied
        )
        if not self._configured_runtime:
            reason = "Kata runtime is neither installed nor registered with Docker"
        elif not report.kata_runtime_available:
            reason = "configured Kata runtime is not attested by local runtime discovery"
        elif not report.host_prerequisites_satisfied:
            reason = "KVM, nested virtualization, vsock, or containerd prerequisites are incomplete"
        else:
            reason = "Kata runtime and host prerequisites are available"
        return BackendAvailability(
            backend=self.kind,
            available=available,
            reason=reason,
            runtime=self._configured_runtime,
            host_report=report,
        )

    def prepare(self, mission: SandboxMission, policy: SandboxPolicy) -> SandboxLaunchSpec:
        self._validate_backend_compatibility(mission, policy)
        availability = self.availability()
        if not availability.available:
            raise SandboxUnavailable(availability.reason)
        return self._prepare_common(
            mission,
            policy,
            runtime=self._configured_runtime or self.runtime,
            backend=self.kind,
        )


class HardenedDockerSandboxBackend(_DockerSandboxBackend):
    """Explicit lower-isolation fallback for policy-authorized research only."""

    kind = SandboxBackendKind.HARDENED_DOCKER

    def __init__(
        self,
        *,
        runtime: str = "runc",
        host_report: HostCapabilityReport | None = None,
        command_probe: CommandProbe | None = None,
        docker_binary: str = "docker",
        secret_materializer: SecretMaterializer | None = None,
        runtime_identity: Mapping[str, str | None] | None = None,
    ) -> None:
        super().__init__(
            runtime=runtime,
            host_report=host_report,
            command_probe=command_probe,
            docker_binary=docker_binary,
            secret_materializer=secret_materializer,
            runtime_identity=runtime_identity,
        )

    def availability(self) -> BackendAvailability:
        report = self._host()
        available = report.docker_available and self.runtime != "kata"
        reason = (
            "Docker daemon and hardened runtime are available"
            if available
            else "Docker daemon is unavailable or runtime is not a hardened Docker runtime"
        )
        return BackendAvailability(
            backend=self.kind,
            available=available,
            reason=reason,
            runtime=self.runtime,
            host_report=report,
        )

    def prepare(self, mission: SandboxMission, policy: SandboxPolicy) -> SandboxLaunchSpec:
        self._validate_backend_compatibility(mission, policy)
        availability = self.availability()
        if not availability.available:
            raise SandboxUnavailable(availability.reason)
        return self._prepare_common(
            mission,
            policy,
            runtime=self.runtime,
            backend=self.kind,
        )


@dataclass(frozen=True, slots=True)
class BackendSelection:
    trust_class: TrustClass
    requested_backend: SandboxBackendKind | None
    selected_backend: SandboxBackendKind
    backend: SandboxBackend
    fallback_used: bool
    fallback_reason: str | None
    policy_authorization: str | None
    policy_hash: str

    def audit_record(self) -> SandboxBackendSelectionRecord:
        return SandboxBackendSelectionRecord(
            trust_class=self.trust_class,
            requested_backend=self.requested_backend,
            selected_backend=self.selected_backend,
            fallback_used=self.fallback_used,
            fallback_reason=self.fallback_reason,
            policy_authorization=self.policy_authorization,
            policy_hash=self.policy_hash,
        )


def select_sandbox_backend(
    trust_class: TrustClass,
    *,
    policy: SandboxPolicy,
    kata: KataSandboxBackend,
    docker: HardenedDockerSandboxBackend,
) -> BackendSelection:
    """Select a backend without silently weakening the trust class."""

    if trust_class in {TrustClass.TRUSTED_CORE, TrustClass.BLOCKED_EXECUTION}:
        raise SandboxPolicyError(f"execution is prohibited for trust class {trust_class.value}")
    requested = policy.requested_backend
    kata_available = kata.availability()
    docker_available = docker.availability()

    if trust_class is TrustClass.UNTRUSTED_NATIVE:
        if requested is SandboxBackendKind.HARDENED_DOCKER:
            raise SandboxPolicyError("UNTRUSTED_NATIVE requires Kata and cannot request Docker")
        if not kata_available.available:
            raise SandboxUnavailable(f"UNTRUSTED_NATIVE requires Kata: {kata_available.reason}")
        return BackendSelection(
            trust_class=trust_class,
            requested_backend=requested,
            selected_backend=SandboxBackendKind.KATA,
            backend=kata,
            fallback_used=False,
            fallback_reason=None,
            policy_authorization=None,
            policy_hash=policy.identity,
        )

    if requested is SandboxBackendKind.KATA:
        if not kata_available.available:
            raise SandboxUnavailable(kata_available.reason)
        return BackendSelection(
            trust_class=trust_class,
            requested_backend=requested,
            selected_backend=SandboxBackendKind.KATA,
            backend=kata,
            fallback_used=False,
            fallback_reason=None,
            policy_authorization=None,
            policy_hash=policy.identity,
        )

    if (
        trust_class is TrustClass.UNTRUSTED_RESEARCH
        and requested is SandboxBackendKind.HARDENED_DOCKER
        and not policy.allow_docker_fallback
    ):
        raise SandboxPolicyError("Docker selection requires explicit fallback authorization")

    if trust_class is TrustClass.TRUSTED_RESEARCH and requested is None:
        if docker_available.available:
            return BackendSelection(
                trust_class=trust_class,
                requested_backend=requested,
                selected_backend=SandboxBackendKind.HARDENED_DOCKER,
                backend=docker,
                fallback_used=False,
                fallback_reason=None,
                policy_authorization=None,
                policy_hash=policy.identity,
            )
        if kata_available.available:
            return BackendSelection(
                trust_class=trust_class,
                requested_backend=requested,
                selected_backend=SandboxBackendKind.KATA,
                backend=kata,
                fallback_used=False,
                fallback_reason="trusted research Docker backend unavailable",
                policy_authorization=None,
                policy_hash=policy.identity,
            )
    if kata_available.available and requested is None:
        return BackendSelection(
            trust_class=trust_class,
            requested_backend=requested,
            selected_backend=SandboxBackendKind.KATA,
            backend=kata,
            fallback_used=False,
            fallback_reason=None,
            policy_authorization=None,
            policy_hash=policy.identity,
        )
    if docker_available.available and (
        trust_class is TrustClass.TRUSTED_RESEARCH or policy.allow_docker_fallback
    ):
        reason = kata_available.reason
        return BackendSelection(
            trust_class=trust_class,
            requested_backend=requested,
            selected_backend=SandboxBackendKind.HARDENED_DOCKER,
            backend=docker,
            fallback_used=trust_class is TrustClass.UNTRUSTED_RESEARCH,
            fallback_reason=reason if trust_class is TrustClass.UNTRUSTED_RESEARCH else None,
            policy_authorization=(
                policy.fallback_authorization_reference
                if trust_class is TrustClass.UNTRUSTED_RESEARCH
                else None
            ),
            policy_hash=policy.identity,
        )
    raise SandboxUnavailable(
        f"no policy-authorized backend is available; Kata={kata_available.reason}; Docker={docker_available.reason}"
    )


class SandboxProcessRunner:
    """Execute a prepared spec with wall/output enforcement and cleanup."""

    def __init__(self, *, poll_seconds: float = 0.05) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.poll_seconds = poll_seconds

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> bool:
        if process.poll() is not None:
            return True
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            with suppress(OSError):
                process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(process.pid, signal.SIGKILL)
            with suppress(OSError):
                process.kill()
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=2)
        return process.poll() is not None

    def execute(
        self,
        spec: SandboxLaunchSpec,
        *,
        gpu_lease: GpuLease | None = None,
    ) -> SandboxRunResult:
        started_at = datetime.now(UTC)
        stdout_hash = hashlib.sha256()
        stderr_hash = hashlib.sha256()
        process: subprocess.Popen[bytes] | None = None
        exit_reason = SandboxExitReason.START_FAILURE
        exit_code: int | None = None
        output_bytes = 0
        cleanup_ok = False
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            try:
                process = subprocess.Popen(
                    list(spec.command),
                    cwd=spec.working_directory,
                    env={key: value for key, value in spec.environment},
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=True,
                )
                deadline = time.monotonic() + spec.wall_time_seconds
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        exit_reason = SandboxExitReason.WALL_TIME_LIMIT
                        self._terminate(process)
                        break
                    output_bytes = (
                        sum(
                            path.stat().st_size
                            for path in spec.output_directory.rglob("*")
                            if path.is_file() and not path.is_symlink()
                        )
                        if spec.output_directory.exists()
                        else 0
                    )
                    if output_bytes > spec.output_quota_bytes:
                        exit_reason = SandboxExitReason.OUTPUT_QUOTA
                        self._terminate(process)
                        break
                    time.sleep(self.poll_seconds)
                if process.poll() is not None and exit_reason is SandboxExitReason.START_FAILURE:
                    exit_code = process.returncode
                    exit_reason = (
                        SandboxExitReason.COMPLETED
                        if exit_code == 0
                        else SandboxExitReason.NONZERO_EXIT
                    )
                output_bytes = (
                    sum(
                        path.stat().st_size
                        for path in spec.output_directory.rglob("*")
                        if path.is_file() and not path.is_symlink()
                    )
                    if spec.output_directory.exists()
                    else 0
                )
                cleanup_ok = self._terminate(process)
            except (OSError, ValueError):
                if process is not None:
                    cleanup_ok = self._terminate(process)
                else:
                    cleanup_ok = True
                exit_reason = SandboxExitReason.START_FAILURE
            finally:
                if gpu_lease is not None:
                    gpu_lease.release()
            stdout_file.seek(0)
            stderr_file.seek(0)
            for chunk in iter(lambda: stdout_file.read(1024 * 1024), b""):
                stdout_hash.update(chunk)
            for chunk in iter(lambda: stderr_file.read(1024 * 1024), b""):
                stderr_hash.update(chunk)
        ended_at = datetime.now(UTC)
        output_hashes = _artifact_hashes(spec.output_directory)
        provenance = spec.provenance.model_copy(
            update={
                "started_at": started_at,
                "ended_at": ended_at,
                "exit_reason": exit_reason,
                "output_artifact_hashes": output_hashes,
                "resource_usage": {"output_bytes": output_bytes},
            }
        )
        return SandboxRunResult(
            mission_id=spec.mission_id,
            backend=spec.backend,
            exit_code=exit_code,
            exit_reason=exit_reason if cleanup_ok else SandboxExitReason.CLEANUP_FAILURE,
            started_at=started_at,
            ended_at=ended_at,
            process_alive_after_cleanup=not cleanup_ok,
            output_bytes=output_bytes,
            stdout_sha256=f"sha256:{stdout_hash.hexdigest()}",
            stderr_sha256=f"sha256:{stderr_hash.hexdigest()}",
            provenance=provenance,
        )


_ADVERSARIAL_PROBES = (
    "host_repository_read",
    "host_repository_write",
    "ssh_directory_read",
    "secret_environment_read",
    "unrelated_user_file_read",
    "docker_socket_read",
    "containerd_socket_read",
    "host_mount_attempt",
    "privileged_device_create",
    "namespace_escape",
    "arbitrary_lan_network",
    "blocked_broker_network",
    "wall_time_expiry",
    "pid_budget_excess",
    "memory_budget_excess",
    "mission_cross_write",
    "order_submission",
    "risk_policy_write",
    "oms_invocation",
    "self_promotion",
    "native_extension_probe",
)


def adversarial_probe_names() -> tuple[str, ...]:
    """Return the bounded hostile workload contract used by both backends."""

    return _ADVERSARIAL_PROBES


class BackendMeasurement(BaseModel):
    """Measured or explicitly unverified backend comparison data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: SandboxBackendKind
    status: str
    startup_ms: int | None = None
    idle_memory_mib: int | None = None
    peak_memory_mib: int | None = None
    cpu_overhead_percent: float | None = None
    disk_overhead_mib: int | None = None
    teardown_ms: int | None = None
    isolation_attestations: Mapping[str, bool | None] = {}
    limitations: tuple[str, ...] = ()


def unavailable_backend_measurement(backend: SandboxBackendKind, reason: str) -> BackendMeasurement:
    """Create an honest comparison row without fabricating runtime evidence."""

    return BackendMeasurement(
        backend=backend,
        status="unavailable",
        limitations=(reason, "no hostile workload was executed"),
        isolation_attestations={probe: None for probe in adversarial_probe_names()},
    )
