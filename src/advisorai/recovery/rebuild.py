"""Recovery drills rebuild local state from immutable data and ledgers."""

from __future__ import annotations

import json
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.lake import DataLake, DatasetManifest
from advisorai.ledger import LedgerNamespace, SqliteLedgers


class RecoveryReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifacts_verified: int = Field(ge=0)
    ledger_events_verified: int = Field(ge=0)
    rebuilt_state_hash: str = Field(min_length=64, max_length=64)
    archive_restore_verified: bool
    passed: bool
    reasons: tuple[str, ...] = ()

    @field_validator("rebuilt_state_hash")
    @classmethod
    def require_digest(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("rebuilt_state_hash must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def validate_report(self) -> RecoveryReport:
        if self.passed and (
            not self.archive_restore_verified or self.reasons or self.artifacts_verified < 1
        ):
            raise ValueError(
                "a passed recovery report requires verified artifacts, ledgers, and archive"
            )
        return self


class RecoveryService:
    def __init__(self, lake: DataLake, ledgers: SqliteLedgers) -> None:
        self.lake = lake
        self.ledgers = ledgers

    def rebuild(
        self,
        *,
        manifests: tuple[DatasetManifest, ...],
        archive_restore_verified: bool = False,
        account=None,
    ) -> RecoveryReport:
        artifact_hashes: list[str] = []
        if len({manifest.artifact_id for manifest in manifests}) != len(manifests):
            raise ValueError("recovery manifests must be unique")
        for manifest in manifests:
            self.lake.read_manifest(manifest)
            rows = self.lake.read_rows(manifest)
            artifact_hashes.append(f"{manifest.content_hash}:{len(rows)}")
        event_count = 0
        event_fingerprints: list[str] = []
        for namespace in LedgerNamespace:
            events = self.ledgers.events(namespace)
            event_count += len(events)
            event_fingerprints.extend(
                json.dumps(
                    {
                        "namespace": event.namespace.value,
                        "event_type": event.event_type,
                        "idempotency_key": event.idempotency_key,
                        "occurred_at": event.occurred_at.isoformat(),
                        "payload": event.payload,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                for event in events
            )
        account_hash = None
        if account is not None:
            # Hydration is deliberately delegated to the authoritative account
            # ledger projection; recovery never invents balances from Parquet.
            from advisorai.execution import AccountLedger

            AccountLedger(self.ledgers, account, hydrate=True)
            account_hash = account.snapshot().state_hash
        state_hash = sha256(
            "|".join(
                sorted(
                    (
                        *artifact_hashes,
                        *event_fingerprints,
                        *((account_hash,) if account_hash else ()),
                    )
                )
            ).encode("utf-8")
        ).hexdigest()
        reasons_list: list[str] = []
        if not manifests:
            reasons_list.append("no_artifacts_verified")
        if not archive_restore_verified:
            reasons_list.append("archive_restore_not_verified")
        reasons = tuple(reasons_list)
        return RecoveryReport(
            artifacts_verified=len(manifests),
            ledger_events_verified=event_count,
            rebuilt_state_hash=state_hash,
            archive_restore_verified=archive_restore_verified,
            passed=not reasons,
            reasons=reasons,
        )
