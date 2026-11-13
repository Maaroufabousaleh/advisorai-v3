# Provider onboarding manifest — non-secret coordination contract

Version: `provider-manifest-v1`. This file coordinates external provider
onboarding between the AdvisorAI software owner (application adapters,
runtime integration, tests, data correctness) and Luna (external account
setup, credential-vault provisioning, independent verification).

**This manifest MUST NOT contain passwords, API keys, access tokens,
recovery codes, session cookies, or any secret values.** Credential variable
*names* and scopes are declared so Luna can provision exactly what each
adapter needs; values live only in the vault / owner environment.

## How to use

- Luna provisions accounts/keys per row and records evidence in the
  `onboarding` columns (never secrets).
- The software owner implements adapters against `adapter module` and marks
  `software status`; admission stays separate per the gate matrix.
- A provider moves to `QUALIFIED` only after its own smoke test + review;
  no provider is implicitly trusted from this table.

## Conventions

- `credential scope`: the `CredentialScope` the adapter resolves — the vault
  must expose exactly those variable names, nothing broader.
- `auth type`: `api-key-hmac` | `api-key-bearer` | `none` (public).
- `tier`: `OFFICIAL PRIMARY` | `QUALIFIED SECONDARY` | `DISCOVERY / CONTEXT ONLY`.

| Provider | Docs | Feed / endpoint | Data purpose | Tier | Auth type | Credential scope | Credential variables | Free-tier notes | History | Rate limits | License / use | Adapter module | Software status | Smoke-test status | Qualification |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Binance Spot Testnet | https://developers.binance.com/docs/binance-spot-api-docs | `https://testnet.binance.vision` REST `/api/v3/*`, WS API/stream | Crypto spot paper execution (BTCUSDT single-symbol profile) | QUALIFIED SECONDARY (sandbox) | api-key-hmac | PAPER_VENUE | ADVISORAI_VENUE_API_KEY, ADVISORAI_VENUE_API_SECRET, ADVISORAI_VENUE_NAME=binance_spot_testnet, ADVISORAI_VENUE_ENVIRONMENT=testnet, ADVISORAI_VENUE_BASE_URL | Testnet faucets/keys; no real funds | Sandbox only; no production history implied | Per Binance testnet docs; adapter enforces allowlisted host | Testnet terms; paper use only | `integrations/binance_spot.py` + `session/providers/binance_spot.py` | IMPLEMENTED | recorded in `tests/session/test_binance_spot_owner_adapter.py` | PENDING provider evidence |
| Coinbase Exchange Sandbox | https://docs.cloud.coinbase.com/exchange/docs | `https://api-public.sandbox.exchange.coinbase.com` REST `/orders`, `/fills`, `/accounts`, products | Crypto spot paper execution (BTC-USD single-product profile) | QUALIFIED SECONDARY (sandbox) | api-key-hmac + passphrase | PAPER_VENUE | ADVISORAI_VENUE_API_KEY, ADVISORAI_VENUE_API_SECRET, ADVISORAI_VENUE_PASSPHRASE, ADVISORAI_VENUE_NAME=coinbase_exchange_sandbox, ADVISORAI_VENUE_ENVIRONMENT=testnet, ADVISORAI_VENUE_BASE_URL | Sandbox keys; no real funds | Sandbox only | Per sandbox docs; adapter enforces allowlisted host | Sandbox terms; paper use only | `integrations/coinbase_exchange.py` + `session/providers/coinbase_exchange.py` | IMPLEMENTED | recorded in `tests/session/test_coinbase_exchange_owner_adapter.py` | PENDING provider evidence |
| Deribit public API | https://docs.deribit.com | Public derivatives metadata (no credentials) | Derivatives context only | DISCOVERY / CONTEXT ONLY | none | — | — | Public endpoints | As published | Respect published limits | Terms per endpoint | `collectors/sources.py` DeribitCollector via `integrations/sources.py` | IMPLEMENTED | existing collector tests | PENDING scope review |
| GDELT | https://www.gdeltproject.org/data.html | GDELT 2.x API | Event/news discovery context | DISCOVERY / CONTEXT ONLY | none | — | — | Open | 15-min batch cadence; not event-time truth | Respect cadence | Verify redistribution per item | `collectors/sources.py` GDELTCollector | IMPLEMENTED | existing collector tests | PENDING scope review |
| Operator RSS allowlist | operator-configured | Allowlisted issuer/regulator feeds | Official news context | OFFICIAL PRIMARY per source | none | — | — | N/A | Per feed | Per feed | Per feed; no scrape-time-as-publication | `collectors/sources.py` RSSCollector | IMPLEMENTED | existing collector tests | PENDING per-source review |
| SEC EDGAR | https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data | data.sec.gov JSON + filing documents | Issuer filings / disclosures | OFFICIAL PRIMARY candidate | none (declared UA) | — | ADVISORAI_CONTACT_EMAIL (User-Agent, not secret) | Freely accessible; fair-access limits | Full archive | Fair-access limits; declared UA required | Freely reusable; check per-document rights | `collectors/official.py` SecEdgarCollector | IMPLEMENTED | existing collector tests | PENDING integration scope |
| FRED / ALFRED | https://fred.stlouisfed.org/docs/api/fred/overview.html | ALFRED vintage series | Macro series + revisions | OFFICIAL PRIMARY candidate | api-key (provisioned by Luna) | PAPER_VENUE or dedicated source scope (TBD with Luna) | TBD — no secret committed here | Free API key tier | Vintage-aware | Per-series terms | Attribution per terms | `collectors/official.py` AlfredCollector | IMPLEMENTED | existing collector tests | PENDING credential + scope review |
| Native venue (reviewed paper venue) | operator review | Reviewed paper-venue transport | Execution-grade market truth (paper) | EXECUTION (paper scope) | per venue | PAPER_VENUE | per venue row above | N/A | Session-scoped | Adapter-enforced | Paper scope only | `collectors/sources.py` NativeVenueCollector | IMPLEMENTED | collector + runtime tests | PENDING venue evidence |

## Credential-provider interface (software side)

`CredentialResolver` + `CredentialScope` remain the only credential path.
Adapters resolve exactly one scope (`PAPER_VENUE` for both sandbox venues).
A vault-backed resolver may implement the same `resolve(scope)` interface;
the coding-agent process MUST NOT inherit provider secrets — tests use
`CredentialResolver.from_mapping` with non-secret fixtures, and the owner
runtime receives credentials only from the trusted environment / vault at
launch. See `docs/plans/provider-credential-isolation.md` (companion note).
