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


def _forecast_metrics(payload: dict[str, Any]) -> dict[str, float | str]:
    properties = payload.get("properties", {})
    temperatures = properties.get("temperature", {}).get("values", [])
    precipitation = properties.get("quantitativePrecipitation", {}).get("values", [])
    if not temperatures:
        raise OfficialDataError("NWS grid forecast has no temperature values")
    temp_weight = 0.0
    temp_total = 0.0
    temp_max: float | None = None
    for item in temperatures:
        if item.get("value") is None:
            continue
        hours = _duration_hours(str(item["validTime"]))
        value = float(item["value"])
        temp_total += value * hours
        temp_weight += hours
        temp_max = value if temp_max is None else max(temp_max, value)
    if temp_weight == 0 or temp_max is None:
        raise OfficialDataError("NWS grid forecast has no usable temperatures")
    precip_total = sum(
        float(item["value"])
        for item in precipitation
        if item.get("value") is not None
    )
    return {
        "mean_temperature_c": temp_total / temp_weight,
        "maximum_temperature_c": temp_max,
        "precipitation_mm": precip_total,
        "updated_at": str(properties.get("updateTime", "")),
        "valid_times": str(properties.get("validTimes", "")),
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

    def load_state(item: tuple[str, tuple[int, float, float]]) -> tuple[str, dict, dict]:
        state, (_acres, latitude, longitude) = item
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
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            raise OfficialDataError(f"NWS forecast request failed for {state}: {exc}") from exc
        return state, point_payload, grid_payload

    state_payloads: dict[str, dict[str, Any]] = {}
    state_metrics: dict[str, dict[str, float | str]] = {}
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="corn-weather") as executor:
        futures = [
            executor.submit(load_state, item)
            for item in STATE_SAMPLE.items()
        ]
        for future in futures:
            state, point_payload, grid_payload = future.result()
            state_payloads[state] = {
                "point": point_payload,
                "grid": grid_payload,
            }
            state_metrics[state] = _forecast_metrics(grid_payload)

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
        )
    }
    coverage = sample_acres / US_2026_INTENDED_CORN_ACRES_THOUSAND * 100
    retrieved = retrieved_at or datetime.now(timezone.utc)
    raw_payload = {
        "drought_csv": drought_response.content.decode("utf-8-sig"),
        "nws_states": state_payloads,
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
        ("sample_coverage_percent_of_intended_acres", "percent"),
    ):
        observations.append(
            OfficialObservation(
                metric=f"corn_weather_{metric}",
                value=values[metric],
                unit=unit,
                period=as_of.date().isoformat(),
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
                },
            )
        )
    section = {
        "status": "partial",
        "commodity": "corn",
        "values": values,
        "missing": [
            "production_weighted_rainfall_anomaly",
            "production_weighted_temperature_anomaly",
            "14_day_forecast",
            "calibrated_weather_risk_score",
            "yield_impact_range",
        ],
        "methodology": (
            "Drought exposure is corn-production-weighted by Drought.gov. "
            "Seven-day NWS values are representative-state point forecasts "
            "weighted by USDA 2026 intended corn acres."
        ),
        "state_sample": state_metrics,
    }
    source = {
        "source_id": SOURCE_ID,
        "provider": "NOAA/NIDIS, NWS, and USDA NASS",
        "dataset": "Corn drought exposure and seven-day grid forecasts",
        "source_url": DROUGHT_URL,
        "weather_documentation_url": DOCUMENTATION_URL,
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


__all__ = ["STATE_SAMPLE", "load_corn_weather"]
