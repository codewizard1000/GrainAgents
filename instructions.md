# GrainAgents Implementation Instructions

## Mission

Create **GrainAgents**, a commodity-market research and probabilistic forecasting system for corn, soybeans, and wheat by forking and adapting the open-source TradingAgents repository:

- Upstream repository: `TauricResearch/TradingAgents`
- Desired fork name: `GrainAgents`
- Primary initial user: publisher of a newsletter read by more than 6,000 farmers
- Primary deliverable: evidence-grounded, contract-specific grain-market outlooks suitable for newsletter publication

GrainAgents is **not** initially an automated trading bot. It is a research, scenario-analysis, forecasting, and publication system.

The system must generate:

- Contract-specific analysis for corn, soybeans, and wheat
- Bull, base, and bear price scenarios
- Probabilities and prediction intervals rather than unsupported point predictions
- Clear explanations of what changed since the previous report
- Technical, fundamental, weather, demand, positioning, and macro perspectives
- Newsletter-ready Markdown, structured JSON, source records, and charts
- Historical forecast scoring and calibration

---

# 1. Agent Operating Rules

Follow these rules throughout the project.

1. **Inspect before editing.** Read the upstream architecture, tests, configuration, dataflows, graph setup, agent state, report generation, and license before changing code.
2. **Preserve attribution and license files.** This is a derivative of TradingAgents. Do not remove upstream copyright, license, notices, acknowledgments, or repository history.
3. **Do not develop directly on `main`.** Create focused branches using the prefix `agent/`.
4. **Do not attempt the entire system in one change.** Implement the milestones in this document sequentially.
5. **Keep the project runnable after every milestone.** Add tests with each functional change.
6. **Use typed schemas for all structured data.** Prefer Pydantic models or typed dataclasses at system boundaries.
7. **Never let an LLM invent a number.** Every publishable numerical claim must be traceable to an evidence record or deterministic model output.
8. **Separate facts, model outputs, and interpretation.** Store them as different data types and label them in reports.
9. **Use point-in-time data.** Historical tests may use only information available as of the forecast timestamp.
10. **Fail loudly on stale, unavailable, malformed, or contradictory core data.** Do not silently fabricate or substitute values.
11. **Make outputs reproducible.** Record code version, model version, prompt version, data timestamps, inputs, and configuration for every run.
12. **Do not publish automatically in v1.** Require an explicit human approval step.
13. **Do not add live order execution in v1.** GrainAgents produces research, not personalized trading instructions.
14. **Avoid unnecessary rewrites of stable upstream infrastructure.** Reuse TradingAgents' LLM clients, checkpointing, reporting, and graph utilities when they fit.
15. **Document assumptions and unresolved choices.** Do not conceal missing data or uncertain mappings.

---

# 2. Repository Creation and Git Workflow

## 2.1 Create the fork

Using an authenticated GitHub CLI session, create and clone the fork:

```powershell
gh auth status
gh repo fork TauricResearch/TradingAgents --fork-name GrainAgents --clone=false
gh repo clone YOUR_GITHUB_LOGIN/GrainAgents GrainAgents
cd GrainAgents
```

Confirm the remotes:

```powershell
git remote -v
```

Expected remotes:

- `origin`: the user's `GrainAgents` fork
- `upstream`: `TauricResearch/TradingAgents`

If `upstream` is missing, add it:

```powershell
git remote add upstream https://github.com/TauricResearch/TradingAgents.git
```

## 2.2 Initial blueprint branch

Create the initial branch:

```powershell
git switch -c agent/grainagents-blueprint
```

Add this file to the repository root as `instructions.md`, then commit and push:

```powershell
git add instructions.md
git commit -m "Add GrainAgents implementation blueprint"
git push -u origin agent/grainagents-blueprint
```

Set an appropriate repository description:

```powershell
gh repo edit YOUR_GITHUB_LOGIN/GrainAgents --description "Multi-agent grain-market research and probabilistic forecasting for corn, soybeans, and wheat"
```

Do not open a pull request against the upstream TradingAgents repository unless the user explicitly requests it. Development PRs should normally target the fork's `main` branch.

---

# 3. Product Scope

## 3.1 Initial commodities

Support these commodities first:

| Commodity | Futures root | Initial priority contract |
|---|---:|---|
| Corn | `ZC` | December new-crop corn |
| Soybeans | `ZS` | November new-crop soybeans |
| Chicago SRW wheat | `ZW` | July, September, or December, selected explicitly |

