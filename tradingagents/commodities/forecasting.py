"""Transparent deterministic price-distribution baselines for grain futures."""

from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import mean
from typing import Any

from .evidence import EvidencePackage, EvidenceQuality, freeze_evidence_value

MODEL_VERSION = "transparent-baseline-ensemble-v1"
MINIMUM_TRAINING_BARS = 120


@dataclass(frozen=True)
class ForecastEvidenceRun:
    evidence: EvidencePackage
    quantitative_forecast: dict[str, Any]
    scenarios: dict[str, Any]


def _prices(history: dict[str, Any]) -> list[float]:
    bars = sorted(history["bars"], key=lambda item: item["date"])
    values = [
        float(
            bar["settlement"]
            if bar.get("settlement") is not None
            else bar["close"]
        )
        for bar in bars
    ]
    if len(values) < 200:
        raise ValueError("forecast baselines require at least 200 daily prices")
    return values


def _model_predictions(values: list[float], horizon: int) -> dict[str, float]:
    current = values[-1]
    differences = [
        values[index] - values[index - 1]
        for index in range(1, len(values))
    ]
    average_drift = mean(differences)
    alpha = 2 / 21
    ewma_difference = differences[0]
    for value in differences[1:]:
        ewma_difference = alpha * value + (1 - alpha) * ewma_difference
    local = values[-min(60, len(values)) :]
    x_mean = (len(local) - 1) / 2
    y_mean = mean(local)
    denominator = sum((index - x_mean) ** 2 for index in range(len(local)))
    slope = (
        sum(
            (index - x_mean) * (value - y_mean)
            for index, value in enumerate(local)
        )
        / denominator
        if denominator
        else 0.0
    )
    weekly_index = -5 + ((horizon - 1) % 5)
    return {
        "random_walk": current,
        "weekly_seasonal_naive": values[weekly_index],
        "long_run_drift": current + average_drift * horizon,
        "local_linear_trend": current + slope * horizon,
        "ewma_difference": current + ewma_difference * horizon,
    }


def _weighted_quantile(
    values: list[tuple[float, float]],
    quantile: float,
) -> float:
    ordered = sorted(values, key=lambda item: item[0])
    total_weight = sum(weight for _, weight in ordered)
    target = quantile * total_weight
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= target:
            return value
    return ordered[-1][0]


def _probability(
    distribution: list[tuple[float, float]],
    predicate,
) -> float:
    total = sum(weight for _, weight in distribution)
    matching = sum(weight for value, weight in distribution if predicate(value))
    return matching / total if total else 0.0


