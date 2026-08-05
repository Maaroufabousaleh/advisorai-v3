from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from advisorai.contracts import AssetClass, Evidence, Snapshot
from advisorai.expansion import (
    ArchiveAutomation,
    ArchiveVerification,
    BrowserEscalationPolicy,
    ChallengerCard,
    ChallengerRegistry,
    CorporateAction,
    CorporateActionType,
    EquityDailyCouncil,
    EquityEvidence,
)
from advisorai.expansion.browser import AcquisitionMethod
from advisorai.expansion.challengers import ChallengerState
from advisorai.ledger import LedgerNamespace, SqliteLedgers
from advisorai.ports import ArchiveObject


class MemoryArchive:
    def __init__(self, name="memory"):
        self.name = name
        self.payloads = {}

    def put(self, key, payload):
        self.payloads[key] = payload
        return ArchiveObject(
            key=key,
            content_hash=__import__("hashlib").sha256(payload).hexdigest(),
            size_bytes=len(payload),
            encrypted=True,
        )

    def get(self, key):
        return self.payloads[key]

    def verify(self, obj):
        return obj.content_hash == __import__("hashlib").sha256(self.payloads[obj.key]).hexdigest()


def test_challenger_cannot_be_admitted_without_paper_and_marginal_value():
    registry = ChallengerRegistry()
    registry.register(ChallengerCard(name="tabpfn-ts", version="v1", category="forecast"))
    with pytest.raises(ValueError):
        registry.promote(
            name="tabpfn-ts",
            version="v1",
            target=ChallengerState.ADMITTED,
            marginal_net_utility=Decimal("1"),
            core_stability_preserved=True,
        )
    registry.promote(
        name="tabpfn-ts",
        version="v1",
        target=ChallengerState.SHADOW,
        marginal_net_utility=Decimal("0"),
        core_stability_preserved=True,
    )
    registry.promote(
        name="tabpfn-ts",
        version="v1",
        target=ChallengerState.PAPER,
        marginal_net_utility=Decimal("0.1"),
        core_stability_preserved=True,
    )
    assert (
        registry.promote(
            name="tabpfn-ts",
            version="v1",
            target=ChallengerState.ADMITTED,
            marginal_net_utility=Decimal("0.1"),
            core_stability_preserved=True,
        ).state
        is ChallengerState.ADMITTED
    )


def test_challenger_registry_requires_quarantined_entry():
    registry = ChallengerRegistry()
    with pytest.raises(ValueError, match="quarantined"):
        registry.register(
            ChallengerCard(
                name="pre-admitted",
                version="v1",
                category="forecast",
                state=ChallengerState.PAPER,
            )
        )


def test_challenger_registry_rebuilds_lifecycle_from_model_ledger(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "challengers.sqlite")
    registry = ChallengerRegistry(ledgers)
    registry.register(ChallengerCard(name="candidate", version="v1", category="forecast"))
    registry.promote(
        name="candidate",
        version="v1",
        target=ChallengerState.SHADOW,
        marginal_net_utility=Decimal("0"),
        core_stability_preserved=True,
    )
    restarted = ChallengerRegistry(ledgers)
    assert restarted.all()[0].state is ChallengerState.SHADOW
    assert len(ledgers.events(LedgerNamespace.MODEL)) == 2


def test_browser_escalation_requires_public_page_and_failure():
    policy = BrowserEscalationPolicy()
    with pytest.raises(PermissionError):
        policy.admit(
            url="https://x",
            method=AcquisitionMethod.PLAYWRIGHT,
            public_page=True,
            ordinary_method_failed=False,
        )
    job = policy.admit(
        url="https://x",
        method=AcquisitionMethod.PLAYWRIGHT,
        public_page=True,
        ordinary_method_failed=True,
    )
    assert job.prompt_injection_blocked and job.active_content_stripped


