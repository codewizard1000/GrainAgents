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
  missing news/macro evidence, and incomplete export-demand evidence.
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

The 2026-07-30 `ZCZ26` run generated a neutral December corn draft with a
69.7% deterministic base-scenario probability and a calibrated 20-session
range of $3.949627-$5.466114.

The approval gate correctly refused the live run and wrote no approval record.
Its current blockers are:

- incomplete weather evidence
- unverified market-data redistribution rights
- research-only forecast status
- unavailable grain-news and macro evidence
- unavailable weekly export-sales and export-inspection evidence

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
- The live EIA credential remains absent from all generated artifacts.

## Current limitations

- The news report is an explicit unavailable marker, not a news analyst.
- Change-from-prior-report cannot be calculated until an earlier approved
  publication exists.
- A true multi-year seasonal comparison is not available; the current chart is
  an explicitly labelled indexed exact-contract history.
- Scenario-probability change cannot be plotted until an approved prior report
  exists.
- Weather anomalies, a 14-day weather layer, yield impact, export sales, and
  export inspections remain missing.
- The current Databento/exchange license scope is internal testing only, so
  publication must remain blocked regardless of editorial approval.
- Soybean and wheat publication renderers are not implemented in this slice.

## Next publication slice

- Connect export inspections and grain-news evidence before making the draft
  eligible for approval.
- Complete weather anomalies, 14-day forecasts, and yield-impact evidence.
- Start saving forecast vintages for genuine future-outcome scoring.
