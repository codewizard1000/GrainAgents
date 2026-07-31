"""Current-run FRED macro context with conservative availability rules."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from .models import OfficialDataError, OfficialObservation, OfficialSnapshot

GRAPH_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
SOURCE_ID = "source_fred_grain_macro"
NEW_YORK = ZoneInfo("America/New_York")
LOOKBACK_DAYS = 45

SERIES = {
    "broad_us_dollar_index": {
        "series_id": "DTWEXBGS",
        "metric": "fred_broad_us_dollar_index",
        "unit": "index_jan_2006_100",
        "title": "Nominal Broad U.S. Dollar Index",
        "underlying_provider": "Board of Governors of the Federal Reserve System",
        "release": "H.10 Foreign Exchange Rates",
        "source_url": "https://fred.stlouisfed.org/series/DTWEXBGS",
        "grain_role": "currency context for U.S. export competitiveness",
    },
    "wti_crude_oil": {
        "series_id": "DCOILWTICO",
        "metric": "fred_wti_crude_oil_usd_per_barrel",
        "unit": "usd_per_barrel",
        "title": "Crude Oil Prices: West Texas Intermediate",
        "underlying_provider": "U.S. Energy Information Administration",
        "release": "EIA Spot Prices",
        "source_url": "https://fred.stlouisfed.org/series/DCOILWTICO",
        "grain_role": "energy and ethanol-economics context",
    },
    "ten_year_treasury_yield": {
        "series_id": "DGS10",
        "metric": "fred_10y_treasury_percent",
        "unit": "percent",
        "title": "10-Year Treasury Constant Maturity Rate",
        "underlying_provider": "Board of Governors of the Federal Reserve System",
        "release": "H.15 Selected Interest Rates",
        "source_url": "https://fred.stlouisfed.org/series/DGS10",
        "grain_role": "long-term interest-rate context",
    },
    "effective_federal_funds_rate": {
        "series_id": "DFF",
        "metric": "fred_effective_federal_funds_rate_percent",
        "unit": "percent",
        "title": "Effective Federal Funds Rate",
        "underlying_provider": "Board of Governors of the Federal Reserve System",
        "release": "H.15 Selected Interest Rates",
        "source_url": "https://fred.stlouisfed.org/series/DFF",
        "grain_role": "short-term monetary-policy context",
    },
}


def conservative_available_at(period: date) -> datetime:
    """Treat a daily observation as usable only after a seven-day buffer."""
    local = datetime.combine(
        period + timedelta(days=7),
        time(18, 0),
        tzinfo=NEW_YORK,
    )
    return local.astimezone(timezone.utc)


def _select_observation(
    content: str,
    *,
    series_id: str,
    as_of: datetime,
) -> tuple[date, float, datetime]:
    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames != ["observation_date", series_id]:
        raise OfficialDataError(
            f"FRED {series_id} CSV has unexpected columns"
        )
    selected: tuple[date, float, datetime] | None = None
    as_of_utc = as_of.astimezone(timezone.utc)
    for row in reader:
        value_text = row.get(series_id)
        if value_text in (None, "", "."):
            continue
        try:
            period = date.fromisoformat(row["observation_date"])
            value = float(value_text)
        except (KeyError, TypeError, ValueError) as exc:
            raise OfficialDataError(f"FRED {series_id} CSV row is invalid") from exc
        available_at = conservative_available_at(period)
        if available_at <= as_of_utc and (
            selected is None or period > selected[0]
        ):
            selected = (period, value, available_at)
    if selected is None:
        raise OfficialDataError(
            f"no conservatively available FRED {series_id} observation"
        )
    return selected


def load_grain_macro(
    *,
    as_of: datetime,
    session: requests.Session | None = None,
    retrieved_at: datetime | None = None,
    today: date | None = None,
) -> OfficialSnapshot:
    """Load current macro context and refuse non-vintage-safe historical runs."""
    current_date = today or date.today()
    if as_of.date() != current_date:
        raise OfficialDataError(
            "FRED public CSV is not vintage-safe for historical as-of runs; "
            "use a previously captured raw archive"
        )

    client = session or requests.Session()
    headers = {"User-Agent": "GrainAgents/1.0 research@example.invalid"}
    start_date = as_of.date() - timedelta(days=LOOKBACK_DAYS)
    payloads: dict[str, str] = {}
    selected: dict[str, tuple[date, float, datetime]] = {}
    for key, definition in SERIES.items():
        series_id = definition["series_id"]
        try:
            response = client.get(
                GRAPH_CSV_URL,
                params={
                    "id": series_id,
                    "cosd": start_date.isoformat(),
                    "coed": as_of.date().isoformat(),
                },
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            content = response.content.decode("utf-8-sig")
        except (requests.RequestException, UnicodeDecodeError) as exc:
            raise OfficialDataError(f"FRED {series_id} request failed: {exc}") from exc
        payloads[series_id] = content
        selected[key] = _select_observation(
            content,
            series_id=series_id,
            as_of=as_of,
        )

    retrieved = retrieved_at or datetime.now(timezone.utc)
    values: dict[str, float | str] = {}
    observations = []
    for key, definition in SERIES.items():
        period, value, available_at = selected[key]
        values[key] = value
        values[f"{key}_period"] = period.isoformat()
        observations.append(
            OfficialObservation(
                metric=definition["metric"],
                value=value,
                unit=definition["unit"],
                period=period.isoformat(),
                released_at=available_at,
                available_at=available_at,
                retrieved_at=retrieved,
                source_id=SOURCE_ID,
                source_url=definition["source_url"],
                vintage=retrieved.isoformat(),
                availability_policy=(
                    "current operational runs only; conservatively available "
                    "7 calendar days after observation at 6:00 p.m. "
                    "America/New_York"
                ),
                metadata={
                    "series_id": definition["series_id"],
                    "title": definition["title"],
                    "underlying_provider": definition["underlying_provider"],
                    "release": definition["release"],
                    "grain_role": definition["grain_role"],
                },
            )
        )

    raw_content = (
        json.dumps(
            {
                "retrieved_at": retrieved.isoformat(),
                "request_window": {
                    "start": start_date.isoformat(),
                    "end": as_of.date().isoformat(),
                },
                "series_csv": payloads,
            },
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    digest = hashlib.sha256(raw_content).hexdigest()
    section = {
        "status": "ready",
        "coverage_status": "partial",
        "report": "FRED official macro context",
        "scope": "currency, energy, and U.S. interest rates",
        "values": values,
        "missing": [
            "official_grain_news_events",
            "trade_policy_and_tariffs",
            "river_and_port_disruptions",
            "fertilizer_and_input_costs",
            "china_policy",
            "biofuel_mandates",
        ],
        "availability_policy": "current_run_only_conservative_plus_7_days",
    }
    source = {
        "source_id": SOURCE_ID,
        "provider": "Federal Reserve Bank of St. Louis",
        "dataset": "FRED official macro series",
        "source_url": "https://fred.stlouisfed.org/",
        "series_ids": [item["series_id"] for item in SERIES.values()],
        "underlying_providers": sorted(
            {item["underlying_provider"] for item in SERIES.values()}
        ),
        "retrieved_at": retrieved.isoformat(),
        "vintage": retrieved.isoformat(),
        "sha256": digest,
        "license_scope": "official_public_data",
        "api_key_mode": "not_required_public_csv",
    }
    return OfficialSnapshot(
        section_name="macro",
        section=section,
        observations=tuple(observations),
        source=source,
        archive_filename=f"fred_grain_macro_{current_date.isoformat()}.json",
        raw_content=raw_content,
    )


__all__ = [
    "SERIES",
    "conservative_available_at",
    "load_grain_macro",
]
