# Security model and reporting

AdvisorAI V3 handles data, credentials, model/provider boundaries, paper execution state, and operator controls. The implementation is designed to fail closed at several boundaries, but this repository is not a turnkey production deployment and does not make a security certification or live-trading guarantee.

## Implemented controls

- Point-in-time checks reject observations and artifacts unavailable at a snapshot cutoff.
- Parquet artifacts and manifests are content-addressed and verified before reads.
- `LakeQuery` accepts read-only queries and restricts local file access to its configured lake root.
- SQLite ledgers use WAL mode, namespaced events, full synchronous writes, and idempotency keys; conflicting reuse is rejected.
- `EvidenceCouncil` roles return typed evidence and cannot create orders.
- The model gateway validates route identity, privacy class, output schema, budgets, and tool-execution claims.
- Credential values are parsed without sourcing a shell file and are exposed only through one allowlisted `CredentialScope` at a time.
- The risk kernel owns hard-limit approval/reduction/rejection, stale-data behavior, and an independent kill switch; AI cannot loosen limits.
- The OMS requires idempotency and treats ambiguous acknowledgements as reconciliation work rather than a blind retry.
- Dashboard protected mode uses Argon2id password verification, TOTP, short-lived sessions, `HttpOnly`/`SameSite=Strict` cookies, CSRF checks, login throttling, security headers, and step-up tokens for sensitive commands.
- Dashboard commands are typed, confirmed, idempotent, audited, and limited to paper/control-plane operations. The API has no live order or live activation route.

## Deployment limitations

The repository does not provide a process supervisor, TLS termination, certificate management, secret manager, network firewall, production monitoring stack, or public incident-response service. The default dashboard launcher is for local development and synthetic paper state. Operators are responsible for reviewing those controls before any non-local use.

## Workstation threat and release lifecycle

The planning threat model covers local-file disclosure, untrusted or generated
research code, dependency and model supply-chain changes, malicious data/news
payloads, prompt or tool injection, stolen provider credentials, corrupt or
stale state, and unauthorized operating-mode changes. Research and builder
jobs remain untrusted by default and receive no broker, OMS, or RiskKernel
authority. Permission classes are defined independently of sandbox backends
in [ADR 0012](docs/decisions/0012-agent-permission-and-sandbox-boundary.md).

The owner-workstation release and recovery plan requires:

- Keep credentials out of Git, logs, prompts, reports, generated artifacts,
  and research sandboxes. Use narrow purpose-specific scopes, avoid copying
  secrets into child processes, and document creation, ownership, rotation,
  revocation, and incident steps.
- Pin dependencies, model code, checkpoints, provider schemas, and source
  adapters to reviewed identities. Record hashes and license/data-rights
  findings. Produce an SBOM where the existing release workflow supports it;
  do not add a new release platform solely for this plan.
- Review dependency, model, provider-schema, and configuration updates before
  activation. Keep the previous known-good version available for rollback.
- Verify backup integrity and restore from a checkpoint before relying on it.
  Recovery reconciles authoritative state before resuming work; uncertain
  state fails closed.
- Record security incidents and operator decisions without copying secret
  values. Revoke or rotate exposed credentials at the provider and preserve
  only redacted evidence.

For Integrated Alpha, record exact code/config/schema/provider/model identities,
confirm secrets are scoped and redacted, exercise checkpoint integrity,
backup/restore and configuration rollback, and verify that recovery cannot
escalate authority. These are planned workstation controls, not a claim that a
central secret manager, signed release process, production monitoring service,
or incident-response service is supplied. This owner-operated baseline follows
the risk-based practices in [NIST SSDF](https://csrc.nist.gov/projects/ssdf)
and [OWASP Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet).

Phase gates are operational controls, not security theater: installed dependencies, passing tests, or a configured endpoint do not establish provider trust, timed soak evidence, human approval, or live readiness. See [Project status](docs/concepts/status.md) and [Transition configuration](docs/getting-started/configuration.md).

## Reporting a vulnerability

No public security contact is published in this checkout. Please do not disclose credentials, private provider responses, or exploitable details in a public issue. Until a maintainer-provided private channel is documented, use the repository's established private maintainer contact or hosting-provider private disclosure mechanism and include only the minimum reproducible detail.

If credentials or secrets are accidentally committed locally, revoke/rotate them immediately, preserve the evidence needed for the maintainer, and avoid copying the values into tickets, logs, or documentation. The ignored `secrets.env` file and local state roots are not documentation material.
