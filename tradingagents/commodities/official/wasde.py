"""USDA WASDE point-in-time adapter for the U.S. corn balance sheet."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests

from .models import OfficialDataError, OfficialObservation, OfficialSnapshot

ARCHIVE_URL = (
    "https://esmis.nal.usda.gov/publication/"
    "world-agricultural-supply-and-demand-estimates"
)
SOURCE_ID = "source_usda_wasde_corn"
NEW_YORK = ZoneInfo("America/New_York")

METRICS = {
    "Area Planted": ("area_planted", "million_acres"),
    "Area Harvested": ("area_harvested", "million_acres"),
    "Yield per Harvested Acre": (
        "yield_per_harvested_acre",
        "bushels_per_acre",
    ),
    "Beginning Stocks": ("beginning_stocks", "million_bushels"),
    "Production": ("production", "million_bushels"),
    "Imports": ("imports", "million_bushels"),
    "Supply, Total": ("total_supply", "million_bushels"),
    "Feed and Residual": ("feed_and_residual", "million_bushels"),
    "Food, Seed & Industrial": ("food_seed_and_industrial", "million_bushels"),
    "Ethanol & by-products": ("ethanol_and_byproducts", "million_bushels"),
    "Domestic, Total": ("domestic_total", "million_bushels"),
    "Exports": ("exports", "million_bushels"),
    "Use, Total": ("total_use", "million_bushels"),
    "Ending Stocks": ("ending_stocks", "million_bushels"),
    "Avg. Farm Price ($/bu)": ("average_farm_price", "USD_per_bushel"),
}


@dataclass(frozen=True)
class WasdeRelease:
    release_date: date
    available_at: datetime
    url: str
    vintage: str


class _ReleaseIndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_row = False
        self.row_date: date | None = None
        self.row_xml: str | None = None
        self.rows: list[tuple[date, str]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if tag == "tr":
            self.in_row = True
            self.row_date = None
            self.row_xml = None
        elif self.in_row and tag == "time" and attributes.get("datetime"):
            self.row_date = date.fromisoformat(attributes["datetime"][:10])
        elif self.in_row and tag == "a":
            href = attributes.get("href")
            if href and href.lower().endswith(".xml"):
                self.row_xml = href

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr" and self.in_row:
            if self.row_date is not None and self.row_xml is not None:
                self.rows.append((self.row_date, self.row_xml))
            self.in_row = False


def _availability(release_date: date, filename: str) -> tuple[datetime, str]:
    revision = re.search(r"v(\d+)(?=\.xml$)", filename, flags=re.IGNORECASE)
    revision_number = int(revision.group(1)) if revision else 1
    conservative_date = release_date + timedelta(days=revision_number - 1)
    local = datetime.combine(conservative_date, time(12, 0), tzinfo=NEW_YORK)
    return local.astimezone(timezone.utc), f"release_v{revision_number}"


def parse_release_index(content: str) -> tuple[WasdeRelease, ...]:
    parser = _ReleaseIndexParser()
    parser.feed(content)
    releases = []
    for release_date, href in parser.rows:
        url = urljoin(ARCHIVE_URL, href)
        available_at, vintage = _availability(release_date, url)
        releases.append(
            WasdeRelease(
                release_date=release_date,
                available_at=available_at,
                url=url,
                vintage=vintage,
            )
        )
    return tuple(sorted(releases, key=lambda item: item.available_at, reverse=True))


def _clean_label(value: str) -> str:
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())


def _numeric(value: str, metric: str) -> int | float:
    cleaned = value.replace(",", "").replace("*", "").strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", cleaned):
        raise OfficialDataError(f"WASDE {metric} is not a single numeric value: {value!r}")
    parsed = float(cleaned)
    return int(parsed) if parsed.is_integer() else parsed


def parse_corn_balance(
    content: bytes,
    *,
    crop_year: str,
    release: WasdeRelease,
    retrieved_at: datetime,
) -> tuple[dict[str, int | float], tuple[OfficialObservation, ...]]:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise OfficialDataError("USDA WASDE returned invalid XML") from exc

    report = root.find("./sr12/Report")
    if report is None or "Corn Supply and Use" not in report.get(
        "sub_report_title",
        "",
    ):
        raise OfficialDataError("WASDE corn balance table sr12 was not found")

    release_month = release.release_date.strftime("%B")
    values: dict[str, int | float] = {}
    observations: list[OfficialObservation] = []
    groups = report.findall("./matrix2/m2_attribute_group_Collection/m2_attribute_group")
    for group in groups:
        attribute = group.find("./attribute2")
        if attribute is None:
            continue
        original_label = _clean_label(attribute.get("attribute2", ""))
        normalized_label = re.sub(
            r"\s+\d+/$",
            "",
            original_label.replace(" ,", ","),
        )
        matched = next(
            (
                (source_label, definition)
                for source_label, definition in METRICS.items()
                if normalized_label == source_label
            ),
            None,
        )
        if matched is None:
            continue
        _, (metric, unit) = matched
        for year_group in attribute.findall("./m2_year_group_Collection/m2_year_group"):
            if not year_group.get("market_year2", "").strip().startswith(crop_year):
                continue
            month_group = year_group.find("./m2_month_group_Collection/m2_month_group")
            if month_group is None:
                continue
            if month_group.get("forecast_month2", "").strip() != release_month:
                continue
            cell = month_group.find("./Cell")
            raw_value = None if cell is None else cell.get("cell_value2")
            if not raw_value:
                continue
            value = _numeric(raw_value, metric)
            values[metric] = value
            observations.append(
                OfficialObservation(
                    metric=f"wasde_corn_{metric}",
                    value=value,
                    unit=unit,
                    period=crop_year,
                    released_at=release.available_at,
                    available_at=release.available_at,
                    retrieved_at=retrieved_at,
                    source_id=SOURCE_ID,
                    source_url=release.url,
                    vintage=release.vintage,
                    availability_policy=(
                        "USDA release date at noon America/New_York; "
                        "revisions conservatively delayed one day per version"
                    ),
                    metadata={
                        "report": "WASDE",
                        "table": "sr12",
                        "release_date": release.release_date.isoformat(),
                        "release_month": release_month,
                        "crop_year": crop_year,
                    },
                )
            )
            break
    required = {"production", "total_supply", "total_use", "ending_stocks"}
    missing = sorted(required - values.keys())
    if missing:
        raise OfficialDataError(
            f"WASDE corn balance is missing required metrics: {', '.join(missing)}"
        )
    return values, tuple(observations)


def load_corn_wasde(
    *,
    as_of: datetime,
    crop_year: str,
    session: requests.Session | None = None,
    retrieved_at: datetime | None = None,
) -> OfficialSnapshot:
    client = session or requests.Session()
    headers = {"User-Agent": "GrainAgents/1.0 research@example.invalid"}
    try:
        index_response = client.get(ARCHIVE_URL, headers=headers, timeout=30)
        index_response.raise_for_status()
    except requests.RequestException as exc:
        raise OfficialDataError(f"USDA WASDE archive request failed: {exc}") from exc
    releases = parse_release_index(index_response.text)
    release = next(
        (item for item in releases if item.available_at <= as_of.astimezone(timezone.utc)),
        None,
    )
    if release is None:
        raise OfficialDataError("no point-in-time-safe WASDE XML release was found")
    try:
        response = client.get(release.url, headers=headers, timeout=60)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise OfficialDataError(f"USDA WASDE XML request failed: {exc}") from exc
    retrieved = retrieved_at or datetime.now(timezone.utc)
    digest = hashlib.sha256(response.content).hexdigest()
    values, observations = parse_corn_balance(
        response.content,
        crop_year=crop_year,
        release=release,
        retrieved_at=retrieved,
    )
    section = {
        "status": "ready",
        "commodity": "corn",
        "crop_year": crop_year,
        "report": "WASDE",
        "release_date": release.release_date.isoformat(),
        "available_at": release.available_at.isoformat(),
        "vintage": release.vintage,
        "values": values,
    }
    source = {
        "source_id": SOURCE_ID,
        "provider": "USDA",
        "dataset": "World Agricultural Supply and Demand Estimates",
        "source_url": release.url,
        "archive_index_url": ARCHIVE_URL,
        "release_date": release.release_date.isoformat(),
        "available_at": release.available_at.isoformat(),
        "retrieved_at": retrieved.isoformat(),
        "vintage": release.vintage,
        "sha256": digest,
        "license_scope": "official_publication",
    }
    return OfficialSnapshot(
        section_name="supply_demand",
        section=section,
        observations=observations,
        source=source,
        archive_filename=f"usda_wasde_{release.release_date.isoformat()}.xml",
        raw_content=response.content,
    )


__all__ = [
    "ARCHIVE_URL",
    "WasdeRelease",
    "load_corn_wasde",
    "parse_corn_balance",
    "parse_release_index",
]
