from __future__ import annotations

import pytest

from tradingagents.commodities.publication import build_publication_bundle


def _fact(fact_id: str, metric: str, value, unit: str = "fixture") -> dict:
    return {
        "fact_id": fact_id,
        "metric": metric,
        "value": value,
        "unit": unit,
        "source_id": "source_fixture",
    }


def _evidence() -> dict:
    facts = [
        _fact("fact_zcz26_settlement", "official_settlement", 4.70),
        _fact("fact_zcz26_return_20d", "20_trading_day_return", 2.0),
        _fact("fact_zcz26_return_60d", "60_trading_day_return", -3.0),
        _fact("fact_zcz26_sma_20", "20_day_moving_average", 4.60),
        _fact("fact_zcz26_sma_50", "50_day_moving_average", 4.55),
        _fact("fact_zcz26_rsi_14", "rsi_14", 58.0),
        _fact("fact_zcz26_support_20", "support_20", 4.30),
        _fact("fact_zcz26_resistance_20", "resistance_20", 4.95),
        _fact(
            "fact_wasde_corn_production_2026_27",
            "wasde_corn_production",
            16000,
        ),
        _fact(
            "fact_wasde_corn_ending_stocks_2026_27",
            "wasde_corn_ending_stocks",
            1790,
        ),
        _fact(
            "fact_wasde_corn_exports_2026_27",
            "wasde_corn_exports",
            3200,
        ),
        _fact(
            "fact_eia_us_fuel_ethanol_production_2026_07_17",
            "eia_us_fuel_ethanol_production",
            1094,
        ),
        _fact(
            "fact_eia_us_fuel_ethanol_stocks_2026_07_17",
            "eia_us_fuel_ethanol_stocks",
            24481,
        ),
        _fact(
            "fact_corn_drought_d1_or_worse_percent_2026_07_28",
            "corn_drought_d1_or_worse_percent",
            39.0,
        ),
        _fact(
            "fact_corn_weather_precipitation_2026_07_30",
            "corn_weather_sample_weighted_7_day_precipitation_mm",
            28.4,
        ),
        _fact(
            "fact_cftc_corn_noncommercial_net_2026_07_21",
            "cftc_corn_noncommercial_net",
            186650,
        ),
        _fact(
            "fact_zcz26_first_notice_date",
            "first_notice_date",
            "2026-11-30",
        ),
        _fact(
            "fact_forecast_20d_median",
            "forecast_median_projected_price",
            4.72,
        ),
        _fact(
            "fact_forecast_20d_interval_80",
            "forecast_prediction_interval_80",
            [4.00, 5.40],
        ),
        _fact(
            "fact_forecast_20d_confidence",
            "forecast_confidence_score",
            35.0,
        ),
        _fact(
            "fact_forecast_20d_disagreement",
            "forecast_model_disagreement_score",
            90.0,
        ),
        _fact(
            "fact_forecast_20d_coverage_80",
            "forecast_rolling_interval_coverage_80",
            0.78,
        ),
    ]
    return {
        "run_id": "corn-zcz26-2026-07-30",
        "as_of": "2026-07-30T20:00:00-04:00",
        "instrument": {
            "symbol": "ZCZ26",
            "delivery_month_name": "December",
        },
        "facts": facts,
        "sources": [
            {
                "provider": "Databento",
                "dataset": "GLBX.MDP3",
                "license_scope": "internal_testing_only",
            },
            {
                "provider": "USDA",
                "dataset": "WASDE",
                "license_scope": "official_public_data",
                "source_url": "https://example.invalid/wasde",
            },
        ],
        "macro": {},
        "quality": {
            "missing_core_data": ["weather"],
            "stale_sources": [],
            "contradictions": [],
            "warnings": [],
        },
    }


def _quantitative() -> dict:
    return {
        "status": "ready_research_only",
        "forecast_horizons": [
            {
                "horizon_trading_days": 20,
                "current_price": 4.70,
                "median_projected_price": 4.72,
                "prediction_interval_80": [4.00, 5.40],
                "forecast_confidence_score": 35.0,
                "model_disagreement_score": 90.0,
            }
        ],
    }


