# External Hermes runtime review

This runbook records the distinction between AdvisorAI's repository Hermes
policy harness and the upstream Hermes Agent package. The upstream runtime is
reviewed only in a disposable environment. It never becomes an AdvisorAI
dependency and receives no broker, order, OMS, execution, withdrawal, or
production-write capability.

## Pinned review

The 2026-08-09 review pinned the upstream tag `v2026.8.3` to commit
`3c27eb6234bf91b8ceee9e9071591b31e9b148cb` and installed package version
`0.20.0` under `/tmp/advisorai-hermes-review.MK7wSX`. The AdvisorAI repository
was not modified by the installation.

The immutable report is:

`artifacts/phase0/external-hermes-review/20260809T162031Z/external-hermes-review.json`

Report SHA-256:

`2fcfe86c151bffe2f4c59af0f7e0e029005a4ad94675c47fc3c18348a151b51c`

The task used one synthetic loopback OpenAI-compatible endpoint, the
`delegation` toolset, a four-turn limit, and one leaf subagent. A dummy key was
used only to satisfy the client interface. No AdvisorAI secret was mounted.
The task exited successfully and measured a maximum resident set size of
126,508 KiB.

## OS namespace result

The task was executed inside WSL2 using:

```text
unshare --user --map-root-user --mount --net --pid --fork --mount-proc
```

The loopback interface was explicitly enabled for the local synthetic endpoint;
a non-loopback connection failed closed with `errno 101`. The probe therefore
demonstrates user, PID, mount, and network namespace behavior for this host.
It does not attest filesystem restriction, seccomp, direct native syscall
containment, or C-extension containment: the mounted `/mnt/c` path remained
writable from the namespace. Formal Phase-8 admission remains closed.

## Reproduction and interpretation

Reproduce only in a new disposable directory, with a synthetic local endpoint
and no credential files. Pin the exact upstream revision, inspect its entry
points and toolsets, bound turns and delegation depth, measure RSS/latency,
and preserve the complete command/output hashes. Destroy or quarantine the
environment after the review.

The report is external-runtime evidence for Phase 0, not real provider-route
evidence and not formal Hermes or Phase-8 admission. A future formal admission
requires the earlier phase gates and a host boundary that attests the required
native/process/filesystem/credential restrictions.

## Local Docker boundary measurement

On 2026-08-10, the host-supported Docker runtime was measured independently of
the pinned Hermes review with:

```text
scripts/probe_phase8_os_sandbox.py --evidence-dir artifacts/phase8/os-sandbox-probe
```

The immutable report is
`artifacts/phase8/os-sandbox-probe/20260810T050947.907604Z/phase8-os-sandbox-probe.json`
with SHA-256
`1671cd03a821a5751ff046d3732c009cb5a727b6b59d8e1bc89dc829196a7b1a`.
The probe used a pre-existing local Alpine image with no pull, the explicit
local Docker socket, no repository or credential mounts, Docker `--network
none`, a root-identity read-only root, a constrained writable tmpfs, all
capabilities dropped, `no-new-privileges`, and CPU/memory/PID ceilings. It
measured zero external network calls, denied root filesystem writes, allowed
only the declared tmpfs write, reported zero effective capabilities, denied
the bounded unshare/mount escape probes, and allowed a bounded child shell.

This is real host-boundary evidence, but not formal Hermes admission. The
report explicitly leaves universal native syscall containment, C-extension containment,
credential isolation, production-tree isolation, and a real Hermes capability
task as `not_attested`; the sandbox remains quarantined until the earlier phase
gates and the remaining admission evidence are complete.

`LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.`
