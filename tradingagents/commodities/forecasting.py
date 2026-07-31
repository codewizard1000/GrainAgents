"""Transparent deterministic price-distribution baselines for grain futures."""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import sqrt
from statistics import mean
from typing import Any

from .evidence import EvidencePackage, EvidenceQuality, freeze_evidence_value

MODEL_VERSION = "validated-baseline-tree-ensemble-v2"
PERFORMANCE_VERSION = "expanding-window-score-registry-v1"
MINIMUM_TRAINING_BARS = 120
PERFORMANCE_WARMUP = 30
TREE_MODEL = "regression_tree"
TREE_FEATURE_NAMES = (
    "price_change_1",
    "price_change_5",
    "price_change_20",
    "moving_average_gap_20",
    "realized_volatility_20",
)
TREE_MAX_DEPTH = 3
TREE_MIN_LEAF = 8


@dataclass(frozen=True)
class _TreeNode:
    prediction: float
    feature_index: int | None = None
    threshold: float | None = None
    left: _TreeNode | None = None
    right: _TreeNode | None = None


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


def _tree_rows(
    values: list[float],
    *,
    horizon: int,
) -> list[tuple[tuple[float, ...], float]]:
    rows = []
    for index in range(20, len(values) - horizon):
        recent_changes = [
            values[position] - values[position - 1]
            for position in range(index - 19, index + 1)
        ]
        average_change = mean(recent_changes)
        volatility = sqrt(
            mean((change - average_change) ** 2 for change in recent_changes)
        )
        moving_average = mean(values[index - 19 : index + 1])
        features = (
            values[index] - values[index - 1],
            values[index] - values[index - 5],
            values[index] - values[index - 20],
            values[index] - moving_average,
            volatility,
        )
        target_change = values[index + horizon] - values[index]
        rows.append((features, target_change))
    return rows


