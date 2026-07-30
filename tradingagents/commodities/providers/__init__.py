"""Replaceable market-data providers for delivery-specific commodity contracts."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import VendorNotConfiguredError

from .databento import get_databento_contract_history

ContractHistoryProvider = Callable[..., dict[str, Any]]

CONTRACT_HISTORY_PROVIDERS: dict[str, ContractHistoryProvider] = {
    "databento": get_databento_contract_history,
}


def configured_contract_history_provider() -> str:
    """Return the explicitly configured delivery-contract provider."""
    if "GRAIN_DATA_PROVIDER" in os.environ:
        provider = os.environ["GRAIN_DATA_PROVIDER"].strip()
    else:
        vendors = get_config().get("commodity_data_vendors", {})
        provider = str(vendors.get("contract_history", "")).strip()
    if not provider:
        raise VendorNotConfiguredError(
            "No delivery-specific commodity market-data provider is configured"
        )
    return provider


def load_contract_history(
    provider: str,
    *,
    contract_symbol: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """Load contract history through the explicitly selected provider."""
    normalized = provider.strip().lower()
    try:
        implementation = CONTRACT_HISTORY_PROVIDERS[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(CONTRACT_HISTORY_PROVIDERS))
        raise VendorNotConfiguredError(
            f"Unsupported commodity market-data provider {provider!r}; "
            f"supported providers: {supported}"
        ) from exc
    return implementation(
        contract_symbol=contract_symbol,
        start_date=start_date,
        end_date=end_date,
    )


def load_configured_contract_history(
    *,
    contract_symbol: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """Load exact-contract history through the configured provider."""
    return load_contract_history(
        configured_contract_history_provider(),
        contract_symbol=contract_symbol,
        start_date=start_date,
        end_date=end_date,
    )


__all__ = [
    "CONTRACT_HISTORY_PROVIDERS",
    "configured_contract_history_provider",
    "get_databento_contract_history",
    "load_configured_contract_history",
    "load_contract_history",
]
