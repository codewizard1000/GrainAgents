"""USDA FAS weekly export-sales evidence for current operational runs."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import requests

from .models import OfficialDataError, OfficialObservation, OfficialSnapshot

API_BASE = "https://apps.fas.usda.gov/OpenData/api/esr"
DOCUMENTATION_URL = "https://apps.fas.usda.gov/opendata/swagger/ui/index"
SOURCE_ID = "source_usda_fas_esr_corn"
COMMODITY_CODE = 401
NEW_YORK = ZoneInfo("America/New_York")
METRICS = {
    "weeklyExports": "weekly_exports",
    "accumulatedExports": "accumulated_exports",
    "outstandingSales": "outstanding_sales",
    "grossNewSales": "gross_new_sales",
    "currentMYNetSales": "current_my_net_sales",
    "currentMYTotalCommitment": "current_my_total_commitment",
    "nextMYOutstandingSales": "next_my_outstanding_sales",
    "nextMYNetSales": "next_my_net_sales",
}


def _api_key(explicit: str | None) -> str:
    key = (
        explicit
        or os.getenv("USDA_FAS_API_KEY")
        or os.getenv("DATA_GOV_API_KEY")
    )
    if not key:
        raise OfficialDataError(
            "USDA FAS export sales requires USDA_FAS_API_KEY from api.data.gov"
        )
    return key


def _rows(payload: object, *, label: str) -> list[dict]:
    if isinstance(payload, dict):
        for key in ("data", "results", "value"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                payload = candidate
                break
    if not isinstance(payload, list) or not all(
        isinstance(item, dict) for item in payload
    ):
        raise OfficialDataError(f"USDA FAS {label} response has no data list")
    return payload


def _parse_date(value: object, *, label: str) -> date:
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError as exc:
        raise OfficialDataError(f"USDA FAS {label} date is invalid") from exc


def _release_available_at(value: object) -> datetime:
    release_date = _parse_date(value, label="release")
    local = datetime.combine(release_date, time(8, 30), tzinfo=NEW_YORK)
    return local.astimezone(timezone.utc)


def _current_corn_market_year(as_of: date) -> int:
    """Return FAS's ending-year identifier for the active Sep-Aug corn year."""
    return as_of.year + 1 if as_of.month >= 9 else as_of.year


def _target_market_year(crop_year: str) -> int:
    try:
        start_year = int(crop_year.split("/", 1)[0])
    except (AttributeError, TypeError, ValueError) as exc:
        raise OfficialDataError(f"invalid corn crop year {crop_year!r}") from exc
    return start_year + 1


def _numeric(value: object, *, metric: str) -> int | float:
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError) as exc:
        raise OfficialDataError(
            f"USDA FAS export row has invalid {metric}"
        ) from exc
    return int(number) if number.is_integer() else number