Phase 2 may add:

| Commodity | Futures root |
|---|---:|
| Kansas City HRW wheat | `KE` |
| Soybean meal | `ZM` |
| Soybean oil | `ZL` |

## 3.2 Forecast horizons

For every selected futures contract, support:

- 5 trading days
- 20 trading days
- 60 trading days
- Optional user-specified target date
- Optional seasonal milestone, such as pollination, harvest, or contract expiration

## 3.3 Product outputs

Each completed run must produce:

- Immutable evidence package
- Individual analyst reports
- Quantitative forecast JSON
- Bull and bear cases
- Scenario probabilities
- Risk review
- Final market outlook
- Newsletter-ready Markdown
- Source audit file
- Charts
- Machine-readable run manifest

---

# 4. Architectural Principle

Use this separation of responsibilities:

> Deterministic data pipelines and forecasting models calculate facts and price distributions. LLM agents interpret, challenge, synthesize, and communicate them.

LLMs must not independently calculate or invent:

- Futures settlement prices
- USDA figures
- CFTC positions
- Export totals
- Ethanol production
- Weather observations
- Model probabilities
- Prediction intervals
- Contract expiration dates

The LLM layer may derive qualitative inferences from verified facts, but every inference must be labeled as an inference.

---

# 5. Target System Architecture

```text
Official APIs       Market Data       News/Documents
─────────────       ───────────       ──────────────
USDA NASS           Futures prices    USDA releases
USDA WASDE          Futures curves    FAS GAIN reports
USDA FAS            Options           Government releases
USDA ERS            Open interest     Licensed commodity news
USDA AMS            Cash bids
CFTC COT
NOAA / USDM
EIA / FRED
       │                 │                  │
       └─────────────────┼──────────────────┘
                         ▼
              INGESTION AND RAW ARCHIVE
                         ▼
              POINT-IN-TIME DATA LAYER
                         ▼
                 FEATURE GENERATION
                         ▼
                FORECASTING ENSEMBLE
                         ▼
                  EVIDENCE PACKAGE
                         ▼
                  LANGGRAPH AGENTS
                         ▼
              FACT AND CONSISTENCY CHECK
                         ▼
                 PUBLICATION OUTPUT
```

Recommended v1 technology choices:

- Python 3.12 or the upstream-supported Python version
- LangGraph and existing TradingAgents LLM abstraction
- Pydantic for data contracts
- PostgreSQL for operational structured data
- S3-compatible object storage for raw files and generated artifacts
- Parquet for analytical datasets
- DuckDB for local research and backtesting
- Pandas or Polars for transformations
- MLflow or a lightweight internal model registry for model metadata
- FastAPI only when an API surface is needed
- Typer or the existing CLI framework for command-line operation

Do not introduce infrastructure merely because it is fashionable. Keep the v1 deployment operable by a small team.

---

# 6. Adaptation of TradingAgents

Retain useful upstream components:

- LangGraph orchestration
- Tool-calling analyst nodes
- Bull and bear debate
- Risk review pattern
- Multiple LLM provider support
- Checkpoint recovery
- Persistent run memory
- Markdown report generation
- Configuration and environment-variable patterns
- Typed vendor error handling

Replace stock-oriented concepts as follows:

| TradingAgents concept | GrainAgents concept |
|---|---|
| Company/ticker | Commodity and specific futures contract |
| Market Analyst | Technical and Futures-Curve Analyst |
| Sentiment Analyst | Positioning and Market Sentiment Analyst |
| News Analyst | Grain News and Macro Analyst |
| Fundamentals Analyst | Supply-and-Demand Analyst |
| Bull Researcher | Bull Case Researcher |
| Bear Researcher | Bear Case Researcher |
| Research Manager | Scenario Manager |
| Trader | Producer Market Outlook Agent |
| Aggressive Risk Analyst | Upside Risk Analyst |
| Neutral Risk Analyst | Base-Case Risk Analyst |
| Conservative Risk Analyst | Downside Risk Analyst |
| Portfolio Manager | Final Outlook and Editorial Manager |
| BUY/HOLD/SELL | Bullish/Neutral/Bearish outlook plus scenario ranges |
| Alpha versus SPY | Forecast accuracy, interval coverage, and calibration |

Do not force commodities through the stock fundamentals pipeline.

---

# 7. Agent Team

## 7.1 Technical and Futures-Curve Analyst

Analyze:

