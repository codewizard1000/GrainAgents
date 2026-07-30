"""Commodity tools exposed to the Milestone 1 technical analyst."""

from __future__ import annotations

import json
from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import VendorNotConfiguredError

from .contracts import resolve_contract


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

    Milestone 1 defines the provider boundary but deliberately ships no
    redistribution-sensitive market-data adapter. It fails loudly until one is
    configured in a later milestone.
    """
    resolve_contract(contract_symbol, as_of=end_date, reject_expired=False)
    vendors = get_config().get("commodity_data_vendors", {})
    provider = vendors.get("contract_history")
    if not provider:
        raise VendorNotConfiguredError(
            "No delivery-specific commodity market-data provider is configured "
            "for get_contract_history"
        )
    raise VendorNotConfiguredError(
        f"Commodity market-data provider {provider!r} is configured but its adapter "
        "is not implemented in Milestone 1"
    )