def _horizon_forecast(
    prices: list[float],
    *,
    horizon: int,
    support: float | None,
    resistance: float | None,
) -> dict[str, Any]:
    residuals: dict[str, list[float]] = {}
    min_train = max(MINIMUM_TRAINING_BARS, horizon + 30)
    for origin in range(min_train, len(prices) - horizon):
        train = prices[:origin]
        actual = prices[origin + horizon - 1]
        for model, prediction in _model_predictions(train, horizon).items():
            residuals.setdefault(model, []).append(actual - prediction)
    if not residuals or any(not values for values in residuals.values()):
        raise ValueError(f"insufficient rolling validation for {horizon}-day forecast")

    point_predictions = _model_predictions(prices, horizon)
    mae = {
        model: mean(abs(value) for value in model_residuals)
        for model, model_residuals in residuals.items()
    }
    inverse_mae = {
        model: 1 / max(value, 1e-8)
        for model, value in mae.items()
    }
    inverse_total = sum(inverse_mae.values())
    weights = {
        model: value / inverse_total
        for model, value in inverse_mae.items()
    }
    distribution = [
        (
            point_predictions[model] + residual,
            weights[model] / len(model_residuals),
        )
        for model, model_residuals in residuals.items()
        for residual in model_residuals
    ]
    median = _weighted_quantile(distribution, 0.5)
    interval_50 = [
        _weighted_quantile(distribution, 0.25),
        _weighted_quantile(distribution, 0.75),
    ]
    interval_80 = [
        _weighted_quantile(distribution, 0.10),
        _weighted_quantile(distribution, 0.90),
    ]
    current = prices[-1]
    future_paths = [
        prices[origin + 1 : origin + horizon + 1]
        for origin in range(0, len(prices) - horizon)
    ]
    favorable = [max(path) - prices[index] for index, path in enumerate(future_paths)]
    adverse = [min(path) - prices[index] for index, path in enumerate(future_paths)]
    prediction_range = max(point_predictions.values()) - min(
        point_predictions.values()
    )
    residual_scale = mean(mae.values())
    disagreement_score = min(
        100.0,
        prediction_range / max(residual_scale, 1e-8) * 50,
    )
    interval_width_percent = (
        (interval_80[1] - interval_80[0]) / current * 100
        if current
        else 100
    )
    validation_count = min(len(values) for values in residuals.values())
    confidence = max(
        0.0,
        min(
            100.0,
            80
            + min(validation_count / 10, 10)
            - disagreement_score * 0.35
            - min(interval_width_percent, 50) * 0.6,
        ),
    )
    return {
        "horizon_trading_days": horizon,
        "current_price": round(current, 6),
        "median_projected_price": round(median, 6),
        "prediction_interval_50": [round(value, 6) for value in interval_50],
        "prediction_interval_80": [round(value, 6) for value in interval_80],
        "probability_above_resistance": (
            round(
                _probability(distribution, lambda value: value > resistance),
                6,
            )
            if resistance is not None
            else None
        ),
        "resistance_level": resistance,
        "probability_below_support": (
            round(
                _probability(distribution, lambda value: value < support),
                6,
            )
            if support is not None
            else None
        ),
        "support_level": support,
        "expected_maximum_favorable_excursion": round(mean(favorable), 6),
        "expected_maximum_adverse_excursion": round(mean(adverse), 6),
        "forecast_confidence_score": round(confidence, 2),
        "model_disagreement_score": round(disagreement_score, 2),
        "models": {
            model: {
                "point_prediction": round(point_predictions[model], 6),
                "rolling_mae": round(mae[model], 6),
                "ensemble_weight": round(weights[model], 6),
                "validation_observations": len(residuals[model]),
            }
            for model in point_predictions
        },
        "important_feature_contributions": [],
        "feature_contribution_note": (
            "These transparent price-only baselines do not support feature "
            "attribution."
        ),
    }


def _scenario_payload(
    forecasts: list[dict[str, Any]],
    *,
    symbol: str,
) -> dict[str, Any]:
    reference = min(
        forecasts,
        key=lambda item: abs(item["horizon_trading_days"] - 20),
    )
    current = reference["current_price"]
    bull_probability = max(
        0.10,
        min(
            0.40,
            reference["probability_above_resistance"]
            if reference["probability_above_resistance"] is not None
            else 0.25,
        ),
    )
    bear_probability = max(
        0.10,
        min(
            0.40,
            reference["probability_below_support"]
            if reference["probability_below_support"] is not None
            else 0.25,
        ),
    )
    base_probability = 1 - bull_probability - bear_probability
    probabilities = {
        "bull": bull_probability,
        "base": base_probability,
        "bear": bear_probability,
    }
    rounded = {key: round(value, 6) for key, value in probabilities.items()}
    rounded["base"] = round(1 - rounded["bull"] - rounded["bear"], 6)
    ranges = {
        str(item["horizon_trading_days"]): item["prediction_interval_80"]
        for item in forecasts
    }
    return {
        "schema_version": "1.0",
        "contract_symbol": symbol,
        "method": (
            "20-day ensemble probabilities beyond technical support and "
            "resistance, bounded to preserve a base scenario"
        ),
        "current_price": current,
        "probabilities": rounded,
        "probability_total": round(sum(rounded.values()), 6),
        "forecast_ranges_80": ranges,
        "constraint": (
            "Scenario probabilities are deterministic transformations of the "
            "forecast distribution and are not selected by an LLM."
        ),
    }