- Specific contract price trend
- 5-, 20-, 50-, 100-, and 200-day moving averages
- RSI
- MACD
- Stochastic oscillator
- ATR
- Bollinger Bands
- Support and resistance
- Volume and open interest
- Calendar spreads
- Carry, contango, and inversion
- Seasonal price tendencies
- Volatility regime
- Options-implied ranges when available
- Days to first notice and expiration

The report must clearly distinguish:

1. The specific tradable contract
2. The current futures curve
3. A continuous historical series used only for long-horizon analysis

Never describe a continuous series as one continuously tradable contract.

## 7.2 Supply-and-Demand Analyst

### Corn

Analyze:

- Planted and harvested acres
- Yield
- Production
- Beginning stocks
- Imports
- Feed and residual use
- Ethanol and industrial use
- Exports
- Ending stocks
- Stocks-to-use ratio
- Global balances
- Competitor production

### Soybeans

Analyze:

- Acres, yield, and production
- Beginning and ending stocks
- Domestic crush
- Exports
- Residual use
- Soybean meal demand
- Soybean oil demand
- Brazil and Argentina production
- China imports
- Global stocks

### Wheat

Analyze:

- Winter and spring wheat production
- Production by class when available
- Domestic food and feed use
- Exports
- Ending stocks
- Black Sea, EU, Canadian, Australian, and Argentine production
- Global trade and stocks

## 7.3 Weather-and-Yield Analyst

Analyze:

- 7- and 14-day weather forecasts
- Rainfall and temperature anomalies
- Soil moisture
- Drought severity
- Growing-degree days
- Planting and harvest windows
- Vegetation conditions
- Crop-condition ratings
- Pollination, pod-setting, frost, and freeze risks
- South American weather
- Historical weather and yield analog years

Weight U.S. weather features by planted acres or expected production. Do not average all counties or states equally.

Required output:

- Weather risk score from 0 to 100
- Change from previous report
- Production-weighted rainfall anomaly
- Production-weighted temperature anomaly
- Yield-impact range
- Confidence score
- Critical forecast dates

## 7.4 Demand Analyst

### Corn

- Weekly export sales
- Export inspections
- Mexico and China purchases
- Ethanol production and inventories
- Feed indicators
- Livestock context
- Domestic basis and river demand when available

### Soybeans

- Weekly export sales
- China purchases
- Crush pace
- Meal exports
- Soybean oil use
- Renewable-diesel influences
- Brazil export competition

### Wheat

- Weekly export sales by class
- Export inspections
- International tenders
- Relative U.S., Black Sea, and EU pricing
- Freight and currency competitiveness

## 7.5 Positioning and Market Sentiment Analyst

Analyze:

- Managed-money gross longs
- Managed-money gross shorts
- Managed-money net position
- Producer/merchant positions
- Swap-dealer positions
- Index-trader positions when applicable
- Weekly changes
- One-, three-, five-, and ten-year percentiles
- Open-interest changes
- Options skew
- Put/call activity
- Commercial versus speculative divergence
- Relevant news sentiment

Always store and display both the COT position date and publication timestamp.

## 7.6 Grain News and Macro Analyst

Prioritize sources in this order:

1. USDA
2. CFTC
3. EIA
4. NOAA and U.S. Drought Monitor
5. USDA FAS GAIN reports
6. Other government and central-bank releases
7. Licensed commodity news
8. General web news

Analyze:

- Trade policy and tariffs
- Sanctions
- Black Sea shipping
- River and port conditions
- Currency movements
- Oil and ethanol economics
- Interest rates
- Fertilizer and input costs
- Chinese policy
- Biofuel mandates
- Government and major private crop estimates

## 7.7 Quantitative Forecast Analyst

This agent explains deterministic model outputs. It does not create them.

Required forecast fields:

- Median projected price
- 50% prediction interval
- 80% prediction interval
- Probability of exceeding selected resistance levels
- Probability of falling below selected support levels
- Expected maximum favorable excursion
- Expected maximum adverse excursion
- Forecast confidence
- Model disagreement score
- Important feature contributions when supported

---

# 8. Forecasting Engine

Use an ensemble. Do not use one model as the authoritative forecast.

## 8.1 Statistical baselines

Implement first:

- Seasonal naïve
- Drift
- Exponential smoothing
- AutoARIMA or an equivalent transparent autoregressive model
- Historical seasonal analogs

A complex model must outperform these baselines out of sample before receiving meaningful ensemble weight.

## 8.2 Tree models

Candidate models:

