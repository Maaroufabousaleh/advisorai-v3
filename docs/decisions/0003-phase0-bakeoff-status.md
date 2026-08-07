# ADR 0003: Phase 0 bake-off status

Status: executable harness implemented; admission gate pending measured runs

The repository now contains a non-invasive Phase 0 inventory and short-probe
harness in `advisorai.phase0`. It records exact candidate names, availability,
versions, resource samples, benchmark hashes, route identity, privacy result,
failure handling, and stability evidence. The gate rejects a `passed` decision
unless every selected component has reproducible identity and at least 24 hours
of measured stability.

The current laptop checkout reports the following candidate state from the
inventory command:

- PydanticAI/Graph, LiteLLM, NautilusTrader, Prefect, Hamilton (`sf-hamilton`),
  and the direct API recovery port: runtime/import or command available;
- OmniRoute, LightGBM, TTM-R3 (with TTM-R2 control), TSPulse, Chronos-2-small, Kronos-mini/small, TabPFN-TS,
  DuckLake, Hermes, and rclone: quarantined until their declared runtime probes
  are available and measured.

Runtime availability is not production admission. The orchestration and Nautilus
wrappers remain quarantined unless an explicit Phase 0 admission record is
provided, even when the package is installed locally.

This is intentionally not a Phase 0 pass. No external component is promoted by
name, import availability, or a short synthetic probe. Run the inventory and
then supply the pinned benchmark/stability records before writing a `passed`
gate record.
