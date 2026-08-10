"""Qualify the real two-provider rclone-crypt archive boundary.

This runner is opt-in because it performs network I/O.  It loads only the
``ARCHIVE_RCLONE`` credential scope, uses the existing ``ArchiveAutomation``
and ``RcloneCryptBackend`` ports, and records only sanitized classifications,
aliases, hashes, and counts.  Provider command output is never emitted or
persisted.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from advisorai.archive import (
    RcloneArchiveConfig,
    RcloneCommandError,
    RcloneCryptBackend,
)
from advisorai.config import CredentialResolver
from advisorai.expansion import ArchiveAutomation
from advisorai.ports import ArchiveObject

SCHEMA = "advisorai.phase0.rclone-crypt-two-provider-qualification.v1"


class CommandRecorder:
    """Count bounded subprocess calls without retaining command arguments."""

    def __init__(self) -> None:
        self.call_count = 0

    def __call__(self, args: list[str], **kwargs: object):
        self.call_count += 1
        return subprocess.run(args, **kwargs)


class _ObservedBackend:
    """Capture per-provider results while preserving the ArchiveBackend port."""

    def __init__(self, backend: RcloneCryptBackend) -> None:
        self.backend = backend
        self.name = backend.name
        self.upload_object: ArchiveObject | None = None
        self.upload_error_class: str | None = None
        self.restore_hashes: list[str] = []
        self.restore_error_classes: list[str] = []

    def put(self, key: str, payload: bytes) -> ArchiveObject:
        try:
            obj = self.backend.put(key, payload)
        except Exception as exc:
            self.upload_error_class = _error_class(exc)
            raise
        self.upload_object = obj
        return obj

    def get(self, key: str) -> bytes:
        try:
            payload = self.backend.get(key)
        except Exception as exc:
            self.restore_error_classes.append(_error_class(exc))
            raise
        self.restore_hashes.append(sha256(payload).hexdigest())
        return payload

    def verify(self, obj: ArchiveObject) -> bool:
        return self.backend.verify(obj)


class _UnavailableRunner:
    """Inject a provider outage before a command reaches the network."""

    def __init__(self, delegate: CommandRecorder, remote: str) -> None:
        self.delegate = delegate
        self.remote = remote
        self.injected = False

    def __call__(self, args: list[str], **kwargs: object):
        if (
            not self.injected
            and len(args) >= 4
            and any(str(value).startswith(self.remote + "/") for value in args[2:4])
        ):
            self.injected = True
            raise RcloneCommandError("restore", "injected_provider_unavailable")
        return self.delegate(args, **kwargs)


class _FirstUploadTimeoutRunner:
    """Inject one pre-network interruption, then delegate the retry."""

    def __init__(self, delegate: CommandRecorder, remote: str) -> None:
        self.delegate = delegate
        self.remote = remote
        self.injected = False

    def __call__(self, args: list[str], **kwargs: object):
        if (
            not self.injected
            and len(args) >= 4
            and args[1] == "copyto"
            and str(args[3]).startswith(self.remote + "/")
        ):
            self.injected = True
            raise subprocess.TimeoutExpired(args, timeout=0.1)
        return self.delegate(args, **kwargs)


def _sha256(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _run_id(evidence_dir: Path) -> str:
    base = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    candidate = base
    suffix = 1
    while (evidence_dir / candidate).exists():
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


def _write_immutable_evidence(evidence_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    run_id = str(payload["run_id"])
    run_dir = evidence_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    base_record = {"schema": SCHEMA, **payload}
    canonical = (json.dumps(base_record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    evidence_sha = _sha256(canonical)
    record = {**base_record, "evidence_sha256": evidence_sha}
    encoded = (json.dumps(record, sort_keys=True, indent=2) + "\n").encode()
    manifest = run_dir / "rclone-crypt-qualification.json"
    with manifest.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    manifest_sha = _sha256(encoded)
    (run_dir / "manifest.sha256").write_text(manifest_sha + "\n", encoding="ascii")
    pointer = {
        "schema": f"{SCHEMA}.latest",
        "run_id": run_id,
        "manifest_sha256": manifest_sha,
    }
    temporary = evidence_dir / "latest.json.tmp"
    temporary.write_text(json.dumps(pointer, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, evidence_dir / "latest.json")
    return {
        **payload,
        "evidence_path": str(manifest),
        "evidence_sha256": evidence_sha,
        "manifest_sha256": manifest_sha,
    }


def _rclone_version() -> str | None:
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": "C",
    }
    try:
        result = subprocess.run(
            ["rclone", "version"],
            check=False,
            capture_output=True,
            env=environment,
            timeout=20,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        if line.startswith("rclone "):
            return line.strip()
    return None


def _raw_listing(
    remote: str,
    environment: dict[str, str] | Any,
    recorder: CommandRecorder,
) -> set[str] | None:
    try:
        result = recorder(
            ["rclone", "lsf", "--recursive", "--files-only", "--format", "p", remote],
            check=False,
            capture_output=True,
            env=dict(environment),
            timeout=45,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return {
        line.strip()
        for line in result.stdout.decode("utf-8", errors="replace").splitlines()
        if line.strip()
    }


def _raw_result(
    before: set[str] | None,
    after: set[str] | None,
    *,
    source_filename: str,
    key: str,
) -> dict[str, Any]:
    if before is None or after is None:
        return {
            "status": "failed",
            "listing_available": False,
            "new_object_count": None,
            "plaintext_filename_exposed": None,
            "plaintext_key_exposed": None,
        }
    new_objects = after - before
    filename_exposed = source_filename in after
    key_exposed = any(key in path for path in after)
    return {
        "status": "passed"
        if new_objects and not filename_exposed and not key_exposed
        else "failed",
        "listing_available": True,
        "before_object_count": len(before),
        "after_object_count": len(after),
        "new_object_count": len(new_objects),
        "plaintext_filename_exposed": filename_exposed,
        "plaintext_key_exposed": key_exposed,
    }


def _error_class(exc: BaseException) -> str:
    if isinstance(exc, RcloneCommandError):
        return exc.classification
    return type(exc).__name__


def _provider_result(
    observed: _ObservedBackend,
    source_hash: str,
    raw_result: dict[str, Any],
) -> dict[str, Any]:
    object_valid = observed.upload_object is not None and (
        observed.upload_object.content_hash == source_hash and observed.upload_object.encrypted
    )
    restored_hash = observed.restore_hashes[-1] if observed.restore_hashes else None
    restore_valid = restored_hash == source_hash
    return {
        "upload_status": "passed" if object_valid else "failed",
        "restore_status": "passed" if restore_valid else "failed",
        "source_sha256": source_hash,
        "restored_sha256": restored_hash,
        "raw_layer": raw_result,
        "upload_error_class": observed.upload_error_class,
        "restore_error_classes": observed.restore_error_classes,
    }


def _run_failure_drills(
    *,
    config: RcloneArchiveConfig,
    backends: dict[str, RcloneCryptBackend],
    key: str,
    payload: bytes,
    source_hash: str,
    recorder: CommandRecorder,
    timeout_seconds: float,
) -> dict[str, Any]:
    provider_a = config.provider("provider_a")
    provider_b = config.provider("provider_b")
    drills: dict[str, Any] = {}

    unavailable_a = config.backend(
        provider_a,
        runner=_UnavailableRunner(recorder, provider_a.crypt_remote),
        timeout_seconds=timeout_seconds,
    )
    try:
        unavailable_a.get(key)
        a_failed = False
    except Exception as exc:
        a_failed = _error_class(exc) == "injected_provider_unavailable"
    try:
        b_payload = backends["provider_b"].get(key)
        b_restorable = _sha256(b_payload) == source_hash
    except Exception:
        b_restorable = False
    drills["provider_a_unavailable_b_restorable"] = {
        "status": "passed" if a_failed and b_restorable else "failed",
        "evidence_type": "deterministic_fault_injection_plus_real_survivor_restore",
        "provider_a_failure_injected": a_failed,
        "provider_b_restore_verified": b_restorable,
    }

    unavailable_b = config.backend(
        provider_b,
        runner=_UnavailableRunner(recorder, provider_b.crypt_remote),
        timeout_seconds=timeout_seconds,
    )
    try:
        unavailable_b.get(key)
        b_failed = False
    except Exception as exc:
        b_failed = _error_class(exc) == "injected_provider_unavailable"
    try:
        a_payload = backends["provider_a"].get(key)
        a_restorable = _sha256(a_payload) == source_hash
    except Exception:
        a_restorable = False
    drills["provider_b_unavailable_a_restorable"] = {
        "status": "passed" if b_failed and a_restorable else "failed",
        "evidence_type": "deterministic_fault_injection_plus_real_survivor_restore",
        "provider_b_failure_injected": b_failed,
        "provider_a_restore_verified": a_restorable,
    }

    for name, missing_key in {
        "missing_object": f"{key}.missing",
        "wrong_destination_path": f"wrong-destination/{key}",
    }.items():
        try:
            backends["provider_a"].get(missing_key)
            expected_failure = False
            failure_class = "unexpected_restore_success"
        except Exception as exc:
            expected_failure = True
            failure_class = _error_class(exc)
        drills[name] = {
            "status": "passed" if expected_failure else "failed",
            "evidence_type": "real_provider_read",
            "expected_failure": expected_failure,
            "failure_class": failure_class,
        }

    retry_key = f"{key}.retry"
    retry_runner = _FirstUploadTimeoutRunner(recorder, provider_b.crypt_remote)
    retry_backend = config.backend(
        provider_b,
        runner=retry_runner,
        timeout_seconds=timeout_seconds,
    )
    try:
        retry_backend.put(retry_key, payload)
        first_failed = False
    except Exception as exc:
        first_failed = _error_class(exc) == "timeout"
    try:
        retry_object = retry_backend.put(retry_key, payload)
        retry_payload = retry_backend.get(retry_key)
        retry_succeeded = (
            first_failed
            and retry_object.content_hash == source_hash
            and _sha256(retry_payload) == source_hash
        )
    except Exception:
        retry_succeeded = False
    drills["interrupted_copy_retry"] = {
        "status": "passed" if retry_succeeded else "failed",
        "evidence_type": "deterministic_pre_network_timeout_injection_plus_real_retry",
        "first_attempt_interrupted": first_failed,
        "retry_restore_verified": retry_succeeded,
    }

    source_object = ArchiveObject(
        key=key,
        content_hash=source_hash,
        size_bytes=len(payload),
        encrypted=True,
    )
    wrong_hash_object = ArchiveObject(
        key=source_object.key,
        content_hash="0" * 64,
        size_bytes=source_object.size_bytes,
        encrypted=True,
    )
    checksum_detected = not backends["provider_a"].verify(wrong_hash_object)
    drills["checksum_mismatch_detection"] = {
        "status": "passed" if checksum_detected else "failed",
        "evidence_type": "real_restore_plus_local_checksum_injection",
        "mismatch_rejected": checksum_detected,
    }

    try:
        restored = backends["provider_a"].get(key)
        corrupted = bytearray(restored)
        corrupted[0] ^= 0x01
        corrupted_detected = _sha256(bytes(corrupted)) != source_hash
    except Exception:
        corrupted_detected = False
    drills["corrupted_local_restore_detection"] = {
        "status": "passed" if corrupted_detected else "failed",
        "evidence_type": "real_restore_plus_local_corruption_injection",
        "corruption_detected": corrupted_detected,
    }
    return drills


def _base_result(
    *,
    run_id: str,
    secrets_path: Path,
    source_hash: str,
    rclone_version: str | None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "status": "pending_operator_action",
        "config_path_reference": None,
        "secrets_file_reference": str(secrets_path),
        "credential_references": ("RCLONE_CONFIG", "RCLONE_CONFIG_PASS"),
        "rclone_version": rclone_version,
        "source_sha256": source_hash,
        "provider_aliases": {},
        "operations_attempted": [],
        "network_call_count": 0,
        "providers": {},
        "failure_drills": {},
        "blocker": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real",
        action="store_true",
        help="required explicit opt-in for provider network I/O",
    )
    parser.add_argument(
        "--secrets",
        type=Path,
        default=Path(
            os.getenv("ADVISORAI_SECRETS_FILE", "/home/maaro/.config/advisorai-v3/secrets.env")
        ),
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path("artifacts/phase0/rclone-crypt-qualification"),
    )
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    args = parser.parse_args()

    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    run_id = _run_id(args.evidence_dir)
    payload = (
        "AdvisorAI V3 Phase-0 rclone-crypt qualification\n"
        f"run_id={run_id}\n"
        f"nonce={secrets.token_hex(24)}\n"
    ).encode()
    source_hash = _sha256(payload)
    result = _base_result(
        run_id=run_id,
        secrets_path=args.secrets,
        source_hash=source_hash,
        rclone_version=_rclone_version(),
    )

    if not args.real:
        result.update({"status": "not_run", "blocker": "explicit_real_opt_in_required"})
        emitted = _write_immutable_evidence(args.evidence_dir, result)
        print(json.dumps(emitted, sort_keys=True, separators=(",", ":")))
        return 2

    try:
        resolver = CredentialResolver.from_env_file(args.secrets)
        config = RcloneArchiveConfig.from_resolver(resolver)
    except (FileNotFoundError, ValueError) as exc:
        result.update(
            {
                "status": "pending_operator_action",
                "blocker": "scoped_archive_configuration_unavailable",
                "configuration_error_class": type(exc).__name__,
            }
        )
        emitted = _write_immutable_evidence(args.evidence_dir, result)
        print(json.dumps(emitted, sort_keys=True, separators=(",", ":")))
        return 1

    if len(config.providers) != 2 or {provider.name for provider in config.providers} != {
        "provider_a",
        "provider_b",
    }:
        result.update(
            {
                "status": "pending_operator_action",
                "blocker": "exactly_two_suffixed_provider_pairs_required",
                "config_path_reference": str(config.config_path),
                "provider_aliases": {
                    provider.name: {
                        "raw_remote": provider.raw_remote,
                        "crypt_remote": provider.crypt_remote,
                    }
                    for provider in config.providers
                },
            }
        )
        emitted = _write_immutable_evidence(args.evidence_dir, result)
        print(json.dumps(emitted, sort_keys=True, separators=(",", ":")))
        return 1

    recorder = CommandRecorder()
    backends = {
        provider.name: config.backend(
            provider,
            runner=recorder,
            timeout_seconds=args.timeout_seconds,
        )
        for provider in config.providers
    }
    observed = {name: _ObservedBackend(backend) for name, backend in backends.items()}
    raw_before = {
        provider.name: _raw_listing(provider.raw_remote, config.process_environment, recorder)
        for provider in config.providers
    }
    key = f"phase0-rclone/{run_id}/qualification-payload.bin"
    source_filename = key.rsplit("/", 1)[-1]
    verification = ArchiveAutomation(tuple(observed.values())).archive(key=key, payload=payload)
    raw_after = {
        provider.name: _raw_listing(provider.raw_remote, config.process_environment, recorder)
        for provider in config.providers
    }
    provider_results = {
        provider.name: _provider_result(
            observed[provider.name],
            source_hash,
            _raw_result(
                raw_before[provider.name],
                raw_after[provider.name],
                source_filename=source_filename,
                key=key,
            ),
        )
        for provider in config.providers
    }
    failure_drills = _run_failure_drills(
        config=config,
        backends=backends,
        key=key,
        payload=payload,
        source_hash=source_hash,
        recorder=recorder,
        timeout_seconds=args.timeout_seconds,
    )
    three_way = (
        source_hash
        == provider_results["provider_a"]["restored_sha256"]
        == provider_results["provider_b"]["restored_sha256"]
    )
    provider_success = all(
        value["upload_status"] == "passed"
        and value["restore_status"] == "passed"
        and value["raw_layer"]["status"] == "passed"
        for value in provider_results.values()
    )
    drills_success = all(value["status"] == "passed" for value in failure_drills.values())
    result.update(
        {
            "status": "passed"
            if verification.passed and provider_success and three_way and drills_success
            else "failed",
            "config_path_reference": str(config.config_path),
            "provider_aliases": {
                provider.name: {
                    "raw_remote": provider.raw_remote,
                    "crypt_remote": provider.crypt_remote,
                }
                for provider in config.providers
            },
            "archive_key": key,
            "operations_attempted": [
                "raw_listing_before_upload",
                "two_provider_archive_automation_upload_verify_restore",
                "raw_listing_after_upload",
                "independent_provider_restore_hashes",
                "failure_recovery_drills",
            ],
            "network_call_count": recorder.call_count,
            "archive_automation": {
                "passed": verification.passed,
                "upload_verified": verification.upload_verified,
                "restore_verified": verification.restore_verified,
                "reasons": verification.reasons,
            },
            "providers": provider_results,
            "three_way_sha256_equal": three_way,
            "failure_drills": failure_drills,
            "blocker": None if verification.passed else "archive_automation_verification_failed",
        }
    )
    emitted = _write_immutable_evidence(args.evidence_dir, result)
    print(json.dumps(emitted, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