- LightGBM
- XGBoost
- CatBoost

Candidate features:

- Technical indicators
- Futures spreads
- Volatility
- Weather
- USDA balance changes
- COT positions
- Export sales
- Ethanol
- Currency and energy prices
- Calendar and seasonal variables

## 8.3 Neural time-series models

Candidate models using NeuralForecast or an equivalent maintained library:

- NHITS
- NBEATSx
- Temporal Fusion Transformer
- PatchTST

Begin with one or two models. Do not add every available model before establishing reliable baselines and point-in-time validation.

## 8.4 Market-implied models

When options data is available, include:

- Options-implied volatility
- Probability estimates derived from option prices
- Futures-curve structure
- Calendar spreads
- Put/call skew

## 8.5 Ensemble weighting

Calculate weights using rolling out-of-sample validation. Do not ask an LLM to select weights.

Track weights by:

- Commodity
- Contract month
- Forecast horizon
- Crop-season phase
- Volatility regime

Use constraints so that unstable recent performance cannot cause extreme weight changes without sufficient observations.

---

# 9. Futures Contract Methodology

## 9.1 Separate continuous and specific contracts

Use a documented continuous contract for long-horizon technical context only.

Use specific delivery contracts for:

- Forecasting
- Scenario ranges
- Backtesting
- Newsletter price targets
- Options analysis

Examples:

- `ZCZ26`: December 2026 corn
- `ZSX26`: November 2026 soybeans
- `ZWZ26`: December 2026 Chicago wheat

## 9.2 Contract-aware training panel

Train comparable observations based on:

- Commodity
- Delivery month
- Crop year
- Days to first notice
- Days to expiration
- Calendar date
- Crop-season stage

Example training unit:

```text
Commodity: corn
Delivery month: December
Observation timing: 180 calendar days before expiration
Target: 20-trading-day contract return
```

## 9.3 Required contract metadata

Store:

- Commodity
- Exchange
- Root
- Full contract symbol
- Delivery month
- Delivery year
- Crop year
- First-notice date
- Last-trade date
- Days to first notice
- Days to expiration
- Tick size
- Contract multiplier
- Volume
- Open interest
- Active-contract rank
- Old-crop/new-crop designation
- Continuous-series mapping rule
- Back-adjustment rule

---

# 10. Point-in-Time Data

Every historical run must use only information actually available as of its `as_of` timestamp.

A historical forecast must not use:

- Later USDA revisions
- Final acreage or yield figures unavailable at the time
- Realized future weather
- COT data not yet published
- Corrected source files published later
- Future contract rolls
- News released after the forecast timestamp

Every observation must support fields equivalent to:

```json
{
  "source": "USDA_WASDE",
  "series_id": "US_CORN_ENDING_STOCKS",
  "commodity": "corn",
  "market_year": "2026/27",
  "observation_date": "2026-07-10",
  "published_at": "2026-07-10T12:00:00-04:00",
  "available_at": "2026-07-10T12:00:00-04:00",
  "retrieved_at": "2026-07-10T12:03:41-04:00",
  "revision_number": 0,
  "value": 1.66,
  "unit": "billion_bushels",
  "status": "official_estimate"
}
```

Do not overwrite raw source responses. Corrections and revisions are new records linked to the prior record.

---

# 11. Data Sources and Adapters

Create provider interfaces so vendors can be replaced without rewriting agents.

## 11.1 Official data adapters

Plan adapters for:

- USDA NASS Quick Stats
- USDA WASDE machine-readable files
- USDA FAS PSD
- USDA FAS Export Sales
- USDA ERS datasets
- USDA AMS MyMarketNews
- CFTC COT and Public Reporting Environment
- NOAA/NWS observations and forecasts
- U.S. Drought Monitor
- EIA ethanol data
- FRED macroeconomic data

## 11.2 Market-data adapters

Prototype options may include free daily data, but production should use a contract-aware provider with licensing appropriate for newsletter publication.

The interface must support:

```text
get_contract_history
get_contract_metadata
get_curve_snapshot
get_options_chain
get_open_interest
get_continuous_history
get_cash_market_quotes
```

Do not hard-code a single commercial provider into agent prompts.

## 11.3 Vendor licensing

Before publishing vendor-derived data, charts, or tables to newsletter subscribers, verify that the selected plan permits the intended display and redistribution.

Store vendor attribution requirements in configuration and report metadata.

---

# 12. Storage Design

## 12.1 PostgreSQL

Use PostgreSQL for:

