import pytest

from advisorai.config import ConfigBundle, ConfigBundleStore


def test_config_rollback_reactivates_an_immutable_prior_bundle(tmp_path):
    store = ConfigBundleStore(tmp_path / "config-state")
    first = store.create({"risk": {"max_order_notional": "100"}})
    second = store.create({"risk": {"max_order_notional": "50"}})

    store.activate(first.content_hash, actor="reviewer", reason="initial approval")
    store.activate(second.content_hash, actor="reviewer", reason="tighten limit")
    restored = store.rollback(first.content_hash, actor="incident-owner", reason="revert test")

    assert restored.content_hash == first.content_hash
    assert store.active() is not None
    assert store.active().content_hash == first.content_hash  # type: ignore[union-attr]
    assert len(store.activation_log.read_text(encoding="utf-8").splitlines()) == 3


def test_config_bundle_rejects_hash_content_mismatch():
    with pytest.raises(ValueError, match="does not match"):
        ConfigBundle(content_hash="a" * 64, uri="bundle.json", content={"risk": "changed"})
