# Implementation status and gate evidence

This record distinguishes implementation coverage from an architecture gate that
requires external, time-based evidence. A green unit test does not claim a 24-hour
or 60-day operational gate.

| Phase | Implementation | Automated evidence | Gate status |
|---|---|---|---|
| 0 | Harness, ports, direct/LiteLLM/OmniRoute typed adapters, candidate inventory, short probes, stability evaluator, and durable Phase-0 gate records | `tests/phase0`, gateway/port tests | Pending pinned external runtimes and 24-hour measurements |
| 1 | Contracts, PIT lake, DuckDB/Polars query, ledgers, typed V3-Core YAML admission, config rollback, resources, traces, durable flows/memory/incidents, and explicit service ownership/mode boundaries | contracts/data/config/recovery/resource/orchestration/memory/service tests | Local gate fixtures pass; operational config rollback evidence remains required |
| 2 | Paper event spool/replay, account and margin/borrow/FX/corporate-action accounting, durable-first account/OMS retries, signed target constraints, combined-state-hash RiskKernel/OMS binding, paper/native testnet boundary, venue-projection reconciliation, TCA | `tests/execution` | Paper failure fixtures pass; Nautilus runtime admission remains Phase 0 governed |
| 3 | Native/Deribit/RSS/GDELT/official-vintage parsers, origin/revision/availability, quality monitor | `tests/data` | Parser/lineage fixtures pass; source availability dashboards need live soak |
| 4 | Naive/statistical/LightGBM boundary, calibration/abstention, optional model quarantine, GPU lease, and evidence-bound model lifecycle promotion | `tests/models` | Deterministic fixtures pass; named model bake-offs remain pending |
| 5 | Router, typed roles, bounded adaptive waves, evidence graph, independence gates, DecisionBundle and expiry/cutoff binding | `tests/agents`, `tests/api` | Correlated-evidence and target-only boundaries pass |
| 6 | Portfolio comparisons, risk analytics/stress, validation, attribution incidents | `tests/institutional` | Deterministic controls pass |
| 7 | Soak records/gate, incident-ledger rebuild, and immutable recovery rebuild | `tests/recovery/test_soak.py` | Requires actual 60-day paper operation and restore drills |
| 8 | Hermes policy, bounded isolation runner, sensitive-environment scrubbing, artifact/capability lifecycle and broker | `tests/capabilities` | Sandbox integration and review evidence still required |
| 9 | Vintaged official releases, equity corporate-action/daily-council boundary, challenger registry, browser ladder, archive verification | `tests/expansion` | Requires one-at-a-time live data/challenger evidence |
| 10 | Human approval, bounded live readiness, AI-offline invariant, order guard | `tests/live` | Must remain closed until Phase 7 and explicit human approval pass |

The current repository therefore has broad executable coverage, but does not claim
Phase 0, Phase 7, or Phase 10 gates without the external evidence explicitly named
above.

Latest local verification (2026-08-05):
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python scripts/verify_acceptance.py`
passed all eleven phase suites, with 233 tests distributed as
Phase 0/1/2/3/4/5/6/7/8/9/10 = 17/76/52/16/15/17/10/5/9/11/5. A single-process
`pytest -q tests` also passes all 233 tests. The acceptance runner stops at the
first failed phase, so later suites are never counted as evidence after an
earlier gate failure. The Phase 0 inventory was regenerated at
`artifacts/phase0/availability.json` (ignored runtime output), and remains an
availability record rather than an admission decision. Final local static and
reproducibility checks also pass: `./.venv/bin/ruff check .`; `./.venv/bin/ruff
format --check .` (154 files); `UV_CACHE_DIR=/tmp/advisorai-uv-cache
/home/maaro/.local/bin/uv lock --check`; `./.venv/bin/python -m compileall -q src
tests scripts`; and `pytest --collect-only -q tests` (233 collected).
