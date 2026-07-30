"""Replaceable market-data providers for delivery-specific commodity contracts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tradingagents.dataflows.errors import VendorNotConfiguredError

from .databento import get_databento_contract_history

ContractHistoryProvider = Callable[..., dict[str, Any]]

CONTRACT_HISTORY_PROVIDERS: dict[str, ContractHistoryProvider] = {
    "databento": get_databento_contract_history,
}


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


__all__ = [
    "CONTRACT_HISTORY_PROVIDERS",
    "get_databento_contract_history",
    "load_contract_history",
]
