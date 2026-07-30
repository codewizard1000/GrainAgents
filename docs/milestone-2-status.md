# Milestone 2 market-data progress

## Implemented

- Added a Databento historical-data adapter for exact CBOT corn, soybean, and
  Chicago SRW wheat delivery contracts.
- Mapped GrainAgents symbols such as `ZCZ26`, `ZSX26`, and `ZWZ26` to
  Databento raw symbols only after resolving a single instrument ID.
- Added daily OHLCV, settlement, cleared-volume, open-interest, and instrument
  definition retrieval from `GLBX.MDP3`.
- Normalized CBOT price fields from cents per bushel to USD per bushel.
- Preserved event and availability timestamps for point-in-time analysis.
- Added a non-interactive `grainagents market-data` diagnostic command.
- Added fixture-backed unit tests that make no billable network requests.
- Wired exact-contract history into `grainagents analyze`.
- Added deterministic technical indicators, a five-contract active futures
  curve, stable fact IDs, and a source audit.
- Added normalized Parquet and provider-response archives to each run.
- Added current-session protection so a current-day run uses the latest
  completed daily session.
- Captured Databento data-quality warnings in the evidence package.

## Design decisions

- Delivery-specific symbols remain the public identity. A continuous futures
  series cannot silently replace the requested tradable contract.
- One-digit vendor years are resolved over the requested date range. Multiple
  instrument IDs fail loudly instead of guessing the decade.
- The adapter retrieves definitions and statistics separately because daily
  OHLCV does not contain settlement or open interest.
- Provider output is labelled `internal_testing_only` until redistribution and
  publication rights are explicitly established.

## Verification

On 2026-07-30, authenticated live smoke tests returned data for:

| GrainAgents symbol | Databento raw symbol | Commodity |
|---|---|---|
| `ZCZ26` | `ZCZ6` | December corn |
| `ZSX26` | `ZSX6` | November soybeans |
| `ZWZ26` | `ZWZ6` | December Chicago SRW wheat |

The live checks confirmed exact instrument resolution, daily bars, settlement,
exchange metadata, and contract expiration metadata. Estimated Databento cost
for the small verification query was below one cent.

## Technical vertical-slice verification

On 2026-07-30, an authenticated end-to-end `ZCZ26` run produced:

- 310 exact-contract daily observations through 2026-07-29
- 5 active corn curve points
- 37 evidence facts
- a deterministic evidence-linked Markdown report
- `technical_ready_publication_blocked` status

The run remained non-publishable as designed.

## Current limitations

- A current trading day's daily bar may be incomplete before the session and
  clearing cycle finish, so the pipeline deliberately selects the prior
  completed weekday.
- First-notice dates currently come from GrainAgents contract rules rather than
  a separately archived exchange notice-calendar source.
- The provider archive contains normalized Databento responses rather than the
  vendor's native DBN byte stream.
- Databento can take several minutes to serve the complete historical and curve
  request.
- No seasonal model, probabilistic forecast, fundamental evidence, or
  newsletter publication path is complete.

## Security and licensing

The API key is loaded from `DATABENTO_API_KEY`, remains in the git-ignored
`.env` file, and is never included in normalized output. Users must confirm
their Databento plan and exchange entitlements before redistributing derived or
raw market data.

## Next work

Add point-in-time WASDE, CFTC COT, export-sales, ethanol, weather, and drought
adapters, then build transparent statistical forecast baselines and leakage
tests for the December corn vertical slice.
