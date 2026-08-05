from advisorai.archive import RcloneCryptBackend


def test_rclone_backend_upload_restore_is_explicit_and_key_safe():
    calls = []
    payloads = {}

    class Result:
        returncode = 0

    def runner(args, **kwargs):
        calls.append(args)
        if args[2].startswith("/tmp/"):
            with open(args[2], "rb") as source:
                payloads[args[3]] = source.read()
        else:
            with open(args[3], "wb") as destination:
                destination.write(payloads[args[2]])
        return Result()

    backend = RcloneCryptBackend("remote:archive", runner=runner)
    backend.put("bundle/state.bin", b"state")
    assert backend.get("bundle/state.bin") == b"state"
    assert len(calls) == 2

    try:
        backend.put("../escape", b"bad")
    except ValueError as exc:
        assert "parent traversal" in str(exc)
    else:
        raise AssertionError("rclone backend accepted a traversal key")
