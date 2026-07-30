from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from tradingagents.commodities.evidence import build_evidence_package
from tradingagents.commodities.official.cftc import (
    conservative_available_at,
    parse_corn_cot,
)
from tradingagents.commodities.official.eia import load_corn_ethanol
from tradingagents.commodities.official.models import OfficialSnapshot
from tradingagents.commodities.official.pipeline import build_official_evidence
from tradingagents.commodities.official.wasde import (
    WasdeRelease,
    parse_corn_balance,
    parse_release_index,
)
from tradingagents.commodities.official.weather import load_corn_weather

UTC = timezone.utc


def _wasde_xml(month: str = "July", ending_stocks: str = "1,790") -> bytes:
    metrics = {
        "Production": "16,000",
        "Supply, Total": "18,045",
        "Use, Total": "16,255",
        "Ending Stocks": ending_stocks,
        "Exports": "3,200",
    }
    groups = "".join(
        f"""
        <m2_attribute_group>
          <attribute2 attribute2="{label}">
            <m2_year_group_Collection>
              <m2_year_group market_year2="2026/27 Proj.">
                <m2_month_group_Collection>
                  <m2_month_group forecast_month2="{month}">
                    <Cell cell_value2="{value}" />
                  </m2_month_group>
                </m2_month_group_Collection>
              </m2_year_group>
            </m2_year_group_Collection>
          </attribute2>
        </m2_attribute_group>
        """
        for label, value in metrics.items()
    )
    return f"""
    <Report Name="wasde">
      <sr12>
        <Report Report_Month="{month} 2026"
          sub_report_title="U.S. Feed Grain and Corn Supply and Use  1/">
          <matrix2><m2_attribute_group_Collection>
            {groups}
          </m2_attribute_group_Collection></matrix2>
        </Report>
      </sr12>
    </Report>
    """.encode()


def _cot_row(report_date: str, noncommercial_short: str = "180000") -> dict[str, str]:
    return {
        "market_and_exchange_names": "CORN - CHICAGO BOARD OF TRADE",
        "report_date_as_yyyy_mm_dd": f"{report_date}T00:00:00.000",
        "open_interest_all": "1500000",
        "noncomm_positions_long_all": "200000",
        "noncomm_positions_short_all": noncommercial_short,
        "noncomm_postions_spread_all": "90000",
        "comm_positions_long_all": "700000",
        "comm_positions_short_all": "800000",
    }


@pytest.mark.unit
def test_wasde_index_excludes_future_and_delays_revision_vintages():
    html = """
    <table>
      <tr><td><time datetime="2026-07-10T12:00:00Z">Jul 10</time></td>
          <td><a href="/files/wasde0726.xml">XML</a></td></tr>
      <tr><td><time datetime="2026-05-12T12:00:00Z">May 12</time></td>
          <td><a href="/files/wasde0526v2.xml">XML</a></td></tr>
    </table>
    """
    releases = parse_release_index(html)

    july = next(item for item in releases if "0726" in item.url)
    revision = next(item for item in releases if "0526" in item.url)
    assert july.available_at == datetime(2026, 7, 10, 16, tzinfo=UTC)
    assert revision.available_at == datetime(2026, 5, 13, 16, tzinfo=UTC)
    assert revision.vintage == "release_v2"
    assert not any(
        item.available_at <= datetime(2026, 5, 12, 20, tzinfo=UTC)
        for item in releases
        if item.vintage == "release_v2"
    )


@pytest.mark.unit
def test_wasde_parser_extracts_corn_crop_year_without_month_lookahead():
    release = WasdeRelease(
        release_date=date(2026, 7, 10),
        available_at=datetime(2026, 7, 10, 16, tzinfo=UTC),
        url="https://example.test/wasde0726.xml",
        vintage="release_v1",
    )
    values, observations = parse_corn_balance(
        _wasde_xml(),
        crop_year="2026/27",
        release=release,
        retrieved_at=datetime(2026, 7, 30, tzinfo=UTC),
    )

    assert values["production"] == 16000
    assert values["ending_stocks"] == 1790
    assert len(observations) == 5
    assert all(item.available_at == release.available_at for item in observations)