def build_quantitative_forecast(
    base: EvidencePackage,
    *,
    history: dict[str, Any],
) -> ForecastEvidenceRun:
    prices = _prices(history)
    technical = base.technical
    support = technical.get("levels", {}).get("support_20")
    resistance = technical.get("levels", {}).get("resistance_20")
    forecasts = [
        _horizon_forecast(
            prices,
            horizon=horizon,
            support=float(support) if support is not None else None,
            resistance=float(resistance) if resistance is not None else None,
        )
        for horizon in base.forecast_horizons
    ]
    scenarios = _scenario_payload(
        forecasts,
        symbol=base.instrument.symbol,
    )
    quantitative = {
        "schema_version": "1.0",
        "status": "ready_research_only",
        "model_version": MODEL_VERSION,
        "contract_symbol": base.instrument.symbol,
        "as_of": base.as_of.isoformat(),
        "price_unit": history["price_unit"],
        "training_observations": len(prices),
        "forecast_horizons": forecasts,
        "scenarios": scenarios,
        "limitations": [
            "Price-only baseline ensemble; official fundamentals and weather are not model features.",
            "Prediction intervals use rolling historical residuals from this exact delivery contract.",
            "No claim of calibrated live trading performance is made until forecasts are scored out of sample.",
        ],
    }
    source_id = "source_grainagents_transparent_forecast"
    facts = list(base.facts)
    for item in forecasts:
        horizon = item["horizon_trading_days"]
        for suffix, metric, value, unit in (
            (
                "median",
                "forecast_median_projected_price",
                item["median_projected_price"],
                history["price_unit"],
            ),
            (
                "interval_50",
                "forecast_prediction_interval_50",
                item["prediction_interval_50"],
                history["price_unit"],
            ),
            (
                "interval_80",
                "forecast_prediction_interval_80",
                item["prediction_interval_80"],
                history["price_unit"],
            ),
            (
                "confidence",
                "forecast_confidence_score",
                item["forecast_confidence_score"],
                "score_0_100",
            ),
            (
                "disagreement",
                "forecast_model_disagreement_score",
                item["model_disagreement_score"],
                "score_0_100",
            ),
        ):
            facts.append(
                {
                    "fact_id": f"fact_forecast_{horizon}d_{suffix}",
                    "metric": metric,
                    "value": value,
                    "unit": unit,
                    "observed_at": base.as_of.date().isoformat(),
                    "available_at": base.as_of.isoformat(),
                    "source_id": source_id,
                    "derivation": MODEL_VERSION,
                }
            )
    sources = list(base.sources)
    sources.append(
        {
            "source_id": source_id,
            "provider": "GrainAgents",
            "dataset": MODEL_VERSION,
            "contract_symbol": base.instrument.symbol,
            "retrieved_at": history["retrieved_at"],
            "training_start": history["start_date"],
            "training_end": history["end_date"],
            "license_scope": history["license_scope"],
        }
    )
    missing = tuple(
        item for item in base.quality.missing_core_data if item != "forecast"
    )
    quality = EvidenceQuality(
        status="forecast_baseline_ready_publication_blocked",
        missing_core_data=missing,
        stale_sources=base.quality.stale_sources,
        contradictions=base.quality.contradictions,
        warnings=base.quality.warnings,
    )
    evidence = replace(
        base,
        forecast=freeze_evidence_value(quantitative),
        facts=freeze_evidence_value(facts),
        sources=freeze_evidence_value(sources),
        quality=quality,
    )
    return ForecastEvidenceRun(
        evidence=evidence,
        quantitative_forecast=quantitative,
        scenarios=scenarios,
    )


__all__ = [
    "ForecastEvidenceRun",
    "MODEL_VERSION",
    "build_quantitative_forecast",
]
