"""Immutable point-in-time evidence-package skeleton."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from types import MappingProxyType
from typing import Any

from .contracts import ContractMetadata, resolve_contract


def _empty_section() -> Mapping[str, object]:
    return MappingProxyType({})


def freeze_evidence_value(value: Any) -> Any:
    """Recursively freeze JSON-compatible evidence values."""
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): freeze_evidence_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(freeze_evidence_value(item) for item in value)
    return value


def thaw_evidence_value(value: Any) -> Any:
    """Return a JSON-serializable copy of a frozen evidence value."""
    if isinstance(value, Mapping):
        return {str(key): thaw_evidence_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw_evidence_value(item) for item in value]
    return value


@dataclass(frozen=True)
class EvidenceQuality:
    status: str = "incomplete"
    missing_core_data: tuple[str, ...] = (
        "contract_market_history",
        "futures_curve",
        "verified_technical_metrics",
    )
    stale_sources: tuple[str, ...] = ()
    contradictions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "missing_core_data": list(self.missing_core_data),
            "stale_sources": list(self.stale_sources),
            "contradictions": list(self.contradictions),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class EvidencePackage:
    schema_version: str
    run_id: str
    as_of: datetime
    instrument: ContractMetadata
    forecast_horizons: tuple[int, ...]
    market: Mapping[str, object] = field(default_factory=_empty_section)
    curve: Mapping[str, object] = field(default_factory=_empty_section)
    technical: Mapping[str, object] = field(default_factory=_empty_section)
    supply_demand: Mapping[str, object] = field(default_factory=_empty_section)
    weather: Mapping[str, object] = field(default_factory=_empty_section)
    demand: Mapping[str, object] = field(default_factory=_empty_section)
    positioning: Mapping[str, object] = field(default_factory=_empty_section)
    macro: Mapping[str, object] = field(default_factory=_empty_section)
    forecast: Mapping[str, object] = field(default_factory=_empty_section)
    facts: tuple[object, ...] = ()
    sources: tuple[object, ...] = ()
    quality: EvidenceQuality = field(default_factory=EvidenceQuality)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "as_of": self.as_of.isoformat(),
            "instrument": self.instrument.to_dict(),
            "forecast_horizons": list(self.forecast_horizons),
            "market": thaw_evidence_value(self.market),
            "curve": thaw_evidence_value(self.curve),
            "technical": thaw_evidence_value(self.technical),
            "supply_demand": thaw_evidence_value(self.supply_demand),
            "weather": thaw_evidence_value(self.weather),
            "demand": thaw_evidence_value(self.demand),
            "positioning": thaw_evidence_value(self.positioning),
            "macro": thaw_evidence_value(self.macro),
            "forecast": thaw_evidence_value(self.forecast),
            "facts": thaw_evidence_value(self.facts),
            "sources": thaw_evidence_value(self.sources),
            "quality": self.quality.to_dict(),
        }


def _as_of_datetime(value: str | date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, date):
        return datetime.combine(value, time(16, 0), tzinfo=timezone.utc)
    if len(value) == 10:
        return datetime.combine(
            date.fromisoformat(value),
            time(16, 0),
            tzinfo=timezone.utc,
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = datetime.combine(date.fromisoformat(value), time(16, 0))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def normalize_horizons(values: tuple[int, ...] | list[int]) -> tuple[int, ...]:
    horizons = tuple(dict.fromkeys(int(value) for value in values))
    if not horizons or any(value <= 0 for value in horizons):
        raise ValueError("forecast horizons must contain positive trading-day integers")
    return horizons


def build_evidence_package(
    *,
    commodity: str,
    contract_symbol: str,
    as_of: str | date | datetime,
    forecast_horizons: tuple[int, ...] | list[int] = (5, 20, 60),
) -> EvidencePackage:
    timestamp = _as_of_datetime(as_of)
    contract = resolve_contract(
        contract_symbol,
        as_of=timestamp.date(),
        expected_commodity=commodity,
    )
    horizons = normalize_horizons(forecast_horizons)
    run_id = f"{contract.commodity.value}-{contract.symbol.lower()}-{timestamp.date().isoformat()}"
    return EvidencePackage(
        schema_version="1.0",
        run_id=run_id,
        as_of=timestamp,
        instrument=contract,
        forecast_horizons=horizons,
    )
