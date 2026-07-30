# Milestone 1: Commodity foundation

This checklist is the initial implementation backlog for the corn-first
commodity foundation. Work must be split into focused `agent/` branches and keep
the upstream stock and crypto test suite passing.

## Domain model

- [ ] Add `commodity_future` as an explicit asset type.
- [ ] Define typed commodity, exchange, delivery month, and contract metadata
      models.
- [ ] Implement strict symbols for `ZC`, `ZS`, and `ZW`, beginning with December
      corn.
- [ ] Map delivery month and year to crop year without conflating continuous and
      delivery-specific series.
- [ ] Validate first-notice and last-trade dates.
- [ ] Raise typed errors for malformed, unknown, and expired contracts.

## State and evidence

- [ ] Add a commodity-specific LangGraph state schema.
- [ ] Add the immutable evidence-package schema with stable fact and source IDs.
- [ ] Record commodity, contract, crop year, as-of timestamp, horizons, and
      evidence URI in run state.
- [ ] Add freshness, contradiction, unit, and point-in-time quality fields.

## CLI and configuration

- [ ] Add commodity, contract, as-of, horizons, analysts, research depth, and
      output format inputs.
- [ ] Add commodity configuration and environment-variable overlays.
- [ ] Preserve current stock and crypto CLI behavior.
- [ ] Include commodity and contract fields in checkpoint identity.

## Graph and tools

- [ ] Add a central commodity analyst registry.
- [ ] Add the initial technical/futures-curve analyst path.
- [ ] Disable company financial statements and insider tools for commodity
      runs.
- [ ] Route specific-contract market history separately from continuous-series
      context.
- [ ] Produce a technical-only December corn report from an explicit contract.

## Tests

- [ ] Add unit tests for contract parsing and normalization.
- [ ] Add crop-year, first-notice, last-trade, and expiration tests.
- [ ] Add typed-error tests for unknown and expired contracts.
- [ ] Add state and evidence schema tests.
- [ ] Add CLI tests for explicit commodity inputs.
- [ ] Add prompt/tool tests proving commodity runs never request company
      financial statements.
- [ ] Keep all upstream stock and crypto tests passing.

## Acceptance criteria

- [ ] One command analyzes an explicit December corn contract.
- [ ] The run never requests company financial statements.
- [ ] Unknown and expired contracts fail with typed errors.
- [ ] Continuous and specific-contract series are clearly distinguished.
- [ ] The evidence-package skeleton is immutable and versioned.
- [ ] Stock and crypto paths remain functional unless a breaking decision is
      explicitly documented.
