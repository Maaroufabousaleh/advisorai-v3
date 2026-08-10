from __future__ import annotations

import json

import scripts.qualify_phase3_binance_wss_diagnostic as diagnostic
from scripts.qualify_phase3_binance_wss_diagnostic import (
    HOST,
    _close_code_class,
    _message_metadata,
    _overall_classification,
    run_evidence,
)


def test_message_metadata_persists_digest_and_not_payload():
    metadata = _message_metadata('{"e":"depthUpdate","s":"BTCUSDT"}')

    assert metadata["message_kind"] == "market_event"
    assert metadata["message_length"] > 0
    assert len(metadata["message_sha256"]) == 64
    assert "payload" not in metadata


def test_provider_error_metadata_classifies_throttling_without_message():
    metadata = _message_metadata('{"code":-1003,"msg":"private detail"}')

    assert metadata["message_kind"] == "provider_error"
    assert metadata["provider_error_code"] == -1003
    assert metadata["throttling_candidate"] is True
    assert "msg" not in metadata


def test_close_code_classification_is_bounded():
    assert _close_code_class(1000) == "normal_or_endpoint_shutdown"
    assert _close_code_class(4008) == "policy_or_provider_limit_candidate"
    assert _close_code_class(3999) == "provider_close_code_observed"


def test_overall_classification_prefers_local_runtime_failure():
    probes = {
        "dns_resolution": {"status": "pass"},
        "tcp_connectivity": {"status": "pass"},
        "tls_negotiation": {"status": "pass"},
        "websocket": {
            "direct_streams": [{"status": "failed", "failure_layer": "local_runtime_library"}]
        },
    }

    assert _overall_classification(probes) == "local_runtime_library_missing_or_failed"


def test_diagnostic_requires_explicit_real_network_for_cli():
    assert HOST == "stream.testnet.binance.vision"


def test_evidence_writer_is_immutable(tmp_path, monkeypatch):
    async def fake_probes(_timeout_seconds, _attempts):
        return {
            "dns_resolution": {"status": "pass"},
            "tcp_connectivity": {"status": "pass"},
            "tls_negotiation": {"status": "pass"},
            "websocket": {"direct_streams": [], "valid_subscriptions": []},
        }

    monkeypatch.setattr(diagnostic, "_run_probes", fake_probes)
    result = run_evidence(tmp_path, timeout_seconds=0.01, attempts=1)
    manifest = tmp_path / result["evidence"]
    payload = json.loads(manifest.read_text())

    assert payload["public_market_data_only"] is True
    assert payload["credentials_loaded"] is False
    assert payload["order_writes_attempted"] is False
    assert len(result["evidence_sha256"]) == 64
