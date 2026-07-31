from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.commodities.evidence import build_evidence_package
from tradingagents.commodities.forecast_vintages import (
    ForecastVintage,
    attach_live_performance,
    build_forecast_vintage,
    load_prior_forecast_vintages,
    score_saved_forecasts,
    write_immutable_forecast_vintage,
)
from tradingagents.commodities.forecasting import ForecastEvidenceRun

UTC = timezone.utc


def _quantitative(
    *,
    as_of: str = "2026-01-05T21:00:00+00:00",
    current: float = 10.0,
    projected: float = 12.0,
    horizon: int = 2,
    model_version: str = "fixture-model-v1",
) -> dict:
    return {
        "contract_symbol": "ZCZ26",
        "as_of": as_of,
        "price_unit": "USD_per_bushel",
        "model_version": model_version,
        "forecast_horizons": [
            {
                "horizon_trading_days": horizon,
                "current_price": current,
                "median_projected_price": projected,
                "prediction_interval_50": [projected - 0.5, projected + 0.5],
                "prediction_interval_80": [projected - 1.0, projected + 1.0],
            }
        ],
    }


def _vintage(
    *,
    as_of: str = "2026-01-05T21:00:00+00:00",
    origin: str = "2026-01-05",
    current: float = 10.0,
    projected: float = 12.0,
    horizon: int = 2,
    model_version: str = "fixture-model-v1",
) -> ForecastVintage:
    return build_forecast_vintage(
        _quantitative(
            as_of=as_of,
            current=current,
            projected=projected,
            horizon=horizon,
            model_version=model_version,
        ),
        run_id=f"fixture-{origin}",
        commodity="corn",
        origin_market_date=origin,
    )


@pytest.mark.unit
def test_forecast_vintage_is_content_addressed_and_tamper_evident(tmp_path):
    vintage = _vintage()
    path = write_immutable_forecast_vintage(tmp_path, vintage)

    assert ForecastVintage.from_dict(
        json.loads(path.read_text(encoding="utf-8"))
    ) == vintage
    assert path.name == f"{vintage.vintage_id}.json"

    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["current_price"] = 999
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity validation"):
        write_immutable_forecast_vintage(tmp_path, vintage)
    assert json.loads(path.read_text(encoding="utf-8"))["current_price"] == 999

    invalid = _quantitative()
    invalid["forecast_horizons"][0]["prediction_interval_80"] = [13, 11]
    with pytest.raises(ValueError, match="intervals are not nested"):
        build_forecast_vintage(
            invalid,
            run_id="fixture-invalid",
            commodity="corn",
            origin_market_date="2026-01-05",
        )


@pytest.mark.unit
def test_prior_vintage_loader_excludes_current_as_of_and_validates_identity(tmp_path):
    prior = _vintage()
    current = _vintage(
        as_of="2026-01-07T21:00:00+00:00",
        origin="2026-01-07",
    )
    later_same_origin = _vintage(
        as_of="2026-01-06T21:00:00+00:00",
        origin="2026-01-05",
        projected=13,
    )
    prior_dir = tmp_path / "corn" / "ZCZ26" / "2026-01-05" / "forecast_vintages"
    current_dir = tmp_path / "corn" / "ZCZ26" / "2026-01-07" / "forecast_vintages"
    rerun_dir = tmp_path / "corn" / "ZCZ26" / "2026-01-06" / "forecast_vintages"
    write_immutable_forecast_vintage(prior_dir, prior)
    write_immutable_forecast_vintage(rerun_dir, later_same_origin)
    write_immutable_forecast_vintage(current_dir, current)

    loaded = load_prior_forecast_vintages(
        tmp_path,
        commodity="corn",
        contract_symbol="ZCZ26",
        before=datetime(2026, 1, 7, 21, tzinfo=UTC),
    )

    assert loaded == (prior,)


@pytest.mark.unit
def test_live_scoring_uses_only_matured_prior_vintages_and_available_bars():
    prior = _vintage()
    current = _vintage(
        as_of="2026-01-07T21:00:00+00:00",
        origin="2026-01-07",
        current=12,
        projected=14,
    )
    old_model = _vintage(
        as_of="2026-01-06T21:00:00+00:00",
        origin="2026-01-06",
        current=11,
        projected=100,
        horizon=1,
        model_version="legacy-model-v0",
    )
    history = {
        "requested_symbol": "ZCZ26",
        "bars": [
            {"date": "2026-01-05", "settlement": 10.0},
            {"date": "2026-01-06", "settlement": 11.0},
            {"date": "2026-01-07", "settlement": 12.0},
            {
                "date": "2026-01-08",
                "settlement": 999.0,
                "settlement_available_at": "2026-01-08T21:00:00+00:00",
            },
        ],
    }

    registry = score_saved_forecasts(
        (prior, old_model, current),
        history=history,
        as_of=datetime(2026, 1, 7, 21, tzinfo=UTC),
        requested_horizons=(2,),
        model_version="fixture-model-v1",
        minimum_scores_per_horizon=1,
    )

    assert registry["matured_score_rows"] == 1
    assert registry["scores"][0]["vintage_id"] == prior.vintage_id
    assert registry["scores"][0]["target_market_date"] == "2026-01-07"
    assert registry["scores"][0]["actual_price"] == 12
    assert registry["horizons"][0]["relative_absolute_error"] == 0
    assert registry["status"] == "ready_for_publication"


@pytest.mark.unit
def test_live_scoring_requires_every_requested_horizon_to_mature_and_pass():
    start = datetime(2026, 1, 1, 21, tzinfo=UTC)
    bars = []
    vintages = []
    for index in range(5):
        bar_date = (start + timedelta(days=index)).date().isoformat()
        price = 10.0 + index
        bars.append({"date": bar_date, "settlement": price})
        if index < 2:
            vintages.append(
                _vintage(
                    as_of=(start + timedelta(days=index)).isoformat(),
                    origin=bar_date,
                    current=price,
                    projected=price + 1,
                    horizon=1,
                )
            )

    registry = score_saved_forecasts(
        tuple(vintages),
        history={"requested_symbol": "ZCZ26", "bars": bars},
        as_of=datetime(2026, 1, 5, 21, tzinfo=UTC),
        requested_horizons=(1, 5),
        minimum_scores_per_horizon=2,
    )

    one_day, five_day = registry["horizons"]
    assert one_day["scored_forecasts"] == 2
    assert one_day["publication_eligible"] is True
    assert five_day["scored_forecasts"] == 0
    assert five_day["publication_eligible"] is False
    assert registry["status"] == "insufficient_live_scores"


@pytest.mark.unit
def test_live_registry_promotes_forecast_only_after_registry_is_ready():
    base = build_evidence_package(
        commodity="corn",
        contract_symbol="ZCZ26",
        as_of="2026-01-05",
    )
    run = ForecastEvidenceRun(
        evidence=base,
        quantitative_forecast={
            "status": "ready_research_only",
            "limitations": [
                "No claim of calibrated live trading performance is made until "
                "forecasts are scored out of sample."
            ],
        },
        scenarios={},
    )
    registry = {"status": "ready_for_publication"}

    promoted = attach_live_performance(run, registry)

    assert promoted.quantitative_forecast["status"] == "ready_for_publication"
    assert promoted.evidence.to_dict()["forecast"] == {
        "live_performance_registry": registry,
        "status": "ready_for_publication",
        "limitations": [],
    }
