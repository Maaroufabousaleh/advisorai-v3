# Provider credential isolation — trusted runtime note

The coding-agent process and the trusted AdvisorAI runtime are different
trust domains even when they share a host OS identity.

## Rule

- Adapters declare the single `CredentialScope` they need (both sandbox
  venues use `PAPER_VENUE`; see `docs/plans/provider-onboarding-manifest.md`).
- `CredentialResolver.resolve(scope)` is the only credential path. It
  exposes one scope and has no method returning the full mapping.
- Tests construct resolvers from `CredentialResolver.from_mapping` with
  non-secret fixtures (`test-key`, sandbox host constants). No test asserts
  on, logs, or transmits a real credential.
- The owner runtime receives real credentials only from the trusted
  environment or a vault-backed resolver implementing the same
  `resolve(scope)` interface. The coding agent MUST NOT inherit production
  or provider secrets in its own process environment.

## Why same-UID isolation is insufficient

On a single-user workstation the agent shell and the owner process run as
the same OS user: file permissions and environment scrubbing raise the bar
but do not stop a privileged peer process from reading another process's
memory or environment. Treat the build/test agent as untrusted with respect
to provider secrets:

- Never export real `ADVISORAI_VENUE_API_SECRET` / passphrase values in a
  shell used for code editing, test runs, or log collection.
- If a vault is configured, only the launched owner runtime (separate
  service identity where the platform allows it) resolves the vault-backed
  scope. The agent verifies the *interface* (refusal without credentials,
  single-scope resolution, redaction in logs) with fixtures.
- `SafeHttpClient` redacts configured secret values from logs and errors;
  dashboard APIs never expose credential variables.

## Verification

- `tests/security/test_credential_scopes.py` audits import boundaries and
  the reviewed consumer set.
- Provider tests inject stub transports; no test performs network I/O and
  no fixture contains a real credential.
- A vault-backed resolver, when Luna provisions one, must pass the same
  scope-confinement tests before admission.
