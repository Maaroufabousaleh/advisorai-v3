"""Hash-chained tamper-evidence audit over the append-only ledgers.

The ledgers are append-only with idempotency keys, but nothing verifies the
chain itself. This module computes a deterministic per-namespace hash chain
over committed events (in rowid order) and verifies continuity: any deletion,
reorder, or content mutation breaks the chain. The audit is a read-only
projection; it never mutates ledger state and grants no authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from .sqlite import LedgerEvent, LedgerNamespace, SqliteLedgers


def _canonical(event: LedgerEvent) -> bytes:
    payload = {
        "event_id": str(event.event_id),
        "namespace": event.namespace.value,
        "event_type": event.event_type,
        "idempotency_key": event.idempotency_key,
        "occurred_at": event.occurred_at.isoformat(),
        "payload": json.loads(
            json.dumps(event.payload, sort_keys=True, separators=(",", ":"), default=str)
        ),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True, slots=True)
class NamespaceChain:
    namespace: LedgerNamespace
    events: int
    head_hash: str


class LedgerAuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chains: tuple[NamespaceChain, ...]
    total_events: int = Field(ge=0)
    chain_hash: str = Field(min_length=64, max_length=64)
    verified: bool = False
    gaps: tuple[str, ...] = ()


def compute_chains(ledgers: SqliteLedgers) -> tuple[NamespaceChain, ...]:
    """Compute the head hash of each namespace chain in commit order."""
    chains: list[NamespaceChain] = []
    for namespace in LedgerNamespace:
        digest = hashlib.sha256(f"advisorai-ledger:{namespace.value}:genesis".encode())
        count = 0
        for event in ledgers.events(namespace):
            digest.update(_canonical(event))
            count += 1
        chains.append(
            NamespaceChain(namespace=namespace, events=count, head_hash=digest.hexdigest())
        )
    return tuple(chains)


def audit(
    ledgers: SqliteLedgers, *, expected: tuple[NamespaceChain, ...] | None = None
) -> LedgerAuditReport:
    """Verify the ledger chain; against `expected` heads when supplied.

    Without expected heads, the audit attests the current chain state
    (counts + head hashes). With expected heads, any mismatch — deletion,
    reorder, mutation, or unexpected append — fails verification.
    """
    chains = compute_chains(ledgers)
    by_namespace = {chain.namespace: chain for chain in chains}
    gaps: list[str] = []
    verified = True
    if expected is not None:
        for want in expected:
            have = by_namespace.get(want.namespace)
            if have is None:
                verified = False
                gaps.append(f"missing_chain:{want.namespace.value}")
            elif have.head_hash != want.head_hash:
                verified = False
                gaps.append(f"chain_mismatch:{want.namespace.value}")
            elif have.events != want.events:
                verified = False
                gaps.append(f"event_count_mismatch:{want.namespace.value}")
    combined = hashlib.sha256()
    for chain in chains:
        combined.update(f"{chain.namespace.value}:{chain.events}:{chain.head_hash}".encode())
    return LedgerAuditReport(
        chains=chains,
        total_events=sum(chain.events for chain in chains),
        chain_hash=combined.hexdigest(),
        verified=verified and (expected is not None),
        gaps=tuple(gaps),
    )


__all__ = ["LedgerAuditReport", "NamespaceChain", "audit", "compute_chains"]
