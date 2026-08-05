# ADR 0006: Canonical runtimes are optional and gate-controlled

PydanticAI/Pydantic Graph, Prefect, Hamilton, LiteLLM, and NautilusTrader are
available through the explicit `runtimes` extra rather than the lightweight base
environment. Trade/Fast must not import research-only/browser/Hermes/training
dependencies. Installing an extra is not promotion: Phase 0 still records exact
versions, route identity, privacy, resource, failure, and 24-hour stability
evidence before any runtime owns a production boundary.
