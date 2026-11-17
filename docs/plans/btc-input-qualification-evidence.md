# BTCUSDT Input Qualification — Handoff Evidence (P05 prep)

Owner decision 2026-11-17: this handoff evidence is ACCEPTED without a
second-machine re-fetch. P05 itself stays OPEN and closes only after P04
lands (merge + green CI).

## Qualified input

- Source: `binance-public-spot-archive` (per-file `.zip.CHECKSUM` verified).
- Snapshot: 3332 gapless UTC-day bars, 2017-08-17..2026-09-30,
  `bars_sha256=1ee07b5b24a5640da47e8be89964daf63a9bee86e18e8488fbf7136a10e53dce`.
- Units: ms-utc normalized (archive mixes ms and microsecond stamps;
  raw units recorded per manifest). Interval 1d, symbol BTCUSDT.
- Cutoff rules: bar complete at T iff `close_time < T`; 24/7, no holidays.
- Corporate actions: none (spot); `adj_close` equals venue close.
- Qualifier: `scripts/run_btc_input_qualification.py` → `QUALIFIED`.

## Archive-to-feed handoff (explicit cutoff 1794936673879)

- Seam 2026-09-30 → 2026-10-01 proven continuous (`check_continuity`).
- 47 complete forward bars 2026-10-01..2026-11-16 via the read-only
  public klines API; 1 forming bar dropped at the cutoff.
- Forward segment independently QUALIFIED
  (`ca4c46c9d4ddd945973e5b866491b60f807aaeff2465e2ea8725b6348ec915c7`).

## Fail-closed demonstrations (all raise, nothing scored)

- Stale: snapshot qualified with far-future `as_of` → "feed is stale".
- Incomplete: segment qualified with `as_of` equal to the last bar's
  `close_ms` → "bar not complete at decision cutoff".
- Missing: crafted one-day gap → "date gap".
- Manifest: tampered source/symbol/interval/units → value mismatch.
- View: gapped or duplicate handoff → load refused; unknown or
  history-less day → logged as abstention with reason, never scored.

Unit suite: `tests/research/test_btc_spot.py` (8) pins every gate above.
No Testnet, venue, credential, or order path is involved anywhere.
