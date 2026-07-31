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
