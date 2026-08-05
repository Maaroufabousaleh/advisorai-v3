import pytest

from advisorai.config import MissionMode
from advisorai.services import ServiceKind, ServiceRegistry


def test_default_service_registry_preserves_canonical_ownership():
    registry = ServiceRegistry()
    market = registry.get("market-node")
    account = registry.get("account-ledger")
    assert market.kind is ServiceKind.ALWAYS_ON
    assert {"risk_kernel", "oms"}.issubset(market.owns)
    assert "account_state" in account.owns
    order = registry.startup_order()
    assert [item.name for item in order].index("resource-governor") < [
        item.name for item in order
    ].index("market-node")


def test_hermes_and_browser_are_not_admitted_in_trade_fast():
    registry = ServiceRegistry()
    with pytest.raises(PermissionError, match="not admitted"):
        registry.admit_mode("hermes-worker", MissionMode.TRADE_FAST)
    with pytest.raises(PermissionError, match="not admitted"):
        registry.admit_mode("browser-worker", MissionMode.TRADE_FAST)
    assert registry.admit_mode("hermes-worker", MissionMode.BUILDER).name == "hermes-worker"
