"""Append-only instrument identity registry.

An instrument is identified by a canonical ID and validity interval, not by a
reused venue ticker alone. The in-memory registry is intentionally sufficient for
Phase 1; a ledger-backed projection can replace it without changing contracts.
"""

from datetime import UTC, datetime

from advisorai.contracts import InstrumentIdentity


class InstrumentRegistry:
    def __init__(self) -> None:
        self._by_canonical_id: dict[str, tuple[InstrumentIdentity, ...]] = {}

    def register(self, instrument: InstrumentIdentity) -> None:
        existing = self._by_canonical_id.get(instrument.canonical_id, ())
        for item in existing:
            if item.artifact_id == instrument.artifact_id:
                if item != instrument:
                    raise ValueError("instrument identity artifact is immutable")
                return
        for prior in existing:
            starts_before_end = (
                instrument.valid_from is None
                or prior.valid_to is None
                or instrument.valid_from < prior.valid_to
            )
            prior_starts_before_end = (
                prior.valid_from is None
                or instrument.valid_to is None
                or prior.valid_from < instrument.valid_to
            )
            if starts_before_end and prior_starts_before_end:
                raise ValueError("instrument validity windows overlap for canonical ID")
        self._by_canonical_id[instrument.canonical_id] = (*existing, instrument)

    def resolve(self, canonical_id: str, as_of: datetime) -> InstrumentIdentity:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("instrument resolution cutoff must include a timezone")
        as_of = as_of.astimezone(UTC)
        candidates = self._by_canonical_id.get(canonical_id, ())
        for instrument in candidates:
            starts_before = instrument.valid_from is None or instrument.valid_from <= as_of
            ends_after = instrument.valid_to is None or as_of < instrument.valid_to
            if starts_before and ends_after:
                return instrument
        raise KeyError(f"no valid instrument identity for {canonical_id} at {as_of.isoformat()}")
