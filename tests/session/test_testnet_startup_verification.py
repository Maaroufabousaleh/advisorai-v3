"""Startup verification order for the supervised testnet launch path.

Uses the real Binance Spot Testnet adapter with mocked HTTP. Proves the
reported startup failure (baseline read on an unverified transport),
proves the fixed order reaches readiness, and proves invalid products or
quotes are refused before any submission is possible.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from advisorai.config import CredentialResolver
from advisorai.integrations import (
    BINANCE_SPOT_TESTNET_BASE_URL,
    VenueTransportError,
    build_binance_spot_testnet_transport,
)
from advisorai.session.testnet_launch import (
    SandboxLaunchRequest,
    read_testnet_baseline,
)
from tests.integrations.test_binance_spot import _account, _symbol


def _symbols() -> list[dict]:
    return [
        _symbol("BTCUSDT", "BTC", "USDT"),
        _symbol("ETHUSDT", "ETH", "USDT"),
    ]


def _requester(responses: list, calls: list):
    def request(method, url, headers, body, timeout):
        calls.append((method, url))
        if "/api/v3/exchangeInfo" in url:
            return (200, json.dumps({"symbols": responses[0]}).encode(), ())
        if "/api/v3/account" in url:
            return (200, json.dumps(responses[1]).encode(), ())
        if "/api/v3/time" in url:
            return (200, json.dumps({"serverTime": 1700000070000}).encode(), ())
        raise AssertionError(f"unexpected mocked call {method} {url}")

    return request


def _resolver() -> CredentialResolver:
    return CredentialResolver.from_mapping(
        {
            "ADVISORAI_VENUE_NAME": "binance_spot_testnet",
            "ADVISORAI_VENUE_ENVIRONMENT": "paper_testnet",
            "ADVISORAI_VENUE_BASE_URL": BINANCE_SPOT_TESTNET_BASE_URL,
            "ADVISORAI_VENUE_API_KEY": "synthetic-testnet-key",
            "ADVISORAI_VENUE_API_SECRET": "synthetic-testnet-secret",
        }
    )


def _launch(**overrides) -> SandboxLaunchRequest:
    values = {
        "provider": "binance_spot_testnet",
        "provider_instrument_id": "BTCUSDT",
        "quote_currency": "USDT",
        "max_submissions": 1,
    }
    values.update(overrides)
    return SandboxLaunchRequest(**values)


def test_unverified_transport_baseline_read_fails_with_adapter_guard() -> None:
    """The old call order: real adapter, fresh transport, no verification."""
    calls: list = []
    transport = build_binance_spot_testnet_transport(
        _resolver(),
        requester=_requester([_symbols(), _account()], calls),
    )
    assert transport.verified_symbol_ids == ()
    with pytest.raises(VenueTransportError, match="requires verified symbols"):
        transport.fetch_account_snapshot()


def test_baseline_verifies_instrument_and_quote_before_account_read(monkeypatch) -> None:
    """Fixed order: same-transport verification, then the baseline read."""
    import advisorai.session.testnet_launch as launch_module

    calls: list = []
    real_builder = build_binance_spot_testnet_transport

    def seeded_builder(resolver, **kwargs):
        return real_builder(
            resolver,
            requester=_requester([_symbols(), _account()], calls),
            **kwargs,
        )

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )
    assert launch_module is not None  # module import seam for monkeypatch target
    transport, baseline = read_testnet_baseline(
        resolver=_resolver(), launch=_launch()
    )
    assert transport.verified_symbol_ids == ("BTCUSDT",)
    spec = transport.verified_symbol_spec("BTCUSDT")
    assert spec.base_asset == "BTC"
    assert spec.quote_asset == "USDT"
    assert baseline.cash == Decimal("1000")
    assert baseline.positions == {"BTC": Decimal("0.010")}
    assert "/api/v3/exchangeInfo" in calls[0][1]
    assert any("/api/v3/account" in url for _, url in calls[1:])


def test_baseline_refuses_unknown_product_before_any_submission(monkeypatch) -> None:
    """Live catalog without BTCUSDT fails verification; no order possible."""
    calls: list = []
    real_builder = build_binance_spot_testnet_transport
    eth_only = [_symbol("ETHUSDT", "ETH", "USDT")]

    def seeded_builder(resolver, **kwargs):
        return real_builder(
            resolver,
            requester=_requester([eth_only, _account()], calls),
            **kwargs,
        )

    monkeypatch.setattr(
        "advisorai.integrations.build_binance_spot_testnet_transport", seeded_builder
    )
    with pytest.raises(VenueTransportError, match="does not contain BTCUSDT"):
        read_testnet_baseline(resolver=_resolver(), launch=_launch())
    assert calls
    assert all("/api/v3/order" not in url for _, url in calls)


def test_shared_verifier_refuses_quote_mismatch_before_any_submission() -> None:
    """The shared quote rule refuses live truth that disagrees with the request."""
    from advisorai.agents import EvidenceCouncil
    from advisorai.contracts import AssetClass, InstrumentIdentity
    from advisorai.execution import AccountState
    from advisorai.session.provider_profiles import (
        ProviderProfileComponents,
        ProviderProfileContext,
        ProviderProfileRequest,
        verify_selected_instrument,
    )
    from advisorai.session.testnet_launch import TestnetFixtureDataAdapter, _reference_factory

    calls: list = []
    transport = build_binance_spot_testnet_transport(
        _resolver(),
        requester=_requester([_symbols(), _account()], calls),
    )
    context = ProviderProfileContext(
        data=TestnetFixtureDataAdapter("USDC"),  # type: ignore[arg-type]
        evidence_council=EvidenceCouncil({}),
        decision_model_factory=_reference_factory("quiet"),
        initial_account=AccountState(cash=Decimal("100000"), marks={"BTC": Decimal("1")}),
        ledgers=None,  # type: ignore[arg-type]
        instrument=InstrumentIdentity(
            canonical_id="BTC",
            asset_class=AssetClass.CRYPTO,
            venue="paper",
            venue_symbol="BTCUSDT",
            base_asset="BTC",
            quote_asset="USDC",
        ),
        account_quote_currency="USDC",
        fx_rate_provider=lambda snapshot: None,  # type: ignore[arg-type]
    )
    request = ProviderProfileRequest(
        provider="binance_spot_testnet",
        provider_instrument_id="BTCUSDT",
        quote_currency="USDC",
    )
    with pytest.raises(ValueError, match="quote differs from exchange-verified"):
        verify_selected_instrument(
            transport, ProviderProfileComponents(context=context, request=request)
        )
    assert all("/api/v3/order" not in url for _, url in calls)
