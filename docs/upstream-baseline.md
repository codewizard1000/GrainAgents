# Upstream baseline

## Source

- Repository: `TauricResearch/TradingAgents`
- Commit: `a33fd4c0f134485a43553a2c23a63cb14adbd88f`
- Commit date: 2026-07-18T15:55:04Z
- Subject: `docs: streamline README header`
- License: Apache License 2.0, preserved in `LICENSE`

## Environment

- Operating system: Windows
- Python: 3.13.2
- Test runner: pytest 9.1.1
- Installation: editable project install with the declared `dev` extra

## Baseline command

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Result

- 578 tests collected
- 576 passed
- 2 skipped
- 18 warnings
- Duration: 111.78 seconds

The Bedrock test was skipped because the optional `langchain-aws` dependency was
not installed. One DeepSeek live API test was skipped because no live API key
was configured. Neither skip affects the core upstream baseline.

## Architecture inspection

The initial review covered:

- `tradingagents/default_config.py`
- `tradingagents/agents/utils/agent_states.py`
- `tradingagents/graph/setup.py`
- `tradingagents/graph/analyst_execution.py`
- `tradingagents/graph/propagation.py`
- `tradingagents/graph/trading_graph.py`
- `tradingagents/dataflows/interface.py`
- `tradingagents/dataflows/errors.py`
- `tradingagents/reporting.py`
- `cli/main.py`, `cli/models.py`, and `cli/utils.py`
- the upstream test suite and `LICENSE`

No functional source code was modified before this baseline was recorded.