- Contract metadata
- Structured official observations
- Features
- Forecasts
- Agent runs
- Source references
- Publication versions
- Performance metrics

## 12.2 Object storage

Use S3-compatible object storage for:

- Raw API responses
- XML, CSV, XLSX, and text releases
- Weather grids
- Parquet datasets
- Model artifacts
- Generated charts
- Final report bundles

## 12.3 DuckDB

Use DuckDB for:

- Local analytical research
- Parquet queries
- Point-in-time training datasets
- Backtests
- Feature diagnostics

Do not introduce a separate enterprise feature-store product in v1.

---

# 13. Core Tables

Create migrations for tables equivalent to:

```text
commodities
contracts
contract_daily_prices
contract_intraday_prices
futures_curve_snapshots
options_snapshots

source_documents
raw_ingestion_events
economic_observations
usda_balance_sheets
crop_progress
export_sales
ethanol_statistics
cash_market_quotes
cot_positions
weather_observations
weather_forecasts
drought_statistics

model_features
forecast_runs
forecast_predictions
forecast_outcomes
model_performance

agent_runs
agent_reports
agent_evidence_links
outlook_reports
publication_versions
```

Universal provenance columns should include:

```text
source_name
source_record_id
observation_date
published_at
available_at
retrieved_at
revision_number
is_preliminary
is_projected
unit
quality_flag
raw_object_uri
```

Add appropriate uniqueness constraints, foreign keys, indexes, and commodity/date partitions only when justified by expected scale.

---

# 14. Evidence Package

Before any LLM analyst runs, generate one immutable evidence package.

Example:

```json
{
  "schema_version": "1.0",
  "run_id": "corn-zcz26-2026-07-30",
  "as_of": "2026-07-30T16:30:00-04:00",
  "instrument": {
    "commodity": "corn",
    "contract": "ZCZ26",
    "crop_year": "2026/27",
    "days_to_expiration": 137
  },
  "market": {},
  "curve": {},
  "technical": {},
  "supply_demand": {},
  "weather": {},
  "demand": {},
  "positioning": {},
  "macro": {},
  "forecast": {},
  "facts": [],
  "sources": [],
  "quality": {}
}
```

Each fact must have a stable ID.

Example analyst output contract:

```json
{
  "conclusion": "",
  "confidence": 0.72,
  "facts_used": ["fact_103", "fact_209"],
  "inferences": [],
  "missing_data": [],
  "risks": [],
  "directional_score": 18
}
```

Create a validator that:

- Extracts every numerical assertion from analyst outputs
- Confirms the number exists in the evidence package or deterministic derived metrics
- Confirms units and time periods match
- Rejects unsupported claims
- Flags stale or conflicting values
- Produces a source audit

---

# 15. LangGraph Workflow

Target workflow:

```text
START
  │
  ▼
Validate Request
  │
  ▼
Build Evidence Package
  │
  ├───────── Technical Analyst
  ├───────── Supply-and-Demand Analyst
  ├───────── Weather-and-Yield Analyst
  ├───────── Demand Analyst
  ├───────── Positioning Analyst
  ├───────── News-and-Macro Analyst
  └───────── Quant Forecast Analyst
                 │
                 ▼
          Evidence Validator
                 │
        ┌────────┴────────┐
        ▼                 ▼
 Bull Researcher     Bear Researcher
        └────────┬────────┘
                 ▼
          Scenario Manager
                 │
     ┌───────────┼───────────┐
     ▼           ▼           ▼
 Upside Risk   Base Risk   Downside Risk
     └───────────┼───────────┘
                 ▼
      Final Outlook Manager
                 │
                 ▼
        Newsletter Editor
                 │
                 ▼
                END
```

Run independent analysts in parallel after the evidence package is ready. Do not let one analyst's narrative bias another supposedly independent analyst.

The Scenario Manager may review all analyst outputs and the bull/bear debate, but it must use the deterministic forecast probabilities as constraints rather than freely inventing probabilities.

---

# 16. Exact Upstream Areas to Modify

Inspect current upstream paths before editing because file names and APIs may change.

## 16.1 `tradingagents/default_config.py`

Add commodity-specific configuration such as:

```python
"asset_type": "commodity_future",
"supported_commodities": ["corn", "soybeans", "wheat_srw"],
"forecast_horizons": [5, 20, 60],
"prediction_quantiles": [0.10, 0.25, 0.50, 0.75, 0.90],
"commodity_data_vendors": {},
"require_point_in_time_data": True,
"reject_unverified_numbers": True,
```

