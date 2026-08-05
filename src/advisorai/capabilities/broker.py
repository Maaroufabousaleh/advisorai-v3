"""Permission- and resource-filtered capability registry/broker."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from advisorai.contracts import (
    CapabilityCard,
    CapabilityLifecycle,
    is_forbidden_authority_action,
    normalize_authority_action,
)
from advisorai.gates import PhaseGateRegistry
from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers


class CapabilityPermissionError(PermissionError):
    pass


LIFECYCLE_ORDER = (
    CapabilityLifecycle.GAP,
    CapabilityLifecycle.SCOUT,
    CapabilityLifecycle.PIN,
    CapabilityLifecycle.INSPECT,
    CapabilityLifecycle.SANDBOX,
    CapabilityLifecycle.WRAP_BUILD,
    CapabilityLifecycle.CONTRACT_TESTED,
    CapabilityLifecycle.SECURITY_TESTED,
    CapabilityLifecycle.PERFORMANCE_BENCHMARKED,
    CapabilityLifecycle.SHADOW,
    CapabilityLifecycle.ACTIVE_READ,
    CapabilityLifecycle.ACTIVE_WRITE_LIMITED,
    CapabilityLifecycle.DEPRECATED,
)


class CapabilityRegistry:
    def __init__(
        self,
        ledgers: SqliteLedgers | None = None,
        *,
        gate_registry: PhaseGateRegistry | None = None,
    ) -> None:
        self._cards: dict[str, CapabilityCard] = {}
        self.ledgers = ledgers
        self.gate_registry = gate_registry
        if ledgers is not None:
            self._hydrate()

    def _hydrate(self) -> None:
        assert self.ledgers is not None
        for event in self.ledgers.events(LedgerNamespace.CAPABILITY):
            if event.event_type not in {"capability_registered", "capability_transitioned"}:
                continue
            payload = event.payload.get("card")
            if not isinstance(payload, dict):
                raise ValueError("capability ledger contains an invalid card payload")
            card = CapabilityCard.model_validate(payload)
            key = f"{card.name}:{card.capability_version}"
            prior = self._cards.get(key)
            if event.event_type == "capability_registered":
                if card.lifecycle is not CapabilityLifecycle.GAP:
                    raise ValueError("capability ledger registration must begin at gap")
                if not str(event.payload.get("actor", "")).strip():
                    raise ValueError("capability ledger registration requires an actor")
                if prior is not None and prior != card:
                    raise ValueError("capability ledger registration is not immutable")
                self._cards[key] = card
                continue
            if prior is None:
                raise ValueError("capability ledger transition precedes registration")
            if not str(event.payload.get("actor", "")).strip():
                raise ValueError("capability ledger transition requires an actor")
            if card.lifecycle is CapabilityLifecycle.ACTIVE_WRITE_LIMITED and (
                event.payload.get("human_approval") is not True
            ):
                raise ValueError(
                    "active-write capability ledger transition requires human approval"
                )
            self._validate_transition(prior, card.lifecycle)
            self._cards[key] = card

    def register(self, card: CapabilityCard) -> CapabilityCard:
        if card.lifecycle is not CapabilityLifecycle.GAP:
            raise ValueError("capabilities must enter the registry at the gap lifecycle")
        key = f"{card.name}:{card.capability_version}"
        prior = self._cards.get(key)
        if prior is not None and prior != card:
            raise ValueError("capability version is immutable")
        if prior is not None:
            return card
        self._record(card, "capability_registered", actor="registry")
        self._cards[key] = card
        return card

    def get(self, name: str, version: str) -> CapabilityCard:
        try:
            return self._cards[f"{name}:{version}"]
        except KeyError as exc:
            raise KeyError(f"unknown capability {name}:{version}") from exc

    def register_collector_candidate(self, candidate, *, foundry) -> CapabilityCard:
        """Register one typed Hermes collector candidate at the GAP state.

        The conversion is delegated to the foundry so a registry never has to
        interpret source code.  Callers must still promote the returned card
        through every lifecycle stage; registration alone grants no access.
        """

        card = foundry.collector_capability_card(candidate)
        return self.register(card)

    def promote_collector_to_active_read(
        self,
        *,
        candidate,
        foundry,
        actor: str,
    ) -> CapabilityCard:
        """Run the complete read-only lifecycle for one collector candidate.

        This helper is deliberately explicit and bounded: it cannot promote to
        active-write, and the optional Phase 8 registry gate still controls the
        final transition when the application supplies one.
        """

        card = self.register_collector_candidate(candidate, foundry=foundry)
        for target in (
            CapabilityLifecycle.SCOUT,
            CapabilityLifecycle.PIN,
            CapabilityLifecycle.INSPECT,
            CapabilityLifecycle.SANDBOX,
            CapabilityLifecycle.WRAP_BUILD,
            CapabilityLifecycle.CONTRACT_TESTED,
            CapabilityLifecycle.SECURITY_TESTED,
            CapabilityLifecycle.PERFORMANCE_BENCHMARKED,
            CapabilityLifecycle.SHADOW,
            CapabilityLifecycle.ACTIVE_READ,
        ):
            card = self.promote(
                name=card.name,
                version=card.capability_version,
                target=target,
                actor=actor,
            )
        return card

    def promote(
        self,
        *,
        name: str,
        version: str,
        target: CapabilityLifecycle,
        actor: str,
        human_approval: bool = False,
    ) -> CapabilityCard:
        card = self.get(name, version)
        if target is CapabilityLifecycle.ACTIVE_WRITE_LIMITED and not human_approval:
            raise CapabilityPermissionError("active-write-limited requires explicit human approval")
        if (
            target
            in {
                CapabilityLifecycle.ACTIVE_READ,
                CapabilityLifecycle.ACTIVE_WRITE_LIMITED,
            }
            and self.gate_registry is not None
        ):
            try:
                self.gate_registry.require_admitted(8, component="active capability")
            except PermissionError as exc:
                raise CapabilityPermissionError(str(exc)) from exc
        if not actor.strip():
            raise CapabilityPermissionError("every capability transition requires a named actor")
        self._validate_transition(card, target)
        if (
            target
            in {
                CapabilityLifecycle.CONTRACT_TESTED,
                CapabilityLifecycle.SECURITY_TESTED,
                CapabilityLifecycle.PERFORMANCE_BENCHMARKED,
            }
            and not card.test_references
        ):
            raise ValueError(f"{target.value} requires recorded test references")
        required_reference = {
            CapabilityLifecycle.CONTRACT_TESTED: "contract",
            CapabilityLifecycle.SECURITY_TESTED: "security",
            CapabilityLifecycle.PERFORMANCE_BENCHMARKED: "performance",
        }.get(target)
        if required_reference and not any(
            required_reference in reference.lower() for reference in card.test_references
        ):
            raise ValueError(f"{target.value} requires a {required_reference} test reference")
        if target in {CapabilityLifecycle.ACTIVE_WRITE_LIMITED} and not actor.strip():
            raise CapabilityPermissionError("authority promotion requires a named approver")
        updated = card.model_copy(update={"lifecycle": target})
        self._record(
            updated,
            "capability_transitioned",
            actor=actor,
            human_approval=human_approval,
        )
        self._cards[f"{name}:{version}"] = updated
        return updated

    def all(self) -> tuple[CapabilityCard, ...]:
        return tuple(self._cards.values())

    @staticmethod
    def _validate_transition(card: CapabilityCard, target: CapabilityLifecycle) -> None:
        current_index = LIFECYCLE_ORDER.index(card.lifecycle)
        target_index = LIFECYCLE_ORDER.index(target)
        if target_index != current_index + 1 and target is not CapabilityLifecycle.DEPRECATED:
            raise ValueError(f"invalid capability lifecycle transition {card.lifecycle}->{target}")

    def _record(
        self,
        card: CapabilityCard,
        event_type: str,
        *,
        actor: str,
        human_approval: bool = False,
    ) -> None:
        if self.ledgers is None:
            return
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.CAPABILITY,
                event_type=event_type,
                idempotency_key=(
                    f"capability:{card.name}:{card.capability_version}:"
                    f"{card.lifecycle.value}:{card.canonical_hash()}"
                ),
                payload={
                    "card": card.model_dump(mode="json", round_trip=True),
                    "actor": actor,
                    "human_approval": human_approval,
                },
            )
        )


@dataclass(frozen=True, slots=True)
class CapabilityBroker:
    registry: CapabilityRegistry

    def expose(
        self,
        *,
        capability_name: str,
        capability_version: str,
        requested_action: str,
        mode: str,
        executor: Callable[..., object],
        available_resource_envelopes: tuple[str, ...] | None = None,
    ) -> Callable[..., object]:
        card = self.registry.get(capability_name, capability_version)
        if card.lifecycle not in {
            CapabilityLifecycle.ACTIVE_READ,
            CapabilityLifecycle.ACTIVE_WRITE_LIMITED,
        }:
            raise CapabilityPermissionError("capability is not active")
        normalized_action = normalize_authority_action(requested_action)
        if normalized_action not in card.allowed_actions:
            raise CapabilityPermissionError("requested action is not declared by capability card")
        if is_forbidden_authority_action(normalized_action):
            raise CapabilityPermissionError(
                "trading authority is never exposed by capability broker"
            )
        if mode.lower() not in {"deep", "builder", "recovery"}:
            raise CapabilityPermissionError(
                "capabilities are unavailable in Trade/Fast and Standard modes"
            )
        if available_resource_envelopes is not None:
            envelopes = tuple(item.strip() for item in available_resource_envelopes)
            if any(not item for item in envelopes):
                raise CapabilityPermissionError("resource envelopes cannot be blank")
            if card.resource_envelope not in envelopes:
                raise CapabilityPermissionError(
                    "capability resource envelope is not admitted for this worker"
                )
        return executor
