# Read-only provider integration status

Snapshot date: 2026-11-13. This is a non-secret status record. No API values
belong in this file.

| Provider | Integration state | Offline contract | External verification | Credential state / next step |
|---|---|---|---|---|
| EIA v2 | Credential-safe client and guarded Infisical runtime path implemented | Dummy-key request, error, echo-scrubbing contracts added | Not authenticated-tested | `EIA_API_KEY` exists in Infisical `dev`; owner must review runtime isolation and execute trusted smoke |
| BLS v2 | Read-only client implemented | POST body, schema, and row mapping contract added | Not tested with a registered key | Awaiting owner CAPTCHA, terms consent, registration, and secure key transfer |
| BEA Data API | Read-only client implemented; exposed key is treated as compromised and unused | Reserved-parameter and sanitized-provider-error contract added | Not tested with any BEA credential | Owner/Luna revoke exposed key, reissue, and transfer replacement securely |
| OpenAlex | Keyless metadata search client implemented; citation metadata only | Offline card mapping contract added | Prior keyless works search passed | No key needed for current use |
| Crossref | Keyless works metadata client implemented; citation metadata only | Offline DOI/card mapping contract added | Prior public API check passed | No key needed |
| BLS v1 | No new connector in this change | — | Prior public endpoint check passed | No credentialed test |
| Kraken | Existing direct public market-data feeds unchanged | Existing repo tests remain authoritative | Prior public ticker/orderbook/OHLC checks passed | Public endpoints only; no trading key |
| Deribit | Existing direct public market-data feeds unchanged | Existing repo tests remain authoritative | Prior public BTC perpetual summary check passed | Public endpoints only; no trading key |
| Census | Not added | — | Deferred previously | No key provisioned; add only after scope/use review |
| OpenBB V5 | Optional architecture evaluation only; not installed or enabled | Design guardrails documented | No adapter-level verification | Future opt-in decision after isolated provider and provenance review |

Infisical state remains: project `AdvisorAI V3`; `dev` contains only the existing
`EIA_API_KEY`; `paper-testnet` and `prod` are empty; no machine identity exists.
The EIA value has not been read by the coding agent. No new provider credentials
were provisioned. No paid service, financial account, trading credential, or
production secret was activated.
