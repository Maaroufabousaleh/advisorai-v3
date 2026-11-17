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
        taker: str | None = "0.0000",
    ) -> None:
        self.calls = calls
        self.order_status = order_status
        self.bid_qty = bid_qty
        self.fee = fee
        self.fee_asset = fee_asset
        self.withhold_fills = withhold_fills
        self.taker = taker
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
            payload = dict(_account())
        else:
            # Post-fill economics must match apply_fill exactly:
            # cash 1000 + 15.00 - 0.000, BTC 0.010 - 0.003.
            payload = {
                "accountType": "SPOT",
                "canTrade": True,
                "balances": [
                    {"asset": "USDT", "free": "1015.00", "locked": "0"},
                    {"asset": "BTC", "free": "0.007", "locked": "0"},
                    {"asset": "ETH", "free": "0.0", "locked": "0"},
                ],
            }
        if self.taker is not None:
            payload["commissionRates"] = {
                "maker": "0.0000",
                "taker": self.taker,
                "buyer": "0.0000",
                "seller": "0.0000",
            }
        return payload

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

    Offline structural proof with mocked HTTP: zero taker rate, exactly one
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


def test_fok_probe_refuses_positive_taker_rate_without_submission(tmp_path, monkeypatch, capsys):
    """P03 prep: positive taker rate refuses pre-write (BNB may apply)."""
    calls: list = []
    venue = _ProbeVenue(calls, taker="0.0010")
    assert _run_probe(monkeypatch, tmp_path, venue) == 1
    assert not [url for method, url in calls if method == "POST"]
    out = capsys.readouterr().out
    assert '"status":"failed"' in out


def test_fok_probe_refuses_missing_commission_rates_without_submission(
    tmp_path, monkeypatch, capsys
):
    """P03 prep: unestablishable commission refuses pre-write."""
    calls: list = []
    venue = _ProbeVenue(calls, taker=None)
    assert _run_probe(monkeypatch, tmp_path, venue) == 1
    assert not [url for method, url in calls if method == "POST"]
    out = capsys.readouterr().out
    assert '"status":"failed"' in out


def _run_preflight(monkeypatch, tmp_path, venue: _ProbeVenue) -> int:
    from advisorai.integrations import build_binance_spot_testnet_transport as real_builder

    def seeded_builder(resolver, **kwargs):
        return real_builder(resolver, requester=venue.request, **kwargs)

    monkeypatch.setattr(preflight_module, "build_binance_spot_testnet_transport", seeded_builder)
    monkeypatch.setenv("ADVISORAI_RUN_NETWORK_SMOKE", "1")
    return preflight_module.main(["--secrets", str(_synthetic_secrets_file(tmp_path))])


def test_commission_preflight_passes_on_zero_taker_rate(tmp_path, monkeypatch, capsys):
    """P03 prep: standalone preflight passes with zero fee, leaking nothing."""
    calls: list = []
    venue = _ProbeVenue(calls)
    assert _run_preflight(monkeypatch, tmp_path, venue) == 0
    assert all(method == "GET" for method, _ in calls)
    out = capsys.readouterr().out
    assert '"status":"passed"' in out
    assert '"bnb_payment_can_apply":false' in out
    assert "balances" not in out and "1015" not in out and "1000" not in out
    assert "synthetic-probe-key" not in out


def test_commission_preflight_refuses_positive_taker_rate(tmp_path, monkeypatch, capsys):
    """P03 prep: standalone preflight reports BNB may apply, read-only."""
    calls: list = []
    venue = _ProbeVenue(calls, taker="0.0010")
    assert _run_preflight(monkeypatch, tmp_path, venue) == 1
    assert all(method == "GET" for method, _ in calls)
    out = capsys.readouterr().out
    assert '"status":"failed"' in out
    assert '"bnb_payment_can_apply":true' in out