@pytest.mark.unit
def test_cftc_parser_uses_only_reports_conservatively_available_as_of():
    rows = [
        _cot_row("2026-07-28", noncommercial_short="250000"),
        _cot_row("2026-07-21", noncommercial_short="180000"),
    ]
    values, observations, available_at = parse_corn_cot(
        rows,
        as_of=datetime(2026, 7, 30, 20, tzinfo=UTC),
        retrieved_at=datetime(2026, 7, 30, 21, tzinfo=UTC),
    )

    assert values["report_date"] == "2026-07-21"
    assert values["noncommercial_net"] == 20000
    assert available_at == datetime(2026, 7, 28, 19, 30, tzinfo=UTC)
    assert all(item.metadata["market_scope"] == "all corn futures contract months" for item in observations)


@pytest.mark.unit
def test_cftc_refuses_shutdown_backlog_instead_of_guessing_release_time():
    assert conservative_available_at(date(2025, 10, 7)) is None
    with pytest.raises(RuntimeError, match="shutdown-backlog"):
        parse_corn_cot(
            [_cot_row("2025-10-07")],
            as_of=datetime(2025, 12, 1, tzinfo=UTC),
            retrieved_at=datetime(2026, 7, 30, tzinfo=UTC),
        )


@pytest.mark.unit
def test_official_pipeline_adds_sections_facts_sources_and_clears_missing():
    base = build_evidence_package(
        commodity="corn",
        contract_symbol="ZCZ26",
        as_of="2026-07-30",
    )

    def wasde_loader(**_kwargs):
        observation_release = datetime(2026, 7, 10, 16, tzinfo=UTC)
        release = WasdeRelease(
            release_date=date(2026, 7, 10),
            available_at=observation_release,
            url="https://example.test/wasde.xml",
            vintage="release_v1",
        )
        values, observations = parse_corn_balance(
            _wasde_xml(),
            crop_year="2026/27",
            release=release,
            retrieved_at=datetime(2026, 7, 30, tzinfo=UTC),
        )
        return OfficialSnapshot(
            section_name="supply_demand",
            section={"status": "ready", "crop_year": "2026/27", "values": values},
            observations=observations,
            source={"source_id": "source_usda_wasde_corn", "provider": "USDA"},
            archive_filename="wasde.xml",
            raw_content=b"xml",
        )

    def cftc_loader(**_kwargs):
        values, observations, available_at = parse_corn_cot(
            [_cot_row("2026-07-21")],
            as_of=datetime(2026, 7, 30, tzinfo=UTC),
            retrieved_at=datetime(2026, 7, 30, tzinfo=UTC),
        )
        return OfficialSnapshot(
            section_name="positioning",
            section={
                "status": "ready",
                "report_date": values["report_date"],
                "available_at": available_at.isoformat(),
                "values": values,
            },
            observations=observations,
            source={"source_id": "source_cftc_cot_corn", "provider": "CFTC"},
            archive_filename="cot.json",
            raw_content=b"json",
        )

    run = build_official_evidence(
        base,
        wasde_loader=wasde_loader,
        cftc_loader=cftc_loader,
        eia_loader=None,
        weather_loader=None,
    )
    payload = run.evidence.to_dict()

    assert payload["supply_demand"]["values"]["ending_stocks"] == 1790
    assert payload["positioning"]["values"]["noncommercial_net"] == 20000
    assert "supply_demand" not in payload["quality"]["missing_core_data"]
    assert "positioning" not in payload["quality"]["missing_core_data"]
    assert len(payload["facts"]) == 13
    assert len(run.archives) == 2


class _EiaResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _EiaSession:
    def get(self, url: str, **_kwargs):
        is_production = "YOP" in url
        values = (1133, 1094, 1040) if is_production else (24726, 24481, 24391)
        rows = [
            {
                "period": period,
                "value": value,
                "series-description": "fixture",
            }
            for period, value in zip(
                ("2026-07-24", "2026-07-17", "2026-07-10"),
                values,
                strict=True,
            )
        ]
        return _EiaResponse({"response": {"data": rows}})