def _scenarios() -> dict:
    return {
        "probabilities": {
            "bull": 0.20,
            "base": 0.65,
            "bear": 0.15,
        }
    }


@pytest.mark.unit
def test_publication_bundle_is_evidence_linked_and_blocked():
    bundle = build_publication_bundle(
        _evidence(),
        quantitative=_quantitative(),
        scenarios=_scenarios(),
    )

    assert bundle.publication_status["status"] == "blocked"
    assert bundle.publication_status["publication_ready"] is False
    assert bundle.publication_status["human_approval_required"] is True
    blocker_codes = {
        blocker["code"]
        for blocker in bundle.publication_status["blockers"]
    }
    assert "missing_core_data:weather" in blocker_codes
    assert "market_data_redistribution_rights_unverified" in blocker_codes
    assert "forecast_research_only" in blocker_codes
    assert bundle.newsletter.startswith("# DRAFT — NOT APPROVED")
    assert "[fact_forecast_20d_interval_80]" in bundle.newsletter
    assert "[output_scenario_base_probability]" in bundle.newsletter
    assert bundle.final_outlook["publication_ready"] is False
    assert bundle.final_outlook["scenario_probabilities"]["base"] == 0.65


@pytest.mark.unit
def test_publication_reference_horizon_uses_matching_fact_ids():
    bundle = build_publication_bundle(
        _evidence(),
        quantitative=_quantitative(),
        scenarios=_scenarios(),
    )

    assert "fact_forecast_20d_median" in bundle.final_outlook["evidence_refs"]
    assert "fact_forecast_20d_interval_80" in bundle.final_outlook[
        "evidence_refs"
    ]


@pytest.mark.unit
def test_publication_uses_export_sales_and_retains_inspections_blocker():
    evidence = _evidence()
    evidence["demand"] = {
        "status": "partial",
        "export_sales": {"status": "ready"},
        "missing": ["export_inspections"],
    }
    evidence["facts"].extend(
        [
            _fact(
                "fact_fas_corn_weekly_exports_2026_07_23",
                "fas_corn_weekly_exports",
                1234567,
            ),
            _fact(
                "fact_fas_corn_target_marketing_year_commitment_2026_07_23",
                "fas_corn_target_marketing_year_commitment",
                2345678,
            ),
        ]
    )

    bundle = build_publication_bundle(
        evidence,
        quantitative=_quantitative(),
        scenarios=_scenarios(),
    )
    blocker_codes = {
        blocker["code"]
        for blocker in bundle.publication_status["blockers"]
    }

    assert "export_sales_unavailable" not in blocker_codes
    assert "export_inspections_not_implemented" in blocker_codes
    assert "1,234,567 metric tons" in bundle.newsletter
    assert "[fact_fas_corn_weekly_exports_2026_07_23]" in bundle.newsletter


@pytest.mark.unit
def test_publication_clears_export_blockers_when_both_feeds_are_ready():
    evidence = _evidence()
    evidence["demand"] = {
        "status": "ready",
        "export_sales": {"status": "ready"},
        "export_inspections": {"status": "ready"},
        "missing": [],
    }
    evidence["facts"].extend(
        [
            _fact(
                "fact_fas_corn_weekly_exports_2026_07_23",
                "fas_corn_weekly_exports",
                1234567,
            ),
            _fact(
                "fact_fas_corn_target_marketing_year_commitment_2026_07_23",
                "fas_corn_target_marketing_year_commitment",
                2345678,
            ),
            _fact(
                "fact_ams_corn_weekly_inspections_2026_07_23",
                "ams_corn_weekly_inspections",
                1488028,
            ),
        ]
    )

    bundle = build_publication_bundle(
        evidence,
        quantitative=_quantitative(),
        scenarios=_scenarios(),
    )
    blocker_codes = {
        blocker["code"]
        for blocker in bundle.publication_status["blockers"]
    }

    assert "export_sales_unavailable" not in blocker_codes
    assert "export_inspections_not_implemented" not in blocker_codes
    assert "1,488,028 metric tons" in bundle.newsletter
    assert "[fact_ams_corn_weekly_inspections_2026_07_23]" in bundle.newsletter


