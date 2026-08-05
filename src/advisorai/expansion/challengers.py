"""One-at-a-time challenger lifecycle with marginal-value admission."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from advisorai.gates import PhaseGateRegistry
from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers


class ChallengerState(StrEnum):
    QUARANTINED = "quarantined"
    SHADOW = "shadow"
    PAPER = "paper"
    ADMITTED = "admitted"
    RETIRED = "retired"


class ChallengerCard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: str
    category: str
    state: ChallengerState = ChallengerState.QUARANTINED
    independent_of: tuple[str, ...] = ()
    marginal_net_utility: Decimal | None = None
    core_stability_preserved: bool = False
    sole_authority: bool = False
    notes: tuple[str, ...] = ()

    @field_validator("independent_of", "notes")
    @classmethod
    def normalize_metadata(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("challenger metadata must be unique and non-blank")
        return normalized

    @model_validator(mode="after")
    def validate_card(self) -> ChallengerCard:
        if not self.name.strip() or not self.version.strip() or not self.category.strip():
            raise ValueError("challenger cards require name, version, and category")
        if self.marginal_net_utility is not None and not self.marginal_net_utility.is_finite():
            raise ValueError("challenger marginal utility must be finite")
        if self.sole_authority and self.state is not ChallengerState.RETIRED:
            raise ValueError("challengers can never be sole authority")
        return self


class ChallengerRegistry:
    def __init__(
        self,
        ledgers: SqliteLedgers | None = None,
        *,
        gate_registry: PhaseGateRegistry | None = None,
    ) -> None:
        self._cards: dict[str, ChallengerCard] = {}
        self.ledgers = ledgers
        self.gate_registry = gate_registry
        if ledgers is not None:
            self._hydrate()

    def _hydrate(self) -> None:
        assert self.ledgers is not None
        for event in self.ledgers.events(LedgerNamespace.MODEL):
            if event.event_type not in {"challenger_registered", "challenger_transitioned"}:
                continue
            payload = event.payload.get("card")
            if not isinstance(payload, dict):
                raise ValueError("challenger ledger contains an invalid card payload")
            card = ChallengerCard.model_validate(payload)
            key = f"{card.name}:{card.version}"
            prior = self._cards.get(key)
            if event.event_type == "challenger_registered":
                if card.state is not ChallengerState.QUARANTINED:
                    raise ValueError("challenger ledger registration must begin quarantined")
                if not str(event.payload.get("actor", "")).strip():
                    raise ValueError("challenger ledger registration requires an actor")
                if prior is not None and prior != card:
                    raise ValueError("challenger registration is not immutable")
                self._cards[key] = card
                continue
            if prior is None:
                raise ValueError("challenger transition precedes registration")
            if not str(event.payload.get("actor", "")).strip():
                raise ValueError("challenger ledger transition requires an actor")
            self._validate_transition(prior, card.state)
            self._cards[key] = card

    def register(self, card: ChallengerCard) -> ChallengerCard:
        if card.state is not ChallengerState.QUARANTINED:
            raise ValueError("challengers must enter the registry quarantined")
        key = f"{card.name}:{card.version}"
        if key in self._cards and self._cards[key] != card:
            raise ValueError("challenger card is immutable")
        if key in self._cards:
            return card
        self._record(card, "challenger_registered", actor="registry")
        self._cards[key] = card
        return card

    def promote(
        self,
        *,
        name: str,
        version: str,
        target: ChallengerState,
        marginal_net_utility: Decimal,
        core_stability_preserved: bool,
    ) -> ChallengerCard:
        if not marginal_net_utility.is_finite():
            raise ValueError("challenger marginal utility must be finite")
        key = f"{name}:{version}"
        card = self._cards[key]
        self._validate_transition(card, target)
        if (
            target
            in {
                ChallengerState.SHADOW,
                ChallengerState.PAPER,
                ChallengerState.ADMITTED,
            }
            and self.gate_registry is not None
        ):
            try:
                self.gate_registry.require_admitted(9, component="challenger expansion")
            except PermissionError as exc:
                raise ValueError(str(exc)) from exc
        if target in {
            ChallengerState.SHADOW,
            ChallengerState.PAPER,
            ChallengerState.ADMITTED,
        } and any(
            other_key != key
            and other.state
            in {ChallengerState.SHADOW, ChallengerState.PAPER, ChallengerState.ADMITTED}
            for other_key, other in self._cards.items()
        ):
            raise ValueError(
                "only one challenger may be in shadow/paper/admitted expansion at a time"
            )
        if target is ChallengerState.ADMITTED and (
            marginal_net_utility <= 0 or not core_stability_preserved or card.sole_authority
        ):
            raise ValueError(
                "challenger cannot be admitted without positive marginal value and core stability"
            )
        if target is ChallengerState.ADMITTED and card.state is not ChallengerState.PAPER:
            raise ValueError("challenger must pass shadow and paper before admission")
        updated = card.model_copy(
            update={
                "state": target,
                "marginal_net_utility": marginal_net_utility,
                "core_stability_preserved": core_stability_preserved,
            }
        )
        self._record(updated, "challenger_transitioned", actor="operator")
        self._cards[key] = updated
        return updated

    def all(self) -> tuple[ChallengerCard, ...]:
        return tuple(self._cards.values())

    @staticmethod
    def _validate_transition(card: ChallengerCard, target: ChallengerState) -> None:
        allowed_next = {
            ChallengerState.QUARANTINED: {ChallengerState.SHADOW, ChallengerState.RETIRED},
            ChallengerState.SHADOW: {ChallengerState.PAPER, ChallengerState.RETIRED},
            ChallengerState.PAPER: {ChallengerState.ADMITTED, ChallengerState.RETIRED},
            ChallengerState.ADMITTED: {ChallengerState.RETIRED},
            ChallengerState.RETIRED: set(),
        }
        if target not in allowed_next[card.state]:
            raise ValueError(f"invalid challenger lifecycle transition {card.state}->{target}")

    def _record(self, card: ChallengerCard, event_type: str, *, actor: str) -> None:
        if self.ledgers is None:
            return
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MODEL,
                event_type=event_type,
                idempotency_key=(
                    f"challenger:{card.name}:{card.version}:{card.state.value}:"
                    f"{card.model_dump_json()}"
                ),
                payload={"card": card.model_dump(mode="json", round_trip=True), "actor": actor},
            )
        )
