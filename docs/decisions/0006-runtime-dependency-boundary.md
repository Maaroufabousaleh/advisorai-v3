# ADR 0006: Canonical runtimes are optional and gate-controlled

PydanticAI/Pydantic Graph, Prefect, Hamilton, LiteLLM, and NautilusTrader are
available through the explicit `runtimes` extra rather than the lightweight base
environment. Trade/Fast must not import research-only/browser/Hermes/training
dependencies. Installing an extra is not promotion: Phase 0 still records exact
versions, route identity, privacy, resource, failure, and 24-hour stability
evidence before any runtime owns a production boundary.

## Planning-policy supersession — 2026-09-30

The exact identity, route/privacy, resource, failure, and no-authority checks
remain. The 24-hour stability threshold in this original decision is a
superseded planning requirement for the owner-operated workstation; repeated
session/model lifecycle and recovery evidence are primary. Any future
always-on/server profile may define its own endurance gate. This policy update
does not change the historical evidence that accompanied the earlier
decision. See [ADR 0007](0007-session-oriented-operating-model.md).
