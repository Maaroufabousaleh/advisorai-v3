# BTCUSDT Spot PAPER-V1 Candidate — PROPOSED, NOT APPROVED

Status: **proposal for review. No approval. No order authority. No order
submission.** Testnet is used for order-handling and reconciliation
evidence only (P02/P03), never as strategy-performance evidence. Fixture or
Testnet prices are never performance evidence.

## Profile

- Instrument: BTCUSDT spot (long/flat only; spot has no shorts).
- Execution venue (when/if approved later): Binance Spot Testnet, fake funds.
- Research input: Binance public 1d spot archive snapshot, qualified by
  `scripts/run_btc_input_qualification.py`. Input snapshot:
  `bars_sha256=1ee07b5b24a5640da47e8be89964daf63a9bee86e18e8488fbf7136a10e53dce`,
  3332 gapless UTC-day bars 2017-08-17..2026-09-30, verdict QUALIFIED as of
  2026-11-17. No evaluation was run before this hash was recorded.

## Frozen signal (v1, long/flat daily time-series momentum)

- Formation: 63 closes (~3 months). Holding: position re-evaluated daily.
- Position for day T: `+1` if `close[T-1] > close[T-1-63]`, else `0`
  (flat on equal/lower; never short).
- Decision timing: decide at the 00:00 UTC close using only bars with
  `close_time < decision time`; execute at the next (T+1) open.
- Rationale recorded before any evaluation: daily analogue of the
  researched monthly 12x/21d momentum leg, adapted to 24/7 spot with no
  corporate actions. If it fails, record REJECT and propose another
  candidate; do not re-tune these numbers on viewed data.

## Frozen costs, benchmarks, windows

- Costs: 20 bps all-in per unit position change (10 bps per side, taker
  assumption). Buy-and-hold pays one entry only. Assumption, not measured.
- Benchmarks on identical bars and timing: cash (0 bps) and buy-and-hold
  (enter first T+1 open, hold to last close).
- Warmup: first 63 bars of any window are formation-only, never scored.
- Historical characterization (viewed data, NOT pass/fail evidence): full
  qualified snapshot minus warmup, reported for plumbing only.
- Forward shadow (the only pass/fail evidence): accrues from the freeze
  date recorded below. A complete forward evaluation needs at least 63
  post-freeze bars before the first decision plus a 21-bar holding tail.

## Pass/fail (forward shadow only)

PASS iff, after identical costs: forward `net_bps > 0` AND forward
`net_bps > buy-and-hold net_bps`, with zero data-integrity violations and
turnover within 3x of the historical plumbing expectation. Otherwise
REJECT — a rejected candidate is a valid result. Always report: net vs
benchmarks, drawdown, turnover/trades, per-year breakdown, cost
sensitivity (±10 bps grid), abstention/quarantine counts.

## Independent replay + forward-shadow protocol (no order authority)

1. Three implementations must agree exactly (net, trades, snapshot hash):
   `replay.py` on a BTCUSDT exposure, a Hermes-style mirror, and a
   plain-loop evaluator. Wire-up is a later task; acceptance is bitwise
   agreement on the qualified snapshot, not a preferred number.
2. Daily shadow log per cutoff: date, signal, position, bars hash, model
   id (`btc-tsmom-63d-longflat-v1`), costs applied. Missing, stale, or
   unqualified bars → abstain and quarantine; never carry a stale signal.
3. Shadow writes decision logs only, to an out-of-tree state dir. No OMS,
   RiskKernel, venue, or key contact. Order submission requires a separate
   explicit approval that this document does not grant.
4. Already-viewed data stays labeled historical: no re-labeling a viewed
   window as unseen, no tuning on the forward window while it accrues.

## Evidence still needed (do not pass P06 before these exist)

- [x] Qualified BTCUSDT snapshot hash recorded (`1ee07b5b…10e53dce`, QUALIFIED 2026-11-17).
- [ ] 64 closes after the snapshot end accrued for the first forward decision (47/64 as of 2026-11-16; first signal 2026-12-04).
- [x] Independent replay wired for BTCUSDT (`btc_replay.py`, three-way agreement at 0.01 bps on the pinned snapshot; plumbing only, no verdict).
- [ ] Forward shadow log started at the freeze date below.

Freeze date: 2026-11-17 (input hash recorded above; forward clock starts here).

## Amendment 1 (2026-11-17, before any forward results exist)

