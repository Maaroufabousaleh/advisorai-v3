# ADR 0008: Reference model fabric and deferred broad bake-offs

Status: accepted integration-planning authority; model admission remains
individual, versioned, and evidence-gated.

Freeze a small logical reference fabric so infrastructure and end-to-end
workflows can be completed before broad model selection is reopened:

| Reference role | Initial logical model/adapter |
|---|---|
| Probabilistic/time-series forecast and intervals | Chronos-2-small |
| Lightweight independent temporal forecast/control | TTM-R2, using the already integrated/measured R2 reference |
| Finance OHLCV/K-line evidence | Kronos-small |
| Deterministic structured baseline | LightGBM |
| Financial language/news/event sentiment | FinBERT or the currently selected compact financial sentiment implementation |
| Local typed System-One decision | Laya typed-decision adapter or equivalent small local decision-specialized model behind `DecisionModelPort` |
| Deep/research/complex-event reasoning | one strong remote reasoning LLM behind `ModelGatewayPort`, invoked on demand |

Chronos and Kronos have complementary roles. Their logical inclusion does not
require co-residency: retain one global GPU lease, lazy load, sequential waves,
micro-batching, and unload/cleanup. The scheduler may skip optional work when
evidence is sufficient, data is absent, a model is quarantined, the resource
budget is exhausted, or latency limits would be violated. Hermes is a
research/builder runtime, not a market-forecast model.

This reference fabric is for integration, not authority or permanent approval.
Level A integration qualification checks identity, adapter contracts, PIT
safety, resource bounds, load/inference/unload, failure behavior, and authority
separation. Integrated paper evaluation and broad challenger comparisons are
later work. TimesFM 3, TTM-R3, Kronos-base, TabPFN-TS, newer TSFMs, alternate
decision models, and training methods are challengers and cannot block core
infrastructure.

Jev API connectivity is optional and never a product dependency. Kev or other
small decision models may be challengers. The future AdvisorAI custom Decision
Model is trained only after an eligible, leakage-safe decision/outcome dataset
has accumulated; no training is authorized by this ADR.
