# Milestone 3 official-data progress

## Implemented

- Added a shared immutable provenance contract for official observations.
- Added USDA WASDE archive discovery and U.S. corn balance-sheet parsing.
- Added CFTC Legacy Futures Only corn-positioning retrieval and normalization.
- Added EIA weekly U.S. fuel-ethanol production and stocks as explicit corn
  demand proxies.
- Added USDA FAS weekly corn export sales, including current- and next-
  marketing-year commitments aggregated across reported destinations.
- Added USDA AMS/FGIS weekly corn export inspections from the Open Ag
  Transport dataset, including four-week and marketing-year-to-date totals.
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

FAS export sales follow the same current-run-only rule because the API can
revise earlier observations. GrainAgents uses FAS's commodity-level release
calendar and the official Thursday 8:30 a.m. America/New_York publication time,
then archives both the release-calendar and export payloads. A free
`USDA_FAS_API_KEY` from api.data.gov is required.

AMS/FGIS inspections also use a current-run-only rule. Availability comes from
the Socrata dataset's exact `rowsUpdatedAt` timestamp rather than an assumed
Monday schedule, which safely accommodates holidays and corrections.
Marketing-year totals filter on certification date so a boundary week's
pre-September inspections are not assigned to the new corn marketing year.

## Live verification

On 2026-07-30, the live adapters retrieved:

- USDA WASDE dated 2026-07-10 for the 2026/27 U.S. corn crop year, including
  production, supply, use, exports, ending stocks, and average farm price.
- CFTC Legacy Futures Only corn positions reported for 2026-07-21, explicitly
  labelled as an aggregate across all corn delivery months.
- EIA U.S. fuel-ethanol production and ending stocks for the latest
  conservatively available week.
- USDA AMS/FGIS corn export inspections for the week ending 2026-07-23:
  1,488,028 metric tons for the week, a 1,597,617 metric-ton four-week
  average, and 75,323,661 metric tons marketing-year-to-date.

The FAS adapter is fixture-verified but is not yet live-verified in this
workspace because `USDA_FAS_API_KEY` has not been configured.

## Current limitations

- WASDE archive discovery currently uses the releases on the archive's first
  page; older historical runs fail safely rather than substituting a newer
  vintage.
- CFTC retrieval currently requests the latest 80 reports; older historical
  runs fail safely.
- CFTC positioning is market-level, not specific to one delivery contract.
- Complete weather anomalies and live probability calibration remain
  incomplete. The first price-only baseline ensemble is documented in the
  Milestone 4 status.
- The run remains publication-blocked and requires human approval.
