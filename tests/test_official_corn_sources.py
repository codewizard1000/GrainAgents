from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
import requests

from tradingagents.commodities.evidence import build_evidence_package
from tradingagents.commodities.official.cftc import (
    conservative_available_at,
    parse_corn_cot,
)
from tradingagents.commodities.official.eia import load_corn_ethanol
from tradingagents.commodities.official.fas import load_corn_export_sales
from tradingagents.commodities.official.inspections import (
    load_corn_export_inspections,
)
from tradingagents.commodities.official.macro import load_grain_macro
from tradingagents.commodities.official.models import OfficialSnapshot
from tradingagents.commodities.official.pipeline import build_official_evidence
from tradingagents.commodities.official.wasde import (
    WasdeRelease,
    parse_corn_balance,
    parse_release_index,
)
from tradingagents.commodities.official.weather import load_corn_weather
from tradingagents.commodities.reporting import render_demand_report

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
        fas_loader=None,
        inspections_loader=None,
        weather_loader=None,
        macro_loader=None,
    )
    payload = run.evidence.to_dict()

    assert payload["supply_demand"]["values"]["ending_stocks"] == 1790
    assert payload["positioning"]["values"]["noncommercial_net"] == 20000
    assert "supply_demand" not in payload["quality"]["missing_core_data"]
    assert "positioning" not in payload["quality"]["missing_core_data"]
    assert len(payload["facts"]) == 13
    assert len(run.archives) == 2


@pytest.mark.unit
def test_official_pipeline_merges_domestic_and_export_demand_components():
    base = build_evidence_package(
        commodity="corn",
        contract_symbol="ZCZ26",
        as_of="2026-07-30",
    )

    def snapshot_loader(section_name, source_id, section):
        def load(**_kwargs):
            return OfficialSnapshot(
                section_name=section_name,
                section=section,
                observations=(),
                source={"source_id": source_id, "provider": "fixture"},
                archive_filename=f"{source_id}.json",
                raw_content=b"{}",
            )

        return load

    run = build_official_evidence(
        base,
        wasde_loader=snapshot_loader(
            "supply_demand",
            "source_usda_wasde_corn",
            {"status": "ready"},
        ),
        cftc_loader=snapshot_loader(
            "positioning",
            "source_cftc_cot_corn",
            {"status": "ready"},
        ),
        eia_loader=snapshot_loader(
            "demand",
            "source_eia_weekly_ethanol",
            {"status": "ready", "values": {"production": 1000}},
        ),
        fas_loader=snapshot_loader(
            "demand",
            "source_usda_fas_esr_corn",
            {"status": "ready", "values": {"weekly_exports": 300}},
        ),
        inspections_loader=snapshot_loader(
            "demand",
            "source_usda_ams_fgis_corn_inspections",
            {"status": "ready", "values": {"weekly_inspections": 275}},
        ),
        weather_loader=None,
        macro_loader=None,
    )
    payload = run.evidence.to_dict()

    assert payload["demand"]["ethanol"]["values"]["production"] == 1000
    assert payload["demand"]["export_sales"]["values"]["weekly_exports"] == 300
    assert (
        payload["demand"]["export_inspections"]["values"][
            "weekly_inspections"
        ]
        == 275
    )
    assert payload["demand"]["missing"] == []
    assert payload["demand"]["status"] == "ready"
    assert "demand" not in payload["quality"]["missing_core_data"]


@pytest.mark.unit
def test_demand_report_renders_all_three_components_without_row_collisions():
    report = render_demand_report(
        {
            "demand": {
                "status": "ready",
                "ethanol": {
                    "values": {
                        "production": 1094,
                        "production_period": "2026-07-17",
                        "stocks": 24481,
                        "stocks_period": "2026-07-17",
                    }
                },
                "export_sales": {
                    "crop_year": "2026/27",
                    "target_role": "next_marketing_year",
                    "week_ending": "2026-07-23",
                    "unit": "metric_tons",
                    "values": {
                        "weekly_exports": 1528496,
                        "accumulated_exports": 76373332,
                        "outstanding_sales": 10602239,
                        "current_my_net_sales": 362916,
                        "current_my_total_commitment": 86975571,
                        "next_my_outstanding_sales": 8623552,
                        "next_my_net_sales": 1062421,
                        "target_marketing_year_commitment": 8623552,
                    },
                },
                "export_inspections": {
                    "week_ending": "2026-07-23",
                    "market_year_start": "2025-09-01",
                    "values": {
                        "weekly_inspections": 1488028,
                        "previous_week_inspections": 1612823,
                        "four_week_average_inspections": 1597617,
                        "week_over_week_change_percent": -7.736,
                        "market_year_to_date_inspections": 75323661,
                    },
                },
                "missing": [],
            }
        }
    )

    assert "## Domestic ethanol proxy" in report
    assert "## USDA weekly export sales" in report
    assert "1,528,496" in report
    assert "## USDA weekly export inspections" in report
    assert "1,488,028" in report


