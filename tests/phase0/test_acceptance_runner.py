from scripts import verify_acceptance


def test_acceptance_runner_stops_at_first_failed_phase(monkeypatch):
    called: list[int] = []

    def fake_run(suite):
        called.append(suite.number)
        return suite.number == 0, "passed" if suite.number == 0 else "failed"

    monkeypatch.setattr(verify_acceptance, "_run_suite", fake_run)
    monkeypatch.setattr(verify_acceptance.sys, "argv", ["verify_acceptance.py"])
    assert verify_acceptance.main() == 1
    assert called == [0, 1]
