# Phase-4 release-candidate PR traceability

This PR is a draft release candidate based on current main. It does not merge
or launch anything. V3B is qualified canary evidence only; Phase-4 admission,
the long-run preregistration, and live capital remain blocked.

| Source PR | Function retained | Verification | Evidence boundary |
| --- | --- | --- | --- |
| #190 | Sealed source raw/normalized integrity auditor, exclusion overlay, chronological terminal audit, and outcome-link boundaries | `tests/models/test_phase4_v3core_integrity.py`, forward materialization and terminal-workflow tests | Historical sealed roots remain immutable; no evidence is copied or rewritten |
| #193 | Readiness fingerprint validation and identity-bound report contracts | readiness fingerprint tests plus this branch's dual-lock checks | The dry-run report is not an immutable long-run preregistration |
| #199 | Corrected Chronos/finality qualification lineage | candidate, Chronos, and V3B reference tests | Qualified model/checkpoint identity is pinned; no model is loaded here |
| #200 | Prospective canary finality, context, ledger, and evidence-class boundaries | canary/finality tests | `PROSPECTIVE_CANARY_ONLY` remains admission-ineligible |
| #201 | Deterministic warm-up and scheduler contract | warm-up and scheduler tests | First cutoff is calculated from a future start; no start time is selected |
| #202 | Atomic watchdog monitoring, fatal latching, and chronological replay repair | watchdog, replay, and terminal-audit tests | Fatal history remains append-only and absorbing |

The release candidate adds the 80-opportunity/64-clean-case accounting,
fresh-run first-cutoff formula, bounded one-attempt recovery policy, dual
runtime-lock identity contract, and no-launch dry-run preflight.

PR #192 remains post-seal baseline regeneration tooling. PR #195 remains the
shutdown/readiness operational gate. They are deliberately not part of the
critical prospective acquisition contract.

No OpenCode/venue/broker/credential/order path is changed. No Phase-4 process,
GPU workload, or protected historical evidence is touched.
