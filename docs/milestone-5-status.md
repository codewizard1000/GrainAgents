# Milestone 5 publication progress

## Implemented and tested

- Added a deterministic publication bundle built only from the immutable
  evidence package and deterministic forecast/scenario outputs.
- Added `newsletter.md`, `final_outlook.json`, `bull_bear_debate.md`,
  `risk_report.md`, `news_report.md`, and `publication_status.json`.
- Added stable fact and model-output references throughout numerical
  newsletter claims. Unknown references fail validation.
- Changed `--output newsletter` to return the actual newsletter draft when the
  current corn official-data prerequisites are present. `--output markdown`
  continues to return the technical report.
- Added blocker detection for incomplete core evidence, stale or contradictory
  evidence, internal-testing market-data licensing, research-only forecasts,
  missing grain-news event evidence, and incomplete export-demand evidence.
- Added archived, evidence-linked FRED observations for the broad U.S. dollar,
  WTI crude, the 10-year Treasury yield, and the effective federal funds rate.
  The public CSV adapter needs no API key, refuses historical replay, and uses
  a conservative seven-day availability buffer.
- Added archived Federal Register regulatory events for selected grain
  transportation, biofuel, ethanol-market-access, fertilizer, and grain-
  regulation title queries. Each record links to the official GPO PDF and
  receives a stable fact ID. The feed remains explicitly partial.
- Added a separate `approve-publication` command. It refuses any run with a
  blocker and never performs external publication. For an unblocked run it
  records the human editor, timestamp, note, and SHA-256 of the exact approved
  artifact.
- Added publication state and artifacts to the run manifest.
- Added eight deterministic PNG charts: price/technicals, futures curve, CFTC
  positioning, indexed exact-contract history, forecast fan, scenario
  probabilities, drought exposure, and stocks-to-use.
- Added `charts_manifest.json` with contract, as-of timestamp, unit, sources,
  evidence references, status, limitation, and SHA-256 for every chart.
- Added exact-contract discovery of the most recent earlier approved outlook
  and deterministic `prior_report_comparison.json`.
- Embedded the chart package into the newsletter at the relevant sections.

## Live verification

The 2026-07-31 `ZCZ26` run generated a neutral December corn draft with a
72.1% deterministic base-scenario probability and a calibrated 20-session
range of $3.934833-$5.400680. The run archived all four FRED CSV series and
rendered their July 23 observations with stable fact references. It also
archived 11 matched Federal Register documents and rendered 11 unique event
facts without assigning directional market impact.
The same run archived the dated July 30 CPC 8-14 day outlook, produced eight
weather-outlook facts, and removed `14_day_forecast` from the weather gaps.

The approval gate correctly refused the live run and wrote no approval record.
Its current blockers are:

- incomplete weather evidence
- unverified market-data redistribution rights
- research-only forecast status
- incomplete shipping, international-policy, and broader grain-news coverage
- incomplete multi-year seasonal comparison

The live draft remains `publication_ready: false`.

The live chart package generated eight visually inspected PNG files. Five are
ready or research-ready; the seasonal chart is explicitly partial because a
multi-year contract-month panel is unavailable, the weather chart remains
partial with the weather evidence, and scenario probabilities are
current-only because no earlier approved report exists.

`prior_report_comparison.json` correctly reports
`no_prior_approved_report`; a blocked or unapproved draft is never used as the
comparison baseline.

## Architecture decisions

- Publication prose is deterministic in this slice. No LLM is allowed to
  calculate or introduce a numerical claim.
- Facts and deterministic model outputs use separate citation namespaces:
  `fact_*` and `output_*`.
- Analysis and approval are separate commands. Analysis can create only a
  blocked draft; it cannot approve or publish it.
- Approval creates a local approved artifact and audit record but does not send
  email, update a newsletter platform, or otherwise publish externally.
- Charts are rendered locally with Matplotlib's non-interactive backend. Chart
  metadata references are validated against fact and deterministic-output IDs
  before the manifest is accepted.
- Prior-report comparison is exact-contract only and considers only earlier
  runs whose publication status is approved and publication-ready.

## Tests and verification

- Publication unit tests cover blocker detection, evidence/model-output
  references, and horizon-specific forecast fact selection.
- CLI tests cover artifact generation, newsletter routing, blocked approval,
  successful human approval, and artifact hashing.
- Chart tests verify PNG signatures, non-empty render output, required metadata,
  evidence references, and partial-status disclosures.
- Comparison tests verify that the latest earlier approved report is selected
  while newer blocked drafts are ignored.
- Macro tests verify conservative availability selection, archived public CSV
  payloads, historical-replay refusal, blocker separation, and publication
  citations.
- Regulatory-event tests verify exact title filtering, next-day availability,
  historical-replay refusal, pipeline merging, unique event facts, and
  evidence-linked rendering.
- CPC tests verify dated KMZ selection, KML polygon classification, intended-
  acreage weighting, historical-replay refusal, pipeline merging, and cited
  weather/newsletter rendering.
- The live EIA credential remains absent from all generated artifacts.
- The full suite passes with 650 tests, 2 optional skips, and clean Ruff lint.

## Current limitations

- The news report contains official macro context and selected Federal Register
  regulatory events. Black Sea shipping, river and port disruptions, broader
  sanctions, China policy, and international crop estimates remain missing.
- Change-from-prior-report cannot be calculated until an earlier approved
  publication exists.
- A true multi-year seasonal comparison is not available; the current chart is
  an explicitly labelled indexed exact-contract history.
- Scenario-probability change cannot be plotted until an approved prior report
  exists.
- Weather anomaly magnitudes, calibrated weather risk, and yield impact remain
  missing. The CPC 8-14 day probability layer, export sales, and export
  inspections are connected for current runs.
- The current Databento/exchange license scope is internal testing only, so
  publication must remain blocked regardless of editorial approval.
- Soybean and wheat publication renderers are not implemented in this slice.

## Next publication slice

- Add official shipping, river/port, sanctions, China-policy, and international
  crop-estimate sources to broaden the partial grain-news layer.
- Complete weather anomaly magnitudes and yield-impact evidence.
- Start saving forecast vintages for genuine future-outcome scoring.
