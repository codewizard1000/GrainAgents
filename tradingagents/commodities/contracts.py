"""Typed, delivery-specific grain futures contract metadata.

Continuous symbols are intentionally absent from this registry. GrainAgents
uses them only for separately labelled long-horizon context; every forecast and
publishable price target must resolve to a delivery-specific contract.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from enum import Enum


class Commodity(str, Enum):
    CORN = "corn"
    SOYBEANS = "soybeans"
    WHEAT_SRW = "wheat_srw"


class ContractError(ValueError):
    """Base error for an invalid commodity-futures request."""


class InvalidContractError(ContractError):
    """The contract symbol is malformed or contradicts the requested commodity."""


class UnsupportedContractError(ContractError):
    """The root or delivery month is outside the GrainAgents registry."""


class ContractExpiredError(ContractError):
    """The requested contract had expired by the point-in-time analysis date."""


@dataclass(frozen=True)
class CommoditySpec:
    commodity: Commodity
    name: str
    root: str
    exchange: str
    delivery_months: tuple[str, ...]
    tick_size: float
    contract_multiplier: int
    unit: str


@dataclass(frozen=True)
class ContractMetadata:
    commodity: Commodity
    commodity_name: str
    exchange: str
    root: str
    symbol: str
    delivery_month_code: str
    delivery_month: int
    delivery_month_name: str
    delivery_year: int
    crop_year: str
    first_notice_date: date
    last_trade_date: date
    days_to_first_notice: int
    days_to_expiration: int
    tick_size: float
    contract_multiplier: int
    unit: str
    old_new_crop: str
    continuous_series_mapping_rule: str
    back_adjustment_rule: str

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["commodity"] = self.commodity.value
        payload["first_notice_date"] = self.first_notice_date.isoformat()
        payload["last_trade_date"] = self.last_trade_date.isoformat()
        return payload


MONTH_CODES = {
    "F": (1, "January"),
    "G": (2, "February"),
    "H": (3, "March"),
    "J": (4, "April"),
    "K": (5, "May"),
    "M": (6, "June"),
    "N": (7, "July"),
    "Q": (8, "August"),
    "U": (9, "September"),
    "V": (10, "October"),
    "X": (11, "November"),
    "Z": (12, "December"),
}

COMMODITY_SPECS = {
    "ZC": CommoditySpec(
        commodity=Commodity.CORN,
        name="Corn",
        root="ZC",
        exchange="CBOT",
        delivery_months=("H", "K", "N", "U", "Z"),
        tick_size=0.0025,
        contract_multiplier=5_000,
        unit="USD_per_bushel",
    ),
    "ZS": CommoditySpec(
        commodity=Commodity.SOYBEANS,
        name="Soybeans",
        root="ZS",
        exchange="CBOT",
        delivery_months=("F", "H", "K", "N", "Q", "U", "X"),
        tick_size=0.0025,
        contract_multiplier=5_000,
        unit="USD_per_bushel",
    ),
    "ZW": CommoditySpec(
        commodity=Commodity.WHEAT_SRW,
        name="Chicago SRW Wheat",
        root="ZW",
        exchange="CBOT",
        delivery_months=("H", "K", "N", "U", "Z"),
        tick_size=0.0025,
        contract_multiplier=5_000,
        unit="USD_per_bushel",
    ),
}

_COMMODITY_ALIASES = {
    "corn": Commodity.CORN,
    "zc": Commodity.CORN,
    "soybean": Commodity.SOYBEANS,
    "soybeans": Commodity.SOYBEANS,
    "zs": Commodity.SOYBEANS,
    "wheat": Commodity.WHEAT_SRW,
    "wheat_srw": Commodity.WHEAT_SRW,
    "chicago_wheat": Commodity.WHEAT_SRW,
    "chicago_srw_wheat": Commodity.WHEAT_SRW,
    "zw": Commodity.WHEAT_SRW,
}

_CONTRACT_PATTERN = re.compile(r"^(ZC|ZS|ZW)([FGHJKMNQUVXZ])(\d{2}|\d{4})$")


def normalize_commodity(value: str | Commodity) -> Commodity:
    if isinstance(value, Commodity):
        return value
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return _COMMODITY_ALIASES[normalized]
    except KeyError as exc:
        supported = ", ".join(item.value for item in Commodity)
        raise UnsupportedContractError(
            f"Unsupported commodity {value!r}; supported commodities: {supported}"
        ) from exc


def _coerce_date(value: str | date | datetime | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InvalidContractError(
            f"Invalid as-of date {value!r}; expected YYYY-MM-DD"
        ) from exc


def _previous_business_day(value: date) -> date:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _crop_year(spec: CommoditySpec, delivery_year: int, month_code: str) -> str:
    if spec.commodity == Commodity.CORN:
        start_year = delivery_year if month_code in {"U", "Z"} else delivery_year - 1
    elif spec.commodity == Commodity.SOYBEANS:
        start_year = delivery_year if month_code in {"Q", "U", "X"} else delivery_year - 1
    else:
        start_year = delivery_year if month_code in {"N", "U", "Z"} else delivery_year - 1
    return f"{start_year}/{str(start_year + 1)[-2:]}"


def _crop_designation(spec: CommoditySpec, month_code: str) -> str:
    if spec.commodity == Commodity.CORN:
        return "new_crop" if month_code in {"U", "Z"} else "old_crop"
    if spec.commodity == Commodity.SOYBEANS:
        return "new_crop" if month_code in {"Q", "U", "X"} else "old_crop"
    return "new_crop" if month_code in {"N", "U", "Z"} else "old_crop"


def resolve_contract(
    symbol: str,
    *,
    as_of: str | date | datetime | None = None,
    expected_commodity: str | Commodity | None = None,
    reject_expired: bool = True,
) -> ContractMetadata:
    """Resolve a delivery-specific CBOT grain contract without network access."""
    normalized = str(symbol).strip().upper()
    match = _CONTRACT_PATTERN.fullmatch(normalized)
    if match is None:
        raise InvalidContractError(
            f"Invalid contract {symbol!r}; expected a delivery-specific symbol such as ZCZ26"
        )

    root, month_code, year_text = match.groups()
    spec = COMMODITY_SPECS[root]
    if month_code not in spec.delivery_months:
        allowed = ", ".join(spec.delivery_months)
        raise UnsupportedContractError(
            f"{normalized} uses delivery month {month_code}, but {root} supports: {allowed}"
        )

    if expected_commodity is not None:
        expected = normalize_commodity(expected_commodity)
        if expected != spec.commodity:
            raise InvalidContractError(
                f"Contract {normalized} is {spec.commodity.value}, not {expected.value}"
            )

    delivery_year = int(year_text)
    if len(year_text) == 2:
        delivery_year += 2000
    if delivery_year < 2000 or delivery_year > 2099:
        raise UnsupportedContractError(
            f"Contract year {delivery_year} is outside the supported 2000-2099 range"
        )

    delivery_month, delivery_name = MONTH_CODES[month_code]
    first_notice = _previous_business_day(date(delivery_year, delivery_month, 1))
    last_trade = _previous_business_day(date(delivery_year, delivery_month, 15))
    analysis_date = _coerce_date(as_of)
    if reject_expired and analysis_date > last_trade:
        raise ContractExpiredError(
            f"Contract {normalized} expired on {last_trade.isoformat()} "
            f"and cannot be analyzed as of {analysis_date.isoformat()}"
        )

    return ContractMetadata(
        commodity=spec.commodity,
        commodity_name=spec.name,
        exchange=spec.exchange,
        root=spec.root,
        symbol=f"{root}{month_code}{str(delivery_year)[-2:]}",
        delivery_month_code=month_code,
        delivery_month=delivery_month,
        delivery_month_name=delivery_name,
        delivery_year=delivery_year,
        crop_year=_crop_year(spec, delivery_year, month_code),
        first_notice_date=first_notice,
        last_trade_date=last_trade,
        days_to_first_notice=(first_notice - analysis_date).days,
        days_to_expiration=(last_trade - analysis_date).days,
        tick_size=spec.tick_size,
        contract_multiplier=spec.contract_multiplier,
        unit=spec.unit,
        old_new_crop=_crop_designation(spec, month_code),
        continuous_series_mapping_rule=(
            f"{root} continuous history is context-only and must never replace {normalized}"
        ),
        back_adjustment_rule="none_for_specific_contract",
    )
