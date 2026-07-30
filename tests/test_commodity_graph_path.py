from unittest.mock import MagicMock

import pytest

from cli.models import AnalystType, AssetType
from cli.utils import detect_asset_type, filter_analysts_for_asset_type
from tradingagents.agents.analysts.market_analyst import (
    COMMODITY_TECHNICAL_SYSTEM_MESSAGE,
    get_market_tools,
)
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.setup import GraphSetup
from tradingagents.graph.trading_graph import TradingAgentsGraph


@pytest.mark.unit
def test_detects_delivery_specific_grain_contract():
    assert detect_asset_type("ZCZ26") == AssetType.COMMODITY_FUTURE
    assert detect_asset_type("ZSX26") == AssetType.COMMODITY_FUTURE
    assert detect_asset_type("ZWZ26") == AssetType.COMMODITY_FUTURE


@pytest.mark.unit
def test_commodity_filter_keeps_only_technical_market_analyst():
    analysts = list(AnalystType)
    assert filter_analysts_for_asset_type(
        analysts, AssetType.COMMODITY_FUTURE
    ) == [AnalystType.MARKET]


@pytest.mark.unit
def test_commodity_market_tools_exclude_stock_fundamentals():
    names = {tool.name for tool in get_market_tools("commodity_future")}
    assert names == {"get_futures_contract", "get_contract_history"}
    assert not names & {
        "get_balance_sheet",
        "get_cashflow",
        "get_income_statement",
        "get_insider_transactions",
        "get_stock_data",
    }


@pytest.mark.unit
def test_commodity_tool_node_matches_bound_analyst_tools():
    graph = object.__new__(TradingAgentsGraph)
    graph.asset_type = "commodity_future"
    nodes = graph._create_tool_nodes()
    assert set(nodes["market"].tools_by_name) == {
        "get_futures_contract",
        "get_contract_history",
    }


@pytest.mark.unit
def test_commodity_prompt_prohibits_stock_semantics():
    prompt = COMMODITY_TECHNICAL_SYSTEM_MESSAGE.lower()
    assert "company financial statements" in prompt
    assert "buy/hold/sell" in prompt
    assert "never substitute" in prompt
    assert "continuous series" in prompt
    assert "do not invent" in prompt


@pytest.mark.unit
def test_commodity_graph_stops_after_technical_analyst():
    graph = object.__new__(TradingAgentsGraph)
    graph.asset_type = "commodity_future"
    nodes = graph._create_tool_nodes()
    setup = GraphSetup(MagicMock(), MagicMock(), nodes, MagicMock())
    workflow = setup.setup_graph(["market"], asset_type="commodity_future")

    assert "Market Analyst" in workflow.nodes
    assert "Trader" not in workflow.nodes
    assert "Portfolio Manager" not in workflow.nodes
    assert "Fundamentals Analyst" not in workflow.nodes


@pytest.mark.unit
def test_commodity_graph_rejects_nontechnical_analysts():
    graph = object.__new__(TradingAgentsGraph)
    graph.asset_type = "commodity_future"
    nodes = graph._create_tool_nodes()
    setup = GraphSetup(MagicMock(), MagicMock(), nodes, MagicMock())
    with pytest.raises(ValueError, match="only the market"):
        setup.setup_graph(["market", "fundamentals"], asset_type="commodity_future")


@pytest.mark.unit
def test_propagator_builds_commodity_specific_state():
    state = Propagator().create_initial_state(
        "ZCZ26",
        "2026-07-30",
        asset_type="commodity_future",
        commodity="corn",
        crop_year="2026/27",
        forecast_horizons=[5, 20, 60],
        evidence_package_uri="file:///evidence.json",
    )
    assert state["contract_symbol"] == "ZCZ26"
    assert state["commodity"] == "corn"
    assert state["crop_year"] == "2026/27"
    assert state["forecast_horizons"] == [5, 20, 60]
    assert state["technical_report"] == ""
