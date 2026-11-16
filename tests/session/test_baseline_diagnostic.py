"""P02: the baseline diagnostic flags exactly the launcher-rejected assets.

Exercises ``classify_balances`` over synthetic ``/api/v3/account``
``balances`` payloads (no network, no credentials, no venue writes) and
cross-checks the verdict against the real ``read_testnet_baseline()``
predicate via the real transport with mocked HTTP.
"""

from __future__ import annotations

import base64
import json
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, "scripts")

from diagnose_testnet_baseline import classify_balances, classify_coinbase_accounts
from diagnose_testnet_baseline import main as diagnostic_main

from advisorai.config import CredentialResolver
from advisorai.integrations import (
    BINANCE_SPOT_TESTNET_BASE_URL,
    COINBASE_EXCHANGE_SANDBOX_BASE_URL,
    build_binance_spot_testnet_transport,
    build_coinbase_exchange_sandbox_transport,
)
from advisorai.session.testnet_launch import TestnetLaunchRequest, read_testnet_baseline
from tests.integrations.test_binance_spot import _symbol


def _balances(*records: tuple[str, str, str]) -> list[dict[str, str]]:
    return [
        {"asset": asset, "free": free, "locked": locked} for asset, free, locked in records
    ]


def _account_payload(balances: list[dict[str, str]]) -> dict:
    return {"accountType": "SPOT", "canTrade": True, "balances": balances}


def _requester(account: dict, calls: list):
    def request(method, url, headers, body, timeout):
        calls.append((method, url))
        if "/api/v3/exchangeInfo" in url:
            symbols = [_symbol("BTCUSDT", "BTC", "USDT"), _symbol("ETHUSDT", "ETH", "USDT")]
            return (200, json.dumps({"symbols": symbols}).encode(), ())
        if "/api/v3/account" in url:
            return (200, json.dumps(account).encode(), ())
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


def _launch() -> TestnetLaunchRequest:
    return TestnetLaunchRequest(
        provider="binance_spot_testnet",
        provider_instrument_id="BTCUSDT",
        quote_currency="USDT",
        max_submissions=0,
    )


def test_diagnostic_passes_profile_only_balances() -> None:
    """BTC + USDT (plus zero dust) classify as guard-compatible."""
    balances = _balances(
        ("BTC", "0.010", "0.000"),
        ("USDT", "9994.50", "0.000"),
        ("ETH", "0.000", "0.000"),
    )
    assert classify_balances(balances) == []


def test_diagnostic_flags_each_nonzero_foreign_leg() -> None:
    """Each foreign asset reports exactly which leg is nonzero."""
    balances = _balances(
        ("BTC", "0.010", "0.000"),
        ("USDT", "9994.50", "0.000"),
        ("ETH", "0.500", "0.000"),
        ("BNB", "0.000", "1.250"),
    )
    assert classify_balances(balances) == [
        {"asset": "ETH", "free_nonzero": True, "locked_nonzero": False},
        {"asset": "BNB", "free_nonzero": False, "locked_nonzero": True},
    ]


def test_diagnostic_verdict_matches_launcher_predicate(monkeypatch) -> None:
    """The diagnostic preview matches exactly what the launcher pins.

    Real transport + mocked HTTP; the flagged payload pins the faucet
    asset into the baseline while the clean payload seeds the baseline
    with BTC only. Asserts the diagnostic and the launcher agree on
    both the must-pin and must-skip balances, with GET reads only.
    """
    import advisorai.integrations as integrations_module

    clean_balances = _balances(("BTC", "0.010", "0.000"), ("USDT", "9994.50", "0.000"))
    dirty_balances = _balances(
        ("BTC", "0.010", "0.000"),
        ("USDT", "9994.50", "0.000"),
        ("ETH", "0.500", "0.000"),
    )
    clean_calls: list = []
    monkeypatch.setattr(
        integrations_module,
        "build_binance_spot_testnet_transport",
        lambda resolver: build_binance_spot_testnet_transport(
            resolver, requester=_requester(_account_payload(clean_balances), clean_calls)
        ),
    )
    _, baseline = read_testnet_baseline(resolver=_resolver(), launch=_launch())
    assert baseline.positions == {"BTC": Decimal("0.010")}
    assert classify_balances(clean_balances) == []

    dirty_calls: list = []
    monkeypatch.setattr(
        integrations_module,
        "build_binance_spot_testnet_transport",
        lambda resolver: build_binance_spot_testnet_transport(
            resolver, requester=_requester(_account_payload(dirty_balances), dirty_calls)
        ),
    )
    _, pinned = read_testnet_baseline(resolver=_resolver(), launch=_launch())
    assert pinned.positions == {"BTC": Decimal("0.010"), "asset:ETH": Decimal("0.500")}
    assert classify_balances(dirty_balances) == [
        {"asset": "ETH", "free_nonzero": True, "locked_nonzero": False}
    ]
    assert clean_calls and dirty_calls
    assert all(method == "GET" for method, _ in clean_calls + dirty_calls)


