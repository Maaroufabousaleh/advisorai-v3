"""Owner backups are private, typed, hash-verified, and restored without overwrite."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers
from advisorai.memory.scorecards import Scorecard, ScorecardStore
from advisorai.session.backups import OwnerStateBackupStore


def _state(tmp_path):
    ledger_path = tmp_path / "state" / "ledgers.sqlite3"
    ledgers = SqliteLedgers(ledger_path)
    ledgers.append(
        LedgerEvent(
            namespace=LedgerNamespace.MISSION,
            event_type="backup_test_event",
            idempotency_key="backup-test:initial",
            payload={"sequence": 1},
        )
    )
    scorecards_path = ledger_path.with_name("scorecards.sqlite3")
    ScorecardStore(scorecards_path).append(
        Scorecard(
            subject="backup-test",
            subject_version="v1",
            role="synthesizer",
            asset="BTC",
            horizon="1h",
            regime="unknown",
            factual_precision=Decimal("0.9"),
            calibration=Decimal("0.8"),
            abstention_quality=Decimal("1"),
            contradiction_detection=Decimal("1"),
            net_utility=Decimal("0.01"),
            latency_ms=12,
            api_cost_usd=Decimal("0"),
            failure_rate=Decimal("0"),
            eligible_for_routing=False,
            recorded_at=datetime(2026, 11, 12, 16, tzinfo=UTC),
        )
    )
    return ledger_path, scorecards_path, ledgers


def test_owner_backup_restores_a_verified_database_pair_into_new_state_root(tmp_path):
    ledger_path, scorecards_path, ledgers = _state(tmp_path)
    store = OwnerStateBackupStore(tmp_path / "backups")
    backup = store.create(
        ledgers_path=ledger_path,
        scorecards_path=scorecards_path,
        code_hash="a" * 64,
        config_hash="b" * 64,
    )
    assert OwnerStateBackupStore.verify(backup.path) == backup.manifest
    assert backup.manifest.ledger_event_counts[LedgerNamespace.MISSION.value] == 1
    assert backup.manifest.scorecard_count == 1
    assert (backup.path / "ledgers.sqlite3").stat().st_mode & 0o777 == 0o600
    assert backup.path.stat().st_mode & 0o777 == 0o700

    ledgers.append(
        LedgerEvent(
            namespace=LedgerNamespace.MISSION,
            event_type="backup_test_event",
            idempotency_key="backup-test:after-snapshot",
            payload={"sequence": 2},
        )
    )
    destination = tmp_path / "restored-state"
    receipt = OwnerStateBackupStore.restore(backup.path, destination)
    assert receipt.backup_id == backup.manifest.backup_id
    assert receipt.code_hash == "a" * 64
    assert receipt.config_hash == "b" * 64
    assert OwnerStateBackupStore.verify(backup.path) == backup.manifest

    restored = SqliteLedgers(destination / "ledgers.sqlite3")
    events = restored.events(LedgerNamespace.MISSION)
    assert [event.idempotency_key for event in events] == ["backup-test:initial"]
    restored_scorecards = ScorecardStore(destination / "scorecards.sqlite3")
    assert restored_scorecards.latest("backup-test", "v1") is not None
    stored_receipt = json.loads((destination / "restore-receipt.json").read_text())
    assert stored_receipt["backup_id"] == str(backup.manifest.backup_id)
    assert stored_receipt["manifest_sha256"] == receipt.manifest_sha256


def test_owner_backup_refuses_corruption_and_preserves_existing_restore_target(tmp_path):
    ledger_path, scorecards_path, _ledgers = _state(tmp_path)
    backup = OwnerStateBackupStore(tmp_path / "backups").create(
        ledgers_path=ledger_path,
        scorecards_path=scorecards_path,
        code_hash="c" * 64,
        config_hash="d" * 64,
    )
    target = tmp_path / "existing-state"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError, match="new isolated"):
        OwnerStateBackupStore.restore(backup.path, target)
    assert sentinel.read_text(encoding="utf-8") == "preserve"

    with (backup.path / "ledgers.sqlite3").open("ab") as handle:
        handle.write(b"corrupt")
    with pytest.raises(ValueError, match="database verification failed"):
        OwnerStateBackupStore.verify(backup.path)
    failed_target = tmp_path / "failed-restore"
    with pytest.raises(ValueError, match="database verification failed"):
        OwnerStateBackupStore.restore(backup.path, failed_target)
    assert not failed_target.exists()


def test_owner_backup_refuses_symlink_database_inputs_and_restore_roots(tmp_path):
    ledger_path, scorecards_path, _ledgers = _state(tmp_path)
    link = tmp_path / "ledger-link.sqlite3"
    link.symlink_to(ledger_path)
    store = OwnerStateBackupStore(tmp_path / "backups")
    with pytest.raises(ValueError, match="regular database"):
        store.create(
            ledgers_path=link,
            scorecards_path=scorecards_path,
            code_hash="e" * 64,
            config_hash="f" * 64,
        )

    backup = store.create(
        ledgers_path=ledger_path,
        scorecards_path=scorecards_path,
        code_hash="e" * 64,
        config_hash="f" * 64,
    )
    destination = tmp_path / "restore-link"
    destination.symlink_to(tmp_path / "not-created", target_is_directory=True)
    with pytest.raises(FileExistsError, match="cannot be a symlink"):
        OwnerStateBackupStore.restore(backup.path, destination)
