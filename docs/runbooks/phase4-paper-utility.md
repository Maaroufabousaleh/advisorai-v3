# Phase-4 paper utility evidence

The Phase-4 utility entrypoint is an offline, measurement-only boundary:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/run_phase4_paper_utility.py \
  --input <typed-phase4-input.json> \
  --phase3-gate-record <passed-phase3-gate-record.json> \
  --output-root artifacts/phase4/utility-evaluation/<new-run-id>
```

The input envelope must use
`advisorai.phase4.paper-utility-input.v1` and contain only typed
`Phase4MarketObservation` and `Phase4Prediction` arrays. Every observation
must carry `phase3_admitted=true`. The gate file must validate as a passed,
currently valid `PhaseGateRecord` for Phase 3; a Phase-3 review recommendation
or a pending record is intentionally rejected.

The command performs no network calls, credential loading, model-weight
loading, promotion, gate recording, or execution. It writes one immutable
`phase4-paper-utility-evidence.json` plus its SHA-256 sidecar. The report is
`measured_pending_review`; `phase4_admission_opened` remains false. The report
records the input and gate file hashes, the gate canonical hash, baseline and
candidate utility results, and the unchanged RiskKernel/OMS authority
boundary. A real Phase-3 gate and real admitted paper observations are still
required before this command can produce operational Phase-4 evidence.
