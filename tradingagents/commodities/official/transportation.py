"""USDA/USACE weekly downbound corn barge-traffic evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone

import requests

from .models import OfficialDataError, OfficialObservation, OfficialSnapshot

DATASET_ID = "n4pw-9ygw"
METADATA_URL = f"https://agtransport.usda.gov/api/views/{DATASET_ID}"
API_URL = f"https://agtransport.usda.gov/resource/{DATASET_ID}.json"
SOURCE_URL = (
    "https://agtransport.usda.gov/Barge/"
    "Downbound-Barge-Grain-Movements-Tons-/n4pw-9ygw"
)
METHODOLOGY_URL = (
    "https://www.ams.usda.gov/services/transportation-analysis/gtr-datasets"
)
SOURCE_ID = "source_usda_usace_corn_barge_movements"
GATEWAY_LOCKS = ("MS Locks 27", "OH Olmsted", "AK Lock 1")


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
            f"USDA corn barge {label} request failed: {exc}"
        ) from exc


def _metadata_available_at(payload: object) -> datetime:
    if not isinstance(payload, dict):
        raise OfficialDataError("USDA corn barge metadata response is invalid")
    try:
        epoch = float(payload["rowsUpdatedAt"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OfficialDataError(
            "USDA corn barge metadata has no update timestamp"
        ) from exc
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _parse_weekly_rows(payload: object) -> list[tuple[date, float, int]]:
    if not isinstance(payload, list):
        raise OfficialDataError("USDA corn barge response has no data list")
    rows: list[tuple[date, float, int]] = []
    for row in payload:
        if not isinstance(row, dict):
            raise OfficialDataError(
                "USDA corn barge response contains an invalid row"
            )
        try:
            period = datetime.fromisoformat(str(row["date"])).date()
            short_tons = float(row["short_tons"])
            records = int(row["records"])
        except (KeyError, TypeError, ValueError) as exc:
            raise OfficialDataError(
                "USDA corn barge response contains an invalid row"
            ) from exc
        if short_tons < 0:
            raise OfficialDataError("USDA corn barge response has negative tonnage")
        rows.append((period, short_tons, records))
    rows.sort(key=lambda item: item[0], reverse=True)
    if len(rows) < 53:
        raise OfficialDataError(
            "USDA corn barge response has fewer than 53 weekly observations"
        )
    if any(row[2] != len(GATEWAY_LOCKS) for row in rows[:4]):
        raise OfficialDataError(
            "USDA corn barge response has incomplete recent gateway coverage"
        )
    return rows


def _percent_change(current: float, comparison: float, *, label: str) -> float:
    if comparison <= 0:
        raise OfficialDataError(
            f"USDA corn barge {label} comparison is not positive"
        )
    return round(((current / comparison) - 1) * 100, 6)


def load_corn_barge_movements(
    *,
    as_of: datetime,
    session: requests.Session | None = None,
    retrieved_at: datetime | None = None,
    today: date | None = None,
) -> OfficialSnapshot:
    """Load current weekly corn traffic through three non-overlapping gateways."""
    current_date = today or date.today()
    if as_of.date() != current_date:
        raise OfficialDataError(
            "USDA corn barge current API is not vintage-safe for historical "
            "as-of runs; use a previously captured raw archive"
        )

    client = session or requests.Session()
    metadata = _get_json(client, METADATA_URL, label="metadata")
    available_at = _metadata_available_at(metadata)
    if available_at > as_of.astimezone(timezone.utc):
        raise OfficialDataError(
            "latest USDA corn barge dataset update was not available as of "
            "the analysis timestamp"
        )

    lock_filter = ",".join(f"'{lock}'" for lock in GATEWAY_LOCKS)
    query = {
        "$select": "date,sum(tons) as short_tons,count(*) as records",
        "$where": f"commodity='Corn' AND lock in({lock_filter})",
        "$group": "date",
        "$order": "date desc",
        "$limit": 160,
    }
    payload = _get_json(
        client,
        API_URL,
        label="weekly traffic",
        params=query,
    )
    rows = _parse_weekly_rows(payload)
    period, weekly_tons, weekly_records = rows[0]
    if period > as_of.date():
        raise OfficialDataError("USDA corn barge response includes a future week")

    previous_week_tons = rows[1][1]
    four_week_average = sum(row[1] for row in rows[:4]) / 4
    prior_year_period = period - timedelta(days=364)
    prior_year_matches = [row for row in rows if row[0] == prior_year_period]
    if len(prior_year_matches) != 1:
        raise OfficialDataError(
            "USDA corn barge response lacks the exact prior-year comparison week"
        )
    prior_year_tons = prior_year_matches[0][1]
    values = {
        "week_ending": period.isoformat(),
        "weekly_downbound_barge_tons": weekly_tons,
        "previous_week_downbound_barge_tons": previous_week_tons,
        "four_week_average_downbound_barge_tons": round(four_week_average, 3),
        "same_week_prior_year_downbound_barge_tons": prior_year_tons,
        "week_over_week_change_percent": _percent_change(
            weekly_tons,
            previous_week_tons,
            label="week-over-week",
        ),
        "vs_four_week_average_percent": _percent_change(
            weekly_tons,
            four_week_average,
            label="four-week-average",
        ),
        "year_over_year_change_percent": _percent_change(
            weekly_tons,
            prior_year_tons,
            label="year-over-year",
        ),
    }
    retrieved = retrieved_at or datetime.now(timezone.utc)
    observations = tuple(
        OfficialObservation(
            metric=f"ams_corn_{metric}",
            value=value,
            unit="percent" if metric.endswith("_percent") else "short_tons",
            period=period.isoformat(),
            released_at=available_at,
            available_at=available_at,
            retrieved_at=retrieved,
            source_id=SOURCE_ID,
            source_url=SOURCE_URL,
            vintage=retrieved.isoformat(),
            availability_policy=(
                "current operational runs only; exact Socrata rowsUpdatedAt "
                "timestamp"
            ),
            metadata={
                "dataset_id": DATASET_ID,
                "gateway_locks": list(GATEWAY_LOCKS),
                "gateway_record_count": weekly_records,
                "aggregation_scope": (
                    "MS Locks 27 plus OH Olmsted plus AK Lock 1; selected to "
                    "avoid double-counting sequential Mississippi locks"
                ),
            },
        )
        for metric, value in values.items()
        if metric != "week_ending"
    )
    archive = {
        "metadata": metadata,
        "weekly_query": query,
        "weekly_rows": payload,
    }
    raw_content = (
        json.dumps(archive, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    section = {
        "status": "ready",
        "coverage_status": "partial",
        "commodity": "corn",
        "report": "USDA/USACE Downbound Barge Grain Movements",
        "scope": (
            "Weekly corn short tons through Mississippi Locks 27, Ohio "
            "Olmsted, and Arkansas Lock 1"
        ),
        "week_ending": period.isoformat(),
        "released_at": available_at.isoformat(),
        "unit": "short_tons",
        "values": values,
        "covered_topics": ["mississippi_system_corn_barge_movements"],
        "missing": [
            "active_lock_closure_notices",
            "river_stage_restrictions",
            "port_congestion_and_closures",
            "rail_service_disruptions",
        ],
        "availability_policy": "current_run_only_exact_dataset_update",
        "interpretation_limit": (
            "Traffic volume is a logistics-flow indicator, not direct evidence "
            "of a closure, delay, freight rate, or directional price effect."
        ),
    }
    source = {
        "source_id": SOURCE_ID,
        "provider": "USDA AMS / U.S. Army Corps of Engineers",
        "dataset": "Downbound Barge Grain Movements (Tons)",
        "dataset_id": DATASET_ID,
        "source_url": SOURCE_URL,
        "methodology_url": METHODOLOGY_URL,
        "retrieved_at": retrieved.isoformat(),
        "vintage": retrieved.isoformat(),
        "rows_updated_at": available_at.isoformat(),
        "sha256": hashlib.sha256(raw_content).hexdigest(),
        "license_scope": "official_public_data",
        "license_note": (
            "USDA GTR describes its underlying series as aggregated from "
            "non-confidential and non-copyrighted sources; the Socrata dataset "
            "page does not specify a separate license."
        ),
        "api_key_mode": "not_required",
    }
    return OfficialSnapshot(
        section_name="transportation",
        section=section,
        observations=observations,
        source=source,
        archive_filename=(
            f"usda_usace_corn_barge_movements_{current_date.isoformat()}.json"
        ),
        raw_content=raw_content,
    )


__all__ = ["DATASET_ID", "GATEWAY_LOCKS", "load_corn_barge_movements"]