Expose appropriate environment-variable overrides using the existing configuration pattern.

## 16.2 Agent state

Replace stock-oriented state fields with a commodity-specific state. Preserve backward compatibility only when it does not create ambiguous semantics.

Suggested state:

```python
class GrainAgentState(MessagesState):
    commodity: str
    contract_symbol: str
    crop_year: str
    analysis_date: str
    forecast_horizons: list[int]
    evidence_package_uri: str

    technical_report: str
    supply_demand_report: str
    weather_report: str
    demand_report: str
    positioning_report: str
    news_report: str
    quantitative_forecast_report: str

    bull_case: str
    bear_case: str
    scenario_report: str
    risk_report: str
    final_outlook: str
    newsletter_draft: str
```

## 16.3 Analyst execution registry

Add analyst specs for:

```text
technical
supply_demand
weather
demand
positioning
news
forecast
```

Use a central registry rather than scattering node names throughout the graph.

## 16.4 Tool nodes

Remove or disable stock-only tools for commodity runs:

```text
get_balance_sheet
get_cashflow
get_income_statement
get_insider_transactions
```

Add commodity tools:

```text
get_futures_contract
get_futures_curve
get_options_snapshot
get_usda_balance_sheet
get_crop_progress
get_weather_features
get_export_sales
get_ethanol_data
get_cot_positions
get_cash_market_data
get_quant_forecast
```

## 16.5 Graph setup

- Add commodity analyst factories
- Add parallel fan-out and fan-in
- Add evidence validation
- Replace Trader and Portfolio Manager prompts
- Add final newsletter-rendering node
- Keep checkpoint behavior working
- Include commodity, contract, date, analyst selection, and model versions in checkpoint identity

## 16.6 Dataflow interface

Add vendor-routed methods equivalent to:

```text
get_contract_history
get_contract_metadata
get_curve_snapshot
get_options_chain
get_usda_nass
get_wasde
get_fas_psd
get_fas_export_sales
get_ers_data
get_ams_market_data
get_cftc_cot
get_weather_forecast
get_weather_history
get_drought_data
get_eia_ethanol
```

Use typed error classes for:

- Vendor not configured
- Rate limit
- No data
- Stale data
- Invalid contract
- Contract expired
- Publication not yet available
- Unit mismatch
- Point-in-time violation

## 16.7 Reflection and performance

Replace stock-return reflection with:

- Median absolute error
- Mean absolute scaled error
- Directional accuracy
- Quantile loss
- 50% and 80% interval coverage
- Brier score for threshold probabilities
- Calibration error
- Performance by commodity
- Performance by horizon
- Performance by crop-season phase
- Performance by volatility regime

## 16.8 CLI

The CLI should request:

```text
Commodity
Contract
Analysis date or as-of timestamp
Forecast horizons
Analysts
Research depth
Output format
```

Example command:

```powershell
grainagents analyze `
  --commodity corn `
  --contract ZCZ26 `
  --as-of 2026-07-30 `
  --horizons 5,20,60 `
  --output newsletter
```

---

# 17. Newsletter Output Contract

Each commodity outlook should contain:

## Headline

A plain-language headline that accurately reflects the scenario balance.

## Executive summary

Three to five sentences covering:

- Directional bias
- Most likely scenario
- Important change from the previous report
- Biggest upside and downside risks

## Price scenarios

```markdown
| Scenario | Probability | 30-day range | 90-day range |
|---|---:|---:|---:|
| Bull | 25% | ... | ... |
| Base | 50% | ... | ... |
| Bear | 25% | ... | ... |
```

Probabilities must total 100%, be derived from or reconciled with the deterministic forecast distribution, and be stored before prose generation.

## Required sections

1. What changed this week
2. Technical picture
3. Supply and demand
4. Weather and yield risk
5. Export and domestic demand
6. Fund positioning
7. Bull case
8. Bear case
9. Base case
10. Levels and events to watch
11. Confidence and limitations
12. Sources

## Required chart package

- Specific contract price with moving averages
- Futures curve
- Seasonal price comparison
- Managed-money positioning
- Stocks-to-use ratio
- Weather or drought visualization
- Forecast fan chart
- Scenario-probability change chart

Charts must identify data timestamp, contract, units, and source.

---

# 18. Output Directory Contract

The first milestone should generate:

```text
results/corn/ZCZ26/2026-07-30/
├── run_manifest.json
├── evidence.json
├── market_data.parquet
├── technical_report.md
├── supply_demand_report.md
├── weather_report.md
├── demand_report.md
├── positioning_report.md
├── news_report.md
├── quantitative_forecast.json
├── bull_bear_debate.md
├── scenario_report.json
├── final_outlook.json
├── newsletter.md
├── source_audit.csv
└── charts/
    ├── price_technicals.png
    ├── futures_curve.png
    ├── cot_positioning.png
    ├── seasonal_comparison.png
    └── forecast_fan.png