def test_coinbase_snapshot_rejects_unmapped_nonzero_asset() -> None:
    """P02 prep: the sibling adapter refuses unmapped nonzero assets.

    Coinbase raises (rather than preserving ``asset:`` markers) when a
    nonzero currency has no verified product, so no foreign position
    can silently reach the baseline. Synthetic transport double + a
    stub ``_accounts`` payload; no network, no credentials.
    """
    from advisorai.integrations import (
        COINBASE_EXCHANGE_SANDBOX_BASE_URL,
        build_coinbase_exchange_sandbox_transport,
    )
    from advisorai.integrations.venue import VenueTransportError

    coinbase_resolver = CredentialResolver.from_mapping(
        {
            "ADVISORAI_VENUE_NAME": "coinbase_exchange_sandbox",
            "ADVISORAI_VENUE_ENVIRONMENT": "paper_testnet",
            "ADVISORAI_VENUE_BASE_URL": COINBASE_EXCHANGE_SANDBOX_BASE_URL,
            "ADVISORAI_VENUE_API_KEY": "synthetic-testnet-key",
            "ADVISORAI_VENUE_API_SECRET": base64.b64encode(
                b"synthetic-testnet-secret"
            ).decode("ascii"),
            "ADVISORAI_VENUE_PASSPHRASE": "synthetic-testnet-passphrase",
        }
    )
    transport = build_coinbase_exchange_sandbox_transport(coinbase_resolver)
    transport.verify_product_mappings(
        [
            {
                "id": "BTC-USD",
                "base_currency": "BTC",
                "quote_currency": "USD",
                "base_increment": "0.00000001",
                "quote_increment": "0.01",
                "status": "online",
            }
        ],
        required=("BTC-USD",),
    )
    transport._accounts = lambda: [  # type: ignore[method-assign]
        {"currency": "USD", "balance": "10000", "available": "10000"},
        {"currency": "BTC", "balance": "0.01", "available": "0.01"},
        {"currency": "ETH", "balance": "0.50", "available": "0.50"},
    ]
    with pytest.raises(VenueTransportError, match="non-zero unmapped asset ETH"):
        transport.fetch_account_snapshot()


def test_diagnostic_rejects_misshapen_payloads() -> None:
    """P02 prep: the diagnostic fails closed on misshapen payloads."""
    with pytest.raises(ValueError, match="must be a sequence of balance records"):
        classify_balances({"asset": "ETH"})
    with pytest.raises(ValueError, match="must be a sequence of balance records"):
        classify_balances("ETH")
    with pytest.raises(ValueError, match="must be an object"):
        classify_balances([("ETH", "0.5", "0.0")])


