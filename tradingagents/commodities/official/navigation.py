"""Current USACE navigation notices for the U.S. grain river network."""

from __future__ import annotations

import hashlib
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

from .models import OfficialDataError, OfficialObservation, OfficialSnapshot

DISTRICT_API_URL = (
    "https://ndc.ops.usace.army.mil/ords/ntni/json_data/"
    "notices_by_district/{district_code}"
)
NOTICE_URL = (
    "https://ndc.ops.usace.army.mil/ords/ntni/print_nav_notice?"
    "in_nav_notice_number={control_number}&in_title_formatting=UB"
)
DOCUMENTATION_URL = (
    "https://ndc.ops.usace.army.mil/ords/r/ntni/notices/data-web-services"
)
SOURCE_ID = "source_usace_grain_corridor_navigation_notices"
NEW_YORK = ZoneInfo("America/New_York")
UPCOMING_DAYS = 14

# Districts intersecting the primary inland grain routes. Missouri River
# districts remain explicit even when their active-notice response is empty.
GRAIN_CORRIDOR_DISTRICTS = {
    "MVP": "Mississippi River - St. Paul",
    "MVR": "Mississippi and Illinois Rivers - Rock Island",
    "MVS": "Mississippi River - St. Louis",
    "MVK": "Mississippi River - Vicksburg",
    "MVM": "Mississippi River - Memphis",
    "MVN": "Lower Mississippi River - New Orleans",
    "LRC": "Illinois Waterway - Chicago",
    "LRP": "Upper Ohio River - Pittsburgh",
    "LRH": "Ohio River - Huntington",
    "LRL": "Ohio River - Louisville",
    "LRN": "Tennessee and Cumberland Rivers - Nashville",
    "SWL": "McClellan-Kerr Arkansas River - Little Rock",
    "NWK": "Missouri River - Kansas City",
    "NWO": "Missouri River - Omaha",
}

_EFFECTIVE_RE = re.compile(
    r"<b>\s*EFFECTIVE:\s*</b>\s*(.*?)</td>",
    flags=re.IGNORECASE | re.DOTALL,
)
_ISSUE_DATE_RE = re.compile(
    r"<b>\s*DATE:\s*</b>.*?(\d{2}/\d{2}/\d{4})",
    flags=re.IGNORECASE | re.DOTALL,
)
_TITLE_RE = re.compile(
    r"<font[^>]*size\s*=\s*[\"']?\+1[\"']?[^>]*>"
    r"(.*?)(?:</font>|<font)",
    flags=re.IGNORECASE | re.DOTALL,
)
_DATE_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")


def conservative_available_at(issue_date: date) -> datetime:
    """Avoid assuming an intraday publication time not supplied by NTNI."""
    local = datetime.combine(
        issue_date + timedelta(days=1),
        time(6, 0),
        tzinfo=NEW_YORK,
    )
    return local.astimezone(timezone.utc)


def _clean_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(text).replace("\xa0", " ").split())


def _parse_detail(raw_html: str) -> dict[str, Any]:
    issue_match = _ISSUE_DATE_RE.search(raw_html)
    if issue_match is None:
        raise OfficialDataError("USACE navigation notice has no issue date")
    try:
        issue_date = datetime.strptime(issue_match.group(1), "%m/%d/%Y").date()
    except ValueError as exc:
        raise OfficialDataError(
            "USACE navigation notice has an invalid issue date"
        ) from exc
    title_match = _TITLE_RE.search(raw_html)
    title = _clean_html(title_match.group(1)) if title_match else ""
    plain_text = _clean_html(raw_html)
    effective_match = _EFFECTIVE_RE.search(raw_html)
    if effective_match is None:
        if "NAVIGATION POLICY NOTICE" in plain_text.upper():
            return {
                "title": title,
                "issue_date": issue_date,
                "policy_notice": True,
                "effective_text": None,
                "effective_start": None,
                "effective_end": None,
                "until_further_notice": False,
                "plain_text": plain_text,
            }
        raise OfficialDataError("USACE navigation notice has no effective period")
    effective_text = _clean_html(effective_match.group(1))
    dates = _DATE_RE.findall(effective_text)
    immediate = "immediately" in effective_text.lower()
    if not dates and not immediate:
        raise OfficialDataError("USACE navigation notice has no effective start date")
    try:
        if immediate:
            start_date = issue_date
            end_date = (
                datetime.strptime(dates[0], "%m/%d/%Y").date()
                if dates
                else None
            )
        else:
            start_date = datetime.strptime(dates[0], "%m/%d/%Y").date()
            end_date = (
                datetime.strptime(dates[1], "%m/%d/%Y").date()
                if len(dates) > 1
                else None
            )
    except ValueError as exc:
        raise OfficialDataError(
            "USACE navigation notice has an invalid effective date"
        ) from exc

    return {
        "title": title,
        "issue_date": issue_date,
        "policy_notice": False,
        "effective_text": effective_text,
        "effective_start": start_date,
        "effective_end": end_date,
        "until_further_notice": "until further notice" in effective_text.lower(),
        "plain_text": plain_text,
    }


