# Milestone 1: Commodity foundation

This checklist is the initial implementation backlog for the corn-first
commodity foundation. Work must be split into focused `agent/` branches and keep
the upstream stock and crypto test suite passing.

## Domain model

- [x] Add `commodity_future` as an explicit asset type.
- [x] Define typed commodity, exchange, delivery month, and contract metadata
      models.
- [x] Implement strict symbols for `ZC`, `ZS`, and `ZW`, beginning with December
      corn.
- [x] Map delivery month and year to crop year without conflating continuous and
      delivery-specific series.
- [x] Validate first-notice and last-trade dates using the Milestone 1 weekday
      calendar rule.
- [x] Raise typed errors for malformed, unknown, and expired contracts.

## State and evidence

- [x] Add a commodity-specific LangGraph state schema.
- [x] Add the immutable evidence-package skeleton.
- [ ] Add populated fact/source schemas with stable IDs.
- [x] Record commodity, contract, crop year, as-of timestamp, horizons, and
      evidence URI in run state.
- [x] Add freshness, contradiction, unit, and point-in-time quality fields to
      the evidence skeleton.

## CLI and configuration

- [x] Add commodity, contract, as-of, horizons, analysts, research depth, and
      output format inputs.
- [x] Add commodity configuration and environment-variable overlays.
- [x] Preserve current stock and crypto CLI behavior.
- [x] Include asset type, delivery contract, and horizons in checkpoint identity.

## Graph and tools

- [x] Reuse the central analyst registry with a commodity-only execution plan.
- [x] Add the initial technical/futures-contract analyst path.
- [x] Disable company financial statements and insider tools for commodity
      runs.
- [x] Route specific-contract market history separately from continuous-series
      context.
- [x] Produce a technical-only December corn foundation report that blocks
      unsupported numerical claims when core data is unavailable.
- [ ] Produce a price-and-indicator technical report after a contract-aware
      market-data adapter is configured.

## Tests

- [x] Add unit tests for contract parsing and normalization.
- [x] Add crop-year, first-notice, last-trade, and expiration tests.
- [x] Add typed-error tests for unknown and expired contracts.
- [x] Add state and evidence schema tests.
- [x] Add CLI tests for explicit commodity inputs.
- [x] Add prompt/tool tests proving commodity runs never request company
      financial statements.
- [x] Keep all upstream stock and crypto tests passing.

## Acceptance criteria

- [ ] One command performs price-and-indicator analysis of an explicit December
      corn contract (blocked on the market-data adapter).
- [x] The commodity run never requests company financial statements.
- [x] Unknown and expired contracts fail with typed errors.
- [x] Continuous and specific-contract series are clearly distinguished.
- [x] The evidence-package skeleton is immutable and versioned.
- [x] Stock and crypto paths remain functional unless a breaking decision is
      explicitly documented.
