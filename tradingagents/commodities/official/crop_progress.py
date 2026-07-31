"""USDA NASS weekly corn condition and reproductive-stage evidence."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timezone
from html.parser import HTMLParser
from typing import Any

import requests

from .models import OfficialDataError, OfficialObservation, OfficialSnapshot

LANDING_URL = (
    "https://usda.library.cornell.edu/concern/publications/"
    "8336h188j?locale=en"
)
REPORT_URL = "https://release.nass.usda.gov/reports/{filename}"
SOURCE_ID = "source_usda_nass_corn_crop_progress"

CONDITION_WEIGHTS = {
    "very_poor": 100,
    "poor": 75,
    "fair": 50,
    "good": 25,
    "excellent": 0,
}


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.hrefs.append(href)


def _release_entries(html: str) -> list[tuple[date, str]]:
    parser = _AnchorParser()
    parser.feed(html)
    entries: list[tuple[date, str]] = []
    for index, href in enumerate(parser.hrefs):
        match = re.fullmatch(
            r"/publication/crop-progress/(?P<date>\d{4}-\d{2}-\d{2})",
            href,
        )
        if match is None:
            continue
        filename = next(
            (
                candidate.rsplit("/", 1)[-1]
                for candidate in reversed(parser.hrefs[:index])
                if re.fullmatch(r"prog\d{4}\.txt", candidate.rsplit("/", 1)[-1])
            ),
            None,
        )
        if filename is None:
            raise OfficialDataError(
                f"Crop Progress archive had no text file for {match.group('date')}"
            )
        entries.append((date.fromisoformat(match.group("date")), filename))
    if not entries:
        raise OfficialDataError("Crop Progress archive contained no dated releases")
    return entries


def _available_at(release_date: date) -> datetime:
    # Reports are released after 4 p.m. Eastern. 21:00 UTC is deliberately
    # conservative during both standard and daylight time.
    return datetime.combine(release_date, time(hour=21), tzinfo=timezone.utc)


def _report_block(text: str, heading: str, next_heading: str) -> str:
    start = text.find(heading)
    if start < 0:
        raise OfficialDataError(f"Crop Progress report omitted {heading}")
    end = text.find(next_heading, start + len(heading))
    if end < 0:
        raise OfficialDataError(
            f"Crop Progress report omitted section after {heading}"
        )
    return text[start:end]


def _row_values(block: str, label: str, expected: int) -> tuple[int, ...]:
    match = re.search(
        rf"^[ \t]*{re.escape(label)}[ \t.]*:[ \t]*"
        rf"(?P<values>[^\r\n]*?)[ \t]*\r?$",
        block,
        flags=re.MULTILINE,
    )
    if match is None:
        raise OfficialDataError(f"Crop Progress report omitted row {label}")
    tokens = re.findall(r"(?<!\d)(?:-|\d+)(?!\d)", match.group("values"))
    if len(tokens) != expected:
        raise OfficialDataError(
            f"Crop Progress row {label} had {len(tokens)} values, expected {expected}"
        )
    return tuple(0 if token == "-" else int(token) for token in tokens)


def _condition_risk(values: tuple[int, ...]) -> float:
    if len(values) != len(CONDITION_WEIGHTS) or sum(values) != 100:
        raise OfficialDataError("Crop Progress condition shares did not sum to 100")
    return sum(
        share * weight
        for share, weight in zip(values, CONDITION_WEIGHTS.values(), strict=True)
    ) / 100


def parse_corn_crop_progress(
    text: str,
    *,
    expected_release_date: date,
) -> dict[str, Any]:
    """Parse national corn condition, silking, and dough rows."""
    release_match = re.search(
        r"Released (?P<date>[A-Z][a-z]+ \d{1,2}, \d{4}), by the National",
        text,
    )
    if release_match is None:
        raise OfficialDataError("Crop Progress release date was missing")
    release_date = datetime.strptime(
        release_match.group("date"),
        "%B %d, %Y",
    ).date()
    if release_date != expected_release_date:
        raise OfficialDataError(
            "Crop Progress document release date did not match archive index"
        )

    condition = _report_block(
        text,
        "Corn Condition - Selected States",
        "Soybeans Blooming - Selected States",
    )
    week_match = re.search(
        r"Week Ending (?P<date>[A-Z][a-z]+ \d{1,2}, \d{4})",
        condition,
    )
    coverage_match = re.search(
        r"These 18 States planted (?P<coverage>\d+)% of the (?P<year>\d{4}) corn acreage",
        condition,
    )
    if week_match is None or coverage_match is None:
        raise OfficialDataError(
            "Crop Progress condition period or acreage coverage was missing"
        )
    week_ending = datetime.strptime(
        week_match.group("date"),
        "%B %d, %Y",
    ).date()
    current = _row_values(condition, "18 States", 5)
    previous_week = _row_values(condition, "Previous week", 5)
    previous_year = _row_values(condition, "Previous year", 5)

    silking = _report_block(
        text,
        "Corn Silking - Selected States",
        "Corn Dough - Selected States",
    )
    dough = _report_block(
        text,
        "Corn Dough - Selected States",
        "Corn Condition - Selected States",
    )
    silking_values = _row_values(silking, "18 States", 4)
    dough_values = _row_values(dough, "18 States", 4)

    labels = tuple(CONDITION_WEIGHTS)
    condition_shares = dict(zip(labels, current, strict=True))
    current_risk = _condition_risk(current)
    prior_week_risk = _condition_risk(previous_week)
    prior_year_risk = _condition_risk(previous_year)
    coverage = int(coverage_match.group("coverage"))
    return {
        "week_ending": week_ending.isoformat(),
        "release_date": release_date.isoformat(),
        "coverage_crop_year": int(coverage_match.group("year")),
        "condition_acreage_coverage_percent": coverage,
        "condition_shares_percent": condition_shares,
        "good_excellent_percent": condition_shares["good"]
        + condition_shares["excellent"],
        "poor_very_poor_percent": condition_shares["poor"]
        + condition_shares["very_poor"],
        "condition_based_weather_risk_score": round(current_risk, 3),
        "condition_based_weather_risk_change_week_over_week": round(
            current_risk - prior_week_risk,
            3,
        ),
        "condition_based_weather_risk_change_year_over_year": round(
            current_risk - prior_year_risk,
            3,
        ),
        "condition_risk_data_coverage_confidence_percent": coverage,
        "silking_percent": silking_values[2],
        "silking_five_year_average_percent": silking_values[3],
        "silking_vs_five_year_average_percentage_points": (
            silking_values[2] - silking_values[3]
        ),
        "dough_percent": dough_values[2],
        "dough_five_year_average_percent": dough_values[3],
        "dough_vs_five_year_average_percentage_points": (
            dough_values[2] - dough_values[3]
        ),
    }


def load_corn_crop_progress(
    *,
    as_of: datetime,
    session: requests.Session | None = None,
    retrieved_at: datetime | None = None,
) -> OfficialSnapshot:
    """Load the latest archived weekly report available at ``as_of``."""
    client = session or requests.Session()
    headers = {
        "User-Agent": "GrainAgents/1.0 research@example.invalid",
        "Accept": "text/html,text/plain",
    }
    try:
        landing_response = client.get(LANDING_URL, headers=headers, timeout=30)
        landing_response.raise_for_status()
    except requests.RequestException as exc:
        raise OfficialDataError(f"Crop Progress archive request failed: {exc}") from exc
    try:
        landing_html = landing_response.content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise OfficialDataError("Crop Progress archive was not UTF-8") from exc

    as_of_utc = as_of.astimezone(timezone.utc)
    candidates = [
        (release_date, filename)
        for release_date, filename in _release_entries(landing_html)
        if _available_at(release_date) <= as_of_utc
    ]
    if not candidates:
        raise OfficialDataError("no Crop Progress report was available as of the run")
    release_date, filename = max(candidates)
    report_url = REPORT_URL.format(filename=filename)
    try:
        report_response = client.get(report_url, headers=headers, timeout=30)
        report_response.raise_for_status()
    except requests.RequestException as exc:
        raise OfficialDataError(f"Crop Progress report request failed: {exc}") from exc
    try:
        report_text = report_response.content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise OfficialDataError("Crop Progress report was not UTF-8") from exc

    values = parse_corn_crop_progress(
        report_text,
        expected_release_date=release_date,
    )
    available_at = _available_at(release_date)
    retrieved = retrieved_at or datetime.now(timezone.utc)
    period = str(values["week_ending"])
    units = {
        "good_excellent_percent": "percent_of_reported_corn_acres",
        "poor_very_poor_percent": "percent_of_reported_corn_acres",
        "condition_based_weather_risk_score": "index_0_low_to_100_high",
        "condition_based_weather_risk_change_week_over_week": "index_points",
        "condition_based_weather_risk_change_year_over_year": "index_points",
        "condition_risk_data_coverage_confidence_percent": "percent",
        "silking_percent": "percent_of_reported_corn_acres",
        "silking_five_year_average_percent": "percent_of_reported_corn_acres",
        "silking_vs_five_year_average_percentage_points": "percentage_points",
        "dough_percent": "percent_of_reported_corn_acres",
        "dough_five_year_average_percent": "percent_of_reported_corn_acres",
        "dough_vs_five_year_average_percentage_points": "percentage_points",
    }
    observations = tuple(
        OfficialObservation(
            metric=f"corn_crop_{metric}",
            value=values[metric],
            unit=unit,
            period=period,
            released_at=available_at,
            available_at=available_at,
            retrieved_at=retrieved,
            source_id=SOURCE_ID,
            source_url=report_url,
            vintage=release_date.isoformat(),
            availability_policy=(
                "archived weekly report; conservatively available at 21:00 UTC "
                "on its USDA NASS release date"
            ),
            metadata={
                "reported_state_count": 18,
                "acreage_coverage_percent": values[
                    "condition_acreage_coverage_percent"
                ],
                "risk_score_formula": CONDITION_WEIGHTS,
                "risk_score_scope": (
                    "condition-based monitoring index; not a yield-calibrated model"
                ),
            },
        )
        for metric, unit in units.items()
    )
    section = {
        "status": "ready",
        "commodity": "corn",
        "report": "USDA NASS Crop Progress",
        "values": values,
        "methodology": (
            "The condition-based weather-risk score weights USDA very poor, "
            "poor, fair, good, and excellent shares at 100, 75, 50, 25, and "
            "0. Higher values mean worse reported crop condition. It is a "
            "transparent monitoring index, not a yield-calibrated model."
        ),
    }
    raw_payload = {
        "landing_url": LANDING_URL,
        "landing_html": landing_html,
        "report_url": report_url,
        "report_text": report_text,
    }
    raw_content = json.dumps(raw_payload, indent=2, sort_keys=True).encode() + b"\n"
    source = {
        "source_id": SOURCE_ID,
        "provider": "USDA National Agricultural Statistics Service",
        "dataset": "Crop Progress",
        "source_url": report_url,
        "archive_index_url": LANDING_URL,
        "retrieved_at": retrieved.isoformat(),
        "vintage": release_date.isoformat(),
        "sha256": hashlib.sha256(raw_content).hexdigest(),
        "license_scope": "official_public_data",
    }
    return OfficialSnapshot(
        section_name="crop_progress",
        section=section,
        observations=observations,
        source=source,
        archive_filename=f"usda_nass_crop_progress_{release_date.isoformat()}.json",
        raw_content=raw_content,
    )


__all__ = ["load_corn_crop_progress", "parse_corn_crop_progress"]
