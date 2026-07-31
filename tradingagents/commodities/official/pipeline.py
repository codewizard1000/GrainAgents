"""Assembly of official source snapshots into immutable evidence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from ..evidence import EvidencePackage, EvidenceQuality, freeze_evidence_value
from .cftc import load_corn_cot
from .eia import load_corn_ethanol
from .fas import load_corn_export_sales
from .inspections import load_corn_export_inspections
from .models import OfficialDataError, OfficialSnapshot
from .wasde import load_corn_wasde
from .weather import load_corn_weather

OfficialLoader = Callable[..., OfficialSnapshot]


@dataclass(frozen=True)
class OfficialEvidenceRun:
    evidence: EvidencePackage
    archives: tuple[OfficialSnapshot, ...]


def _fact_id(observation_metric: str, period: str) -> str:
    safe_period = period.lower().replace("/", "_").replace("-", "_")
    return f"fact_{observation_metric}_{safe_period}"


def build_official_evidence(
    base: EvidencePackage,
    *,
    wasde_loader: OfficialLoader = load_corn_wasde,
    cftc_loader: OfficialLoader = load_corn_cot,
    eia_loader: OfficialLoader | None = load_corn_ethanol,
    fas_loader: OfficialLoader | None = load_corn_export_sales,
    inspections_loader: OfficialLoader | None = load_corn_export_inspections,
    weather_loader: OfficialLoader | None = load_corn_weather,
) -> OfficialEvidenceRun:
    """Add point-in-time-safe official evidence without hiding source failures."""
    if base.instrument.commodity.value != "corn":
        return OfficialEvidenceRun(evidence=base, archives=())

    snapshots: list[OfficialSnapshot] = []
    warnings: list[str] = []
    loader_calls: list[tuple[str, OfficialLoader, dict[str, Any]]] = [
        (
            "USDA WASDE",
            wasde_loader,
            {
                "as_of": base.as_of,
                "crop_year": base.instrument.crop_year,
            },
        ),
        ("CFTC COT", cftc_loader, {"as_of": base.as_of}),
    ]
    if eia_loader is not None:
        loader_calls.append(("EIA ethanol", eia_loader, {"as_of": base.as_of}))
    if fas_loader is not None:
        loader_calls.append(
            (
                "USDA FAS export sales",
                fas_loader,
                {
                    "as_of": base.as_of,
                    "crop_year": base.instrument.crop_year,
                },
            )
        )
    if inspections_loader is not None:
        loader_calls.append(
            (
                "USDA AMS export inspections",
                inspections_loader,
                {"as_of": base.as_of},
            )
        )
    if weather_loader is not None:
        loader_calls.append(
            ("NOAA/USDA weather", weather_loader, {"as_of": base.as_of})
        )
    for label, loader, kwargs in loader_calls:
        try:
            snapshots.append(loader(**kwargs))
        except OfficialDataError as exc:
            warnings.append(f"{label}: {exc}")

    updates: dict[str, Any] = {}
    facts = list(base.facts)
    sources = list(base.sources)
    missing = list(base.quality.missing_core_data)
    demand_components: dict[str, Any] = {}
    for snapshot in snapshots:
        if snapshot.section_name == "demand":
            source_id = snapshot.source.get("source_id")
            component = (
                "ethanol"
                if source_id == "source_eia_weekly_ethanol"
                else "export_sales"
                if source_id == "source_usda_fas_esr_corn"
                else "export_inspections"
                if source_id == "source_usda_ams_fgis_corn_inspections"
                else str(source_id or "other")
            )
            demand_components[component] = dict(snapshot.section)
        else:
            updates[snapshot.section_name] = freeze_evidence_value(snapshot.section)
        if (
            snapshot.section.get("status") == "ready"
            and snapshot.section_name in missing
        ):
            missing.remove(snapshot.section_name)
        sources.append(dict(snapshot.source))
        for observation in snapshot.observations:
            facts.append(
                observation.to_fact(
                    _fact_id(observation.metric, observation.period)
                )
            )
    if demand_components:
        missing_demand = [
            item
            for item in ("ethanol", "export_sales", "export_inspections")
            if item not in demand_components
        ]
        updates["demand"] = freeze_evidence_value(
            {
                "status": "ready" if not missing_demand else "partial",
                "commodity": "corn",
                **demand_components,
                "missing": missing_demand,
            }
        )
        if missing_demand and "demand" not in missing:
            missing.append("demand")

    quality = EvidenceQuality(
        status=base.quality.status,
        missing_core_data=tuple(missing),
        stale_sources=base.quality.stale_sources,
        contradictions=base.quality.contradictions,
        warnings=tuple(
            dict.fromkeys((*base.quality.warnings, *warnings))
        ),
    )
    evidence = replace(
        base,
        **updates,
        facts=freeze_evidence_value(facts),
        sources=freeze_evidence_value(sources),
        quality=quality,
    )
    return OfficialEvidenceRun(evidence=evidence, archives=tuple(snapshots))


__all__ = ["OfficialEvidenceRun", "build_official_evidence"]
