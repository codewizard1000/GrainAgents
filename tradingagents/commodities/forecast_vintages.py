"""Immutable saved forecasts and genuine out-of-sample outcome scoring."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from math import isfinite
from pathlib import Path
from statistics import mean
from typing import Any

from .evidence import freeze_evidence_value
from .forecasting import ForecastEvidenceRun

VINTAGE_SCHEMA_VERSION = "1.0"
LIVE_PERFORMANCE_VERSION = "saved-forecast-outcome-registry-v1"
MINIMUM_LIVE_SCORES_PER_HORIZON = 20
MINIMUM_INTERVAL_80_COVERAGE = 0.70
MAXIMUM_RELATIVE_ABSOLUTE_ERROR = 1.0


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _pinball_loss(actual: float, predicted: float, quantile: float) -> float:
    error = actual - predicted
    return max(quantile * error, (quantile - 1) * error)


@dataclass(frozen=True)
class ForecastHorizonVintage:
    horizon_trading_days: int
    median_projected_price: float
    prediction_interval_50: tuple[float, float]
    prediction_interval_80: tuple[float, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon_trading_days": self.horizon_trading_days,
            "median_projected_price": self.median_projected_price,
            "prediction_interval_50": list(self.prediction_interval_50),
            "prediction_interval_80": list(self.prediction_interval_80),
        }


@dataclass(frozen=True)
class ForecastVintage:
    run_id: str
    commodity: str
    contract_symbol: str
    as_of: str
    origin_market_date: str
    current_price: float
    price_unit: str
    model_version: str
    horizons: tuple[ForecastHorizonVintage, ...]
    vintage_id: str
    sha256: str

    def _unsigned_payload(self) -> dict[str, Any]:
        return {
            "schema_version": VINTAGE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "commodity": self.commodity,
            "contract_symbol": self.contract_symbol,
            "as_of": self.as_of,
            "origin_market_date": self.origin_market_date,
            "current_price": self.current_price,
            "price_unit": self.price_unit,
            "model_version": self.model_version,
            "horizons": [item.to_dict() for item in self.horizons],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._unsigned_payload(),
            "vintage_id": self.vintage_id,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ForecastVintage:
        try:
            horizons = tuple(
                ForecastHorizonVintage(
                    horizon_trading_days=int(item["horizon_trading_days"]),
                    median_projected_price=float(item["median_projected_price"]),
                    prediction_interval_50=tuple(
                        float(value) for value in item["prediction_interval_50"]
                    ),
                    prediction_interval_80=tuple(
                        float(value) for value in item["prediction_interval_80"]
                    ),
                )
                for item in payload["horizons"]
            )
            vintage = cls(
                run_id=str(payload["run_id"]),
                commodity=str(payload["commodity"]),
                contract_symbol=str(payload["contract_symbol"]),
                as_of=str(payload["as_of"]),
                origin_market_date=str(payload["origin_market_date"]),
                current_price=float(payload["current_price"]),
                price_unit=str(payload["price_unit"]),
                model_version=str(payload["model_version"]),
                horizons=horizons,
                vintage_id=str(payload["vintage_id"]),
                sha256=str(payload["sha256"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("saved forecast vintage is malformed") from exc
        if payload.get("schema_version") != VINTAGE_SCHEMA_VERSION:
            raise ValueError("saved forecast vintage has an unsupported schema")
        if any(
            len(interval) != 2
            for item in horizons
            for interval in (
                item.prediction_interval_50,
                item.prediction_interval_80,
            )
        ):
            raise ValueError("saved forecast vintage has malformed intervals")
        try:
            as_of = datetime.fromisoformat(vintage.as_of)
            origin_market_date = date.fromisoformat(vintage.origin_market_date)
        except ValueError as exc:
            raise ValueError("saved forecast vintage has malformed dates") from exc
        if as_of.tzinfo is None:
            raise ValueError("saved forecast vintage has a naive as-of timestamp")
        if origin_market_date > as_of.date():
            raise ValueError("saved forecast vintage origin is after its as-of date")
        horizon_days = [item.horizon_trading_days for item in horizons]
        if (
            not horizons
            or any(value <= 0 for value in horizon_days)
            or len(set(horizon_days)) != len(horizon_days)
        ):
            raise ValueError("saved forecast vintage has invalid horizons")
        if not all(
            isfinite(value)
            for item in horizons
            for value in (
                item.median_projected_price,
                *item.prediction_interval_50,
                *item.prediction_interval_80,
            )
        ) or not isfinite(vintage.current_price):
            raise ValueError("saved forecast vintage has non-finite prices")
        if any(
            not (
                item.prediction_interval_80[0]
                <= item.prediction_interval_50[0]
                <= item.median_projected_price
                <= item.prediction_interval_50[1]
                <= item.prediction_interval_80[1]
            )
            for item in horizons
        ):
            raise ValueError("saved forecast vintage intervals are not nested")
        expected_hash = hashlib.sha256(
            _canonical_bytes(vintage._unsigned_payload())
        ).hexdigest()
        if vintage.sha256 != expected_hash:
            raise ValueError("saved forecast vintage checksum does not match")
        if vintage.vintage_id != f"forecast_{expected_hash[:16]}":
            raise ValueError("saved forecast vintage ID does not match its checksum")
        return vintage


def build_forecast_vintage(
    quantitative: dict[str, Any],
    *,
    run_id: str,
    commodity: str,
    origin_market_date: str,
) -> ForecastVintage:
    """Create a content-addressed record of a forecast before outcomes exist."""
    forecast_horizons = quantitative["forecast_horizons"]
    if not forecast_horizons:
        raise ValueError("cannot save a forecast vintage without horizons")
    current_prices = {float(item["current_price"]) for item in forecast_horizons}
    if len(current_prices) != 1:
        raise ValueError("forecast horizons disagree on the origin price")
    horizons = tuple(
        ForecastHorizonVintage(
            horizon_trading_days=int(item["horizon_trading_days"]),
            median_projected_price=float(item["median_projected_price"]),
            prediction_interval_50=tuple(
                float(value) for value in item["prediction_interval_50"]
            ),
            prediction_interval_80=tuple(
                float(value) for value in item["prediction_interval_80"]
            ),
        )
        for item in forecast_horizons
    )
    provisional = ForecastVintage(
        run_id=run_id,
        commodity=commodity,
        contract_symbol=str(quantitative["contract_symbol"]),
        as_of=str(quantitative["as_of"]),
        origin_market_date=origin_market_date,
        current_price=current_prices.pop(),
        price_unit=str(quantitative["price_unit"]),
        model_version=str(quantitative["model_version"]),
        horizons=horizons,
        vintage_id="",
        sha256="",
    )
    digest = hashlib.sha256(
        _canonical_bytes(provisional._unsigned_payload())
    ).hexdigest()
    signed = replace(
        provisional,
        vintage_id=f"forecast_{digest[:16]}",
        sha256=digest,
    )
    return ForecastVintage.from_dict(signed.to_dict())


def write_immutable_forecast_vintage(
    directory: Path,
    vintage: ForecastVintage,
) -> Path:
    """Write once by content hash; never replace a different saved forecast."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{vintage.vintage_id}.json"
    rendered = json.dumps(vintage.to_dict(), indent=2, sort_keys=True) + "\n"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
    except FileExistsError:
        try:
            existing = ForecastVintage.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                f"existing forecast vintage failed integrity validation: {path}"
            ) from exc
        if existing != vintage:
            raise ValueError(
                f"forecast vintage hash collision or content mismatch: {path}"
            ) from None
    return path


