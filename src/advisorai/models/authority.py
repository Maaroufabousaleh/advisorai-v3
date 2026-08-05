"""Model inventory and authority disablement on drift or unsupported regimes."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.contracts import ModelCard
from advisorai.gates import PhaseGateRegistry
from advisorai.ledger import LedgerEvent, LedgerNamespace, SqliteLedgers


class ModelAdmissionEvidence(BaseModel):
    """Reproducible evidence required before a model leaves challenger state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_name: str
    model_version: str
    evaluation_hash: str = Field(min_length=64, max_length=64)
    baseline_net_utility: Decimal
    candidate_net_utility: Decimal
    past_only: bool
    calibrated: bool
    resource_limit_passed: bool
    useful_risk_information: bool = False
    independent_review: bool
    reviewer: str
    benchmark_hash: str | None = None
    max_error_correlation: Decimal | None = None
    regime_failures: tuple[str, ...] = ()
    latency_ms: int | None = Field(default=None, ge=0)
    peak_ram_mib: int | None = Field(default=None, ge=0)
    peak_vram_mib: int | None = Field(default=None, ge=0)

    @field_validator("model_name", "model_version", "reviewer")
    @classmethod
    def require_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model admission evidence requires named identities")
        return value.strip()

    @field_validator("evaluation_hash")
    @classmethod
    def require_hash(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("model evaluation hash must be a lowercase SHA-256 digest")
        return value

    @field_validator("benchmark_hash")
    @classmethod
    def require_optional_benchmark_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("benchmark_hash must be a lowercase SHA-256 digest")
        return value

    @field_validator("baseline_net_utility", "candidate_net_utility")
    @classmethod
    def require_finite_utility(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("model utility evidence must be finite")
        return value

    @field_validator("max_error_correlation")
    @classmethod
    def require_correlation_bound(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (
            not value.is_finite() or not Decimal("-1") <= value <= Decimal("1")
        ):
            raise ValueError("model error correlation must be between negative one and one")
        return value

    @field_validator("regime_failures")
    @classmethod
    def normalize_regime_failures(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("model regime failures must be unique and non-blank")
        return normalized

    @model_validator(mode="after")
    def validate_admission_evidence(self) -> ModelAdmissionEvidence:
        if self.model_name == "" or self.model_version == "":
            raise ValueError("model admission evidence requires model identity")
        return self


@dataclass(frozen=True, slots=True)
class ModelAuthority:
    card: ModelCard
    disabled_reason: str | None = None

    @property
    def authoritative(self) -> bool:
        return self.disabled_reason is None and self.card.lifecycle_state in {
            "champion",
            "active_read",
        }


class ModelInventory:
    _LIFECYCLE_ORDER = ("challenger", "shadow", "paper", "champion")

    def __init__(
        self,
        ledgers: SqliteLedgers | None = None,
        *,
        gate_registry: PhaseGateRegistry | None = None,
    ) -> None:
        self._models: dict[tuple[str, str], ModelAuthority] = {}
        self.ledgers = ledgers
        self.gate_registry = gate_registry
        if ledgers is not None:
            self._hydrate()

    def _hydrate(self) -> None:
        assert self.ledgers is not None
        for event in self.ledgers.events(LedgerNamespace.MODEL):
            if event.event_type not in {"model_registered", "model_promoted", "model_disabled"}:
                continue
            payload = event.payload.get("card")
            if not isinstance(payload, dict):
                raise ValueError("model ledger contains an invalid model card payload")
            card = ModelCard.model_validate(payload)
            key = (card.model_name, card.model_version)
            if event.event_type == "model_registered":
                if card.lifecycle_state not in {
                    "challenger",
                    "shadow",
                    "paper",
                    "active_read",
                    "champion",
                    "retired",
                }:
                    raise ValueError("model ledger contains an unknown lifecycle state")
                prior = self._models.get(key)
                if prior is not None and prior.card != card:
                    raise ValueError("model ledger registration is not immutable")
                self._models[key] = ModelAuthority(card=card)
            elif event.event_type == "model_promoted":
                prior = self._models.get(key)
                if prior is None:
                    raise ValueError("model promotion precedes registration")
                evidence_payload = event.payload.get("evidence")
                if not isinstance(evidence_payload, dict):
                    raise ValueError("model promotion requires admission evidence")
                evidence = ModelAdmissionEvidence.model_validate(evidence_payload)
                self._validate_promotion(prior.card, card, evidence)
                self._models[key] = ModelAuthority(card=card)
            else:
                if key not in self._models:
                    raise ValueError("model ledger disablement precedes registration")
                reason = event.payload.get("reason")
                if not isinstance(reason, str) or not reason.strip():
                    raise ValueError("model ledger disablement requires a reason")
                self._models[key] = ModelAuthority(card=card, disabled_reason=reason)

    def register(self, card: ModelCard) -> ModelAuthority:
        if card.lifecycle_state not in {
            "challenger",
            "shadow",
            "paper",
            "active_read",
            "champion",
            "retired",
        }:
            raise ValueError(f"unknown model lifecycle state: {card.lifecycle_state}")
        key = (card.model_name, card.model_version)
        prior = self._models.get(key)
        if prior is not None and prior.card != card:
            raise ValueError("model version is immutable")
        if prior is not None:
            return prior
        authority = ModelAuthority(card=card)
        self._record(card, "model_registered", actor="inventory")
        self._models[key] = authority
        return authority

    def disable(self, model_name: str, model_version: str, reason: str) -> ModelAuthority:
        model_name = model_name.strip()
        model_version = model_version.strip()
        reason = reason.strip()
        if not model_name or not model_version or not reason:
            raise ValueError("model disablement requires a reason")
        key = (model_name, model_version)
        prior = self._models[key]
        updated = ModelAuthority(card=prior.card, disabled_reason=reason)
        self._record(prior.card, "model_disabled", actor="operator", reason=reason)
        self._models[key] = updated
        return updated

    def promote(
        self,
        model_name: str,
        model_version: str,
        target: str,
        evidence: ModelAdmissionEvidence,
    ) -> ModelAuthority:
        """Move a model through challenger/shadow/paper/champion with evidence."""

        key = (model_name.strip(), model_version.strip())
        if not key[0] or not key[1]:
            raise ValueError("model promotion requires model identity")
        prior = self._models[key]
        target = target.strip().lower()
        if target not in self._LIFECYCLE_ORDER:
            raise ValueError(f"unknown model promotion state: {target}")
        if target in {"paper", "champion"} and self.gate_registry is not None:
            try:
                self.gate_registry.require_admitted(4, component="paper/champion model")
            except PermissionError as exc:
                raise ValueError(str(exc)) from exc
        current_index = self._LIFECYCLE_ORDER.index(prior.card.lifecycle_state)
        target_index = self._LIFECYCLE_ORDER.index(target)
        if target_index != current_index + 1:
            raise ValueError(
                f"invalid model lifecycle transition {prior.card.lifecycle_state}->{target}"
            )
        # Carry the exact evaluation artifact and net utility that justified
        # the transition onto the durable ModelCard.  The evidence remains in
        # the ledger as a full record, while the card gives downstream readers
        # a self-contained provenance pointer instead of an unbound lifecycle
        # string.
        updated = prior.card.model_copy(
            update={
                "lifecycle_state": target,
                "evaluation_hash": evidence.evaluation_hash,
                "net_utility_after_costs": evidence.candidate_net_utility,
            }
        )
        self._validate_promotion(prior.card, updated, evidence)
        self._record(updated, "model_promoted", actor=evidence.reviewer, evidence=evidence)
        authority = ModelAuthority(card=updated)
        self._models[key] = authority
        return authority

    def get(self, model_name: str, model_version: str) -> ModelAuthority:
        return self._models[(model_name, model_version)]

    def all(self) -> tuple[ModelAuthority, ...]:
        return tuple(self._models.values())

    def _record(
        self,
        card: ModelCard,
        event_type: str,
        *,
        actor: str,
        reason: str | None = None,
        evidence: ModelAdmissionEvidence | None = None,
    ) -> None:
        if self.ledgers is None:
            return
        payload: dict[str, object] = {
            "card": card.model_dump(mode="json", round_trip=True),
            "actor": actor,
        }
        if reason is not None:
            payload["reason"] = reason
        if evidence is not None:
            payload["evidence"] = evidence.model_dump(mode="json", round_trip=True)
        self.ledgers.append(
            LedgerEvent(
                namespace=LedgerNamespace.MODEL,
                event_type=event_type,
                idempotency_key=(
                    f"model:{card.model_name}:{card.model_version}:{event_type}:"
                    f"{card.canonical_hash()}"
                ),
                payload=payload,
            )
        )

    @classmethod
    def _validate_promotion(
        cls, prior: ModelCard, updated: ModelCard, evidence: ModelAdmissionEvidence
    ) -> None:
        if (evidence.model_name, evidence.model_version) != (
            updated.model_name,
            updated.model_version,
        ):
            raise ValueError("model admission evidence identity does not match the model")
        if updated.evaluation_hash != evidence.evaluation_hash:
            raise ValueError("model card evaluation hash does not match admission evidence")
        if (
            prior.lifecycle_state not in cls._LIFECYCLE_ORDER
            or updated.lifecycle_state not in cls._LIFECYCLE_ORDER
        ):
            raise ValueError("model promotion lifecycle state is not promotable")
        if (
            cls._LIFECYCLE_ORDER.index(updated.lifecycle_state)
            != cls._LIFECYCLE_ORDER.index(prior.lifecycle_state) + 1
        ):
            raise ValueError(
                f"invalid model lifecycle transition {prior.lifecycle_state}->{updated.lifecycle_state}"
            )
        if not evidence.past_only or not evidence.calibrated or not evidence.resource_limit_passed:
            raise ValueError("model promotion requires past-only calibrated resource evidence")
        if updated.lifecycle_state in {"paper", "champion"} and not (
            evidence.candidate_net_utility > evidence.baseline_net_utility
            or evidence.useful_risk_information
        ):
            raise ValueError(
                "paper/champion promotion requires positive marginal utility or useful risk information"
            )
        if updated.lifecycle_state == "champion" and (
            not evidence.independent_review
            or evidence.candidate_net_utility <= evidence.baseline_net_utility
        ):
            raise ValueError("champion promotion requires independent positive marginal utility")
