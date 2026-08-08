"""Hermes-compatible isolated artifact exports and Skill Foundry checks."""

from __future__ import annotations

import builtins
import hashlib
import io
import json
import multiprocessing as mp
import os
import queue as queue_module
import socket
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.contracts import (
    CapabilityCard,
    CapabilityLifecycle,
    SourceGrade,
    is_forbidden_authority_action,
    normalize_authority_action,
)


def _require_digest(value: str, info: object) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(
            f"{getattr(info, 'field_name', 'hash')} must be a lowercase SHA-256 digest"
        )
    return value


class EnvironmentManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    image_digest: str = Field(min_length=1)
    lock_hash: str = Field(min_length=64, max_length=64)
    model_revisions: tuple[str, ...] = ()
    dataset_revisions: tuple[str, ...] = ()
    skill_hashes: tuple[str, ...] = ()
    seed: int
    tool_versions: tuple[str, ...] = ()

    _lock_hash = field_validator("lock_hash")(_require_digest)

    @field_validator("image_digest")
    @classmethod
    def require_image_digest(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("environment image digest is required")
        return value.strip()

    @field_validator("model_revisions", "dataset_revisions", "skill_hashes", "tool_versions")
    @classmethod
    def require_nonblank_revisions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("environment revisions and tool versions cannot be blank")
        if len(values) != len(set(values)):
            raise ValueError("environment revisions and tool versions must be unique")
        return tuple(value.strip() for value in values)


class HermesSandboxPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: str
    read_only_snapshot: bool = True
    allowed_network_hosts: tuple[str, ...] = ()
    allowed_secrets: tuple[str, ...] = ()
    cpu_seconds: int = Field(gt=0)
    memory_mib: int = Field(gt=0)
    wall_time_seconds: int = Field(gt=0)
    forbidden_actions: tuple[str, ...] = (
        "submit_order",
        "change_risk_limit",
        "live_deploy",
        "broker_credentials",
    )

    @field_validator("mode")
    @classmethod
    def normalize_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("Hermes mode cannot be blank")
        return normalized

    @field_validator("allowed_network_hosts", "allowed_secrets")
    @classmethod
    def normalize_isolation_tokens(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("Hermes isolation entries must be unique and non-blank")
        return normalized

    @field_validator("forbidden_actions")
    @classmethod
    def normalize_forbidden_actions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(normalize_authority_action(value) for value in values)
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("Hermes forbidden actions must be unique and non-blank")
        return normalized

    @model_validator(mode="after")
    def require_isolation(self) -> HermesSandboxPolicy:
        if self.mode not in {"deep", "builder", "recovery"}:
            raise ValueError("Hermes is allowed only in Deep, Builder, or offline Recovery")
        if not self.read_only_snapshot:
            raise ValueError("Hermes task snapshots must be read-only by default")
        required_forbidden = {
            "submit_order",
            "change_risk_limit",
            "live_deploy",
            "broker_credentials",
        }
        normalized_forbidden = set(self.forbidden_actions)
        if not required_forbidden.issubset(normalized_forbidden) or any(
            action.endswith(
                ("_submit_order", "_change_risk_limit", "_live_deploy", "_broker_credentials")
            )
            for action in normalized_forbidden
        ):
            raise ValueError("Hermes policy must retain all trading and credential prohibitions")
        forbidden_secret_tokens = ("broker", "exchange", "order", "live", "credential")
        if any(
            any(token in secret.lower() for token in forbidden_secret_tokens)
            for secret in self.allowed_secrets
        ):
            raise ValueError("Hermes tasks may not receive broker or live-trading secrets")
        if any(
            not host.strip() or host.strip() == "*" or host.strip().startswith("*.")
            for host in self.allowed_network_hosts
        ):
            raise ValueError("Hermes network policy requires explicit host allowlists")
        return self


class HermesNetworkAccessError(RuntimeError):
    """A Hermes task attempted a socket or DNS operation outside its policy."""


class HermesFilesystemWriteError(RuntimeError):
    """A read-only Hermes task attempted to mutate local filesystem state."""


class HermesSensitivePathAccessError(RuntimeError):
    """A Hermes task attempted to read a path conventionally containing secrets."""


class _HermesNetworkGuard(AbstractContextManager["_HermesNetworkGuard"]):
    """Enforce the child-process network allowlist at the socket boundary.

    The guard deliberately fails closed for an empty allowlist.  An explicit
    hostname allowlist is resolved once with the original resolver so that the
    subsequent socket ``connect`` call can accept the resolved address without
    allowing an unrelated address to bypass the hostname policy.
    """

    def __init__(self, allowed_hosts: tuple[str, ...]) -> None:
        self.allowed_hosts = {host.strip().lower().rstrip(".") for host in allowed_hosts}
        self.allowed_addresses: set[str] = set()
        self.attempted = False
        self._original_socket = socket.socket
        self._stream_type = socket.SOCK_STREAM
        self._original_create_connection = socket.create_connection
        self._original_getaddrinfo = socket.getaddrinfo
        self._original_gethostbyname = socket.gethostbyname
        self._original_gethostbyname_ex = socket.gethostbyname_ex
        self._original_gethostbyaddr = socket.gethostbyaddr
        self._original_getnameinfo = socket.getnameinfo

    def __enter__(self) -> _HermesNetworkGuard:
        if self.allowed_hosts:
            for host in self.allowed_hosts:
                try:
                    infos = self._original_getaddrinfo(
                        host,
                        None,
                        type=self._stream_type,
                    )
                except OSError:
                    continue
                self.allowed_addresses.update(
                    str(info[4][0]).lower().rstrip(".") for info in infos if info[4]
                )

        guard = self

        def check_address(address: object) -> None:
            host: object
            if isinstance(address, tuple) and address:
                host = address[0]
            else:
                host = address
            normalized = str(host).strip().lower().rstrip(".")
            if normalized in guard.allowed_hosts or normalized in guard.allowed_addresses:
                return
            guard.attempted = True
            raise HermesNetworkAccessError("Hermes network access is not allowed for this task")

        original_socket = self._original_socket

        class GuardedSocket(original_socket):
            def connect(self, address):  # type: ignore[no-untyped-def]
                check_address(address)
                return super().connect(address)

            def connect_ex(self, address):  # type: ignore[no-untyped-def]
                check_address(address)
                return super().connect_ex(address)

            def sendto(self, data, *args):  # type: ignore[no-untyped-def]
                if args:
                    check_address(args[-1])
                return super().sendto(data, *args)

            def sendmsg(self, buffers, ancdata=(), flags=0, address=None):  # type: ignore[no-untyped-def]
                if address is not None:
                    check_address(address)
                    return super().sendmsg(buffers, ancdata, flags, address)
                return super().sendmsg(buffers, ancdata, flags)

        def guarded_getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
            if host is not None:
                check_address((host, 0))
            return guard._original_getaddrinfo(host, *args, **kwargs)

        def guarded_create_connection(address, *args, **kwargs):  # type: ignore[no-untyped-def]
            check_address(address)
            return guard._original_create_connection(address, *args, **kwargs)

        def guarded_gethostbyname(host):  # type: ignore[no-untyped-def]
            check_address((host, 0))
            return guard._original_gethostbyname(host)

        def guarded_gethostbyname_ex(host):  # type: ignore[no-untyped-def]
            check_address((host, 0))
            return guard._original_gethostbyname_ex(host)

        def guarded_gethostbyaddr(host):  # type: ignore[no-untyped-def]
            check_address((host, 0))
            return guard._original_gethostbyaddr(host)

        def guarded_getnameinfo(address, flags):  # type: ignore[no-untyped-def]
            check_address(address)
            return guard._original_getnameinfo(address, flags)

        socket.socket = GuardedSocket
        socket.getaddrinfo = guarded_getaddrinfo
        socket.create_connection = guarded_create_connection
        socket.gethostbyname = guarded_gethostbyname
        socket.gethostbyname_ex = guarded_gethostbyname_ex
        socket.gethostbyaddr = guarded_gethostbyaddr
        socket.getnameinfo = guarded_getnameinfo
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[no-untyped-def]
        socket.socket = self._original_socket
        socket.getaddrinfo = self._original_getaddrinfo
        socket.create_connection = self._original_create_connection
        socket.gethostbyname = self._original_gethostbyname
        socket.gethostbyname_ex = self._original_gethostbyname_ex
        socket.gethostbyaddr = self._original_gethostbyaddr
        socket.getnameinfo = self._original_getnameinfo


class _HermesFilesystemGuard(AbstractContextManager["_HermesFilesystemGuard"]):
    """Reject common Python and OS filesystem mutations inside a task."""

    _SENSITIVE_PATH_NAMES = frozenset(
        {
            ".aws",
            ".azure",
            ".bundle",
            ".docker",
            ".env",
            ".gnupg",
            ".kube",
            ".netrc",
            ".npmrc",
            ".password-store",
            ".pypirc",
            ".ssh",
            ".terraform.d",
            "api-keys",
            "application_default_credentials.json",
            "authorized_keys",
            "credentials",
            "credentials.env",
            "environ",
            ".git-credentials",
            "git-credentials",
            "id_dsa",
            "id_ecdsa",
            "id_ed25519",
            "id_rsa",
            "keyrings",
            "known_hosts",
            "private-keys",
            "proc",
            "secrets",
            "secrets.env",
            "token",
            "tokens",
        }
    )
    _SENSITIVE_PATH_PREFIXES = (
        "access_key",
        "api_key",
        "credential_",
        "credentials.",
        "password",
        "private_key",
        "secret_",
        "secrets.",
    )

    _MUTATING_OS_FUNCTIONS = (
        "remove",
        "unlink",
        "rename",
        "replace",
        "mkdir",
        "makedirs",
        "rmdir",
        "chmod",
        "fchmod",
        "chown",
        "fchown",
        "lchown",
        "utime",
        "truncate",
        "ftruncate",
        "link",
        "symlink",
        "mknod",
        "write",
        "writev",
        "pwrite",
        "pwritev",
        "copy_file_range",
    )

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.attempted = False
        self.sensitive_access_attempted = False
        self._original_builtin_open = builtins.open
        self._original_io_open = io.open
        self._original_os_open = os.open
        self._original_os_fdopen = os.fdopen
        self._original_os_functions = {
            name: getattr(os, name) for name in self._MUTATING_OS_FUNCTIONS if hasattr(os, name)
        }

    def __enter__(self) -> _HermesFilesystemGuard:
        if not self.enabled:
            return self
        guard = self

        def check_sensitive_path(path: object) -> None:
            if isinstance(path, int):
                return
            raw_path = os.fspath(path)
            try:
                normalized = raw_path.decode(errors="ignore")
            except AttributeError:
                normalized = str(raw_path)
            paths = {normalized}
            try:
                paths.add(os.path.realpath(normalized))
            except (OSError, RuntimeError, ValueError):
                pass
            for candidate in paths:
                components = candidate.replace("\\", "/").lower().split("/")
                if not any(
                    component in guard._SENSITIVE_PATH_NAMES
                    or any(
                        component.startswith(prefix) for prefix in guard._SENSITIVE_PATH_PREFIXES
                    )
                    for component in components
                ):
                    continue
                guard.sensitive_access_attempted = True
                raise HermesSensitivePathAccessError(
                    "Hermes task access to a sensitive path is not allowed"
                )

        def reject(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            guard.attempted = True
            raise HermesFilesystemWriteError(
                "Hermes read-only snapshot rejected a filesystem mutation"
            )

        def read_only_open(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
            check_sensitive_path(file)
            if any(flag in str(mode) for flag in "wxa+"):
                return reject(file, mode, *args, **kwargs)
            return guard._original_builtin_open(file, mode, *args, **kwargs)

        def read_only_io_open(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
            check_sensitive_path(file)
            if any(flag in str(mode) for flag in "wxa+"):
                return reject(file, mode, *args, **kwargs)
            return guard._original_io_open(file, mode, *args, **kwargs)

        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        if hasattr(os, "O_TMPFILE"):
            write_flags |= os.O_TMPFILE

        def read_only_os_open(path, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
            check_sensitive_path(path)
            if flags & write_flags:
                return reject(path, flags, *args, **kwargs)
            return guard._original_os_open(path, flags, *args, **kwargs)

        def read_only_fdopen(fd, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
            if any(flag in str(mode) for flag in "wxa+"):
                return reject(fd, mode, *args, **kwargs)
            return guard._original_os_fdopen(fd, mode, *args, **kwargs)

        builtins.open = read_only_open
        io.open = read_only_io_open
        os.open = read_only_os_open
        os.fdopen = read_only_fdopen
        for name in self._original_os_functions:
            setattr(os, name, reject)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[no-untyped-def]
        if not self.enabled:
            return
        builtins.open = self._original_builtin_open
        io.open = self._original_io_open
        os.open = self._original_os_open
        os.fdopen = self._original_os_fdopen
        for name, original in self._original_os_functions.items():
            setattr(os, name, original)


class HermesTaskResult(BaseModel):
    """Measured result of one bounded, isolated Hermes task attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: UUID = Field(default_factory=uuid4)
    task_name: str = Field(min_length=1)
    policy_hash: str = Field(min_length=64, max_length=64)
    passed: bool
    timed_out: bool = False
    network_access_attempted: bool = False
    filesystem_write_attempted: bool = False
    sensitive_path_access_attempted: bool = False
    output: object | None = None
    output_hash: str | None = None
    elapsed_ms: int = Field(ge=0)
    cpu_seconds: Decimal = Field(ge=Decimal("0"))
    peak_memory_mib: int = Field(ge=0)
    error: str | None = None

    _policy_hash = field_validator("policy_hash")(_require_digest)

    @field_validator("task_name")
    @classmethod
    def normalize_task_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Hermes task name cannot be blank")
        return value.strip()

    @field_validator("cpu_seconds")
    @classmethod
    def require_finite_cpu(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Hermes task CPU measurement must be finite")
        return value

    @field_validator("output_hash")
    @classmethod
    def validate_output_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("output_hash must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def validate_result(self) -> HermesTaskResult:
        if self.passed and (self.timed_out or self.error is not None or self.output_hash is None):
            raise ValueError("a passed Hermes task requires a bounded successful output")
        if self.passed and self.network_access_attempted:
            raise ValueError("a Hermes task that attempted network access cannot pass")
        if self.passed and self.filesystem_write_attempted:
            raise ValueError("a read-only Hermes task that attempted a write cannot pass")
        if self.passed and self.sensitive_path_access_attempted:
            raise ValueError("a Hermes task that accessed a sensitive path cannot pass")
        if self.timed_out and self.error is None:
            raise ValueError("a timed-out Hermes task requires an error")
        if self.error is not None and not self.error.strip():
            raise ValueError("Hermes task errors cannot be blank")
        return self


def _run_hermes_task(
    task,
    result_queue,
    allowed_network_hosts: tuple[str, ...],
    read_only_snapshot: bool,
) -> None:
    """Child-process entry point; no broker/credential environment is inherited."""

    forbidden_environment_tokens = (
        "API_KEY",
        "SECRET",
        "TOKEN",
        "PASSWORD",
        "CREDENTIAL",
        "PRIVATE_KEY",
        "BROKER",
        "EXCHANGE",
    )
    for key in tuple(os.environ):
        if any(token in key.upper() for token in forbidden_environment_tokens):
            os.environ.pop(key, None)
    started_cpu = time.process_time()
    with (
        _HermesNetworkGuard(allowed_network_hosts) as network_guard,
        _HermesFilesystemGuard(read_only_snapshot) as filesystem_guard,
    ):
        try:
            output = task()
            error = None
            passed = True
        except Exception as exc:  # pragma: no cover - exact child exception is task-specific
            output = None
            error = f"{type(exc).__name__}: {exc}"
            passed = False
    network_access_attempted = network_guard.attempted
    filesystem_write_attempted = filesystem_guard.attempted
    sensitive_path_access_attempted = filesystem_guard.sensitive_access_attempted
    elapsed_cpu = max(0.0, time.process_time() - started_cpu)
    peak_memory_mib = 0
    try:
        import resource

        peak_memory_mib = max(0, int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024))
    except (ImportError, OSError):
        pass
    output_hash = None
    if passed:
        output_hash = hashlib.sha256(
            json.dumps(output, sort_keys=True, default=str, separators=(",", ":")).encode()
        ).hexdigest()
    result_queue.put(
        {
            "passed": passed,
            "network_access_attempted": network_access_attempted,
            "filesystem_write_attempted": filesystem_write_attempted,
            "sensitive_path_access_attempted": sensitive_path_access_attempted,
            "output": output,
            "output_hash": output_hash,
            "cpu_seconds": elapsed_cpu,
            "peak_memory_mib": peak_memory_mib,
            "error": error,
        }
    )


@dataclass(frozen=True, slots=True)
class HermesIsolationRunner:
    """Run one research/build callable in a bounded child process.

    The runner intentionally returns a measured artifact rather than granting
    the task a capability.  A task receives no arguments, has sensitive
    environment variables removed, and is terminated when its wall-time
    budget expires.  CapabilityRegistry remains the only lifecycle authority
    for anything produced by the task.
    """

    policy: HermesSandboxPolicy

    def run(self, *, task_name: str, task: Callable[[], object]) -> HermesTaskResult:
        if not task_name.strip():
            raise ValueError("Hermes task name cannot be blank")
        if not callable(task):
            raise TypeError("Hermes task must be callable")
        context_name = "fork" if "fork" in mp.get_all_start_methods() else "spawn"
        context = mp.get_context(context_name)
        result_queue = context.Queue(maxsize=1)
        process = context.Process(
            target=_run_hermes_task,
            args=(
                task,
                result_queue,
                self.policy.allowed_network_hosts,
                self.policy.read_only_snapshot,
            ),
        )
        policy_hash = hashlib.sha256(self.policy.model_dump_json().encode()).hexdigest()
        started = time.monotonic()
        try:
            process.start()
        except Exception as exc:
            result_queue.close()
            return HermesTaskResult(
                task_name=task_name,
                policy_hash=policy_hash,
                passed=False,
                elapsed_ms=0,
                cpu_seconds=Decimal("0"),
                peak_memory_mib=0,
                error=f"process_start_failed:{type(exc).__name__}: {exc}",
            )
        process.join(self.policy.wall_time_seconds)
        timed_out = process.is_alive()
        if timed_out:
            process.terminate()
            process.join()
        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        payload: dict[str, object] | None = None
        if not timed_out:
            try:
                candidate = result_queue.get(timeout=0.5)
                if isinstance(candidate, dict):
                    payload = candidate
            except queue_module.Empty:
                payload = None
        result_queue.close()
        if timed_out:
            return HermesTaskResult(
                task_name=task_name,
                policy_hash=policy_hash,
                passed=False,
                timed_out=True,
                elapsed_ms=elapsed_ms,
                cpu_seconds=Decimal("0"),
                peak_memory_mib=0,
                error="wall_time_budget_exceeded",
            )
        if payload is None:
            return HermesTaskResult(
                task_name=task_name,
                policy_hash=policy_hash,
                passed=False,
                elapsed_ms=elapsed_ms,
                cpu_seconds=Decimal("0"),
                peak_memory_mib=0,
                error="task_exited_without_a_result",
            )
        cpu_seconds = Decimal(str(payload.get("cpu_seconds", 0)))
        peak_memory_mib = int(payload.get("peak_memory_mib", 0))
        network_access_attempted = bool(payload.get("network_access_attempted", False))
        filesystem_write_attempted = bool(payload.get("filesystem_write_attempted", False))
        sensitive_path_access_attempted = bool(
            payload.get("sensitive_path_access_attempted", False)
        )
        child_passed = bool(payload.get("passed", False))
        error = payload.get("error")
        if not isinstance(error, str) and error is not None:
            error = str(error)
        if network_access_attempted:
            child_passed = False
            error = "network_access_attempted"
        if filesystem_write_attempted:
            child_passed = False
            error = "filesystem_write_attempted"
        if sensitive_path_access_attempted:
            child_passed = False
            error = "sensitive_path_access_attempted"
        if child_passed and cpu_seconds > Decimal(self.policy.cpu_seconds):
            child_passed = False
            error = "cpu_budget_exceeded"
        if child_passed and peak_memory_mib > self.policy.memory_mib:
            child_passed = False
            error = "memory_budget_exceeded"
        output = payload.get("output")
        output_hash = payload.get("output_hash")
        if output_hash is not None and not isinstance(output_hash, str):
            output_hash = str(output_hash)
        return HermesTaskResult(
            task_name=task_name,
            policy_hash=policy_hash,
            passed=child_passed,
            network_access_attempted=network_access_attempted,
            filesystem_write_attempted=filesystem_write_attempted,
            sensitive_path_access_attempted=sensitive_path_access_attempted,
            output=output if child_passed else None,
            output_hash=output_hash if child_passed else None,
            elapsed_ms=elapsed_ms,
            cpu_seconds=cpu_seconds,
            peak_memory_mib=max(0, peak_memory_mib),
            error=None if child_passed else error or "task_failed",
        )


class ResearchBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1)
    claims: tuple[str, ...]
    source_artifact_ids: tuple[str, ...]
    code_hash: str = Field(min_length=64, max_length=64)
    unresolved_questions: tuple[str, ...] = ()
    environment: EnvironmentManifest

    _code_hash = field_validator("code_hash")(_require_digest)

    @field_validator("claims", "source_artifact_ids", "unresolved_questions")
    @classmethod
    def require_bundle_tokens(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("research bundle entries cannot be blank")
        return tuple(value.strip() for value in values)

    @model_validator(mode="after")
    def require_claims_and_sources(self) -> ResearchBundle:
        if not self.claims or not self.source_artifact_ids:
            raise ValueError("research bundles require claims and source artifact IDs")
        return self


class CandidateStrategy(BaseModel):
    """Quarantined strategy proposal; it contains an experiment, never orders."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    experiment_spec: str = Field(min_length=1)
    source_artifact_ids: tuple[str, ...] = ()
    code_hash: str = Field(min_length=64, max_length=64)
    required_tests: tuple[str, ...] = ()
    risk_notes: tuple[str, ...] = ()
    environment: EnvironmentManifest

    _code_hash = field_validator("code_hash")(_require_digest)

    @model_validator(mode="after")
    def require_strategy_tests(self) -> CandidateStrategy:
        if not self.required_tests or any(not test.strip() for test in self.required_tests):
            raise ValueError("candidate strategies require reproducibility/validation tests")
        if any(not item.strip() for item in (*self.source_artifact_ids, *self.risk_notes)):
            raise ValueError("candidate strategy entries cannot be blank")
        return self


class ModelAdapterCandidate(BaseModel):
    """Pinned model-adapter proposal awaiting independent admission."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    role: str = Field(min_length=1)
    adapter_hash: str = Field(min_length=64, max_length=64)
    checkpoint_hash: str = Field(min_length=64, max_length=64)
    contract_tests: tuple[str, ...]
    security_tests: tuple[str, ...]
    performance_benchmark: str = Field(min_length=1)
    environment: EnvironmentManifest

    _adapter_hash = field_validator("adapter_hash")(_require_digest)
    _checkpoint_hash = field_validator("checkpoint_hash")(_require_digest)

    @model_validator(mode="after")
    def require_model_tests(self) -> ModelAdapterCandidate:
        if (
            not self.contract_tests
            or not self.security_tests
            or any(not test.strip() for test in (*self.contract_tests, *self.security_tests))
        ):
            raise ValueError("model adapters require contract and security tests")
        return self


class RunbookDraft(BaseModel):
    """Offline recovery/maintenance runbook draft exported for review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    runbook_version: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    steps: tuple[str, ...]
    rollback: str = Field(min_length=1)
    environment: EnvironmentManifest

    @model_validator(mode="after")
    def require_steps(self) -> RunbookDraft:
        if not self.steps or any(not step.strip() for step in self.steps):
            raise ValueError("runbooks require non-empty recovery steps")
        return self


class CollectorCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    interface_version: str = Field(min_length=1)
    source_grade: str = Field(min_length=1)
    parser_hash: str = Field(min_length=64, max_length=64)
    contract_tests: tuple[str, ...]
    security_tests: tuple[str, ...]
    performance_benchmark: str = Field(min_length=1)
    environment: EnvironmentManifest

    _parser_hash = field_validator("parser_hash")(_require_digest)

    @model_validator(mode="after")
    def require_collector_tests(self) -> CollectorCandidate:
        if (
            not self.contract_tests
            or not self.security_tests
            or any(not test.strip() for test in (*self.contract_tests, *self.security_tests))
        ):
            raise ValueError("collector candidates require contract and security tests")
        return self


class CapabilityBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    capability_version: str
    interface: str
    code_hash: str = Field(min_length=64, max_length=64)
    permissions: tuple[str, ...]
    artifact_hash: str = Field(min_length=64, max_length=64)
    environment: EnvironmentManifest
    review_references: tuple[str, ...] = ()

    _code_hash = field_validator("code_hash")(_require_digest)
    _artifact_hash = field_validator("artifact_hash")(_require_digest)

    @field_validator("permissions")
    @classmethod
    def prohibit_trading_permissions(cls, permissions: tuple[str, ...]) -> tuple[str, ...]:
        normalized = {normalize_authority_action(permission) for permission in permissions}
        forbidden = {
            permission for permission in normalized if is_forbidden_authority_action(permission)
        }
        if forbidden:
            raise ValueError("capability bundles cannot contain trading permissions")
        if len(permissions) != len(normalized) or any(
            not permission.strip() for permission in permissions
        ):
            raise ValueError("capability permissions must be unique and non-blank")
        return tuple(normalized)

    @field_validator("review_references")
    @classmethod
    def normalize_review_references(cls, references: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(reference.strip() for reference in references)
        if any(not reference for reference in normalized) or len(normalized) != len(
            set(normalized)
        ):
            raise ValueError("capability review references must be unique and non-blank")
        return normalized

    @model_validator(mode="after")
    def require_bundle_identity(self) -> CapabilityBundle:
        if (
            not self.name.strip()
            or not self.capability_version.strip()
            or not self.interface.strip()
        ):
            raise ValueError("capability bundles require name, version, and interface")
        if any(not reference.strip() for reference in self.review_references):
            raise ValueError("capability review references cannot be blank")
        return self


@dataclass(frozen=True, slots=True)
class CapabilityFoundry:
    """Converts a sandbox result into immutable, reviewable capability artifacts."""

    def export_research(
        self,
        *,
        title: str,
        claims: tuple[str, ...],
        source_artifact_ids: tuple[str, ...],
        code: str,
        environment: EnvironmentManifest,
    ) -> ResearchBundle:
        return ResearchBundle(
            title=title,
            claims=claims,
            source_artifact_ids=source_artifact_ids,
            code_hash=hashlib.sha256(code.encode()).hexdigest(),
            environment=environment,
        )

    def export_collector(
        self,
        *,
        name: str,
        interface_version: str,
        source_grade: str,
        parser_code: str,
        contract_tests: tuple[str, ...],
        security_tests: tuple[str, ...],
        performance_benchmark: str,
        environment: EnvironmentManifest,
    ) -> CollectorCandidate:
        return CollectorCandidate(
            name=name,
            interface_version=interface_version,
            source_grade=source_grade,
            parser_hash=hashlib.sha256(parser_code.encode()).hexdigest(),
            contract_tests=contract_tests,
            security_tests=security_tests,
            performance_benchmark=performance_benchmark,
            environment=environment,
        )

    def collector_capability_card(
        self,
        candidate: CollectorCandidate,
        *,
        inputs: tuple[str, ...] = ("snapshot",),
        outputs: tuple[str, ...] = ("observations",),
        resource_envelope: str = "small",
        latency_class: str = "bounded",
    ) -> CapabilityCard:
        """Convert a reviewed collector candidate into a quarantined card.

        This is intentionally a separate step from :meth:`export_collector`:
        Hermes can describe code, but the registry owns lifecycle admission.
        The resulting card has read-only source authority and carries the
        candidate's contract/security/performance references as immutable
        provenance.
        """

        try:
            source_grade = SourceGrade(candidate.source_grade.strip().lower())
        except ValueError as exc:
            raise ValueError("collector candidate has an unsupported source grade") from exc
        references = (
            tuple(f"contract:{reference}" for reference in candidate.contract_tests)
            + tuple(f"security:{reference}" for reference in candidate.security_tests)
            + (f"performance:{candidate.performance_benchmark}",)
        )
        return CapabilityCard(
            name=candidate.name,
            capability_version=candidate.interface_version,
            lifecycle=CapabilityLifecycle.GAP,
            inputs=inputs,
            outputs=outputs,
            allowed_actions=("read_source",),
            resource_envelope=resource_envelope,
            latency_class=latency_class,
            deterministic=True,
            source_grade=source_grade,
            failure_modes=("source_unavailable", "parse_error", "quality_rejected"),
            test_references=references,
        )

    def export_strategy(
        self,
        *,
        name: str,
        strategy_version: str,
        rationale: str,
        experiment_spec: str,
        source_artifact_ids: tuple[str, ...],
        code: str,
        required_tests: tuple[str, ...],
        risk_notes: tuple[str, ...],
        environment: EnvironmentManifest,
    ) -> CandidateStrategy:
        return CandidateStrategy(
            name=name,
            strategy_version=strategy_version,
            rationale=rationale,
            experiment_spec=experiment_spec,
            source_artifact_ids=source_artifact_ids,
            code_hash=hashlib.sha256(code.encode()).hexdigest(),
            required_tests=required_tests,
            risk_notes=risk_notes,
            environment=environment,
        )

    def export_model_adapter(
        self,
        *,
        name: str,
        adapter_version: str,
        role: str,
        adapter_code: str,
        checkpoint_hash: str,
        contract_tests: tuple[str, ...],
        security_tests: tuple[str, ...],
        performance_benchmark: str,
        environment: EnvironmentManifest,
    ) -> ModelAdapterCandidate:
        return ModelAdapterCandidate(
            name=name,
            adapter_version=adapter_version,
            role=role,
            adapter_hash=hashlib.sha256(adapter_code.encode()).hexdigest(),
            checkpoint_hash=checkpoint_hash,
            contract_tests=contract_tests,
            security_tests=security_tests,
            performance_benchmark=performance_benchmark,
            environment=environment,
        )

    def export_runbook(
        self,
        *,
        name: str,
        runbook_version: str,
        trigger: str,
        steps: tuple[str, ...],
        rollback: str,
        environment: EnvironmentManifest,
    ) -> RunbookDraft:
        return RunbookDraft(
            name=name,
            runbook_version=runbook_version,
            trigger=trigger,
            steps=steps,
            rollback=rollback,
            environment=environment,
        )

    def export_capability(
        self,
        *,
        name: str,
        capability_version: str,
        interface: str,
        code: str,
        permissions: tuple[str, ...],
        environment: EnvironmentManifest,
        review_references: tuple[str, ...] = (),
    ) -> CapabilityBundle:
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        normalized_permissions = tuple(
            normalize_authority_action(permission) for permission in permissions
        )
        normalized_reviews = tuple(reference.strip() for reference in review_references)
        payload = json.dumps(
            {
                "name": name,
                "capability_version": capability_version,
                "interface": interface,
                "code_hash": code_hash,
                "permissions": normalized_permissions,
                "environment": environment.model_dump(mode="json"),
                "review_references": normalized_reviews,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        forbidden = {
            permission
            for permission in normalized_permissions
            if is_forbidden_authority_action(permission)
        }
        if forbidden:
            raise PermissionError(
                "Hermes exports cannot contain trading credentials or live authority"
            )
        return CapabilityBundle(
            name=name,
            capability_version=capability_version,
            interface=interface,
            code_hash=code_hash,
            permissions=permissions,
            artifact_hash=hashlib.sha256(payload.encode()).hexdigest(),
            environment=environment,
            review_references=review_references,
        )
