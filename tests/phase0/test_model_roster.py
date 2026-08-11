import json
from pathlib import Path


def test_combined_phase0_roster_is_role_oriented_and_secret_free():
    path = Path("configs/models/phase0_model_roster.json")
    roster = json.loads(path.read_text(encoding="utf-8"))
    roles = roster["roles"]

    required = {
        "forecast_primary",
        "forecast_fast",
        "forecast_challengers",
        "probabilistic_forecast",
        "feature_regime_model",
        "finance_sentiment_primary",
        "finance_sentiment_fast",
        "finance_sentiment_challengers",
        "contributor_public",
        "private_worker",
        "private_reviewer",
        "blocked_execution",
    }
    assert required <= roles.keys()
    assert roster["live_capital"] == "not_approved"
    remote = roster["remote_bakeoff_evidence"]
    assert len(remote["report_hash"]) == 64
    assert len(remote["inventory_hash"]) == 64
    assert remote["billed_spend_usd"] < 0.25
    serialized = path.read_text(encoding="utf-8").lower()
    assert "api_key" not in serialized
    assert "secret" not in serialized
    assert ".safetensors" not in serialized
    assert ".bin" not in serialized


def test_tspulse_is_not_admitted_as_a_forecaster():
    role = json.loads(Path("configs/models/phase0_model_roster.json").read_text())["roles"][
        "feature_regime_model"
    ]
    assert role["candidate"] == "tspulse"
    assert role["price_forecast_prohibited"] is True


def test_stability_roster_points_to_the_active_immutable_root():
    roster = json.loads(Path("configs/models/phase0_model_roster.json").read_text())
    assert roster["stability"]["state"] == "passed"
    assert roster["stability"]["run_directory"].endswith(
        "phase0-selected-24h-terminal-sample-20260810-r3"
    )
    assert roster["stability"]["phase0_admission"] is False
    assert roster["stability"]["validation_sha256"] == (
        "1a6ab92c4f28d456776eac0c89ab099b0c1ef579c729fa8e458e4d5192b06949"
    )


def test_terminal_model_roles_are_stable_qualified_without_global_promotion():
    roster = json.loads(Path("configs/models/phase0_model_roster.json").read_text())
    roles = roster["roles"]
    expected = {
        "forecast_primary": "1eaec1958847ac85e3019d948e8050727a93bf937aa147924fde37f1aa415952",
        "forecast_fast": "1eaec1958847ac85e3019d948e8050727a93bf937aa147924fde37f1aa415952",
        "finance_sentiment_primary": "dd1b8ca98bd836c85be652373cd7fd49b8fd9cad03f37b79d5cb93b458a1f288",
        "finance_sentiment_fast": "5c08e2684732aaeb6c2585305509307356faf5db864094c75fc1395f39845d1e",
    }
    for role, evidence_hash in expected.items():
        assert roles[role]["state"] == "qualified"
        assert roles[role]["stability_status"] == "passed"
        assert roles[role]["evidence_hash"] == evidence_hash
    assert roster["live_capital"] == "not_approved"