@pytest.mark.unit
def test_eia_current_run_uses_only_conservatively_available_weeks():
    snapshot = load_corn_ethanol(
        as_of=datetime(2026, 7, 30, 20, tzinfo=UTC),
        today=date(2026, 7, 30),
        session=_EiaSession(),
        api_key="not-written-to-output",
        retrieved_at=datetime(2026, 7, 30, 21, tzinfo=UTC),
    )

    assert snapshot.section["values"]["production"] == 1094
    assert snapshot.section["values"]["production_period"] == "2026-07-17"
    assert snapshot.section["values"]["stocks"] == 24481
    assert b"not-written-to-output" not in snapshot.raw_content
    assert snapshot.source["api_key_mode"] == "configured"


@pytest.mark.unit
def test_eia_refuses_current_api_for_historical_replay():
    with pytest.raises(RuntimeError, match="not vintage-safe"):
        load_corn_ethanol(
            as_of=datetime(2026, 7, 29, 20, tzinfo=UTC),
            today=date(2026, 7, 30),
            session=_EiaSession(),
            api_key="fixture",
        )


class _WeatherResponse:
    def __init__(self, *, content: bytes = b"", payload: dict | None = None):
        self.content = content
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        assert self._payload is not None
        return self._payload


class _WeatherSession:
    def get(self, url: str, **_kwargs):
        if "CornDMstats.csv" in url:
            rows = [
                "USDMWEEK,CATACRES,DM,TOTACRES,CATPRCNT,CUMLACRES,CUMLPRCNT",
                "USDM_20260728,1,0,10,52.0,1,1",
                "USDM_20260728,1,1,10,39.0,1,1",
                "USDM_20260728,1,2,10,20.0,1,1",
                "USDM_20260728,1,3,10,6.0,1,1",
                "USDM_20260728,1,4,10,0.0,1,1",
            ]
            return _WeatherResponse(content=("\n".join(rows) + "\n").encode())
        if "/points/" in url:
            return _WeatherResponse(
                payload={
                    "properties": {
                        "forecastGridData": "https://api.weather.gov/gridpoints/FIXTURE"
                    }
                }
            )
        return _WeatherResponse(
            payload={
                "properties": {
                    "updateTime": "2026-07-30T12:00:00+00:00",
                    "validTimes": "2026-07-30T12:00:00+00:00/P7D",
                    "temperature": {
                        "values": [
                            {
                                "validTime": "2026-07-30T12:00:00+00:00/P7D",
                                "value": 25,
                            }
                        ]
                    },
                    "quantitativePrecipitation": {
                        "values": [
                            {
                                "validTime": "2026-07-30T12:00:00+00:00/P7D",
                                "value": 30,
                            }
                        ]
                    },
                }
            }
        )


@pytest.mark.unit
def test_weather_snapshot_uses_production_weighted_drought_and_acreage_sample():
    snapshot = load_corn_weather(
        as_of=datetime(2026, 7, 30, 16, tzinfo=UTC),
        today=date(2026, 7, 30),
        session=_WeatherSession(),
        retrieved_at=datetime(2026, 7, 30, 16, tzinfo=UTC),
    )

    values = snapshot.section["values"]
    assert snapshot.section["status"] == "partial"
    assert values["d1_or_worse_percent"] == 39
    assert values["sample_weighted_7_day_precipitation_mm"] == 30
    assert values["sample_weighted_7_day_mean_temperature_c"] == 25
    assert 80 < values["sample_coverage_percent_of_intended_acres"] < 90
    assert len(snapshot.observations) == 9


@pytest.mark.unit
def test_weather_current_endpoints_are_refused_for_historical_replay():
    with pytest.raises(RuntimeError, match="not vintage-safe"):
        load_corn_weather(
            as_of=datetime(2026, 7, 29, 16, tzinfo=UTC),
            today=date(2026, 7, 30),
            session=_WeatherSession(),
        )
