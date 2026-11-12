#!/usr/bin/env python3
"""Record read-only Kata feasibility and honest backend-comparison status.

The command never installs Kata, pulls an image, starts a container, loads a
secret, or probes a repository mount.  A later operational run may supply a
reviewed immutable hostile workload once Kata is installed and attested; this
probe deliberately records that evidence as unavailable today.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from advisorai.capabilities.sandbox import (
    BackendMeasurement,
    HardenedDockerSandboxBackend,
    KataSandboxBackend,
    adversarial_probe_names,
    probe_host_capabilities,
)

SCHEMA = "advisorai.security.kata-host-feasibility.v1"


def _write_json(path: Path, payload: object) -> None:
    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _not_run(backend: str, reason: str) -> BackendMeasurement:
    return BackendMeasurement(
        backend=backend,
        status="not_run",
        limitations=(reason, "no image pull or workload execution was performed"),
        isolation_attestations={probe: None for probe in adversarial_probe_names()},
    )


def build_report() -> dict[str, object]:
    host = probe_host_capabilities()
    kata = KataSandboxBackend(host_report=host)
    docker = HardenedDockerSandboxBackend(host_report=host)
    kata_status = kata.availability()
    docker_status = docker.availability()
    measurements = (
        _not_run("kata", kata_status.reason),
        _not_run("hardened_docker", docker_status.reason),
    )
    return {
        "schema": SCHEMA,
        "measured_at": datetime.now(UTC).isoformat(),
        "host": {
            **host.model_dump(mode="json"),
            "host_prerequisites_satisfied": host.host_prerequisites_satisfied,
        },
        "backends": {
            "kata": kata_status.model_dump(mode="json"),
            "hardened_docker": docker_status.model_dump(mode="json"),
        },
        "adversarial_workload": {
            "probe_names": adversarial_probe_names(),
            "execution_status": "not_run",
            "network_calls": 0,
            "credentials_loaded": False,
            "orders_attempted": False,
        },
        "measurements": [measurement.model_dump(mode="json") for measurement in measurements],
        "kata_security_evidence_pass": False,
        "phase8_admitted": False,
        "limitations": (
            "Kata runtime installation and guest startup were not attempted",
            "no equivalent hardened-Docker/Kata hostile workload comparison exists yet",
            "the existing Phase-8 Docker probe remains separate and does not admit Phase 8",
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report()
    _write_json(args.output, report)
    print(
        json.dumps(
            {"path": str(args.output), "sha256": sha256(args.output.read_bytes()).hexdigest()}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
