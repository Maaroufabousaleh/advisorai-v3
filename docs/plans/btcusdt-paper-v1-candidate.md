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
- [ ] 63+ post-freeze bars accrued for the first forward decision.
- [ ] Independent replay wired for BTCUSDT with three-way agreement.
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
