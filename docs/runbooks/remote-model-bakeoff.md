# Phase 0 remote-model bake-off

This workstream measures remote text models only through the accepted
`PolicyGateway`. It does not replace or weaken the gateway, and it never gives
a model access to broker, order, portfolio, risk, reconciliation, or execution
tools.

## Route roles

| Role | Permitted data | Admission rule |
| --- | --- | --- |
| `CONTRIBUTOR_PUBLIC` | Public or synthetic text | A dynamic free router is recorded as non-reproducible unless a frozen endpoint admission exists. |
| `PRIVATE_WORKER` | Sanitized internal research | Exact provider selector, model alias, resolved model, endpoint proof, ZDR/no-training, no fallback, and billed-cost admission. |
| `PRIVATE_REVIEWER` | Minimal confidential synthesis | The same private controls, with a stronger reviewed endpoint and portfolio-influencing requests routed here. |
| `BLOCKED_EXECUTION` | Secrets, orders, balances, positions, risk and broker actions | Deterministic systems only; no LLM route exists. |

The caller supplies a task classification and invocation mode, not a provider
identity. Exact route profiles are built from a live, sanitized OpenRouter
inventory artifact. Provider display names (for example `Novita`) remain
separate from selector slugs (for example `novita`), and a resolved endpoint
model is never inferred from either one.

## Running a bounded probe

The acquisition script reads `secrets.env` without sourcing it and requests
only the `DIRECT_LLM` credential scope. It writes no credentials or raw
provider errors:

```bash
uv run python scripts/run_remote_model_bakeoff.py \
  --secrets /home/maaro/.config/advisorai-v3/secrets.env
```

The spend budget is:

```text
min(USD 0.25, 25% of the read-only provider limit remaining)
```

Every dispatch also receives the Phase-0 per-call ceiling of USD 0.001,
256 output tokens, two same-route attempts, and a 30-second total deadline.
The script reserves that ceiling before dispatch and records actual billed
cost from `usage.cost` separately.

## Current evidence checkpoint

The latest live inventory was collected on 2026-08-08. It admitted these exact
private candidates for probing:

* `novita` / `inclusionai/ling-2.6-flash`, resolved
  `inclusionai/ling-2.6-flash-20260421`;
* `coreweave/fp4` / `openai/gpt-oss-20b`, resolved
  `openai/gpt-oss-20b`;
* `digitalocean` / `deepseek/deepseek-v4-flash`, resolved
  `deepseek/deepseek-v4-flash-20260423`.

`openrouter/free` is retained as a public contributor candidate but remains
blocked from reproducible admission because its provider and model pool change
between requests. This is an identity/reproducibility decision, not a quality
claim.

The latest bounded run is
`20260808T000627.596582Z-35412c8a` (report SHA
`a5b0aa59ef3f5c2ebc4a43abcf52b9b1aa56a77aa739821c1cccfc12f6d7f200`, inventory
SHA `d9104650657d2f964dbb5dffbf9eff5ae3ac7ac159ec1720a019f4c868798ef9`). It
measured Ling/Novita structured, tool-optional, and tool-required calls at
`$0.00000243`, `$0.00000301`, and `$0.00000315`; the required-tool response
contained a validated `read_evidence` call and remained
`tool_execution_status=not_executed`. DeepSeek/DigitalOcean produced one valid
structured response at `$0.000021504`; its tool probes and all GPT-OSS/CoreWeave
probes were recorded as gateway abstentions without actual identities. These
are short reliability observations, not quality or production-selection claims.

Run-scoped sanitized evidence is written below:

```text
artifacts/phase0/remote-model-bakeoff/<run-id>/inventory.json
artifacts/phase0/remote-model-bakeoff/<run-id>/remote-model-bakeoff.json
configs/models/phase0_remote_roster.json
```

The current sanitized report and inventory hashes are stored in that roster.
The run billed `$0.000030094` and reserved USD 0.009 of the USD 0.25/remaining-
balance cap; no route is selected for production from this short evidence alone.

Reports contain requested and observed identities, endpoint-selection proof,
latency, token counts, billed cost, safe failure classes, and whether a tool
was called. They explicitly record `tool_execution_status=not_executed`; a
gateway probe never claims that a deterministic evidence tool ran.

