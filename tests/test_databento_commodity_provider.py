from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest import mock

import pandas as pd
import pytest

from tradingagents.commodities.providers import CONTRACT_HISTORY_PROVIDERS
from tradingagents.commodities.providers.databento import (
    DatabentoContractHistoryProvider,
    DatabentoNotConfiguredError,
)
from tradingagents.commodities.tools import get_contract_history
from tradingagents.dataflows.errors import NoMarketDataError, VendorNotConfiguredError


class _Store:
    def __init__(self, frame: pd.DataFrame):
        self._frame = frame

    def to_df(self) -> pd.DataFrame:
        return self._frame.copy()


class _FakeDatabentoClient:
    def __init__(
        self,
        frames: dict[str, pd.DataFrame],
        *,
        instrument_ids: tuple[int, ...] = (415887,),
    ):
        self.frames = frames
        self.instrument_ids = instrument_ids
        self.requested_vendor_symbol = ""
        self.symbology = self
        self.timeseries = self

    def resolve(self, **kwargs):
        self.requested_vendor_symbol = kwargs["symbols"]
        return {
            "result": {
                kwargs["symbols"]: [
                    {
                        "d0": kwargs["start_date"],
                        "d1": kwargs["end_date"],
                        "s": str(instrument_id),
                    }
                    for instrument_id in self.instrument_ids
                ]
            }
        }

    def get_range(self, **kwargs):
        self.requested_vendor_symbol = kwargs["symbols"]
        return _Store(self.frames[kwargs["schema"]])


def _frames(vendor_symbol: str = "ZCZ6") -> dict[str, pd.DataFrame]:
    ohlcv_index = pd.DatetimeIndex(
        ["2026-07-28T00:00:00Z", "2026-07-29T00:00:00Z"],
        name="ts_event",
    )
    ohlcv = pd.DataFrame(
        {
            "instrument_id": [415887, 415887],
            "open": [474.75, 481.50],
            "high": [484.25, 483.50],
            "low": [473.50, 470.75],
            "close": [481.00, 471.00],
            "volume": [136684, 146501],
            "symbol": [vendor_symbol, vendor_symbol],
        },
        index=ohlcv_index,
    )

    stats_index = pd.DatetimeIndex(
        [
            "2026-07-29T18:15:16Z",
            "2026-07-29T21:39:07Z",
            "2026-07-29T14:04:37Z",
            "2026-07-29T14:04:38Z",
        ],
        name="ts_recv",
    )
    statistics = pd.DataFrame(
        {
            "ts_ref": pd.to_datetime(
                [
                    "2026-07-29T00:00:00Z",
                    "2026-07-29T00:00:00Z",
                    "2026-07-28T00:00:00Z",
                    "2026-07-28T00:00:00Z",
                ]
            ),
            "stat_type": [3, 3, 6, 9],
            "update_action": [1, 1, 1, 1],
            "price": [471.50, 471.75, float("nan"), float("nan")],
            "quantity": [2147483647, 2147483647, 214852, 776501],
            "symbol": [vendor_symbol] * 4,
        },
        index=stats_index,
    )

    definition_index = pd.DatetimeIndex(
        ["2026-07-29T00:00:00Z"],
        name="ts_recv",
    )
    definition = pd.DataFrame(
        {
            "instrument_id": [415887],
            "raw_symbol": [vendor_symbol],
            "exchange": ["XCBT"],
            "instrument_class": ["F"],
            "activation": pd.to_datetime(["2022-12-14T22:30:00Z"]),
            "expiration": pd.to_datetime(["2026-12-14T18:01:00Z"]),
            "min_price_increment": [0.25],
            "unit_of_measure_qty": [5000.0],
            "unit_of_measure": ["BU"],
            "currency": ["USD"],
        },
        index=definition_index,
    )
    return {
        "ohlcv-1d": ohlcv,
        "statistics": statistics,
        "definition": definition,
    }


