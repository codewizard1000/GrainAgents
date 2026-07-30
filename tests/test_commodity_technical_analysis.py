from __future__ import annotations

from datetime import date, timedelta

import pytest

from tradingagents.commodities.analysis import (
    active_curve_contracts,
    build_technical_evidence,
    latest_complete_market_date,
)
from tradingagents.commodities.contracts import resolve_contract
from tradingagents.commodities.evidence import build_evidence_package
from tradingagents.commodities.technical import calculate_technical_metrics


def _history(
    contract_symbol: str = "ZCZ26",
    *,
    end_date: str = "2026-07-30",
    bar_count: int = 230,
) -> dict:
    end = date.fromisoformat(end_date)
    bars = []
    for index in range(bar_count):
        bar_date = end - timedelta(days=bar_count - index - 1)
        price = round(4.0 + index * 0.002, 4)
        available_at = f"{bar_date.isoformat()}T21:00:00+00:00"
        bars.append(
            {
                "date": bar_date.isoformat(),
                "contract_symbol": contract_symbol,
                "vendor_symbol": f"{contract_symbol[:-2]}{contract_symbol[-1]}",
                "open": price - 0.01,
                "high": price + 0.02,
                "low": price - 0.02,
                "close": price,
                "settlement": price,
                "volume": 100_000 + index,
                "bar_volume": 100_000 + index,
                "cleared_volume": 100_000 + index,
                "open_interest": 500_000 + index,
                "bar_ts_event": f"{bar_date.isoformat()}T00:00:00+00:00",
                "settlement_available_at": available_at,
                "cleared_volume_available_at": available_at,
                "open_interest_available_at": available_at,
            }
        )
    return {
        "schema_version": "1.0",
        "provider": "databento",
        "dataset": "GLBX.MDP3",
        "data_scope": "delivery_specific",
        "license_scope": "internal_testing_only",
        "requested_symbol": contract_symbol,
        "vendor_symbol": f"{contract_symbol[:-2]}{contract_symbol[-1]}",
        "start_date": bars[0]["date"],
        "end_date": end_date,
        "retrieved_at": "2026-07-30T22:00:00+00:00",
        "price_unit": "USD_per_bushel",
        "volume_unit": "contracts",
        "definition": None,
        "bars": bars,
        "source_record_counts": {
            "ohlcv-1d": bar_count,
            "statistics": bar_count * 3,
            "definition": 1,
        },
        "warnings": [],
    }


@pytest.mark.unit
def test_calculates_ready_delivery_contract_technicals():
    metrics = calculate_technical_metrics(_history(), as_of="2026-07-30")

    assert metrics["quality"]["status"] == "ready"
    assert metrics["bar_count"] == 230
    assert metrics["moving_averages"]["200"] is not None
    assert metrics["momentum"]["rsi_14"] == 100.0
    assert metrics["trend"]["classification"] == "above_20_and_50_day_averages"
    assert metrics["participation"]["open_interest_change_5"] == 5
    assert metrics["latest"]["settlement"] == 4.458


@pytest.mark.unit
def test_blocks_insufficient_or_non_delivery_specific_history():
    short_history = _history(bar_count=50)
    metrics = calculate_technical_metrics(short_history, as_of="2026-07-30")
    assert metrics["quality"]["status"] == "blocked"
    assert "at_least_200_daily_bars" in metrics["quality"]["missing"]

    short_history["data_scope"] = "continuous"
    with pytest.raises(ValueError, match="delivery-specific"):
        calculate_technical_metrics(short_history, as_of="2026-07-30")


@pytest.mark.unit
def test_resolves_the_active_corn_curve_in_delivery_order():
    primary = resolve_contract("ZCZ26", as_of="2026-07-30")
    contracts = active_curve_contracts(primary, as_of=date(2026, 7, 30))
    assert [contract.symbol for contract in contracts] == [
        "ZCU26",
        "ZCZ26",
        "ZCH27",
        "ZCK27",
        "ZCN27",
    ]


@pytest.mark.unit
def test_current_day_run_uses_the_previous_complete_weekday():
    assert latest_complete_market_date(
        date(2026, 7, 30),
        today=date(2026, 7, 30),
    ) == date(2026, 7, 29)
    assert latest_complete_market_date(
        date(2026, 8, 3),
        today=date(2026, 8, 3),
    ) == date(2026, 7, 31)
    with pytest.raises(ValueError, match="later than"):
        latest_complete_market_date(
            date(2026, 7, 31),
            today=date(2026, 7, 30),
        )


@pytest.mark.unit
def test_builds_immutable_evidence_with_curve_and_fact_ids():
    base = build_evidence_package(
        commodity="corn",
        contract_symbol="ZCZ26",
        as_of="2026-07-30",
    )
    requested: list[str] = []

    def loader(**kwargs):
        requested.append(kwargs["contract_symbol"])
        return _history(kwargs["contract_symbol"], end_date=kwargs["end_date"])

    run = build_technical_evidence(
        base,
        history_loader=loader,
        today=date(2026, 7, 30),
    )
    payload = run.evidence.to_dict()

    assert set(requested) == {"ZCZ26", "ZCU26", "ZCH27", "ZCK27", "ZCN27"}
    assert payload["quality"]["status"] == "technical_ready_publication_blocked"
    assert len(payload["curve"]["points"]) == 5
    assert payload["curve"]["structure"]["classification"] == "flat"
    assert any(fact["fact_id"] == "fact_zcz26_settlement" for fact in payload["facts"])
    assert any(
        fact["fact_id"] == "fact_zcz26_days_to_expiration"
        for fact in payload["facts"]
    )

    with pytest.raises(TypeError):
        run.evidence.technical["latest"]["settlement"] = 99