@pytest.mark.unit
def test_publication_renders_macro_events_and_marks_news_coverage_partial():
    evidence = _evidence()
    evidence["macro"] = {
        "status": "ready",
        "coverage_status": "partial",
        "missing": ["black_sea_shipping", "china_policy"],
        "events": {
            "status": "ready",
            "coverage_status": "partial",
            "documents": [
                {
                    "document_number": "2026-14772",
                    "title": "Notice of National Grain Car Council Meeting",
                    "publication_date": "2026-07-22",
                    "agencies": ["Surface Transportation Board"],
                    "categories": ["grain_transportation"],
                    "html_url": "https://example.test/2026-14772",
                    "metric": "federal_register_grain_event_2026_14772",
                }
            ],
        },
        "transportation": {
            "status": "ready",
            "coverage_status": "partial",
            "values": {"weekly_downbound_barge_tons": 519600},
        },
    }
    macro_facts = [
        _fact(
            "fact_fred_broad_us_dollar_index_2026_07_23",
            "fred_broad_us_dollar_index",
            120.9075,
        ),
        _fact(
            "fact_fred_wti_crude_oil_usd_per_barrel_2026_07_23",
            "fred_wti_crude_oil_usd_per_barrel",
            93.08,
        ),
        _fact(
            "fact_fred_10y_treasury_percent_2026_07_23",
            "fred_10y_treasury_percent",
            4.71,
        ),
        _fact(
            "fact_fred_effective_federal_funds_rate_percent_2026_07_23",
            "fred_effective_federal_funds_rate_percent",
            3.63,
        ),
        _fact(
            "fact_federal_register_grain_event_2026_14772_2026_07_22",
            "federal_register_grain_event_2026_14772",
            "Notice of National Grain Car Council Meeting",
        ),
        _fact(
            "fact_ams_corn_weekly_downbound_barge_tons_2026_07_25",
            "ams_corn_weekly_downbound_barge_tons",
            519600,
        ),
        _fact(
            "fact_ams_corn_four_week_average_downbound_barge_tons_2026_07_25",
            "ams_corn_four_week_average_downbound_barge_tons",
            455162.5,
        ),
        _fact(
            "fact_ams_corn_week_over_week_change_percent_2026_07_25",
            "ams_corn_week_over_week_change_percent",
            4.274533,
        ),
        _fact(
            "fact_ams_corn_year_over_year_change_percent_2026_07_25",
            "ams_corn_year_over_year_change_percent",
            10.718091,
        ),
    ]
    for fact in macro_facts:
        fact["observed_at"] = "2026-07-23"
    evidence["facts"].extend(macro_facts)

    bundle = build_publication_bundle(
        evidence,
        quantitative=_quantitative(),
        scenarios=_scenarios(),
    )
    blocker_codes = {
        blocker["code"]
        for blocker in bundle.publication_status["blockers"]
    }

    assert "macro_evidence_unavailable" not in blocker_codes
    assert "grain_news_not_implemented" not in blocker_codes
    assert "grain_news_coverage_incomplete" in blocker_codes
    assert "Grain news and macro context — PARTIAL" in bundle.news_report
    assert "120.9075" in bundle.newsletter
    assert "Notice of National Grain Car Council Meeting" in bundle.news_report
    assert "519,600 short tons" in bundle.newsletter
    assert "Official river-barge movement context" in bundle.news_report
    assert "Volume alone does not establish" in bundle.newsletter
    assert "[fact_ams_corn_weekly_downbound_barge_tons_2026_07_25]" in (
        bundle.news_report
    )
    assert "[fact_federal_register_grain_event_2026_14772_2026_07_22]" in (
        bundle.newsletter
    )
    assert "[fact_fred_wti_crude_oil_usd_per_barrel_2026_07_23]" in (
        bundle.news_report
    )


