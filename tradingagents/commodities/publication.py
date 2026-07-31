"""Deterministic, evidence-linked publication drafts and approval gating."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

PUBLICATION_VERSION = "deterministic-publication-draft-v1"
REFERENCE_PATTERN = re.compile(r"\[((?:fact|output)_[a-z0-9_]+)\]")


@dataclass(frozen=True)
class PublicationBundle:
    final_outlook: dict[str, Any]
    publication_status: dict[str, Any]
    newsletter: str
    bull_bear_debate: str
    risk_report: str
    news_report: str


def _number(value: float | int, decimals: int = 2) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    return f"{float(value):,.{decimals}f}"


def _find_fact(
    evidence: dict[str, Any],
    metric: str,
    *,
    fact_id: str | None = None,
) -> dict[str, Any]:
    matching = [
        fact
        for fact in evidence["facts"]
        if fact["metric"] == metric
        and (fact_id is None or fact["fact_id"] == fact_id)
    ]
    if not matching:
        raise ValueError(f"publication requires evidence metric {metric!r}")
    return matching[-1]


def _citation(fact: dict[str, Any]) -> str:
    return f"[{fact['fact_id']}]"


def _reference_forecast(
    quantitative: dict[str, Any],
    *,
    target_horizon: int = 20,
) -> dict[str, Any]:
    return min(
        quantitative["forecast_horizons"],
        key=lambda item: abs(item["horizon_trading_days"] - target_horizon),
    )


def _publication_blockers(
    evidence: dict[str, Any],
    quantitative: dict[str, Any],
) -> list[dict[str, str]]:
    blockers = [
        {
            "code": f"missing_core_data:{item}",
            "message": f"Core evidence section {item!r} is incomplete.",
        }
        for item in evidence["quality"]["missing_core_data"]
    ]
    if evidence["quality"]["stale_sources"]:
        blockers.append(
            {
                "code": "stale_sources",
                "message": "One or more core evidence sources are stale.",
            }
        )
    if evidence["quality"]["contradictions"]:
        blockers.append(
            {
                "code": "evidence_contradictions",
                "message": "The evidence package contains unresolved contradictions.",
            }
        )

    restricted_scopes = sorted(
        {
            source["license_scope"]
            for source in evidence["sources"]
            if source.get("license_scope")
            in {"internal_testing_only", "unknown", "not_for_redistribution"}
        }
    )
    if restricted_scopes:
        blockers.append(
            {
                "code": "market_data_redistribution_rights_unverified",
                "message": (
                    "One or more sources are restricted to internal testing: "
                    + ", ".join(restricted_scopes)
                ),
            }
        )
    if quantitative.get("status") != "ready_for_publication":
        blockers.append(
            {
                "code": "forecast_research_only",
                "message": (
                    "Forecasts remain research-only until saved forecasts "
                    "accumulate genuine out-of-sample scores."
                ),
            }
        )
    if not evidence.get("macro"):
        blockers.append(
            {
                "code": "news_macro_not_implemented",
                "message": "The grain-news and macro evidence section is unavailable.",
            }
        )
    blockers.append(
        {
            "code": "export_demand_not_implemented",
            "message": "Weekly export-sales and export-inspection evidence is unavailable.",
        }
    )
    return blockers


def _validate_references(
    documents: tuple[str, ...],
    *,
    fact_ids: set[str],
    output_ids: set[str],
) -> None:
    allowed = fact_ids | output_ids
    references = {
        reference
        for document in documents
        for reference in REFERENCE_PATTERN.findall(document)
    }
    unknown = sorted(references - allowed)
    if unknown:
        raise ValueError(
            "publication contains unknown evidence references: "
            + ", ".join(unknown)
        )


def _source_lines(evidence: dict[str, Any]) -> str:
    rendered = []
    seen: set[tuple[str, str, str]] = set()
    for source in evidence["sources"]:
        url = source.get("source_url", "archived source record")
        identity = (source["provider"], source["dataset"], url)
        if identity in seen:
            continue
        seen.add(identity)
        rendered.append(
            f"- {source['provider']}: {source['dataset']} — {url}"
        )
    return "\n".join(rendered)


def build_publication_bundle(
    evidence: dict[str, Any],
    *,
    quantitative: dict[str, Any],
    scenarios: dict[str, Any],
) -> PublicationBundle:
    """Build a blocked, evidence-linked publication draft.

    This renderer does not ask an LLM to create or calculate any numerical
    claim. It accepts only the immutable evidence and deterministic forecast
    outputs produced earlier in the run.
    """
    instrument = evidence["instrument"]
    symbol = instrument["symbol"]
    reference = _reference_forecast(quantitative)
    current = float(reference["current_price"])
    median = float(reference["median_projected_price"])
    projected_change_percent = (median / current - 1) * 100
    if projected_change_percent > 1:
        directional_bias = "bullish"
    elif projected_change_percent < -1:
        directional_bias = "bearish"
    else:
        directional_bias = "neutral"

    probabilities = scenarios["probabilities"]
    output_values = {
        "output_reference_horizon_trading_days": reference[
            "horizon_trading_days"
        ],
        "output_projected_change_percent": round(projected_change_percent, 6),
        "output_scenario_bull_probability": probabilities["bull"],
        "output_scenario_base_probability": probabilities["base"],
        "output_scenario_bear_probability": probabilities["bear"],
        "output_publication_directional_bias": directional_bias,
    }
    output_ids = set(output_values)
    blockers = _publication_blockers(evidence, quantitative)
    status = {
        "schema_version": "1.0",
        "publication_version": PUBLICATION_VERSION,
        "run_id": evidence["run_id"],
        "status": "blocked" if blockers else "awaiting_human_approval",
        "publication_ready": False,
        "human_approval_required": True,
        "human_approval_recorded": False,
        "blockers": blockers,
        "quality_warnings": evidence["quality"]["warnings"],
    }

    settlement = _find_fact(evidence, "official_settlement")
    return_20 = _find_fact(evidence, "20_trading_day_return")
    return_60 = _find_fact(evidence, "60_trading_day_return")
    sma_20 = _find_fact(evidence, "20_day_moving_average")
    sma_50 = _find_fact(evidence, "50_day_moving_average")
    rsi = _find_fact(evidence, "rsi_14")
    support = _find_fact(evidence, "support_20")
    resistance = _find_fact(evidence, "resistance_20")
    production = _find_fact(evidence, "wasde_corn_production")
    ending_stocks = _find_fact(evidence, "wasde_corn_ending_stocks")
    exports = _find_fact(evidence, "wasde_corn_exports")
    ethanol_production = _find_fact(
        evidence,
        "eia_us_fuel_ethanol_production",
    )
    ethanol_stocks = _find_fact(evidence, "eia_us_fuel_ethanol_stocks")
    drought = _find_fact(evidence, "corn_drought_d1_or_worse_percent")
    precipitation = _find_fact(
        evidence,
        "corn_weather_sample_weighted_7_day_precipitation_mm",
    )
    noncommercial_net = _find_fact(evidence, "cftc_corn_noncommercial_net")
    first_notice = _find_fact(evidence, "first_notice_date")
    forecast_median = _find_fact(
        evidence,
        "forecast_median_projected_price",
        fact_id=(
            f"fact_forecast_{reference['horizon_trading_days']}d_median"
        ),
    )
    forecast_interval = _find_fact(
        evidence,
        "forecast_prediction_interval_80",
        fact_id=(
            f"fact_forecast_{reference['horizon_trading_days']}d_interval_80"
        ),
    )
    forecast_confidence = _find_fact(
        evidence,
        "forecast_confidence_score",
        fact_id=(
            f"fact_forecast_{reference['horizon_trading_days']}d_confidence"
        ),
    )
    forecast_disagreement = _find_fact(
        evidence,
        "forecast_model_disagreement_score",
        fact_id=(
            f"fact_forecast_{reference['horizon_trading_days']}d_disagreement"
        ),
    )
    forecast_coverage = _find_fact(
        evidence,
        "forecast_rolling_interval_coverage_80",
        fact_id=(
            f"fact_forecast_{reference['horizon_trading_days']}d_coverage_80"
        ),
    )

    headline = (
        f"{instrument['delivery_month_name']} corn outlook is "
        f"{directional_bias}, with wide calibrated risk"
    )
    final_outlook = {
        "schema_version": "1.0",
        "publication_version": PUBLICATION_VERSION,
        "run_id": evidence["run_id"],
        "contract_symbol": symbol,
        "as_of": evidence["as_of"],
        "status": status["status"],
        "headline": headline,
        "directional_bias": directional_bias,
        "reference_horizon_trading_days": reference["horizon_trading_days"],
        "current_price": current,
        "median_projected_price": median,
        "projected_change_percent": round(projected_change_percent, 6),
        "prediction_interval_80": reference["prediction_interval_80"],
        "forecast_confidence_score": reference["forecast_confidence_score"],
        "model_disagreement_score": reference["model_disagreement_score"],
        "scenario_probabilities": probabilities,
        "output_records": [
            {"output_id": key, "value": value}
            for key, value in output_values.items()
        ],
        "evidence_refs": [
            settlement["fact_id"],
            forecast_median["fact_id"],
            forecast_interval["fact_id"],
            forecast_confidence["fact_id"],
            forecast_disagreement["fact_id"],
        ],
        "publication_ready": False,
        "human_approval_required": True,
    }

    bull_bear = f"""# Bull and bear case — DRAFT

