"""Deterministic technical metrics for delivery-specific grain contracts."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import pandas as pd

REQUIRED_HISTORY_BARS = 200
MAX_STALE_CALENDAR_DAYS = 4


def _optional_number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return round(number, 8)


def _latest_number(series: pd.Series) -> float | None:
    if series.empty:
        return None
    return _optional_number(series.iloc[-1])


def _percent_change(prices: pd.Series, periods: int) -> float | None:
    if len(prices) <= periods:
        return None
    prior = float(prices.iloc[-periods - 1])
    if prior == 0:
        return None
    return round((float(prices.iloc[-1]) / prior - 1.0) * 100.0, 8)


def _absolute_change(values: pd.Series, periods: int) -> int | None:
    valid = values.dropna()
    if len(valid) <= periods:
        return None
    return int(valid.iloc[-1] - valid.iloc[-periods - 1])


def _rsi(prices: pd.Series, periods: int = 14) -> float | None:
    if len(prices) <= periods:
        return None
    delta = prices.diff()
    average_gain = delta.clip(lower=0).rolling(periods).mean().iloc[-1]
    average_loss = (-delta.clip(upper=0)).rolling(periods).mean().iloc[-1]
    if pd.isna(average_gain) or pd.isna(average_loss):
        return None
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    return round(100.0 - 100.0 / (1.0 + average_gain / average_loss), 8)


def calculate_technical_metrics(
    history: dict[str, Any],
    *,
    as_of: str | date,
) -> dict[str, Any]:
    """Calculate point-in-time metrics from one exact delivery contract."""
    as_of_date = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    frame = pd.DataFrame(history.get("bars", []))
    if frame.empty:
        raise ValueError("Technical analysis requires at least one market-data bar")
    if history.get("data_scope") != "delivery_specific":
        raise ValueError("Technical analysis requires delivery-specific market data")

    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    frame = frame[frame["date"] <= as_of_date].sort_values("date")
    for column in (
        "open",
        "high",
        "low",
        "close",
        "settlement",
        "volume",
        "bar_volume",
        "cleared_volume",
        "open_interest",
    ):
        frame[column] = pd.to_numeric(frame.get(column), errors="coerce")
    frame["analysis_price"] = frame["settlement"].combine_first(frame["close"])
    frame["analysis_volume"] = frame["bar_volume"].combine_first(frame["volume"])
    frame = frame.dropna(subset=["analysis_price"]).reset_index(drop=True)
    if frame.empty:
        raise ValueError("Technical analysis requires a close or settlement price")

    prices = frame["analysis_price"].astype(float)
    high = frame["high"].combine_first(prices).astype(float)
    low = frame["low"].combine_first(prices).astype(float)
    previous = prices.shift(1)
    true_range = pd.concat(
        [(high - low), (high - previous).abs(), (low - previous).abs()],
        axis=1,
    ).max(axis=1)

    moving_averages = {
        str(window): (
            _latest_number(prices.rolling(window).mean())
            if len(prices) >= window
            else None
        )
        for window in (5, 20, 50, 100, 200)
    }
    ema_12 = prices.ewm(span=12, adjust=False).mean()
    ema_26 = prices.ewm(span=26, adjust=False).mean()
    macd_series = ema_12 - ema_26
    macd_signal = macd_series.ewm(span=9, adjust=False).mean()
    rolling_20 = prices.rolling(20)
    band_mid = _latest_number(rolling_20.mean()) if len(prices) >= 20 else None
    band_std = _latest_number(rolling_20.std(ddof=0)) if len(prices) >= 20 else None

    latest = frame.iloc[-1]
    latest_date = latest["date"]
    stale_days = (as_of_date - latest_date).days
    latest_settlement = _optional_number(latest.get("settlement"))
    available_open_interest = frame.dropna(subset=["open_interest"])
    latest_open_interest_row = (
        available_open_interest.iloc[-1] if not available_open_interest.empty else None
    )
    open_interest_stale_days = (
        (as_of_date - latest_open_interest_row["date"]).days
        if latest_open_interest_row is not None
        else None
    )
    settled_latest = latest_settlement is not None
    missing: list[str] = []
    if len(frame) < REQUIRED_HISTORY_BARS:
        missing.append(f"at_least_{REQUIRED_HISTORY_BARS}_daily_bars")
    if stale_days > MAX_STALE_CALENDAR_DAYS:
        missing.append("recent_market_bar")
    if not settled_latest:
        missing.append("latest_official_settlement")
    if (
        latest_open_interest_row is None
        or open_interest_stale_days is None
        or open_interest_stale_days > MAX_STALE_CALENDAR_DAYS
    ):
        missing.append("recent_open_interest")

    low_14 = low.rolling(14).min()
    high_14 = high.rolling(14).max()
    stochastic_denominator = high_14 - low_14
    stochastic = (prices - low_14) / stochastic_denominator * 100.0
    returns = prices.pct_change()
    latest_price = float(prices.iloc[-1])
    sma_20 = moving_averages["20"]
    sma_50 = moving_averages["50"]
    if sma_20 is not None and sma_50 is not None:
        if latest_price > sma_20 and latest_price > sma_50:
            trend_classification = "above_20_and_50_day_averages"
        elif latest_price < sma_20 and latest_price < sma_50:
            trend_classification = "below_20_and_50_day_averages"
        else:
            trend_classification = "mixed_versus_20_and_50_day_averages"
    else:
        trend_classification = "insufficient_history"

    return {
        "methodology_version": "delivery-contract-technicals-v1",
        "contract_symbol": history["requested_symbol"],
        "price_unit": history["price_unit"],
        "price_field": "settlement_with_close_fallback",
        "bar_count": len(frame),
        "first_bar_date": frame.iloc[0]["date"].isoformat(),
        "latest_bar_date": latest_date.isoformat(),
        "latest_bar_staleness_calendar_days": stale_days,
        "latest_settlement_is_official": settled_latest,
        "latest": {
            "settlement": latest_settlement,
            "close": _optional_number(latest.get("close")),
            "analysis_price": _optional_number(latest.get("analysis_price")),
            "volume": (
                int(latest["analysis_volume"])
                if not pd.isna(latest.get("analysis_volume"))
                else None
            ),
            "open_interest": (
                int(latest_open_interest_row["open_interest"])
                if latest_open_interest_row is not None
                else None
            ),
            "open_interest_date": (
                latest_open_interest_row["date"].isoformat()
                if latest_open_interest_row is not None
                else None
            ),
            "open_interest_staleness_calendar_days": open_interest_stale_days,
        },
        "returns_percent": {
            str(period): _percent_change(prices, period)
            for period in (1, 5, 20, 60)
        },
        "moving_averages": moving_averages,
        "trend": {
            "classification": trend_classification,
            "price_minus_sma_20": (
                round(latest_price - sma_20, 8) if sma_20 is not None else None
            ),
            "price_minus_sma_50": (
                round(latest_price - sma_50, 8) if sma_50 is not None else None
            ),
        },
        "participation": {
            "volume_change_5": _absolute_change(frame["analysis_volume"], 5),
            "open_interest_change_5": _absolute_change(frame["open_interest"], 5),
        },
        "momentum": {
            "rsi_14": _rsi(prices),
            "macd_12_26": _latest_number(macd_series) if len(prices) >= 26 else None,
            "macd_signal_9": (
                _latest_number(macd_signal) if len(prices) >= 34 else None
            ),
            "stochastic_14": (
                _latest_number(stochastic) if len(prices) >= 14 else None
            ),
        },
        "volatility": {
            "atr_14": (
                _latest_number(true_range.rolling(14).mean())
                if len(prices) >= 14
                else None
            ),
            "realized_20_annualized_percent": (
                _optional_number(returns.rolling(20).std(ddof=1).iloc[-1] * math.sqrt(252) * 100)
                if len(prices) >= 21
                else None
            ),
            "bollinger_20_mid": band_mid,
            "bollinger_20_upper": (
                round(band_mid + 2 * band_std, 8)
                if band_mid is not None and band_std is not None
                else None
            ),
            "bollinger_20_lower": (
                round(band_mid - 2 * band_std, 8)
                if band_mid is not None and band_std is not None
                else None
            ),
        },
        "levels": {
            "support_20": (
                _latest_number(low.rolling(20).min()) if len(prices) >= 20 else None
            ),
            "resistance_20": (
                _latest_number(high.rolling(20).max()) if len(prices) >= 20 else None
            ),
        },
        "quality": {
            "status": "ready" if not missing else "blocked",
            "missing": missing,
        },
    }


__all__ = [
    "MAX_STALE_CALENDAR_DAYS",
    "REQUIRED_HISTORY_BARS",
    "calculate_technical_metrics",
]
