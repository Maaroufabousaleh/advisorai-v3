"""Typed local process-boundary descriptors for the AdvisorAI deployment.

The registry is descriptive rather than a second execution engine.  It makes
ownership and mode admission testable while deterministic components continue
to own account, risk, OMS, and ledger state.
"""

from .boundaries import (
    DEFAULT_SERVICES,
    ServiceDescriptor,
    ServiceKind,
    ServiceRegistry,
)

__all__ = ["DEFAULT_SERVICES", "ServiceDescriptor", "ServiceKind", "ServiceRegistry"]