class _EiaResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _EiaSession:
    def get(self, url: str, **kwargs):
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
        return _EiaResponse(
            {
                "response": {"data": rows},
                "request": {
                    "url": f"{url}?api_key={kwargs['params']['api_key']}",
                    "api_key": kwargs["params"]["api_key"],
                },
            }
        )


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
    assert snapshot.raw_content.count(b"[REDACTED]") == 4
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


class _MacroResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _MacroSession:
    values = {
        "DTWEXBGS": (120.5401, 120.7105),
        "DCOILWTICO": (86.04, 91.74),
        "DGS10": (4.71, 4.69),
        "DFF": (3.63, 3.63),
    }

    def get(self, _url: str, **kwargs):
        series_id = kwargs["params"]["id"]
        older, newer = self.values[series_id]
        content = (
            f"observation_date,{series_id}\n"
            f"2026-07-24,{older}\n"
            f"2026-07-25,{newer}\n"
        ).encode()
        return _MacroResponse(content)


@pytest.mark.unit
def test_fred_macro_uses_conservative_rows_and_archives_public_csv():
    snapshot = load_grain_macro(
        as_of=datetime(2026, 7, 31, 23, tzinfo=UTC),
        today=date(2026, 7, 31),
        session=_MacroSession(),
        retrieved_at=datetime(2026, 7, 31, 23, 30, tzinfo=UTC),
    )

    values = snapshot.section["values"]
    assert snapshot.section["status"] == "ready"
    assert snapshot.section["coverage_status"] == "partial"
    assert values["broad_us_dollar_index"] == 120.5401
    assert values["broad_us_dollar_index_period"] == "2026-07-24"
    assert values["wti_crude_oil"] == 86.04
    assert len(snapshot.observations) == 4
    assert snapshot.source["api_key_mode"] == "not_required_public_csv"
    assert b"DTWEXBGS" in snapshot.raw_content


@pytest.mark.unit
def test_fred_macro_refuses_current_csv_for_historical_replay():
    with pytest.raises(RuntimeError, match="not vintage-safe"):
        load_grain_macro(
            as_of=datetime(2026, 7, 30, 23, tzinfo=UTC),
            today=date(2026, 7, 31),
            session=_MacroSession(),
        )


class _FasResponse:
    def __init__(self, payload: list[dict]):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict]:
        return self._payload


class _FasSession:
    def get(self, url: str, **kwargs):
        assert kwargs["headers"]["API_KEY"] == "not-written-to-output"
        if url.endswith("/datareleasedates"):
            return _FasResponse(
                [
                    {
                        "commodityCode": 401,
                        "marketYear": 2026,
                        "releaseTimeStamp": "2026-07-30T00:00:00",
                    }
                ]
            )
        rows = []
        for country, multiplier in ((2010, 1), (3010, 2)):
            rows.append(
                {
                    "commodityCode": 401,
                    "countryCode": country,
                    "weeklyExports": 100 * multiplier,
                    "accumulatedExports": 1000 * multiplier,
                    "outstandingSales": 4000 * multiplier,
                    "grossNewSales": 120 * multiplier,
                    "currentMYNetSales": 110 * multiplier,
                    "currentMYTotalCommitment": 5000 * multiplier,
                    "nextMYOutstandingSales": 800 * multiplier,
                    "nextMYNetSales": 50 * multiplier,
                    "unitId": 1,
                    "weekEndingDate": "2026-07-23T00:00:00",
                }
            )
        return _FasResponse(rows)


class _FailedFasResponse(_FasResponse):
    def raise_for_status(self) -> None:
        raise requests.HTTPError("fixture legacy gateway failure")


class _FallbackFasSession:
    token = "fixture-public-token-not-archived"

    def post(self, url: str, **_kwargs):
        assert url.endswith("/token")
        return _FasResponse(
            {
                "access_token": self.token,
                "expires_in": 3600,
                "token_type": "bearer",
            }
        )

    def get(self, url: str, **kwargs):
        if "/OpenData/" in url:
            return _FailedFasResponse([])
        assert kwargs["headers"]["Authorization"] == f"Bearer {self.token}"
        if url.endswith("/GetPublishedDateAndWeekEndingDate"):
            return _FasResponse(
                {
                    "weekendingdate": "2026-07-23T00:00:00",
                    "publisedDate": "2026-07-30T08:30:09.927",
                }
            )
        if url.endswith("/lookups/Commodities"):
            return _FasResponse(
                [
                    {
                        "id": 10,
                        "commodityCode": 401,
                        "commodityName": "CORN - UNMILLED",
                    }
                ]
            )
        return _FasResponse(
            [
                {
                    "commodityId": 10,
                    "commodityName": "CORN - UNMILLED",
                    "weekNumber": 47,
                    "myDefinition": "Sep 2025/Aug 2026",
                    "weekEndingDate": "2026-07-23T00:00:00",
                    "weeklyExport": 1528496,
                    "netSales": 362916,
                    "outstandingSales": 10602239,
                    "accumulatedExport": 76373332,
                    "nextYearOutstandingSales": 8623552,
                    "nextYearNetSales": 1062421,
                    "mycoTypeName": "Standard",
                }
            ]
        )