## Bull case

- The contract settled at ${_number(settlement["value"], 4)} per bushel and
  remains above its 20- and 50-session averages of
  ${_number(sma_20["value"], 4)} and ${_number(sma_50["value"], 4)}
  {_citation(settlement)} {_citation(sma_20)} {_citation(sma_50)}.
- Moderate drought or worse covers {_number(drought["value"], 1)}% of sampled
  corn area {_citation(drought)}.
- The deterministic bull-scenario probability is
  {_number(probabilities["bull"] * 100, 1)}%
  [output_scenario_bull_probability].

## Bear case

- The contract's 60-session return is {_number(return_60["value"], 2)}%
  {_citation(return_60)}.
- WASDE production is {_number(production["value"], 0)} million bushels and
  ending stocks are {_number(ending_stocks["value"], 0)} million bushels
  {_citation(production)} {_citation(ending_stocks)}.
- The deterministic bear-scenario probability is
  {_number(probabilities["bear"] * 100, 1)}%
  [output_scenario_bear_probability].

## Base case

- The reference-horizon median is ${_number(median, 4)} per bushel
  {_citation(forecast_median)}, versus ${_number(current, 4)} currently
  {_citation(settlement)}.
- The deterministic base-scenario probability is
  {_number(probabilities["base"] * 100, 1)}%
  [output_scenario_base_probability].

