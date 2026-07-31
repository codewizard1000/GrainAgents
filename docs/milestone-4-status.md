# Milestone 4 weather and forecast progress

## Implemented

- Added current corn-area drought exposure from Drought.gov's USDA
  NASS/U.S. Drought Monitor overlay.
- Added a twelve-state National Weather Service seven-day point sample,
  weighted by USDA NASS 2026 intended corn acres.
- Added current-endpoint safety: current weather calls are rejected for
  historical as-of dates and NWS grid updates after the run timestamp are
  excluded.
- Added raw weather archives, source hashes, stable fact IDs, and a
  deterministic weather report.
- Added five transparent price baselines: random walk, weekly seasonal naive,
  long-run drift, local linear trend, and exponentially weighted daily change.
- Added a dependency-free shallow regression-tree candidate using lagged price
  changes, moving-average distance, and realized volatility.
- Added a strict complexity gate: the tree receives ensemble weight only when
  its rolling point-in-time MAE is lower than every transparent baseline for
  that horizon. Rejected trees remain visible with zero weight.
- Added rolling horizon-specific validation, inverse-MAE ensemble weights,
  empirical residual prediction intervals, support/resistance probabilities,
  excursion estimates, confidence and disagreement scores.
- Added an expanding-window performance registry with MAE, MASE, directional
  accuracy, 50% and 80% interval coverage, and quantile loss. At each scored
  origin, model weights and interval residuals use only earlier outcomes.
- Added under-coverage warnings and proportional confidence penalties when the
  rolling 80% interval achieves less than its nominal coverage.
- Added deterministic scenarios constrained by the 20-trading-day forecast
  distribution.
- Added quantitative forecast, scenario, and forecast-report artifacts.

## Live verification

On 2026-07-30, the official weather adapter returned:

- 39% of corn area in moderate drought or worse
- 20% in severe drought or worse
- 6% in extreme drought or worse
- 28.378 mm acreage-sample-weighted seven-day precipitation
- 23.229 C acreage-sample-weighted seven-day mean temperature
- 82.968% coverage of USDA's intended 2026 corn acres

Using the archived 310-observation `ZCZ26` history, the baseline ensemble
produced deterministic 5-, 20-, and 60-trading-day distributions. The
60-trading-day result had materially lower confidence and higher model
disagreement than the shorter horizons, as expected from the wider residual
distribution.

The first regression-tree live evaluation produced:

- 5 days: tree MAE 0.082976 versus best baseline MAE 0.071716; rejected
- 20 days: tree MAE 0.190172 versus best baseline MAE 0.171574; rejected
- 60 days: tree MAE 0.201417 versus best baseline MAE 0.223250; admitted with
  21.4267% ensemble weight

This is evidence that the complexity gate is active rather than an assumption
that a more complex model must improve every horizon.

The leak-safe expanding-window registry reported:

| Horizon | Scored forecasts | MAE | MASE | Directional accuracy | 80% coverage | Quantile loss |
|---:|---:|---:|---:|---:|---:|---:|
| 5 days | 155 | 0.078162 | 3.120279 | 52.26% | 63.20% | 0.027289 |
| 20 days | 140 | 0.219011 | 8.854133 | 39.29% | 51.82% | 0.077188 |
| 60 days | 100 | 0.300520 | 12.908352 | 35.00% | 52.86% | 0.106467 |

All three nominal 80% intervals under-covered. GrainAgents now records three
quality warnings and reduced the displayed confidence scores to 56.50, 30.12,
and 28.31 respectively. These results support keeping publication blocked.

## Current limitations

- The NWS layer samples one disclosed point per state and does not represent
  within-state spatial variation.
- Production-weighted rainfall and temperature anomalies need a vintage-safe
  1991-2020 normal and observation pipeline.
- A 14-day weather layer, calibrated weather risk score, and yield-impact range
  are still missing, so weather remains `partial`.
- Forecast models are currently price-only and do not yet ingest the official
  fundamental or weather features.
- Rolling backtests are now leak-safe and auditable, but they are not a
  substitute for scoring forecasts saved before outcomes occur. The current
  interval calibration is materially inadequate.
- The regression tree is deliberately small and price-only. Feature expansion
  should wait for vintage-safe fundamental and weather training panels.
- EIA's public `DEMO_KEY` can return HTTP 429 after repeated test calls. Set a
  free `EIA_API_KEY` for reliable unattended runs.
- Publication remains blocked and human approval remains mandatory.