def _synthetic_secrets_file(tmp_path) -> object:
    path = tmp_path / "synthetic-diagnostic.env"
    path.write_text(
        "\n".join(
            [
                "ADVISORAI_VENUE_NAME=binance_spot_testnet",
                "ADVISORAI_VENUE_ENVIRONMENT=paper_testnet",
                f"ADVISORAI_VENUE_BASE_URL={BINANCE_SPOT_TESTNET_BASE_URL}",
                "ADVISORAI_VENUE_API_KEY=synthetic-diagnostic-key",
                "ADVISORAI_VENUE_API_SECRET=synthetic-diagnostic-secret",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_diagnostic_entry_point_accepts_real_adapter_tuple(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02 unblock: the CLI entry point consumes the real adapter's tuple.

    Runs ``main()`` end to end through the real transport factory
    with mocked HTTP: a synthetic secrets file, one ``GET
    /api/v3/account``, and the tuple ``list_balances()`` returns.
    Compatible balances exit 0; the output names no amounts and no
    secrets. Previously crashed with ``balances payload must be a
    list``.
    """
    import diagnose_testnet_baseline as diagnostic_module

    balances = _balances(("BTC", "0.010", "0.000"), ("USDT", "9994.50", "0.000"))
    calls: list = []
    monkeypatch.setattr(
        diagnostic_module,
        "build_binance_spot_testnet_transport",
        lambda resolver: build_binance_spot_testnet_transport(
            resolver, requester=_requester(_account_payload(balances), calls)
        ),
    )
    code = diagnostic_main(["--secrets", str(_synthetic_secrets_file(tmp_path))])
    out, err = capsys.readouterr()
    assert code == 0
    assert "PASS" in out
    assert calls and all(method == "GET" for method, _ in calls)
    assert "synthetic-diagnostic-key" not in out + err
    assert "synthetic-diagnostic-secret" not in out + err
    assert "9994.50" not in out + err
    assert "0.010" not in out + err


def test_diagnostic_entry_point_reports_pinned_assets(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02 unblock: the CLI entry point exits 0 with the pin preview.

    Same end-to-end path with a faucet asset: exit 0, names the pinned
    symbol and the nonzero leg only. No amounts, no secrets; GET
    reads only.
    """
    import diagnose_testnet_baseline as diagnostic_module

    balances = _balances(
        ("BTC", "0.010", "0.000"),
        ("USDT", "9994.50", "0.000"),
        ("ETH", "0.500", "0.000"),
    )
    calls: list = []
    monkeypatch.setattr(
        diagnostic_module,
        "build_binance_spot_testnet_transport",
        lambda resolver: build_binance_spot_testnet_transport(
            resolver, requester=_requester(_account_payload(balances), calls)
        ),
    )
    code = diagnostic_main(["--secrets", str(_synthetic_secrets_file(tmp_path))])
    out, err = capsys.readouterr()
    assert code == 0
    assert "PASS" in out
    assert "pinned=ETH" in out
    assert "nonzero=free" in out
    assert calls and all(method == "GET" for method, _ in calls)
    assert "synthetic-diagnostic-key" not in out + err
    assert "synthetic-diagnostic-secret" not in out + err
    assert "0.500" not in out + err
    assert "9994.50" not in out + err


def test_diagnostic_entry_point_fails_closed_on_transport_error(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02 unblock: malformed data and transport faults exit 2.

    A non-object account payload and a network failure both reach
    the documented input/transport-error exit instead of raising.
    The stderr line names only the exception type.
    """
    import diagnose_testnet_baseline as diagnostic_module

    def _requester_for(payload_calls: list, item: object):
        def request(method, url, headers, body, timeout):
            payload_calls.append((method, url))
            return item

        return request

    malformed_calls: list = []
    monkeypatch.setattr(
        diagnostic_module,
        "build_binance_spot_testnet_transport",
        lambda resolver: build_binance_spot_testnet_transport(
            resolver,
            requester=_requester_for(
                malformed_calls, (200, json.dumps(["not-an-object"]).encode(), ())
            ),
        ),
    )
    code = diagnostic_main(["--secrets", str(_synthetic_secrets_file(tmp_path))])
    _out, err = capsys.readouterr()
    assert code == 2
    assert err.strip() == "diagnostic failed: VenueTransportError"
    assert all(method == "GET" for method, _ in malformed_calls)

    import urllib.error

    failing_calls: list = []

    def _raising_requester(method, url, headers, body, timeout):
        failing_calls.append((method, url))
        raise urllib.error.URLError("synthetic network fault")

    monkeypatch.setattr(
        diagnostic_module,
        "build_binance_spot_testnet_transport",
        lambda resolver: build_binance_spot_testnet_transport(
            resolver, requester=_raising_requester
        ),
    )
    code = diagnostic_main(["--secrets", str(_synthetic_secrets_file(tmp_path))])
    _out, err = capsys.readouterr()
    assert code == 2
    assert err.strip() == "diagnostic failed: VenueTransportError"
    assert all(method == "GET" for method, _ in failing_calls)


def test_diagnostic_fails_closed_on_malformed_and_nonnumeric_values() -> None:
    """P02: malformed/non-finite legs flag; symbols normalize to upper.

    A foreign asset with a garbage ``free`` leg, a ``NaN`` leg, or an
    ``Infinity`` leg must all flag (fail closed toward REJECT), never
    silently pass; lowercase symbols normalize before comparison.
    Pure classifier over synthetic payloads; no network, no secrets.
    """
    flagged = classify_balances(
        _balances(
            ("eth", "not-a-number", "0.000"),
            ("bnb", "NaN", "0.000"),
            ("xrp", "0.000", "Infinity"),
            ("doge", "-Infinity", "-Infinity"),
        )
    )
    assert flagged == [
        {"asset": "ETH", "free_nonzero": True, "locked_nonzero": False},
        {"asset": "BNB", "free_nonzero": True, "locked_nonzero": False},
        {"asset": "XRP", "free_nonzero": False, "locked_nonzero": True},
        {"asset": "DOGE", "free_nonzero": True, "locked_nonzero": True},
    ]
    assert classify_balances(_balances(("btc", "0.010", "0.000"))) == []


def _coinbase_accounts(*records: tuple[str, str, str, str]) -> list[dict[str, str]]:
    return [
        {"currency": currency, "balance": balance, "available": available, "hold": hold}
        for currency, balance, available, hold in records
    ]


def _coinbase_requester(accounts: list[dict[str, str]], calls: list):
    def request(method, url, headers, body, timeout):
        calls.append((method, url))
        path = url.split("?", 1)[0].rstrip("/")
        if path.endswith("/accounts"):
            return (200, json.dumps(accounts).encode(), ())
        if path.endswith("/time"):
            return (200, json.dumps({"epoch": 1700000000.0}).encode(), ())
        raise AssertionError(f"unexpected mocked call {method} {url}")

    return request


def _coinbase_secrets_file(tmp_path) -> object:
    import base64 as _base64

    path = tmp_path / "synthetic-coinbase-diagnostic.env"
    path.write_text(
        "\n".join(
            [
                "ADVISORAI_VENUE_NAME=coinbase_exchange_sandbox",
                "ADVISORAI_VENUE_ENVIRONMENT=paper_testnet",
                f"ADVISORAI_VENUE_BASE_URL={COINBASE_EXCHANGE_SANDBOX_BASE_URL}",
                "ADVISORAI_VENUE_API_KEY=synthetic-diagnostic-key",
                "ADVISORAI_VENUE_API_SECRET="
                + _base64.b64encode(b"synthetic-diagnostic-secret").decode("ascii"),
                "ADVISORAI_VENUE_PASSPHRASE=synthetic-diagnostic-passphrase",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_coinbase_diagnostic_flags_each_nonzero_foreign_leg() -> None:
    """Coinbase dual: each foreign currency reports available/hold legs."""
    accounts = _coinbase_accounts(
        ("USD", "1000", "1000", "0"),
        ("BTC", "0.010", "0.010", "0"),
        ("ETH", "1.5", "1.5", "0"),
        ("SOL", "2.0", "0", "2.0"),
    )
    assert classify_coinbase_accounts(accounts) == [
        {"asset": "ETH", "available_nonzero": True, "hold_nonzero": False},
        {"asset": "SOL", "available_nonzero": False, "hold_nonzero": True},
    ]
    assert (
        classify_coinbase_accounts(
            _coinbase_accounts(
                ("USD", "1000", "1000", "0"),
                ("BTC", "0.010", "0.010", "0"),
                ("ETH", "0", "0", "0"),
            )
        )
        == []
    )


def test_coinbase_diagnostic_fails_closed_on_malformed_values() -> None:
    """Malformed/non-finite legs flag; symbols normalize to upper."""
    flagged = classify_coinbase_accounts(
        _coinbase_accounts(
            ("eth", "junk", "junk", "0"),
            ("sol", "NaN", "NaN", "0"),
            ("xrp", "1.0", "0", "Infinity"),
        )
    )
    assert flagged == [
        {"asset": "ETH", "available_nonzero": True, "hold_nonzero": False},
        {"asset": "SOL", "available_nonzero": True, "hold_nonzero": False},
        {"asset": "XRP", "available_nonzero": False, "hold_nonzero": True},
    ]
    with pytest.raises(ValueError, match="sequence of account records"):
        classify_coinbase_accounts({"currency": "ETH"})


def test_coinbase_diagnostic_entry_point_reports_rejected_assets(
    tmp_path, monkeypatch, capsys
) -> None:
    """CLI `--provider coinbase_exchange_sandbox` exits 1 on foreign legs.

    End to end through the real Coinbase transport factory with mocked
    HTTP: one GET /accounts, tuple list_balances(), exit 1 naming the
    symbol and the nonzero leg only. No amounts, no secrets.
    """
    import diagnose_testnet_baseline as diagnostic_module

    accounts = _coinbase_accounts(
        ("USD", "1000", "1000", "0"),
        ("BTC", "0.010", "0.010", "0"),
        ("ETH", "1.5", "1.5", "0"),
    )
    calls: list = []
    monkeypatch.setattr(
        diagnostic_module,
        "build_coinbase_exchange_sandbox_transport",
        lambda resolver: build_coinbase_exchange_sandbox_transport(
            resolver, requester=_coinbase_requester(accounts, calls)
        ),
    )
    code = diagnostic_main(
        [
            "--secrets",
            str(_coinbase_secrets_file(tmp_path)),
            "--provider",
            "coinbase_exchange_sandbox",
        ]
    )
    out, err = capsys.readouterr()
    assert code == 1
    assert "REJECT" in out
    assert "asset=ETH" in out
    assert "nonzero=available" in out
    assert calls and all(method == "GET" for method, _ in calls)
    assert "synthetic-diagnostic-key" not in out + err
    assert "synthetic-diagnostic-secret" not in out + err
    assert "1.5" not in out + err
    assert "1000" not in out + err

def test_recovery_check_lists_fills_orders_and_legs_without_economics(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02 canary: --recovery-check distinguishes fill vs order vs balance.

    Runs ``main(['--recovery-check'])`` end to end through the real
    transport factory with mocked HTTP: one historical fill, one open
    order, BTC/USDT legs. Output names fill/order identities and leg
    flags only — no quantities, prices, fees, amounts, or secrets —
    and all calls are GET reads.
    """
    import json as json_module

    import diagnose_testnet_baseline as diagnostic_module

    balances = _balances(("BTC", "0.010", "0.000"), ("USDT", "9994.50", "0.000"))
    account = _account_payload(balances)
    fills = [
        {
            "symbol": "BTCUSDT",
            "id": 777001,
            "orderId": 424242,
            "price": "50000.00",
            "qty": "0.001",
            "commission": "0.05",
            "commissionAsset": "USDT",
            "time": 1700000000000,
            "isBuyer": True,
        }
    ]
    orders = [
        {
            "symbol": "BTCUSDT",
            "orderId": 424243,
            "clientOrderId": "probe-local",
            "price": "49000.00",
            "origQty": "0.001",
            "status": "NEW",
        }
    ]
    symbols = [_symbol("BTCUSDT", "BTC", "USDT"), _symbol("ETHUSDT", "ETH", "USDT")]
    calls: list = []

    def request(method, url, headers, body, timeout):
        calls.append((method, url))
        if "/api/v3/exchangeInfo" in url:
            return (200, json_module.dumps({"symbols": symbols}).encode(), ())
        if "/api/v3/account" in url:
            return (200, json_module.dumps(account).encode(), ())
        if "/api/v3/myTrades" in url:
            if "BTCUSDT" in url:
                return (200, json_module.dumps(fills).encode(), ())
            return (200, b"[]", ())
        if "/api/v3/openOrders" in url:
            if "symbol=" in url and "BTCUSDT" not in url:
                return (200, b"[]", ())
            return (200, json_module.dumps(orders).encode(), ())
        if "/api/v3/time" in url:
            return (200, json_module.dumps({"serverTime": 1700000070000}).encode(), ())
        raise AssertionError(f"unexpected mocked call {method} {url}")

    monkeypatch.setattr(
        diagnostic_module,
        "build_binance_spot_testnet_transport",
        lambda resolver: build_binance_spot_testnet_transport(
            resolver, requester=request
        ),
    )
    code = diagnostic_main(
        ["--secrets", str(_synthetic_secrets_file(tmp_path)), "--recovery-check"]
    )
    out, err = capsys.readouterr()
    assert code == 0
    assert "venue_fills=1" in out
    assert "venue_fill_id=" in out
    assert "occurred_at=" in out
    assert "venue_open_orders=1" in out
    assert "venue_order_id=" in out
    assert "balance BTC" in out
    assert "balance USDT" in out
    assert calls and all(method == "GET" for method, _ in calls)
    assert "synthetic-diagnostic-key" not in out + err
    assert "synthetic-diagnostic-secret" not in out + err
    for secret_value in ("50000.00", "49000.00", "0.001", "0.05", "9994.50", "0.010"):
        assert secret_value not in out + err