```

---

# 19. MVP Boundaries

Include in v1:

- Corn, soybeans, and Chicago wheat
- One explicit primary contract per commodity run
- Daily end-of-day data refresh
- Weekly comprehensive outlook
- Futures curve
- USDA balances
- COT positions
- Export sales
- Corn ethanol data
- Weather and drought
- Forecast ensemble
- Newsletter Markdown
- Charts
- Evidence-linked sources
- Historical forecast scoring
- Human approval step

Exclude from v1:

- Live trade execution
- Personalized hedging instructions
- Automated brokerage integration
- Intraday trading signals
- County-level basis forecasts
- Every wheat class
- Fully autonomous newsletter publishing
- Unsupported social-media sentiment scraping

---

# 20. Implementation Milestones

## Milestone 0: Repository and documentation

Deliver:

- Fork created
- `upstream` remote configured
- This `instructions.md` committed
- Fork README updated with GrainAgents mission and development status
- Architecture decision record describing fork-versus-rewrite decision
- Initial issue or project checklist

Acceptance criteria:

- Fork builds and tests exactly as upstream before functional modifications
- Upstream license and attribution remain intact

## Milestone 1: Commodity foundation

Deliver:

- `commodity_future` asset type
- Commodity and contract registry
- Contract parser and validation
- Commodity-specific state schema
- Commodity CLI inputs
- Stock fundamentals disabled for commodity runs
- Existing technical analyst successfully analyzes one explicit contract
- Initial evidence-package schema

Acceptance criteria:

- One command analyzes December corn without requesting company financial statements
- Unknown or expired contracts fail with typed errors
- Existing stock and crypto modes remain functional unless an intentional breaking fork decision is documented

## Milestone 2: Official data foundation

Deliver adapters for:

- NASS
- WASDE
- FAS PSD
- FAS export sales
- CFTC COT
- EIA ethanol
- Weather
- Drought Monitor

Deliver:

- Raw immutable archive
- Point-in-time observation model
- Data-quality checks
- Source audit

Acceptance criteria:

- A historical `as_of` query never returns a later publication or revision
- Fixture-based tests run without live API keys
- Stale core data prevents publication

## Milestone 3: Commodity agents

Deliver:

- Technical/curve agent
- Supply-and-demand agent
- Weather-and-yield agent
- Demand agent
- Positioning agent
- Grain news agent
- Evidence validator
- Bull and bear debate

Acceptance criteria:

- Every numerical statement is supported by a fact ID or deterministic output
- Unsupported numerical claims cause validation failure
- Parallel analysts receive the same immutable evidence package

## Milestone 4: Forecasting

Deliver:

- Statistical baselines
- At least one tree model
- At least one neural model only after baselines work
- Ensemble weighting
- Prediction intervals
- Threshold probabilities
- Rolling point-in-time evaluation
- Performance registry

Acceptance criteria:

- Backtests contain no future-data leakage
- Forecast package includes model and data versions
- Complex models are compared with transparent baselines
- Interval coverage and quantile loss are reported

## Milestone 5: Publication

Deliver:

- Scenario manager
- Risk agents
- Final outlook manager
- Newsletter editor
- Chart generation
- Change-from-prior-report comparison
- Human approval gate

Acceptance criteria:

- Corn run produces the full output directory contract
- Scenario probabilities reconcile with the quantitative distribution
- Newsletter copy contains no unsupported numbers
- All charts display contract, date, units, and source

## Milestone 6: Expansion

Possible later work:

- Kansas City wheat
- Soybean meal and oil
- Cash basis
- User-selectable geography
- Marketing-year simulations
- Options scenario analysis
- Web dashboard
- Newsletter-platform integration after human approval

---

# 21. Testing Requirements

Implement tests at these levels.

## Unit tests

- Contract parsing
- Crop-year mapping
- Unit conversions
- Publication timestamps
- Point-in-time filtering
- Roll logic
- Feature calculations
- Scenario-probability normalization
- Evidence claim validation

## Adapter contract tests

Every data adapter must return a shared normalized schema and typed errors.

Use recorded fixtures for deterministic CI tests. Live API tests must be optional and clearly marked.

## Integration tests

- Build corn evidence package from fixtures
- Run every analyst against fixed evidence
- Validate outputs
- Generate final report bundle
- Resume from a checkpoint

## Leakage tests

Create explicit tests that inject future revisions or later weather observations and prove they are excluded from historical runs.

## Prompt tests

Test that agents:

- Do not request company financial statements for commodity futures
- Do not emit BUY/HOLD/SELL as the primary output
- Cite fact IDs
- Label inferences
- Report missing data
- Avoid personalized recommendations

## Regression tests

Maintain one frozen example for:

- December corn
- November soybeans
- Chicago wheat

Compare structured output schemas and deterministic metrics. Do not require exact LLM prose equality.

---

# 22. Security and Reliability

- Never commit API keys or credentials
- Provide `.env.example`
- Validate all external inputs
- Sanitize contract symbols used in file paths
- Apply retry budgets and rate-limit handling
- Cache immutable source releases by content hash
- Record checksums
- Validate downloaded file types
- Enforce reasonable maximum payload sizes
- Use least-privilege credentials
- Add timeouts to every network request
- Add structured logs with run IDs
- Make critical failures visible in final run status

---

# 23. Model and Editorial Safety

Every published outlook must include:

- Exact contract
- As-of date and time
- Forecast horizon
- Data freshness status
- Confidence level
- Known limitations
- Statement that the material is general market research and not individualized financial, trading, legal, or tax advice

Do not portray the system as certain. Prefer calibrated language such as:

- “The model assigns…”
- “The base scenario is…”
- “This risk would invalidate…”
- “The interval reflects…”

Do not use language implying guaranteed prices or outcomes.

---

# 24. First Concrete Deliverable

The first functional target is:

> Generate a defensible weekly December corn outlook from one command using an explicit contract, a point-in-time evidence package, technical analysis, a basic USDA balance snapshot, COT positioning, weather context, statistical baseline forecasts, bull/bear debate, and newsletter-ready Markdown.

Example:

```powershell
grainagents analyze `
  --commodity corn `
  --contract ZCZ26 `
  --as-of 2026-07-30 `
  --horizons 5,20,60 `
  --output newsletter
```

