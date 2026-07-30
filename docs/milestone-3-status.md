# Milestone 3 official-data progress

## Implemented

- Added a shared immutable provenance contract for official observations.
- Added USDA WASDE archive discovery and U.S. corn balance-sheet parsing.
- Added CFTC Legacy Futures Only corn-positioning retrieval and normalization.
- Added EIA weekly U.S. fuel-ethanol production and stocks as explicit corn
  demand proxies.
- Selected records by conservative `available_at` timestamps, not merely by
  report period.
- Added raw official XML/JSON archives and SHA-256 hashes to every live run.
- Added stable fact IDs and deterministic supply/demand and positioning reports.
- Added future-release, revision-vintage, and CFTC shutdown-backlog leakage
  tests.

## Point-in-time policies

USDA releases are treated as available at noon America/New_York on the official
release date. A revised `v2` file is conservatively unavailable until the
following day, with the same rule extended for later version suffixes.

CFTC Legacy reports normally describe Tuesday positions and are published
later. GrainAgents conservatively waits seven calendar days after the report
date at 3:30 p.m. America/New_York. Reports affected by the 2025 federal
government shutdown are rejected until an exact official catch-up publication
calendar is encoded.

These policies intentionally trade some recency for leakage safety.

EIA's current API is used only when the analysis date is the actual run date.
Historical as-of calls are refused because the endpoint is not a vintage
database. Weekly observations are conservatively delayed seven calendar days,
and the archived response from each live run is retained for future replay.

## Live verification

On 2026-07-30, the live adapters retrieved:

- USDA WASDE dated 2026-07-10 for the 2026/27 U.S. corn crop year, including
  production, supply, use, exports, ending stocks, and average farm price.
- CFTC Legacy Futures Only corn positions reported for 2026-07-21, explicitly
  labelled as an aggregate across all corn delivery months.
- EIA U.S. fuel-ethanol production and ending stocks for the latest
  conservatively available week.

## Current limitations

- WASDE archive discovery currently uses the releases on the archive's first
  page; older historical runs fail safely rather than substituting a newer
  vintage.
- CFTC retrieval currently requests the latest 80 reports; older historical
  runs fail safely.
- CFTC positioning is market-level, not specific to one delivery contract.
- FAS export sales, production-weighted weather/drought, seasonal baselines,
  probability calibration, and forecast reports remain incomplete.
- The run remains publication-blocked and requires human approval.