@pytest.mark.unit
def test_publication_renders_cpc_8_14_day_outlook_facts():
    evidence = _evidence()
    evidence["weather"] = {
        "outlook_8_14_day": {
            "valid_start": "2026-08-07",
            "valid_end": "2026-08-13",
            "temperature": {"dominant_category": "above_normal"},
            "precipitation": {"dominant_category": "below_normal"},
        }
    }
    evidence["facts"].extend(
        [
            _fact(
                "fact_cpc_corn_8_14_day_temperature_above_normal_acre_share_2026_08_07_2026_08_13",
                "cpc_corn_8_14_day_temperature_above_normal_acre_share",
                75.0,
            ),
            _fact(
                "fact_cpc_corn_8_14_day_precipitation_below_normal_acre_share_2026_08_07_2026_08_13",
                "cpc_corn_8_14_day_precipitation_below_normal_acre_share",
                60.0,
            ),
        ]
    )

    bundle = build_publication_bundle(
        evidence,
        quantitative=_quantitative(),
        scenarios=_scenarios(),
    )

    assert "CPC 8-14 day dominant category" in bundle.newsletter
    assert "above normal temperature" in bundle.newsletter
    assert "below normal precipitation" in bundle.newsletter
    assert (
        "[fact_cpc_corn_8_14_day_temperature_"
        "above_normal_acre_share_2026_08_07_2026_08_13]"
        in bundle.newsletter
    )


@pytest.mark.unit
def test_publication_renders_seven_day_weather_anomalies():
    evidence = _evidence()
    evidence["facts"].extend(
        [
            _fact(
                "fact_corn_weather_temperature_anomaly",
                "corn_weather_sample_weighted_7_day_temperature_anomaly_c",
                2.4,
            ),
            _fact(
                "fact_corn_weather_precipitation_anomaly",
                "corn_weather_sample_weighted_7_day_precipitation_anomaly_mm",
                -8.3,
            ),
        ]
    )

    bundle = build_publication_bundle(
        evidence,
        quantitative=_quantitative(),
        scenarios=_scenarios(),
    )

    assert "NCEI 1991-2020 daily normals" in bundle.newsletter
    assert "temperature anomaly is 2.4 C" in bundle.newsletter
    assert "precipitation anomaly is -8.3 mm" in bundle.newsletter
    assert "[fact_corn_weather_temperature_anomaly]" in bundle.newsletter
    assert "Temperature and rainfall anomalies" not in bundle.newsletter


@pytest.mark.unit
def test_publication_renders_condition_based_weather_risk():
    evidence = _evidence()
    evidence["facts"].extend(
        [
            _fact(
                "fact_corn_crop_condition_risk",
                "corn_crop_condition_based_weather_risk_score",
                34.75,
            ),
            _fact(
                "fact_corn_crop_condition_risk_change",
                "corn_crop_condition_based_weather_risk_change_week_over_week",
                2.75,
            ),
            _fact(
                "fact_corn_crop_good_excellent",
                "corn_crop_good_excellent_percent",
                63,
            ),
            _fact(
                "fact_corn_crop_silking",
                "corn_crop_silking_percent",
                78,
            ),
            _fact(
                "fact_corn_crop_dough",
                "corn_crop_dough_percent",
                25,
            ),
        ]
    )

    bundle = build_publication_bundle(
        evidence,
        quantitative=_quantitative(),
        scenarios=_scenarios(),
    )

    assert "condition-based weather-risk index is 34.75" in bundle.newsletter
    assert "weekly change of 2.75 points" in bundle.newsletter
    assert "63% of reported corn acres good or excellent" in bundle.newsletter
    assert "This monitoring index is not yield calibrated" in bundle.newsletter
    assert "[fact_corn_crop_condition_risk]" in bundle.newsletter
