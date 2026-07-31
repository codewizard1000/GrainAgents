from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, timezone

import pytest
import requests

from tradingagents.commodities.evidence import build_evidence_package
from tradingagents.commodities.official.cftc import (
    conservative_available_at,
    parse_corn_cot,
)
from tradingagents.commodities.official.cpc import load_corn_8_14_day_outlook
from tradingagents.commodities.official.crop_progress import (
    load_corn_crop_progress,
)
from tradingagents.commodities.official.eia import load_corn_ethanol
from tradingagents.commodities.official.events import load_grain_regulatory_events
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
from tradingagents.commodities.reporting import (
    render_demand_report,
    render_weather_report,
)

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
        outlook_loader=None,
        crop_progress_loader=None,
        macro_loader=None,
        event_loader=None,
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
        weather_loader=snapshot_loader(
            "weather",
            "source_noaa_usda_corn_weather",
            {
                "status": "partial",
                "values": {
                    "d1_or_worse_percent": 20,
                    "seven_day_forecast_valid_start": "2026-07-30",
                    "seven_day_forecast_valid_end": "2026-08-05",
                },
                "missing": [
                    "14_day_forecast",
                    "crop_condition_ratings",
                    "condition_based_weather_risk_score",
                    "critical_forecast_dates",
                    "yield_impact_range",
                ],
            },
        ),
        outlook_loader=snapshot_loader(
            "weather_outlook",
            "source_noaa_cpc_corn_8_14_day",
            {
                "status": "ready",
                "issue_date": "2026-07-30",
                "valid_start": "2026-08-07",
                "valid_end": "2026-08-13",
            },
        ),
        crop_progress_loader=snapshot_loader(
            "crop_progress",
            "source_usda_nass_corn_crop_progress",
            {
                "status": "ready",
                "values": {
                    "week_ending": "2026-07-26",
                    "silking_percent": 78,
                    "dough_percent": 25,
                },
            },
        ),
        macro_loader=snapshot_loader(
            "macro",
            "source_fred_grain_macro",
            {
                "status": "ready",
                "coverage_status": "partial",
                "values": {"broad_us_dollar_index": 120.5},
                "missing": ["official_grain_news_events", "china_policy"],
            },
        ),
        event_loader=snapshot_loader(
            "macro_events",
            "source_federal_register_grain_events",
            {
                "status": "ready",
                "coverage_status": "partial",
                "documents": [{"document_number": "2026-14772"}],
                "missing": ["black_sea_shipping"],
            },
        ),
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
    assert payload["macro"]["events"]["documents"][0][
        "document_number"
    ] == "2026-14772"
    assert "official_grain_news_events" not in payload["macro"]["missing"]
    assert "black_sea_shipping" in payload["macro"]["missing"]
    assert payload["weather"]["outlook_8_14_day"]["issue_date"] == (
        "2026-07-30"
    )
    assert "14_day_forecast" not in payload["weather"]["missing"]
    assert "crop_condition_ratings" not in payload["weather"]["missing"]
    assert payload["weather"]["crop_progress"]["values"]["silking_percent"] == 78
    assert payload["weather"]["critical_forecast_dates"] == {
        "start": "2026-07-30",
        "end": "2026-08-13",
        "basis": "78% silking and 25% dough as of 2026-07-26",
    }
    assert "yield_impact_range" in payload["weather"]["missing"]


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


class _EventResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _EventSession:
    def get(self, _url: str, **kwargs):
        query = kwargs["params"]["conditions[term]"]
        if query != "national grain car council":
            return _EventResponse({"results": [], "count": 0})
        documents = []
        for publication_date, number in (
            ("2026-07-31", "2026-15555"),
            ("2026-07-30", "2026-15444"),
        ):
            documents.append(
                {
                    "document_number": number,
                    "title": "Notice of National Grain Car Council Meeting",
                    "publication_date": publication_date,
                    "type": "Notice",
                    "agencies": [{"name": "Surface Transportation Board"}],
                    "html_url": f"https://example.test/documents/{number}",
                    "pdf_url": f"https://example.test/official/{number}.pdf",
                    "abstract": None,
                }
            )
        return _EventResponse({"results": documents, "count": 2})


