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
  missing news/macro evidence, and missing export-demand evidence.
- Added a separate `approve-publication` command. It refuses any run with a
  blocker and never performs external publication. For an unblocked run it
  records the human editor, timestamp, note, and SHA-256 of the exact approved
  artifact.
- Added publication state and artifacts to the run manifest.

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

## Architecture decisions

- Publication prose is deterministic in this slice. No LLM is allowed to
  calculate or introduce a numerical claim.
- Facts and deterministic model outputs use separate citation namespaces:
  `fact_*` and `output_*`.
- Analysis and approval are separate commands. Analysis can create only a
  blocked draft; it cannot approve or publish it.
- Approval creates a local approved artifact and audit record but does not send
  email, update a newsletter platform, or otherwise publish externally.

## Tests and verification

- Publication unit tests cover blocker detection, evidence/model-output
  references, and horizon-specific forecast fact selection.
- CLI tests cover artifact generation, newsletter routing, blocked approval,
  successful human approval, and artifact hashing.
- The live EIA credential remains absent from all generated artifacts.

## Current limitations

- The news report is an explicit unavailable marker, not a news analyst.
- Change-from-prior-report cannot be calculated until an earlier approved
  publication exists.
- Required charts are not generated yet.
- Weather anomalies, a 14-day weather layer, yield impact, export sales, and
  export inspections remain missing.
- The current Databento/exchange license scope is internal testing only, so
  publication must remain blocked regardless of editorial approval.
- Soybean and wheat publication renderers are not implemented in this slice.

## Next publication slice

- Generate evidence-linked charts with contract, date, units, and source.
- Add deterministic prior-report comparison.
- Connect export-demand and grain-news evidence before making the draft
  eligible for approval.
