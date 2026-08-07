# ADR 0003: Phase 0 bake-off status

Status: real local bake-off measured; selected roles pending 24-hour stability

The repository now contains a non-invasive Phase 0 inventory and short-probe
harness in `advisorai.phase0`. It records exact candidate names, availability,
versions, resource samples, benchmark hashes, route identity, privacy result,
failure handling, and stability evidence. The gate rejects a `passed` decision
unless every selected component has reproducible identity and at least 24 hours
of measured stability.

The current laptop has real isolated/offline qualification evidence for:

- PydanticAI/Graph, LiteLLM, NautilusTrader, Prefect, Hamilton (`sf-hamilton`),
  and the direct API recovery port: runtime/import or command available;
- TTM-R2, TTM-R3, TSPulse, Chronos-2-small, Kronos-mini/small,
  ModernFinBERT, FinBERT-MiniLM, and finance DeBERTa-v3: measured within the
  laptop resource envelope;
- TTM-R2: forecast role winner pending 24-hour stability;
- finance DeBERTa-v3 and FinBERT-MiniLM: sentiment quality/fast-path winners
  pending 24-hour stability;
- TabPFN-TS: waiting for user acceptance of gated upstream access;
- OmniRoute, DuckLake, Hermes, and rclone: separate component workstreams and
  not admitted by this model bake-off.

Runtime availability is not production admission. The orchestration and Nautilus
wrappers remain quarantined unless an explicit Phase 0 admission record is
provided, even when the package is installed locally.

This is intentionally not the complete Phase-0 pass. Models were not promoted
by name, import availability, or synthetic probes: the role decisions use
frozen public financial datasets and exact runtime/checkpoint evidence. The
24-hour model stability window and non-model Phase-0 component gates remain
open, so no `passed` Phase-0 gate or live-capital claim is made.
