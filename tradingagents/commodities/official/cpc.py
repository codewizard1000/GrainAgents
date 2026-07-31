"""NOAA CPC 8-14 day corn-area outlook from dated GIS archives."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import zipfile
from datetime import date, datetime, time, timedelta, timezone
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests

from .models import OfficialDataError, OfficialObservation, OfficialSnapshot
from .weather import STATE_SAMPLE, US_2026_INTENDED_CORN_ACRES_THOUSAND

GIS_URL = (
    "https://ftp.cpc.ncep.noaa.gov/GIS/us_tempprcpfcst/"
    "814{variable}_{issue_date}.kmz"
)
DOCUMENTATION_URL = (
    "https://www.cpc.ncep.noaa.gov/products/GIS/GIS_DATA/"
    "us_tempprcpfcst/814-day.php"
)
SOURCE_ID = "source_noaa_cpc_corn_8_14_day"
NEW_YORK = ZoneInfo("America/New_York")
KML_NAMESPACE = {"kml": "http://www.opengis.net/kml/2.2"}


def conservative_available_at(issue_date: date) -> datetime:
    """Make a daily CPC issue usable at 6 a.m. New York time next day."""
    local = datetime.combine(
        issue_date + timedelta(days=1),
        time(6, 0),
        tzinfo=NEW_YORK,
    )
    return local.astimezone(timezone.utc)


def _available_issue_date(as_of: datetime) -> tuple[date, datetime]:
    issue_date = as_of.date()
    as_of_utc = as_of.astimezone(timezone.utc)
    while conservative_available_at(issue_date) > as_of_utc:
        issue_date -= timedelta(days=1)
    return issue_date, conservative_available_at(issue_date)


def _coordinates(text: str) -> list[tuple[float, float]]:
    try:
        return [
            (float(parts[0]), float(parts[1]))
            for token in text.split()
            if len(parts := token.split(",")) >= 2
        ]
    except ValueError as exc:
        raise OfficialDataError("CPC KML polygon has invalid coordinates") from exc


def _point_in_ring(
    longitude: float,
    latitude: float,
    ring: list[tuple[float, float]],
) -> bool:
    inside = False
    previous = len(ring) - 1
    for current, (current_x, current_y) in enumerate(ring):
        previous_x, previous_y = ring[previous]
        crosses = (current_y > latitude) != (previous_y > latitude)
        if crosses:
            boundary_x = (
                (previous_x - current_x)
                * (latitude - current_y)
                / (previous_y - current_y)
                + current_x
            )
            if longitude < boundary_x:
                inside = not inside
        previous = current
    return inside


def _point_in_polygon(
    longitude: float,
    latitude: float,
    polygon: ElementTree.Element,
) -> bool:
    outer = polygon.find(
        "kml:outerBoundaryIs/kml:LinearRing/kml:coordinates",
        KML_NAMESPACE,
    )
    if outer is None or not outer.text:
        raise OfficialDataError("CPC KML polygon has no outer boundary")
    if not _point_in_ring(longitude, latitude, _coordinates(outer.text)):
        return False
    for inner in polygon.findall(
        "kml:innerBoundaryIs/kml:LinearRing/kml:coordinates",
        KML_NAMESPACE,
    ):
        if inner.text and _point_in_ring(
            longitude,
            latitude,
            _coordinates(inner.text),
        ):
            return False
    return True


def _kml_from_kmz(content: bytes, *, variable: str) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.lower().endswith(".kml")
            ]
            if len(names) != 1:
                raise OfficialDataError(
                    f"CPC {variable} KMZ did not contain exactly one KML"
                )
            return archive.read(names[0])
    except (zipfile.BadZipFile, KeyError) as exc:
        raise OfficialDataError(f"CPC {variable} KMZ was invalid") from exc


def _parse_outlook(
    content: bytes,
    *,
    variable: str,
    expected_issue_date: date,
) -> dict:
    try:
        root = ElementTree.fromstring(_kml_from_kmz(content, variable=variable))
    except ElementTree.ParseError as exc:
        raise OfficialDataError(f"CPC {variable} KML was invalid") from exc
    document_name = root.findtext(
        "kml:Document/kml:name",
        default="",
        namespaces=KML_NAMESPACE,
    )
    dates = re.search(
        r"Created: (\d{2}/\d{2}/\d{4}) - Valid: "
        r"(\d{2}/\d{2}/\d{4}) - (\d{2}/\d{2}/\d{4})",
        document_name,
    )
    if dates is None:
        raise OfficialDataError(f"CPC {variable} KML has invalid issue dates")
    issue_date, valid_start, valid_end = (
        datetime.strptime(value, "%m/%d/%Y").date()
        for value in dates.groups()
    )
    if issue_date != expected_issue_date:
        raise OfficialDataError(
            f"CPC {variable} KML issue date did not match dated archive"
        )

    variable_label = "Temperature" if variable == "temp" else "Precipitation"
    pattern = re.compile(
        rf"(?P<probability>\d+(?:\.\d+)?)% Chance of "
        rf"(?P<category>Above|Near|Below) Normal {variable_label}"
    )
    polygons = []
    for placemark in root.findall(".//kml:Placemark", KML_NAMESPACE):
        name = placemark.findtext(
            "kml:name",
            default="",
            namespaces=KML_NAMESPACE,
        )
        match = pattern.fullmatch(name)
        if match is None:
            continue
        category = f"{match.group('category').lower()}_normal"
        probability = float(match.group("probability"))
        for polygon in placemark.findall(".//kml:Polygon", KML_NAMESPACE):
            polygons.append((category, probability, polygon))
    if not polygons:
        raise OfficialDataError(f"CPC {variable} KML had no outlook polygons")

    state_values = {}
    for state, (_acres, latitude, longitude) in STATE_SAMPLE.items():
        matches = [
            (category, probability)
            for category, probability, polygon in polygons
            if _point_in_polygon(longitude, latitude, polygon)
        ]
        if len(matches) != 1:
            raise OfficialDataError(
                f"CPC {variable} outlook matched {len(matches)} polygons for {state}"
            )
        category, probability = matches[0]
        state_values[state] = {
            "category": category,
            "probability_percent": probability,
        }

    sample_acres = sum(definition[0] for definition in STATE_SAMPLE.values())
    shares = {
        category: sum(
            STATE_SAMPLE[state][0]
            for state, value in state_values.items()
            if value["category"] == category
        )
        / sample_acres
        * 100
        for category in ("above_normal", "near_normal", "below_normal")
    }
    weighted_probability = sum(
        STATE_SAMPLE[state][0] * float(value["probability_percent"])
        for state, value in state_values.items()
    ) / sample_acres
    dominant_category = max(shares, key=shares.get)
    return {
        "issue_date": issue_date.isoformat(),
        "valid_start": valid_start.isoformat(),
        "valid_end": valid_end.isoformat(),
        "state_sample": state_values,
        "acre_share_percent": {
            key: round(value, 3) for key, value in shares.items()
        },
        "weighted_category_probability_percent": round(
            weighted_probability,
            3,
        ),
        "dominant_category": dominant_category,
        "sample_coverage_percent_of_intended_acres": round(
            sample_acres / US_2026_INTENDED_CORN_ACRES_THOUSAND * 100,
            3,
        ),
    }


def load_corn_8_14_day_outlook(
    *,
    as_of: datetime,
    session: requests.Session | None = None,
    retrieved_at: datetime | None = None,
    today: date | None = None,
) -> OfficialSnapshot:
    """Load dated CPC temperature and precipitation probability polygons."""
    current_date = today or date.today()
    if as_of.date() != current_date:
        raise OfficialDataError(
            "CPC current GIS retrieval is not replay-safe for historical as-of "
            "runs; use a previously captured dated archive"
        )
    issue_date, available_at = _available_issue_date(as_of)
    client = session or requests.Session()
    headers = {"User-Agent": "GrainAgents/1.0 research@example.invalid"}
    contents = {}
    parsed = {}
    urls = {}
    for variable in ("temp", "prcp"):
        url = GIS_URL.format(
            variable=variable,
            issue_date=issue_date.strftime("%Y%m%d"),
        )
        try:
            response = client.get(url, headers=headers, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise OfficialDataError(
                f"CPC 8-14 day {variable} request failed: {exc}"
            ) from exc
        contents[variable] = response.content
        urls[variable] = url
        parsed[variable] = _parse_outlook(
            response.content,
            variable=variable,
            expected_issue_date=issue_date,
        )
    if any(
        item["valid_start"] != parsed["temp"]["valid_start"]
        or item["valid_end"] != parsed["temp"]["valid_end"]
        for item in parsed.values()
    ):
        raise OfficialDataError(
            "CPC temperature and precipitation validity windows differ"
        )

    retrieved = retrieved_at or datetime.now(timezone.utc)
    raw_content = (
        json.dumps(
            {
                "retrieved_at": retrieved.isoformat(),
                "issue_date": issue_date.isoformat(),
                "temperature_url": urls["temp"],
                "precipitation_url": urls["prcp"],
                "temperature_kmz_base64": base64.b64encode(
                    contents["temp"]
                ).decode("ascii"),
                "precipitation_kmz_base64": base64.b64encode(
                    contents["prcp"]
                ).decode("ascii"),
            },
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    period = f"{parsed['temp']['valid_start']}/{parsed['temp']['valid_end']}"
    observations = []
    for variable, prefix in (("temp", "temperature"), ("prcp", "precipitation")):
        outlook = parsed[variable]
        for category, value in outlook["acre_share_percent"].items():
            observations.append(
                OfficialObservation(
                    metric=f"cpc_corn_8_14_day_{prefix}_{category}_acre_share",
                    value=value,
                    unit="percent_of_sampled_intended_corn_acres",
                    period=period,
                    released_at=available_at,
                    available_at=available_at,
                    retrieved_at=retrieved,
                    source_id=SOURCE_ID,
                    source_url=urls[variable],
                    vintage=issue_date.isoformat(),
                    availability_policy=(
                        "dated CPC archive conservatively available at 6:00 a.m. "
                        "America/New_York on the day after issue"
                    ),
                    metadata={
                        "issue_date": issue_date.isoformat(),
                        "valid_start": outlook["valid_start"],
                        "valid_end": outlook["valid_end"],
                        "weighting": "2026 intended corn acres",
                    },
                )
            )
        observations.append(
            OfficialObservation(
                metric=f"cpc_corn_8_14_day_{prefix}_weighted_probability",
                value=outlook["weighted_category_probability_percent"],
                unit="percent_probability_of_assigned_category",
                period=period,
                released_at=available_at,
                available_at=available_at,
                retrieved_at=retrieved,
                source_id=SOURCE_ID,
                source_url=urls[variable],
                vintage=issue_date.isoformat(),
                availability_policy=(
                    "dated CPC archive conservatively available at 6:00 a.m. "
                    "America/New_York on the day after issue"
                ),
                metadata={
                    "dominant_category": outlook["dominant_category"],
                    "weighting": "2026 intended corn acres",
                },
            )
        )

    section = {
        "status": "ready",
        "coverage_status": "sampled_corn_area",
        "report": "NOAA CPC 8-14 Day Outlook",
        "issue_date": issue_date.isoformat(),
        "available_at": available_at.isoformat(),
        "valid_start": parsed["temp"]["valid_start"],
        "valid_end": parsed["temp"]["valid_end"],
        "temperature": parsed["temp"],
        "precipitation": parsed["prcp"],
        "methodology": (
            "CPC probability polygons are sampled at twelve transparent Corn "
            "Belt points and weighted by USDA 2026 intended corn acres."
        ),
    }
    source = {
        "source_id": SOURCE_ID,
        "provider": "NOAA Climate Prediction Center",
        "dataset": "8-14 Day Temperature and Precipitation Outlook GIS",
        "source_url": DOCUMENTATION_URL,
        "temperature_archive_url": urls["temp"],
        "precipitation_archive_url": urls["prcp"],
        "issue_date": issue_date.isoformat(),
        "available_at": available_at.isoformat(),
        "retrieved_at": retrieved.isoformat(),
        "vintage": issue_date.isoformat(),
        "sha256": hashlib.sha256(raw_content).hexdigest(),
        "license_scope": "official_public_data",
        "api_key_mode": "not_required",
    }
    return OfficialSnapshot(
        section_name="weather_outlook",
        section=section,
        observations=tuple(observations),
        source=source,
        archive_filename=f"cpc_corn_8_14_day_{issue_date.isoformat()}.json",
        raw_content=raw_content,
    )


__all__ = [
    "conservative_available_at",
    "load_corn_8_14_day_outlook",
]
