"""Deterministic evidence-linked Markdown for commodity technical analysis."""

from __future__ import annotations

from typing import Any


def _number(value: Any, digits: int = 4) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, int):
        return f"{value:,}"
    return f"{float(value):,.{digits}f}"


def _fact_ref(symbol: str, suffix: str) -> str:
    return f"`fact_{symbol.lower()}_{suffix}`"


def _citation(value: Any, reference: str) -> str:
    return reference if value is not None else "unavailable"


def render_technical_report(evidence: dict[str, Any]) -> str:
    """Render a numerical report whose claims cite deterministic evidence IDs."""
    instrument = evidence["instrument"]
    market = evidence["market"]
    technical = evidence["technical"]
    curve = evidence["curve"]
    symbol = instrument["symbol"]
    latest = technical["latest"]
    price = latest["settlement"]
    price_fact = _fact_ref(symbol, "settlement")
    price_label = "Official settlement"
    if price is None:
        price = latest["close"]
        price_fact = _fact_ref(symbol, "close")
        price_label = "Daily close (settlement unavailable)"

    ma_rows = "\n".join(
        f"| {period}-day | {_number(value)} | "
        f"{_citation(value, _fact_ref(symbol, f'sma_{period}'))} |"
        for period, value in technical["moving_averages"].items()
    )
    return_rows = "\n".join(
        f"| {period} trading day(s) | {_number(value, 2)}% | "
        f"{_citation(value, _fact_ref(symbol, f'return_{period}d'))} |"
        for period, value in technical["returns_percent"].items()
    )
    curve_rows = "\n".join(
        "| {contract} | {delivery} {year} | {date} | {price} | `{fact}` |".format(
            contract=point["contract_symbol"],
            delivery=point["delivery_month"],
            year=point["delivery_year"],
            date=point["observation_date"],
            price=_number(point["curve_price"]),
            fact=f"fact_curve_{point['contract_symbol'].lower()}",
        )
        for point in curve["points"]
    )
    missing = ", ".join(evidence["quality"]["missing_core_data"])
    technical_status = technical["quality"]["status"]
    structure = curve["structure"]
    structure_fact = (
        f"`fact_curve_{symbol.lower()}_front_to_back`"
        if structure["front_to_back_spread"] is not None
        else "unavailable"
    )

    return f"""# {symbol} deterministic technical report

**Market-data status:** {technical_status}

**Publication status:** blocked pending fundamental, weather, positioning, and forecast evidence

**As of:** {evidence["as_of"]}

**License scope:** `{market["license_scope"]}`

## Contract identity

This report analyzes the explicit {instrument["delivery_month_name"]}
{instrument["delivery_year"]} {instrument["commodity_name"]} contract
`{symbol}`. It does not substitute a continuous futures series.

| Field | Value |
|---|---|
| Exchange | {instrument["exchange"]} |
| Crop year | {instrument["crop_year"]} |
| First notice | {instrument["first_notice_date"]} ({_fact_ref(symbol, "first_notice_date")}) |
| Last trade | {instrument["last_trade_date"]} ({_fact_ref(symbol, "last_trade_date")}) |
| Days to first notice | {instrument["days_to_first_notice"]} ({_fact_ref(symbol, "days_to_first_notice")}) |
| Days to expiration | {instrument["days_to_expiration"]} ({_fact_ref(symbol, "days_to_expiration")}) |
| Observations | {technical["bar_count"]} |
| History window | {technical["first_bar_date"]} through {technical["latest_bar_date"]} |

## Latest market observation

| Metric | Value | Evidence |
|---|---:|---|
| {price_label} | {_number(price)} USD/bu | {price_fact} |
| Bar volume | {_number(latest["volume"], 0)} contracts | {_citation(latest["volume"], _fact_ref(symbol, "volume"))} |
| Open interest ({latest["open_interest_date"] or "date unavailable"}) | {_number(latest["open_interest"], 0)} contracts | {_citation(latest["open_interest"], _fact_ref(symbol, "open_interest"))} |
| Five-day volume change | {_number(technical["participation"]["volume_change_5"], 0)} contracts | {_citation(technical["participation"]["volume_change_5"], _fact_ref(symbol, "volume_change_5"))} |
| Five-day open-interest change | {_number(technical["participation"]["open_interest_change_5"], 0)} contracts | {_citation(technical["participation"]["open_interest_change_5"], _fact_ref(symbol, "open_interest_change_5"))} |

## Price changes

| Horizon | Change | Evidence |
|---|---:|---|
{return_rows}

## Trend

The latest analysis price is classified as
**{technical["trend"]["classification"].replace("_", " ")}**. Its differences
from the 20- and 50-day averages are
{_number(technical["trend"]["price_minus_sma_20"])} and
{_number(technical["trend"]["price_minus_sma_50"])} USD/bu, respectively
({_fact_ref(symbol, "price_minus_sma_20")},
{_fact_ref(symbol, "price_minus_sma_50")}).

| Measure | USD/bu | Evidence |
|---|---:|---|
{ma_rows}

## Momentum and volatility

| Measure | Value | Evidence |
|---|---:|---|
| RSI (14) | {_number(technical["momentum"]["rsi_14"], 2)} | {_fact_ref(symbol, "rsi_14")} |
| MACD (12, 26) | {_number(technical["momentum"]["macd_12_26"])} | {_fact_ref(symbol, "macd_12_26")} |
| MACD signal (9) | {_number(technical["momentum"]["macd_signal_9"])} | {_fact_ref(symbol, "macd_signal_9")} |
| Stochastic (14) | {_number(technical["momentum"]["stochastic_14"], 2)} | {_fact_ref(symbol, "stochastic_14")} |
| ATR (14) | {_number(technical["volatility"]["atr_14"])} USD/bu | {_fact_ref(symbol, "atr_14")} |
| Realized volatility (20-day annualized) | {_number(technical["volatility"]["realized_20_annualized_percent"], 2)}% | {_fact_ref(symbol, "realized_20_annualized_percent")} |
| Bollinger upper (20, 2σ) | {_number(technical["volatility"]["bollinger_20_upper"])} USD/bu | {_fact_ref(symbol, "bollinger_20_upper")} |
| Bollinger lower (20, 2σ) | {_number(technical["volatility"]["bollinger_20_lower"])} USD/bu | {_fact_ref(symbol, "bollinger_20_lower")} |
| 20-day observed low | {_number(technical["levels"]["support_20"])} USD/bu | {_fact_ref(symbol, "support_20")} |
| 20-day observed high | {_number(technical["levels"]["resistance_20"])} USD/bu | {_fact_ref(symbol, "resistance_20")} |

## Futures curve

| Contract | Delivery | Observation | USD/bu | Evidence |
|---|---|---|---:|---|
{curve_rows}

The displayed strip is classified as **{structure["classification"] or "unavailable"}**.
The {structure["front_contract"] or "front"}-to-{structure["back_contract"] or "back"}
spread is {_number(structure["front_to_back_spread"])} USD/bu
({structure_fact}).

## Quality and use constraints

- Technical quality status: `{technical_status}`.
- Full-run quality status: `{evidence["quality"]["status"]}`.
- Remaining required evidence: {missing}.
- Provider/data-quality warnings: {"; ".join(evidence["quality"]["warnings"]) or "none"}.
- All market figures above come from `{market["provider"]}` dataset
  `{market["dataset"]}` or deterministic transformations of those records.
- This is an internal testing artifact, not a publishable newsletter and not
  trading advice.
"""


__all__ = ["render_technical_report"]
