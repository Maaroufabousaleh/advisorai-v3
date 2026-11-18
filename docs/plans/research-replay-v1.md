# Research replay v1 — multi-asset time-series momentum

One command replays a sourced, versioned DSL factor against a buy-and-hold
baseline on frozen point-in-time ETF inputs, separately per exposure.

## Reproduce (fixture, in Git + CI)

```bash
# from the omp/research-to-paper-integration worktree
uv sync --group dev
uv run python scripts/run_research_replay.py --label <your-label>
cat var/research-replay/evidence/<your-label>/manifest.json
```

Default snapshot is the synthetic, redistributable fixture
`data/research-replay/snapshots/synthetic-daily-2015-2025-v1`
(seed=20261114 geometric walk; NOT market data). Expected:

| Exposure | Obs | Trades | Net (bps) | Baseline (bps) |
|---|---|---|---|---|
| SPY | 120 | 19 | -7642.91 | 7185.51 |
| TLT | 120 | 19 | -1472.26 | -4819.59 |

## Reproduce (authorized local Yahoo snapshot, not in Git)

The Yahoo snapshot `etf-daily-2015-2025-v1` is retained locally only —
Yahoo's terms prohibit redistribution, so its payloads are not merge
candidates. Keep the local directory, verify its hash, and pass it explicitly:

```bash
sha256sum data/research-replay/snapshots/etf-daily-2015-2025-v1/bars.json
# expect 86183246194fb712…
uv run python scripts/run_research_replay.py --label <your-label> \
  --snapshot data/research-replay/snapshots/etf-daily-2015-2025-v1
```

Expected per-window results (local Yahoo data, real market data):

| Exposure | Window | Obs | Trades | Net (bps) | Baseline (bps) |
|---|---|---|---|---|---|
| SPY | screen 2015–2019 | 49 | 5 | 1176.05 | 4845.06 |
| SPY | validation 2020–2022 | 36 | 3 | -57.12 | 3951.21 |
| SPY | holdout 2023–2025 | 35 | 1 | 5531.91 | 5539.44 |
| SPY | total | 120 | 9 | 6650.84 | 14335.70 |
| TLT | screen 2015–2019 | 49 | 8 | -1616.39 | 2901.22 |
| TLT | validation 2020–2022 | 36 | 3 | 1541.58 | -3206.08 |
| TLT | holdout 2023–2025 | 35 | 7 | -814.96 | -1607.66 |
| TLT | total | 120 | 18 | -889.77 | -1912.53 |

Units: bps of notional (100bps=1pct);
net = Σ(position × realized) − Σ(change × all-in) over month-end cutoffs.
Costs: SPY 4 / TLT 5 bps per unit position change, one shared recorded config
(`research.costs`) across replay, shadow, and Hermes verification.

## Factor identity (corrected)

The formation is a 252-bar return **including** the cutoff bar — a 12-0 span,
not the paper's 12-1 skip-month variant. Model name is `tsmom-252b-sign-v1`.
The hypothesis remains sourced to Moskowitz–Ooi–Pedersen (JFE 2012,
`doi:10.1016/j.jfineco.2011.11.003`), citation-only; the deviation from the
paper's skip-month construction is disclosed here, not hidden in a "12m" label.

## What is proven

- Hypothesis sourced citation-only under `research-source-policy-v4`; lineage
  `paper → hypothesis → factor → experiment → validation` in the registry.
- DSL factor `c2faeb70…` parsed, hashed, and evaluated past-only (252-bar
  formation, 21-bar holding, month-end cutoffs).
- Windows enforced by `require_window_embargo`, not just documented: every
  cutoff falls in a preregistered window, all three windows are non-empty on
  full replays, and consecutive windows are separated by ≥21 days (proven from
  actual cutoffs: 31d and 32d gaps on both snapshots).
- Deterministic `ReplayEngine` ordering with conflict detection; numerics
  reproduce exactly across state dirs.
- Per-exposure independence: one bad exposure is recorded as failure evidence,
  never masks its sibling.

## What is not proven

- No paper-order authority for any market; this is research measurement only.
- Yahoo vendor data is research-grade, not execution-grade; redistribution
  terms apply — payloads stay local, only hashes travel.
- Regime labels are realized-volatility terciles, not the (unimplemented)
  canonical MarketStateArtifact.
- Fixture results are contract checks, not market evidence.