## Exact-route stability window

The short bake-off is not a 24-hour route gate. The resumable stability runner
uses the same scoped credential resolver and `PolicyGateway`, freezes the live
inventory hash and exact provider/model/endpoint identity in its run directory,
and sends only the structured synthetic probe. It does not use `openrouter/free`
or any fallback route.

```bash
setsid --fork ./.venv/bin/python scripts/run_remote_route_stability.py \
  --secrets /home/maaro/.config/advisorai-v3/secrets.env \
  --roster configs/models/phase0_remote_roster.json \
  --run-directory artifacts/phase0/remote-route-stability/<run-id> \
  --candidate private-deepseek-digitalocean \
  --duration-hours 24 \
  --interval-seconds 600 \
  > artifacts/phase0/remote-route-stability/<run-id>/runner.nohup.log 2>&1 < /dev/null &
```

`setsid --fork` is required on the WSL host when the invoking terminal wrapper
cleans up ordinary background process groups. Capture the returned PID and
verify that the runner has its own session plus a live `status.json` heartbeat;
the evidence root and status PID are authoritative for resumption.

Each run contains `config.json`, its write-once `config.sha256` sidecar,
`inventory.json`, hash-chained `cycles.jsonl`, `status.json`, `summary.json`,
and a PID in `runner.lock`. The first sample binds the config hash; the config
also records the implementation hash and exact command. `status.json` is a
heartbeat, not an admission record. A route can become
`failed` from one provider identity/rate-limit failure; preserve that run and
start another immutable root only when the reviewed route is expected to be
available again. Never concatenate failed samples into the new root.

The Novita trial at
`artifacts/phase0/remote-route-stability/20260809T162800Z` recorded one valid
sample followed by an upstream shared-pool HTTP 429. It is quarantined; its
incident report has SHA-256
`825e78c3cf416df52ddd1e7b51b4df7801c6bde3adee08149158602ff183a9d6`.

The earlier DigitalOcean trial at
`artifacts/phase0/remote-route-stability/20260809T171000Z` was quarantined
because its runner did not bind samples to immutable configuration/code
attestation. The first post-fix smoke at
`artifacts/phase0/remote-route-stability/20260809T173059.039176Z` was also
quarantined because its config metadata retained the old schema label. Their
incident records have SHA-256 values
`302220c0b2be692de953848d7cf2b8058baceb271581a776f52f82c3d13f8677` and
`a3f8a51aeb5a437b1dd5c570cf86ce2cc4eb47b86e108055fcbf0b0ae34a9f8e`. The
the 20260809T173237.710604Z DigitalOcean window recorded three upstream
shared-pool HTTP 429 gateway abstentions and is quarantined. Its incident
report SHA-256 is
`f58eee4632a644655d6f9edd563091740799beec40d3f1048394d6d5541410ea`.
The replacement root
`artifacts/phase0/remote-route-stability/20260810T034500Z` recorded 11 passing
cycles before an upstream shared-pool HTTP 429 and is quarantined. Its
incident SHA-256 is
`805d763d69841515f7beb676ec2a0dea2e2043106dbb4dbc43b292bff4350e9f`.
The runner now stops immediately after a failed sample; the corrected
systemd-backed root
`artifacts/phase0/remote-route-stability/20260810T053600Z` stopped after its
first sanitized `deadline_exhausted` gateway abstention and is quarantined.
Its incident SHA-256 is
`5b6d5ffe9133811a664f24151b95fcd850f130cff718bc6ed1eae9289178cff1`.
No route window is currently active. Retry only when the reviewed provider
route is available again, using a fresh root; never concatenate failed
samples.

## Admission and selection

A successful probe is not production approval. A route remains a candidate
until it passes repeated quality, reliability, privacy, cost, and latency
benchmarks. Endpoint drift, missing metadata, unsupported invocation modes,
429/503 exhaustion, missing billed cost, or unknown identity causes a truthful
failure/abstention record. There is no fallback from a private route to the
public contributor pool.

Remote results must be interpreted together with the local model roster and
the Phase-0 stability evidence. Live-capital deployment remains explicitly
not approved.
