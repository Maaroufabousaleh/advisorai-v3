from scripts.probe_kata_sandbox import build_report


def test_kata_probe_is_read_only_and_does_not_fabricate_security_evidence():
    report = build_report()

    assert report["kata_security_evidence_pass"] is False
    assert report["phase8_admitted"] is False
    workload = report["adversarial_workload"]
    assert workload["execution_status"] == "not_run"
    assert workload["network_calls"] == 0
    assert workload["credentials_loaded"] is False
    assert workload["orders_attempted"] is False
    assert all(item["status"] == "not_run" for item in report["measurements"])
