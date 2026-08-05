from datetime import UTC, datetime
from decimal import Decimal

from advisorai.contracts import ModelCard
from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.models.authority import ModelAdmissionEvidence, ModelInventory


def test_model_inventory_can_disable_authority_on_drift():
    card = ModelCard(
        model_name="naive",
        model_version="v1",
        role="forecast",
        data_hash="a" * 64,
        code_hash="b" * 64,
        training_cutoff=datetime(2026, 8, 1, tzinfo=UTC),
        lifecycle_state="champion",
    )
    inventory = ModelInventory()
    assert inventory.register(card).authoritative
    disabled = inventory.disable("naive", "v1", "calibration drift")
    assert not disabled.authoritative


def test_model_inventory_rebuilds_registered_and_disabled_state(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "models.sqlite")
    card = ModelCard(
        model_name="naive",
        model_version="v1",
        role="forecast",
        data_hash="a" * 64,
        code_hash="b" * 64,
        training_cutoff=datetime(2026, 8, 1, tzinfo=UTC),
        lifecycle_state="champion",
    )
    inventory = ModelInventory(ledgers)
    inventory.register(card)
    inventory.disable("naive", "v1", "calibration drift")
    restarted = ModelInventory(ledgers)
    assert not restarted.get("naive", "v1").authoritative
    assert len(ledgers.events(LedgerNamespace.MODEL)) == 2


def test_model_inventory_requires_evidence_for_champion_promotion(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "model-promotion.sqlite")
    card = ModelCard(
        model_name="candidate",
        model_version="v1",
        role="forecast",
        data_hash="a" * 64,
        code_hash="b" * 64,
        training_cutoff=datetime(2026, 8, 1, tzinfo=UTC),
        lifecycle_state="challenger",
    )
    inventory = ModelInventory(ledgers)
    inventory.register(card)
    evidence = ModelAdmissionEvidence(
        model_name="candidate",
        model_version="v1",
        evaluation_hash="c" * 64,
        baseline_net_utility=Decimal("1"),
        candidate_net_utility=Decimal("2"),
        past_only=True,
        calibrated=True,
        resource_limit_passed=True,
        independent_review=True,
        reviewer="reviewer",
    )
    inventory.promote("candidate", "v1", "shadow", evidence)
    inventory.promote("candidate", "v1", "paper", evidence)
    authority = inventory.promote("candidate", "v1", "champion", evidence)
    assert authority.authoritative
    assert ModelInventory(ledgers).get("candidate", "v1").authoritative
