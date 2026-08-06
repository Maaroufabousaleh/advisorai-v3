"""Connector cards and lifecycle state persisted without credential material."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers


class ConnectorState(StrEnum):
    DISABLED = "disabled"
    CONFIGURED = "configured"
    SMOKE_TESTED = "smoke-tested"
    SHADOW = "shadow"
    ACTIVE_READ = "active-read"
    PAPER_ONLY = "paper-only"
    REVOKED = "revoked"


_ALLOWED_TRANSITIONS: dict[ConnectorState, frozenset[ConnectorState]] = {
    ConnectorState.DISABLED: frozenset({ConnectorState.CONFIGURED}),
    ConnectorState.CONFIGURED: frozenset({ConnectorState.SMOKE_TESTED, ConnectorState.REVOKED}),
    ConnectorState.SMOKE_TESTED: frozenset(
        {ConnectorState.SHADOW, ConnectorState.PAPER_ONLY, ConnectorState.REVOKED}
    ),
    ConnectorState.SHADOW: frozenset({ConnectorState.ACTIVE_READ, ConnectorState.REVOKED}),
    ConnectorState.ACTIVE_READ: frozenset({ConnectorState.REVOKED}),
    ConnectorState.PAPER_ONLY: frozenset({ConnectorState.REVOKED}),
    ConnectorState.REVOKED: frozenset({ConnectorState.CONFIGURED}),
}


class ConnectorCard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    allowed_hosts: tuple[str, ...] = Field(min_length=1)
    environment: str = "paper_testnet"
    credential_refs: tuple[str, ...] = ()
    source_grade: str = Field(min_length=1)
    quota_and_cost: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    rollback_procedure: str = Field(min_length=1)
    state: ConnectorState = ConnectorState.DISABLED
    config_hash: str | None = None

    @field_validator(
        "name",
        "owner",
        "purpose",
        "endpoint",
        "source_grade",
        "quota_and_cost",
        "adapter_version",
        "rollback_procedure",
    )
    @classmethod
    def nonblank(cls, value: str) -> str:
        return value.strip()

    @field_validator("allowed_hosts")
    @classmethod
    def normalize_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        hosts = tuple(item.strip().lower() for item in value if item.strip())
        if not hosts:
            raise ValueError("connector cards require at least one allowed host")
        return tuple(dict.fromkeys(hosts))

    @field_validator("environment")
    @classmethod
    def paper_environment(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"paper", "testnet", "paper_testnet"}:
            raise ValueError("transition connector cards are paper/testnet only")
        return normalized

    def canonical_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"config_hash", "state"}, round_trip=True)
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def with_state(self, state: ConnectorState, *, reason: str) -> ConnectorCard:
        if not reason.strip():
            raise ValueError("connector state changes require a reason")
        if state is not self.state and state not in _ALLOWED_TRANSITIONS[self.state]:
            raise ValueError(
                f"invalid connector lifecycle transition {self.state.value}->{state.value}"
            )
        return self.model_copy(update={"state": state, "config_hash": self.canonical_hash()})


class ConnectorRegistry:
    def __init__(self, ledgers: SqliteLedgers) -> None:
        self.ledgers = ledgers
        self.cards: dict[str, ConnectorCard] = {}
        self._hydrate()

    def _hydrate(self) -> None:
        for event in self.ledgers.events(LedgerNamespace.CAPABILITY):
            if event.event_type != "connector_card_recorded":
                continue
            payload = event.payload.get("card")
            if isinstance(payload, dict):
                card = ConnectorCard.model_validate(payload)
                self.cards[card.name] = card

    def register(self, card: ConnectorCard, *, reason: str) -> ConnectorCard:
        previous = self.cards.get(card.name)
        if previous is not None and previous.canonical_hash() != card.canonical_hash():
            raise ValueError("connector identity/config changed without revocation")
        # Re-registration is still a lifecycle transition.  Validate the
        # requested state from the durable stored card rather than from the
        # incoming card, which could otherwise jump over smoke testing.
        stored = (
            previous.with_state(card.state, reason=reason)
            if previous is not None
            else card.with_state(card.state, reason=reason)
        )
        self._append(stored, reason=reason)
        self.cards[stored.name] = stored
        return stored

    def transition(self, name: str, state: ConnectorState, *, reason: str) -> ConnectorCard:
        try:
            current = self.cards[name]
        except KeyError as exc:
            raise KeyError(name) from exc
        updated = current.with_state(state, reason=reason)
        self._append(updated, reason=reason)
        self.cards[name] = updated
        return updated

    def _append(self, card: ConnectorCard, *, reason: str) -> None:
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.CAPABILITY,
                event_type="connector_card_recorded",
                idempotency_key=f"connector:{card.name}:{card.state.value}:{card.config_hash}",
                payload={"card": card.model_dump(mode="json", round_trip=True), "reason": reason},
            )
        )


__all__ = ["ConnectorCard", "ConnectorRegistry", "ConnectorState"]
