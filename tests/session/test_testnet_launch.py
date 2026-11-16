"""Offline contracts for the supervised testnet launch path.

All credentials are synthetic fixtures for injected transports. No
network call and no real ``secrets.env`` value is used here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

import pytest

from advisorai.config import CredentialResolver
from advisorai.integrations import (
    BINANCE_SPOT_TESTNET_BASE_URL,
    BINANCE_SPOT_TESTNET_HOST,
)
from advisorai.integrations.http import HttpClientConfig, SafeHttpClient
from advisorai.ledger import SqliteLedgers
from advisorai.session.application import OwnerApplicationConfig
from advisorai.session.testnet_launch import (
    SandboxFixtureDataAdapter,
    SandboxLaunchRequest,
    build_testnet_owner_application,
)


class SyntheticBinanceTransport:
    """Minimal synthetic double behind the reviewed Binance testnet boundary."""

    venue_name = "binance_spot_testnet"
    environment = "paper_testnet"

    def __init__(self) -> None:
        self.client = SafeHttpClient(
            HttpClientConfig(allowed_hosts=(BINANCE_SPOT_TESTNET_HOST,)),
            base_url=BINANCE_SPOT_TESTNET_BASE_URL,
            requester=lambda *_args: (200, b"{}", ()),
            sleeper=lambda _seconds: None,
        )
        self.verified_symbol_ids = ("BTCUSDT",)
        self.orders: dict[str, dict[str, object]] = {}

    def list_products(self):  # noqa: ANN001, ANN202
        return ({"symbol": "BTCUSDT"},)

    def verify_symbol_mappings(self, _products, *, required):  # noqa: ANN001
        assert tuple(required) == ("BTCUSDT",)

    def verified_symbol_spec(self, symbol: str):  # noqa: ANN001, ANN202
        from types import SimpleNamespace

        assert symbol == "BTCUSDT"
        return SimpleNamespace(base_asset="BTC", quote_asset="USDT")

    def canonical_instrument_id(self, symbol: str) -> str:
        return f"crypto:{symbol}:binance_spot_testnet:spot"

    def submit_order(self, payload):  # noqa: ANN001, ANN202
        client_id = str(payload["client_order_id"])
        self.orders[client_id] = {"id": f"provider-{client_id}"}
        return {"venue_order_id": f"provider-{client_id}", "accepted": True}

    def query_order(self, *, client_order_id: str):  # noqa: ANN202
        return self.orders.get(client_order_id)

    def list_open_orders(self):  # noqa: ANN202
        return ()

    def cancel_order(self, *, client_order_id: str):  # noqa: ANN202
        self.orders.pop(client_order_id, None)
        return {"cancelled": True}

    def fetch_account_snapshot(self):  # noqa: ANN202
        from advisorai.execution.reconciliation import VenueAccountSnapshot

        return VenueAccountSnapshot(
            as_of=datetime.now(UTC), cash=Decimal("100000"), positions={}
        )

    def list_fills(self):  # noqa: ANN202
        return ()

    def fill_contract_values(self, _record):  # noqa: ANN001, ANN202
        raise AssertionError("synthetic transport holds no fills")


def _resolver() -> CredentialResolver:
    return CredentialResolver.from_mapping(
        {
            "ADVISORAI_VENUE_API_KEY": "synthetic-testnet-key",
            "ADVISORAI_VENUE_API_SECRET": "synthetic-testnet-secret",
            "ADVISORAI_VENUE_NAME": "binance_spot_testnet",
            "ADVISORAI_VENUE_ENVIRONMENT": "testnet",
            "ADVISORAI_VENUE_BASE_URL": BINANCE_SPOT_TESTNET_BASE_URL,
        }
    )


def test_testnet_launch_composes_reviewed_profile_without_real_secrets(tmp_path) -> None:
    ledgers = SqliteLedgers(tmp_path / "testnet-owner.sqlite3")
    app = build_testnet_owner_application(
        ledgers=ledgers,
        state_dir=tmp_path / "state",
        lock_identity="testnet-launch-test",
        code_hash="c" * 64,
        config_hash="d" * 64,
        config=OwnerApplicationConfig(scenario="quiet", interval_seconds=3600),
        resolver=_resolver(),
        launch=SandboxLaunchRequest(
            provider="binance_spot_testnet",
            provider_instrument_id="BTCUSDT",
            quote_currency="USDT",
        ),
        transport=SyntheticBinanceTransport(),
    )
    assert app.venue_profile == "binance_spot_testnet"
    assert app.adapters.instrument.venue_symbol == "BTCUSDT"
    assert app.adapters.instrument.quote_asset == "USDT"
    assert app.adapters.paper_venue.venue == "paper"
    assert "synthetic-testnet-key" not in repr(app.adapters.paper_venue)


def test_testnet_launch_refuses_wrong_instrument_or_live_identity(tmp_path) -> None:
    ledgers = SqliteLedgers(tmp_path / "testnet-refuse.sqlite3")
    kwargs = {
        "ledgers": ledgers,
        "state_dir": tmp_path / "state",
        "lock_identity": "testnet-launch-refuse",
        "code_hash": "c" * 64,
        "config_hash": "d" * 64,
        "config": OwnerApplicationConfig(scenario="quiet"),
        "resolver": _resolver(),
        "transport": SyntheticBinanceTransport(),
    }
    with pytest.raises(ValueError, match="exactly BTCUSDT"):
        build_testnet_owner_application(
            **kwargs,  # type: ignore[arg-type]
            launch=SandboxLaunchRequest(
                provider="binance_spot_testnet",
                provider_instrument_id="ETHUSDT",
                quote_currency="USDT",
            ),
        )
    live = CredentialResolver.from_mapping(
        {
            "ADVISORAI_VENUE_API_KEY": "synthetic-testnet-key",
            "ADVISORAI_VENUE_API_SECRET": "synthetic-testnet-secret",
            "ADVISORAI_VENUE_NAME": "binance_spot_testnet",
            "ADVISORAI_VENUE_ENVIRONMENT": "live",
            "ADVISORAI_VENUE_BASE_URL": "https://api.binance.com",
        }
    )
    with pytest.raises(ValueError, match="paper/testnet"):
        build_testnet_owner_application(
            ledgers=ledgers,
            state_dir=tmp_path / "state",
            lock_identity="testnet-launch-refuse",
            code_hash="c" * 64,
            config_hash="d" * 64,
            config=OwnerApplicationConfig(scenario="quiet"),
            transport=SyntheticBinanceTransport(),
            launch=SandboxLaunchRequest(
                provider="binance_spot_testnet",
                provider_instrument_id="BTCUSDT",
                quote_currency="USDT",
            ),
            resolver=live,
        )


def test_testnet_fixture_adapter_marks_explicit_fixture_identity(tmp_path) -> None:  # noqa: ARG001
    adapter = SandboxFixtureDataAdapter("USDT")
    assert adapter.watermark_key == "testnet-fixture/BTCUSDT-1h"
    assert adapter.quote_currency == "USDT"
    cutoff = datetime(2026, 1, 2, tzinfo=UTC)
    snapshot = adapter.snapshot_at(cutoff)
    assert snapshot.artifact_id == uuid5(
        NAMESPACE_URL, f"advisorai-v3/testnet-fixture-snapshot/{cutoff.isoformat()}"
    )
    market = adapter.market_at(snapshot)
    assert market.marks["BTC"] > 0


def test_testnet_fixture_verification_fails_closed(tmp_path) -> None:
    """Fixture PIT/gap verifiers accept exact coverage, reject anything else."""
    from uuid import uuid4

    from advisorai.session.records import (
        DataGapRecord,
        DataGapResolutionRecord,
        PITSnapshotVerification,
    )

    ledgers = SqliteLedgers(tmp_path / "testnet-verify.sqlite3")
    adapter = SandboxFixtureDataAdapter("USDT")
    cutoff = datetime(2026, 1, 2, tzinfo=UTC)
    snapshot = adapter.snapshot_at(cutoff)
    watermark = SandboxFixtureDataAdapter.watermark_value(snapshot)

    pit = PITSnapshotVerification(
        session_id=uuid4(),
        snapshot_id=snapshot.artifact_id,
        snapshot_hash="a" * 64,
        source="testnet-fixture",
        cutoff=cutoff,
        first_available_at=cutoff,
        watermarks={adapter.watermark_key: watermark},
    )
    assert adapter.verify_current_pit(ledgers, pit) is True
    wrong_source = pit.model_copy(update={"source": "binance_spot_public_market_data"})
    assert adapter.verify_current_pit(ledgers, wrong_source) is False
    wrong_mark = pit.model_copy(
        update={"watermarks": {adapter.watermark_key: "2099-01-01T00:00:00+00:00"}}
    )
    assert adapter.verify_current_pit(ledgers, wrong_mark) is False

    gap = DataGapRecord(
        source="testnet-fixture",
        instrument="BTC",
        gap_start=cutoff - timedelta(hours=2),
        gap_end=cutoff,
        last_valid_watermark=(cutoff - timedelta(hours=3)).isoformat(),
    )
    resolution = DataGapResolutionRecord(
        gap_id=gap.artifact_id,
        source="testnet-fixture",
        instrument="BTC",
        covered_through=cutoff,
        watermark=watermark,
        evidence_id=uuid4(),
    )
    assert adapter.verify_gap_coverage(ledgers, gap, resolution) is True
    short = resolution.model_copy(update={"covered_through": cutoff - timedelta(hours=1)})
    assert adapter.verify_gap_coverage(ledgers, gap, short) is False
    foreign = resolution.model_copy(update={"source": "binance_spot_public_market_data"})
    assert adapter.verify_gap_coverage(ledgers, gap, foreign) is False


def test_launcher_rejects_out_of_bounds_before_any_venue_touch(capsys) -> None:
    """P02: invalid bounds exit 2 before credentials, baseline, or network."""
    import sys

    sys.path.insert(0, "scripts")
    from launch_testnet_paper import main

    for argv in (
        ["--max-runtime-seconds", "0"],
        ["--max-runtime-seconds", "86401"],
        ["--max-submissions", "-1"],
        ["--max-submissions", "101"],
        ["--interval-seconds", "0.05"],
        ["--interval-seconds", "86401"],
        ["--max-rss-mb", "255"],
        ["--max-rss-mb", "8193"],
        ["--instrument", "ETHUSDT"],
        ["--quote", "USDC"],
    ):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "testnet_baseline" not in captured.out


def test_testnet_fixture_marks_are_deterministic_and_timezone_safe() -> None:
    """Fixture bars refuse naive cutoffs; marks are deterministic in time."""
    from datetime import datetime as dt

    adapter = SandboxFixtureDataAdapter("USDT")
    with pytest.raises(ValueError, match="must include a timezone"):
        adapter.snapshot_at(dt(2026, 1, 2, 12, 0, 0))

    early = adapter.snapshot_at(dt(2026, 1, 2, 12, 0, 0, tzinfo=UTC))
    late = adapter.snapshot_at(dt(2026, 1, 2, 15, 0, 0, tzinfo=UTC))
    assert adapter.market_at(early).marks["BTC"] == adapter.market_at(early).marks["BTC"]
    assert adapter.market_at(late).marks["BTC"] > adapter.market_at(early).marks["BTC"]


def test_observation_from_testnet_bar_pins_pit_and_hash() -> None:
    """P05 prep: PIT helper refuses naive times, hashes content deterministically."""
    from datetime import datetime as dt
    from datetime import timezone

    from advisorai.session.testnet_launch import observation_from_testnet_bar

    with pytest.raises(ValueError, match="must include a timezone"):
        observation_from_testnet_bar(
            provider="binance_spot_testnet",
            series_id="BTCUSDT-1h",
            period_start=dt(2026, 1, 2, 12, 0, 0),
            value="50000.00",
            retrieved_at=dt(2026, 1, 2, 13, 0, 0, tzinfo=UTC),
        )

    plus5 = timezone(timedelta(hours=5))
    first = observation_from_testnet_bar(
        provider="binance_spot_testnet",
        series_id="BTCUSDT-1h",
        period_start=dt(2026, 1, 2, 12, 0, 0, tzinfo=plus5),
        value="50000.00",
        retrieved_at=dt(2026, 1, 2, 13, 0, 0, tzinfo=UTC),
    )
    second = observation_from_testnet_bar(
        provider="binance_spot_testnet",
        series_id="BTCUSDT-1h",
        period_start=dt(2026, 1, 2, 7, 0, 0, tzinfo=UTC),
        value="50000.00",
        retrieved_at=dt(2026, 1, 2, 13, 0, 0, tzinfo=UTC),
    )
    assert first.event_time == second.event_time
    assert first.event_time.utcoffset() == timedelta(0)
    assert first.first_available_at <= first.ingested_at
    assert first.raw_artifact_hash == second.raw_artifact_hash
    assert len(first.raw_artifact_hash) == 64


def test_snapshot_bound_fx_labels_peg_and_refuses_other_quotes() -> None:
    """FX: USD identity, labelled USDT 1.0 peg, anything else refused."""
    from advisorai.contracts import Snapshot
    from advisorai.session.testnet_launch import _snapshot_bound_fx

    snapshot = Snapshot(as_of=datetime(2026, 1, 2, tzinfo=UTC), purpose="fx-fixture")

    usd = _snapshot_bound_fx("USD")(snapshot)
    assert usd.usd_per_quote == Decimal("1")
    assert usd.source == "currency-identity/USD"
    assert usd.snapshot_id == snapshot.artifact_id

    peg = _snapshot_bound_fx("USDT")(snapshot)
    assert peg.usd_per_quote == Decimal("1")
    assert peg.source == "testnet-fixture/USDT-peg-1.0"

    with pytest.raises(ValueError, match="no FX provider"):
        _snapshot_bound_fx("EUR")(snapshot)


def test_launch_request_validation_normalizes_and_refuses() -> None:
    """P02: request validation normalizes case/space, refuses bad profiles."""
    from advisorai.session.testnet_launch import (
        SandboxLaunchRequest,
        _validate_request,
    )

    normalized = _validate_request(
        SandboxLaunchRequest(
            provider="  BINANCE_SPOT_TESTNET ",
            provider_instrument_id="btcusdt",
            quote_currency=" usdt ",
            max_submissions=0,
        )
    )
    assert normalized.provider == "binance_spot_testnet"
    assert normalized.provider_instrument_id == "BTCUSDT"
    assert normalized.quote_currency == "USDT"

    bad = [
        ("unknown venue", {"provider": "kraken_demo"}),
        ("wrong instrument", {"provider_instrument_id": "ETHUSDT"}),
        ("wrong binance quote", {"quote_currency": "USD"}),
        ("negative cap", {"max_submissions": -1}),
        ("non-positive mark", {"initial_mark": Decimal("0")}),
    ]
    for _label, overrides in bad:
        kwargs = {
            "provider": "binance_spot_testnet",
            "provider_instrument_id": "BTCUSDT",
            "quote_currency": "USDT",
        }
        kwargs.update(overrides)
        with pytest.raises(ValueError):
            _validate_request(SandboxLaunchRequest(**kwargs))

    with pytest.raises(ValueError, match="USD quote"):
        _validate_request(
            SandboxLaunchRequest(
                provider="coinbase_exchange_sandbox",
                provider_instrument_id="BTC-USD",
                quote_currency="USDT",
            )
        )


def test_fixture_council_roles_emit_deterministic_labelled_evidence() -> None:
    """Council: 4 roles, distinct origins, deterministic ids, 1h expiry."""
    from advisorai.contracts import Snapshot
    from advisorai.session.testnet_launch import _fixture_council

    council = _fixture_council()
    assert sorted(council.role_functions) == [
        "data_verifier",
        "news_event",
        "skeptic_base_rate",
        "technical_flow",
    ]
    snapshot = Snapshot(as_of=datetime(2026, 1, 2, tzinfo=UTC), purpose="council-fixture")
    origins = set()
    for role, run in council.role_functions.items():
        first = run(snapshot)
        second = run(snapshot)
        assert first.role == role
        assert len(first.evidence) == 1
        evidence = first.evidence[0]
        assert evidence.artifact_id == second.evidence[0].artifact_id
        assert evidence.observed_at == snapshot.as_of
        assert evidence.expires_at == snapshot.as_of + timedelta(hours=1)
        origins.add(evidence.origin)
    assert origins == {
        "testnet-fixture-a",
        "testnet-fixture-b",
        "testnet-fixture-c",
        "testnet-fixture-d",
    }


def test_launch_request_validation_refuses_out_of_profile_inputs() -> None:
    """P02 prep: launch-request construction refuses misshapen inputs.

    The supervised launch path admits exactly one venue profile per
    provider plus a positive fake-funds baseline and a non-negative
    submission cap. Unknown venue profiles, wrong quotes, zero
    baselines, and negative caps are refused before any transport is
    built. Pure unit coverage; no network, no credentials.
    """
    from advisorai.session.testnet_launch import (
        TestnetFixtureDataAdapter,
        TestnetLaunchRequest,
        _validate_request,
        observation_from_testnet_bar,
    )

    def request(**overrides):
        kwargs = {
            "provider": "binance_spot_testnet",
            "provider_instrument_id": "BTCUSDT",
            "quote_currency": "USDT",
        }
        kwargs.update(overrides)
        return TestnetLaunchRequest(**kwargs)

    with pytest.raises(ValueError, match="unknown testnet venue profile"):
        _validate_request(request(provider="live-exchange"))
    with pytest.raises(ValueError, match="requires the USDT quote"):
        _validate_request(request(quote_currency="USD"))
    with pytest.raises(ValueError, match="requires the USD quote"):
        _validate_request(
            request(
                provider="coinbase_exchange_sandbox",
                provider_instrument_id="BTC-USD",
                quote_currency="USDT",
            )
        )
    with pytest.raises(ValueError, match="positive fake-funds baseline"):
        _validate_request(request(initial_cash=Decimal("0")))
    with pytest.raises(ValueError, match="positive fake-funds baseline"):
        _validate_request(request(initial_mark=Decimal("-1")))
    with pytest.raises(ValueError, match="cannot be negative"):
        _validate_request(request(max_submissions=-1))
    with pytest.raises(ValueError, match="requires a quote currency"):
        TestnetFixtureDataAdapter("  ")
    with pytest.raises(ValueError, match="observation times must include a timezone"):
        observation_from_testnet_bar(
            provider="binance_spot_testnet",
            series_id="BTCUSDT",
            period_start=datetime(2026, 11, 15, 12, 0),
            value="50000",
            retrieved_at=datetime.now(UTC),
        )


def test_launcher_prints_baseline_only_with_explicit_flag(
    tmp_path, monkeypatch, capsys
) -> None:
    """P02 disclosure: baseline cash stays out of stdout unless flagged.

    Offline: stub the resolver + baseline read, then run launcher
    ``main`` to the lock-acquisition failure (no session starts).
    Default argv prints no baseline; ``--print-baseline`` prints it.
    """
    import sys
    from types import SimpleNamespace

    sys.path.insert(0, "scripts")
    import launch_testnet_paper
    from launch_testnet_paper import main

    from advisorai.execution.account import AccountState

    baseline = AccountState(
        cash=Decimal("1234.56"),
        marks={"BTC": Decimal("50000")},
        as_of=datetime(2026, 11, 16, tzinfo=UTC),
    )
    monkeypatch.setattr(
        launch_testnet_paper.CredentialResolver,
        "from_env_file",
        classmethod(lambda cls, path: SimpleNamespace()),
    )
    monkeypatch.setattr(
        launch_testnet_paper,
        "read_testnet_baseline",
        lambda resolver, launch: (SimpleNamespace(), baseline),
    )
    lock_calls: list = []

    class _RefusingSupervisor:
        def __init__(self, *args, **kwargs) -> None:
            lock_calls.append(True)

        def __getattr__(self, name):  # pragma: no cover - fail fast
            raise RuntimeError("owner lock is held elsewhere")

    monkeypatch.setattr(launch_testnet_paper, "SessionSupervisor", _RefusingSupervisor)
    monkeypatch.setenv("ADVISORAI_STATE_DIR", str(tmp_path / "state"))
    assert main([]) == 2
    assert "testnet_baseline" not in capsys.readouterr().out
    assert main(["--print-baseline"]) == 2
    out = capsys.readouterr().out
    assert "testnet_baseline cash=1234.56" in out
    assert lock_calls