def _classify_notice(title: str, plain_text: str) -> str:
    text = f"{title} {plain_text}".lower()
    if any(
        phrase in text
        for phrase in (
            "closure",
            "closed to navigation",
            "closed to all traffic",
            "out of service",
            "navigation suspended",
        )
    ):
        return "closure"
    if any(
        phrase in text
        for phrase in (
            "restriction",
            "restricted",
            "maximum draft",
            "maximum width",
            "daylight only",
            "one-way traffic",
            "one way traffic",
            "no wake",
        )
    ):
        return "restriction"
    if any(phrase in text for phrase in ("delay", "queue", "wait time")):
        return "delay"
    if "dredg" in text:
        return "dredging"
    if any(
        phrase in text
        for phrase in ("maintenance", "repair", "construction", "inspection")
    ):
        return "maintenance"
    return "other_navigation_notice"


def _get_json(client: requests.Session, url: str) -> dict[str, Any]:
    try:
        response = client.get(
            url,
            headers={"User-Agent": "GrainAgents/1.0 research@example.invalid"},
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise OfficialDataError(f"USACE navigation request failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise OfficialDataError("USACE district response was not an object")
    return payload


def _get_text(client: requests.Session, url: str) -> str:
    try:
        response = client.get(
            url,
            headers={"User-Agent": "GrainAgents/1.0 research@example.invalid"},
            timeout=45,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise OfficialDataError(f"USACE navigation request failed: {exc}") from exc
    return response.text


def _default_json_request(url: str) -> dict[str, Any]:
    with requests.Session() as client:
        return _get_json(client, url)


def _default_text_request(url: str) -> str:
    with requests.Session() as client:
        return _get_text(client, url)


def _load_district_payloads(
    session: requests.Session | None,
) -> dict[str, dict[str, Any]]:
    urls = {
        code: DISTRICT_API_URL.format(district_code=code)
        for code in GRAIN_CORRIDOR_DISTRICTS
    }
    if session is not None:
        return {code: _get_json(session, url) for code, url in urls.items()}
    with ThreadPoolExecutor(
        max_workers=7,
        thread_name_prefix="usace-districts",
    ) as executor:
        payloads = executor.map(_default_json_request, urls.values())
        return dict(zip(urls, payloads, strict=True))


def _validated_summaries(
    payloads: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    seen: set[int] = set()
    for district_code, payload in payloads.items():
        items = payload.get("items")
        if not isinstance(items, list):
            raise OfficialDataError(
                f"USACE {district_code} response has no items list"
            )
        if payload.get("hasMore") is not False:
            raise OfficialDataError(
                f"USACE {district_code} response is paginated or incomplete"
            )
        try:
            count = int(payload["count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise OfficialDataError(
                f"USACE {district_code} response has no valid count"
            ) from exc
        if count != len(items):
            raise OfficialDataError(
                f"USACE {district_code} count does not match returned items"
            )
        for item in items:
            if not isinstance(item, dict):
                raise OfficialDataError(
                    f"USACE {district_code} response contains an invalid item"
                )
            try:
                control_number = int(item["controlnumber"])
                notice_number = str(item["noticeno"])
                raw_issue_timestamp = item.get("issuedate")
                issue_timestamp = (
                    datetime.fromisoformat(
                        str(raw_issue_timestamp).replace("Z", "+00:00")
                    )
                    if raw_issue_timestamp
                    else None
                )
                raw_waterways = item.get("waterways")
                waterways = (
                    str(raw_waterways)
                    if raw_waterways
                    else GRAIN_CORRIDOR_DISTRICTS[district_code]
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise OfficialDataError(
                    f"USACE {district_code} response contains an invalid item"
                ) from exc
            if issue_timestamp is not None and issue_timestamp.tzinfo is None:
                raise OfficialDataError(
                    f"USACE {district_code} response contains a naive timestamp"
                )
            if control_number in seen:
                continue
            seen.add(control_number)
            summaries.append(
                {
                    "control_number": control_number,
                    "notice_number": notice_number,
                    "district_code": district_code,
                    "district_scope": GRAIN_CORRIDOR_DISTRICTS[district_code],
                    "api_issue_timestamp": (
                        issue_timestamp.isoformat() if issue_timestamp else None
                    ),
                    "waterways": waterways,
                    "notice_url": NOTICE_URL.format(control_number=control_number),
                }
            )
    return summaries


def _load_notice_details(
    summaries: list[dict[str, Any]],
    session: requests.Session | None,
) -> dict[int, tuple[str, dict[str, Any]]]:
    urls = [summary["notice_url"] for summary in summaries]
    if session is not None:
        raw_pages = [_get_text(session, url) for url in urls]
    else:
        with ThreadPoolExecutor(
            max_workers=10,
            thread_name_prefix="usace-notices",
        ) as executor:
            raw_pages = list(executor.map(_default_text_request, urls))
    details: dict[int, tuple[str, dict[str, Any]]] = {}
    for summary, raw_html in zip(summaries, raw_pages, strict=True):
        control_number = summary["control_number"]
        try:
            parsed = _parse_detail(raw_html)
        except OfficialDataError as exc:
            raise OfficialDataError(
                f"USACE notice {control_number} could not be parsed: {exc}"
            ) from exc
        details[control_number] = (raw_html, parsed)
    return details


def _select_notices(
    summaries: list[dict[str, Any]],
    details: dict[int, tuple[str, dict[str, Any]]],
    *,
    as_of: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active: list[dict[str, Any]] = []
    upcoming: list[dict[str, Any]] = []
    as_of_date = as_of.date()
    as_of_utc = as_of.astimezone(timezone.utc)
    upcoming_end = as_of_date + timedelta(days=UPCOMING_DAYS)
    for summary in summaries:
        _raw_html, detail = details[summary["control_number"]]
        if detail["policy_notice"]:
            continue
        available_at = conservative_available_at(detail["issue_date"])
        if available_at > as_of_utc:
            continue
        start_date = detail["effective_start"]
        end_date = detail["effective_end"]
        category = _classify_notice(detail["title"], detail["plain_text"])
        notice = {
            "control_number": summary["control_number"],
            "notice_number": summary["notice_number"],
            "district_code": summary["district_code"],
            "district_scope": summary["district_scope"],
            "issue_date": detail["issue_date"].isoformat(),
            "available_at": available_at.isoformat(),
            "waterways": summary["waterways"],
            "title": detail["title"]
            or f"USACE notice {summary['notice_number']}",
            "effective_start": start_date.isoformat(),
            "effective_end": end_date.isoformat() if end_date else None,
            "until_further_notice": detail["until_further_notice"],
            "effective_text": detail["effective_text"],
            "category": category,
            "notice_url": summary["notice_url"],
            "metric": f"usace_navigation_notice_{summary['control_number']}",
        }
        if start_date <= as_of_date and (
            end_date is None or end_date >= as_of_date
        ):
            active.append(notice)
        elif as_of_date < start_date <= upcoming_end:
            upcoming.append(notice)
    priority = {
        "closure": 0,
        "restriction": 1,
        "delay": 2,
        "dredging": 3,
        "maintenance": 4,
        "other_navigation_notice": 5,
    }
    def active_key(item: dict[str, Any]) -> tuple[int, int, int]:
        return (
            priority[item["category"]],
            -date.fromisoformat(item["issue_date"]).toordinal(),
            -item["control_number"],
        )

    def upcoming_key(item: dict[str, Any]) -> tuple[int, str, int]:
        return (
            priority[item["category"]],
            item["effective_start"],
            item["control_number"],
        )

    return sorted(active, key=active_key), sorted(upcoming, key=upcoming_key)


def load_usace_navigation_notices(
    *,
    as_of: datetime,
    session: requests.Session | None = None,
    retrieved_at: datetime | None = None,
    today: date | None = None,
) -> OfficialSnapshot:
    """Load active and near-term notices for defined inland grain corridors."""
    current_date = today or date.today()
    if as_of.date() != current_date:
        raise OfficialDataError(
            "USACE active navigation notices are not vintage-safe for historical "
            "as-of runs; use a previously captured raw archive"
        )

    payloads = _load_district_payloads(session)
    summaries = _validated_summaries(payloads)
    details = _load_notice_details(summaries, session)
    excluded_policy_notices = sum(
        detail["policy_notice"] for _raw_html, detail in details.values()
    )
    active, upcoming = _select_notices(
        summaries,
        details,
        as_of=as_of,
    )
    retrieved = retrieved_at or datetime.now(timezone.utc)
    aggregate_values = {
        "active_notice_count": len(active),
        "active_closure_count": sum(
            notice["category"] == "closure" for notice in active
        ),
        "active_restriction_count": sum(
            notice["category"] == "restriction" for notice in active
        ),
        "upcoming_14_day_notice_count": len(upcoming),
        "excluded_policy_notice_count": excluded_policy_notices,
    }
    aggregate_observations = tuple(
        OfficialObservation(
            metric=f"usace_grain_corridor_{metric}",
            value=value,
            unit="notices",
            period=as_of.date().isoformat(),
            released_at=as_of.astimezone(timezone.utc),
            available_at=as_of.astimezone(timezone.utc),
            retrieved_at=retrieved,
            source_id=SOURCE_ID,
            source_url=DOCUMENTATION_URL,
            vintage=retrieved.isoformat(),
            availability_policy=(
                "current operational runs only; current active district lists "
                "with notice availability delayed to 6:00 a.m. America/New_York "
                "on the day after issue"
            ),
            metadata={"district_codes": list(GRAIN_CORRIDOR_DISTRICTS)},
        )
        for metric, value in aggregate_values.items()
    )
    notice_observations = tuple(
        OfficialObservation(
            metric=notice["metric"],
            value=notice["title"],
            unit="official_navigation_notice",
            period=notice["issue_date"],
            released_at=datetime.fromisoformat(notice["available_at"]),
            available_at=datetime.fromisoformat(notice["available_at"]),
            retrieved_at=retrieved,
            source_id=SOURCE_ID,
            source_url=notice["notice_url"],
            vintage=str(notice["control_number"]),
            availability_policy=(
                "current operational runs only; conservatively available at "
                "6:00 a.m. America/New_York on the day after issue"
            ),
            metadata={
                key: value
                for key, value in notice.items()
                if key not in {"metric", "title", "notice_url"}
            },
        )
        for notice in (*active, *upcoming)
    )
    archive = {
        "retrieved_at": retrieved.isoformat(),
        "district_scope": GRAIN_CORRIDOR_DISTRICTS,
        "district_payloads": payloads,
        "notice_pages": {
            str(control_number): raw_html
            for control_number, (raw_html, _detail) in details.items()
        },
    }
    raw_content = (
        json.dumps(archive, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    section = {
        "status": "ready",
        "coverage_status": "partial",
        "report": "USACE Notices to Navigation Interests",
        "as_of_date": as_of.date().isoformat(),
        "upcoming_window_days": UPCOMING_DAYS,
        "districts": [
            {"code": code, "scope": scope}
            for code, scope in GRAIN_CORRIDOR_DISTRICTS.items()
        ],
        "values": aggregate_values,
        "active_notices": active,
        "upcoming_notices": upcoming,
        "covered_topics": [
            "active_lock_closure_notices",
            "river_and_channel_restriction_notices",
            "port_and_channel_closure_notices",
        ],
        "missing": [
            "port_congestion",
            "rail_service_disruptions",
            "non_usace_marine_notices",
        ],
        "availability_policy": "current_run_only_conservative_next_day",
        "interpretation_limit": (
            "Keyword categories summarize official notice text. A listed notice "
            "does not by itself quantify delay, freight cost, affected grain "
            "volume, or price direction."
        ),
    }
    source = {
        "source_id": SOURCE_ID,
        "provider": "U.S. Army Corps of Engineers",
        "dataset": "Notices to Navigation Interests - grain river districts",
        "source_url": DOCUMENTATION_URL,
        "retrieved_at": retrieved.isoformat(),
        "vintage": retrieved.isoformat(),
        "sha256": hashlib.sha256(raw_content).hexdigest(),
        "license_scope": "official_public_data",
        "license_note": (
            "The official public data-service page does not identify a separate "
            "dataset redistribution license."
        ),
        "api_key_mode": "not_required",
    }
    return OfficialSnapshot(
        section_name="transport_disruptions",
        section=section,
        observations=aggregate_observations + notice_observations,
        source=source,
        archive_filename=(
            f"usace_grain_navigation_notices_{current_date.isoformat()}.json"
        ),
        raw_content=raw_content,
    )


__all__ = [
    "GRAIN_CORRIDOR_DISTRICTS",
    "conservative_available_at",
    "load_usace_navigation_notices",
]
