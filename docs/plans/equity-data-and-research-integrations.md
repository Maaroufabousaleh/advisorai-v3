# Equity data and research integrations

**Review dates:** 2026-11-17, 2026-11-23, and 2026-11-25. The
[native roadmap](native-first-integration-roadmap.md) selects OpenBB V5 and
Qlib/RD-Agent for the full workstation and defines stock/ETF and listed
options paper qualification in I8. The scope below covers equity research
inputs and pilot qualification.
The [technology decision register](../reference/technology-decision-register.md)
tracks integration and admission. The [provider authority ADR](../decisions/0011-provider-source-authority-and-pit.md)
governs source qualification. Each prototype has a separate review and may run
independently of the others.

## Current evidence and scope

- Main contains point-in-time corporate-action contracts and an equity evidence
  council. [PR #215](https://github.com/Maaroufabousaleh/advisorai-v3/pull/215)
  reports private historical Yahoo Finance ETF replay on SPY and TLT. Its
  checked-in replay input is synthetic. That establishes a research path and
  fixture coverage.
- There is no admitted stock or ETF market-data feed or broker-paper route.
  [PAPER-V1](paper-venue-selection.md) targets Binance Spot Testnet for crypto.
  Equity research can proceed through frozen snapshots. Equity paper execution
  requires a separately qualified venue and account.
- Start with end-of-day U.S.-listed stocks and ETFs. AAPL, SPY, and TLT cover
  a single stock, an equity ETF, and a bond ETF. Include fixtures for a split,
  a cash dividend, a ticker change, and an inactive or delisted instrument.
  The three-symbol smoke test does not establish broad universe coverage.

## Feed selection

| Role | First evaluation | Backup or expansion decision |
|---|---|---|
| U.S. research bars | [Alpaca historical SIP](https://docs.alpaca.markets/us/docs/market-data-faq). Its historical API documents SIP access without a paid subscription for queries ending at least 15 minutes in the past. Set the feed explicitly and verify the account and endpoint. Keep the completed session, API delay, and observed availability in the snapshot. | Free live IEX is single-exchange coverage with different volume and OHLC semantics. Keep it as a separate research series. If the exact SIP feed or retention terms fail qualification, qualify an alternative on the same fixture before routing research to it. |
| Second daily-price check | [Massive Stocks Basic](https://massive.com/pricing?product=stocks) advertises two years of end-of-day data. Compare only dates and fields supported by both feeds. | [Tiingo EOD](https://www.tiingo.com/documentation/end-of-day) is the alternate candidate where split/dividend fields, longer requested history, or coverage justify it. Confirm the account limits and [permitted uses](https://www.tiingo.com/pricing). Select one secondary connector initially. A different vendor may still use the same exchange/SIP inputs. |
| U.S. filings and issuer events | [SEC EDGAR](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) and issuer IR releases are the primary candidates. Compare the [TradingAgents EDGAR implementation](https://github.com/TauricResearch/TradingAgents/blob/main/tradingagents/default_config.py) with [EdgarTools](https://github.com/dgunning/edgartools) on a 10-K, 10-Q, amendment, and earnings release. Use one parsing path behind the existing collector and retain the source accession and acceptance time. | [Financial Datasets](https://www.financialdatasets.ai/pricing), used by `ai-hedge-fund`, is an optional paid enrichment trial for normalized statements, earnings and operating metrics. Require accession or source linkage and replayable filing/amendment times. A second EDGAR parser tests interpretation of the same filing. |
| Corporate actions and universe history | Use permitted official publisher/exchange records and verify provider action records against them. Capture splits, dividends, symbol/identifier changes, listing state, and delistings. Preserve both effective and first-known times. | Evaluate [Databento corporate actions](https://databento.com/docs/datasets/corporate-actions) only if the available feeds cannot supply auditable action or delisting history. Expand beyond the smoke-test symbols only after historical universe membership and inactive-symbol coverage are verified. |
| Macro context | [FRED/ALFRED](https://fred.stlouisfed.org/docs/api/fred/) for release-time and vintage-aware series. | Use the [New York Fed Markets API](https://markets.newyorkfed.org/static/docs/markets-api.html) directly when SOFR/EFFR or publisher verification is required. A FRED copy of a NY Fed series shares the same original publisher. |
| Limited research or discovery | Yahoo Finance has the documented private replay. [Alpha Vantage](https://www.alphavantage.co/support/) is a keyed spot-check candidate; its standard free limit is 25 requests/day. | Yahoo wrappers, Qlib's Yahoo collector, and the [defeatbeta Yahoo-derived snapshot](https://huggingface.co/datasets/defeatbeta/yahoo-finance-data) belong to the same upstream family. Different wrappers can test ingestion behavior. Qualify source rights and provenance before collecting, retaining, or sharing data. |
| Equity paper venue | Use the existing execution boundary to evaluate [NautilusTrader's upstream IBKR adapter](https://nautilustrader.io/docs/latest/integrations/interactive_brokers/), which supports IB Gateway/TWS paper access. Confirm account permissions and [market-data subscriptions](https://www.ibkrguides.com/clientportal/aboutpapertradingaccounts.htm). | [Alpaca Paper](https://docs.alpaca.markets/us/docs/paper-trading) is an alternative if IBKR access or integration effort warrants it. Qualify venue data, permissions, order/fill/cancel behavior, reconciliation, restart and recovery separately. Alpaca research-feed access alone establishes none of that venue evidence. |

## London Strategic Edge evaluation

**Decision (2026-11-25):** Queue a read-only I5 pilot, starting with U.S.
options. [LSE Data 3.0](https://londonstrategicedge.com/data/) is a candidate
for discovery and frozen research inputs. Authenticated coverage, accuracy,
latency and access on the intended account are unverified. Review each
dataset and the Terminal separately.

| Candidate | Test and decision |
|---|---|
| Options chains, prints and contract candles | **First pilot.** Use SPY and AAPL, two expiries each and one completed U.S. session. Compare contract identity, multiplier, venue, trade price and size, timestamps and any available bid/ask against an entitled IBKR or other qualified reference. Declare comparison tolerances and a coverage target before collecting. Admit for discovery or historical prints only if those fields pass. Test quote continuity, spreads and availability separately before considering fill-sensitive replay. |
| Implied volatility and Greeks | The [SDK](https://pypi.org/project/lse-data/) describes twice-daily calculations and says session prints are archived after the session. Record when each input and calculated value became available. Show only as dated analytics if the calculation or availability cannot be established. |
| Equity and ETF Parquet | Compare AAPL, SPY and TLT bars with the planned Alpaca SIP check on completed, aligned sessions. Verify adjustments, split/dividend handling, gaps, timestamps and revisions. Evaluate as a secondary backfill or frozen research input where the checks pass and there is a documented coverage or cost benefit. |
| Macro releases, yields and reference data | Test one named dataset for calendar or instrument context. Compare availability times and historical revisions with the original publisher. Keep FRED/ALFRED for vintage-aware U.S. workflows unless equivalent evidence is established for the proposed series. |
| [LSE Terminal](https://github.com/londonstrategicedge/lse-terminal) | In I9 dashboard work, compare one separable feature, starting with options-flow exploration or a backtest results view. Review the exact revision, MIT obligations, dependencies, data rights and effort saved. Reuse only a component that improves the existing dashboard. |

**Evidence to retain.** Pin the client/API revision, endpoint, account tier,
instrument and session. Use the provider's documented API or SDK. Keep raw
payloads in an entitled private store with export hashes, event, availability
and retrieval timestamps, and observed revisions. Record coverage, field-level
discrepancies, freshness/latency, throttling, and quota cost. Verify limits
against the actual account at the time of the trial. The November review
found SDK documentation of 2.5 million rows per export and 12.5 GiB per
week on the free plan. Where upstream lineage is undisclosed, label it unknown and exclude it from independent-source counts.
LSE says its own-market prices use its methodology, are not official exchange
or consolidated prices, and can change after publication.

**Rights and routing.** Review the [terms](https://londonstrategicedge.com/terms/)
and [data licence](https://londonstrategicedge.com/licence/) for each use.
The provider describes personal, individually run use with the operator's own
key. Shared dashboards, redistribution and organisational or commercial use
require separate rights. The SDK and Terminal code licences do not convey
market-data rights. Nasdaq TotalView and CME Globex are paid feed entitlements
with exchange-specific terms. Keep keys and restricted rows out of source
control and shared artifacts.

Record **adopt for a named read-only use**, **defer with missing evidence**,
or **reject** for each candidate, with scope and reasons. A passing display or
research pilot can feed a provider-labeled view in Markets & OpenBB. Keep
LSE research data outside paper routing, order reconciliation and I8 venue
qualification.

## Evaluation of external projects

| Project | Decision and specific role | Acceptance or revisit trigger |
|---|---|---|
| [OpenBB V5](../architecture/openbb-v5-research-adapter.md) | Deliver one allowlisted read-only market-data or options-chain connector through an entitled provider. [Cboe historical bars](https://docs.openbb.co/odp/python/reference/cboe/equity/historical) are a secondary-check candidate if instrument coverage and field definitions match. Use V5 provider namespaces. | Compare against a direct client for the same endpoint and retain the upstream dataset and feed identity. V5 is Apache-2.0 and [V4 Yahoo/FMP connectors did not carry forward](https://docs.openbb.co/odp/python/migration-from-v4). Compare it with the Massive/Tiingo options for the same slot before adding another price connector. |
| [Qlib](https://github.com/microsoft/qlib) | First isolated quantitative challenger. Run Alpha158 + LightGBM on a frozen AdvisorAI snapshot against a preregistered simple ranking baseline. Deliver bounded [RD-Agent quant research](#qlib-research-workflow) through the same adapter after the baseline passes. | Compare out-of-sample rank IC, turnover and behavior after realistic costs. Independently replay finalists in NautilusTrader. An upstream Yahoo collector or sample dataset is for tooling checks. |
| [TradingAgents](https://github.com/TauricResearch/TradingAgents) | Sole initial external qualitative challenger. Run the bull/bear stage on selected Equity Council disagreements and stop at the research artifact. | Use the same dated evidence packet and restrict retrieval to that cutoff. Compare verified corrections, novel source-backed objections, latency and cost against the Council alone. Regression-test late filings. Upstream defaults include Yahoo for prices/news and SEC EDGAR then Yahoo for financial statements. The connector evaluation is independent of the agent trial. |
| [`ai-hedge-fund`](https://github.com/virattt/ai-hedge-fund) | Valuation-method reference, with scenario DCF or owner earnings as potential narrow cross-checks. Its portfolio simulator and investor personas duplicate existing responsibilities. | Compare one independently specified valuation on the same statement snapshot when assumptions materially affect the thesis. Record each assumption's effect. Evaluate its Financial Datasets provider separately if needed. Verify the exact upstream version if adapting an older implementation. |
| [Trading-R1](https://github.com/TauricResearch/Trading-R1) | Manual, on-demand trial for hard filing, earnings, or valuation questions where Hermes/Deep research has a gap. | Use public cases. Disable [default Tauric session sync](https://github.com/TauricResearch/Trading-R1/blob/main/MANUAL.md#session-sync) for the trial and verify data/model/retention terms before sending anything private. Count an original source the baseline missed; keep Tauric results as leads until upstream provenance and publication time are verified. |
| [FinceptTerminal](https://github.com/Fincept-Corporation/FinceptTerminal) | Standalone UX and connector reference. Its Nasdaq and FMP connector code can guide discovery. | Open an evaluation only for a named endpoint or separable component, with exact revision, code/license and provider-rights review. The documented open edition uses AGPL. Evaluate direct APIs before adopting the terminal or its storage. |
| [`daily_stock_analysis`](https://github.com/ZhuLinsen/daily_stock_analysis) | Defer the application. Review its A-share/Hong Kong collector patterns if those markets enter scope. U.S./HK fundamentals in its documented path use yfinance. | Find a specific upstream dataset that fills a regional gap and verify publication times, adjustments and rights. Existing jobs and Council own watchlists, analysis, dashboards and alerts. |
| [`bbfamily/abu`](https://github.com/bbfamily/abu) | Methods reference for a specific timing or exit hypothesis. | Run the rule through the existing Alpha Team experiment workflow. Its simulation and ML stack overlaps with Qlib and canonical replay. |
| [`myhhub/stock`](https://github.com/myhhub/stock) | Defer. Position-cost distribution (CYQ) is the distinctive A-share feature worth revisiting. | Require approved A-share scope and a testable feature with clear source and window semantics. Keep the existing screening, data and execution owners. |

Freqtrade/FreqAI remains a crypto testing reference, such as for
[lookahead analysis](https://www.freqtrade.io/en/latest/lookahead-analysis/).
OctoBot remains rejected as a second runtime in the decision register. Neither
is part of this equity integration sequence.

## Qlib research workflow

1. Reproduce the Alpha158 + LightGBM benchmark from a frozen, point-in-time
   AdvisorAI U.S. equity snapshot. Pin the universe, calendar, feature and label
   definitions, and train/validation/test windows. Keep the benchmark runnable
   without an LLM.
2. Run Microsoft's [RD-Agent quant workflow](https://github.com/microsoft/RD-Agent/blob/main/docs/scens/quant_agent_fin.rst)
   as an opt-in factor and model search within the Qlib adapter. Pin Qlib and
   RD-Agent separately and override upstream data, universe and benchmark
   defaults for the approved snapshot. AdvisorAI schedules both workloads
   through the same adapter and runs generated code in the existing research
   sandbox. Bound trials, runtime and model spend. Use training and validation
   for search and withhold final test data and results from the search loop.
3. Export factor proposals as `CandidateFactorArtifact` and model experiments
   as `ExperimentArtifact` with snapshot, configuration, dependency revisions,
   and trial lineage. Reproduce finalists through independent NautilusTrader
   replay before promotion. The adapter has research-only permissions.

## Awesome Quant intake

[Awesome Quant's data section](https://github.com/wilsonfreitas/awesome-quant/blob/c285428fce3da53b9a3b0c5ef3594310046a2b52/README.md#market-data--data-sources)
is a manual discovery source for the Research Scout. Record each useful lead
with the actual upstream, market, dataset, exact endpoint, source family and
proposed use before qualification. A catalog listing or open-source client
license grants neither provider entitlement nor data redistribution rights.

- **Evaluate first:** EdgarTools and Tiingo as described above. Use
  [canli-pit-lake](https://github.com/arhancanli/canli-pit-lake) for test design
  around explicit as-of reads, delistings and action visibility in AdvisorAI's
  existing lake. Use the [Dividend Data Toolkit](https://github.com/holaclea/dividend-data-toolkit)
  as a narrow SCHD fixture reference only if ETF holdings or dividend checks
  enter scope. Trace each relevant input to its original publisher.
- **Defer until a concrete need:** [itch-book](https://github.com/groovg/itch-book)
  for Nasdaq TotalView-ITCH sample decoding if equity order-book research is
  scheduled. Sample depth is a parser fixture. [pit-adjuster](https://github.com/holdout-labs/pit-adjuster)
  and A-share collectors are regional references if A-share research opens.
  [Fonrex](https://github.com/fonrex/fonrex) can surface individual provider
  leads for U.S./European coverage. Its full store and normalization overlap
  with V3. Macro and futures clients can be considered when a research
  hypothesis needs their exact official series.

## Verification and delivery order

1. **Freeze the provider contract.** Capture provider, underlying publisher or
   feed, symbol and stable instrument ID, venue/calendar, field units, session,
   adjustment mode, event/effective time, first-available time, retrieval
   time, revision and raw-payload hash where permitted. Keep original and
   derived series traceable. Record exact API/account tier, access, retention,
   redistribution, quota and expiry before storing data.
2. **Qualify prices and filings first.** Run the Alpaca/EDGAR smoke cases with
   fixture tests for delayed bars, early close, missing sessions, splits,
   dividends, amendments, ticker reuse, and rate limit or source outage.
   Compare regular-session completed bars on matching price/adjustment bases.
   Derive comparisons from aligned fields and declare acceptable tolerances
   before seeing results. Record coverage and discrepancies by instrument,
   field, and session. Keep unverified or conflicting fields explicitly
   missing/degraded until resolved.
3. **Add redundancy only for a named failure mode.** A qualified outage
   fallback preserves its own source label and freshness. Do not splice IEX
   into a SIP history or mix adjustment conventions. A second wrapper of one
   feed checks transport/parsing. A second provider can cross-check ingestion
   or adjustment semantics. An original filing/issuer release verifies a
   material fact. A blinded thesis, valuation method, or independent replay
   tests interpretation or implementation. Document shared upstream lineage
   and failures each check cannot detect.
4. **Run the research pilots.** Once immutable prices, corporate actions,
   instrument history and filings are available, run Qlib on a frozen snapshot
   before qualifying bounded RD-Agent search in that adapter. Test TradingAgents
   when the Equity Council can emit a dated packet.
   Record outcomes, rejected trials, runtime and marginal cost in existing
   AdvisorAI artifacts. Keep each challenger optional and removable.
5. **Qualify broker paper separately.** Only the existing Decision Model,
   Portfolio Constructor, RiskKernel, OMS and approved NautilusTrader path
   participate in proposal, sizing, veto and execution. Keep analyst tools,
   directories and provider wrappers outside broker access and order
   authority. Follow the [gate matrix](gate-matrix.md) for promotion evidence.