def test_browser_escalation_rejects_non_public_url_targets():
    policy = BrowserEscalationPolicy()
    with pytest.raises(ValueError, match="HTTP"):
        policy.admit(
            url="file:///tmp/secret",
            method=AcquisitionMethod.OFFICIAL_API,
            public_page=True,
            ordinary_method_failed=False,
        )
    with pytest.raises(PermissionError, match="private"):
        policy.admit(
            url="http://127.0.0.1:8080/metadata",
            method=AcquisitionMethod.OFFICIAL_API,
            public_page=True,
            ordinary_method_failed=False,
        )


def test_equity_daily_council_requires_independent_evidence_and_pit_actions(btc_usdt, timestamp):
    def record(claim, family, origin, factor):
        return EquityEvidence(
            evidence=Evidence(
                claim=claim,
                source_family=family,
                origin=origin,
                observed_at=timestamp,
                first_available_at=timestamp,
                expires_at=timestamp + timedelta(days=1),
                uncertainty=Decimal("0.1"),
            ),
            factor_family=factor,
        )

    equity = btc_usdt.model_copy(
        update={"canonical_id": "NYSE:TEST", "asset_class": AssetClass.EQUITY, "venue": "NYSE"}
    )
    action = CorporateAction(
        action_id=uuid4(),
        instrument=equity,
        action_type=CorporateActionType.DIVIDEND,
        announced_at=timestamp,
        effective_at=timestamp + timedelta(days=1),
        first_available_at=timestamp,
        ingested_at=timestamp,
        cash_amount=Decimal("1"),
        source_artifact_hash="a" * 64,
    )
    result = EquityDailyCouncil().evaluate(
        snapshot=Snapshot(as_of=timestamp, purpose="equity-daily"),
        evidence=(
            record("quality", "official", "sec", "data_verifier"),
            record("market", "market", "exchange", "technical"),
            record("macro", "macro", "alfred", "macro"),
        ),
        corporate_actions=(action,),
    )
    assert result.passed
    assert result.corporate_action_ids == (action.action_id,)

    future = action.model_copy(
        update={"action_id": uuid4(), "first_available_at": timestamp + timedelta(minutes=1)}
    )
    blocked = EquityDailyCouncil().evaluate(
        snapshot=Snapshot(as_of=timestamp, purpose="equity-daily"),
        evidence=(),
        corporate_actions=(future,),
    )
    assert not blocked.passed
    assert "corporate_action_unavailable_at_cutoff" in blocked.reasons


def test_archive_requires_two_provider_restore_verification():
    first, second = MemoryArchive("memory-a"), MemoryArchive("memory-b")
    result = ArchiveAutomation((first, second)).archive(key="bundle", payload=b"state")
    assert result.passed and result.restore_verified
    assert not ArchiveAutomation((first,)).archive(key="bundle2", payload=b"state").passed


def test_archive_rejects_duplicate_provider_identity():
    first, second = MemoryArchive("same"), MemoryArchive("same")
    result = ArchiveAutomation((first, second)).archive(key="bundle", payload=b"state")
    assert not result.passed
    assert "two_distinct_providers_required" in result.reasons


def test_archive_verification_rejects_duplicate_providers_directly():
    with pytest.raises(ValueError, match="distinct"):
        ArchiveVerification(
            key="bundle",
            content_hash="a" * 64,
            providers=("memory-a", "MEMORY-A"),
            upload_verified=True,
            restore_verified=True,
            passed=True,
        )


def test_archive_verification_can_be_replayed_from_the_incident_ledger(tmp_path):
    ledgers = SqliteLedgers(tmp_path / "archive.sqlite")
    first, second = MemoryArchive("memory-a"), MemoryArchive("memory-b")
    result = ArchiveAutomation((first, second), ledgers=ledgers).archive(
        key="bundle", payload=b"state"
    )
    assert result.passed
    event = next(
        event
        for event in ledgers.events(LedgerNamespace.INCIDENT)
        if event.event_type == "archive_verification_recorded"
    )
    assert event.payload["verification"]["content_hash"] == result.content_hash