@pytest.mark.unit
def test_regulatory_events_filter_titles_and_delay_current_issue():
    snapshot = load_grain_regulatory_events(
        as_of=datetime(2026, 7, 31, 23, tzinfo=UTC),
        today=date(2026, 7, 31),
        session=_EventSession(),
        retrieved_at=datetime(2026, 7, 31, 23, 30, tzinfo=UTC),
    )

    documents = snapshot.section["documents"]
    assert snapshot.section["status"] == "ready"
    assert snapshot.section["coverage_status"] == "partial"
    assert len(documents) == 1
    assert documents[0]["document_number"] == "2026-15444"
    assert documents[0]["categories"] == ["grain_transportation"]
    assert len(snapshot.observations) == 1
    assert snapshot.observations[0].source_url.endswith("2026-15444.pdf")
    assert snapshot.source["api_key_mode"] == "not_required"


@pytest.mark.unit
def test_regulatory_events_refuse_current_search_for_historical_replay():
    with pytest.raises(RuntimeError, match="not replay-safe"):
        load_grain_regulatory_events(
            as_of=datetime(2026, 7, 30, 23, tzinfo=UTC),
            today=date(2026, 7, 31),
            session=_EventSession(),
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


def _crop_progress_report() -> bytes:
    return b"""Crop Progress

Released July 27, 2026, by the National Agricultural Statistics Service (NASS).

Corn Silking - Selected States
[These 18 States planted 91% of the 2025 corn acreage]
18 States .......:    73          59          78          74

Corn Dough - Selected States
[These 18 States planted 91% of the 2025 corn acreage]
18 States .......:    24          13          25          22

Corn Condition - Selected States: Week Ending July 26, 2026
[These 18 States planted 91% of the 2025 corn acreage]
18 States ......:     3           9          25          50          13
Previous week ..:     2           7          24          51          16
Previous year ..:     2           5          20          53          20

Soybeans Blooming - Selected States
"""


class _CropProgressSession:
    def get(self, url: str, **_kwargs):
        if "publications/8336h188j" in url:
            return _WeatherResponse(
                content=b"""
                <a href="/sites/default/release-files/1/prog2926.txt">Text</a>
                <a href="/publication/crop-progress/2026-07-20">Old</a>
                <a href="/sites/default/release-files/2/prog3026.txt">Text</a>
                <a href="/publication/crop-progress/2026-07-27">Current</a>
                """
            )
        assert url.endswith("prog3026.txt")
        return _WeatherResponse(content=_crop_progress_report())


@pytest.mark.unit
def test_crop_progress_builds_condition_risk_and_stage_facts():
    snapshot = load_corn_crop_progress(
        as_of=datetime(2026, 7, 31, 23, tzinfo=UTC),
        session=_CropProgressSession(),
        retrieved_at=datetime(2026, 7, 31, 23, 30, tzinfo=UTC),
    )

    values = snapshot.section["values"]
    assert values["week_ending"] == "2026-07-26"
    assert values["good_excellent_percent"] == 63
    assert values["poor_very_poor_percent"] == 12
    assert values["condition_based_weather_risk_score"] == 34.75
    assert values["condition_based_weather_risk_change_week_over_week"] == 2.75
    assert values["silking_percent"] == 78
    assert values["silking_vs_five_year_average_percentage_points"] == 4
    assert values["dough_percent"] == 25
    assert values["condition_risk_data_coverage_confidence_percent"] == 91
    assert len(snapshot.observations) == 12
    assert b"prog3026.txt" in snapshot.raw_content


@pytest.mark.unit
def test_crop_progress_excludes_release_not_yet_conservatively_available():
    class PriorSession(_CropProgressSession):
        def get(self, url: str, **kwargs):
            if "publications/8336h188j" in url:
                return super().get(url, **kwargs)
            assert url.endswith("prog2926.txt")
            prior = _crop_progress_report().replace(
                b"Released July 27, 2026",
                b"Released July 20, 2026",
            )
            return _WeatherResponse(content=prior)

    snapshot = load_corn_crop_progress(
        as_of=datetime(2026, 7, 27, 20, 30, tzinfo=UTC),
        session=PriorSession(),
    )

    assert snapshot.source["vintage"] == "2026-07-20"


def _cpc_kmz(variable: str, category: str, probability: float) -> bytes:
    label = "Temperature" if variable == "temp" else "Precipitation"
    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>8-14 Day {label} Outlook - Created: 07/30/2026 - Valid: 08/07/2026 - 08/13/2026</name>
    <Placemark>
      <name>{probability:.1f}% Chance of {category} Normal {label}</name>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        -110,30 -80,30 -80,50 -110,50 -110,30
      </coordinates></LinearRing></outerBoundaryIs></Polygon>
    </Placemark>
  </Document>
</kml>""".encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(f"814{variable}_latest.kml", kml)
    return output.getvalue()


class _CpcSession:
    def get(self, url: str, **_kwargs):
        assert "20260730" in url
        if "814temp" in url:
            return _WeatherResponse(content=_cpc_kmz("temp", "Above", 50))
        return _WeatherResponse(content=_cpc_kmz("prcp", "Below", 40))


@pytest.mark.unit
def test_cpc_outlook_uses_dated_archive_and_acreage_weighted_categories():
    snapshot = load_corn_8_14_day_outlook(
        as_of=datetime(2026, 7, 31, 23, tzinfo=UTC),
        today=date(2026, 7, 31),
        session=_CpcSession(),
        retrieved_at=datetime(2026, 7, 31, 23, 30, tzinfo=UTC),
    )

    section = snapshot.section
    assert section["issue_date"] == "2026-07-30"
    assert section["valid_start"] == "2026-08-07"
    assert section["valid_end"] == "2026-08-13"
    assert section["temperature"]["dominant_category"] == "above_normal"
    assert section["temperature"]["acre_share_percent"]["above_normal"] == 100
    assert section["precipitation"]["dominant_category"] == "below_normal"
    assert section["precipitation"]["acre_share_percent"]["below_normal"] == 100
    assert len(snapshot.observations) == 8
    assert b"temperature_kmz_base64" in snapshot.raw_content
    assert snapshot.source["api_key_mode"] == "not_required"
    report = render_weather_report(
        {
            "as_of": "2026-07-31T23:00:00+00:00",
            "instrument": {"symbol": "ZCZ26"},
            "weather": {
                "status": "partial",
                "values": {
                    "drought_valid_date": "2026-07-28",
                    "d1_or_worse_percent": 39,
                    "d2_or_worse_percent": 20,
                    "d3_or_worse_percent": 6,
                    "sample_weighted_7_day_precipitation_mm": 30,
                    "sample_weighted_7_day_normal_precipitation_mm": 18,
                    "sample_weighted_7_day_precipitation_anomaly_mm": 12,
                    "sample_weighted_7_day_mean_temperature_c": 25,
                    "sample_weighted_7_day_normal_mean_temperature_c": 20,
                    "sample_weighted_7_day_temperature_anomaly_c": 5,
                    "sample_weighted_7_day_maximum_temperature_c": 31,
                    "seven_day_forecast_valid_start": "2026-07-31",
                    "seven_day_forecast_valid_end": "2026-08-06",
                    "sample_coverage_percent_of_intended_acres": 85,
                },
                "missing": ["yield_impact_range"],
                "methodology": "fixture",
                "outlook_8_14_day": dict(section),
                "crop_progress": {
                    "methodology": "fixture crop methodology.",
                    "values": {
                        "week_ending": "2026-07-26",
                        "good_excellent_percent": 63,
                        "poor_very_poor_percent": 12,
                        "condition_based_weather_risk_score": 34.75,
                        "condition_based_weather_risk_change_week_over_week": 2.75,
                        "condition_acreage_coverage_percent": 91,
                        "silking_percent": 78,
                        "dough_percent": 25,
                    },
                },
                "critical_forecast_dates": {
                    "start": "2026-07-31",
                    "end": "2026-08-13",
                    "basis": "78% silking and 25% dough as of 2026-07-26",
                },
            },
        }
    )
    assert "## CPC 8-14 day outlook" in report
    assert "above normal" in report
    assert "below normal" in report
    assert "## USDA crop condition and development" in report
    assert "34.75" in report
    assert "2026-07-31 through 2026-08-13" in report


@pytest.mark.unit
def test_cpc_outlook_refuses_current_retrieval_for_historical_replay():
    with pytest.raises(RuntimeError, match="not replay-safe"):
        load_corn_8_14_day_outlook(
            as_of=datetime(2026, 7, 30, 23, tzinfo=UTC),
            today=date(2026, 7, 31),
            session=_CpcSession(),
        )


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
        if "normals-daily/1991-2020" in url:
            station = url.rsplit("/", 1)[-1].removesuffix(".csv")
            rows = [
                "STATION,DATE,NAME,hour,DLY-TAVG-NORMAL,MTD-PRCP-NORMAL",
                f"{station},07-29,Fixture Station,99,68.0,2.9",
                f"{station},07-30,Fixture Station,99,68.0,3.0",
                f"{station},07-31,Fixture Station,99,68.0,3.1",
                f"{station},08-01,Fixture Station,99,68.0,0.1",
                f"{station},08-02,Fixture Station,99,68.0,0.2",
                f"{station},08-03,Fixture Station,99,68.0,0.3",
                f"{station},08-04,Fixture Station,99,68.0,0.4",
                f"{station},08-05,Fixture Station,99,68.0,0.5",
            ]
            return _WeatherResponse(content=("\n".join(rows) + "\n").encode())
        return _WeatherResponse(
            payload={
                "properties": {
                    "updateTime": "2026-07-30T12:00:00+00:00",
                    "validTimes": "2026-07-30T12:00:00+00:00/P7DT20H",
                    "temperature": {
                        "values": [
                            {
                                "validTime": "2026-07-30T12:00:00+00:00/P7D",
                                "value": 25,
                            },
                            {
                                "validTime": "2026-08-06T12:00:00+00:00/PT20H",
                                "value": 35,
                            }
                        ]
                    },
                    "quantitativePrecipitation": {
                        "values": [
                            {
                                "validTime": "2026-07-30T12:00:00+00:00/P7D",
                                "value": 30,
                            },
                            {
                                "validTime": "2026-08-06T12:00:00+00:00/PT20H",
                                "value": 5,
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
    assert values["sample_weighted_7_day_maximum_temperature_c"] == 25
    assert values["sample_weighted_7_day_normal_precipitation_mm"] == 17.78
    assert values["sample_weighted_7_day_normal_mean_temperature_c"] == 20
    assert values["sample_weighted_7_day_precipitation_anomaly_mm"] == 12.22
    assert values["sample_weighted_7_day_temperature_anomaly_c"] == 5
    assert "production_weighted_rainfall_anomaly" not in snapshot.section["missing"]
    assert "production_weighted_temperature_anomaly" not in snapshot.section["missing"]
    assert 80 < values["sample_coverage_percent_of_intended_acres"] < 90
    assert len(snapshot.observations) == 13
    assert b"ncei_daily_normals_csv" in snapshot.raw_content


@pytest.mark.unit
def test_weather_current_endpoints_are_refused_for_historical_replay():
    with pytest.raises(RuntimeError, match="not vintage-safe"):
        load_corn_weather(
            as_of=datetime(2026, 7, 29, 16, tzinfo=UTC),
            today=date(2026, 7, 30),
            session=_WeatherSession(),
        )


@pytest.mark.unit
def test_weather_report_handles_missing_base_snapshot():
    report = render_weather_report(
        {
            "weather": {
                "status": "partial",
                "values": {},
                "outlook_8_14_day": {"status": "ready"},
            }
        }
    )

    assert "seven-day weather and drought snapshot was unavailable" in report