This is a deterministic research draft, not an LLM-generated price forecast
and not a trading or hedging recommendation.
"""

    risk_report = f"""# Risk review — DRAFT

- The reference 80% interval is
  ${_number(reference["prediction_interval_80"][0], 4)} to
  ${_number(reference["prediction_interval_80"][1], 4)}
  {_citation(forecast_interval)}.
- Forecast confidence is {_number(reference["forecast_confidence_score"], 1)}
  out of 100 {_citation(forecast_confidence)}, while model disagreement is
  {_number(reference["model_disagreement_score"], 1)} out of 100
  {_citation(forecast_disagreement)}.
- Rolling nominal-80% coverage is
  {_number(forecast_coverage["value"] * 100, 1)}%
  {_citation(forecast_coverage)}.
- Weather evidence is partial, export-demand evidence is missing, and current
  market-data licensing is restricted to internal testing.
- Human approval is mandatory, and this draft cannot pass the gate while any
  blocker remains.
"""

    news_report = """# Grain news and macro — UNAVAILABLE

The official-source grain-news and macro analyst is not implemented in this
run. No headlines, sentiment scores, or event claims are inferred or
fabricated. This missing section is a publication blocker.
"""

    blocker_lines = "\n".join(
        f"- `{blocker['code']}`: {blocker['message']}"
        for blocker in blockers
    )
    source_lines = _source_lines(evidence)
    newsletter = f"""# DRAFT — NOT APPROVED FOR PUBLICATION

## {headline}

**Contract:** `{symbol}`

**As of:** {evidence["as_of"]}

**Publication status:** `{status["status"]}`

## Executive summary

The deterministic reference-horizon outlook is {directional_bias}
[output_publication_directional_bias]. The median projection is
${_number(median, 4)} per bushel {_citation(forecast_median)}, compared with
the current ${_number(current, 4)} settlement {_citation(settlement)}. The
base scenario has {_number(probabilities["base"] * 100, 1)}% probability
[output_scenario_base_probability]. Wide calibrated intervals, incomplete
weather evidence, missing export and news evidence, and unverified
redistribution rights prevent publication.

