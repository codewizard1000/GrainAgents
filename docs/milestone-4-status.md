# Milestone 4 weather and forecast progress

## Implemented

- Added current corn-area drought exposure from Drought.gov's USDA
  NASS/U.S. Drought Monitor overlay.
- Added a twelve-state National Weather Service seven-day point sample,
  weighted by USDA NASS 2026 intended corn acres.
- Added fixed nearby-station NOAA NCEI 1991-2020 daily temperature and
  precipitation normals for the same seven calendar dates, then computed
  intended-acreage-weighted sample anomalies. Daily precipitation normals are
  derived from consecutive month-to-date normal values.
- Added dated NOAA CPC 8-14 day temperature and precipitation GIS archives,
  classified at the same twelve points and weighted by intended corn acres.
  Mutable `latest` files are not used.
- Added the archived USDA NASS weekly Crop Progress text report with national
  corn-condition shares, silking, dough, prior-week comparisons, and acreage
  coverage.
- Added a deterministic condition-based weather-risk monitoring index. It
  weights very poor, poor, fair, good, and excellent shares at 100, 75, 50,
  25, and 0; reports weekly and annual changes; and is explicitly not labelled
  as yield calibrated.
- Added crop-stage-linked critical monitoring dates spanning the available
  seven-day NWS and 8-14-day CPC windows.
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
- Replaced fixed pooled-residual intervals with adaptive conformal intervals.
  Each origin uses the prior 30 normalized errors, a 60-session volatility
  scale available at that origin, and a fixed 0.02 adaptation rate.
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

After adding NCEI daily normals, the verified July 31-August 6 run returned:

- 27.497 mm sample-weighted forecast precipitation versus a 21.381 mm normal,
  a +6.116 mm anomaly
- 22.690 C sample-weighted forecast mean temperature versus a 23.927 C normal,
  a -1.237 C anomaly
- 13,022,339 bytes in the exact weather archive, including all twelve NCEI
  station CSV files

The July 30 CPC issue, valid August 7-13, placed 100% of sampled intended corn
acres in an above-normal temperature category. For precipitation, 86.03% fell
in below-normal and 13.97% in near-normal categories. Eight stable facts and
the exact dated temperature and precipitation KMZ payloads were archived.

The USDA NASS report released July 27 for the week ending July 26 reported:

- 63% of corn in good or excellent condition
- 12% in poor or very poor condition
- a 34.75 condition-based weather-risk index, 2.75 points worse than the prior
  week and 5.75 points worse than the prior year
- 78% silking versus a 74% five-year average
- 25% at dough versus a 22% five-year average
- 91% coverage of prior-year planted corn acreage across the 18 reported
  States

The exact report and archive-index HTML were preserved in a 139,965-byte raw
archive, and 12 stable crop-progress facts were added. The reproductive-stage
evidence sets July 31-August 13 as the current critical monitoring window.

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

The original pooled-residual intervals under-covered at all three horizons.
After adaptive conformal calibration, the same point-in-time evaluation
reported:

| Horizon | 50% coverage | 80% coverage | Current 80% interval | Confidence |
|---:|---:|---:|---:|---:|
| 5 days | 48.80% | 79.20% | $4.501685-$4.929664 | 68.53 |
| 20 days | 49.09% | 76.36% | $3.949627-$5.466114 | 34.09 |
| 60 days | 52.86% | 77.14% | $3.385848-$5.810174 | 24.11 |

Calibration is materially closer to the 50% and 80% targets, but the honest
cost is much wider medium- and long-horizon ranges. Confidence remains low
where the range is wide or observed coverage is below nominal.

## Current limitations

- The NWS layer samples one disclosed point per state and does not represent
  within-state spatial variation.
- The anomaly layer uses one fixed nearby airport station per state sample and
  intended-acreage weights; it is not a gridded, production-weighted field
  climatology and does not capture within-state variation.
- A calibrated weather risk score and yield-impact range are still missing, so
  weather remains `partial`.
- USDA ERS published an acreage-weighted monthly corn-yield equation using
  July temperature and nonlinear July precipitation. It is not applied to the
  current seven-day forecast because that would mix weekly anomalies with
  monthly coefficients. NOAA nClimGrid-Daily preliminary July area averages
  were complete only through July 26 during the July 31 run. The yield slice
  must wait for a point-in-time-complete July observation/forecast panel or
  implement a separately validated weekly model.
- Forecast models are currently price-only and do not yet ingest the official
  fundamental or weather features.
- Rolling backtests are now leak-safe and auditable, but they are not a
  substitute for scoring forecasts saved before outcomes occur. Adaptive
  conformal calibration is validated only on the available exact-contract
  history and must be monitored on saved future forecasts.
- The regression tree is deliberately small and price-only. Feature expansion
  should wait for vintage-safe fundamental and weather training panels.
- EIA's public `DEMO_KEY` can return HTTP 429 after repeated test calls. Set a
  free `EIA_API_KEY` for reliable unattended runs.
- Publication remains blocked and human approval remains mandatory.
