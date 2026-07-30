"""Commodity tools exposed to the Milestone 1 technical analyst."""

from __future__ import annotations

import json
import os
from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import VendorNotConfiguredError

from .contracts import resolve_contract
from .providers import load_contract_history


@tool
def get_futures_contract(
    contract_symbol: Annotated[str, "Delivery-specific futures symbol, for example ZCZ26"],
    as_of: Annotated[str, "Point-in-time analysis date in YYYY-MM-DD format"],
) -> str:
    """Return deterministic metadata for one delivery-specific grain contract."""
    metadata = resolve_contract(contract_symbol, as_of=as_of)
    return json.dumps(metadata.to_dict(), sort_keys=True)


@tool
def get_contract_history(
    contract_symbol: Annotated[str, "Delivery-specific futures symbol, for example ZCZ26"],
    start_date: Annotated[str, "Start date in YYYY-MM-DD format"],
    end_date: Annotated[str, "End date in YYYY-MM-DD format"],
) -> str:
    """Retrieve delivery-specific futures history from a configured provider.

    The selected adapter must return the exact delivery contract. Continuous
    history is never substituted at this boundary.
    """
    resolve_contract(contract_symbol, as_of=end_date, reject_expired=False)
    config = get_config()
    vendors = config.get("commodity_data_vendors", {})
    if "GRAIN_DATA_PROVIDER" in os.environ:
        provider = os.environ["GRAIN_DATA_PROVIDER"].strip()
    else:
        provider = str(vendors.get("contract_history", "")).strip()
    if not provider:
        raise VendorNotConfiguredError(
            "No delivery-specific commodity market-data provider is configured "
            "for get_contract_history"
        )
    payload = load_contract_history(
        provider,
        contract_symbol=contract_symbol,
        start_date=start_date,
        end_date=end_date,
    )
    return json.dumps(payload, sort_keys=True)