Original text above is preserved; where this amendment conflicts, it governs.
No forward window has been inspected: the only numbers seen are the
pinned-snapshot plumbing totals in the handoff, which carry no verdict.

- Cost per position change: fee 10 + spread 1 + slippage 9 = **20 bps
  all-in** per unit change (taker schedule, liquid spread, slippage buffer).
  Buy-and-hold pays one entry leg. Pinned in `btc_replay.py`.
- Realized leg: entry at the next (T+1) open, exit at the next close,
  open-to-close in bps. Position for day T uses only closes from
  post-freeze bars (no pre-freeze formation context).
- First eligible signal: **2027-01-20T00:00:00Z** (close of the Jan-19 bar,
  the 64th post-freeze close). First fill: **2027-01-20T00:00:00Z**
  (T+1 open on the daily boundary).
- Exact scoring window: the first **181** scored decisions,
  **2027-01-20T00:00:00Z .. 2027-07-19T00:00:00Z**.
- Numerical turnover cap: **60** unit position changes over the window
  (~a rebalance every 3rd day worst case). Breach → REJECT on protocol
  grounds (churn regime), regardless of net.
- Correction: the "21-bar holding tail" sentence in the windows section is
  superseded — holding is 1 day by daily re-evaluation; it was
  monthly-thinking residue, not a measured parameter.
- Replay wire-up: the three-way agreement is implemented in
  `src/advisorai/research/btc_replay.py` (no shared-map changes, so the
  SPY/TLT cost contract is untouched). A `replay.py` BTCUSDT exposure is
  deferred until the BTC profile is admitted, and needs its own contract
  review for the shared costs map.

## Amendment 2 (2026-11-17, before any forward scoring)

Amendment 1 is preserved; where this conflicts, it governs. Still no
forward results inspected: no signal or return has been computed on any
bar completing after 2026-09-30. Only the pinned-snapshot plumbing totals
below were ever produced, and they carry no verdict.

- Continuous holdings replace per-day legs. The position for day j is
  decided at the close of day j-1 and fills at the open of day j (the
  fill price is observable after the signal — this is the simulated fill).
  Entry day scores open-to-close of the fill day; held days score
  close-to-close; exit day scores 0 from the exit fill. Overnight gaps
  are never attributed (fill prices only, disclosed).
- True buy-and-hold: enter the first scored open, hold to the last close,
  one entry leg, no rebalancing, no dividends (spot). Cash is 0.
- Actual turnover costs: 20 bps × |position change| on every fill day,
  fee 10 + spread 1 + slippage 9 per unit.
- First-signal correction: Amendment 1 started the 64-bar count at the
  freeze date, wrongly excluding prospectively collected October–November
  bars that were unseen at freeze. The forward frontier at freeze is the
  snapshot end (2026-09-30); eligible closes are bars completing after it.
  The 64th such close is the Dec-3 bar, so the first eligible signal is
  **2026-12-04T00:00:00Z** and the first fill prints at the same stamp.
- Scoring window correction: the first **181** scored decisions,
  **2026-12-04T00:00:00Z .. 2027-06-02T00:00:00Z**. Turnover cap **60**
  stands.
- Formation context may include qualified historical bars (disclosed
  history, same as replay warmup); only decision days must be forward
  (on/after the first-signal date). This supersedes Amendment 1's
  post-freeze-bars-only rule, which would have discarded unseen data.
- Superseded plumbing (open-to-close-leg economics, kept as evidence
  only): 3268 obs, 151 trades, net 38153.28 bps vs BH 46670.89 bps.
- Current plumbing (continuous holdings, `run_btc_three_way.py` on the
  pinned snapshot, three implementations agree at 0.01 bps): 3268 obs,
  151 trades, net **38190.11** bps vs BH **46667.66** bps.

## Amendment 3 (2026-11-17, before any forward scoring)

Amendments 1–2 are preserved; where this conflicts, it governs. Still no
forward results inspected: no signal or return has been computed on any
bar completing after 2026-09-30.

- Decision record is writable from received bars only: date, signal,
  position, qualified input hash, model id, costs. No fill, no future
  bar, no venue data (`DecisionRecord`, pinned by
  `test_decision_uses_only_received_bars`).
- Simulated fill is a separate artifact observable after the decision:
  fill price = next open, recordable only once the next bar is received
  (`FillRecord` + `observe_fill`; unreceived fill → "not yet observable",
  pinned by `test_fill_requires_next_bar_received`). Never inferred.
