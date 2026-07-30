from dataclasses import FrozenInstanceError

import pytest

from tradingagents.commodities.contracts import (
    Commodity,
    ContractExpiredError,
    InvalidContractError,
    UnsupportedContractError,
    resolve_contract,
)


@pytest.mark.unit
def test_resolves_december_2026_corn_contract():
    contract = resolve_contract("zcz26", as_of="2026-07-30", expected_commodity="corn")

    assert contract.symbol == "ZCZ26"
    assert contract.commodity == Commodity.CORN
    assert contract.delivery_month_name == "December"
    assert contract.delivery_year == 2026
    assert contract.crop_year == "2026/27"
    assert contract.first_notice_date.isoformat() == "2026-11-30"
    assert contract.last_trade_date.isoformat() == "2026-12-14"
    assert contract.days_to_first_notice == 123
    assert contract.days_to_expiration == 137
    assert contract.old_new_crop == "new_crop"


@pytest.mark.unit
def test_contract_metadata_is_frozen():
    contract = resolve_contract("ZCZ26", as_of="2026-07-30")
    with pytest.raises(FrozenInstanceError):
        contract.symbol = "ZCH27"


@pytest.mark.unit
def test_rejects_malformed_and_unsupported_contracts():
    with pytest.raises(InvalidContractError, match="delivery-specific"):
        resolve_contract("ZC=F", as_of="2026-07-30")
    with pytest.raises(UnsupportedContractError, match="supports"):
        resolve_contract("ZCF26", as_of="2026-07-30")


@pytest.mark.unit
def test_rejects_commodity_mismatch():
    with pytest.raises(InvalidContractError, match="not soybeans"):
        resolve_contract("ZCZ26", as_of="2026-07-30", expected_commodity="soybeans")


@pytest.mark.unit
def test_expired_contract_fails_loudly():
    with pytest.raises(ContractExpiredError, match="expired"):
        resolve_contract("ZCZ25", as_of="2026-07-30")


@pytest.mark.unit
def test_supported_initial_soybean_and_wheat_contracts():
    soybean = resolve_contract("ZSX26", as_of="2026-07-30")
    wheat = resolve_contract("ZWZ26", as_of="2026-07-30")
    assert soybean.commodity == Commodity.SOYBEANS
    assert soybean.crop_year == "2026/27"
    assert wheat.commodity == Commodity.WHEAT_SRW
    assert wheat.crop_year == "2026/27"
