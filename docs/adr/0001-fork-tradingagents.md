# ADR 0001: Fork TradingAgents for GrainAgents

- Status: Accepted
- Date: 2026-07-30
- Upstream: `TauricResearch/TradingAgents`
- Baseline commit: `a33fd4c0f134485a43553a2c23a63cb14adbd88f`

## Context

GrainAgents must produce evidence-grounded, contract-specific grain-market
research and calibrated forecasts. TradingAgents already provides useful
multi-agent orchestration, provider-independent LLM clients, tool-calling
analysts, bull/bear debate, risk review, checkpoint recovery, persistent memory,
configuration patterns, and Markdown report generation.

The upstream domain model is nevertheless stock-oriented. Its shared state is
centered on a company and ticker, the analyst sequence includes company
fundamentals and social sentiment, and the terminal workflow produces a trade
decision. GrainAgents instead needs explicit futures contracts, point-in-time
evidence, deterministic forecast distributions, source auditing, and
newsletter-ready market outlooks.

## Decision

Fork and incrementally adapt TradingAgents instead of rewriting the system.

Retain and extend:

- LangGraph `StateGraph` orchestration and conditional routing
- the central analyst execution registry
- multi-provider LLM clients and configuration overlays
- SQLite checkpoint recovery and graph-shape-aware run identity
- typed vendor error routing
- report-tree writing and reusable programmatic API surfaces
- upstream tests for stock and crypto regression coverage

Introduce a separate `commodity_future` execution path with:

- commodity and contract domain models
- a commodity-specific state schema
- a validated, immutable evidence package before LLM execution
- commodity analyst and tool registries
- deterministic forecast and scenario data
- source-audited publication outputs

The stock and crypto paths remain available while the corn vertical slice is
developed. Commodity behavior must not be forced through stock fundamentals or
expressed primarily as BUY/HOLD/SELL.

## Alternatives considered

### Rewrite from scratch

Rejected for v1. It would discard mature orchestration, provider support,
checkpointing, reporting, error handling, and regression coverage without
improving the commodity domain model by itself.

### Treat futures symbols as stock tickers

Rejected. This would conflate continuous and delivery-specific contracts,
retain irrelevant company-financial tools, and make crop-year, expiration, and
point-in-time semantics ambiguous.

### Modify the stock path in place

Rejected. A shared state with overloaded company/contract meanings would make
regressions and evidence validation harder to reason about.

## Consequences

- The fork keeps upstream history, license, copyright, and attribution.
- Commodity work can reuse stable infrastructure and remain runnable after each
  milestone.
- Some duplicated state and routing code is acceptable initially to preserve
  clear semantics.
- Upstream merges may require explicit conflict resolution around graph, CLI,
  state, and reporting code.
- Every commodity boundary must use typed schemas and point-in-time rules.
- Numerical publication claims must originate from evidence records or
  deterministic model output, never free-form LLM generation.

## Validation

Before GrainAgents functional changes, the upstream suite completed with 576
passed and 2 optional skips on Python 3.13.2. See
[`docs/upstream-baseline.md`](../upstream-baseline.md).
