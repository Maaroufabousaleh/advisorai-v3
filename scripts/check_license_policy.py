#!/usr/bin/env python3
"""Validate the pinned AdvisorAI third-party license inventory offline.

The checker intentionally does not contact package indexes, model hubs, or
providers. It validates the evidence that has already been pinned into the
repository and fails closed for components marked for redistribution.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / "configs/compliance/third-party-licenses.yaml"
ALLOWED_RISK_CLASSES = {"GREEN", "YELLOW", "RED", "REVIEW_REQUIRED"}
REQUIRED_FIELDS = {
    "name",
    "version",
    "category",
    "license_spdx",
    "source_url",
    "incorporation_mode",
    "redistributed",
    "modified",
    "risk_class",
    "notice_required",
    "source_obligation",
    "redistribution_restricted",
    "commercial_restricted",
    "evidence_source",
    "evidence_checked_at",
    "current_compliance_state",
    "action_required",
    "distribution_profiles",
    "notes",
}


def _is_unknown(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"", "unknown", "n/a", "not established"}
    return False


def _load(path: Path) -> dict[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read inventory: {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in inventory: {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("inventory root must be a mapping")
    if document.get("schema_version") != 1:
        raise ValueError("unsupported or missing schema_version; expected 1")
    components = document.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("inventory must contain a non-empty components list")
    return document


def _validate_component(component: Any, index: int) -> list[str]:
    prefix = f"components[{index}]"
    if not isinstance(component, dict):
        return [f"{prefix}: component must be a mapping"]

    errors: list[str] = []
    missing = sorted(REQUIRED_FIELDS - set(component))
    errors.extend(f"{prefix}: missing required field {field!r}" for field in missing)
    if missing:
        return errors

    name = component["name"]
    if not isinstance(name, str) or not name.strip():
        errors.append(f"{prefix}: name must be a non-empty string")
    if component["risk_class"] not in ALLOWED_RISK_CLASSES:
        errors.append(f"{prefix} ({name}): invalid risk_class {component['risk_class']!r}")
    for field in ("redistributed", "modified", "notice_required"):
        if not isinstance(component[field], bool):
            errors.append(f"{prefix} ({name}): {field} must be boolean")
    if not isinstance(component["redistribution_restricted"], (bool, str)):
        errors.append(f"{prefix} ({name}): redistribution_restricted must be boolean or unknown")
    if not isinstance(component["source_url"], str) or not component["source_url"].strip():
        errors.append(f"{prefix} ({name}): source_url is required")
    if (
        not isinstance(component["evidence_source"], str)
        or not component["evidence_source"].strip()
    ):
        errors.append(f"{prefix} ({name}): evidence_source is required")

    redistributed = component["redistributed"] is True
    if redistributed:
        if _is_unknown(component["license_spdx"]):
            errors.append(f"{prefix} ({name}): redistributed component has unknown license_spdx")
        if component["risk_class"] in {"RED", "REVIEW_REQUIRED"}:
            errors.append(
                f"{prefix} ({name}): redistributed component is {component['risk_class']}"
            )
        if component["redistribution_restricted"] is True:
            errors.append(f"{prefix} ({name}): redistribution is explicitly restricted")
        if component["notice_required"] and _is_unknown(component.get("action_required")):
            errors.append(f"{prefix} ({name}): required notice has no action_required")
        source_obligation = component["source_obligation"]
        if source_obligation not in (False, None) and _is_unknown(component.get("action_required")):
            errors.append(f"{prefix} ({name}): source obligation has no action_required")

    if component["modified"] and component["risk_class"] == "GREEN":
        errors.append(f"{prefix} ({name}): modified component cannot be GREEN without review")
    return errors


def check(path: Path, profile: str) -> list[str]:
    document = _load(path)
    errors: list[str] = []
    components = document["components"]
    names: set[str] = set()
    profile_entries = 0
    for index, component in enumerate(components):
        errors.extend(_validate_component(component, index))
        if not isinstance(component, dict):
            continue
        name = component.get("name")
        if name in names:
            errors.append(f"components[{index}] ({name}): duplicate component name")
        names.add(name)
        profiles = component.get("distribution_profiles", [])
        if profiles and profile in profiles:
            profile_entries += 1

    if profile_entries == 0:
        errors.append(f"no inventory components declare distribution profile {profile!r}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument(
        "--profile",
        default="source-visible",
        help="profile label used to verify the inventory declares a relevant scope",
    )
    args = parser.parse_args(argv)
    try:
        errors = check(args.inventory, args.profile)
    except ValueError as exc:
        print(f"LICENSE_POLICY_FAIL: {exc}", file=sys.stderr)
        return 2
    if errors:
        print("LICENSE_POLICY_FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        f"LICENSE_POLICY_OK: {args.inventory} validated offline "
        f"for profile={args.profile}; no unresolved component is marked redistributed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
