"""Federal Register grain-policy events for current operational runs."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from .models import OfficialDataError, OfficialObservation, OfficialSnapshot

API_URL = "https://www.federalregister.gov/api/v1/documents.json"
DOCUMENTATION_URL = "https://www.federalregister.gov/developers/documentation/api/v1"
SOURCE_ID = "source_federal_register_grain_events"
NEW_YORK = ZoneInfo("America/New_York")
LOOKBACK_DAYS = 60

EVENT_QUERIES = (
    ("ethanol market access", "trade_and_biofuel_policy"),
    ("renewable fuel standard", "biofuel_policy"),
    ("renewable volume obligations", "biofuel_policy"),
    ("small refinery exemptions", "biofuel_policy"),
    ("national grain car council", "grain_transportation"),
    ("phosphate fertilizer", "fertilizer_inputs"),
    ("grain inspection advisory committee", "grain_regulation"),
)


def conservative_available_at(publication_date: date) -> datetime:
    """Delay a published issue until 6 a.m. New York time the next day."""
    local = datetime.combine(
        publication_date + timedelta(days=1),
        time(6, 0),
        tzinfo=NEW_YORK,
    )
    return local.astimezone(timezone.utc)


def _title_matches(title: str, query: str) -> bool:
    normalized = re.sub(r"[^a-z0-9 ]", " ", title.lower())
    words = normalized.split()
    return all(
        any(candidate.rstrip("s") == term.rstrip("s") for candidate in words)
        for term in query.split()
    )


def _parse_documents(
    payloads: dict[str, dict],
    *,
    as_of: datetime,
) -> list[dict]:
    selected: dict[str, dict] = {}
    as_of_utc = as_of.astimezone(timezone.utc)
    for query, category in EVENT_QUERIES:
        payload = payloads.get(query)
        if not isinstance(payload, dict) or not isinstance(
            payload.get("results"), list
        ):
            raise OfficialDataError(
                f"Federal Register response for {query!r} has no results list"
            )
        for document in payload["results"]:
            try:
                document_number = str(document["document_number"])
                title = str(document["title"])
                publication = date.fromisoformat(document["publication_date"])
                html_url = str(document["html_url"])
                pdf_url = str(document["pdf_url"])
            except (KeyError, TypeError, ValueError) as exc:
                raise OfficialDataError(
                    f"Federal Register result for {query!r} is invalid"
                ) from exc
            available_at = conservative_available_at(publication)
            if available_at > as_of_utc or not _title_matches(title, query):
                continue
            agencies = sorted(
                {
                    str(agency.get("name") or agency.get("raw_name"))
                    for agency in document.get("agencies", [])
                    if agency.get("name") or agency.get("raw_name")
                }
            )
            existing = selected.get(document_number)
            if existing is None:
                selected[document_number] = {
                    "document_number": document_number,
                    "title": title,
                    "publication_date": publication.isoformat(),
                    "available_at": available_at.isoformat(),
                    "document_type": str(document.get("type") or "unknown"),
                    "agencies": agencies,
                    "html_url": html_url,
                    "official_pdf_url": pdf_url,
                    "abstract": document.get("abstract"),
                    "categories": [category],
                    "matched_queries": [query],
                    "metric": (
                        "federal_register_grain_event_"
                        + document_number.lower().replace("-", "_")
                    ),
                }
            else:
                existing["categories"] = sorted(
                    {*existing["categories"], category}
                )
                existing["matched_queries"] = sorted(
                    {*existing["matched_queries"], query}
                )
    return sorted(
        selected.values(),
        key=lambda item: (item["publication_date"], item["document_number"]),
        reverse=True,
    )


def load_grain_regulatory_events(
    *,
    as_of: datetime,
    session: requests.Session | None = None,
    retrieved_at: datetime | None = None,
    today: date | None = None,
) -> OfficialSnapshot:
    """Load tightly filtered published regulatory events from a public API."""
    current_date = today or date.today()
    if as_of.date() != current_date:
        raise OfficialDataError(
            "Federal Register current search is not replay-safe for historical "
            "as-of runs; use a previously captured raw archive"
        )

    client = session or requests.Session()
    headers = {"User-Agent": "GrainAgents/1.0 research@example.invalid"}
    start_date = as_of.date() - timedelta(days=LOOKBACK_DAYS)
    payloads: dict[str, dict] = {}
    for query, _category in EVENT_QUERIES:
        try:
            response = client.get(
                API_URL,
                params={
                    "per_page": 20,
                    "order": "newest",
                    "conditions[publication_date][gte]": start_date.isoformat(),
                    "conditions[publication_date][lte]": as_of.date().isoformat(),
                    "conditions[term]": query,
                },
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise OfficialDataError(
                f"Federal Register request for {query!r} failed: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise OfficialDataError(
                f"Federal Register response for {query!r} was not an object"
            )
        payloads[query] = payload

    documents = _parse_documents(payloads, as_of=as_of)
    retrieved = retrieved_at or datetime.now(timezone.utc)
    observations = tuple(
        OfficialObservation(
            metric=document["metric"],
            value=document["title"],
            unit="published_regulatory_event",
            period=document["publication_date"],
            released_at=datetime.fromisoformat(document["available_at"]),
            available_at=datetime.fromisoformat(document["available_at"]),
            retrieved_at=retrieved,
            source_id=SOURCE_ID,
            source_url=document["official_pdf_url"],
            vintage=document["document_number"],
            availability_policy=(
                "current operational runs only; conservatively available at "
                "6:00 a.m. America/New_York on the day after publication"
            ),
            metadata={
                "document_number": document["document_number"],
                "document_type": document["document_type"],
                "agencies": document["agencies"],
                "categories": document["categories"],
                "matched_queries": document["matched_queries"],
                "informational_html_url": document["html_url"],
                "official_pdf_url": document["official_pdf_url"],
            },
        )
        for document in documents
    )
    raw_content = (
        json.dumps(
            {
                "retrieved_at": retrieved.isoformat(),
                "request_window": {
                    "start": start_date.isoformat(),
                    "end": as_of.date().isoformat(),
                },
                "query_payloads": payloads,
            },
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    digest = hashlib.sha256(raw_content).hexdigest()
    covered_topics = sorted(
        {
            category
            for document in documents
            for category in document["categories"]
        }
    )
    section = {
        "status": "ready",
        "coverage_status": "partial",
        "report": "Federal Register published grain-policy events",
        "lookback_start": start_date.isoformat(),
        "lookback_end": as_of.date().isoformat(),
        "documents": documents,
        "covered_topics": covered_topics,
        "missing": [
            "black_sea_shipping",
            "river_and_port_disruptions",
            "sanctions_outside_matched_regulatory_titles",
            "china_policy",
            "official_private_crop_estimates",
        ],
        "availability_policy": "current_run_only_next_day_6am_new_york",
        "relevance_policy": "exact_query_words_in_published_document_title",
    }
    source = {
        "source_id": SOURCE_ID,
        "provider": "Office of the Federal Register / GPO",
        "dataset": "Federal Register published documents API",
        "source_url": DOCUMENTATION_URL,
        "retrieved_at": retrieved.isoformat(),
        "vintage": retrieved.isoformat(),
        "sha256": digest,
        "license_scope": "official_public_data",
        "api_key_mode": "not_required",
        "official_edition_links": "govinfo PDF per document",
    }
    return OfficialSnapshot(
        section_name="macro_events",
        section=section,
        observations=observations,
        source=source,
        archive_filename=(
            f"federal_register_grain_events_{current_date.isoformat()}.json"
        ),
        raw_content=raw_content,
    )


__all__ = [
    "EVENT_QUERIES",
    "conservative_available_at",
    "load_grain_regulatory_events",
]
