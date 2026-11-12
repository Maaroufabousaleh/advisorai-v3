# Autonomous development backlog — machine-readable work queue

Status values: IMPLEMENTED / PARTIAL / MISSING / EXTERNALLY BLOCKED.
Admission is tracked separately and never implied by implementation.

| ID | Work package | Owner | Files | Tests | Status | Next action |
|---|---|---|---|---|---|---|
| A-runtime | App entry + supervision | session | `src/advisorai/app.py`, `src/advisorai/session/` | `tests/session/` | IMPLEMENTED | maintain |
| D-authority | Proposal-bound targets | api | `src/advisorai/api/service.py` | `tests/decision/test_authority_boundary.py` | IMPLEMENTED | maintain |
| E-registry | Research registry | research | `src/advisorai/research/registry.py` | `tests/research/test_registry_dsl_jobs.py` | IMPLEMENTED | expose via API |
| E-inspect | Registry inspection API + UI | api/dashboard | `src/advisorai/api/dashboard.py`, `dashboard/src/` | new | MISSING | **build now** |
| E-retrieval | FTS retrieval over registry | research | new `src/advisorai/research/retrieval.py` | new | MISSING | build after E-inspect |
| I-research-ui | Research browser views | dashboard | `dashboard/src/` | build | MISSING | build with E-inspect |
| G-replay | Nautilus replay qualification | execution | `src/advisorai/execution/nautilus.py` | Phase 0 gate | EXTERNALLY BLOCKED | needs pinned runtime + gate |
| H-sandbox | Kata/gVisor attestation | capabilities | `src/advisorai/capabilities/sandbox.py` | host tests | EXTERNALLY BLOCKED | needs host hardware (PR #207) |
| B-sources | New source adapters | collectors | per-source | qualification | EXTERNALLY BLOCKED | needs rights + review per source |
| C-models | Challenger qualification | models/research | `challengers/` | prospective | EXTERNALLY BLOCKED | needs elapsed market time |
| J-live | Phase 10 approval | governance | `docs/plans/phase-10*` | human gate | EXTERNALLY BLOCKED | owner decision, never autonomous |
