# Phase-8 Hermes capability evidence

This runbook records the local end-to-end proof required for one quarantined
Hermes capability. It uses the existing deterministic RSS parser against a
fixed fixture, so it does not contact an external feed or claim external source
availability.

## Run the drill

From the repository root:

```bash
uv run python scripts/run_phase8_capability_evidence.py \
  --repository-root . \
  --output artifacts/phase8/capability-evidence
```

The drill performs the following sequence:

1. Run the RSS collector twice inside bounded Hermes child processes.
2. Verify identical output hashes, untrusted-content preservation, enforced
   socket/DNS network policy, read-only filesystem policy, and sensitive-environment
   scrubbing.
3. Export a typed `CollectorCandidate` and read-only `CapabilityCard` with
   parser, contract, security, performance, lock, and fixture identities.
4. Persist the lifecycle in SQLite through `active_read`, restart the registry,
   and verify the lifecycle rehydrates unchanged.
5. Expose only `read_source` through `CapabilityBroker`, execute the fixture,
   reject `submit_order`, and reject active-write promotion without human
   approval.

Each report directory is immutable. `latest.json` is only an atomically updated
pointer; prior reports remain available for review.

The read-only guard rejects common Python and `os` file mutation APIs inside
the child. It is an in-process policy boundary, not a container or VM
attestation; the report therefore retains its explicit local-source identity
note and does not claim stronger isolation than was measured.

## Evidence interpretation

`passed: true` and `local_exit_gate_evidence_passed: true` prove this bounded
local lifecycle. They do not create a formal Phase-8 gate record or promote a
capability into a production authority. The report must retain:

- `phase8_gate_decision: "pending"`;
- `phase8_gate_recorded: false` and `phase8_admitted: false`;
- `network_access_attempted: false` for every Hermes child result;
- `filesystem_write_attempted: false` for every Hermes child result;
- `sensitive_path_access_attempted: false` for every Hermes child result;
- `network_required: false`, an empty `secrets_required`, and only
  `read_source` in `allowed_actions`; and
- the fixture-only environment identity note rather than a claimed container
  image attestation.

External source reliability, earlier phase gates, paper operation, and all live
capital controls remain separate. A changed parser, dependency lock, policy, or
fixture requires a fresh report; never edit an old report into a pass.

`LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.`
