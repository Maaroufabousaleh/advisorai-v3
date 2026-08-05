# Phase 5 — First typed evidence council

## Objective

Turn independent V3-Core evidence into an explainable DecisionBundle that ends at
a target portfolio and cannot circumvent deterministic controls.

## Work packages

1. Implement Mission Router and Snapshot Builder for Fast, Standard, Deep,
   Builder, and Recovery modes with policy-selected budgets.
2. Add on-demand typed roles: Data Verifier, Technical/Flow,
   Derivatives/Regime, News/Event, Skeptic/Base-Rate, Risk/Opportunity, and
   Synthesizer.
3. Add evidence-dependency graph, source/factor/model ancestry discounting,
   dissent/missing-evidence retention, expiration, adaptive waves, early stop,
   and decision cutoff expiry.
4. Record agent/model/provider/prompt/tool versions, scorecard inputs, and a
   DecisionBundle that may propose only a TargetPortfolio.

## Exit gate

Duplicated or syndicated evidence cannot create quorum; material conflict
escalates; no agent output can bypass portfolio/risk contracts.

## Explicitly out of scope

No standing dozens-of-agents process model, no direct order tool, and no claim
that repeated LLM outputs are independent evidence.
