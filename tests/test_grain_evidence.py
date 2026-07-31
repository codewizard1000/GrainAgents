from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from tradingagents.commodities.evidence import build_evidence_package


@pytest.mark.unit
def test_builds_point_in_time_evidence_skeleton():
    evidence = build_evidence_package(
        commodity="corn",
        contract_symbol="ZCZ26",
        as_of="2026-07-30",
        forecast_horizons=[5, 20, 60],
    )
    payload = evidence.to_dict()

    assert evidence.run_id == "corn-zcz26-2026-07-30"
    assert evidence.as_of.tzinfo is not None
    assert evidence.forecast_horizons == (5, 20, 60)
    assert payload["instrument"]["symbol"] == "ZCZ26"
    assert payload["quality"]["status"] == "incomplete"
    assert "contract_market_history" in payload["quality"]["missing_core_data"]
    assert payload["facts"] == []
    assert payload["sources"] == []


@pytest.mark.unit
def test_evidence_package_is_immutable():
    evidence = build_evidence_package(
        commodity="corn",
        contract_symbol="ZCZ26",
        as_of="2026-07-30",
    )
    with pytest.raises(FrozenInstanceError):
        evidence.run_id = "changed"
    with pytest.raises(TypeError):
        evidence.market["settlement"] = 4.25


@pytest.mark.unit
def test_horizons_must_be_positive():
    with pytest.raises(ValueError, match="positive"):
        build_evidence_package(
            commodity="corn",
            contract_symbol="ZCZ26",
            as_of="2026-07-30",
            forecast_horizons=[0, 20],
        )


@pytest.mark.unit
def test_current_date_keeps_the_local_analysis_date():
    today = date.today()
    evidence = build_evidence_package(
        commodity="corn",
        contract_symbol="ZCZ26",
        as_of=today.isoformat(),
    )

    assert evidence.as_of.date() == today