- Correction: exit-day P&L. Amendment 2 scored exit day 0 from the exit
  fill, dropping the overnight gap the closing position actually held
  through. The exit leg is now prior close through the exit fill; entry
  gaps remain unattributed (flat before the entry fill). Reason: a fill
  at the open settles the close-to-open move for whoever held it
  overnight — the exiter, not nobody.
- Correction: buy-and-hold benchmark is portfolio value —
  `(last_close / first_scored_open − 1)` in bps minus one entry leg —
  path-independent and auditable against two prices. Amendment 2's
  leg-summed BH is superseded.
- Superseded plumbing (Amendment-2 economics, kept as evidence only):
  3268 obs, 151 trades, net 38190.11 bps vs BH 46667.66 bps.
- Current plumbing (Amendment-3 economics, `run_btc_three_way.py` on the
  pinned snapshot, three implementations agree at 0.01 bps): 3268 obs,
  151 trades, net **38141.81** bps vs BH **137118.90** bps. The BH jump
  is the 2017–2025 buy-and-hold run-up measured as portfolio value;
  it is characterization, not a verdict.

## Amendment 4 (2026-11-17, before any forward scoring)

Amendments 1–3 are preserved; where this conflicts, it governs. Still no
forward results inspected: no signal or return has been computed on any
bar completing after 2026-09-30.

- Same portfolio-value accounting for strategy and buy-and-hold from
  identical starting capital (unit notional 1). Reported nets are
  `(V_end − 1)` in bps for both legs.
- Consistent cost deductions: every unit position change multiplies
  portfolio value by (1 − 20/10000) on the fill day; BH pays it once at
  entry. No additive bps bookkeeping anywhere.
- End-to-end fixture pinned (`test_decision_then_fill_end_to_end_fixture`):
  the 2024-03-05 decision records from the completed close with no fill
  bar present (and the stored record contains no fill data); the
  simulated fill records only after 2024-03-06 arrives, at the next open
  207.50, observable after the decision.
- Superseded plumbing (Amendment-3 economics, kept as evidence only):
  3268 obs, 151 trades, net 38141.81 bps vs BH 137118.90 bps.
- Current plumbing (Amendment-4 economics, `run_btc_three_way.py` on the
  pinned snapshot, three implementations agree at 0.01 bps): 3268 obs,
  151 trades, net **154319.31** bps vs BH **136844.63** bps.
  Characterization, not a verdict; compounding over 2017–2025 explains
  the scale versus additive legs.

## Amendment 5 (2026-11-17, before any forward scoring)

Amendments 1–4 are preserved; where this conflicts, it governs. Still no
forward results inspected: no signal or return has been computed on any
bar completing after 2026-09-30. Replay economics and plumbing totals
are unchanged by this amendment.

- Correction: the logger indexed the decision one bar early (prior
  close). The decision for a date now uses that date's own completed
  close. Pinned by the opposite-signal fixture: 2024-03-04 decides 0
  from its flat close while 2024-03-05 decides 1 from its elevated
  close; the old indexing fails both assertions.
- Decision records pin actual receipt: `received_through` is the latest
  bar date in the view at record time, alongside the input hash.
- Simulated fills carry `observable_after`, and a fill whose price
  timestamp precedes the recorded decision is rejected, never recorded.
- End-to-end fixture (`test_decision_then_fill_end_to_end_fixture`):
  2024-03-05 decides 1 with no fill bar present; the fill records only
  after 2024-03-06 arrives at 207.50.

## Amendment 6 (2026-11-17, before any forward scoring)

Amendments 1–5 are preserved; where this conflicts, it governs. Still no
forward results inspected: no signal or return has been computed on any
bar completing after 2026-09-30. Replay economics and plumbing totals
are unchanged by this amendment.

- Correction: a bar date alone is not proof of receipt time. Every
  formation close must now carry an explicit receipt timestamp at or
  before the decision record time, or the decision quarantines
  (`verify_inputs_received`; late or missing receipts abstain, pinned by
  `test_late_formation_input_quarantines`).
- Decision records pin `recorded_at_ms` alongside the receipt frontier;
  fills pin their own `recorded_at_ms` and require the fill bar's
  receipt by that time (`test_fill_rejects_unreceived_fill_bar`).
- End-to-end fixture extended: the 2024-03-05 decision and its 2024-03-06
  fill both carry pinned receipt and record timestamps.
