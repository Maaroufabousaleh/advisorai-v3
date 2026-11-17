"""Offline P03 fill-probe qualification (mocked HTTP, synthetic credentials).

Runs the real ``scripts/probe_binance_spot_testnet_fill.py`` entry point end
to end through the real Binance Spot Testnet adapter with a mocked
requester: read-only gate, fresh book liquidity check, deterministic
RiskKernel target + order checks, exactly one SELL LIMIT FOK submission,
shared fill guards, exact reconciliation, and restart recovery without
resubmission.

The fill-path test additionally runs the REAL P03 validator against the
synthetic probe evidence to prove the probe emits validator-compatible
evidence. This is offline structural proof only — never P03 completion.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import scripts.preflight_binance_spot_testnet_commission as preflight_module
import scripts.probe_binance_spot_testnet_fill as probe_module
from scripts.validate_binance_fill_evidence import validate_run
from tests.integrations.test_binance_spot import _account, _symbol

CONFIG_HASH = "c" * 64
SERVER_MS = 1794787500000
VENUE_ORDER_ID = 4242

_BOOK = {
    "symbol": "BTCUSDT",
    "bidPrice": "5000.00",
    "bidQty": "1.0",
    "askPrice": "5000.10",
    "askQty": "1.0",
}


def _symbols() -> list[dict]:
    return [_symbol("BTCUSDT", "BTC", "USDT"), _symbol("ETHUSDT", "ETH", "USDT")]


def _synthetic_secrets_file(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic-probe.env"
    path.write_text(
        "\n".join(
            [
                "ADVISORAI_VENUE_NAME=binance_spot_testnet",
                "ADVISORAI_VENUE_ENVIRONMENT=paper_testnet",
                "ADVISORAI_VENUE_BASE_URL=https://testnet.binance.vision",
                "ADVISORAI_VENUE_API_KEY=synthetic-probe-key",
                "ADVISORAI_VENUE_API_SECRET=synthetic-probe-secret",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _read_only_evidence(tmp_path: Path, config_hash: str = CONFIG_HASH) -> Path:
    root = tmp_path / "read-only-smoke"
    run_id = "20261117T000000.000000Z"
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    operations = [
        {"name": name, "status": "ok", "endpoint": "/api/v3/time"}
        for name in (
            "server_time",
            "products",
            "product_mapping_verification",
            "account_state",
            "balances",
            "positions",
            "open_orders",
            "fills",
        )
    ]
    operations[1]["required_symbols"] = ["BTCUSDT", "ETHUSDT"]
    operations[2]["admitted_symbols"] = ["BTCUSDT", "ETHUSDT"]
    record = {
        "schema": "advisorai.phase2.binance-spot-testnet.read-only-smoke.v1",
        "run_id": run_id,
        "result": {
            "status": "passed",
            "writes_attempted": False,
            "config_hash": config_hash,
            "endpoint": "https://testnet.binance.vision",
            "credential_refs": [
                "ADVISORAI_VENUE_API_KEY",
                "ADVISORAI_VENUE_API_SECRET",
            ],
            "operations": operations,
        },
    }
    manifest = run_dir / "binance-spot-testnet-read-only-smoke.json"
    encoded = (json.dumps(record, sort_keys=True) + "\n").encode()
    manifest.write_bytes(encoded)
    (root / "latest.json").write_text(
        json.dumps({"run_id": run_id, "manifest_sha256": sha256(encoded).hexdigest()}),
        encoding="utf-8",
    )
    return root


class _ProbeVenue:
    """Stateful mocked venue: fills the FOK order once economics are known."""

    def __init__(
        self,
        calls: list,
        *,
        order_status: str = "FILLED",
        bid_qty: str = "1.0",
        fee: str = "0",
        fee_asset: str = "USDT",
        withhold_fills: bool = False,
        commission: str = "zero",
    ) -> None:
        self.calls = calls
        self.order_status = order_status
        self.bid_qty = bid_qty
        self.fee = fee
        self.fee_asset = fee_asset
        self.withhold_fills = withhold_fills
        self.commission = commission
        self.posts = 0

    def request(self, method, url, headers, body, timeout):
        self.calls.append((method, url))
        if "/api/v3/exchangeInfo" in url:
            return (200, json.dumps({"symbols": _symbols()}).encode(), ())
        if "/api/v3/time" in url:
            return (200, json.dumps({"serverTime": SERVER_MS}).encode(), ())
        if "/api/v3/ticker/bookTicker" in url:
            book = dict(_BOOK, bidQty=self.bid_qty)
            return (200, json.dumps(book).encode(), ())
        if "/api/v3/account/commission" in url:
            return (200, json.dumps(self._commission()).encode(), ())
        if "/api/v3/account" in url:
            return (200, json.dumps(self._account()).encode(), ())
        if "/api/v3/openOrders" in url:
            return (200, b"[]", ())
        if method == "POST" and "/api/v3/order" in url:
            self.posts += 1
            client_id = self._query(url, "newClientOrderId") or self._query(
                url, "origClientOrderId"
            )
            return (
                200,
                json.dumps(
                    {
                        "symbol": "BTCUSDT",
                        "orderId": VENUE_ORDER_ID,
                        "clientOrderId": client_id,
                        "status": "NEW",
                    }
                ).encode(),
                (),
            )
        if "/api/v3/order" in url:
            client_id = self._query(url, "origClientOrderId")
            return (
                200,
                json.dumps(
                    {
                        "symbol": "BTCUSDT",
                        "orderId": VENUE_ORDER_ID,
                        "clientOrderId": client_id,
                        "status": self.order_status,
                    }
                ).encode(),
                (),
            )
        if "/api/v3/myTrades" in url:
            if self.order_status != "FILLED" or self.posts == 0 or self.withhold_fills:
                return (200, b"[]", ())
            return (
                200,
                json.dumps(
                    [
                        {
                            "symbol": "BTCUSDT",
                            "id": 777001,
                            "orderId": VENUE_ORDER_ID,
                            "price": "5000.00",
                            "qty": "0.003",
                            "quoteQty": "15.00",
                            "commission": self.fee,
                            "commissionAsset": self.fee_asset,
                            "time": SERVER_MS,
                            "isBuyer": False,
                            "isMaker": False,
                            "isBestMatch": True,
                        }
                    ]
                ).encode(),
                (),
            )
        raise AssertionError(f"unexpected mocked call {method} {url}")

    def _account(self) -> dict:
        if self.posts == 0 or self.order_status != "FILLED":
            return _account()
        # Post-fill economics must match apply_fill exactly:
        # cash 1000 + 15.00 - fee, BTC 0.010 - 0.003.
        usdt = Decimal("1015") - Decimal(self.fee)
        return {
            "accountType": "SPOT",
            "canTrade": True,
            "balances": [
                {"asset": "USDT", "free": str(usdt), "locked": "0"},
                {"asset": "BTC", "free": "0.007", "locked": "0"},
                {"asset": "ETH", "free": "0.0", "locked": "0"},
            ],
        }

    def _commission(self) -> dict:
        """Documented /api/v3/account/commission shape per scenario."""
        zero = {
            "maker": "0.00000000",
            "taker": "0.00000000",
            "buyer": "0.00000000",
            "seller": "0.00000000",
        }
        standard = dict(zero)
        special = dict(zero)
        tax = dict(zero)
        discount = {
            "enabledForAccount": True,
            "enabledForSymbol": True,
            "discountAsset": "BNB",
            "discount": "0.75000000",
        }
        if self.commission == "nonzero_seller":
            standard["seller"] = "0.00040000"
        elif self.commission == "nonzero_special":
            special["taker"] = "0.02000000"
        elif self.commission == "nonzero_tax":
            tax["taker"] = "0.00000114"
        elif self.commission == "positive_usdt_bnb_off":
            standard["taker"] = "0.00100000"
            discount["enabledForAccount"] = False
        elif self.commission == "malformed":
            return {"symbol": "BTCUSDT", "standardCommission": standard}
        elif self.commission != "zero":
            raise AssertionError(f"unknown commission scenario {self.commission}")
        return {
            "symbol": "BTCUSDT",
            "standardCommission": standard,
            "specialCommission": special,
            "taxCommission": tax,
            "discount": discount,
        }

    @staticmethod
    def _query(url: str, name: str) -> str:
        match = re.search(r"[?&]" + re.escape(name) + r"=([^&]*)", url)
        return match.group(1) if match else ""


def _run_probe(
    monkeypatch, tmp_path, venue: _ProbeVenue, argv_extra: list[str] | None = None
) -> int:
    from advisorai.integrations import build_binance_spot_testnet_transport as real_builder

    def seeded_builder(resolver, **kwargs):
        return real_builder(resolver, requester=venue.request, **kwargs)

    monkeypatch.setattr(probe_module, "build_binance_spot_testnet_transport", seeded_builder)
    monkeypatch.setenv("ADVISORAI_RUN_NETWORK_SMOKE", "1")
    monkeypatch.setenv("ADVISORAI_RUN_FILL_PROBE", "1")
    argv = [
        "--secrets",
        str(_synthetic_secrets_file(tmp_path)),
        "--read-only-evidence",
        str(_read_only_evidence(tmp_path)),
        "--evidence-dir",
        str(tmp_path / "fill-probe"),
        "--configuration-hash",
        CONFIG_HASH,
    ]
    return probe_module.main(argv + (argv_extra or []))


def _only_run_dir(tmp_path: Path) -> Path:
    root = tmp_path / "fill-probe"
    run_dirs = [child for child in root.iterdir() if child.is_dir()]
    assert len(run_dirs) == 1
    return run_dirs[0]


def test_fok_probe_fills_reconciles_and_passes_p03_validator_offline(tmp_path, monkeypatch, capsys):
    """P03 prep: synthetic SELL FOK fills, reconciles, restarts, validates.

    Offline structural proof with mocked HTTP: all-zero symbol legs, exactly one
    POST, zero USDT fee, exact reconciliation, zero resubmissions, and the
    REAL P03 validator certifies the synthetic evidence format. Not venue
    evidence; P03 still needs the owner run.
    """
    calls: list = []
    venue = _ProbeVenue(calls)
    assert _run_probe(monkeypatch, tmp_path, venue) == 0
    posts = [url for method, url in calls if method == "POST"]
    assert len(posts) == 1 and "/api/v3/order" in posts[0]
    assert "timeInForce=FOK" in posts[0] or "timeInForce%3DFOK" in posts[0]
    report = validate_run(_only_run_dir(tmp_path))
    assert report.fill_pass
    assert report.p03_certified, [check.name for check in report.failed]
    out = capsys.readouterr().out
    assert '"status":"passed"' in out
    assert "synthetic-probe-key" not in out


def test_fok_probe_expires_honestly_without_fill(tmp_path, monkeypatch, capsys):
    """P03 prep: an EXPIRED FOK is an honest no-fill pass, never a retry."""
    calls: list = []
    venue = _ProbeVenue(calls, order_status="EXPIRED")
    assert _run_probe(monkeypatch, tmp_path, venue) == 0
    posts = [url for method, url in calls if method == "POST"]
    assert len(posts) == 1
    report = validate_run(_only_run_dir(tmp_path))
    assert not report.fill_pass
    assert not report.p03_certified
    out = capsys.readouterr().out
    assert '"status":"passed"' in out


def test_fok_probe_refuses_thin_book_without_submission(tmp_path, monkeypatch, capsys):
    """P03 prep: top-of-book depth below size refuses before any write."""
    calls: list = []
    venue = _ProbeVenue(calls, bid_qty="0.0001")
    assert _run_probe(monkeypatch, tmp_path, venue) == 1
    assert all("/api/v3/order" not in url or method == "GET" for method, url in calls)
    assert not [url for method, url in calls if method == "POST"]
    out = capsys.readouterr().out
    assert '"status":"failed"' in out


def test_fok_probe_fails_closed_when_fills_are_missing(tmp_path, monkeypatch, capsys):
    """P03 prep: FILLED status with empty trade history fails closed, no retry."""
    calls: list = []
    venue = _ProbeVenue(calls, withhold_fills=True)
    assert _run_probe(monkeypatch, tmp_path, venue) == 1
    posts = [url for method, url in calls if method == "POST"]
    assert len(posts) == 1
    out = capsys.readouterr().out
    assert '"status":"failed"' in out


def test_fok_probe_refuses_nonzero_seller_leg_without_submission(tmp_path, monkeypatch, capsys):
    """P03 prep: zero standard taker but nonzero seller refuses (BNB may apply)."""
    calls: list = []
    venue = _ProbeVenue(calls, commission="nonzero_seller")
    assert _run_probe(monkeypatch, tmp_path, venue) == 1
    assert not [url for method, url in calls if method == "POST"]
    out = capsys.readouterr().out
    assert '"status":"failed"' in out


def test_fok_probe_refuses_nonzero_special_leg_without_submission(tmp_path, monkeypatch, capsys):
    """P03 prep: zero standard taker but nonzero special refuses (BNB may apply)."""
    calls: list = []
    venue = _ProbeVenue(calls, commission="nonzero_special")
    assert _run_probe(monkeypatch, tmp_path, venue) == 1
    assert not [url for method, url in calls if method == "POST"]
    out = capsys.readouterr().out
    assert '"status":"failed"' in out


def test_fok_probe_refuses_nonzero_tax_leg_without_submission(tmp_path, monkeypatch, capsys):
    """P03 prep: zero standard taker but nonzero tax refuses (BNB may apply)."""
    calls: list = []
    venue = _ProbeVenue(calls, commission="nonzero_tax")
    assert _run_probe(monkeypatch, tmp_path, venue) == 1
    assert not [url for method, url in calls if method == "POST"]
    out = capsys.readouterr().out
    assert '"status":"failed"' in out


def test_fok_probe_refuses_malformed_commission_without_submission(tmp_path, monkeypatch, capsys):
    """P03 prep: commission record missing a block refuses pre-write."""
    calls: list = []
    venue = _ProbeVenue(calls, commission="malformed")
    assert _run_probe(monkeypatch, tmp_path, venue) == 1
    assert not [url for method, url in calls if method == "POST"]
    out = capsys.readouterr().out
    assert '"status":"failed"' in out


def test_fok_probe_admits_positive_usdt_fee_with_bnb_disabled(tmp_path, monkeypatch, capsys):
    """P03 prep: positive standard taker + BNB ruled out fills in USDT exactly.

    Offline proof that path B (BNB disabled, USDT-supported fee) runs the
    full lifecycle: exactly one POST, the 0.015 USDT fee passes the exact
    post-fill fee check, reconciliation is exact, and the REAL P03
    validator certifies the synthetic evidence format.
    """
    calls: list = []
    venue = _ProbeVenue(calls, commission="positive_usdt_bnb_off", fee="0.015")
    assert _run_probe(monkeypatch, tmp_path, venue) == 0
    posts = [url for method, url in calls if method == "POST"]
    assert len(posts) == 1 and "/api/v3/order" in posts[0]
    report = validate_run(_only_run_dir(tmp_path))
    assert report.fill_pass
    assert report.p03_certified, [check.name for check in report.failed]
    out = capsys.readouterr().out
    assert '"status":"passed"' in out


def _run_preflight(monkeypatch, tmp_path, venue: _ProbeVenue) -> int:
    from advisorai.integrations import build_binance_spot_testnet_transport as real_builder

    def seeded_builder(resolver, **kwargs):
        return real_builder(resolver, requester=venue.request, **kwargs)

    monkeypatch.setattr(preflight_module, "build_binance_spot_testnet_transport", seeded_builder)
    monkeypatch.setenv("ADVISORAI_RUN_NETWORK_SMOKE", "1")
    return preflight_module.main(["--secrets", str(_synthetic_secrets_file(tmp_path))])


def test_commission_preflight_passes_on_zero_symbol_fees(tmp_path, monkeypatch, capsys):
    """P03 prep: zero symbol legs pass even with BNB discount on, leaking nothing."""
    calls: list = []
    venue = _ProbeVenue(calls)
    assert _run_preflight(monkeypatch, tmp_path, venue) == 0
    assert all(method == "GET" for method, _ in calls)
    out = capsys.readouterr().out
    assert '"status":"passed"' in out
    assert '"bnb_payment_can_apply":false' in out
    assert "balances" not in out and "1015" not in out and "1000" not in out
    assert "synthetic-probe-key" not in out


def test_commission_preflight_refuses_bnb_may_apply(tmp_path, monkeypatch, capsys):
    """P03 prep: standalone preflight reports BNB may apply, read-only."""
    calls: list = []
    venue = _ProbeVenue(calls, commission="nonzero_seller")
    assert _run_preflight(monkeypatch, tmp_path, venue) == 1
    assert all(method == "GET" for method, _ in calls)
    out = capsys.readouterr().out
    assert '"status":"failed"' in out
    assert '"bnb_payment_can_apply":true' in out


def test_commission_preflight_passes_positive_usdt_with_bnb_disabled(tmp_path, monkeypatch, capsys):
    """P03 prep: positive taker + account-level BNB off passes, read-only."""
    calls: list = []
    venue = _ProbeVenue(calls, commission="positive_usdt_bnb_off")
    assert _run_preflight(monkeypatch, tmp_path, venue) == 0
    assert all(method == "GET" for method, _ in calls)
    out = capsys.readouterr().out
    assert '"status":"passed"' in out
    assert '"bnb_payment_can_apply":false' in out
    assert '"reason":"positive_usdt_fee_bnb_ruled_out"' in out