@pytest.mark.unit
def test_databento_normalizes_delivery_contract_history():
    client = _FakeDatabentoClient(_frames())
    provider = DatabentoContractHistoryProvider(
        client=client,
        now=lambda: datetime(2026, 7, 30, 12, tzinfo=timezone.utc),
    )

    result = provider.get_contract_history(
        contract_symbol="ZCZ26",
        start_date="2026-07-28",
        end_date="2026-07-29",
    )

    assert result.requested_symbol == "ZCZ26"
    assert result.vendor_symbol == "ZCZ6"
    assert result.data_scope == "delivery_specific"
    assert result.license_scope == "internal_testing_only"
    assert result.price_unit == "USD_per_bushel"
    assert result.definition is not None
    assert result.definition.exchange == "XCBT"
    assert result.definition.tick_size == 0.0025
    assert result.definition.contract_multiplier == 5000.0

    first, latest = result.bars
    assert first.close == 4.81
    assert first.cleared_volume == 214852
    assert first.open_interest == 776501
    assert first.volume == 214852
    assert latest.close == 4.71
    assert latest.settlement == 4.7175
    assert latest.settlement_available_at == "2026-07-29T21:39:07+00:00"
    assert latest.volume == 146501


@pytest.mark.unit
@pytest.mark.parametrize(
    "contract_symbol,vendor_symbol",
    [
        ("ZCZ26", "ZCZ6"),
        ("ZSX26", "ZSX6"),
        ("ZWZ26", "ZWZ6"),
    ],
)
def test_databento_maps_explicit_year_to_cme_raw_symbol(
    contract_symbol,
    vendor_symbol,
):
    client = _FakeDatabentoClient(_frames(vendor_symbol))
    provider = DatabentoContractHistoryProvider(client=client)
    result = provider.get_contract_history(
        contract_symbol=contract_symbol,
        start_date="2026-07-28",
        end_date="2026-07-29",
    )
    assert client.requested_vendor_symbol == vendor_symbol
    assert result.vendor_symbol == vendor_symbol


@pytest.mark.unit
def test_databento_rejects_decade_ambiguous_resolution():
    client = _FakeDatabentoClient(_frames(), instrument_ids=(111, 222))
    provider = DatabentoContractHistoryProvider(client=client)
    with pytest.raises(NoMarketDataError, match="decade-ambiguous"):
        provider.get_contract_history(
            contract_symbol="ZCZ26",
            start_date="2016-01-01",
            end_date="2026-07-29",
        )


@pytest.mark.unit
def test_databento_requires_api_key_without_injected_client(monkeypatch):
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    with pytest.raises(DatabentoNotConfiguredError, match="DATABENTO_API_KEY"):
        DatabentoContractHistoryProvider()


@pytest.mark.unit
def test_contract_history_tool_dispatches_selected_provider(monkeypatch):
    monkeypatch.setenv("GRAIN_DATA_PROVIDER", "databento")

    def fake_provider(**kwargs):
        return {
            "provider": "databento",
            "requested_symbol": kwargs["contract_symbol"],
            "bars": [],
        }

    with mock.patch.dict(
        CONTRACT_HISTORY_PROVIDERS,
        {"databento": fake_provider},
        clear=False,
    ):
        raw = get_contract_history.invoke(
            {
                "contract_symbol": "ZCZ26",
                "start_date": "2026-07-01",
                "end_date": "2026-07-29",
            }
        )

    payload = json.loads(raw)
    assert payload["provider"] == "databento"
    assert payload["requested_symbol"] == "ZCZ26"


@pytest.mark.unit
def test_contract_history_tool_fails_without_provider(monkeypatch):
    monkeypatch.setenv("GRAIN_DATA_PROVIDER", "")
    with pytest.raises(VendorNotConfiguredError, match="No delivery-specific"):
        get_contract_history.invoke(
            {
                "contract_symbol": "ZCZ26",
                "start_date": "2026-07-01",
                "end_date": "2026-07-29",
            }
        )