## Price scenarios

| Scenario | Probability | Deterministic reference |
|---|---:|---|
| Bull | {_number(probabilities["bull"] * 100, 1)}% | [output_scenario_bull_probability] |
| Base | {_number(probabilities["base"] * 100, 1)}% | [output_scenario_base_probability] |
| Bear | {_number(probabilities["bear"] * 100, 1)}% | [output_scenario_bear_probability] |

The reference horizon is {_number(reference["horizon_trading_days"], 0)}
trading sessions [output_reference_horizon_trading_days]. Its calibrated 80%
range is ${_number(reference["prediction_interval_80"][0], 4)} to
${_number(reference["prediction_interval_80"][1], 4)}
{_citation(forecast_interval)}.

## What changed this week

No prior approved GrainAgents publication is available for a deterministic
change comparison. The current 20-session contract return is
{_number(return_20["value"], 2)}% {_citation(return_20)}.

## Technical picture

Settlement is ${_number(settlement["value"], 4)} per bushel
{_citation(settlement)}. The 20-session moving average is
${_number(sma_20["value"], 4)} {_citation(sma_20)}, the 50-session average is
${_number(sma_50["value"], 4)} {_citation(sma_50)}, and 14-session RSI is
{_number(rsi["value"], 1)} {_citation(rsi)}. Observed support is
${_number(support["value"], 4)} {_citation(support)} and resistance is
${_number(resistance["value"], 4)} {_citation(resistance)}.

## Supply and demand

WASDE production is {_number(production["value"], 0)} million bushels
{_citation(production)}, ending stocks are
{_number(ending_stocks["value"], 0)} million bushels
{_citation(ending_stocks)}, and projected exports are
{_number(exports["value"], 0)} million bushels {_citation(exports)}.

## Weather and yield risk

Moderate drought or worse covers {_number(drought["value"], 1)}% of corn area
{_citation(drought)}. The acreage-sample-weighted seven-day precipitation
forecast is {_number(precipitation["value"], 1)} mm
{_citation(precipitation)}. Temperature and rainfall anomalies, a 14-day
forecast, calibrated yield impact, and a complete weather-risk score remain
missing.

## Export and domestic demand

Fuel-ethanol production is
{_number(ethanol_production["value"], 0)} thousand barrels per day
{_citation(ethanol_production)}, and stocks are
{_number(ethanol_stocks["value"], 0)} thousand barrels
{_citation(ethanol_stocks)}. Weekly export sales and inspections are not yet
connected.

## Fund positioning

The all-month legacy CFTC noncommercial net position is
{_number(noncommercial_net["value"], 0)} contracts
{_citation(noncommercial_net)}.

## Bull, base, and bear cases

The bull, base, and bear probabilities are
{_number(probabilities["bull"] * 100, 1)}%,
{_number(probabilities["base"] * 100, 1)}%, and
{_number(probabilities["bear"] * 100, 1)}%, respectively
[output_scenario_bull_probability] [output_scenario_base_probability]
[output_scenario_bear_probability]. These probabilities are deterministic
transformations of the forecast distribution.

## Levels and events to watch

Support is ${_number(support["value"], 4)} {_citation(support)}, resistance is
${_number(resistance["value"], 4)} {_citation(resistance)}, and first notice is
{first_notice["value"]} {_citation(first_notice)}.

## Confidence and limitations

Reference-horizon confidence is
{_number(reference["forecast_confidence_score"], 1)} out of 100
{_citation(forecast_confidence)}. Model disagreement is
{_number(reference["model_disagreement_score"], 1)} out of 100
{_citation(forecast_disagreement)}. This is a research draft, not personalized
marketing, trading, or hedging advice.

### Publication blockers

{blocker_lines}

## Sources

{source_lines}
"""

    fact_ids = {fact["fact_id"] for fact in evidence["facts"]}
    _validate_references(
        (newsletter, bull_bear, risk_report),
        fact_ids=fact_ids,
        output_ids=output_ids,
    )
    return PublicationBundle(
        final_outlook=final_outlook,
        publication_status=status,
        newsletter=newsletter,
        bull_bear_debate=bull_bear,
        risk_report=risk_report,
        news_report=news_report,
    )


__all__ = [
    "PUBLICATION_VERSION",
    "PublicationBundle",
    "build_publication_bundle",
]
