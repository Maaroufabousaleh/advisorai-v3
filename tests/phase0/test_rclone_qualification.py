from __future__ import annotations

from types import SimpleNamespace

from scripts.qualify_rclone_archive import _raw_listing


def test_raw_listing_uses_qualification_timeout_and_sanitizes_output():
    calls: list[tuple[list[str], dict[str, object]]] = []

    def recorder(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"encrypted/path\n")

    listing = _raw_listing(
        "raw:",
        {"RCLONE_CONFIG": "/tmp/rclone.conf"},
        recorder,
        timeout_seconds=180.0,
    )

    assert listing == {"encrypted/path"}
    assert calls[0][1]["timeout"] == 180.0
    assert calls[0][0] == [
        "rclone",
        "lsf",
        "--recursive",
        "--files-only",
        "--format",
        "p",
        "raw:",
    ]
