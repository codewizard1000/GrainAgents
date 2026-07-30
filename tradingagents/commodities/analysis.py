"""Point-in-time market evidence assembly for the corn technical vertical slice."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

from .contracts import COMMODITY_SPECS, MONTH_CODES, ContractMetadata, resolve_contract
from .evidence import (
    EvidencePackage,
    EvidenceQuality,
    freeze_evidence_value,
)
from .providers import load_configured_contract_history
from .technical import calculate_technical_metrics

HistoryLoader = Callable[..., dict[str, Any]]
TECHNICAL_LOOKBACK_CALENDAR_DAYS = 450
CURVE_LOOKBACK_CALENDAR_DAYS = 14
CURVE_CONTRACT_COUNT = 5


@dataclass(frozen=True)
class TechnicalEvidenceRun:
    evidence: EvidencePackage
    primary_history: dict[str, Any]
    curve_histories: tuple[dict[str, Any], ...]


def latest_complete_market_date(as_of: date, *, today: date | None = None) -> date:
    """Choose the latest daily session expected to be complete in historical data."""
    current_date = today or date.today()
    if as_of > current_date:
        raise ValueError("as_of cannot be later than today's date")
    candidate = as_of - timedelta(days=1) if as_of == current_date else as_of
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def active_curve_contracts(
    primary: ContractMetadata,
    *,
    as_of: date,
    count: int = CURVE_CONTRACT_COUNT,
) -> tuple[ContractMetadata, ...]:
    """Resolve the active nearby delivery strip, retaining the primary contract."""
    spec = COMMODITY_SPECS[primary.root]
    candidates: list[ContractMetadata] = []
    for year in range(as_of.year, as_of.year + 4):
        for month_code in spec.delivery_months:
            month_number = MONTH_CODES[month_code][0]
            if year == as_of.year and month_number < as_of.month:
                continue
            contract = resolve_contract(
                f"{primary.root}{month_code}{str(year)[-2:]}",
                as_of=as_of,
                expected_commodity=primary.commodity,
                reject_expired=False,
            )
            if contract.last_trade_date >= as_of:
                candidates.append(contract)

    selected = candidates[:count]
    if all(item.symbol != primary.symbol for item in selected):
        selected.append(primary)
    return tuple(sorted(selected, key=lambda item: item.last_trade_date))


def _latest_curve_point(history: dict[str, Any]) -> dict[str, Any]:
    bars = sorted(history.get("bars", []), key=lambda item: item["date"])
    settled = [bar for bar in bars if bar.get("settlement") is not None]
    selected = settled[-1] if settled else bars[-1]
    price = (
        selected.get("settlement")
        if selected.get("settlement") is not None
        else selected.get("close")
    )
    return {
        "contract_symbol": history["requested_symbol"],
        "vendor_symbol": history["vendor_symbol"],
        "observation_date": selected["date"],
        "settlement": selected.get("settlement"),
        "close": selected.get("close"),
        "curve_price": price,
        "price_field": (
            "settlement" if selected.get("settlement") is not None else "close"
        ),
        "price_unit": history["price_unit"],
        "settlement_available_at": selected.get("settlement_available_at"),
    }


def _fact(
    fact_id: str,
    *,
    metric: str,
    value: Any,
    unit: str,
    observed_at: str,
    available_at: str | None,
    source_id: str,
) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "metric": metric,
        "value": value,
        "unit": unit,
        "observed_at": observed_at,
        "available_at": available_at,
        "source_id": source_id,
        "derivation": "deterministic",
    }


def _technical_facts(
    history: dict[str, Any],
    technical: dict[str, Any],
    curve_points: list[dict[str, Any]],
    curve_structure: dict[str, Any],
    instrument: ContractMetadata,
) -> list[dict[str, Any]]:
    symbol = history["requested_symbol"].lower()
    observed_at = technical["latest_bar_date"]
    latest_bar = sorted(history["bars"], key=lambda item: item["date"])[-1]
    open_interest_bar = next(
        (
            bar
            for bar in sorted(
                history["bars"],
                key=lambda item: item["date"],
                reverse=True,
            )
            if bar.get("open_interest") is not None
        ),
        None,
    )
    source_id = f"source_{history['provider']}_{symbol}"
    facts: list[dict[str, Any]] = []

    def add(
        suffix: str,
        metric: str,
        value: Any,
        unit: str,
        available_at: str | None = None,
        fact_observed_at: str | None = None,
    ) -> None:
        if value is None:
            return
        facts.append(
            _fact(
                f"fact_{symbol}_{suffix}",
                metric=metric,
                value=value,
                unit=unit,
                observed_at=fact_observed_at or observed_at,
                available_at=available_at,
                source_id=source_id,
            )
        )

    add(
        "settlement",
        "official_settlement",
        technical["latest"]["settlement"],
        history["price_unit"],
        latest_bar.get("settlement_available_at"),
    )
    add("close", "daily_close", technical["latest"]["close"], history["price_unit"])
    add("volume", "daily_bar_volume", technical["latest"]["volume"], "contracts")
    add(
        "open_interest",
        "open_interest",
        technical["latest"]["open_interest"],
        "contracts",
        (
            open_interest_bar.get("open_interest_available_at")
            if open_interest_bar is not None
            else None
        ),
        technical["latest"]["open_interest_date"],
    )
    for period, value in technical["returns_percent"].items():
        add(f"return_{period}d", f"{period}_trading_day_return", value, "percent")
    for period, value in technical["moving_averages"].items():
        add(f"sma_{period}", f"{period}_day_moving_average", value, history["price_unit"])
    for metric, value in technical["trend"].items():
        if metric == "classification":
            continue
        add(metric, metric, value, history["price_unit"])
    for metric, value in technical["participation"].items():
        add(metric, metric, value, "contracts")
    for metric, value in technical["momentum"].items():
        add(metric, metric, value, "index" if "macd" not in metric else history["price_unit"])
    for metric, value in technical["volatility"].items():
        unit = "percent" if "percent" in metric else history["price_unit"]
        add(metric, metric, value, unit)
    for metric, value in technical["levels"].items():
        add(metric, metric, value, history["price_unit"])

    for point in curve_points:
        if point["curve_price"] is None:
            continue
        curve_symbol = point["contract_symbol"].lower()
        facts.append(
            _fact(
                f"fact_curve_{curve_symbol}",
                metric="futures_curve_price",
                value=point["curve_price"],
                unit=point["price_unit"],
                observed_at=point["observation_date"],
                available_at=point["settlement_available_at"],
                source_id=f"source_{history['provider']}_{curve_symbol}",
            )
        )
    if curve_structure["front_to_back_spread"] is not None:
        facts.append(
            _fact(
                f"fact_curve_{history['requested_symbol'].lower()}_front_to_back",
                metric="front_to_back_calendar_spread",
                value=curve_structure["front_to_back_spread"],
                unit=history["price_unit"],
                observed_at=curve_structure["observation_date"],
                available_at=None,
                source_id=source_id,
            )
        )
    contract_source = "source_grainagents_contract_registry"
    for suffix, metric, value, unit in (
        (
            "first_notice_date",
            "first_notice_date",
            instrument.first_notice_date.isoformat(),
            "date",
        ),
        (
            "last_trade_date",
            "last_trade_date",
            instrument.last_trade_date.isoformat(),
            "date",
        ),
        (
            "days_to_first_notice",
            "days_to_first_notice",
            instrument.days_to_first_notice,
            "calendar_days",
        ),
        (
            "days_to_expiration",
            "days_to_expiration",
            instrument.days_to_expiration,
            "calendar_days",
        ),
    ):
        facts.append(
            _fact(
                f"fact_{symbol}_{suffix}",
                metric=metric,
                value=value,
                unit=unit,
                observed_at=observed_at,
                available_at=None,
                source_id=contract_source,
            )
        )
    return facts


def build_technical_evidence(
    base: EvidencePackage,
    *,
    history_loader: HistoryLoader = load_configured_contract_history,
    today: date | None = None,
) -> TechnicalEvidenceRun:
    """Load exact-contract history and populate immutable technical evidence."""
    as_of_date = base.as_of.date()
    market_end_date = latest_complete_market_date(as_of_date, today=today)
    primary_start = market_end_date - timedelta(
        days=TECHNICAL_LOOKBACK_CALENDAR_DAYS
    )
    primary_history = history_loader(
        contract_symbol=base.instrument.symbol,
        start_date=primary_start.isoformat(),
        end_date=market_end_date.isoformat(),
    )
    technical = calculate_technical_metrics(primary_history, as_of=as_of_date)

    curve_start = market_end_date - timedelta(days=CURVE_LOOKBACK_CALENDAR_DAYS)
    curve_contracts = active_curve_contracts(base.instrument, as_of=as_of_date)
    other_contracts = [
        contract
        for contract in curve_contracts
        if contract.symbol != base.instrument.symbol
    ]
    loaded_by_symbol = {base.instrument.symbol: primary_history}
    with ThreadPoolExecutor(
        max_workers=min(4, len(other_contracts)) or 1,
        thread_name_prefix="grain-curve",
    ) as executor:
        futures = {
            contract.symbol: executor.submit(
                history_loader,
                contract_symbol=contract.symbol,
                start_date=curve_start.isoformat(),
                end_date=market_end_date.isoformat(),
            )
            for contract in other_contracts
        }
        for symbol, future in futures.items():
            loaded_by_symbol[symbol] = future.result()

    curve_histories: list[dict[str, Any]] = []
    curve_points: list[dict[str, Any]] = []
    for contract in curve_contracts:
        history = loaded_by_symbol[contract.symbol]
        curve_histories.append(history)
        point = _latest_curve_point(history)
        point.update(
            {
                "delivery_month": contract.delivery_month_name,
                "delivery_year": contract.delivery_year,
                "days_to_expiration": contract.days_to_expiration,
            }
        )
        curve_points.append(point)

    curve_status = "ready" if len(curve_points) >= 3 else "blocked"
    priced_points = [point for point in curve_points if point["curve_price"] is not None]
    front_to_back = (
        round(
            float(priced_points[-1]["curve_price"])
            - float(priced_points[0]["curve_price"]),
            8,
        )
        if len(priced_points) >= 2
        else None
    )
    curve_structure = {
        "front_contract": (
            priced_points[0]["contract_symbol"] if priced_points else None
        ),
        "back_contract": (
            priced_points[-1]["contract_symbol"] if priced_points else None
        ),
        "front_to_back_spread": front_to_back,
        "classification": (
            "carry"
            if front_to_back is not None and front_to_back > 0
            else "inversion"
            if front_to_back is not None and front_to_back < 0
            else "flat"
            if front_to_back == 0
            else None
        ),
        "observation_date": (
            max(point["observation_date"] for point in priced_points)
            if priced_points
            else as_of_date.isoformat()
        ),
    }
    missing_market: list[str] = []
    if technical["quality"]["status"] != "ready":
        missing_market.extend(technical["quality"]["missing"])
    if curve_status != "ready":
        missing_market.append("futures_curve")

    latest_bar = sorted(primary_history["bars"], key=lambda item: item["date"])[-1]
    market = {
        "provider": primary_history["provider"],
        "dataset": primary_history["dataset"],
        "data_scope": primary_history["data_scope"],
        "license_scope": primary_history["license_scope"],
        "contract_symbol": primary_history["requested_symbol"],
        "vendor_symbol": primary_history["vendor_symbol"],
        "history_start": primary_history["start_date"],
        "history_end": primary_history["end_date"],
        "retrieved_at": primary_history["retrieved_at"],
        "price_unit": primary_history["price_unit"],
        "latest_bar": latest_bar,
        "bar_count": len(primary_history["bars"]),
        "source_record_counts": primary_history["source_record_counts"],
        "warnings": primary_history["warnings"],
    }
    curve = {
        "status": curve_status,
        "as_of": as_of_date.isoformat(),
        "price_unit": primary_history["price_unit"],
        "points": curve_points,
        "structure": curve_structure,
    }
    sources = [
        {
            "source_id": f"source_{item['provider']}_{item['requested_symbol'].lower()}",
            "provider": item["provider"],
            "dataset": item["dataset"],
            "contract_symbol": item["requested_symbol"],
            "retrieved_at": item["retrieved_at"],
            "requested_start": item["start_date"],
            "requested_end": item["end_date"],
            "license_scope": item["license_scope"],
        }
        for item in curve_histories
    ]
    sources.append(
        {
            "source_id": "source_grainagents_contract_registry",
            "provider": "GrainAgents",
            "dataset": "contract-rules-v1",
            "contract_symbol": base.instrument.symbol,
            "retrieved_at": primary_history["retrieved_at"],
            "requested_start": as_of_date.isoformat(),
            "requested_end": as_of_date.isoformat(),
            "license_scope": "project_internal",
        }
    )
    facts = _technical_facts(
        primary_history,
        technical,
        curve_points,
        curve_structure,
        base.instrument,
    )
    publication_missing = (
        "supply_demand",
        "weather",
        "demand",
        "positioning",
        "forecast",
    )
    quality = EvidenceQuality(
        status=(
            "technical_ready_publication_blocked"
            if not missing_market
            else "blocked_market_data_quality"
        ),
        missing_core_data=tuple(missing_market) + publication_missing,
        stale_sources=(
            ("contract_market_history",)
            if "recent_market_bar" in missing_market
            else ()
        ),
        warnings=tuple(
            dict.fromkeys(
                warning
                for history in curve_histories
                for warning in history.get("warnings", [])
            )
        ),
    )
    evidence = replace(
        base,
        market=freeze_evidence_value(market),
        curve=freeze_evidence_value(curve),
        technical=freeze_evidence_value(technical),
        facts=freeze_evidence_value(facts),
        sources=freeze_evidence_value(sources),
        quality=quality,
    )
    return TechnicalEvidenceRun(
        evidence=evidence,
        primary_history=primary_history,
        curve_histories=tuple(curve_histories),
    )


__all__ = [
    "CURVE_CONTRACT_COUNT",
    "TECHNICAL_LOOKBACK_CALENDAR_DAYS",
    "TechnicalEvidenceRun",
    "active_curve_contracts",
    "build_technical_evidence",
    "latest_complete_market_date",
]
