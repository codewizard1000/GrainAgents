"""Shared provenance contracts for official observations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any


class OfficialDataError(RuntimeError):
    """Raised when an official source cannot produce point-in-time-safe data."""


@dataclass(frozen=True)
class OfficialObservation:
    metric: str
    value: int | float | str
    unit: str
    period: str
    released_at: datetime
    available_at: datetime
    retrieved_at: datetime
    source_id: str
    source_url: str
    vintage: str
    availability_policy: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_fact(self, fact_id: str) -> dict[str, Any]:
        return {
            "fact_id": fact_id,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "observed_at": self.period,
            "available_at": self.available_at.isoformat(),
            "source_id": self.source_id,
            "derivation": "official_source_normalization",
            "released_at": self.released_at.isoformat(),
            "vintage": self.vintage,
            "availability_policy": self.availability_policy,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class OfficialSnapshot:
    section_name: str
    section: dict[str, Any]
    observations: tuple[OfficialObservation, ...]
    source: dict[str, Any]
    archive_filename: str
    raw_content: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "section", MappingProxyType(dict(self.section)))
        object.__setattr__(self, "source", MappingProxyType(dict(self.source)))


__all__ = [
    "OfficialDataError",
    "OfficialObservation",
    "OfficialSnapshot",
]
