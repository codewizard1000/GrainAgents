"""EIA weekly fuel-ethanol demand proxy with current-run safety rules."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from .models import OfficialDataError, OfficialObservation, OfficialSnapshot

API_BASE = "https://api.eia.gov/v2/seriesid"
DOCUMENTATION_URL = "https://www.eia.gov/opendata/documentation.php"
SOURCE_ID = "source_eia_weekly_ethanol"
NEW_YORK = ZoneInfo("America/New_York")
SERIES = {
    "production": (
        "PET.W_EPOOXE_YOP_NUS_MBBLD.W",
        "thousand_barrels_per_day",
    ),
    "stocks": (
        "PET.W_EPOOXE_SAE_NUS_MBBL.W",
        "thousand_barrels",
    ),
}


def _redact_api_key(value: object, *, api_key: str) -> object:
    """Remove the credential from EIA response metadata before archival."""
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if key.lower() in {"api_key", "apikey"}
                else _redact_api_key(item, api_key=api_key)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_api_key(item, api_key=api_key) for item in value]
    if isinstance(value, str):
        return value.replace(api_key, "[REDACTED]")
    return value


def conservative_available_at(period: date) -> datetime:
    local = datetime.combine(
        period + timedelta(days=7),
        time(10, 30),
        tzinfo=NEW_YORK,
    )
    return local.astimezone(timezone.utc)


def _select_observation(
    rows: list[dict],
    *,
    as_of: datetime,
    metric: str,
) -> tuple[date, int | float, datetime, dict]:
    selected: tuple[date, int | float, datetime, dict] | None = None
    as_of_utc = as_of.astimezone(timezone.utc)
    for row in rows:
        try:
            period = date.fromisoformat(str(row["period"]))
            value = row["value"]
            if not isinstance(value, (int, float)):
                value = float(value)
        except (KeyError, TypeError, ValueError) as exc:
            raise OfficialDataError(f"EIA ethanol {metric} row is invalid") from exc
        available_at = conservative_available_at(period)
        if available_at <= as_of_utc and (
            selected is None or period > selected[0]
        ):
            selected = (period, value, available_at, row)
    if selected is None:
        raise OfficialDataError(
            f"no conservatively available EIA ethanol {metric} observation"
        )
    return selected


def load_corn_ethanol(
    *,
    as_of: datetime,
    session: requests.Session | None = None,
    retrieved_at: datetime | None = None,
    api_key: str | None = None,
    today: date | None = None,
) -> OfficialSnapshot:
    """Load current weekly ethanol observations.

    EIA's API is not a vintage database. Historical as-of calls are refused;
    their replay must use a raw archive captured by an earlier live run.
    """
    current_date = today or date.today()
    if as_of.date() != current_date:
        raise OfficialDataError(
            "EIA current API is not vintage-safe for historical as-of runs; "
            "use a previously captured raw archive"
        )
    key = api_key or os.getenv("EIA_API_KEY") or "DEMO_KEY"
    client = session or requests.Session()
    headers = {"User-Agent": "GrainAgents/1.0 research@example.invalid"}
    payloads: dict[str, dict] = {}
    selected: dict[str, tuple[date, int | float, datetime, dict]] = {}
    for metric, (series_id, _unit) in SERIES.items():
        try:
            response = client.get(
                f"{API_BASE}/{series_id}",
                params={"api_key": key, "length": 12},
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload["response"]["data"]
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            safe_error = str(_redact_api_key(str(exc), api_key=key))
            raise OfficialDataError(
                f"EIA ethanol {metric} request failed: {safe_error}"
            ) from exc
        if not isinstance(rows, list):
            raise OfficialDataError(f"EIA ethanol {metric} response has no data list")
        payloads[metric] = _redact_api_key(payload, api_key=key)
        selected[metric] = _select_observation(
            rows,
            as_of=as_of,
            metric=metric,
        )

    retrieved = retrieved_at or datetime.now(timezone.utc)
    values: dict[str, int | float | str] = {}
    observations = []
    for metric, (series_id, unit) in SERIES.items():
        period, value, available_at, row = selected[metric]
        values[metric] = value
        values[f"{metric}_period"] = period.isoformat()
        observations.append(
            OfficialObservation(
                metric=f"eia_us_fuel_ethanol_{metric}",
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
                    "current operational runs only; conservatively available "
                    "7 calendar days after week-ending period at 10:30 a.m. "
                    "America/New_York"
                ),
                metadata={
                    "series_id": series_id,
                    "series_description": row.get("series-description"),
                    "demand_role": "corn ethanol demand proxy",
                },
            )
        )
    raw_content = (
        json.dumps(payloads, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    digest = hashlib.sha256(raw_content).hexdigest()
    section = {
        "status": "ready",
        "commodity": "corn",
        "report": "EIA Weekly Petroleum Status Report",
        "scope": "U.S. fuel ethanol",
        "role": "corn ethanol demand proxy",
        "values": values,
        "availability_policy": "current_run_only_conservative_plus_7_days",
    }
    source = {
        "source_id": SOURCE_ID,
        "provider": "EIA",
        "dataset": "Weekly U.S. Fuel Ethanol Production and Stocks",
        "source_url": DOCUMENTATION_URL,
        "series_ids": [definition[0] for definition in SERIES.values()],
        "retrieved_at": retrieved.isoformat(),
        "vintage": retrieved.isoformat(),
        "sha256": digest,
        "license_scope": "official_public_data",
        "api_key_mode": "configured" if api_key or os.getenv("EIA_API_KEY") else "DEMO_KEY",
    }
    return OfficialSnapshot(
        section_name="demand",
        section=section,
        observations=tuple(observations),
        source=source,
        archive_filename=f"eia_ethanol_{current_date.isoformat()}.json",
        raw_content=raw_content,
    )


__all__ = [
    "SERIES",
    "conservative_available_at",
    "load_corn_ethanol",
]
