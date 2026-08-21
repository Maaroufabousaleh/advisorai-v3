from __future__ import annotations

from pathlib import Path

import yaml

from scripts.check_license_policy import check

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "configs/compliance/third-party-licenses.yaml"


def _write_inventory(tmp_path: Path, **component_overrides: object) -> Path:
    document = yaml.safe_load(INVENTORY.read_text(encoding="utf-8"))
    component = dict(document["components"][0])
    component.update(component_overrides)
    component["name"] = "fixture-component"
    component["distribution_profiles"] = ["source-visible"]
    document["components"] = [component]
    path = tmp_path / "inventory.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def test_repository_inventory_passes_source_visible_profile() -> None:
    assert check(INVENTORY, "source-visible") == []


def test_documentation_only_external_reference_is_not_distributed() -> None:
    document = yaml.safe_load(INVENTORY.read_text(encoding="utf-8"))
    reference = next(
        item
        for item in document["components"]
        if item["name"] == "architecture-only-external-tools"
    )
    assert reference["incorporation_mode"] == "DOCUMENTATION_REFERENCE"
    assert reference["redistributed"] is False
    assert check(INVENTORY, "source-visible") == []


def test_unknown_license_fails_when_component_is_redistributed(tmp_path: Path) -> None:
    path = _write_inventory(
        tmp_path,
        redistributed=True,
        license_spdx=None,
        risk_class="REVIEW_REQUIRED",
    )
    errors = check(path, "source-visible")
    assert any("unknown license_spdx" in error for error in errors)
    assert any("REVIEW_REQUIRED" in error for error in errors)


def test_restricted_component_fails_when_marked_redistributed(tmp_path: Path) -> None:
    path = _write_inventory(
        tmp_path,
        redistributed=True,
        license_spdx="CC-BY-NC-SA-3.0",
        risk_class="YELLOW",
        redistribution_restricted=True,
    )
    errors = check(path, "source-visible")
    assert any("redistribution permission is restricted" in error for error in errors)


def test_commercially_restricted_component_fails_when_marked_redistributed(
    tmp_path: Path,
) -> None:
    path = _write_inventory(
        tmp_path,
        redistributed=True,
        risk_class="YELLOW",
        commercial_restricted=True,
    )
    errors = check(path, "source-visible")
    assert any("commercial redistribution permission" in error for error in errors)


def test_unknown_redistribution_terms_fail_when_marked_redistributed(tmp_path: Path) -> None:
    path = _write_inventory(
        tmp_path,
        redistributed=True,
        risk_class="YELLOW",
        redistribution_restricted="unknown",
    )
    errors = check(path, "source-visible")
    assert any("restricted or unresolved" in error for error in errors)


def test_missing_notice_action_fails_for_redistributed_component(tmp_path: Path) -> None:
    path = _write_inventory(
        tmp_path,
        redistributed=True,
        risk_class="YELLOW",
        action_required="",
    )
    errors = check(path, "source-visible")
    assert any("required notice has no action_required" in error for error in errors)


def test_modified_green_component_fails_closed(tmp_path: Path) -> None:
    path = _write_inventory(tmp_path, modified=True, risk_class="GREEN")
    errors = check(path, "source-visible")
    assert any("modified component cannot be GREEN" in error for error in errors)


def test_profile_scope_is_required(tmp_path: Path) -> None:
    path = _write_inventory(tmp_path)
    errors = check(path, "container")
    assert any("no inventory components declare distribution profile" in error for error in errors)
