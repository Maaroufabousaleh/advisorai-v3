# Extending AdvisorAI

The preferred extension pattern is to implement a narrow typed port or an existing role/adapter interface, register it explicitly, and test its failure and authority boundaries. Importing a new provider is not enough: credentials, network access, privacy class, phase admission, and output provenance all matter.

## Replaceable ports

The canonical protocols live in [`src/advisorai/ports.py`](../../src/advisorai/ports.py):

| Port | Contract | Example implementation |
| --- | --- | --- |
| `ModelGatewayPort` | `complete(request: GatewayRequest) -> GatewayResponse` | `OpenAICompatibleGatewayAdapter` through the governed gateway |
| `ArchiveBackend` | `put(key, payload) -> ArchiveObject`; `get(key) -> bytes`; `verify(obj) -> bool` | `RcloneCryptBackend` |
| `EventBusPort` | `publish(envelope)` and `replay(event_type=None)` | `SqliteEventOutbox` |
| `HttpTransport` | `get(url) -> HttpResponse` | Injected transport used by collectors and `SafeHttpClient` |
| Evidence role | `Callable[[Snapshot], RoleResult]` | Existing council roles such as `data_verifier` or `technical_flow` |

Implement the smallest interface possible. Keep provider-specific response objects inside the adapter and return the repository's typed contract. Preserve identity, timestamps, hashes, budgets, and failure metadata where the contract requires them.

## Add an evidence role

An evidence role receives one `Snapshot` and returns `RoleResult`:

```python
from advisorai.agents import RoleResult
from advisorai.contracts import Snapshot


def example_role(snapshot: Snapshot) -> RoleResult:
    return RoleResult(
        role="example_role",
        evidence=(),
        dissent=("fixture role has no evidence",),
        unresolved=True,
    )
```

Register the callable in the `EvidenceCouncil` role map and add it to the reviewed role roster only when the mission mode and evidence budget should admit it. A role may return evidence, dissent, and provenance metadata; it cannot create an order, access the OMS, or loosen risk policy. Add tests for missing evidence, future evidence, provider failure, and duplicate/invalid artifact identity.

## Add a collector or provider adapter

`RSSCollector` is a useful existing example. It accepts a `SourceDescriptor` and an injected `HttpTransport`, optionally spools the raw response, rejects non-200 responses, and produces `PointInTimeObservation` values with publication, availability, parser, source-family, origin, grade, and raw-payload hash metadata. Follow the same pattern for a new source:

1. Define the source identity and intended use in configuration.
2. Inject transport or provider clients; do not read global credentials from an arbitrary module.
3. Preserve raw payload/provenance before normalization where the source path requires it.
4. Emit typed observations with aware timestamps and an explicit parser version.
5. Add fixture tests for valid data, malformed data, late data, source disagreement, and fail-closed behavior.
6. Add a runbook if the adapter can make network calls or requires operator credentials.

Venue adapters follow the same boundary but have stricter authority requirements: they must preserve instrument/venue identity, idempotency, acknowledgement ambiguity, and reconciliation behavior. They do not bypass `RiskKernel` or `OrderManager`.

## Add a capability

`CapabilityFoundry` and `CapabilityBroker` model the lifecycle from candidate to registered/active capability. A capability card should identify its version, inputs, outputs, permissions, environment manifest, evidence, and lifecycle state. Use `HermesIsolationRunner` for the builder/sandbox path where applicable. The broker must keep the capability read-only or explicitly permissioned according to its card; generated code cannot self-authorize.

The credential and network allowlists are part of the extension, not deployment trivia. Start with [Credential scopes](../runbooks/credential-scopes.md), [model gateway policy](../runbooks/model-gateway-policy.md), and the [Phase 8 capability evidence](../runbooks/phase8-capability-evidence.md) runbook.

## Registration and tests

There is no global plugin autodiscovery contract. Register an extension through the owning constructor, factory, config roster, or explicit service descriptor. Keep the registration visible and deterministic. Add a test under the owning architectural directory and run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q tests/<owning-area>
uv run ruff check .
```

If the extension changes a contract, risk boundary, gate, or service ownership, update the relevant architecture/decision/runbook documentation in the same change.
