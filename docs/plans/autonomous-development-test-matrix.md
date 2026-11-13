# Autonomous development test matrix

| Layer | Command | Last result | Evidence |
|---|---|---|---|
| L1 unit/contract | `pytest -q -p no:cacheprovider` | 1286 passed, 48 warnings | 2026-11-12 full run, Phase 8 incl. kata classifier + credential audit |
| L2 phase acceptance | `python scripts/verify_acceptance.py` | 11/11 phases (Phase 5: 46, Phase 7: 46, Phase 8: 31) | 2026-11-12 runner |
| Static | `uv run ruff check .` | clean | per-change |
| Format | `ruff format --check` (owned files) | clean | per-change |
| Lock | `uv lock --check` | clean | per-change |
| Dashboard | `npm run build --prefix dashboard` | success | per-change |
| Secrets | pattern scan of outgoing diff | clean | pre-push |
| L3 offline e2e | `tests/session/test_strong_order_lifecycle.py` | 2 passed | deterministic fixture |
| L4 real public data | approved-source qualification | NOT RUN | needs network + review |
| L5 paper/testnet live | supervised session | NOT RUN | needs credentials + admission |
| L6 stress/recovery | soak + fault injection suites | existing suites pass | `tests/recovery/` |
| L7 independent review | security/scientific/operational | NOT DONE | external reviewers |
