from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from tradingagents.commodities.charts import generate_publication_charts
from tradingagents.commodities.comparison import (
    build_prior_report_comparison,
    find_prior_approved_outlook,
)


def _chart_fixture():
    symbol = "ZCZ26"
    start = date(2026, 4, 1)
    bars = [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "settlement": 4.20 + index * 0.005,
            "close": 4.20 + index * 0.005,
        }
        for index in range(90)
    ]
    curve_symbols = ["ZCU26", "ZCZ26", "ZCH27"]
    facts = [
        {"fact_id": f"fact_{symbol.lower()}_settlement"},
        {"fact_id": f"fact_{symbol.lower()}_sma_20"},
        {"fact_id": f"fact_{symbol.lower()}_sma_50"},
        *[
            {"fact_id": f"fact_curve_{contract.lower()}"}
            for contract in curve_symbols
        ],
        {
            "fact_id": "fact_cftc_corn_noncommercial_long_2026_07_21",
        },
        {
            "fact_id": "fact_cftc_corn_noncommercial_short_2026_07_21",
        },
        {
            "fact_id": "fact_cftc_corn_noncommercial_net_2026_07_21",
        },
        *[
            {"fact_id": f"fact_forecast_{horizon}d_{suffix}"}
            for horizon in (5, 20, 60)
            for suffix in ("median", "interval_50", "interval_80")
        ],
        *[
            {
                "fact_id": (
                    f"fact_corn_drought_d{level}_or_worse_percent_2026_07_28"
                )
            }
            for level in range(5)
        ],
        {
            "fact_id": "fact_wasde_corn_ending_stocks_2026_27",
        },
        {
            "fact_id": "fact_wasde_corn_total_use_2026_27",
        },
    ]
    evidence = {
        "as_of": "2026-07-30T20:00:00-04:00",
        "instrument": {"symbol": symbol},
        "facts": facts,
        "curve": {
            "points": [
                {
                    "contract_symbol": contract,
                    "curve_price": 4.50 + index * 0.15,
                }
                for index, contract in enumerate(curve_symbols)
            ]
        },
        "positioning": {
            "report_date": "2026-07-21",
            "values": {
                "noncommercial_long": 490_000,
                "noncommercial_short": 305_000,
                "noncommercial_net": 185_000,
            },
        },
        "weather": {
            "values": {
                "d0_or_worse_percent": 52.0,
                "d1_or_worse_percent": 39.0,
                "d2_or_worse_percent": 20.0,
                "d3_or_worse_percent": 6.0,
                "d4_or_worse_percent": 0.0,
                "drought_valid_date": "2026-07-28",
            }
        },
        "supply_demand": {
            "crop_year": "2026/27",
            "values": {
                "ending_stocks": 1790,
                "total_use": 16255,
            },
        },
    }
    history = {
        "provider": "fixture",
        "dataset": "daily-settlement",
        "bars": bars,
    }
    quantitative = {
        "model_version": "fixture-model-v1",
        "forecast_horizons": [
            {
                "horizon_trading_days": horizon,
                "current_price": 4.65,
                "median_projected_price": 4.65 + horizon * 0.001,
                "prediction_interval_50": [
                    4.50 - horizon * 0.002,
                    4.80 + horizon * 0.002,
                ],
                "prediction_interval_80": [
                    4.35 - horizon * 0.004,
                    4.95 + horizon * 0.004,
                ],
            }
            for horizon in (5, 20, 60)
        ],
    }
    scenarios = {
        "method": "fixture deterministic scenario method",
        "probabilities": {"bull": 0.20, "base": 0.65, "bear": 0.15},
    }
    return evidence, history, quantitative, scenarios


@pytest.mark.unit
def test_chart_package_writes_pngs_and_auditable_metadata(tmp_path):
    evidence, history, quantitative, scenarios = _chart_fixture()

    manifest = generate_publication_charts(
        tmp_path,
        evidence=evidence,
        history=history,
        quantitative=quantitative,
        scenarios=scenarios,
    )

    assert manifest["chart_version"] == "grain-publication-charts-v1"
    assert len(manifest["charts"]) == 8
    assert {
        chart["filename"]
        for chart in manifest["charts"]
    } == {
        "price_technicals.png",
        "futures_curve.png",
        "cot_positioning.png",
        "seasonal_comparison.png",
        "forecast_fan.png",
        "scenario_probabilities.png",
        "weather_drought.png",
        "stocks_to_use.png",
    }
    for chart in manifest["charts"]:
        path = tmp_path / chart["filename"]
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert path.stat().st_size > 10_000
        assert chart["contract_symbol"] == "ZCZ26"
        assert chart["as_of"].startswith("2026-07-30")
        assert chart["unit"]
        assert chart["sources"]
        assert len(chart["sha256"]) == 64
    seasonal = next(
        chart
        for chart in manifest["charts"]
        if chart["filename"] == "seasonal_comparison.png"
    )
    assert seasonal["status"] == "partial"
    assert "multi-year" in seasonal["limitation"]


def _outlook(run_id: str, as_of: str, *, median: float) -> dict:
    return {
        "run_id": run_id,
        "contract_symbol": "ZCZ26",
        "as_of": as_of,
        "directional_bias": "neutral",
        "median_projected_price": median,
        "forecast_confidence_score": 40.0,
        "model_disagreement_score": 70.0,
        "prediction_interval_80": [4.0, 5.4],
        "scenario_probabilities": {
            "bull": 0.20,
            "base": 0.65,
            "bear": 0.15,
        },
    }


@pytest.mark.unit
def test_prior_comparison_uses_latest_earlier_approved_run(tmp_path):
    older = tmp_path / "corn" / "ZCZ26" / "2026-07-16"
    latest = tmp_path / "corn" / "ZCZ26" / "2026-07-23"
    blocked = tmp_path / "corn" / "ZCZ26" / "2026-07-29"
    for directory, status, median in (
        (older, "approved", 4.50),
        (latest, "approved", 4.60),
        (blocked, "blocked", 4.90),
    ):
        directory.mkdir(parents=True)
        (directory / "publication_status.json").write_text(
            json.dumps(
                {
                    "status": status,
                    "publication_ready": status == "approved",
                }
            ),
            encoding="utf-8",
        )
        (directory / "final_outlook.json").write_text(
            json.dumps(
                _outlook(
                    f"run-{directory.name}",
                    f"{directory.name}T20:00:00-04:00",
                    median=median,
                )
            ),
            encoding="utf-8",
        )

    prior = find_prior_approved_outlook(
        tmp_path,
        commodity="corn",
        contract_symbol="ZCZ26",
        current_date=date(2026, 7, 30),
    )
    comparison = build_prior_report_comparison(
        _outlook(
            "run-2026-07-30",
            "2026-07-30T20:00:00-04:00",
            median=4.72,
        ),
        prior,
    )

    assert prior["run_id"] == "run-2026-07-23"
    assert comparison["status"] == "ready"
    assert comparison["prior_run_id"] == "run-2026-07-23"
    assert comparison["changes"]["median_projected_price"] == 0.12


@pytest.mark.unit
def test_prior_comparison_is_explicitly_unavailable_without_approval():
    comparison = build_prior_report_comparison(
        _outlook(
            "run-2026-07-30",
            "2026-07-30T20:00:00-04:00",
            median=4.72,
        ),
        None,
    )

    assert comparison["status"] == "unavailable"
    assert comparison["reason"] == "no_prior_approved_report"
    assert comparison["changes"] == {}
