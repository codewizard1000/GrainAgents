# Milestone 1 progress report

Milestone 1's commodity foundation is implemented and tested. Live numerical
technical analysis remains deliberately blocked until a delivery-specific,
publication-licensed market-data adapter is configured.

## Files changed

- Added `tradingagents/commodities/` for contract models, evidence contracts,
  and commodity tools.
- Added `cli/grain.py` and the `grainagents` console entry point.
- Extended asset detection, configuration, state, graph setup, propagation,
  instrument context, and the market analyst for `commodity_future`.
- Added contract, evidence, CLI, graph-path, prompt/tool-isolation, and
  configuration tests.

## Architecture decisions

- Commodity runs use `GrainAgentState`, not overloaded stock fields.
- Milestone 1 allows only the technical/futures-contract analyst and ends the
  graph before stock-oriented debate, trader, risk, and portfolio nodes.
- Delivery-specific contracts are never normalized to continuous symbols.
- The evidence package is immutable and records a blocking quality status when
  core data is missing.
- The CLI creates reproducible artifacts without requiring an LLM or pretending
  that missing price data exists.

## Tests and results

- Ruff checks: passed.
- Full pytest suite: 599 collected, 597 passed, 2 skipped.
- Skips: optional Bedrock dependency and an unconfigured live DeepSeek API test.
- Installed-command smoke test: passed for `ZCZ26` as of 2026-07-30.

## Data sources connected

None. Contract metadata is deterministic domain configuration. The
`get_contract_history` boundary exists but raises a typed
`VendorNotConfiguredError` until a suitable adapter is implemented.

## Known limitations

- No settlement, volume, open-interest, curve, or options data is connected.
- No price or indicator calculations are published in foundation runs.
- First-notice and last-trade calculations currently skip weekends but do not
  yet use an official exchange-holiday calendar. The validated `ZCZ26` dates
  are correct, but the rule must be replaced or cross-checked before broad
  production use.
- The evidence skeleton has empty fact and source collections; stable populated
  fact/source records arrive with the official-data foundation.
- Commodity analyst wiring is covered by deterministic tests but has not been
  exercised against a live LLM and live contract-data vendor.
- Soybean and wheat parsing exists for registry completeness; the functional
  vertical slice remains corn-first.

## Security and licensing

- No credentials or API keys were added.
- Inputs are constrained to known roots, delivery months, years, dates, positive
  horizons, and safe contract-scoped output paths.
- Vendor display and redistribution rights must be verified before subscriber
  publication.

## Next milestone

Implement the corn-first contract-aware market-data adapter and fixture-backed
point-in-time ingestion. Then populate the evidence package, calculate verified
technical metrics, and complete the unchecked price-and-indicator acceptance
criterion before adding official USDA, CFTC, EIA, weather, or drought adapters.

## User decision required

Select a delivery-specific futures market-data provider whose license permits
newsletter display and redistribution. No provider choice is assumed in this
branch.
