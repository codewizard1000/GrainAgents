"""Current-run corn drought and seven-day NWS forecast evidence."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from .models import OfficialDataError, OfficialObservation, OfficialSnapshot

DROUGHT_URL = (
    "https://storage.googleapis.com/noaa-nidis-drought-gov-data/"
    "current-conditions/json/v1/sector/agriculture/NASS2017-CornDMstats.csv"
)
NWS_POINTS_URL = "https://api.weather.gov/points/{latitude},{longitude}"
DOCUMENTATION_URL = "https://www.weather.gov/documentation/services-web-api"
NORMALS_DOCUMENTATION_URL = (
    "https://www.ncei.noaa.gov/pub/data/cdo/documentation/"
    "normals-daily-1991-2020_documentation.pdf"
)
NORMALS_DATASET_URL = (
    "https://www.ncei.noaa.gov/access/search/datasets/"
    "normals-daily-1991-2020/"
)
NORMALS_URL = (
    "https://www.ncei.noaa.gov/data/normals-daily/1991-2020/"
    "access/{station}.csv"
)
WEIGHTS_URL = "https://release.nass.usda.gov/reports/pspl0326.txt"
SOURCE_ID = "source_noaa_usda_corn_weather"

# Representative points for the twelve largest 2026 intended corn-acre states.
# Acreage is in thousands and comes from USDA NASS Prospective Plantings,
# released March 31, 2026. Points are transparent sampling locations, not
# assertions that one point represents every acre in a state.
STATE_SAMPLE = {
    "Iowa": (13_100, 42.0, -93.5),
    "Illinois": (10_900, 40.0, -89.2),
    "Nebraska": (10_300, 41.2, -98.0),
    "Minnesota": (8_600, 45.0, -94.3),
    "Kansas": (7_100, 38.5, -98.0),
    "South Dakota": (6_300, 44.5, -99.0),
    "Indiana": (5_400, 40.0, -86.2),
    "North Dakota": (4_400, 47.4, -100.3),
    "Wisconsin": (3_700, 44.5, -89.5),
    "Missouri": (3_650, 38.5, -92.5),
    "Ohio": (3_400, 40.2, -82.8),
    "Michigan": (2_250, 43.7, -84.5),
}
US_2026_INTENDED_CORN_ACRES_THOUSAND = 95_338

# One NCEI 1991-2020 daily-normal station near each disclosed state sample
# point. These fixed IDs make the station approximation stable and auditable.
NORMAL_STATIONS = {
    "Iowa": "USW00094989",  # Ames Municipal Airport
    "Illinois": "USW00093822",  # Springfield Capital Airport
    "Nebraska": "USW00014939",  # Lincoln Municipal Airport
    "Minnesota": "USW00014922",  # Minneapolis/St Paul Airport
    "Kansas": "USW00003919",  # Salina Municipal Airport
    "South Dakota": "USW00014936",  # Huron Regional Airport
    "Indiana": "USW00093819",  # Indianapolis
    "North Dakota": "USW00024011",  # Bismarck
    "Wisconsin": "USW00014837",  # Madison Dane Regional Airport
    "Missouri": "USW00003945",  # Columbia Regional Airport
    "Ohio": "USW00014821",  # Columbus Port Columbus International Airport
    "Michigan": "USW00014836",  # Lansing Capital City Airport
}


def _duration_hours(valid_time: str) -> float:
    try:
        duration = valid_time.split("/", 1)[1]
    except IndexError as exc:
        raise OfficialDataError(f"NWS validTime has no duration: {valid_time}") from exc
    match = re.fullmatch(
        r"P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?)?",
        duration,
    )
    if match is None:
        raise OfficialDataError(f"unsupported NWS duration: {duration}")
    return float(match.group("days") or 0) * 24 + float(match.group("hours") or 0)


def _valid_interval(valid_time: str) -> tuple[datetime, datetime]:
    try:
        start_text = valid_time.split("/", 1)[0]
        start = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise OfficialDataError(f"NWS validTime was invalid: {valid_time}") from exc
    return start, start + timedelta(hours=_duration_hours(valid_time))


def _forecast_metrics(payload: dict[str, Any]) -> dict[str, float | str]:
    properties = payload.get("properties", {})
    temperatures = properties.get("temperature", {}).get("values", [])
    precipitation = properties.get("quantitativePrecipitation", {}).get("values", [])
    if not temperatures:
        raise OfficialDataError("NWS grid forecast has no temperature values")
    valid_times = str(properties.get("validTimes", ""))
    forecast_start, available_end = _valid_interval(valid_times)
    forecast_end = forecast_start + timedelta(days=7)
    if available_end < forecast_end:
        raise OfficialDataError(
            f"NWS forecast window was shorter than seven days: {valid_times}"
        )
    temp_weight = 0.0
    temp_total = 0.0
    temp_max: float | None = None
    for item in temperatures:
        if item.get("value") is None:
            continue
        item_start, item_end = _valid_interval(str(item["validTime"]))
        overlap_start = max(item_start, forecast_start)
        overlap_end = min(item_end, forecast_end)
        hours = max(0.0, (overlap_end - overlap_start).total_seconds() / 3600)
        if hours == 0:
            continue
        value = float(item["value"])
        temp_total += value * hours
        temp_weight += hours
        temp_max = value if temp_max is None else max(temp_max, value)
    if temp_weight == 0 or temp_max is None:
        raise OfficialDataError("NWS grid forecast has no usable temperatures")
    precip_total = 0.0
    for item in precipitation:
        if item.get("value") is None:
            continue
        item_start, item_end = _valid_interval(str(item["validTime"]))
        overlap_start = max(item_start, forecast_start)
        overlap_end = min(item_end, forecast_end)
        overlap_hours = max(
            0.0,
            (overlap_end - overlap_start).total_seconds() / 3600,
        )
        item_hours = (item_end - item_start).total_seconds() / 3600
        if overlap_hours:
            precip_total += float(item["value"]) * overlap_hours / item_hours
    return {
        "mean_temperature_c": temp_total / temp_weight,
        "maximum_temperature_c": temp_max,
        "precipitation_mm": precip_total,
        "updated_at": str(properties.get("updateTime", "")),
        "valid_times": valid_times,
    }


def _forecast_dates(valid_times: str) -> tuple[date, ...]:
    try:
        start_text = valid_times.split("/", 1)[0]
        start = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise OfficialDataError(
            f"NWS forecast validTimes was invalid: {valid_times}"
        ) from exc
    return tuple(start.date() + timedelta(days=offset) for offset in range(7))


def _normal_metrics(
    content: bytes,
    *,
    station: str,
    forecast_dates: tuple[date, ...],
) -> dict[str, float | str]:
    try:
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    except UnicodeDecodeError as exc:
        raise OfficialDataError(
            f"NCEI daily normals were not UTF-8 for {station}"
        ) from exc
    by_month_day = {
        str(row.get("DATE", "")).strip(): row
        for row in rows
        if str(row.get("hour", "")).strip() == "99"
    }
    if not by_month_day:
        raise OfficialDataError(f"NCEI daily normals were empty for {station}")

    station_ids = {
        str(row.get("STATION", "")).strip()
        for row in by_month_day.values()
    }
    if station_ids != {station}:
        raise OfficialDataError(
            f"NCEI daily normals station mismatch for {station}: {station_ids}"
        )

    temperatures_f: list[float] = []
    precipitation_inches = 0.0
    for forecast_date in forecast_dates:
        key = forecast_date.strftime("%m-%d")
        row = by_month_day.get(key)
        if row is None:
            raise OfficialDataError(
                f"NCEI daily normals missing {key} for {station}"
            )
        try:
            temperatures_f.append(float(row["DLY-TAVG-NORMAL"].strip()))
            month_to_date = float(row["MTD-PRCP-NORMAL"].strip())
            if forecast_date.day == 1:
                daily_precipitation = month_to_date
            else:
                previous_key = (forecast_date - timedelta(days=1)).strftime(
                    "%m-%d"
                )
                previous = by_month_day[previous_key]
                previous_month_to_date = float(
                    previous["MTD-PRCP-NORMAL"].strip()
                )
                daily_precipitation = month_to_date - previous_month_to_date
        except (KeyError, TypeError, ValueError) as exc:
            raise OfficialDataError(
                f"NCEI daily normals had invalid values for {station} on {key}"
            ) from exc
        if daily_precipitation < -0.001:
            raise OfficialDataError(
                f"NCEI precipitation normal decreased within a month for "
                f"{station} on {key}"
            )
        precipitation_inches += max(0.0, daily_precipitation)

    mean_temperature_f = sum(temperatures_f) / len(temperatures_f)
    first_row = next(iter(by_month_day.values()))
    return {
        "station": station,
        "station_name": str(first_row.get("NAME", "")).strip(),
        "normal_7_day_mean_temperature_c": (
            mean_temperature_f - 32.0
        ) * 5.0 / 9.0,
        "normal_7_day_precipitation_mm": precipitation_inches * 25.4,
        "forecast_start_date": forecast_dates[0].isoformat(),
        "forecast_end_date": forecast_dates[-1].isoformat(),
    }


def _drought_metrics(content: bytes) -> tuple[str, dict[str, float]]:
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    if len(rows) != 5:
        raise OfficialDataError("Drought.gov corn statistics did not contain D0-D4")
    week = rows[0].get("USDMWEEK", "")
    if not re.fullmatch(r"USDM_\d{8}", week):
        raise OfficialDataError("Drought.gov corn statistics had an invalid week")
    metrics = {}
    for row in rows:
        category = row.get("DM")
        if category not in {"0", "1", "2", "3", "4"}:
            raise OfficialDataError("Drought.gov corn statistics had an invalid category")
        try:
            metrics[f"d{category}_or_worse_percent"] = float(row["CATPRCNT"])
        except (KeyError, ValueError) as exc:
            raise OfficialDataError("Drought.gov corn percentage was invalid") from exc
    return f"{week[-8:-4]}-{week[-4:-2]}-{week[-2:]}", metrics


def load_corn_weather(
    *,
    as_of: datetime,
    session: requests.Session | None = None,
    retrieved_at: datetime | None = None,
    today: date | None = None,
) -> OfficialSnapshot:
    """Load current weather evidence; refuse non-vintage-safe historical calls."""
    current_date = today or date.today()
    if as_of.date() != current_date:
        raise OfficialDataError(
            "current drought and NWS forecast endpoints are not vintage-safe for "
            "historical as-of runs; use a previously captured raw archive"
        )
    client = session or requests.Session()
    headers = {
        "User-Agent": "GrainAgents/1.0 research@example.invalid",
        "Accept": "application/geo+json, application/json, text/csv",
    }
    try:
        drought_response = client.get(DROUGHT_URL, headers=headers, timeout=30)
        drought_response.raise_for_status()
    except requests.RequestException as exc:
        raise OfficialDataError(f"Drought.gov corn request failed: {exc}") from exc
    drought_date, drought = _drought_metrics(drought_response.content)

    def load_state(
        item: tuple[str, tuple[int, float, float]],
    ) -> tuple[str, dict, dict, bytes]:
        state, (_acres, latitude, longitude) = item
        station = NORMAL_STATIONS[state]
        try:
            point_response = client.get(
                NWS_POINTS_URL.format(latitude=latitude, longitude=longitude),
                headers=headers,
                timeout=30,
            )
            point_response.raise_for_status()
            point_payload = point_response.json()
            grid_url = point_payload["properties"]["forecastGridData"]
            grid_response = client.get(grid_url, headers=headers, timeout=30)
            grid_response.raise_for_status()
            grid_payload = grid_response.json()
            normals_response = client.get(
                NORMALS_URL.format(station=station),
                headers=headers,
                timeout=30,
            )
            normals_response.raise_for_status()
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            raise OfficialDataError(
                f"weather or climate-normal request failed for {state}: {exc}"
            ) from exc
        return state, point_payload, grid_payload, normals_response.content

    state_payloads: dict[str, dict[str, Any]] = {}
    state_metrics: dict[str, dict[str, float | str]] = {}
    normals_payloads: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="corn-weather") as executor:
        futures = [
            executor.submit(load_state, item)
            for item in STATE_SAMPLE.items()
        ]
        for future in futures:
            state, point_payload, grid_payload, normals_content = future.result()
            forecast = _forecast_metrics(grid_payload)
            normals = _normal_metrics(
                normals_content,
                station=NORMAL_STATIONS[state],
                forecast_dates=_forecast_dates(str(forecast["valid_times"])),
            )
            forecast.update(normals)
            forecast["temperature_anomaly_c"] = (
                float(forecast["mean_temperature_c"])
                - float(forecast["normal_7_day_mean_temperature_c"])
            )
            forecast["precipitation_anomaly_mm"] = (
                float(forecast["precipitation_mm"])
                - float(forecast["normal_7_day_precipitation_mm"])
            )
            state_payloads[state] = {
                "point": point_payload,
                "grid": grid_payload,
            }
            normals_payloads[state] = normals_content.decode("utf-8-sig")
            state_metrics[state] = forecast

    try:
        latest_forecast_update = max(
            datetime.fromisoformat(
                str(metrics["updated_at"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            for metrics in state_metrics.values()
        )
    except ValueError as exc:
        raise OfficialDataError("NWS forecast update timestamp was invalid") from exc
    if latest_forecast_update > as_of.astimezone(timezone.utc):
        raise OfficialDataError(
            "NWS forecast includes a grid update released after the as-of timestamp"
        )

    sample_acres = sum(item[0] for item in STATE_SAMPLE.values())
    weighted = {
        metric: sum(
            float(state_metrics[state][metric]) * STATE_SAMPLE[state][0]
            for state in STATE_SAMPLE
        )
        / sample_acres
        for metric in (
            "mean_temperature_c",
            "maximum_temperature_c",
            "precipitation_mm",
            "normal_7_day_mean_temperature_c",
            "normal_7_day_precipitation_mm",
            "temperature_anomaly_c",
            "precipitation_anomaly_mm",
        )
    }
    forecast_starts = {
        str(metrics["forecast_start_date"])
        for metrics in state_metrics.values()
    }
    forecast_ends = {
        str(metrics["forecast_end_date"])
        for metrics in state_metrics.values()
    }
    if len(forecast_starts) != 1 or len(forecast_ends) != 1:
        raise OfficialDataError(
            "sampled NWS grids did not share the same seven-day calendar window"
        )
    forecast_start = next(iter(forecast_starts))
    forecast_end = next(iter(forecast_ends))
    forecast_period = f"{forecast_start}/{forecast_end}"
    coverage = sample_acres / US_2026_INTENDED_CORN_ACRES_THOUSAND * 100
    retrieved = retrieved_at or datetime.now(timezone.utc)
    raw_payload = {
        "drought_csv": drought_response.content.decode("utf-8-sig"),
        "nws_states": state_payloads,
        "ncei_daily_normals_csv": normals_payloads,
        "weighting": {
            "source_url": WEIGHTS_URL,
            "vintage": "2026-03-31",
            "unit": "thousand_intended_acres",
            "sample": {
                state: definition[0]
                for state, definition in STATE_SAMPLE.items()
            },
            "us_total": US_2026_INTENDED_CORN_ACRES_THOUSAND,
        },
    }
    raw_content = json.dumps(raw_payload, indent=2, sort_keys=True).encode() + b"\n"
    values = {
        **drought,
        "drought_valid_date": drought_date,
        "sample_weighted_7_day_precipitation_mm": round(
            weighted["precipitation_mm"],
            3,
        ),
        "sample_weighted_7_day_mean_temperature_c": round(
            weighted["mean_temperature_c"],
            3,
        ),
        "sample_weighted_7_day_maximum_temperature_c": round(
            weighted["maximum_temperature_c"],
            3,
        ),
        "sample_weighted_7_day_normal_precipitation_mm": round(
            weighted["normal_7_day_precipitation_mm"],
            3,
        ),
        "sample_weighted_7_day_normal_mean_temperature_c": round(
            weighted["normal_7_day_mean_temperature_c"],
            3,
        ),
        "sample_weighted_7_day_precipitation_anomaly_mm": round(
            weighted["precipitation_anomaly_mm"],
            3,
        ),
        "sample_weighted_7_day_temperature_anomaly_c": round(
            weighted["temperature_anomaly_c"],
            3,
        ),
        "seven_day_forecast_valid_start": forecast_start,
        "seven_day_forecast_valid_end": forecast_end,
        "sample_coverage_percent_of_intended_acres": round(coverage, 3),
    }
    observations = []
    drought_available = datetime.combine(
        date.fromisoformat(drought_date) + timedelta(days=2),
        datetime.min.time().replace(hour=13),
        tzinfo=timezone.utc,
    )
    if drought_available > as_of.astimezone(timezone.utc):
        raise OfficialDataError(
            "Drought.gov corn statistics were released after the as-of timestamp"
        )
    for metric, value in drought.items():
        observations.append(
            OfficialObservation(
                metric=f"corn_drought_{metric}",
                value=value,
                unit="percent_of_corn_area",
                period=drought_date,
                released_at=drought_available,
                available_at=drought_available,
                retrieved_at=retrieved,
                source_id=SOURCE_ID,
                source_url=DROUGHT_URL,
                vintage=drought_date,
                availability_policy=(
                    "current-run archive; Tuesday USDM validity conservatively "
                    "available Thursday at 13:00 UTC"
                ),
                metadata={"weighting": "USDA NASS 2017 corn production geography"},
            )
        )
    for metric, unit in (
        ("sample_weighted_7_day_precipitation_mm", "millimeters"),
        ("sample_weighted_7_day_mean_temperature_c", "degrees_celsius"),
        ("sample_weighted_7_day_maximum_temperature_c", "degrees_celsius"),
        ("sample_weighted_7_day_normal_precipitation_mm", "millimeters"),
        (
            "sample_weighted_7_day_normal_mean_temperature_c",
            "degrees_celsius",
        ),
        (
            "sample_weighted_7_day_precipitation_anomaly_mm",
            "millimeters",
        ),
        (
            "sample_weighted_7_day_temperature_anomaly_c",
            "degrees_celsius",
        ),
        ("sample_coverage_percent_of_intended_acres", "percent"),
    ):
        observations.append(
            OfficialObservation(
                metric=f"corn_weather_{metric}",
                value=values[metric],
                unit=unit,
                period=(
                    forecast_period
                    if metric != "sample_coverage_percent_of_intended_acres"
                    else as_of.date().isoformat()
                ),
                released_at=latest_forecast_update,
                available_at=latest_forecast_update,
                retrieved_at=retrieved,
                source_id=SOURCE_ID,
                source_url=DOCUMENTATION_URL,
                vintage=retrieved.isoformat(),
                availability_policy=(
                    "current-run archive; latest sampled NWS grid update must "
                    "be at or before the as-of timestamp"
                ),
                metadata={
                    "weighting": "2026 intended corn acres",
                    "sample_states": len(STATE_SAMPLE),
                    "weight_source": WEIGHTS_URL,
                    "normal_period": "1991-2020",
                    "normal_stations": NORMAL_STATIONS,
                    "normal_source_url": NORMALS_DATASET_URL,
                },
            )
        )
    section = {
        "status": "partial",
        "commodity": "corn",
        "values": values,
        "missing": [
            "14_day_forecast",
            "crop_condition_ratings",
            "condition_based_weather_risk_score",
            "critical_forecast_dates",
            "calibrated_weather_risk_score",
            "yield_impact_range",
        ],
        "methodology": (
            "Drought exposure is corn-production-weighted by Drought.gov. "
            "Seven-day NWS values are representative-state point forecasts "
            "weighted by USDA 2026 intended corn acres. Temperature and "
            "precipitation anomalies compare the same seven calendar dates "
            "with fixed nearby-station NCEI 1991-2020 daily normals; daily "
            "precipitation normals are changes in month-to-date normals."
        ),
        "state_sample": state_metrics,
    }
    source = {
        "source_id": SOURCE_ID,
        "provider": "NOAA/NIDIS, NWS, NCEI, and USDA NASS",
        "dataset": (
            "Corn drought exposure, seven-day grid forecasts, and "
            "1991-2020 daily climate normals"
        ),
        "source_url": DROUGHT_URL,
        "weather_documentation_url": DOCUMENTATION_URL,
        "normals_documentation_url": NORMALS_DOCUMENTATION_URL,
        "normals_dataset_url": NORMALS_DATASET_URL,
        "normal_stations": NORMAL_STATIONS,
        "weight_source_url": WEIGHTS_URL,
        "retrieved_at": retrieved.isoformat(),
        "vintage": retrieved.isoformat(),
        "sha256": hashlib.sha256(raw_content).hexdigest(),
        "license_scope": "official_public_data",
    }
    return OfficialSnapshot(
        section_name="weather",
        section=section,
        observations=tuple(observations),
        source=source,
        archive_filename=f"corn_weather_{current_date.isoformat()}.json",
        raw_content=raw_content,
    )


__all__ = ["NORMAL_STATIONS", "STATE_SAMPLE", "load_corn_weather"]
