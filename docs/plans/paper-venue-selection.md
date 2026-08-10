# V3-Core paper venue selection

This decision records the smallest provider-specific replacement candidate for
the incomplete Coinbase Exchange Sandbox gate. It does not change the
AdvisorAI authority chain or admit a venue for orders.

## Decision

Binance Spot Testnet is the selected candidate for the next V3-Core paper
venue qualification. It is a separate testnet host, uses fake funds, exposes
the `/api/*` Spot REST surface, and has a testnet WebSocket API/user-data
surface documented by Binance. The official Spot API documentation and
testnet revision reviewed on 2026-08-10 are:

- [Binance Spot API documentation](https://github.com/binance/binance-spot-api-docs)
- [Spot Testnet documentation](https://github.com/binance/binance-spot-api-docs/tree/master/testnet)
- reviewed documentation revision: `b483413fcdf4da783d3cfcaad6fab7200a93297f`
- REST host: `https://testnet.binance.vision`
- WebSocket API: `wss://ws-api.testnet.binance.vision/ws-api/v3`
- public stream host: `wss://stream.testnet.binance.vision/ws`

The public, credential-free qualification measured server time and provider
product truth. The returned catalogue contained both required `BTCUSDT` and
`ETHUSDT` symbols. Immutable evidence is at:

```text
artifacts/phase2/binance-spot-testnet/public-truth/20260810T165904.357047Z/binance-spot-testnet-public-truth.json
sha256: 34af4ef5649c0d0b92635507b422d7217c8a83f72156a6e2d99561e6da6d56e6
```

The ordered Binance/Bybit credential-free bake-off subsequently queried the
actual non-production APIs for server time, both product records, provider
filters, BTC/ETH order books, and BTC/ETH public trades. Both candidates passed
that public gate; the machine-generated comparison is at:

```text
artifacts/phase2/paper-venue-bakeoff/20260810T190539.057729Z/paper-venue-candidate-bakeoff.json
sha256: 78d8034c56a1b651da968129e463d73d23745a95565e1d9e80092a0bbd569b3a
```

The comparison also records the official private-operation contracts. Binance
is selected because it was the first preferred candidate to pass and already
has the smallest provider-specific AdvisorAI adapter. Bybit remains a measured
alternative, not an additional runtime dependency or authority path. This is
still public external measurement only: it is not authenticated read-only
qualification, paper lifecycle evidence, or venue admission.

## Alternatives and rejection reasons

### Coinbase Exchange Sandbox — preserved, not active candidate

The provider-specific Coinbase adapter and evidence remain preserved. The real
sandbox was reached and authenticated private reads worked, but the returned
product catalogue did not contain the required `ETH-USD` market and the
product-filtered fills read returned HTTP 401. No order was sent. The
immutable failed evidence is recorded in the gate matrix and the Coinbase
runbook; it remains a provider-side blocker rather than a reason to weaken the
BTC+ETH requirement.

### OKX Demo Trading — rejected for this transition

The official [OKX Demo Trading documentation](https://app.okx.com/docs-v5/en/)
uses the production API host with an `x-simulated-trading: 1` request header.
That is a provider-supported simulation mode, but it conflicts with
AdvisorAI's reviewed-host invariant for this transition: production hostnames
are prohibited even when a request is marked simulated. No OKX adapter or
credential inventory was added.

No generic exchange abstraction, CCXT authority path, or production endpoint
was introduced. Binance is a candidate behind the existing `NativeTransport`
boundary only.

### Bybit Spot Testnet — measured, not selected

The official [Bybit integration guidance](https://bybit-exchange.github.io/docs/v5/guide)
identifies `https://api-testnet.bybit.com`, and the [Spot public WebSocket
endpoint](https://bybit-exchange.github.io/docs/v5/ws/connect) identifies
`wss://stream-testnet.bybit.com/v5/public/spot`. The live public comparison
found `BTCUSDT` and `ETHUSDT` in `Trading` status, with tick size, base
precision, minimum quantity/notional, order books, and public trades. Bybit
also documents `orderLinkId` for client-controlled order identity and private
wallet/open-order/order-history/execution reads. It is not selected because
Binance passed the ordered first-candidate rule and its adapter is already
implemented and tested. No Bybit credentials, writes, or adapter were added.

## Current state and next gate

The Binance adapter is implemented and fixture-tested, and the public product
truth is measured. The authenticated read-only gate remains
`PENDING_OPERATOR_ACTION` until the one canonical `PAPER_VENUE` profile in the
repo-local `secrets.env` is reviewed for Binance Spot Testnet and the explicit
smoke runner passes:

```text
ADVISORAI_RUN_NETWORK_SMOKE=1 ./.venv/bin/python \
  scripts/smoke_binance_spot_testnet.py \
  --secrets /mnt/c/projects/advisorai-v3/secrets.env \
  --configuration-hash <zero-network-binance-config-sha256> \
  --evidence-dir artifacts/phase2/binance-spot-testnet/read-only-smoke
```

Do not paste key or secret values into chat. The adapter resolves only
`CredentialScope.PAPER_VENUE`, rejects every production/transfer/withdrawal
path, and records credential reference names rather than values. No order,
cancel, transfer, or withdrawal is permitted before the authenticated smoke
passes and the deterministic `RiskKernel → OMS → transport` lifecycle is
explicitly exercised.

`LIVE-CAPITAL DEPLOYMENT IS NOT APPROVED.`
