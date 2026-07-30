from __future__ import annotations

from datetime import date, timedelta

import pytest

from tradingagents.commodities.evidence import build_evidence_package
from tradingagents.commodities.forecasting import build_quantitative_forecast


def _history(bar_count: int = 310) -> dict:
    start = date(2025, 9, 1)
    bars = []
    for index in range(bar_count):
        price = 4.25 + index * 0.001 + ((index % 10) - 5) * 0.002
        bars.append(
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "close": round(price, 6),
                "settlement": round(price, 6),
            }
        )
    return {
        "bars": bars,
        "price_unit": "USD_per_bushel",
        "retrieved_at": "2026-07-30T21:00:00+00:00",
        "start_date": bars[0]["date"],
        "end_date": bars[-1]["date"],
        "license_scope": "internal_testing_only",
    }


@pytest.mark.unit
def test_forecast_ensemble_is_deterministic_and_distribution_constrained():
    base = build_evidence_package(
        commodity="corn",
        contract_symbol="ZCZ26",
        as_of="2026-07-30",
        forecast_horizons=(5, 20, 60),
    )
    first = build_quantitative_forecast(base, history=_history())
    second = build_quantitative_forecast(base, history=_history())

    assert first.quantitative_forecast == second.quantitative_forecast
    assert len(first.quantitative_forecast["forecast_horizons"]) == 3
    for horizon in first.quantitative_forecast["forecast_horizons"]:
        assert horizon["prediction_interval_80"][0] <= horizon["median_projected_price"]
        assert horizon["median_projected_price"] <= horizon["prediction_interval_80"][1]
        assert sum(
            model["ensemble_weight"]
            for model in horizon["models"].values()
        ) == pytest.approx(1, abs=1e-5)
        assert all(
            model["validation_observations"] > 0
            for model in horizon["models"].values()
        )
    assert first.scenarios["probability_total"] == 1
    assert first.evidence.quality.status == "forecast_baseline_ready_publication_blocked"
    assert "forecast" not in first.evidence.quality.missing_core_data


@pytest.mark.unit
def test_forecast_refuses_short_history():
    base = build_evidence_package(
        commodity="corn",
        contract_symbol="ZCZ26",
        as_of="2026-07-30",
    )
    with pytest.raises(ValueError, match="at least 200"):
        build_quantitative_forecast(base, history=_history(150))