def _get_json(
    client: requests.Session,
    url: str,
    *,
    api_key: str,
    label: str,
) -> object:
    try:
        response = client.get(
            url,
            headers={
                "API_KEY": api_key,
                "User-Agent": "GrainAgents/1.0 research@example.invalid",
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, TypeError, ValueError) as exc:
        safe_error = str(exc).replace(api_key, "[REDACTED]")
        raise OfficialDataError(
            f"USDA FAS {label} request failed: {safe_error}"
        ) from exc


def _release_record(
    rows: list[dict],
    *,
    market_year: int,
) -> tuple[dict, datetime]:
    matches: list[tuple[dict, datetime]] = []
    for row in rows:
        try:
            commodity_code = int(row["commodityCode"])
            row_market_year = int(row["marketYear"])
            available_at = _release_available_at(row["releaseTimeStamp"])
        except (KeyError, TypeError, ValueError):
            continue
        if commodity_code == COMMODITY_CODE and row_market_year == market_year:
            matches.append((row, available_at))
    if not matches:
        raise OfficialDataError(
            f"USDA FAS has no corn release record for market year {market_year}"
        )
    return max(matches, key=lambda item: item[1])


def _aggregate_latest_week(
    rows: list[dict],
) -> tuple[date, int, dict[str, int | float], int]:
    dated: list[tuple[date, dict]] = []
    for row in rows:
        try:
            if int(row["commodityCode"]) != COMMODITY_CODE:
                continue
            period = _parse_date(row["weekEndingDate"], label="week-ending")
        except (KeyError, TypeError, ValueError):
            continue
        dated.append((period, row))
    if not dated:
        raise OfficialDataError("USDA FAS corn export response has no valid rows")

    period = max(item[0] for item in dated)
    selected = [row for row_period, row in dated if row_period == period]
    unit_ids: set[int] = set()
    totals = dict.fromkeys(METRICS.values(), 0)
    for row in selected:
        try:
            unit_ids.add(int(row["unitId"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise OfficialDataError(
                "USDA FAS export row has invalid unitId"
            ) from exc
        for api_name, normalized in METRICS.items():
            try:
                totals[normalized] += _numeric(
                    row[api_name],
                    metric=api_name,
                )
            except KeyError as exc:
                raise OfficialDataError(
                    f"USDA FAS export row is missing {api_name}"
                ) from exc
    if len(unit_ids) != 1:
        raise OfficialDataError(
            "USDA FAS latest corn week contains inconsistent units"
        )
    return period, unit_ids.pop(), totals, len(selected)


def load_corn_export_sales(
    *,
    as_of: datetime,
    crop_year: str,
    session: requests.Session | None = None,
    retrieved_at: datetime | None = None,
    api_key: str | None = None,
    today: date | None = None,
) -> OfficialSnapshot:
    """Load current FAS export sales without pretending the API is vintage-safe."""
    current_date = today or date.today()
    if as_of.date() != current_date:
        raise OfficialDataError(
            "USDA FAS current API is not vintage-safe for historical as-of "
            "runs; use a previously captured raw archive"
        )

    key = _api_key(api_key)
    client = session or requests.Session()
    market_year = _current_corn_market_year(as_of.date())
    target_market_year = _target_market_year(crop_year)
    if target_market_year not in {market_year, market_year + 1}:
        raise OfficialDataError(
            f"corn crop year {crop_year} is not the current or next FAS "
            f"market year as of {as_of.date().isoformat()}"
        )

    release_payload = _get_json(
        client,
        f"{API_BASE}/datareleasedates",
        api_key=key,
        label="release calendar",
    )
    release_row, available_at = _release_record(
        _rows(release_payload, label="release calendar"),
        market_year=market_year,
    )
    if available_at > as_of.astimezone(timezone.utc):
        raise OfficialDataError(
            "latest USDA FAS corn release was not yet available as of the "
            "analysis timestamp"
        )

    exports_payload = _get_json(
        client,
        (
            f"{API_BASE}/exports/commodityCode/{COMMODITY_CODE}"
            f"/allCountries/marketYear/{market_year}"
        ),
        api_key=key,
        label="corn export sales",
    )
    period, unit_id, values, country_count = _aggregate_latest_week(
        _rows(exports_payload, label="corn export sales")
    )
    role = (
        "current_marketing_year"
        if target_market_year == market_year
        else "next_marketing_year"
    )
    target_value_key = (
        "current_my_total_commitment"
        if role == "current_marketing_year"
        else "next_my_outstanding_sales"
    )
    values["target_marketing_year_commitment"] = values[target_value_key]
    values["week_ending"] = period.isoformat()

    retrieved = retrieved_at or datetime.now(timezone.utc)
    unit = "metric_tons" if unit_id == 1 else f"fas_unit_id_{unit_id}"
    observations = []
    for metric, value in values.items():
        if metric == "week_ending":
            continue
        observations.append(
            OfficialObservation(
                metric=f"fas_corn_{metric}",
                value=value,
                unit=unit,
                period=period.isoformat(),
                released_at=available_at,
                available_at=available_at,
                retrieved_at=retrieved,
                source_id=SOURCE_ID,
                source_url=DOCUMENTATION_URL,
                vintage=retrieved.isoformat(),
                availability_policy=(
                    "current operational runs only; exact FAS release-calendar "
                    "date at 8:30 a.m. America/New_York"
                ),
                metadata={
                    "commodity_code": COMMODITY_CODE,
                    "market_year": market_year,
                    "target_market_year": target_market_year,
                    "target_role": role,
                    "country_rows": country_count,
                    "unit_id": unit_id,
                },
            )
        )

    archive = {
        "release_calendar": release_payload,
        "exports": exports_payload,
    }
    raw_content = (
        json.dumps(archive, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    digest = hashlib.sha256(raw_content).hexdigest()
    section = {
        "status": "ready",
        "commodity": "corn",
        "report": "USDA FAS Weekly Export Sales",
        "scope": "all reported destination countries",
        "crop_year": crop_year,
        "market_year": market_year,
        "target_market_year": target_market_year,
        "target_role": role,
        "week_ending": period.isoformat(),
        "released_at": available_at.isoformat(),
        "unit": unit,
        "values": values,
        "country_rows": country_count,
        "availability_policy": "current_run_only_exact_release_calendar",
    }
    source = {
        "source_id": SOURCE_ID,
        "provider": "USDA FAS",
        "dataset": "U.S. Weekly Export Sales of Agricultural Commodities",
        "source_url": DOCUMENTATION_URL,
        "commodity_code": COMMODITY_CODE,
        "market_year": market_year,
        "retrieved_at": retrieved.isoformat(),
        "vintage": retrieved.isoformat(),
        "release_timestamp": release_row["releaseTimeStamp"],
        "sha256": digest,
        "license_scope": "official_public_data",
        "api_key_mode": "configured",
    }
    return OfficialSnapshot(
        section_name="demand",
        section=section,
        observations=tuple(observations),
        source=source,
        archive_filename=f"usda_fas_esr_corn_{current_date.isoformat()}.json",
        raw_content=raw_content,
    )


__all__ = [
    "COMMODITY_CODE",
    "METRICS",
    "load_corn_export_sales",
]
