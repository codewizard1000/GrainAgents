"""Databento adapter for delivery-specific CBOT grain futures.

Databento's CME raw symbols use a one-digit delivery year (``ZCZ6``), while
GrainAgents' public contract identity is explicit (``ZCZ26``). The adapter
resolves that vendor symbol over the requested point-in-time range before any
billable data request and rejects ambiguous decade-spanning resolutions.
"""

from __future__ import annotations

import math
import os
import time
import warnings as python_warnings
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import databento as db
import pandas as pd
from databento.common.error import BentoClientError, BentoError, BentoServerError

from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)

from ..contracts import ContractMetadata, resolve_contract

DATABENTO_DATASET = "GLBX.MDP3"
_SETTLEMENT_PRICE = 3
_CLEARED_VOLUME = 6
_OPEN_INTEREST = 9
_STAT_NEW = 1
_STAT_DELETE = 2
_SUPPLEMENTAL_LOOKBACK_DAYS = 30
_TRANSIENT_HTTP_STATUS = {500, 502, 503, 504}
_MAX_REQUEST_ATTEMPTS = 3


class DatabentoNotConfiguredError(VendorNotConfiguredError):
    """Databento credentials or dataset permissions are unavailable."""


class DatabentoRateLimitError(VendorRateLimitError):
    """Databento throttled the historical request."""


@dataclass(frozen=True)
class DatabentoDefinition:
    vendor_symbol: str
    instrument_id: int
    exchange: str | None
    instrument_class: str | None
    activation: str | None
    expiration: str | None
    tick_size: float | None
    contract_multiplier: float | None
    unit_of_measure: str | None
    currency: str | None


@dataclass(frozen=True)
class ContractHistoryBar:
    date: str
    contract_symbol: str
    vendor_symbol: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    settlement: float | None
    volume: int | None
    bar_volume: int | None
    cleared_volume: int | None
    open_interest: int | None
    bar_ts_event: str
    settlement_available_at: str | None
    cleared_volume_available_at: str | None
    open_interest_available_at: str | None


@dataclass(frozen=True)
class ContractHistoryResult:
    schema_version: str
    provider: str
    dataset: str
    data_scope: str
    license_scope: str
    requested_symbol: str
    vendor_symbol: str
    start_date: str
    end_date: str
    retrieved_at: str
    price_unit: str
    volume_unit: str
    definition: DatabentoDefinition | None
    bars: tuple[ContractHistoryBar, ...]
    source_record_counts: dict[str, int]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["bars"] = [asdict(bar) for bar in self.bars]
        payload["definition"] = (
            asdict(self.definition) if self.definition is not None else None
        )
        payload["warnings"] = list(self.warnings)
        return payload


