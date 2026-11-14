"""Supervised testnet venue launch path for the paper owner application.

The owner CLI (``advisorai.app start``) only builds the deterministic
local-simulator profile. This module composes the already-reviewed provider
profiles (``session/provider_profiles.py``) into a ``PaperOwnerApplication``
under the same single-owner lock, resource envelope, RiskKernel limits, OMS,
and current-state recovery checks.

It performs no network calls at import or construction time, loads no
credentials itself, and grants no admission. The caller supplies a
``CredentialResolver`` (owner runtime: parsed local inventory; tests:
synthetic fixture mapping). Venue transport construction resolves exactly
one ``PAPER_VENUE`` scope; provider data keys (BLS/BEA/EIA) are never
consulted here and are not exchange credentials.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from advisorai.agents import EvidenceCouncil
from advisorai.config import CredentialResolver
from advisorai.contracts import (
    AssetClass,
    InstrumentIdentity,
    PointInTimeObservation,
    Snapshot,
    SourceGrade,
)
from advisorai.execution import AccountState
from advisorai.ledger import SqliteLedgers
from advisorai.session.application import OwnerApplicationConfig, PaperOwnerApplication
from advisorai.session.provider_profiles import (
    ProviderProfileComponents,
    ProviderProfileContext,
    ProviderProfileRequest,
    build_provider_profile,
    verify_selected_instrument,
)

_TESTNET_FIXTURE_SOURCE = "testnet-fixture"
_TESTNET_FIXTURE_DATASET = "BTCUSDT-1h"


@dataclass(frozen=True, slots=True)
class TestnetLaunchRequest:
    """One reviewed sandbox instrument launch; testnet only, fake funds only."""

    provider: str
    provider_instrument_id: str
    quote_currency: str
    initial_cash: Decimal = Decimal("100000")
    initial_mark: Decimal = Decimal("50000")
    max_submissions: int | None = None


def _validate_request(request: TestnetLaunchRequest) -> TestnetLaunchRequest:
    provider = request.provider.strip().lower()
    instrument = request.provider_instrument_id.strip().upper()
    quote = request.quote_currency.strip().upper()
    if provider not in {"binance_spot_testnet", "coinbase_exchange_sandbox"}:
        raise ValueError(f"unknown testnet venue profile {request.provider!r}")
    expected = "BTCUSDT" if provider == "binance_spot_testnet" else "BTC-USD"
    if instrument != expected:
        raise ValueError(f"{provider} launch admits exactly {expected} in this path")
    if provider == "binance_spot_testnet" and quote != "USDT":
        raise ValueError("binance_spot_testnet launch requires the USDT quote")
    if provider == "coinbase_exchange_sandbox" and quote != "USD":
        raise ValueError("coinbase_exchange_sandbox launch requires the USD quote")
    if request.initial_cash <= 0 or request.initial_mark <= 0:
        raise ValueError("testnet launch requires positive fake-funds baseline")
    if request.max_submissions is not None and request.max_submissions < 0:
        raise ValueError("testnet launch submission cap cannot be negative")
    return TestnetLaunchRequest(
        provider=provider,
        provider_instrument_id=instrument,
        quote_currency=quote,
        initial_cash=request.initial_cash,
        initial_mark=request.initial_mark,
        max_submissions=request.max_submissions,
    )


class TestnetFixtureDataAdapter:
    """Explicit, clearly-labelled fixture bars for the supervised testnet path.

    This is NOT live market truth: it produces deterministic hourly marks so
    the RiskKernel/OMS/reconciliation loop can be exercised against the real
    sandbox transport without inventing provider data. The watermark key is
    namespaced to the fixture so it can never satisfy a real-source freshness
    check.
    """

    source = _TESTNET_FIXTURE_SOURCE
    dataset = _TESTNET_FIXTURE_DATASET

    def __init__(self, quote_currency: str) -> None:
        quote = quote_currency.strip().upper()
        if not quote:
            raise ValueError("testnet fixture data adapter requires a quote currency")
        self.quote_currency = quote
        self._snapshots: dict[datetime, Snapshot] = {}

    @property
    def watermark_key(self) -> str:
        return f"{self.source}/{self.dataset}"

    def snapshot_at(self, cutoff: datetime) -> Snapshot:
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("testnet fixture cutoff must include a timezone")
        cutoff = cutoff.astimezone(UTC)
        snapshot = Snapshot(
            artifact_id=uuid5(
                NAMESPACE_URL, f"advisorai-v3/testnet-fixture-snapshot/{cutoff.isoformat()}"
            ),
            as_of=cutoff,
            purpose="testnet-fixture",
            data_quality_state="validated",
            created_at=cutoff,
        )
        self._snapshots[cutoff] = snapshot
        return snapshot

    def market_at(self, snapshot: Snapshot) -> object:
        from advisorai.execution import RiskMarketState

        elapsed_hours = max(
            0, int((snapshot.as_of - self._genesis()).total_seconds() // 3600)
        )
        price = Decimal("50000") + Decimal(elapsed_hours * 10)
        return RiskMarketState(
            marks={"BTC": price},
            volatility={"BTC": Decimal("0.01")},
            liquidity_notional={"BTC": Decimal("1000000")},
            venue_healthy={"BTC": True},
        )

    @staticmethod
    def _genesis() -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)

    @staticmethod
    def watermark_value(snapshot: Snapshot) -> str:
        return snapshot.as_of.isoformat()

    def verify_current_watermarks(
        self, _ledgers: SqliteLedgers, watermarks: dict[str, str]
    ) -> bool:
        try:
            for key, value in watermarks.items():
                if key != self.watermark_key:
                    return False
                cutoff = datetime.fromisoformat(value).astimezone(UTC)
                if self.watermark_value(self.snapshot_at(cutoff)) != value:
                    return False
            return True
        except Exception:
            return False

    def verify_current_pit(
        self, _ledgers: SqliteLedgers, verification: object
    ) -> bool:
        from advisorai.session.records import PITSnapshotVerification

        try:
            if not isinstance(verification, PITSnapshotVerification):
                return False
            if verification.source != self.source:
                return False
            snapshot = self.snapshot_at(verification.cutoff)
            return (
                snapshot.artifact_id == verification.snapshot_id
                and verification.first_available_at <= verification.cutoff
                and self.watermark_value(snapshot)
                == verification.watermarks.get(self.watermark_key)
            )
        except Exception:
            return False

    def verify_gap_coverage(
        self, ledgers: SqliteLedgers, gap: object, resolution: object
    ) -> bool:
        from advisorai.session.records import DataGapRecord, DataGapResolutionRecord

        del ledgers
        try:
            if not isinstance(gap, DataGapRecord):
                return False
            if not isinstance(resolution, DataGapResolutionRecord):
                return False
            if gap.source != self.source:
                return False
            if resolution.source != self.source:
                return False
            if resolution.covered_through < gap.gap_end:
                return False
            snapshot = self.snapshot_at(resolution.covered_through)
            return self.watermark_value(snapshot) == resolution.watermark
        except Exception:
            return False


def _snapshot_bound_fx(
    quote_currency: str,
) -> Callable[[Snapshot], object]:
    """Snapshot-bound FX for the launched quote; USDT is pegged at 1.0 only.

    No FX synthesis: USD quotes use identity; USDT quotes use an explicit,
    labelled 1.0 peg recorded per snapshot so outcomes stay comparable.
    Any other quote is refused.
    """

    def provider(snapshot: Snapshot) -> object:
        from advisorai.session.outcomes import QuoteToUsdRate

        quote = quote_currency.strip().upper()
        if quote == "USD":
            rate = Decimal("1")
            source = "currency-identity/USD"
        elif quote == "USDT":
            rate = Decimal("1")
            source = "testnet-fixture/USDT-peg-1.0"
        else:
            raise ValueError(f"testnet launch has no FX provider for {quote!r}")
        return QuoteToUsdRate(
            quote_currency=quote,
            usd_per_quote=rate,
            as_of=snapshot.as_of,
            snapshot_id=snapshot.artifact_id,
            evidence_id=uuid5(
                NAMESPACE_URL, f"advisorai-v3/testnet-fx/{snapshot.artifact_id}"
            ),
            source=source,
        )

    return provider


def _verification_context(validated: TestnetLaunchRequest) -> ProviderProfileContext:
    """Minimal context for pre-baseline instrument verification.

    Carries the same canonical owner instrument the application builder
    uses, so the shared verifier checks the identical base/quote binding.
    The account here is a placeholder for context validation only; the
    durable baseline is seeded from the venue snapshot after verification.
    The verifier never touches ledgers; the placeholder is never written.
    """
    instrument = InstrumentIdentity(
        canonical_id="BTC",
        asset_class=AssetClass.CRYPTO,
        venue="paper",
        venue_symbol=validated.provider_instrument_id,
        base_asset="BTC",
        quote_asset=validated.quote_currency,
    )
    return ProviderProfileContext(
        data=TestnetFixtureDataAdapter(validated.quote_currency),  # type: ignore[arg-type]
        evidence_council=EvidenceCouncil({}),
        decision_model_factory=_reference_factory("quiet"),
        initial_account=AccountState(
            cash=validated.initial_cash,
            marks={"BTC": validated.initial_mark},
            as_of=datetime(1970, 1, 1, tzinfo=UTC),
        ),
        ledgers=None,  # type: ignore[arg-type]
        instrument=instrument,
        account_quote_currency=validated.quote_currency,
        fx_rate_provider=_snapshot_bound_fx(validated.quote_currency),  # type: ignore[arg-type]
    )


def read_testnet_baseline(
    *,
    resolver: CredentialResolver,
    launch: TestnetLaunchRequest,
) -> tuple[object, AccountState]:
    """Read-only venue snapshot used to seed the durable account baseline.

    Verifies the selected instrument and quote against live provider truth
    on the same transport before the account read, using the shared
    profile verifier (exact sandbox host, admitted product, quote match).
    The owner runs this BEFORE first start and records the returned cash,
    positions, and marks in the launch record. A later start with a
    different faucet balance fails closed at reconciliation instead of
    silently adopting new funds. Performs product/account reads only;
    never submits, cancels, transfers, or withdraws.
    """
    from advisorai.integrations import (
        build_binance_spot_testnet_transport,
        build_coinbase_exchange_sandbox_transport,
    )

    validated = _validate_request(launch)
    transport = (
        build_binance_spot_testnet_transport(resolver)
        if validated.provider == "binance_spot_testnet"
        else build_coinbase_exchange_sandbox_transport(resolver)
    )
    verify_selected_instrument(
        transport,
        ProviderProfileComponents(
            context=_verification_context(validated),
            request=ProviderProfileRequest(
                provider=validated.provider,
                provider_instrument_id=validated.provider_instrument_id,
                quote_currency=validated.quote_currency,
                max_submissions=validated.max_submissions,
            ),
        ),
    )
    snapshot = transport.fetch_account_snapshot()
    positions = {
        key: value for key, value in dict(snapshot.positions).items() if value != 0
    }
    for key in positions:
        if key.startswith("asset:"):
            raise ValueError(
                "testnet account holds out-of-profile balances; clear them first"
            )
    mapped_positions = (
        {"BTC": positions[validated.provider_instrument_id]}
        if validated.provider_instrument_id in positions
        else {}
    )
    baseline = AccountState(
        cash=snapshot.cash,
        marks={"BTC": validated.initial_mark},
        as_of=snapshot.as_of,
    )
    baseline.positions.update(mapped_positions)
    return transport, baseline


def build_testnet_owner_application(
    *,
    ledgers: SqliteLedgers,
    state_dir: Path,
    lock_identity: str,
    code_hash: str,
    config_hash: str,
    config: OwnerApplicationConfig,
    resolver: CredentialResolver,
    launch: TestnetLaunchRequest,
    transport: object | None = None,
    baseline: AccountState | None = None,
) -> PaperOwnerApplication:
    """Compose the reviewed sandbox profile under the supervised owner.

    ``transport`` is a test seam only: the owner runtime omits it so the
    profile builder constructs the real reviewed transport from the scoped
    credentials. Tests inject a synthetic double; no network or credential
    use happens here either way. ``baseline`` carries the read-only venue
    snapshot from :func:`read_testnet_baseline`; omitting it falls back to
    the launch-request fake-funds baseline (synthetic/test use only).
    """
    validated = _validate_request(launch)
    data = TestnetFixtureDataAdapter(validated.quote_currency)
    instrument = InstrumentIdentity(
        canonical_id="BTC",
        asset_class=AssetClass.CRYPTO,
        venue="paper",
        venue_symbol=validated.provider_instrument_id,
        base_asset="BTC",
        quote_asset=validated.quote_currency,
    )
    account = baseline if baseline is not None else AccountState(
        cash=validated.initial_cash,
        marks={"BTC": validated.initial_mark},
        as_of=datetime(1970, 1, 1, tzinfo=UTC),
    )
    evidence_council: EvidenceCouncil = (
        EvidenceCouncil({}) if config.scenario == "quiet" else _fixture_council()
    )
    context = ProviderProfileContext(
        data=data,  # type: ignore[arg-type]
        evidence_council=evidence_council,
        decision_model_factory=_reference_factory(config.scenario),
        initial_account=account,
        ledgers=ledgers,
        instrument=instrument,
        account_quote_currency=validated.quote_currency,
        fx_rate_provider=_snapshot_bound_fx(validated.quote_currency),  # type: ignore[arg-type]
    )
    components = ProviderProfileComponents(
        context=context,
        request=ProviderProfileRequest(
            provider=validated.provider,
            provider_instrument_id=validated.provider_instrument_id,
            quote_currency=validated.quote_currency,
            max_submissions=validated.max_submissions,
        ),
    )
    kwargs: dict[str, object] = {}
    if transport is not None:
        kwargs["transport"] = transport
    adapters = build_provider_profile(components, resolver, **kwargs)
    return PaperOwnerApplication(
        ledgers=ledgers,
        state_dir=state_dir,
        lock_identity=lock_identity,
        code_hash=code_hash,
        config_hash=config_hash,
        config=config,
        adapters=adapters,
        venue_profile=validated.provider,
    )


def _reference_factory(scenario: str) -> Callable[[Snapshot], object]:
    def factory(snapshot: Snapshot) -> object:
        from advisorai.decision import (
            DecisionModelArtifact,
            DecisionStance,
            ReferenceDecisionAdapter,
        )

        artifact = DecisionModelArtifact(
            artifact_id=uuid5(
                NAMESPACE_URL, f"advisorai-v3/testnet-reference-model/{snapshot.artifact_id}"
            ),
            model_name="reference",
            adapter_name="owner-testnet-reference-v1",
            code_hash="testnet-reference-v1",
            config_hash=scenario,
            snapshot_id=snapshot.artifact_id,
            created_at=snapshot.as_of,
        )
        return ReferenceDecisionAdapter(
            artifact,
            directional_stance=(
                DecisionStance.LONG if scenario == "qualification" else None
            ),
        )

    return factory


def _fixture_council() -> EvidenceCouncil:
    from datetime import timedelta
    from decimal import Decimal as _Decimal
    from uuid import uuid5 as _uuid5

    from advisorai.agents.council import RoleResult
    from advisorai.contracts import Evidence

    def make_role(role: str, family: str, origin: str) -> object:
        def run(snapshot: Snapshot) -> RoleResult:
            cutoff = snapshot.as_of
            evidence = Evidence(
                artifact_id=_uuid5(
                    NAMESPACE_URL,
                    f"advisorai-v3/testnet-evidence/{role}/{snapshot.artifact_id}",
                ),
                claim=f"deterministic testnet fixture evidence ({role})",
                source_family=family,
                origin=origin,
                observed_at=cutoff,
                first_available_at=cutoff,
                uncertainty=_Decimal("0.5"),
                expires_at=cutoff + timedelta(hours=1),
                created_at=cutoff,
            )
            return RoleResult(role=role, evidence=(evidence,))

        return run

    return EvidenceCouncil(
        {
            "data_verifier": make_role("data_verifier", "market", "testnet-fixture-a"),  # type: ignore[dict-item]
            "technical_flow": make_role("technical_flow", "market", "testnet-fixture-b"),  # type: ignore[dict-item]
            "news_event": make_role("news_event", "news", "testnet-fixture-c"),  # type: ignore[dict-item]
            "skeptic_base_rate": make_role("skeptic_base_rate", "research", "testnet-fixture-d"),  # type: ignore[dict-item]
        }
    )


def observation_from_testnet_bar(
    *,
    provider: str,
    series_id: str,
    period_start: datetime,
    value: str,
    retrieved_at: datetime,
) -> PointInTimeObservation:
    """PIT observation helper for future testnet-sourced bars (unused by default)."""
    import json
    from hashlib import sha256

    if period_start.tzinfo is None or retrieved_at.tzinfo is None:
        raise ValueError("testnet observation times must include a timezone")
    record = {"series_id": series_id, "value": value}
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return PointInTimeObservation(
        instrument=InstrumentIdentity(
            canonical_id=f"testnet:{provider}:{series_id}",
            asset_class=AssetClass.OTHER,
            venue=provider,
            venue_symbol=series_id,
        ),
        entity_id=series_id,
        event_time=period_start.astimezone(UTC),
        effective_time=period_start.astimezone(UTC),
        source_published_at=None,
        first_available_at=retrieved_at.astimezone(UTC),
        ingested_at=retrieved_at.astimezone(UTC),
        source_revision=None,
        raw_artifact_hash=sha256(encoded.encode()).hexdigest(),
        parser_version="testnet-fixture-v1",
        source_family="testnet_fixture",
        origin=provider,
        quality_grade=SourceGrade.RESEARCH,
        intended_use="point_in_time_testnet_fixture",
        value=encoded,
    )


__all__ = [
    "SandboxFixtureDataAdapter",
    "SandboxLaunchRequest",
    "TestnetFixtureDataAdapter",
    "TestnetLaunchRequest",
    "build_testnet_owner_application",
    "observation_from_testnet_bar",
    "read_testnet_baseline",
]


SandboxFixtureDataAdapter = TestnetFixtureDataAdapter
SandboxLaunchRequest = TestnetLaunchRequest
