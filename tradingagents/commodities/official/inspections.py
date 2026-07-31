"""USDA AMS/FGIS weekly corn export-inspection evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

import requests

from .models import OfficialDataError, OfficialObservation, OfficialSnapshot

DATASET_ID = "sruw-w49i"
METADATA_URL = f"https://agtransport.usda.gov/api/views/{DATASET_ID}"
API_URL = f"https://agtransport.usda.gov/resource/{DATASET_ID}.json"
SOURCE_URL = (
    "https://agtransport.usda.gov/Exports/Grain-Inspections/sruw-w49i"
)
SOURCE_ID = "source_usda_ams_fgis_corn_inspections"


def _get_json(
    client: requests.Session,
    url: str,
    *,
    label: str,
    params: dict[str, object] | None = None,
) -> object:
    try:
        response = client.get(
            url,
            params=params,
            headers={
                "User-Agent": "GrainAgents/1.0 research@example.invalid"
            },
            timeout=60,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, TypeError, ValueError) as exc:
        raise OfficialDataError(
            f"USDA grain inspections {label} request failed: {exc}"
        ) from exc


def _metadata_available_at(payload: object) -> datetime:
    if not isinstance(payload, dict):
        raise OfficialDataError(
            "USDA grain inspections metadata response is invalid"
        )
    try:
        epoch = float(payload["rowsUpdatedAt"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OfficialDataError(
            "USDA grain inspections metadata has no update timestamp"
        ) from exc
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _parse_weekly_rows(
    payload: object,
) -> list[tuple[date, int, int]]:
    if not isinstance(payload, list):
        raise OfficialDataError(
            "USDA grain inspections response has no data list"
        )
    rows: list[tuple[date, int, int]] = []
    for row in payload:
        if not isinstance(row, dict):
            raise OfficialDataError(
                "USDA grain inspections response contains an invalid row"
            )
        try:
            period = datetime.fromisoformat(str(row["date"])).date()
            metric_tons = int(float(row["metric_tons"]))
            records = int(row["records"])
        except (KeyError, TypeError, ValueError) as exc:
            raise OfficialDataError(
                "USDA grain inspections response contains an invalid row"
            ) from exc
        rows.append((period, metric_tons, records))
    rows.sort(key=lambda item: item[0], reverse=True)
    if len(rows) < 4:
        raise OfficialDataError(
            "USDA grain inspections response has fewer than four corn weeks"
        )
    return rows


def _parse_total(payload: object) -> tuple[int, int]:
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise OfficialDataError(
            "USDA grain inspections market-year response is invalid"
        )
    try:
        return int(float(payload[0]["metric_tons"])), int(
            payload[0]["records"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise OfficialDataError(
            "USDA grain inspections market-year total is invalid"
        ) from exc


def _market_year_start(as_of: date) -> date:
    year = as_of.year if as_of.month >= 9 else as_of.year - 1
    return date(year, 9, 1)


def load_corn_export_inspections(
    *,
    as_of: datetime,
    session: requests.Session | None = None,
    retrieved_at: datetime | None = None,
    today: date | None = None,
) -> OfficialSnapshot:
    """Load the current inspection dataset with its exact update timestamp."""
    current_date = today or date.today()
    if as_of.date() != current_date:
        raise OfficialDataError(
            "USDA grain inspections current API is not vintage-safe for "
            "historical as-of runs; use a previously captured raw archive"
        )

    client = session or requests.Session()
    metadata = _get_json(
        client,
        METADATA_URL,
        label="metadata",
    )
    available_at = _metadata_available_at(metadata)
    if available_at > as_of.astimezone(timezone.utc):
        raise OfficialDataError(
            "latest USDA grain-inspection dataset update was not available "
            "as of the analysis timestamp"
        )

    weekly_params = {
        "$select": (
            "date,grain,sum(mt) as metric_tons,count(*) as records"
        ),
        "$where": "upper(grain)='CORN'",
        "$group": "date,grain",
        "$order": "date desc",
        "$limit": 12,
    }
    weekly_payload = _get_json(
        client,
        API_URL,
        label="weekly corn",
        params=weekly_params,
    )
    weekly_rows = _parse_weekly_rows(weekly_payload)
    period, weekly_inspections, weekly_records = weekly_rows[0]
    if period > as_of.date():
        raise OfficialDataError(
            "USDA grain inspections response includes a future week"
        )

    market_year_start = _market_year_start(as_of.date())
    market_year_params = {
        "$select": "sum(mt) as metric_tons,count(*) as records",
        "$where": (
            "upper(grain)='CORN' "
            f"AND cert_date >= '{market_year_start.isoformat()}T00:00:00' "
            f"AND cert_date <= '{period.isoformat()}T23:59:59'"
        ),
    }
    market_year_payload = _get_json(
        client,
        API_URL,
        label="market-year corn",
        params=market_year_params,
    )
    market_year_total, market_year_records = _parse_total(
        market_year_payload
    )

    previous_week = weekly_rows[1][1]
    four_week_average = round(
        sum(row[1] for row in weekly_rows[:4]) / 4
    )
    week_over_week_change_percent = round(
        ((weekly_inspections / previous_week) - 1) * 100,
        6,
    )
    values = {
        "week_ending": period.isoformat(),
        "weekly_inspections": weekly_inspections,
        "previous_week_inspections": previous_week,
        "four_week_average_inspections": four_week_average,
        "week_over_week_change_percent": week_over_week_change_percent,
        "market_year_to_date_inspections": market_year_total,
    }
    retrieved = retrieved_at or datetime.now(timezone.utc)
    observations = []
    for metric, value in values.items():
        if metric == "week_ending":
            continue
        unit = "percent" if metric.endswith("_percent") else "metric_tons"
        observations.append(
            OfficialObservation(
                metric=f"ams_corn_{metric}",
                value=value,
                unit=unit,
                period=period.isoformat(),
                released_at=available_at,
                available_at=available_at,
                retrieved_at=retrieved,
                source_id=SOURCE_ID,
                source_url=SOURCE_URL,
                vintage=retrieved.isoformat(),
                availability_policy=(
                    "current operational runs only; exact Socrata "
                    "rowsUpdatedAt timestamp"
                ),
                metadata={
                    "dataset_id": DATASET_ID,
                    "weekly_records": weekly_records,
                    "market_year_records": market_year_records,
                    "market_year_start": market_year_start.isoformat(),
                },
            )
        )

    archive = {
        "metadata": metadata,
        "weekly_query": weekly_params,
        "weekly_rows": weekly_payload,
        "market_year_query": market_year_params,
        "market_year_total": market_year_payload,
    }
    raw_content = (
        json.dumps(archive, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    digest = hashlib.sha256(raw_content).hexdigest()
    section = {
        "status": "ready",
        "commodity": "corn",
        "report": "USDA AMS/FGIS Grain Inspections",
        "scope": "U.S. corn inspected and/or weighed for export",
        "week_ending": period.isoformat(),
        "released_at": available_at.isoformat(),
        "market_year_start": market_year_start.isoformat(),
        "unit": "metric_tons",
        "values": values,
        "availability_policy": "current_run_only_exact_dataset_update",
    }
    source = {
        "source_id": SOURCE_ID,
        "provider": "USDA AMS/FGIS",
        "dataset": "Grain Inspections",
        "dataset_id": DATASET_ID,
        "source_url": SOURCE_URL,
        "retrieved_at": retrieved.isoformat(),
        "vintage": retrieved.isoformat(),
        "rows_updated_at": available_at.isoformat(),
        "sha256": digest,
        "license_scope": "official_public_data",
        "api_key_mode": "not_required",
    }
    return OfficialSnapshot(
        section_name="demand",
        section=section,
        observations=tuple(observations),
        source=source,
        archive_filename=(
            f"usda_ams_fgis_corn_inspections_{current_date.isoformat()}.json"
        ),
        raw_content=raw_content,
    )


__all__ = [
    "DATASET_ID",
    "load_corn_export_inspections",
]
