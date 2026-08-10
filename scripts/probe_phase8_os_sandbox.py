#!/usr/bin/env python3
"""Measure a disposable host-supported OS boundary for Phase 8.

This probe is deliberately narrower than formal Hermes admission.  It uses a
pre-existing local Alpine image with Docker's network, capability, read-only
filesystem, process-count, and no-new-privileges controls.  It never mounts
the repository or a credential directory, never pulls an image, and records
only sanitized command classifications and capability outcomes.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

SCHEMA = "advisorai.phase8.os-sandbox-probe.v1"
IMAGE_REFERENCE = "alpine:latest"
MAX_RUNTIME_SECONDS = 20
CONTAINER_SCRIPT = (
    "id -u; "
    "grep CapEff /proc/self/status; "
    "touch /etc/advisorai-probe 2>/dev/null || echo filesystem_write_denied; "
    "touch /tmp/advisorai-probe 2>/dev/null && echo tmpfs_write_allowed; "
    "command -v wget >/dev/null && echo network_probe_tool_available || echo network_probe_tool_missing; "
    "wget -q -T 2 -O /dev/null http://192.0.2.1 2>/dev/null || echo network_probe_denied; "
    "command -v unshare >/dev/null && echo unshare_tool_available || echo unshare_tool_missing; "
    "if command -v unshare >/dev/null; then "
    "unshare -Ur true >/dev/null 2>&1 && echo unshare_escape_allowed || echo unshare_escape_denied; "
    "fi; "
    "mkdir -p /tmp/mount-probe; "
    "mount -t tmpfs none /tmp/mount-probe >/dev/null 2>&1 && echo mount_escape_allowed || echo mount_escape_denied; "
    "(sh -c true >/dev/null 2>&1 && echo child_shell_allowed)"
)


def _sha256(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _write_immutable_json(path: Path, payload: object) -> None:
    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise FileExistsError(f"immutable evidence differs: {path}")
        return
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _process_environment() -> dict[str, str]:
    """Force the local Docker socket and never inherit arbitrary secrets."""

    return {"PATH": os.defpath, "DOCKER_HOST": "unix:///var/run/docker.sock"}


def _run(command: list[str]) -> dict[str, object]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=MAX_RUNTIME_SECONDS,
            check=False,
            env=_process_environment(),
        )
    except FileNotFoundError as exc:
        return {"available": False, "error_class": type(exc).__name__}
    except subprocess.TimeoutExpired as exc:
        return {"available": False, "error_class": type(exc).__name__}
    return {
        "available": True,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
    }


def _public_result(result: dict[str, object]) -> dict[str, object]:
    if not result.get("available"):
        return {"available": False, "error_class": result.get("error_class")}
    return {"available": True, "returncode": result.get("returncode")}


def _parse_container_output(stdout: str) -> dict[str, object]:
    lines = {line.strip() for line in stdout.splitlines() if line.strip()}
    cap_eff = next((line for line in lines if line.startswith("CapEff:")), None)
    uid = next((line for line in lines if line.isdigit()), None)
    return {
        "uid": int(uid) if uid is not None else None,
        "effective_capabilities_zero": cap_eff == "CapEff:\t0000000000000000"
        or cap_eff == "CapEff: 0000000000000000",
        "filesystem_write_denied": "filesystem_write_denied" in lines,
        "tmpfs_write_allowed": "tmpfs_write_allowed" in lines,
        "network_probe_tool_available": "network_probe_tool_available" in lines,
        "network_probe_tool_missing": "network_probe_tool_missing" in lines,
        "network_probe_denied": "network_probe_denied" in lines,
        "unshare_tool_available": "unshare_tool_available" in lines,
        "unshare_tool_missing": "unshare_tool_missing" in lines,
        "unshare_escape_denied": "unshare_escape_denied" in lines,
        "unshare_escape_allowed": "unshare_escape_allowed" in lines,
        "mount_escape_denied": "mount_escape_denied" in lines,
        "mount_escape_allowed": "mount_escape_allowed" in lines,
        "child_shell_allowed": "child_shell_allowed" in lines,
    }


def _docker_info() -> dict[str, object]:
    version = _run(["docker", "info", "--format", "{{.ServerVersion}}"])
    cgroup = _run(["docker", "info", "--format", "{{.CgroupVersion}}"])
    security = _run(["docker", "info", "--format", "{{json .SecurityOptions}}"])
    options: list[str] = []
    if security.get("available") and security.get("returncode") == 0:
        try:
            decoded = json.loads(str(security.get("stdout", "")))
        except json.JSONDecodeError:
            decoded = []
        if isinstance(decoded, list):
            options = [str(item).split(",", 1)[0][:80] for item in decoded]
    return {
        "version": (
            str(version.get("stdout", "")).strip()[:80]
            if version.get("available") and version.get("returncode") == 0
            else None
        ),
        "cgroup_version": (
            str(cgroup.get("stdout", "")).strip()[:40]
            if cgroup.get("available") and cgroup.get("returncode") == 0
            else None
        ),
        "security_options": options,
        "command_results": {
            "version": _public_result(version),
            "cgroup": _public_result(cgroup),
            "security": _public_result(security),
        },
    }


def run_evidence(output_root: Path) -> tuple[Path, dict[str, object], str]:
    output_root = output_root.expanduser().resolve()
    run_id_base = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_id = run_id_base
    suffix = 1
    while (output_root / run_id).exists():
        suffix += 1
        run_id = f"{run_id_base}-{suffix}"
    run_directory = output_root / run_id
    run_directory.mkdir(parents=True, exist_ok=False)

    docker = _docker_info()
    image = _run(["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE_REFERENCE])
    image_id = (
        str(image.get("stdout", "")).strip()[:128]
        if image.get("available") and image.get("returncode") == 0
        else None
    )
    image_target = image_id or IMAGE_REFERENCE
    namespace = _run(["unshare", "--user", "--map-root-user", "--mount", "--pid", "--fork", "true"])
    container = _run(
        [
            "docker",
            "run",
            "--pull=never",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=64",
            "--memory=256m",
            "--cpus=1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev",
            "--user",
            "0:0",
            image_target,
            "sh",
            "-c",
            CONTAINER_SCRIPT,
        ]
    )
    attestations = _parse_container_output(str(container.get("stdout", "")))
    container_passed = bool(
        container.get("available")
        and container.get("returncode") == 0
        and attestations["uid"] == 0
        and attestations["filesystem_write_denied"]
        and attestations["tmpfs_write_allowed"]
        and attestations["network_probe_tool_available"]
        and attestations["network_probe_denied"]
        and attestations["unshare_tool_available"]
        and attestations["unshare_escape_denied"]
        and attestations["mount_escape_denied"]
        and attestations["effective_capabilities_zero"]
    )
    report: dict[str, object] = {
        "schema": SCHEMA,
        "run_id": run_id,
        "measured_at": datetime.now(UTC).isoformat(),
        "runner_code_sha256": _sha256(Path(__file__).read_bytes()),
        "host_kernel_release": platform.release()[:128],
        "docker_server_version": docker["version"],
        "docker_cgroup_version": docker["cgroup_version"],
        "docker_security_options": docker["security_options"],
        "image_reference": IMAGE_REFERENCE,
        "image_id": image_id,
        "process_environment": "minimal_local_docker_only",
        "external_network_calls": 0,
        "namespace_probe": _public_result(namespace),
        "image_probe": _public_result(image),
        "container_probe": _public_result(container),
        "container_attestations": attestations,
        "container_boundary_measured": container_passed,
        "limitations": {
            "native_syscall_containment": "not_attested",
            "native_escape_probes": "unshare_and_mount_denied; universal_containment_not_attested",
            "c_extension_containment": "not_attested",
            "production_tree_mount": "not_attested_not_mounted",
            "credential_mount": "not_attested_not_mounted",
            "process_spawn": "child_shell_allowed_within_pids_limit",
        },
        "formal_admission": False,
        "gate_state": "EXTERNALLY_MEASURED / PENDING_EXTERNAL_EVIDENCE",
        "notes": (
            "Disposable local image only; no repository, secrets, broker, order, or production mounts.",
            "This is a host-boundary measurement and does not admit Hermes or Phase 8.",
        ),
    }
    report_path = run_directory / "phase8-os-sandbox-probe.json"
    _write_immutable_json(report_path, report)
    evidence_sha256 = _sha256(report_path.read_bytes())
    manifest = {
        "schema": f"{SCHEMA}.manifest",
        "run_id": run_id,
        "report": report_path.name,
        "evidence_sha256": evidence_sha256,
    }
    _write_immutable_json(run_directory / "evidence-manifest.json", manifest)
    latest_path = output_root / "latest.json"
    temporary = output_root / ".latest.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "schema": f"{SCHEMA}.latest",
                    "run_id": run_id,
                    "evidence_sha256": evidence_sha256,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, latest_path)
    return report_path, report, evidence_sha256


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path("artifacts/phase8/os-sandbox-probe"),
    )
    args = parser.parse_args()
    path, report, evidence_sha256 = run_evidence(args.evidence_dir)
    print(
        json.dumps(
            {
                "report": str(path),
                "evidence_sha256": evidence_sha256,
                "container_boundary_measured": report["container_boundary_measured"],
                "gate_state": report["gate_state"],
                "formal_admission": report["formal_admission"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["container_boundary_measured"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
