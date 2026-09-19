# Kata Containers isolation workstream

This runbook defines the governed high-isolation boundary for untrusted
research, generated code, imported capabilities, browser artifacts, and native
dependencies. It is a separate security workstream. It does not change the
Phase-4 Chronos release, r4 evidence, the future r5 contract, PR #206, or
TimesFM PR #205.

## Host feasibility measured on 2026-09-19

The read-only probe was run on the WSL2 host before any implementation or
installation change:

| Capability | Observation |
| --- | --- |
| Virtualization backend | WSL2 on Microsoft Hyper-V; KVM/QEMU path |
| `vmx` / nested virtualization | Present; `kvm_intel` nested=`Y` |
| `/dev/kvm` | Present (`root:kvm`, `0660`) |
| `/dev/mshv` | Absent |
| `/dev/vhost-vsock` | Present |
| `/dev/vhost-net` | Present |
| Docker | Available; server 29.8.1 |
| containerd | Binary available; Docker daemon uses containerd |
| Kata runtime | Not installed or registered with Docker |
| KATA_HOST_FEASIBLE | `FALSE` for the current host |

The KVM, nested-virtualization, vsock, and container runtime prerequisites are
present, but Kata cannot be operationally attested until a Kata runtime is
installed and registered. No Windows virtualization setting or package was
changed. The absence of `/dev/mshv` is not itself a blocker for the intended
KVM-backed path.

Run the read-only report with:

```bash
uv run python scripts/probe_kata_sandbox.py \
  --output /tmp/advisorai-kata-host-feasibility.json
```

The report records Kata and hardened-Docker measurements as `not_run` when no
reviewed equivalent hostile workload has been executed. It never fabricates
isolation evidence.

## Trust model

| Trust class | Workloads | Required treatment |
| --- | --- | --- |
| `TRUSTED_CORE` | market-node, RiskKernel, OMS | Never launched by this backend |
| `TRUSTED_RESEARCH` | reviewed quant worker | Existing policy may choose native or Docker |
| `UNTRUSTED_RESEARCH` | Hermes, browser artifacts/code | Prefer Kata; Docker only with named policy authorization |
| `UNTRUSTED_NATIVE` | generated Python/native code, imported capability, unknown third-party tool | Kata required; unavailable means fail closed |
| `BLOCKED_EXECUTION` | prohibited workload | Refuse execution |

Kata success is containment evidence only. It never grants order, risk, OMS,
credential, live-repository, deployment, or self-promotion authority. The
capability lifecycle still ends automatic promotion at `active_read`.

## Mission contract

`advisorai.capabilities.sandbox` provides typed `SandboxMission`,
`SandboxPolicy`, `SandboxBackend`, `KataSandboxBackend`, and
`HardenedDockerSandboxBackend` models. A mission requires:

- an immutable image and rootfs digest;
- one task root and one declared output directory;
- explicit read-only input and dataset mounts;
- CPU, memory, PID, output, wall-time, and GPU policy;
- default-deny network policy;
- name-only secret references, if a separately authorized materializer exists;
- AdvisorAI commit, dependency lock, capability, dataset/model, and input
  artifact identities.

The rendered container command uses `--pull=never`, read-only rootfs, dropped
capabilities, `no-new-privileges`, a private IPC namespace, a non-root UID,
PID/memory/CPU limits, a bounded tmpfs, and no host repository, home, `/mnt/c`,
Docker socket, containerd socket, SSH agent, or root mount. Only input/data
mounts and the task output bind are rendered. The host process environment is a
small explicit allowlist; credentials are not inherited.

The output bind is the quarantine boundary. It is not automatically promoted
into source, configuration, RiskPolicy, OMS state, the ledger, or deployment
artifacts. Promotion requires schema, malware/file-type, contract, security,
reproducibility, independent evaluation, and human review.

## Network and secrets

`NetworkPolicy()` is `NONE`. An allowlist requires explicit hostnames, a named
egress enforcer, destination classes, and a policy hash. Wildcards, localhost,
link-local metadata, private/reserved/multicast IPs, and `.local` destinations
are rejected. Broker destinations require a separately declared read-only
research class; no execution credentials are implied.

Secrets are absent by default. The backend accepts only name/purpose/
destination/lifetime metadata and never a value. A task-scoped materializer,
when separately reviewed and authorized, may provide individual read-only files
under the mission's secret-staging directory. `secrets.env`, `.env`, credential
bundles, broker/execution/order/trade/withdrawal/transfer/private-key references
are rejected.

## Lifecycle and fallback

```text
gap -> scout -> pin -> inspect -> sandbox -> wrap/build -> contract tests
     -> adversarial/security tests -> performance benchmark -> shadow
     -> active-read -> active-write-limited -> deprecated
```

`UNTRUSTED_NATIVE` never falls back to Docker. `UNTRUSTED_RESEARCH` may use
`HardenedDockerSandboxBackend` only when `allow_docker_fallback` and a named
authorization reference are both present. The selection result records the
requested backend, selected backend, downgrade reason, and policy authorization.

## Provenance and evidence

Every prepared/completed mission carries backend, Kata/runtime-rs/hypervisor/
guest-kernel identities when attested, guest image/rootfs/overlay identities,
AdvisorAI and lock identities, capability and dataset/model revisions, input
and output hashes, resource limits, network-policy hash, secret-reference names,
timestamps, exit reason, resource usage, and fixed false security authority
flags. Secret values and raw process output are not persisted.

`SandboxProcessRunner` terminates the process group on wall-time/output-budget
violation, hashes sanitized stdout/stderr, releases an injected GPU lease in a
`finally` path, and requires no process to remain after cleanup. The default
GPU policy is false; enabling GPU requires a separate reviewed capability.

The bounded hostile workload contract includes filesystem, socket, mount,
capability/namespace, LAN/broker network, timeout/PID/memory, mission-crossing,
order/RiskPolicy/OMS, self-promotion, and native-extension probes. The current
host has not run the equivalent workload under either backend in this workstream:
Kata is unavailable, and no new Docker comparison is claimed. Existing Phase-8
Docker evidence remains a separate, narrower measurement and
`phase8_admitted` remains `FALSE`.
