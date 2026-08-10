"""Explicit source selection and failover without silent identity substitution."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from advisorai.collectors.source_health import SourceHealthState


class SourceSelectionState(StrEnum):
    CONTINUE = "continue"
    FAILOVER = "failover"
    FAIL_CLOSED = "fail_closed"


class SourceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1)
    provider_identity: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    health_state: SourceHealthState
    contract_valid: bool
    read_only: bool
    symbols: tuple[str, ...] = Field(min_length=1)
    priority: int = Field(ge=0)

    @field_validator("source_id", "provider_identity", "endpoint")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source selection identity cannot be blank")
        return value

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().upper() for item in value if item.strip())
        if not normalized or len(normalized) != len(set(normalized)):
            raise ValueError("source selection symbols must be unique and nonblank")
        return normalized


class SourceSelectionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: SourceSelectionState
    previous_source_id: str | None = None
    selected_source_id: str | None = None
    selected_provider_identity: str | None = None
    selected_endpoint: str | None = None
    reason: str = Field(min_length=1)
    continuity_reset: bool
    quality_recomputed: bool
    fail_closed: bool
    actual_source_identity: str | None = None


def select_source(
    candidates: tuple[SourceCandidate, ...],
    *,
    required_symbols: tuple[str, ...],
    current_source_id: str | None = None,
) -> SourceSelectionDecision:
    """Choose only an independently healthy, reviewed read-only candidate.

    A change of provider always resets continuity and requires downstream
    quality recomputation.  No decision contains a blended or inherited source
    identity.
    """

    required = {symbol.strip().upper() for symbol in required_symbols if symbol.strip()}
    if not required:
        raise ValueError("source selection requires at least one symbol")
    eligible = tuple(
        sorted(
            (
                candidate
                for candidate in candidates
                if candidate.health_state is SourceHealthState.HEALTHY
                and candidate.contract_valid
                and candidate.read_only
                and required.issubset(candidate.symbols)
            ),
            key=lambda candidate: (candidate.priority, candidate.source_id),
        )
    )
    current = next(
        (candidate for candidate in eligible if candidate.source_id == current_source_id), None
    )
    if current is not None:
        return SourceSelectionDecision(
            state=SourceSelectionState.CONTINUE,
            previous_source_id=current_source_id,
            selected_source_id=current.source_id,
            selected_provider_identity=current.provider_identity,
            selected_endpoint=current.endpoint,
            reason="current_source_remains_independently_healthy",
            continuity_reset=False,
            quality_recomputed=False,
            fail_closed=False,
            actual_source_identity=current.provider_identity,
        )
    if not eligible:
        return SourceSelectionDecision(
            state=SourceSelectionState.FAIL_CLOSED,
            previous_source_id=current_source_id,
            reason="no_independently_healthy_source_satisfies_contract",
            continuity_reset=current_source_id is not None,
            quality_recomputed=False,
            fail_closed=True,
        )
    selected = eligible[0]
    changed = current_source_id is not None and selected.source_id != current_source_id
    return SourceSelectionDecision(
        state=SourceSelectionState.FAILOVER if changed else SourceSelectionState.CONTINUE,
        previous_source_id=current_source_id,
        selected_source_id=selected.source_id,
        selected_provider_identity=selected.provider_identity,
        selected_endpoint=selected.endpoint,
        reason=(
            "replacement_source_selected_after_explicit_health_transition"
            if changed
            else "initial_source_selected_from_reviewed_healthy_candidates"
        ),
        continuity_reset=changed,
        quality_recomputed=changed,
        fail_closed=False,
        actual_source_identity=selected.provider_identity,
    )


__all__ = [
    "SourceCandidate",
    "SourceSelectionDecision",
    "SourceSelectionState",
    "select_source",
]
