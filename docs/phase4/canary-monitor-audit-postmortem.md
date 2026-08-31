# Replacement canary monitoring/audit postmortem

This document records the tooling findings for
`20260823T150000Z-chronos-warmup-v2`. The canary remains permanently:

```text
CANARY_QUALIFIED = FALSE
```

The record is prospective canary evidence only. It is not Phase-4 admission
evidence and must not be converted into admission evidence by a tooling fix.

## Watchdog finding

The immutable watchdog event at `2026-08-23T18:21:24.980917Z` retained only:

```text
watchdog_input_error:FileNotFoundError
```

It did not retain the path or operation, so the exact missing object cannot be
recovered from V2. The possible inputs in the V2 implementation were the
preregistration, source/candidate status files, and source/candidate manifest
files. The preserved source and candidate terminal artifacts do not prove a
source or candidate failure.

The repaired watchdog now:

- reads atomic artifacts with bounded, stability-checked retries;
- fails closed after a genuinely missing mandatory artifact;
- records the exact operation, path, and failure detail;
- appends the fatal event before replacing the current status marker; and
- treats any prior `CANARY_FAILED` event as absorbing history.

The scheduler passes the original watchdog history root to every one-shot
check. A later current-health observation therefore cannot become
`CANARY_HEALTHY` for the same canary after a fatal event.

## Auditor finding

The former terminal auditor copied the already-final normalized bars into the
replay spool before replaying earlier raw receipts. That made an earlier raw
variant look like a post-admission revision.

The repaired auditor replays raw receipts into an empty temporary normalized
spool, records the actual admission receipt and timestamp, and compares the
replayed final bars with the persisted immutable normalized bars afterward.
Variations before admission are reported separately from revisions after
admission.

The ETH interval ending at `2026-08-23T15:45:00Z` is the regression case:

| Receipt | Content | Role |
|---|---|---|
| 15:45:00.970963Z | first OHLCV version | pre-admission observation |
| 15:45:32.668159Z | final OHLCV version | first supporting receipt |
| 15:46:04.696743Z | same final OHLCV version | second supporting receipt and admission |

No differing receipt followed admission in the replay. The repaired
post-hoc diagnostic classified this as pre-admission variation and found zero
genuine post-admission revisions.

## V2 immutable identity record

These hashes were read before and after the post-hoc diagnostic; no V2 file was
written or changed.

| Artifact | SHA-256 |
|---|---|
| Preregistration | `d59a1090f8d06e41986593c34fc9a3ecdd7c4dc62fc22ed76de3321ec677cfc9` |
| Raw receipts | `e840250a7edced215bcba6bfe46c77acc2c0b0b416dc6fdf03434458e8a0423d` |
| Admitted bars | `0bc61540cecd7cc49b9c349cae29c6edfba693e928e141a472da977623bd62b1` |
| Candidate ledger | `3b65b8f2409421b379085a3e1e577a835f8379dbd6c33079316ee5ddc87f81b6` |
| Source status | `732181bb2da29ecd5294d53bb0e84d5a40cb557e22ba0d4d45dabe47419ee33c` |
| Candidate status | `129ac0d6974b605d59fffb5fc6791448320acae1c7bc6889d903193e219f5f02` |
| Watchdog events | `a67d870efbc37658962302152d6870d6487c35b2179764c854412a62e4939ff0` |
| Watchdog status | `161a7cdfaac7b55297ef948b1ab83e232d36d4e3ab8020aa366c4b67a20ebc14` |
| Scheduler checkpoint status | `abdc0e7e1c6ecea00506efd332281104b3d055f80370ffc7b209918b989b43c4` |
| Scheduler terminal status | `6a747c2feed98f6f5451ef57b3b5e8a16bd7d9cd31a45d93268b218c57df5930` |
| Scheduler events | `d54361286f29ad14d309b93402244555fda9f35b6b9dedf342a7635e2a7607ba` |
| Terminal audit stderr | `b9fb98fe63f6c60c0c1c7b148b9174dff58837190fe0b1e2b229a711b63a9f35` |
| Launch metadata | `7246137064aacb4ffa1e7c5cd1d10147465f6ecbf0491d7c2bb21245b88e3a94` |

The zero-record rejection ledger was not present as a file.

## Post-hoc diagnostic result

The corrected audit completed outside the V2 evidence root and reported:

```text
source finality diagnostic: PASS
raw interval identities: 240
admitted final intervals: 240
unresolved intervals: 0
genuine post-admission revisions: 0
pre-admission variation intervals: 2
BTC outcome links: 4/4
ETH outcome links: 4/4
CANARY_QUALIFIED: FALSE
```

The canary remains failed because the original watchdog fatal history is
immutable and absorbing. No model, finality, context, cadence, or sample
contract was changed.
