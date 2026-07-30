"""CFTC Legacy Futures Only point-in-time adapter for corn positioning."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from .models import OfficialDataError, OfficialObservation, OfficialSnapshot

DATASET_ID = "6dca-aqww"
DATA_URL = f"https://publicreporting.cftc.gov/resource/{DATASET_ID}.json"
DATASET_URL = (
    "https://publicreporting.cftc.gov/Commitments-of-Traders/"
    f"Legacy-Futures-Only/{DATASET_ID}"
)
SOURCE_ID = "source_cftc_cot_corn"
NEW_YORK = ZoneInfo("America/New_York")
SHUTDOWN_START = date(2025, 9, 30)
SHUTDOWN_END = date(2026, 1, 20)


def conservative_available_at(report_date: date) -> datetime | None:
    """Return a safe availability time, refusing the 2025 shutdown backlog."""
    if SHUTDOWN_START <= report_date <= SHUTDOWN_END:
        return None
    local = datetime.combine(
        report_date + timedelta(days=7),
        time(15, 30),
        tzinfo=NEW_YORK,
    )
    return local.astimezone(timezone.utc)


def _integer(row: dict[str, str], field: str) -> int:
    try:
        return int(row[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise OfficialDataError(f"CFTC COT row has invalid {field}") from exc


def parse_corn_cot(
    rows: list[dict[str, str]],
    *,
    as_of: datetime,
    retrieved_at: datetime,
) -> tuple[dict[str, int | str], tuple[OfficialObservation, ...], datetime]:
    selected: tuple[dict[str, str], date, datetime] | None = None
    as_of_utc = as_of.astimezone(timezone.utc)
    for row in rows:
        try:
            report_date = date.fromisoformat(row["report_date_as_yyyy_mm_dd"][:10])
        except (KeyError, ValueError) as exc:
            raise OfficialDataError("CFTC COT row has an invalid report date") from exc
        available_at = conservative_available_at(report_date)
        if (
            available_at is not None
            and available_at <= as_of_utc
            and (selected is None or report_date > selected[1])
        ):
            selected = (row, report_date, available_at)
    if selected is None:
        raise OfficialDataError(
            "no point-in-time-safe CFTC corn COT report was available; "
            "shutdown-backlog releases require an exact publication calendar"
        )

    row, report_date, available_at = selected
    values: dict[str, int | str] = {
        "market": row.get("market_and_exchange_names", ""),
        "report_date": report_date.isoformat(),
        "open_interest": _integer(row, "open_interest_all"),
        "noncommercial_long": _integer(row, "noncomm_positions_long_all"),
        "noncommercial_short": _integer(row, "noncomm_positions_short_all"),
        "noncommercial_spreading": _integer(row, "noncomm_postions_spread_all"),
        "commercial_long": _integer(row, "comm_positions_long_all"),
        "commercial_short": _integer(row, "comm_positions_short_all"),
    }
    values["noncommercial_net"] = int(values["noncommercial_long"]) - int(
        values["noncommercial_short"]
    )
    values["commercial_net"] = int(values["commercial_long"]) - int(
        values["commercial_short"]
    )
    observations = []
    for metric, value in values.items():
        if metric in {"market", "report_date"}:
            continue
        observations.append(
            OfficialObservation(
                metric=f"cftc_corn_{metric}",
                value=value,
                unit="contracts",
                period=report_date.isoformat(),
                released_at=available_at,
                available_at=available_at,
                retrieved_at=retrieved_at,
                source_id=SOURCE_ID,
                source_url=DATASET_URL,
                vintage=report_date.isoformat(),
                availability_policy=(
                    "conservative report-date plus 7 calendar days at 3:30 p.m. "
                    "America/New_York; 2025 shutdown backlog refused"
                ),
                metadata={
                    "market_scope": "all corn futures contract months",
                    "market": values["market"],
                    "report_date": report_date.isoformat(),
                },
            )
        )
    return values, tuple(observations), available_at


def load_corn_cot(
    *,
    as_of: datetime,
    session: requests.Session | None = None,
    retrieved_at: datetime | None = None,
) -> OfficialSnapshot:
    client = session or requests.Session()
    params = {
        "$where": "market_and_exchange_names like 'CORN%'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": "80",
    }
    headers = {"User-Agent": "GrainAgents/1.0 research@example.invalid"}
    try:
        response = client.get(DATA_URL, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        rows = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise OfficialDataError(f"CFTC COT request failed: {exc}") from exc
    if not isinstance(rows, list):
        raise OfficialDataError("CFTC COT response was not a list")
    retrieved = retrieved_at or datetime.now(timezone.utc)
    values, observations, available_at = parse_corn_cot(
        rows,
        as_of=as_of,
        retrieved_at=retrieved,
    )
    raw_content = json.dumps(rows, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    digest = hashlib.sha256(raw_content).hexdigest()
    report_date = str(values["report_date"])
    section = {
        "status": "ready",
        "commodity": "corn",
        "report": "CFTC Legacy Futures Only",
        "market_scope": "all corn futures contract months",
        "report_date": report_date,
        "available_at": available_at.isoformat(),
        "values": values,
    }
    source = {
        "source_id": SOURCE_ID,
        "provider": "CFTC",
        "dataset": f"Legacy Futures Only ({DATASET_ID})",
        "source_url": DATASET_URL,
        "api_url": DATA_URL,
        "report_date": report_date,
        "available_at": available_at.isoformat(),
        "retrieved_at": retrieved.isoformat(),
        "vintage": report_date,
        "sha256": digest,
        "license_scope": "official_public_data",
    }
    return OfficialSnapshot(
        section_name="positioning",
        section=section,
        observations=observations,
        source=source,
        archive_filename=f"cftc_cot_corn_{report_date}.json",
        raw_content=raw_content,
    )


__all__ = [
    "DATASET_ID",
    "conservative_available_at",
    "load_corn_cot",
    "parse_corn_cot",
]
