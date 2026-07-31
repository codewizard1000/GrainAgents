from __future__ import annotations

from datetime import date, timedelta

import pytest

from tradingagents.commodities.evidence import build_evidence_package
from tradingagents.commodities.forecasting import (
    _rolling_performance,
    build_quantitative_forecast,
)


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
        assert horizon["prediction_interval_80"][0] <= horizon[
            "prediction_interval_50"
        ][0]
        assert horizon["prediction_interval_50"][1] <= horizon[
            "prediction_interval_80"
        ][1]
        assert (
            horizon["prediction_interval_method"]
            == "adaptive-conformal-score-registry-v2"
        )
        tree = horizon["models"]["regression_tree"]
        baseline_mae = min(
            model["rolling_mae"]
            for name, model in horizon["models"].items()
            if name != "regression_tree"
        )
        assert tree["model_family"] == "regression_tree"
        assert len(tree["feature_names"]) == 5
        assert tree["ensemble_eligible"] == (tree["rolling_mae"] < baseline_mae)
        if not tree["ensemble_eligible"]:
            assert tree["ensemble_weight"] == 0
        assert sum(
            model["ensemble_weight"]
            for model in horizon["models"].values()
        ) == pytest.approx(1, abs=1e-5)
        assert all(
            model["validation_observations"] > 0
            for model in horizon["models"].values()
        )
        performance = horizon["rolling_point_in_time_performance"]
        assert performance["evaluation_observations"] > 0
        assert performance["mean_absolute_error"] >= 0
        assert performance["mean_absolute_scaled_error"] >= 0
        assert 0 <= performance["directional_accuracy"] <= 1
        if performance["interval_evaluation_observations"]:
            assert performance["interval_coverage_50"] == pytest.approx(
                0.50,
                abs=0.05,
            )
            assert performance["interval_coverage_80"] == pytest.approx(
                0.80,
                abs=0.05,
            )
            assert performance["mean_quantile_loss"] >= 0
    assert first.scenarios["probability_total"] == 1
    registry = first.quantitative_forecast["performance_registry"]
    assert registry["methodology_version"] == (
        "adaptive-conformal-score-registry-v2"
    )
    assert len(registry["horizons"]) == 3
    assert any(
        fact["metric"] == "forecast_rolling_interval_coverage_80"
        for fact in first.evidence.to_dict()["facts"]
    )
    undercovered = [
        horizon
        for horizon in first.quantitative_forecast["forecast_horizons"]
        if (
            horizon["rolling_point_in_time_performance"]["interval_coverage_80"]
            is not None
            and horizon["rolling_point_in_time_performance"][
                "interval_coverage_80"
            ]
            < 0.70
        )
    ]
    assert len(
        [
            warning
            for warning in first.evidence.quality.warnings
            if "under-coverage" in warning
        ]
    ) == len(undercovered)
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


@pytest.mark.unit
def test_rolling_score_does_not_use_current_outcome_to_select_weights():
    prior_rows = [
        {
            "current_price": 0.0,
            "actual": 0.0,
            "predictions": {
                "random_walk": 0.0,
                "regression_tree": 1.0,
            },
            "naive_scale": 1.0,
            "recent_scale": 1.0,
        }
        for _ in range(30)
    ]
    current_row = {
        "current_price": 0.0,
        "actual": 0.0,
        "predictions": {
            "random_walk": 1_000_000.0,
            "regression_tree": 0.0,
        },
        "naive_scale": 1.0,
        "recent_scale": 1.0,
    }

    performance = _rolling_performance([*prior_rows, current_row])

    assert performance["evaluation_observations"] == 1
    assert performance["mean_absolute_error"] == 1_000_000