def load_prior_forecast_vintages(
    results_dir: Path,
    *,
    commodity: str,
    contract_symbol: str,
    before: datetime,
) -> tuple[ForecastVintage, ...]:
    """Load only hash-valid earlier vintages for this exact delivery contract."""
    root = results_dir / commodity / contract_symbol
    if not root.exists():
        return ()
    vintages: list[ForecastVintage] = []
    for path in sorted(root.glob("*/forecast_vintages/forecast_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"could not read saved forecast vintage {path}") from exc
        vintage = ForecastVintage.from_dict(payload)
        if (
            vintage.commodity != commodity
            or vintage.contract_symbol != contract_symbol
        ):
            raise ValueError(f"saved forecast vintage identity mismatch in {path}")
        vintage_as_of = datetime.fromisoformat(vintage.as_of)
        if vintage_as_of.tzinfo is None:
            raise ValueError(f"saved forecast vintage has naive as-of time in {path}")
        if vintage_as_of < before:
            vintages.append(vintage)
    earliest_by_origin: dict[str, ForecastVintage] = {}
    for vintage in vintages:
        selected = earliest_by_origin.get(vintage.origin_market_date)
        if selected is None or (
            datetime.fromisoformat(vintage.as_of).timestamp(),
            vintage.vintage_id,
        ) < (
            datetime.fromisoformat(selected.as_of).timestamp(),
            selected.vintage_id,
        ):
            earliest_by_origin[vintage.origin_market_date] = vintage
    return tuple(
        earliest_by_origin[key] for key in sorted(earliest_by_origin)
    )


def _market_rows(
    history: dict[str, Any],
    *,
    as_of: datetime,
) -> list[tuple[date, float]]:
    rows: dict[date, float] = {}
    for bar in history["bars"]:
        bar_date = date.fromisoformat(str(bar["date"])[:10])
        if bar_date > as_of.date():
            continue
        raw_price = bar.get("settlement")
        availability = bar.get("settlement_available_at")
        if raw_price is None:
            raw_price = bar["close"]
            availability = bar.get("bar_available_at", availability)
        if availability is not None:
            available_at = datetime.fromisoformat(str(availability))
            if available_at.tzinfo is None:
                raise ValueError("market bar has a naive availability timestamp")
            if available_at > as_of:
                continue
        rows[bar_date] = float(raw_price)
    return sorted(rows.items())


def score_saved_forecasts(
    vintages: tuple[ForecastVintage, ...],
    *,
    history: dict[str, Any],
    as_of: datetime,
    requested_horizons: tuple[int, ...],
    model_version: str | None = None,
    minimum_scores_per_horizon: int = MINIMUM_LIVE_SCORES_PER_HORIZON,
) -> dict[str, Any]:
    """Score matured earlier forecasts using only bars available at ``as_of``."""
    if as_of.tzinfo is None:
        raise ValueError("live forecast scoring requires a timezone-aware as-of")
    if minimum_scores_per_horizon <= 0:
        raise ValueError("minimum live score count must be positive")
    rows = _market_rows(history, as_of=as_of)
    row_index = {bar_date.isoformat(): index for index, (bar_date, _) in enumerate(rows)}
    score_rows: list[dict[str, Any]] = []
    requested = set(requested_horizons)
    for vintage in vintages:
        if model_version is not None and vintage.model_version != model_version:
            continue
        vintage_as_of = datetime.fromisoformat(vintage.as_of)
        if vintage_as_of.tzinfo is None:
            raise ValueError("saved forecast vintage has a naive as-of timestamp")
        if vintage_as_of >= as_of:
            continue
        origin_index = row_index.get(vintage.origin_market_date)
        if origin_index is None:
            continue
        for forecast in vintage.horizons:
            horizon = forecast.horizon_trading_days
            if horizon not in requested:
                continue
            target_index = origin_index + horizon
            if target_index >= len(rows):
                continue
            target_date, actual = rows[target_index]
            predicted = forecast.median_projected_price
            actual_change = actual - vintage.current_price
            predicted_change = predicted - vintage.current_price
            direction_correct = (
                actual_change == 0 and predicted_change == 0
            ) or (actual_change * predicted_change > 0)
            low_50, high_50 = forecast.prediction_interval_50
            low_80, high_80 = forecast.prediction_interval_80
            quantile_loss = mean(
                (
                    _pinball_loss(actual, low_50, 0.25),
                    _pinball_loss(actual, high_50, 0.75),
                    _pinball_loss(actual, low_80, 0.10),
                    _pinball_loss(actual, high_80, 0.90),
                )
            )
            score_rows.append(
                {
                    "vintage_id": vintage.vintage_id,
                    "forecast_as_of": vintage.as_of,
                    "origin_market_date": vintage.origin_market_date,
                    "horizon_trading_days": horizon,
                    "target_market_date": target_date.isoformat(),
                    "origin_price": vintage.current_price,
                    "median_projected_price": predicted,
                    "actual_price": actual,
                    "error": round(predicted - actual, 10),
                    "absolute_error": round(abs(predicted - actual), 10),
                    "naive_absolute_error": round(
                        abs(vintage.current_price - actual),
                        10,
                    ),
                    "direction_correct": direction_correct,
                    "interval_50_hit": low_50 <= actual <= high_50,
                    "interval_80_hit": low_80 <= actual <= high_80,
                    "mean_quantile_loss": round(quantile_loss, 10),
                }
            )

    horizon_metrics: list[dict[str, Any]] = []
    for horizon in requested_horizons:
        matching = [
            row
            for row in score_rows
            if row["horizon_trading_days"] == horizon
        ]
        count = len(matching)
        if not matching:
            horizon_metrics.append(
                {
                    "horizon_trading_days": horizon,
                    "scored_forecasts": 0,
                    "mean_absolute_error": None,
                    "naive_mean_absolute_error": None,
                    "relative_absolute_error": None,
                    "directional_accuracy": None,
                    "interval_coverage_50": None,
                    "interval_coverage_80": None,
                    "mean_quantile_loss": None,
                    "publication_eligible": False,
                }
            )
            continue
        mae = mean(row["absolute_error"] for row in matching)
        naive_mae = mean(row["naive_absolute_error"] for row in matching)
        relative_error = mae / naive_mae if naive_mae > 0 else None
        coverage_80 = mean(float(row["interval_80_hit"]) for row in matching)
        eligible = (
            count >= minimum_scores_per_horizon
            and relative_error is not None
            and relative_error < MAXIMUM_RELATIVE_ABSOLUTE_ERROR
            and coverage_80 >= MINIMUM_INTERVAL_80_COVERAGE
        )
        horizon_metrics.append(
            {
                "horizon_trading_days": horizon,
                "scored_forecasts": count,
                "mean_absolute_error": round(mae, 10),
                "naive_mean_absolute_error": round(naive_mae, 10),
                "relative_absolute_error": (
                    round(relative_error, 10)
                    if relative_error is not None
                    else None
                ),
                "directional_accuracy": round(
                    mean(float(row["direction_correct"]) for row in matching),
                    10,
                ),
                "interval_coverage_50": round(
                    mean(float(row["interval_50_hit"]) for row in matching),
                    10,
                ),
                "interval_coverage_80": round(coverage_80, 10),
                "mean_quantile_loss": round(
                    mean(row["mean_quantile_loss"] for row in matching),
                    10,
                ),
                "publication_eligible": eligible,
            }
        )
    ready = bool(horizon_metrics) and all(
        item["publication_eligible"] for item in horizon_metrics
    )
    return {
        "schema_version": "1.0",
        "methodology_version": LIVE_PERFORMANCE_VERSION,
        "contract_symbol": history.get("requested_symbol"),
        "model_version": model_version,
        "evaluated_as_of": as_of.isoformat(),
        "status": "ready_for_publication" if ready else "insufficient_live_scores",
        "minimum_scores_per_horizon": minimum_scores_per_horizon,
        "eligibility_criteria": {
            "relative_absolute_error_below": MAXIMUM_RELATIVE_ABSOLUTE_ERROR,
            "interval_80_coverage_at_least": MINIMUM_INTERVAL_80_COVERAGE,
            "all_requested_horizons_must_pass": True,
        },
        "saved_prior_vintages": len(vintages),
        "current_model_prior_vintages": sum(
            model_version is None or vintage.model_version == model_version
            for vintage in vintages
        ),
        "matured_score_rows": len(score_rows),
        "horizons": horizon_metrics,
        "scores": score_rows,
        "point_in_time_policy": (
            "Only forecasts saved before the current as-of timestamp are eligible. "
            "At most the earliest saved forecast per origin market session is "
            "scored, and promotion uses only the current model version. A horizon "
            "is scored at the corresponding later exact-contract market session "
            "only when that session is present in the current as-of history."
        ),
    }


def attach_live_performance(
    run: ForecastEvidenceRun,
    registry: dict[str, Any],
) -> ForecastEvidenceRun:
    """Attach genuine score evidence and promote only when every criterion passes."""
    quantitative = dict(run.quantitative_forecast)
    quantitative["live_performance_registry"] = registry
    quantitative["status"] = (
        "ready_for_publication"
        if registry["status"] == "ready_for_publication"
        else "ready_research_only"
    )
    limitations = list(quantitative.get("limitations", []))
    limitations = [
        item
        for item in limitations
        if not item.startswith("No claim of calibrated live trading performance")
    ]
    if quantitative["status"] != "ready_for_publication":
        limitations.append(
            "Saved forecasts have not yet met the minimum genuine out-of-sample "
            "sample, skill, and interval-coverage criteria at every horizon."
        )
    quantitative["limitations"] = limitations
    forecast_section = dict(run.evidence.forecast)
    forecast_section["live_performance_registry"] = registry
    forecast_section["status"] = quantitative["status"]
    forecast_section["limitations"] = limitations
    evidence = replace(
        run.evidence,
        forecast=freeze_evidence_value(forecast_section),
    )
    return ForecastEvidenceRun(
        evidence=evidence,
        quantitative_forecast=quantitative,
        scenarios=run.scenarios,
    )


__all__ = [
    "ForecastVintage",
    "LIVE_PERFORMANCE_VERSION",
    "MINIMUM_LIVE_SCORES_PER_HORIZON",
    "attach_live_performance",
    "build_forecast_vintage",
    "load_prior_forecast_vintages",
    "score_saved_forecasts",
    "write_immutable_forecast_vintage",
]
