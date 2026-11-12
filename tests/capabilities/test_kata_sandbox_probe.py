"""Kata sandbox probe: classifier, fail-closed parsing, boundary verdict."""

from __future__ import annotations

from scripts.probe_kata_adversarial import boundary_passed, parse_kata_output


def _good_output() -> str:
    return "\n".join(
        (
            "0",
            "CapEff:\t0000000000000000",
            "filesystem_write_denied",
            "tmpfs_write_allowed",
            "network_probe_tool_available",
            "network_probe_denied",
            "unshare_tool_available",
            "unshare_escape_denied",
            "mount_escape_denied",
            "child_shell_allowed",
            "kata_guest_kernel",
        )
    )


def test_parse_kata_output_classifies_vm_boundary():
    result = parse_kata_output(_good_output())
    assert result["uid"] == 0
    assert result["kata_guest_kernel"] is True
    assert result["kata_guest_kernel_absent"] is False
    assert boundary_passed(result) is True


def test_boundary_fails_closed_on_allowed_escape():
    result = parse_kata_output(_good_output() + "\nunshare_escape_allowed")
    assert boundary_passed(result) is False
    assert result["unshare_escape_allowed"] is True


def test_boundary_requires_guest_kernel_attestation():
    lines = [line for line in _good_output().splitlines() if line != "kata_guest_kernel"]
    result = parse_kata_output("\n".join([*lines, "kata_guest_kernel_absent"]))
    assert result["kata_guest_kernel"] is False
    assert boundary_passed(result) is False


def test_boundary_fails_closed_on_incomplete_output():
    result = parse_kata_output("0\n")
    assert result["uid"] == 0
    assert result["kata_guest_kernel"] is False
    assert boundary_passed(result) is False