@pytest.mark.unit
def test_fas_export_sales_aggregates_latest_week_and_targets_next_crop():
    snapshot = load_corn_export_sales(
        as_of=datetime(2026, 7, 30, 20, tzinfo=UTC),
        crop_year="2026/27",
        today=date(2026, 7, 30),
        session=_FasSession(),
        api_key="not-written-to-output",
        retrieved_at=datetime(2026, 7, 30, 21, tzinfo=UTC),
    )

    values = snapshot.section["values"]
    assert snapshot.section["target_role"] == "next_marketing_year"
    assert snapshot.section["market_year"] == 2026
    assert values["weekly_exports"] == 300
    assert values["current_my_total_commitment"] == 15000
    assert values["target_marketing_year_commitment"] == 2400
    assert snapshot.section["unit"] == "metric_tons"
    assert len(snapshot.observations) == 9
    assert b"not-written-to-output" not in snapshot.raw_content


@pytest.mark.unit
def test_fas_falls_back_to_public_esrqs_when_legacy_gateway_fails():
    session = _FallbackFasSession()
    snapshot = load_corn_export_sales(
        as_of=datetime(2026, 7, 30, 20, tzinfo=UTC),
        crop_year="2026/27",
        today=date(2026, 7, 30),
        session=session,
        api_key="not-written-to-output",
        retrieved_at=datetime(2026, 7, 30, 21, tzinfo=UTC),
    )

    values = snapshot.section["values"]
    assert snapshot.section["api_mode"] == "esrqs_public"
    assert snapshot.source["api_key_mode"] == "not_required_esrqs_public"
    assert values["weekly_exports"] == 1528496
    assert values["current_my_total_commitment"] == 86975571
    assert values["target_marketing_year_commitment"] == 8623552
    assert snapshot.section["released_at"] == (
        "2026-07-30T12:30:09.927000+00:00"
    )
    assert len(snapshot.observations) == 8
    assert session.token.encode() not in snapshot.raw_content
    assert b"not-written-to-output" not in snapshot.raw_content


@pytest.mark.unit
def test_fas_refuses_current_api_for_historical_replay():
    with pytest.raises(RuntimeError, match="not vintage-safe"):
        load_corn_export_sales(
            as_of=datetime(2026, 7, 29, 20, tzinfo=UTC),
            crop_year="2026/27",
            today=date(2026, 7, 30),
            session=_FasSession(),
            api_key="fixture",
        )


class _InspectionsResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _InspectionsSession:
    def get(self, url: str, **kwargs):
        if "/api/views/" in url:
            return _InspectionsResponse({"rowsUpdatedAt": 1785278317})
        select = kwargs["params"]["$select"]
        if select.startswith("date,grain"):
            return _InspectionsResponse(
                [
                    {
                        "date": period,
                        "metric_tons": value,
                        "records": "100",
                    }
                    for period, value in (
                        ("2026-07-23T00:00:00.000", "1488028"),
                        ("2026-07-16T00:00:00.000", "1612823"),
                        ("2026-07-09T00:00:00.000", "1554620"),
                        ("2026-07-02T00:00:00.000", "1734997"),
                    )
                ]
            )
        return _InspectionsResponse(
            [{"metric_tons": "75323661", "records": "7218"}]
        )


@pytest.mark.unit
def test_export_inspections_use_exact_dataset_update_and_cert_date_total():
    snapshot = load_corn_export_inspections(
        as_of=datetime(2026, 7, 30, 20, tzinfo=UTC),
        today=date(2026, 7, 30),
        session=_InspectionsSession(),
        retrieved_at=datetime(2026, 7, 30, 21, tzinfo=UTC),
    )

    values = snapshot.section["values"]
    assert values["week_ending"] == "2026-07-23"
    assert values["weekly_inspections"] == 1488028
    assert values["four_week_average_inspections"] == 1597617
    assert values["market_year_to_date_inspections"] == 75323661
    assert snapshot.section["market_year_start"] == "2025-09-01"
    assert snapshot.source["rows_updated_at"] == "2026-07-28T22:38:37+00:00"
    assert len(snapshot.observations) == 5


@pytest.mark.unit
def test_export_inspections_refuse_current_api_for_historical_replay():
    with pytest.raises(RuntimeError, match="not vintage-safe"):
        load_corn_export_inspections(
            as_of=datetime(2026, 7, 29, 20, tzinfo=UTC),
            today=date(2026, 7, 30),
            session=_InspectionsSession(),
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