Do not begin soybean and wheat implementation until the corn vertical slice meets its acceptance criteria.

---

# 25. Definition of Done for the Corn Vertical Slice

The corn vertical slice is complete only when all items below are true.

- A specific December corn contract is resolved correctly
- Contract metadata includes first notice and last trade dates
- Continuous and specific-contract series are not conflated
- Evidence package is immutable and versioned
- Market, USDA, COT, weather, export, and ethanol data have provenance
- Historical `as_of` runs exclude later information
- Statistical baseline forecasts produce 5-, 20-, and 60-day distributions
- Bull, base, and bear scenarios reconcile with the forecast distribution
- Analyst reports cite fact IDs
- Unsupported numbers are rejected
- Newsletter Markdown is generated
- Source audit is generated
- Required charts are generated
- Run manifest records code, model, prompt, and data versions
- Tests pass in CI using fixtures
- Human approval is required before publication
- README explains how to install and run the vertical slice

---

# 26. Required Agent Progress Reporting

At the end of each milestone, report:

1. Files changed
2. Architecture decisions made
3. Tests added and results
4. Data sources connected
5. Known limitations
6. Security or licensing concerns
7. Next milestone
8. Any user decision genuinely required

Do not claim completion when only stubs exist. Clearly distinguish:

- Implemented and tested
- Implemented but not tested live
- Mocked with fixtures
- Planned only

---

# 27. Immediate Execution Order

Begin in this order:

1. Create the GitHub fork and configure remotes
2. Commit this file unchanged to the fork
3. Run the upstream test suite and record the baseline
4. Inspect the current upstream graph, agent state, tool routing, CLI, reporting, tests, and license
5. Create an architecture decision record for the commodity fork
6. Create Milestone 1 issues or a checklist
7. Implement the commodity and futures-contract domain models
8. Add the `commodity_future` execution path
9. Disable stock fundamentals for commodity runs
10. Produce a technical-only explicit-contract corn report
11. Add the evidence-package skeleton
12. Stop and report results before beginning official-data adapters

The project should remain usable and reviewable at every step.
