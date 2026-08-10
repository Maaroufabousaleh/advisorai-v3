# Coinbase Exchange Sandbox paper connector

This runbook covers the provider-specific Coinbase Exchange REST adapter. It
does not replace the canonical execution chain:

```text
target → RiskKernel → OMS → CoinbaseExchangeSandboxTransport → reconciliation
```

The adapter is paper/testnet-only. It accepts only:

- REST: `https://api-public.sandbox.exchange.coinbase.com`
- reviewed WebSocket host: `wss://ws-feed-public.sandbox.exchange.coinbase.com`

The production Exchange host, all transfer/withdrawal paths, and every live
environment are rejected in code. Coinbase Exchange Sandbox is a separate
environment from production and uses separate API keys.

## Authentication boundary

The adapter is constructed only from `CredentialResolver.resolve(CredentialScope.PAPER_VENUE)`.
It never sources `secrets.env`, requests an LLM scope, or persists credential
values. The signer sends only the four Coinbase headers:

```text
CB-ACCESS-KEY
CB-ACCESS-SIGN
CB-ACCESS-TIMESTAMP
CB-ACCESS-PASSPHRASE
```

`CB-ACCESS-SIGN` is the base64-encoded HMAC-SHA256 digest using the base64-
decoded API secret over `timestamp + uppercase_method + request_path + exact_body`.
GET query parameters stay on the URL and are not included in the request path
used by the signer. See the [official Exchange authentication
documentation](https://docs.cdp.coinbase.com/exchange/rest-api/authentication).

## Configuration check

Run the existing zero-network check and review only its non-secret output:

```bash
./.venv/bin/python scripts/check_transition_config.py \
  --secrets /mnt/c/projects/advisorai-v3/secrets.env \
  --venue-allowed-host api-public.sandbox.exchange.coinbase.com
```

The operator-provided configuration hash for the current local configuration
is:

```text
138042cd88c96e9d3079493beee740ba1e96def1ea748c361e51bd8ea88094cf
```

Do not print, source, copy, or paste any credential value.

The scoped resolver is fail-closed on unknown inventory names. If a later
configuration check rejects the populated inventory because it contains a
non-allowlisted variable, remove or review that entry locally against the
repository template before rerunning this smoke. Do not disclose its value in
chat; this prerequisite is separate from Coinbase authentication and does not
authorize a production fallback.

## Read-only smoke gate

The generic venue smoke must not be used for this connector. The Coinbase smoke
is opt-in and performs `/time`, `/products`, product truth verification for
`BTC-USD` and `ETH-USD`, `/accounts` projections, `/orders`, and product-filtered
`/fills` reads. It never submits or cancels an order.

```bash
ADVISORAI_RUN_NETWORK_SMOKE=1 \
  ./.venv/bin/python scripts/smoke_coinbase_exchange_sandbox.py \
  --secrets /mnt/c/projects/advisorai-v3/secrets.env \
  --configuration-hash 138042cd88c96e9d3079493beee740ba1e96def1ea748c361e51bd8ea88094cf \
  --evidence-dir artifacts/phase2/coinbase-exchange-sandbox/read-only-smoke
```

Evidence is sanitized and append-only under
`artifacts/phase2/coinbase-exchange-sandbox/read-only-smoke/`. It contains
connector identity, schema/count/latency outcomes, credential reference names,
operation count, and hashes only; it does not contain API headers, signatures,
account IDs, profile IDs, balances, or fills.

The latest 2026-08-09 real attempt reached the reviewed sandbox `/time`,
`/products`, authenticated `/accounts`, `/orders`, and product-filtered
`/fills` endpoints. The returned 13-product catalogue contained `BTC-USD` and
did not contain `ETH-USD`; account, balance, position, and open-order reads
passed, while the BTC-USD fills read returned sanitized HTTP 401. The immutable
failed evidence is:

```text
run: artifacts/phase2/coinbase-exchange-sandbox/read-only-smoke/20260809T235254.999504Z/coinbase-read-only-smoke.json
sha256: 79c359996cb8d330739495117730924c13ff29f909359e0c189dfea02498fdc7
network calls: 7
```

Because the required product mapping and fills read did not pass, all order
operations were correctly skipped. This is external evidence of a provider
catalogue/permission blocker, not a successful smoke or venue admission.

Coinbase's current [fills endpoint documentation](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/orders/get-all-fills)
requires either `order_id` or `product_id` and documents the `view` or `trade`
permission requirement. The smoke used `product_id=BTC-USD`; it records only
the HTTP status class and never stores the provider error body.

The official [Exchange Sandbox documentation](https://docs.cdp.coinbase.com/exchange/introduction/sandbox)
defines the reviewed sandbox REST host. Product, account, order, and fill
schemas remain provider-owned; the adapter maps them into the existing typed
venue/account/OMS contracts rather than pretending they are generic
`/account`, `/positions`, or `/balances` responses.

## Gate to paper lifecycle

No Coinbase order may be sent until a new immutable read-only smoke records:

1. both exact product IDs from the returned sandbox catalogue;
2. successful authenticated account, balance, open-order, and fill reads;
3. the exact reviewed sandbox host and current configuration hash.

Only then may a supervised operator wire the existing `NativeVenueAdapter` and
`OrderManager` around the transport. The first order must be minimum practical
size, post-only/passive where supported, persist intent before transport, pass
the deterministic order-level `RiskKernel` check, reconcile venue state, and
cancel or ingest a fill through the OMS. No agent, model, Hermes task, browser
task, dashboard, or LLM route can invoke this write path.

The current next actions are operator/provider-side: ensure the local inventory
passes the strict scoped resolver, use a Coinbase Exchange Sandbox
profile/catalogue that genuinely exposes both `BTC-USD` and `ETH-USD`, and grant
the scoped key the documented fills read permission; then rerun the read-only
smoke. Do not paste or disclose credentials in chat.

`LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.`
