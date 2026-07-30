"""Artifact writers for immutable, auditable commodity evidence runs."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .official.models import OfficialSnapshot


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_market_parquet(path: Path, history: dict[str, Any]) -> None:
    frame = pd.DataFrame(history["bars"])
    frame.insert(0, "provider", history["provider"])
    frame.insert(1, "dataset", history["dataset"])
    frame.to_parquet(path, index=False)


def write_source_audit(path: Path, evidence: dict[str, Any]) -> None:
    fields = (
        "fact_id",
        "metric",
        "value",
        "unit",
        "observed_at",
        "available_at",
        "source_id",
        "derivation",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for fact in evidence["facts"]:
            writer.writerow({field: fact.get(field) for field in fields})


def write_official_archives(
    directory: Path,
    snapshots: tuple[OfficialSnapshot, ...],
) -> list[str]:
    """Persist exact official payloads used by a run."""
    if not snapshots:
        return []
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for snapshot in snapshots:
        path = directory / snapshot.archive_filename
        path.write_bytes(snapshot.raw_content)
        paths.append(path.as_posix())
    return paths


__all__ = [
    "write_json",
    "write_market_parquet",
    "write_official_archives",
    "write_source_audit",
]
