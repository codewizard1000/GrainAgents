"""Deterministic publication-chart generation for commodity runs."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib
import pandas as pd

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

CHART_VERSION = "grain-publication-charts-v1"
COLORS = {
    "navy": "#17324D",
    "blue": "#2E6F9E",
    "green": "#4C956C",
    "gold": "#D7A83E",
    "red": "#C65D57",
    "gray": "#6B7280",
    "light": "#DCE6EE",
}


def _save_chart(
    figure,
    path: Path,
    *,
    symbol: str,
    as_of: str,
    unit: str,
    source: str,
) -> str:
    footer = (
        f"Contract: {symbol}  |  As of: {as_of[:10]}  |  "
        f"Unit: {unit}  |  Source: {source}"
    )
    figure.text(0.01, 0.015, footer, fontsize=7.5, color=COLORS["gray"])
    figure.tight_layout(rect=(0, 0.055, 1, 0.98))
    figure.savefig(
        path,
        dpi=150,
        facecolor="white",
        metadata={
            "Software": f"GrainAgents {CHART_VERSION}",
            "Title": path.stem,
            "Description": footer,
        },
    )
    plt.close(figure)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(
    *,
    filename: str,
    title: str,
    status: str,
    symbol: str,
    as_of: str,
    unit: str,
    sources: list[str],
    evidence_refs: list[str],
    sha256: str,
    limitation: str | None = None,
) -> dict[str, Any]:
    payload = {
        "filename": filename,
        "title": title,
        "status": status,
        "contract_symbol": symbol,
        "as_of": as_of,
        "unit": unit,
        "sources": sources,
        "evidence_refs": evidence_refs,
        "sha256": sha256,
    }
    if limitation:
        payload["limitation"] = limitation
    return payload


def _price_technicals(
    directory: Path,
    evidence: dict[str, Any],
    history: dict[str, Any],
) -> dict[str, Any]:
    symbol = evidence["instrument"]["symbol"]
    frame = pd.DataFrame(history["bars"]).sort_values("date")
    frame["date"] = pd.to_datetime(frame["date"])
    frame["price"] = frame["settlement"].fillna(frame["close"])
    frame["sma_20"] = frame["price"].rolling(20).mean()
    frame["sma_50"] = frame["price"].rolling(50).mean()
    figure, axis = plt.subplots(figsize=(10, 5.6))
    axis.plot(frame["date"], frame["price"], color=COLORS["navy"], linewidth=1.3, label=symbol)
    axis.plot(frame["date"], frame["sma_20"], color=COLORS["gold"], linewidth=1.2, label="20-session average")
    axis.plot(frame["date"], frame["sma_50"], color=COLORS["green"], linewidth=1.2, label="50-session average")
    axis.set_title(f"{symbol} price and moving averages")
    axis.set_ylabel("USD per bushel")
    axis.grid(alpha=0.2)
    axis.legend(frameon=False, ncol=3)
    filename = "price_technicals.png"
    source = f"{history['provider']} {history['dataset']}"
    digest = _save_chart(
        figure,
        directory / filename,
        symbol=symbol,
        as_of=evidence["as_of"],
        unit="USD per bushel",
        source=source,
    )
    return _record(
        filename=filename,
        title=f"{symbol} price and moving averages",
        status="ready",
        symbol=symbol,
        as_of=evidence["as_of"],
        unit="USD per bushel",
        sources=[source],
        evidence_refs=[
            f"fact_{symbol.lower()}_settlement",
            f"fact_{symbol.lower()}_sma_20",
            f"fact_{symbol.lower()}_sma_50",
        ],
        sha256=digest,
    )


def _futures_curve(
    directory: Path,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    symbol = evidence["instrument"]["symbol"]
    points = evidence["curve"]["points"]
    labels = [point["contract_symbol"] for point in points]
    values = [point["curve_price"] for point in points]
    figure, axis = plt.subplots(figsize=(9, 5.2))
    axis.plot(
        labels,
        values,
        color=COLORS["blue"],
        marker="o",
        linewidth=2,
    )
    axis.scatter(
        [symbol],
        [values[labels.index(symbol)]],
        color=COLORS["red"],
        s=65,
        zorder=3,
        label="Primary contract",
    )
    axis.set_title("CBOT corn futures curve")
    axis.set_ylabel("USD per bushel")
    axis.grid(axis="y", alpha=0.2)
    axis.legend(frameon=False)
    filename = "futures_curve.png"
    source = "Databento GLBX.MDP3 settlements"
    digest = _save_chart(
        figure,
        directory / filename,
        symbol=symbol,
        as_of=evidence["as_of"],
        unit="USD per bushel",
        source=source,
    )
    return _record(
        filename=filename,
        title="CBOT corn futures curve",
        status="ready",
        symbol=symbol,
        as_of=evidence["as_of"],
        unit="USD per bushel",
        sources=[source],
        evidence_refs=[
            f"fact_curve_{point['contract_symbol'].lower()}"
            for point in points
        ],
        sha256=digest,
    )


def _cot_positioning(
    directory: Path,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    symbol = evidence["instrument"]["symbol"]
    values = evidence["positioning"]["values"]
    labels = ["Noncommercial long", "Noncommercial short", "Noncommercial net"]
    positions = [
        values["noncommercial_long"],
        -values["noncommercial_short"],
        values["noncommercial_net"],
    ]
    figure, axis = plt.subplots(figsize=(9, 5.2))
    colors = [COLORS["green"], COLORS["red"], COLORS["blue"]]
    axis.bar(labels, positions, color=colors)
    axis.axhline(0, color=COLORS["gray"], linewidth=0.8)
    axis.set_title(
        f"CFTC corn positioning — report date {evidence['positioning']['report_date']}"
    )
    axis.set_ylabel("Contracts; shorts shown negative")
    axis.tick_params(axis="x", rotation=10)
    axis.grid(axis="y", alpha=0.2)
    filename = "cot_positioning.png"
    source = "CFTC Legacy Futures Only, all corn contract months"
    digest = _save_chart(
        figure,
        directory / filename,
        symbol=symbol,
        as_of=evidence["as_of"],
        unit="contracts",
        source=source,
    )
    period = evidence["positioning"]["report_date"].replace("-", "_")
    return _record(
        filename=filename,
        title="CFTC corn noncommercial positioning",
        status="ready",
        symbol=symbol,
        as_of=evidence["as_of"],
        unit="contracts",
        sources=[source],
        evidence_refs=[
            f"fact_cftc_corn_noncommercial_long_{period}",
            f"fact_cftc_corn_noncommercial_short_{period}",
            f"fact_cftc_corn_noncommercial_net_{period}",
        ],
        sha256=digest,
        limitation="CFTC positions aggregate all corn delivery months.",
    )


def _seasonal_comparison(
    directory: Path,
    evidence: dict[str, Any],
    history: dict[str, Any],
) -> dict[str, Any]:
    symbol = evidence["instrument"]["symbol"]
    frame = pd.DataFrame(history["bars"]).sort_values("date")
    frame["date"] = pd.to_datetime(frame["date"])
    frame["price"] = frame["settlement"].fillna(frame["close"])
    indexed = frame["price"] / frame["price"].iloc[0] * 100
    figure, axis = plt.subplots(figsize=(10, 5.2))
    axis.plot(frame["date"], indexed, color=COLORS["navy"], linewidth=1.5)
    axis.axhline(100, color=COLORS["gray"], linewidth=0.8)
    axis.set_title(f"{symbol} indexed history — seasonal comparison unavailable")
    axis.set_ylabel("Index; first observation = 100")
    axis.grid(alpha=0.2)
    axis.text(
        0.01,
        0.04,
        "Only one exact-contract history is available; no multi-year seasonal average is shown.",
        transform=axis.transAxes,
        fontsize=8,
        color=COLORS["red"],
    )
    filename = "seasonal_comparison.png"
    source = f"{history['provider']} {history['dataset']}"
    digest = _save_chart(
        figure,
        directory / filename,
        symbol=symbol,
        as_of=evidence["as_of"],
        unit="index, first observation = 100",
        source=source,
    )
    return _record(
        filename=filename,
        title=f"{symbol} indexed history",
        status="partial",
        symbol=symbol,
        as_of=evidence["as_of"],
        unit="index, first observation = 100",
        sources=[source],
        evidence_refs=[f"fact_{symbol.lower()}_settlement"],
        sha256=digest,
        limitation=(
            "A multi-year, contract-month-aligned seasonal panel is unavailable; "
            "the chart shows only the current exact contract."
        ),
    )


def _forecast_fan(
    directory: Path,
    evidence: dict[str, Any],
    quantitative: dict[str, Any],
) -> dict[str, Any]:
    symbol = evidence["instrument"]["symbol"]
    forecasts = sorted(
        quantitative["forecast_horizons"],
        key=lambda item: item["horizon_trading_days"],
    )
    x = [0, *[item["horizon_trading_days"] for item in forecasts]]
    current = forecasts[0]["current_price"]
    median = [current, *[item["median_projected_price"] for item in forecasts]]
    low_50 = [current, *[item["prediction_interval_50"][0] for item in forecasts]]
    high_50 = [current, *[item["prediction_interval_50"][1] for item in forecasts]]
    low_80 = [current, *[item["prediction_interval_80"][0] for item in forecasts]]
    high_80 = [current, *[item["prediction_interval_80"][1] for item in forecasts]]
    figure, axis = plt.subplots(figsize=(9, 5.5))
    axis.fill_between(x, low_80, high_80, color=COLORS["light"], label="80% calibrated interval")
    axis.fill_between(x, low_50, high_50, color=COLORS["blue"], alpha=0.35, label="50% calibrated interval")
    axis.plot(x, median, color=COLORS["navy"], marker="o", linewidth=2, label="Median")
    axis.set_title(f"{symbol} adaptive-conformal forecast fan")
    axis.set_xlabel("Trading sessions")
    axis.set_ylabel("USD per bushel")
    axis.grid(alpha=0.2)
    axis.legend(frameon=False)
    filename = "forecast_fan.png"
    source = quantitative["model_version"]
    digest = _save_chart(
        figure,
        directory / filename,
        symbol=symbol,
        as_of=evidence["as_of"],
        unit="USD per bushel",
        source=source,
    )
    refs = []
    for item in forecasts:
        horizon = item["horizon_trading_days"]
        refs.extend(
            [
                f"fact_forecast_{horizon}d_median",
                f"fact_forecast_{horizon}d_interval_50",
                f"fact_forecast_{horizon}d_interval_80",
            ]
        )
    return _record(
        filename=filename,
        title=f"{symbol} adaptive-conformal forecast fan",
        status="ready_research_only",
        symbol=symbol,
        as_of=evidence["as_of"],
        unit="USD per bushel",
        sources=[source],
        evidence_refs=refs,
        sha256=digest,
        limitation="Research-only until saved forecasts are scored out of sample.",
    )


def _scenario_probabilities(
    directory: Path,
    evidence: dict[str, Any],
    scenarios: dict[str, Any],
    prior_outlook: dict[str, Any] | None,
) -> dict[str, Any]:
    symbol = evidence["instrument"]["symbol"]
    labels = ["Bull", "Base", "Bear"]
    keys = ["bull", "base", "bear"]
    current = [scenarios["probabilities"][key] * 100 for key in keys]
    figure, axis = plt.subplots(figsize=(8.8, 5.2))
    if prior_outlook is None:
        axis.bar(labels, current, color=[COLORS["green"], COLORS["blue"], COLORS["red"]])
        title = "Current scenario probabilities — no approved prior report"
        status = "current_only"
        limitation = "No approved prior report exists for probability-change comparison."
    else:
        prior = [
            prior_outlook["scenario_probabilities"][key] * 100
            for key in keys
        ]
        positions = range(len(labels))
        axis.bar(
            [position - 0.18 for position in positions],
            prior,
            width=0.36,
            color=COLORS["gray"],
            label="Prior approved",
        )
        axis.bar(
            [position + 0.18 for position in positions],
            current,
            width=0.36,
            color=[COLORS["green"], COLORS["blue"], COLORS["red"]],
            label="Current",
        )
        axis.set_xticks(list(positions), labels)
        axis.legend(frameon=False)
        title = "Scenario-probability change"
        status = "ready"
        limitation = None
    axis.set_title(title)
    axis.set_ylabel("Probability (%)")
    axis.set_ylim(0, 100)
    axis.grid(axis="y", alpha=0.2)
    filename = "scenario_probabilities.png"
    source = scenarios["method"]
    digest = _save_chart(
        figure,
        directory / filename,
        symbol=symbol,
        as_of=evidence["as_of"],
        unit="percent probability",
        source=source,
    )
    return _record(
        filename=filename,
        title=title,
        status=status,
        symbol=symbol,
        as_of=evidence["as_of"],
        unit="percent probability",
        sources=[source],
        evidence_refs=[
            "output_scenario_bull_probability",
            "output_scenario_base_probability",
            "output_scenario_bear_probability",
        ],
        sha256=digest,
        limitation=limitation,
    )


def _weather_drought(
    directory: Path,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    symbol = evidence["instrument"]["symbol"]
    values = evidence["weather"]["values"]
    labels = ["D0+", "D1+", "D2+", "D3+", "D4+"]
    percentages = [
        values["d0_or_worse_percent"],
        values["d1_or_worse_percent"],
        values["d2_or_worse_percent"],
        values["d3_or_worse_percent"],
        values["d4_or_worse_percent"],
    ]
    figure, axis = plt.subplots(figsize=(8.8, 5.2))
    axis.bar(
        labels,
        percentages,
        color=["#E6D690", "#E8B85A", "#D98946", "#C45D3E", "#8E3A36"],
    )
    axis.set_title(
        f"Corn-area drought exposure — valid {values['drought_valid_date']}"
    )
    axis.set_ylabel("Corn area (%)")
    axis.set_ylim(0, 100)
    axis.grid(axis="y", alpha=0.2)
    filename = "weather_drought.png"
    source = "Drought.gov USDA NASS / U.S. Drought Monitor overlay"
    digest = _save_chart(
        figure,
        directory / filename,
        symbol=symbol,
        as_of=evidence["as_of"],
        unit="percent of corn area",
        source=source,
    )
    period = values["drought_valid_date"].replace("-", "_")
    return _record(
        filename=filename,
        title="Corn-area drought exposure",
        status="partial",
        symbol=symbol,
        as_of=evidence["as_of"],
        unit="percent of corn area",
        sources=[source],
        evidence_refs=[
            f"fact_corn_drought_d{level}_or_worse_percent_{period}"
            for level in range(5)
        ],
        sha256=digest,
        limitation="Weather anomalies, 14-day forecasts, and yield impact are missing.",
    )


def _stocks_to_use(
    directory: Path,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    symbol = evidence["instrument"]["symbol"]
    values = evidence["supply_demand"]["values"]
    ratio = values["ending_stocks"] / values["total_use"] * 100
    crop_year = evidence["supply_demand"]["crop_year"]
    figure, axis = plt.subplots(figsize=(7.5, 5.2))
    axis.bar([crop_year], [ratio], color=COLORS["gold"], width=0.5)
    axis.set_title("U.S. corn ending-stocks-to-use ratio")
    axis.set_ylabel("Percent")
    axis.set_ylim(0, max(15, ratio * 1.35))
    axis.grid(axis="y", alpha=0.2)
    axis.text(0, ratio + 0.3, f"{ratio:.1f}%", ha="center")
    filename = "stocks_to_use.png"
    source = "USDA WASDE U.S. corn balance"
    digest = _save_chart(
        figure,
        directory / filename,
        symbol=symbol,
        as_of=evidence["as_of"],
        unit="percent",
        source=source,
    )
    period = crop_year.replace("/", "_")
    return _record(
        filename=filename,
        title="U.S. corn ending-stocks-to-use ratio",
        status="current_vintage_only",
        symbol=symbol,
        as_of=evidence["as_of"],
        unit="percent",
        sources=[source],
        evidence_refs=[
            f"fact_wasde_corn_ending_stocks_{period}",
            f"fact_wasde_corn_total_use_{period}",
        ],
        sha256=digest,
        limitation="Only the current point-in-time WASDE crop-year value is available.",
    )


def generate_publication_charts(
    directory: Path,
    *,
    evidence: dict[str, Any],
    history: dict[str, Any],
    quantitative: dict[str, Any],
    scenarios: dict[str, Any],
    prior_outlook: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate deterministic PNG charts plus auditable metadata."""
    directory.mkdir(parents=True, exist_ok=True)
    artifacts = [
        _price_technicals(directory, evidence, history),
        _futures_curve(directory, evidence),
        _cot_positioning(directory, evidence),
        _seasonal_comparison(directory, evidence, history),
        _forecast_fan(directory, evidence, quantitative),
        _scenario_probabilities(
            directory,
            evidence,
            scenarios,
            prior_outlook,
        ),
        _weather_drought(directory, evidence),
        _stocks_to_use(directory, evidence),
    ]
    allowed_refs = {
        fact["fact_id"]
        for fact in evidence["facts"]
    } | {
        "output_scenario_bull_probability",
        "output_scenario_base_probability",
        "output_scenario_bear_probability",
    }
    unknown_refs = sorted(
        {
            reference
            for artifact in artifacts
            for reference in artifact["evidence_refs"]
            if reference not in allowed_refs
        }
    )
    if unknown_refs:
        raise ValueError(
            "chart metadata contains unknown evidence references: "
            + ", ".join(unknown_refs)
        )
    return {
        "schema_version": "1.0",
        "chart_version": CHART_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(),
        "contract_symbol": evidence["instrument"]["symbol"],
        "as_of": evidence["as_of"],
        "charts": artifacts,
    }


__all__ = ["CHART_VERSION", "generate_publication_charts"]