def _parse_date(value: str, *, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an ISO date in YYYY-MM-DD format") from exc


def _vendor_symbol(contract: ContractMetadata) -> str:
    """Convert GrainAgents' explicit year to CME's one-digit raw year."""
    return (
        f"{contract.root}{contract.delivery_month_code}"
        f"{str(contract.delivery_year)[-1]}"
    )


def _optional_float(value: Any, *, price: bool = False) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if price:
        number /= 100.0
    return round(number, 8)


def _optional_int(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _iso_timestamp(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _translate_error(exc: BentoError, *, symbol: str) -> VendorError:
    status = getattr(exc, "http_status", None)
    if status == 429:
        return DatabentoRateLimitError("Databento rate limit reached")
    if status in {401, 402, 403}:
        return DatabentoNotConfiguredError(
            "Databento rejected the API key or GLBX.MDP3 entitlement"
        )
    if status in {400, 404, 422}:
        return NoMarketDataError(
            symbol,
            detail=f"Databento rejected the contract/date request (HTTP {status})",
        )
    return VendorError(f"Databento request failed for {symbol!r}: {exc}")


class DatabentoContractHistoryProvider:
    """Retrieve normalized daily history for one delivery-specific contract."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: Any | None = None,
        now: Any | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        key = (api_key or os.environ.get("DATABENTO_API_KEY", "")).strip()
        if client is None and not key:
            raise DatabentoNotConfiguredError(
                "DATABENTO_API_KEY is required for the Databento commodity provider"
            )
        self._client = client or db.Historical(key)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep or time.sleep

    def _request_with_retry(
        self,
        operation: Callable[[], Any],
        *,
        requested_symbol: str,
    ) -> Any:
        for attempt in range(_MAX_REQUEST_ATTEMPTS):
            try:
                return operation()
            except BentoServerError as exc:
                status = getattr(exc, "http_status", None)
                if (
                    status not in _TRANSIENT_HTTP_STATUS
                    or attempt == _MAX_REQUEST_ATTEMPTS - 1
                ):
                    raise _translate_error(exc, symbol=requested_symbol) from exc
                self._sleep(2**attempt)
            except (BentoClientError, BentoError) as exc:
                raise _translate_error(exc, symbol=requested_symbol) from exc
        raise AssertionError("Databento retry loop exhausted without a result")

    def _resolve_instrument_ids(
        self,
        *,
        requested_symbol: str,
        vendor_symbol: str,
        start_date: date,
        end_date: date,
    ) -> tuple[int, ...]:
        payload = self._request_with_retry(
            lambda: self._client.symbology.resolve(
                dataset=DATABENTO_DATASET,
                symbols=vendor_symbol,
                stype_in="raw_symbol",
                stype_out="instrument_id",
                start_date=start_date.isoformat(),
                end_date=(end_date + timedelta(days=1)).isoformat(),
            ),
            requested_symbol=requested_symbol,
        )

        mappings = payload.get("result", {}).get(vendor_symbol, [])
        instrument_ids = tuple(
            sorted({int(mapping["s"]) for mapping in mappings if mapping.get("s")})
        )
        if not instrument_ids:
            raise NoMarketDataError(
                requested_symbol,
                vendor_symbol,
                "Databento did not resolve this delivery contract over the requested dates",
            )
        if len(instrument_ids) != 1:
            raise NoMarketDataError(
                requested_symbol,
                vendor_symbol,
                "the date range resolves to multiple decade-ambiguous CME instruments",
            )
        return instrument_ids

    def _get_frame(
        self,
        *,
        requested_symbol: str,
        vendor_symbol: str,
        schema: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        with python_warnings.catch_warnings(record=True) as caught_warnings:
            python_warnings.simplefilter("always")
            store = self._request_with_retry(
                lambda: self._client.timeseries.get_range(
                    dataset=DATABENTO_DATASET,
                    symbols=vendor_symbol,
                    stype_in="raw_symbol",
                    schema=schema,
                    start=start_date.isoformat(),
                    end=(end_date + timedelta(days=1)).isoformat(),
                ),
                requested_symbol=requested_symbol,
            )
        frame = store.to_df()
        result = frame if isinstance(frame, pd.DataFrame) else pd.DataFrame(frame)
        result.attrs["vendor_warnings"] = tuple(
            str(item.message) for item in caught_warnings
        )
        return result

    @staticmethod
    def _latest_statistics(
        frame: pd.DataFrame,
        *,
        start_date: date,
        end_date: date,
    ) -> dict[tuple[date, int], tuple[pd.Series, Any]]:
        if frame.empty or "ts_ref" not in frame.columns:
            return {}

        latest: dict[tuple[date, int], tuple[pd.Series, Any]] = {}
        ordered = frame.sort_index()
        for ts_recv, row in ordered.iterrows():
            ts_ref = row.get("ts_ref")
            stat_type = _optional_int(row.get("stat_type"))
            action = _optional_int(row.get("update_action"))
            if ts_ref is None or pd.isna(ts_ref) or stat_type is None:
                continue
            reference_date = pd.Timestamp(ts_ref).date()
            if not start_date <= reference_date <= end_date:
                continue
            key = (reference_date, stat_type)
            if action == _STAT_DELETE:
                latest.pop(key, None)
            elif action in {None, _STAT_NEW}:
                latest[key] = (row, ts_recv)
        return latest

    @staticmethod
    def _definition(
        frame: pd.DataFrame,
        *,
        vendor_symbol: str,
        instrument_id: int,
    ) -> DatabentoDefinition | None:
        if frame.empty:
            return None
        row = frame.sort_index().iloc[-1]
        raw_tick = _optional_float(row.get("min_price_increment"), price=True)
        return DatabentoDefinition(
            vendor_symbol=str(row.get("raw_symbol") or vendor_symbol),
            instrument_id=int(row.get("instrument_id") or instrument_id),
            exchange=str(row.get("exchange")) if row.get("exchange") else None,
            instrument_class=(
                str(row.get("instrument_class"))
                if row.get("instrument_class")
                else None
            ),
            activation=_iso_timestamp(row.get("activation")),
            expiration=_iso_timestamp(row.get("expiration")),
            tick_size=raw_tick,
            contract_multiplier=_optional_float(row.get("unit_of_measure_qty")),
            unit_of_measure=(
                str(row.get("unit_of_measure"))
                if row.get("unit_of_measure")
                else None
            ),
            currency=str(row.get("currency")) if row.get("currency") else None,
        )

    def get_contract_history(
        self,
        *,
        contract_symbol: str,
        start_date: str,
        end_date: str,
    ) -> ContractHistoryResult:
        start = _parse_date(start_date, field_name="start_date")
        end = _parse_date(end_date, field_name="end_date")
        if start > end:
            raise ValueError("start_date must be on or before end_date")

        contract = resolve_contract(
            contract_symbol,
            as_of=end,
            reject_expired=False,
        )
        vendor_symbol = _vendor_symbol(contract)
        instrument_ids = self._resolve_instrument_ids(
            requested_symbol=contract.symbol,
            vendor_symbol=vendor_symbol,
            start_date=start,
            end_date=end,
        )

        ohlcv = self._get_frame(
            requested_symbol=contract.symbol,
            vendor_symbol=vendor_symbol,
            schema="ohlcv-1d",
            start_date=start,
            end_date=end,
        )
        supplemental_start = max(
            start,
            end - timedelta(days=_SUPPLEMENTAL_LOOKBACK_DAYS),
        )
        statistics = self._get_frame(
            requested_symbol=contract.symbol,
            vendor_symbol=vendor_symbol,
            schema="statistics",
            start_date=supplemental_start,
            end_date=end,
        )
        definitions = self._get_frame(
            requested_symbol=contract.symbol,
            vendor_symbol=vendor_symbol,
            schema="definition",
            start_date=supplemental_start,
            end_date=end,
        )

        if ohlcv.empty:
            raise NoMarketDataError(
                contract.symbol,
                vendor_symbol,
                "Databento returned no daily bars for this date range",
            )

        latest_stats = self._latest_statistics(
            statistics,
            start_date=start,
            end_date=end,
        )
        bars: list[ContractHistoryBar] = []
        for ts_event, row in ohlcv.sort_index().iterrows():
            bar_date = pd.Timestamp(ts_event).date()
            if not start <= bar_date <= end:
                continue

            settlement = latest_stats.get((bar_date, _SETTLEMENT_PRICE))
            cleared_volume = latest_stats.get((bar_date, _CLEARED_VOLUME))
            open_interest = latest_stats.get((bar_date, _OPEN_INTEREST))
            settlement_row, settlement_ts = settlement or (None, None)
            cleared_row, cleared_ts = cleared_volume or (None, None)
            interest_row, interest_ts = open_interest or (None, None)

            bar_volume = _optional_int(row.get("volume"))
            official_volume = (
                _optional_int(cleared_row.get("quantity"))
                if cleared_row is not None
                else None
            )
            bars.append(
                ContractHistoryBar(
                    date=bar_date.isoformat(),
                    contract_symbol=contract.symbol,
                    vendor_symbol=vendor_symbol,
                    open=_optional_float(row.get("open"), price=True),
                    high=_optional_float(row.get("high"), price=True),
                    low=_optional_float(row.get("low"), price=True),
                    close=_optional_float(row.get("close"), price=True),
                    settlement=(
                        _optional_float(settlement_row.get("price"), price=True)
                        if settlement_row is not None
                        else None
                    ),
                    volume=official_volume if official_volume is not None else bar_volume,
                    bar_volume=bar_volume,
                    cleared_volume=official_volume,
                    open_interest=(
                        _optional_int(interest_row.get("quantity"))
                        if interest_row is not None
                        else None
                    ),
                    bar_ts_event=_iso_timestamp(ts_event) or bar_date.isoformat(),
                    settlement_available_at=_iso_timestamp(settlement_ts),
                    cleared_volume_available_at=_iso_timestamp(cleared_ts),
                    open_interest_available_at=_iso_timestamp(interest_ts),
                )
            )

        if not bars:
            raise NoMarketDataError(
                contract.symbol,
                vendor_symbol,
                "Databento returned no daily bars inside the requested date range",
            )

        warnings: list[str] = list(
            dict.fromkeys(
                warning
                for frame in (ohlcv, statistics, definitions)
                for warning in frame.attrs.get("vendor_warnings", ())
            )
        )
        if any(bar.settlement is None for bar in bars):
            warnings.append(
                "Some dates lack an official settlement available within the "
                "requested point-in-time window."
            )
        if any(bar.open_interest is None for bar in bars):
            warnings.append(
                "Some dates lack open interest available within the requested "
                "point-in-time window."
            )

        return ContractHistoryResult(
            schema_version="1.0",
            provider="databento",
            dataset=DATABENTO_DATASET,
            data_scope="delivery_specific",
            license_scope="internal_testing_only",
            requested_symbol=contract.symbol,
            vendor_symbol=vendor_symbol,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            retrieved_at=self._now().isoformat(),
            price_unit=contract.unit,
            volume_unit="contracts",
            definition=self._definition(
                definitions,
                vendor_symbol=vendor_symbol,
                instrument_id=instrument_ids[0],
            ),
            bars=tuple(bars),
            source_record_counts={
                "ohlcv-1d": len(ohlcv),
                "statistics": len(statistics),
                "definition": len(definitions),
            },
            warnings=tuple(warnings),
        )


def get_databento_contract_history(
    *,
    contract_symbol: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """Functional provider entry point used by the commodity provider registry."""
    return DatabentoContractHistoryProvider().get_contract_history(
        contract_symbol=contract_symbol,
        start_date=start_date,
        end_date=end_date,
    ).to_dict()


__all__ = [
    "ContractHistoryBar",
    "ContractHistoryResult",
    "DATABENTO_DATASET",
    "DatabentoContractHistoryProvider",
    "DatabentoDefinition",
    "DatabentoNotConfiguredError",
    "DatabentoRateLimitError",
    "get_databento_contract_history",
]
