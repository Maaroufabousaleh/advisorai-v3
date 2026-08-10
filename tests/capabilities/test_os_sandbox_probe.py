import json

from scripts.probe_phase8_os_sandbox import _parse_container_output


def test_os_sandbox_probe_classifies_container_boundary_without_payloads():
    output = "\n".join(
        (
            "65532",
            "CapEff:\t0000000000000000",
            "filesystem_write_denied",
            "tmpfs_write_allowed",
            "network_probe_tool_available",
            "network_probe_denied",
            "unshare_tool_available",
            "unshare_escape_denied",
            "mount_escape_denied",
            "child_shell_allowed",
        )
    )
    result = _parse_container_output(output)
    assert result == {
        "uid": 65532,
        "effective_capabilities_zero": True,
        "filesystem_write_denied": True,
        "tmpfs_write_allowed": True,
        "network_probe_tool_available": True,
        "network_probe_tool_missing": False,
        "network_probe_denied": True,
        "unshare_tool_available": True,
        "unshare_tool_missing": False,
        "unshare_escape_denied": True,
        "unshare_escape_allowed": False,
        "mount_escape_denied": True,
        "mount_escape_allowed": False,
        "child_shell_allowed": True,
    }
    assert "CapEff" not in json.dumps(result)


def test_os_sandbox_probe_fails_closed_on_incomplete_container_output():
    result = _parse_container_output("0\n")
    assert result["uid"] == 0
    assert result["effective_capabilities_zero"] is False
    assert result["network_probe_denied"] is False
