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


def _find_optional_fact(
    evidence: dict[str, Any],
    metric: str,
) -> dict[str, Any] | None:
    matching = [
        fact for fact in evidence["facts"] if fact["metric"] == metric
    ]
    return matching[-1] if matching else None


def _citation(fact: dict[str, Any]) -> str:
    return f"[{fact['fact_id']}]"


def _markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


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
    charts_manifest: dict[str, Any] | None = None,
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
    macro = evidence.get("macro") or {}
    if macro.get("status") != "ready":
        blockers.append(
            {
                "code": "macro_evidence_unavailable",
                "message": "Official currency, energy, and rate context is unavailable.",
            }
        )
    events = macro.get("events") or {}
    if events.get("status") != "ready":
        blockers.append(
            {
                "code": "grain_news_not_implemented",
                "message": "An official-source grain-news event feed is unavailable.",
            }
        )
    elif events.get("coverage_status") != "complete":
        blockers.append(
            {
                "code": "grain_news_coverage_incomplete",
                "message": (
                    "Federal regulatory events and U.S. river-barge volume are "
                    "connected, but active transport disruptions, shipping, "
                    "international policy, and broader grain-news coverage remain "
                    "incomplete."
                ),
            }
        )
    demand = evidence.get("demand") or {}
    export_sales = demand.get("export_sales") or {}
    if export_sales.get("status") != "ready":
        blockers.append(
            {
                "code": "export_sales_unavailable",
                "message": "USDA weekly export-sales evidence is unavailable.",
            }
        )
    if "export_inspections" in demand.get("missing", ["export_inspections"]):
        blockers.append(
            {
                "code": "export_inspections_not_implemented",
                "message": "Weekly export-inspection evidence is unavailable.",
            }
        )
    if charts_manifest is not None and any(
        chart["filename"] == "seasonal_comparison.png"
        and chart["status"] != "ready"
        for chart in charts_manifest["charts"]
    ):
        blockers.append(
            {
                "code": "seasonal_chart_incomplete",
                "message": (
                    "A multi-year contract-month-aligned seasonal comparison "
                    "is unavailable."
                ),
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


def _chart_markdown(
    charts_manifest: dict[str, Any] | None,
    filename: str,
    alt_text: str,
) -> str:
    if charts_manifest is None or not any(
        chart["filename"] == filename
        for chart in charts_manifest["charts"]
    ):
        return ""
    return f"\n\n![{alt_text}](charts/{filename})"


def build_publication_bundle(
    evidence: dict[str, Any],
    *,
    quantitative: dict[str, Any],
    scenarios: dict[str, Any],
    prior_comparison: dict[str, Any] | None = None,
    charts_manifest: dict[str, Any] | None = None,
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
    if prior_comparison and prior_comparison.get("status") == "ready":
        changes = prior_comparison["changes"]
        output_values.update(
            {
                "output_change_median_projected_price": changes[
                    "median_projected_price"
                ],
                "output_change_forecast_confidence_score": changes[
                    "forecast_confidence_score"
                ],
                "output_change_scenario_probability_bull": changes[
                    "scenario_probability_bull"
                ],
                "output_change_scenario_probability_base": changes[
                    "scenario_probability_base"
                ],
                "output_change_scenario_probability_bear": changes[
                    "scenario_probability_bear"
                ],
            }
        )
    output_ids = set(output_values)
    blockers = _publication_blockers(
        evidence,
        quantitative,
        charts_manifest,
    )
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
    weekly_exports = _find_optional_fact(evidence, "fas_corn_weekly_exports")
    target_export_commitment = _find_optional_fact(
        evidence,
        "fas_corn_target_marketing_year_commitment",
    )
    weekly_inspections = _find_optional_fact(
        evidence,
        "ams_corn_weekly_inspections",
    )
    drought = _find_fact(evidence, "corn_drought_d1_or_worse_percent")
    precipitation = _find_fact(
        evidence,
        "corn_weather_sample_weighted_7_day_precipitation_mm",
    )
    temperature_anomaly = _find_optional_fact(
        evidence,
        "corn_weather_sample_weighted_7_day_temperature_anomaly_c",
    )
    precipitation_anomaly = _find_optional_fact(
        evidence,
        "corn_weather_sample_weighted_7_day_precipitation_anomaly_mm",
    )
    if temperature_anomaly is not None and precipitation_anomaly is not None:
        weather_anomaly_text = (
            "Against fixed nearby-station NCEI 1991-2020 daily normals, the "
            "sample-weighted seven-day temperature anomaly is "
            f"{_number(temperature_anomaly['value'], 1)} C "
            f"{_citation(temperature_anomaly)} and the precipitation anomaly "
            f"is {_number(precipitation_anomaly['value'], 1)} mm "
            f"{_citation(precipitation_anomaly)}."
        )
    else:
        weather_anomaly_text = (
            "Temperature and rainfall anomalies, calibrated yield impact, "
            "and a complete weather-risk score remain missing."
        )
    condition_risk = _find_optional_fact(
        evidence,
        "corn_crop_condition_based_weather_risk_score",
    )
    condition_risk_change = _find_optional_fact(
        evidence,
        "corn_crop_condition_based_weather_risk_change_week_over_week",
    )
    good_excellent = _find_optional_fact(
        evidence,
        "corn_crop_good_excellent_percent",
    )
    silking = _find_optional_fact(evidence, "corn_crop_silking_percent")
    dough = _find_optional_fact(evidence, "corn_crop_dough_percent")
    if all(
        fact is not None
        for fact in (
            condition_risk,
            condition_risk_change,
            good_excellent,
            silking,
            dough,
        )
    ):
        crop_condition_text = (
            f"USDA rated {_number(good_excellent['value'], 1)}% of reported "
            f"corn acres good or excellent {_citation(good_excellent)}. The "
            "transparent condition-based weather-risk index is "
            f"{_number(condition_risk['value'], 2)} out of 100 "
            f"{_citation(condition_risk)}, a weekly change of "
            f"{_number(condition_risk_change['value'], 2)} points "
            f"{_citation(condition_risk_change)}; "
            f"{_number(silking['value'], 1)}% was silking {_citation(silking)} "
            f"and {_number(dough['value'], 1)}% was at dough "
            f"{_citation(dough)}. This monitoring index is not yield "
            "calibrated; a calibrated weather-risk score and yield-impact "
            "range remain missing."
        )
    else:
        crop_condition_text = (
            "USDA crop-condition evidence and a yield-calibrated weather-risk "
            "score remain missing."
        )
    weather_outlook = (evidence.get("weather") or {}).get("outlook_8_14_day")
    if weather_outlook:
        outlook_period = (
            f"{weather_outlook['valid_start']}/{weather_outlook['valid_end']}"
        )
        temperature_category = weather_outlook["temperature"][
            "dominant_category"
        ]
        precipitation_category = weather_outlook["precipitation"][
            "dominant_category"
        ]
        temperature_outlook_share = _find_optional_fact(
            evidence,
            "cpc_corn_8_14_day_temperature_"
            f"{temperature_category}_acre_share",
        )
        precipitation_outlook_share = _find_optional_fact(
            evidence,
            "cpc_corn_8_14_day_precipitation_"
            f"{precipitation_category}_acre_share",
        )
        if (
            temperature_outlook_share is not None
            and precipitation_outlook_share is not None
        ):
            extended_weather_text = (
                f"For {outlook_period}, the CPC 8-14 day dominant category is "
                f"{temperature_category.replace('_', ' ')} temperature across "
                f"{_number(temperature_outlook_share['value'], 1)}% of sampled "
                f"acres {_citation(temperature_outlook_share)} and "
                f"{precipitation_category.replace('_', ' ')} precipitation "
                f"across {_number(precipitation_outlook_share['value'], 1)}% "
                f"{_citation(precipitation_outlook_share)}."
            )
        else:
            extended_weather_text = "The CPC 8-14 day facts are unavailable."
    else:
        extended_weather_text = "The CPC 8-14 day outlook is unavailable."
    noncommercial_net = _find_fact(evidence, "cftc_corn_noncommercial_net")
    first_notice = _find_fact(evidence, "first_notice_date")
    broad_dollar = _find_optional_fact(
        evidence,
        "fred_broad_us_dollar_index",
    )
    wti_crude = _find_optional_fact(
        evidence,
        "fred_wti_crude_oil_usd_per_barrel",
    )
    treasury_10y = _find_optional_fact(
        evidence,
        "fred_10y_treasury_percent",
    )
    effective_fed_funds = _find_optional_fact(
        evidence,
        "fred_effective_federal_funds_rate_percent",
    )
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
        "prior_report_comparison_status": (
            prior_comparison["status"]
            if prior_comparison is not None
            else "not_evaluated"
        ),
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

    if weekly_exports is not None and weekly_inspections is not None:
        export_risk = "USDA export sales and inspections are connected"
    elif weekly_exports is not None:
        export_risk = (
            "USDA export sales are connected, but export inspections remain "
            "unavailable"
        )
    elif weekly_inspections is not None:
        export_risk = (
            "USDA export inspections are connected, but export sales remain "
            "unavailable"
        )
    else:
        export_risk = (
            "export-sales and export-inspection evidence is unavailable"
        )
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
- Weather evidence is partial, {export_risk}, and current market-data
  licensing is restricted to internal testing.
- Human approval is mandatory, and this draft cannot pass the gate while any
  blocker remains.
"""

    macro_section = evidence.get("macro") or {}
    transportation_section = macro_section.get("transportation") or {}
    barge_tons = _find_optional_fact(
        evidence,
        "ams_corn_weekly_downbound_barge_tons",
    )
    barge_four_week_average = _find_optional_fact(
        evidence,
        "ams_corn_four_week_average_downbound_barge_tons",
    )
    barge_weekly_change = _find_optional_fact(
        evidence,
        "ams_corn_week_over_week_change_percent",
    )
    barge_yearly_change = _find_optional_fact(
        evidence,
        "ams_corn_year_over_year_change_percent",
    )
    if all(
        fact is not None
        for fact in (
            barge_tons,
            barge_four_week_average,
            barge_weekly_change,
            barge_yearly_change,
        )
    ):
        assert barge_tons is not None
        assert barge_four_week_average is not None
        assert barge_weekly_change is not None
        assert barge_yearly_change is not None
        transportation_report = f"""## Official river-barge movement context

| Metric | Week ending | Value | Evidence |
|---|---|---:|---|
| Downbound corn traffic | {barge_tons['observed_at']} | {_number(barge_tons['value'], 0)} short tons | {_citation(barge_tons)} |
| Four-week average | {barge_four_week_average['observed_at']} | {_number(barge_four_week_average['value'], 1)} short tons | {_citation(barge_four_week_average)} |
| Weekly change | {barge_weekly_change['observed_at']} | {_number(barge_weekly_change['value'], 2)}% | {_citation(barge_weekly_change)} |
| Annual change | {barge_yearly_change['observed_at']} | {_number(barge_yearly_change['value'], 2)}% | {_citation(barge_yearly_change)} |

This USDA/USACE measure sums corn traffic through Mississippi Locks 27, Ohio
Olmsted, and Arkansas Lock 1 to avoid double-counting sequential Mississippi
locks. Traffic volume is a logistics-flow indicator, not direct evidence of a
closure, delay, freight-rate shock, or directional price effect.
"""
        transportation_newsletter = (
            "USDA/USACE downbound corn barge traffic was "
            f"{_number(barge_tons['value'], 0)} short tons "
            f"{_citation(barge_tons)}, {_number(barge_weekly_change['value'], 2)}% "
            f"from the prior week {_citation(barge_weekly_change)} and "
            f"{_number(barge_yearly_change['value'], 2)}% from the comparable "
            f"prior-year week {_citation(barge_yearly_change)}. Volume alone "
            "does not establish a transportation disruption or price direction."
        )
    elif transportation_section.get("status") == "ready":
        transportation_report = """## Official river-barge movement context

The transportation feed was connected, but its cited corn metrics were
unavailable.
"""
        transportation_newsletter = (
            "The official river-barge feed was connected, but cited corn metrics "
            "were unavailable."
        )
    else:
        transportation_report = """## Official river-barge movement context — UNAVAILABLE

USDA/USACE corn barge movement evidence is unavailable for this run.
"""
        transportation_newsletter = (
            "Official USDA/USACE corn barge movement evidence is unavailable."
        )
    events_section = macro_section.get("events") or {}
    event_rows = []
    recent_event_sentence = ""
    for document in events_section.get("documents", []):
        event_fact = _find_optional_fact(evidence, document["metric"])
        if event_fact is None:
            continue
        title = _markdown_cell(document["title"])
        agencies = _markdown_cell(", ".join(document["agencies"]))
        categories = _markdown_cell(
            ", ".join(
                category.replace("_", " ")
                for category in document["categories"]
            )
        )
        event_rows.append(
            f"| {document['publication_date']} | {categories} | "
            f"[{title}]({document['html_url']}) | {agencies} | "
            f"{_citation(event_fact)} |"
        )
        if not recent_event_sentence:
            recent_event_sentence = (
                "The most recent matched Federal Register title is "
                f"“{document['title']}” {_citation(event_fact)}."
            )
    if event_rows:
        event_report = """## Official regulatory events

| Published | Category | Document | Agencies | Evidence |
|---|---|---|---|---|
""" + "\n".join(event_rows)
        event_newsletter = recent_event_sentence
    elif events_section.get("status") == "ready":
        event_report = """## Official regulatory events

No published document title matched the configured grain-policy phrases in
the current search window.
"""
        event_newsletter = (
            "The official Federal Register event feed is connected, but no "
            "configured document title matched in its current window."
        )
    else:
        event_report = """## Official regulatory events — UNAVAILABLE

The Federal Register grain-policy event feed is unavailable for this run.
"""
        event_newsletter = (
            "Official Federal Register grain-policy events are unavailable."
        )
    event_report += """

This deterministic title filter covers selected trade, biofuel, fertilizer,
grain-transportation, and grain-regulation releases. Weekly U.S. river-barge
volume is separately connected, but active lock and port disruptions, Black
Sea shipping, sanctions, China policy, and international crop estimates remain
incomplete.
"""

    macro_facts = (
        broad_dollar,
        wti_crude,
        treasury_10y,
        effective_fed_funds,
    )
    if all(fact is not None for fact in macro_facts):
        assert broad_dollar is not None
        assert wti_crude is not None
        assert treasury_10y is not None
        assert effective_fed_funds is not None
        macro_newsletter = (
            "The broad U.S. dollar index is "
            f"{_number(broad_dollar['value'], 4)} {_citation(broad_dollar)}, "
            f"WTI crude is ${_number(wti_crude['value'], 2)} per barrel "
            f"{_citation(wti_crude)}, the 10-year Treasury yield is "
            f"{_number(treasury_10y['value'], 2)}% {_citation(treasury_10y)}, "
            "and the effective federal funds rate is "
            f"{_number(effective_fed_funds['value'], 2)}% "
            f"{_citation(effective_fed_funds)}."
        )
        news_report = f"""# Grain news and macro context — PARTIAL

## Official macro observations

| Metric | Observation date | Value | Evidence |
|---|---|---:|---|
| Broad U.S. dollar index | {broad_dollar['observed_at']} | {_number(broad_dollar['value'], 4)} | {_citation(broad_dollar)} |
| WTI crude oil | {wti_crude['observed_at']} | ${_number(wti_crude['value'], 2)}/barrel | {_citation(wti_crude)} |
| 10-year Treasury yield | {treasury_10y['observed_at']} | {_number(treasury_10y['value'], 2)}% | {_citation(treasury_10y)} |
| Effective federal funds rate | {effective_fed_funds['observed_at']} | {_number(effective_fed_funds['value'], 2)}% | {_citation(effective_fed_funds)} |

These observations use FRED's public CSV feeds with a conservative seven-day
availability buffer. They provide currency, energy, and rate context only.

{event_report}

{transportation_report}
"""
    else:
        macro_newsletter = "Official macro observations are unavailable."
        news_report = f"""# Grain news and macro context — PARTIAL

Official macro observations are unavailable.

{event_report}

{transportation_report}
"""
    macro_newsletter = (
        f"{macro_newsletter} {transportation_newsletter} {event_newsletter}"
    )

    blocker_lines = "\n".join(
        f"- `{blocker['code']}`: {blocker['message']}"
        for blocker in blockers
    )
    source_lines = _source_lines(evidence)
    if prior_comparison and prior_comparison.get("status") == "ready":
        changes = prior_comparison["changes"]
        change_section = f"""The median projection changed by
${_number(changes["median_projected_price"], 4)}
[output_change_median_projected_price] from the most recent approved report.
Forecast confidence changed by
{_number(changes["forecast_confidence_score"], 1)} points
[output_change_forecast_confidence_score]. Bull, base, and bear probabilities
changed by {_number(changes["scenario_probability_bull"] * 100, 1)},
{_number(changes["scenario_probability_base"] * 100, 1)}, and
{_number(changes["scenario_probability_bear"] * 100, 1)} percentage points
[output_change_scenario_probability_bull]
[output_change_scenario_probability_base]
[output_change_scenario_probability_bear]."""
    else:
        change_section = (
            "No prior approved GrainAgents publication is available for a "
            "deterministic change comparison."
        )

    forecast_chart = _chart_markdown(
        charts_manifest,
        "forecast_fan.png",
        f"{symbol} calibrated forecast fan",
    )
    scenario_chart = _chart_markdown(
        charts_manifest,
        "scenario_probabilities.png",
        f"{symbol} scenario probabilities",
    )
    price_chart = _chart_markdown(
        charts_manifest,
        "price_technicals.png",
        f"{symbol} price and moving averages",
    )
    curve_chart = _chart_markdown(
        charts_manifest,
        "futures_curve.png",
        "CBOT corn futures curve",
    )
    seasonal_chart = _chart_markdown(
        charts_manifest,
        "seasonal_comparison.png",
        f"{symbol} indexed history",
    )
    stocks_chart = _chart_markdown(
        charts_manifest,
        "stocks_to_use.png",
        "U.S. corn stocks-to-use ratio",
    )
    weather_chart = _chart_markdown(
        charts_manifest,
        "weather_drought.png",
        "Corn-area drought exposure",
    )
    positioning_chart = _chart_markdown(
        charts_manifest,
        "cot_positioning.png",
        "CFTC corn positioning",
    )
    export_parts = []
    if weekly_exports is not None and target_export_commitment is not None:
        export_parts.append(
            "USDA FAS reports weekly corn exports of "
            f"{_number(weekly_exports['value'], 0)} metric tons "
            f"{_citation(weekly_exports)}. Target-marketing-year commitment is "
            f"{_number(target_export_commitment['value'], 0)} metric tons "
            f"{_citation(target_export_commitment)}."
        )
    else:
        export_parts.append(
            "USDA weekly export sales are not available for this run."
        )
    if weekly_inspections is not None:
        export_parts.append(
            "USDA FGIS reports weekly corn export inspections of "
            f"{_number(weekly_inspections['value'], 0)} metric tons "
            f"{_citation(weekly_inspections)}."
        )
    else:
        export_parts.append(
            "USDA weekly export inspections are not available for this run."
        )
    export_sales_text = " ".join(export_parts)
    export_gap_text = (
        "missing grain-news event evidence"
        if weekly_exports is not None and weekly_inspections is not None
        else "missing export-inspection and grain-news event evidence"
        if weekly_exports is not None
        else "missing export and grain-news event evidence"
    )
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
weather evidence, {export_gap_text}, and unverified
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
{forecast_chart}

## What changed this week

{change_section}

The current 20-session contract return is
{_number(return_20["value"], 2)}% {_citation(return_20)}.

## Technical picture

Settlement is ${_number(settlement["value"], 4)} per bushel
{_citation(settlement)}. The 20-session moving average is
${_number(sma_20["value"], 4)} {_citation(sma_20)}, the 50-session average is
${_number(sma_50["value"], 4)} {_citation(sma_50)}, and 14-session RSI is
{_number(rsi["value"], 1)} {_citation(rsi)}. Observed support is
${_number(support["value"], 4)} {_citation(support)} and resistance is
${_number(resistance["value"], 4)} {_citation(resistance)}.
{price_chart}
{curve_chart}
{seasonal_chart}

## Supply and demand

WASDE production is {_number(production["value"], 0)} million bushels
{_citation(production)}, ending stocks are
{_number(ending_stocks["value"], 0)} million bushels
{_citation(ending_stocks)}, and projected exports are
{_number(exports["value"], 0)} million bushels {_citation(exports)}.
{stocks_chart}

## Weather and yield risk

Moderate drought or worse covers {_number(drought["value"], 1)}% of corn area
{_citation(drought)}. The acreage-sample-weighted seven-day precipitation
forecast is {_number(precipitation["value"], 1)} mm
{_citation(precipitation)}. {extended_weather_text} {weather_anomaly_text}
{crop_condition_text}
{weather_chart}

## Export and domestic demand

Fuel-ethanol production is
{_number(ethanol_production["value"], 0)} thousand barrels per day
{_citation(ethanol_production)}, and stocks are
{_number(ethanol_stocks["value"], 0)} thousand barrels
{_citation(ethanol_stocks)}. {export_sales_text}

## Fund positioning

The all-month legacy CFTC noncommercial net position is
{_number(noncommercial_net["value"], 0)} contracts
{_citation(noncommercial_net)}.
{positioning_chart}

## Macro context

{macro_newsletter}

These values are contextual observations, not causal claims about corn prices.
Official grain-news event coverage remains incomplete.

## Bull, base, and bear cases

The bull, base, and bear probabilities are
{_number(probabilities["bull"] * 100, 1)}%,
{_number(probabilities["base"] * 100, 1)}%, and
{_number(probabilities["bear"] * 100, 1)}%, respectively
[output_scenario_bull_probability] [output_scenario_base_probability]
[output_scenario_bear_probability]. These probabilities are deterministic
transformations of the forecast distribution.
{scenario_chart}

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
        (newsletter, bull_bear, risk_report, news_report),
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