def _candidate_thresholds(
    rows: list[tuple[tuple[float, ...], float]],
    feature_index: int,
) -> tuple[float, ...]:
    values = sorted({row[0][feature_index] for row in rows})
    if len(values) < 2:
        return ()
    split_count = min(15, len(values) - 1)
    indexes = {
        max(0, min(len(values) - 2, (step * len(values)) // (split_count + 1)))
        for step in range(1, split_count + 1)
    }
    return tuple(
        (values[index] + values[index + 1]) / 2
        for index in sorted(indexes)
    )


def _sum_squared_error(values: list[float]) -> float:
    center = mean(values)
    return sum((value - center) ** 2 for value in values)


def _fit_regression_tree(
    rows: list[tuple[tuple[float, ...], float]],
    *,
    depth: int = 0,
) -> _TreeNode:
    prediction = mean(row[1] for row in rows)
    if depth >= TREE_MAX_DEPTH or len(rows) < TREE_MIN_LEAF * 2:
        return _TreeNode(prediction=prediction)

    best: tuple[
        float,
        int,
        float,
        list[tuple[tuple[float, ...], float]],
        list[tuple[tuple[float, ...], float]],
    ] | None = None
    for feature_index in range(len(TREE_FEATURE_NAMES)):
        for threshold in _candidate_thresholds(rows, feature_index):
            left = [row for row in rows if row[0][feature_index] <= threshold]
            right = [row for row in rows if row[0][feature_index] > threshold]
            if len(left) < TREE_MIN_LEAF or len(right) < TREE_MIN_LEAF:
                continue
            loss = _sum_squared_error([row[1] for row in left])
            loss += _sum_squared_error([row[1] for row in right])
            candidate = (loss, feature_index, threshold, left, right)
            if best is None or candidate[:3] < best[:3]:
                best = candidate
    if best is None:
        return _TreeNode(prediction=prediction)

    _, feature_index, threshold, left, right = best
    return _TreeNode(
        prediction=prediction,
        feature_index=feature_index,
        threshold=threshold,
        left=_fit_regression_tree(left, depth=depth + 1),
        right=_fit_regression_tree(right, depth=depth + 1),
    )


def _latest_tree_features(values: list[float]) -> tuple[float, ...]:
    recent_changes = [
        values[position] - values[position - 1]
        for position in range(len(values) - 20, len(values))
    ]
    average_change = mean(recent_changes)
    volatility = sqrt(
        mean((change - average_change) ** 2 for change in recent_changes)
    )
    moving_average = mean(values[-20:])
    return (
        values[-1] - values[-2],
        values[-1] - values[-6],
        values[-1] - values[-21],
        values[-1] - moving_average,
        volatility,
    )


def _predict_tree(node: _TreeNode, features: tuple[float, ...]) -> float:
    current = node
    while current.feature_index is not None:
        branch = (
            current.left
            if features[current.feature_index] <= current.threshold
            else current.right
        )
        if branch is None:
            break
        current = branch
    return current.prediction


def _tree_prediction(values: list[float], horizon: int) -> tuple[float, _TreeNode]:
    rows = _tree_rows(values, horizon=horizon)
    if len(rows) < TREE_MIN_LEAF * 2:
        raise ValueError("insufficient point-in-time rows for regression tree")
    tree = _fit_regression_tree(rows)
    predicted_change = _predict_tree(tree, _latest_tree_features(values))
    return values[-1] + predicted_change, tree


def _ensemble_eligibility(mae: dict[str, float]) -> dict[str, bool]:
    baseline_errors = [
        error
        for model, error in mae.items()
        if model != TREE_MODEL
    ]
    return {
        model: (
            True
            if model != TREE_MODEL
            else error < min(baseline_errors)
        )
        for model, error in mae.items()
    }


def _ensemble_weights(mae: dict[str, float]) -> tuple[dict[str, float], dict[str, bool]]:
    eligible = _ensemble_eligibility(mae)
    inverse_mae = {
        model: 1 / max(value, 1e-8) if eligible[model] else 0.0
        for model, value in mae.items()
    }
    inverse_total = sum(inverse_mae.values())
    return (
        {
            model: value / inverse_total
            for model, value in inverse_mae.items()
        },
        eligible,
    )


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


def _quantile(values: list[float], quantile: float) -> float:
    return _weighted_quantile(
        [(value, 1 / len(values)) for value in values],
        quantile,
    )


def _quantile_loss(actual: float, predicted: float, quantile: float) -> float:
    error = actual - predicted
    return max(quantile * error, (quantile - 1) * error)


def _rolling_performance(
    validation_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    model_errors: dict[str, list[float]] = {}
    ensemble_residuals: list[float] = []
    absolute_errors: list[float] = []
    scaled_errors: list[float] = []
    direction_matches: list[bool] = []
    coverage_50: list[bool] = []
    coverage_80: list[bool] = []
    quantile_losses: list[float] = []

    for index, row in enumerate(validation_rows):
        if index >= PERFORMANCE_WARMUP:
            prior_mae = {
                model: mean(abs(error) for error in errors)
                for model, errors in model_errors.items()
            }
            weights, _eligible = _ensemble_weights(prior_mae)
            point = sum(
                row["predictions"][model] * weight
                for model, weight in weights.items()
            )
            error = row["actual"] - point
            absolute_errors.append(abs(error))
            scaled_errors.append(abs(error) / max(row["naive_scale"], 1e-8))
            predicted_direction = point - row["current_price"]
            actual_direction = row["actual"] - row["current_price"]
            direction_matches.append(
                (predicted_direction > 0 and actual_direction > 0)
                or (predicted_direction < 0 and actual_direction < 0)
                or (predicted_direction == 0 and actual_direction == 0)
            )

            if len(ensemble_residuals) >= PERFORMANCE_WARMUP:
                quantiles = {
                    0.10: point + _quantile(ensemble_residuals, 0.10),
                    0.25: point + _quantile(ensemble_residuals, 0.25),
                    0.75: point + _quantile(ensemble_residuals, 0.75),
                    0.90: point + _quantile(ensemble_residuals, 0.90),
                }
                coverage_50.append(
                    quantiles[0.25] <= row["actual"] <= quantiles[0.75]
                )
                coverage_80.append(
                    quantiles[0.10] <= row["actual"] <= quantiles[0.90]
                )
                quantile_losses.extend(
                    _quantile_loss(row["actual"], prediction, quantile)
                    for quantile, prediction in quantiles.items()
                )
            ensemble_residuals.append(error)

        for model, prediction in row["predictions"].items():
            model_errors.setdefault(model, []).append(row["actual"] - prediction)

    return {
        "methodology_version": PERFORMANCE_VERSION,
        "weighting_policy": (
            "At each scored origin, inverse-MAE weights and tree eligibility "
            "use only model errors observed before that origin."
        ),
        "interval_policy": (
            "At each interval-scored origin, quantiles use only earlier "
            "point-in-time ensemble residuals."
        ),
        "warmup_observations": PERFORMANCE_WARMUP,
        "evaluation_observations": len(absolute_errors),
        "interval_evaluation_observations": len(coverage_80),
        "mean_absolute_error": (
            round(mean(absolute_errors), 6) if absolute_errors else None
        ),
        "mean_absolute_scaled_error": (
            round(mean(scaled_errors), 6) if scaled_errors else None
        ),
        "directional_accuracy": (
            round(sum(direction_matches) / len(direction_matches), 6)
            if direction_matches
            else None
        ),
        "interval_coverage_50": (
            round(sum(coverage_50) / len(coverage_50), 6)
            if coverage_50
            else None
        ),
        "interval_coverage_80": (
            round(sum(coverage_80) / len(coverage_80), 6)
            if coverage_80
            else None
        ),
        "mean_quantile_loss": (
            round(mean(quantile_losses), 6) if quantile_losses else None
        ),
        "mase_scale": "mean absolute one-session price change available at origin",
    }


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
    validation_rows: list[dict[str, Any]] = []
    min_train = max(MINIMUM_TRAINING_BARS, horizon + 30)
    for origin in range(min_train, len(prices) - horizon):
        train = prices[:origin]
        actual = prices[origin + horizon - 1]
        predictions = _model_predictions(train, horizon)
        tree_prediction, _tree = _tree_prediction(train, horizon)
        predictions[TREE_MODEL] = tree_prediction
        for model, prediction in predictions.items():
            residuals.setdefault(model, []).append(actual - prediction)
        validation_rows.append(
            {
                "current_price": train[-1],
                "actual": actual,
                "predictions": predictions,
                "naive_scale": mean(
                    abs(train[index] - train[index - 1])
                    for index in range(1, len(train))
                ),
            }
        )
    if not residuals or any(not values for values in residuals.values()):
        raise ValueError(f"insufficient rolling validation for {horizon}-day forecast")

    point_predictions = _model_predictions(prices, horizon)
    tree_prediction, fitted_tree = _tree_prediction(prices, horizon)
    point_predictions[TREE_MODEL] = tree_prediction
    mae = {
        model: mean(abs(value) for value in model_residuals)
        for model, model_residuals in residuals.items()
    }
    weights, eligible = _ensemble_weights(mae)
    distribution = [
        (
            point_predictions[model] + residual,
            weights[model] / len(model_residuals),
        )
        for model, model_residuals in residuals.items()
        if weights[model] > 0
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
    rolling_performance = _rolling_performance(validation_rows)
    observed_coverage_80 = rolling_performance["interval_coverage_80"]
    if observed_coverage_80 is not None:
        confidence *= min(1.0, observed_coverage_80 / 0.80)
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
        "rolling_point_in_time_performance": rolling_performance,
        "models": {
            model: {
                "point_prediction": round(point_predictions[model], 6),
                "rolling_mae": round(mae[model], 6),
                "ensemble_weight": round(weights[model], 6),
                "validation_observations": len(residuals[model]),
                "model_family": (
                    "regression_tree"
                    if model == TREE_MODEL
                    else "transparent_baseline"
                ),
                "ensemble_eligible": eligible[model],
                "eligibility_rule": (
                    "rolling MAE must be strictly lower than every transparent baseline"
                    if model == TREE_MODEL
                    else "transparent benchmark model"
                ),
                **(
                    {
                        "feature_names": list(TREE_FEATURE_NAMES),
                        "maximum_depth": TREE_MAX_DEPTH,
                        "minimum_leaf_observations": TREE_MIN_LEAF,
                        "root_split_feature": (
                            TREE_FEATURE_NAMES[fitted_tree.feature_index]
                            if fitted_tree.feature_index is not None
                            else None
                        ),
                    }
                    if model == TREE_MODEL
                    else {}
                ),
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
        "performance_registry": {
            "schema_version": "1.0",
            "methodology_version": PERFORMANCE_VERSION,
            "contract_symbol": base.instrument.symbol,
            "as_of": base.as_of.isoformat(),
            "horizons": [
                {
                    "horizon_trading_days": item["horizon_trading_days"],
                    **item["rolling_point_in_time_performance"],
                }
                for item in forecasts
            ],
        },
        "scenarios": scenarios,
        "limitations": [
            "Price-only baseline and regression-tree candidates; official fundamentals and weather are not model features.",
            "Prediction intervals use rolling historical residuals from this exact delivery contract.",
            "The regression tree receives ensemble weight only when its rolling MAE strictly beats every transparent baseline.",
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
        performance = item["rolling_point_in_time_performance"]
        for suffix, metric, value, unit in (
            (
                "rolling_mae",
                "forecast_rolling_mean_absolute_error",
                performance["mean_absolute_error"],
                history["price_unit"],
            ),
            (
                "rolling_mase",
                "forecast_rolling_mean_absolute_scaled_error",
                performance["mean_absolute_scaled_error"],
                "ratio",
            ),
            (
                "directional_accuracy",
                "forecast_rolling_directional_accuracy",
                performance["directional_accuracy"],
                "proportion",
            ),
            (
                "coverage_50",
                "forecast_rolling_interval_coverage_50",
                performance["interval_coverage_50"],
                "proportion",
            ),
            (
                "coverage_80",
                "forecast_rolling_interval_coverage_80",
                performance["interval_coverage_80"],
                "proportion",
            ),
            (
                "quantile_loss",
                "forecast_rolling_mean_quantile_loss",
                performance["mean_quantile_loss"],
                history["price_unit"],
            ),
        ):
            if value is None:
                continue
            facts.append(
                {
                    "fact_id": f"fact_forecast_{horizon}d_{suffix}",
                    "metric": metric,
                    "value": value,
                    "unit": unit,
                    "observed_at": base.as_of.date().isoformat(),
                    "available_at": base.as_of.isoformat(),
                    "source_id": source_id,
                    "derivation": PERFORMANCE_VERSION,
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
    warnings = list(base.quality.warnings)
    for item in forecasts:
        performance = item["rolling_point_in_time_performance"]
        coverage = performance["interval_coverage_80"]
        if coverage is not None and coverage < 0.70:
            warnings.append(
                f"{item['horizon_trading_days']}-day rolling 80% interval "
                f"coverage is only {coverage:.1%}; forecast confidence was "
                "penalized for under-coverage."
            )
    quality = EvidenceQuality(
        status="forecast_baseline_ready_publication_blocked",
        missing_core_data=missing,
        stale_sources=base.quality.stale_sources,
        contradictions=base.quality.contradictions,
        warnings=tuple(warnings),
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
    "PERFORMANCE_VERSION",
    "build_quantitative_forecast",
]
