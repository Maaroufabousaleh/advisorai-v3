# EIA authenticated smoke check: trusted runtime only

The implementation is in `advisorai.integrations.eia_trusted_runtime` and
`advisorai.integrations.public_data`. The check makes one read-only EIA v2
request for a single sample value. It returns only provider, status, row count,
and retrieval timestamp. It discards EIA's echoed request metadata.

## Security boundary

- Never invoke this module from a coding agent, Hermes, an LLM, browser agent,
  notebook, CI job, or a process that records URL/query parameters, request
  locals, response bodies, or HTTP spans.
- A future owner-reviewed runtime must have an Infisical machine identity
  scoped to read only `EIA_API_KEY` in the AdvisorAI V3 `dev` environment and
  the exact secret path. Do not grant project administration. No identity is
  configured at present.
- Inject Universal Auth runtime values only through the trusted runtime's
  secret manager. The code accepts no secret argument on the command line and
  never prints the secret. Disable HTTP tracing/auto-instrumentation and SDK
  debug logging. Keep stdout/stderr capture and crash dumps restricted.
- `ADVISORAI_TRUSTED_EIA_SMOKE=1` is an explicit opt-in guard, not proof of
  isolation. The owner/runtime operator must verify the actual deployment
  boundary before using it.
- Use `dev` only; production retrieval is rejected. Do not test the real key in
  development workspaces. The repository's unit tests use synthetic values and
  injected transports only.

## Owner-run command

After the trust boundary, read-only identity, `infisical-python` dependency, and
runtime environment are independently reviewed, launch:

```sh
ADVISORAI_TRUSTED_EIA_SMOKE=1 python -m advisorai.integrations.eia_trusted_runtime
```

The successful output contains only HTTP status, a row count, and timestamp.
Until owner/runtime execution provides that external evidence, authenticated
EIA verification remains pending. The coding agent has not read or tested the
stored credential.
