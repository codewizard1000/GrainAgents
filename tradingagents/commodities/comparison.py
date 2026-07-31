"""Deterministic comparison with the latest prior approved outlook."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

COMPARISON_VERSION = "prior-approved-outlook-comparison-v1"


def find_prior_approved_outlook(
    results_dir: Path,
    *,
    commodity: str,
    contract_symbol: str,
    current_date: date,
) -> dict[str, Any] | None:
    """Return the most recent earlier approved outlook for this exact contract."""
    contract_dir = results_dir / commodity / contract_symbol
    if not contract_dir.exists():
        return None
    candidates: list[tuple[date, dict[str, Any]]] = []
    for run_dir in contract_dir.iterdir():
        if not run_dir.is_dir():
            continue
        try:
            run_date = date.fromisoformat(run_dir.name)
        except ValueError:
            continue
        if run_date >= current_date:
            continue
        status_path = run_dir / "publication_status.json"
        outlook_path = run_dir / "final_outlook.json"
        if not status_path.exists() or not outlook_path.exists():
            continue
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            outlook = json.loads(outlook_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if (
            status.get("status") == "approved"
            and status.get("publication_ready") is True
        ):
            candidates.append((run_date, outlook))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def build_prior_report_comparison(
    current_outlook: dict[str, Any],
    prior_outlook: dict[str, Any] | None,
) -> dict[str, Any]:
    """Calculate only deterministic differences between approved outlooks."""
    base = {
        "schema_version": "1.0",
        "comparison_version": COMPARISON_VERSION,
        "contract_symbol": current_outlook["contract_symbol"],
        "current_run_id": current_outlook["run_id"],
        "current_as_of": current_outlook["as_of"],
    }
    if prior_outlook is None:
        return {
            **base,
            "status": "unavailable",
            "reason": "no_prior_approved_report",
            "changes": {},
        }
    if prior_outlook.get("contract_symbol") != current_outlook["contract_symbol"]:
        raise ValueError("prior outlook contract does not match current outlook")

    current_probabilities = current_outlook["scenario_probabilities"]
    prior_probabilities = prior_outlook["scenario_probabilities"]
    current_interval = current_outlook["prediction_interval_80"]
    prior_interval = prior_outlook["prediction_interval_80"]
    changes = {
        "median_projected_price": round(
            current_outlook["median_projected_price"]
            - prior_outlook["median_projected_price"],
            6,
        ),
        "forecast_confidence_score": round(
            current_outlook["forecast_confidence_score"]
            - prior_outlook["forecast_confidence_score"],
            2,
        ),
        "model_disagreement_score": round(
            current_outlook["model_disagreement_score"]
            - prior_outlook["model_disagreement_score"],
            2,
        ),
        "prediction_interval_80_lower": round(
            current_interval[0] - prior_interval[0],
            6,
        ),
        "prediction_interval_80_upper": round(
            current_interval[1] - prior_interval[1],
            6,
        ),
        "prediction_interval_80_width": round(
            (current_interval[1] - current_interval[0])
            - (prior_interval[1] - prior_interval[0]),
            6,
        ),
        "scenario_probability_bull": round(
            current_probabilities["bull"] - prior_probabilities["bull"],
            6,
        ),
        "scenario_probability_base": round(
            current_probabilities["base"] - prior_probabilities["base"],
            6,
        ),
        "scenario_probability_bear": round(
            current_probabilities["bear"] - prior_probabilities["bear"],
            6,
        ),
        "directional_bias": {
            "previous": prior_outlook["directional_bias"],
            "current": current_outlook["directional_bias"],
            "changed": (
                prior_outlook["directional_bias"]
                != current_outlook["directional_bias"]
            ),
        },
    }
    return {
        **base,
        "status": "ready",
        "prior_run_id": prior_outlook["run_id"],
        "prior_as_of": prior_outlook["as_of"],
        "changes": changes,
        "methodology": (
            "Exact-contract current outlook minus the most recent earlier "
            "human-approved outlook."
        ),
    }


__all__ = [
    "COMPARISON_VERSION",
    "build_prior_report_comparison",
    "find_prior_approved_outlook",
]
