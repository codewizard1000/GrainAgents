import json
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

import pandas as pd
from typer.testing import CliRunner

from cli.grain import app
from tradingagents.commodities.evidence import freeze_evidence_value
from tradingagents.commodities.providers import CONTRACT_HISTORY_PROVIDERS
from tradingagents.commodities.publication import PublicationBundle

runner = CliRunner()


def _fixture_history(**kwargs):
    end = date.fromisoformat(kwargs["end_date"])
    contract = kwargs["contract_symbol"]
    offset = sum(ord(character) for character in contract) % 25 / 100
    bars = []
    for index in range(230):
        bar_date = end - timedelta(days=229 - index)
        price = round(4.0 + offset + index * 0.001, 4)
        available_at = f"{bar_date.isoformat()}T21:00:00+00:00"
        bars.append(
            {
                "date": bar_date.isoformat(),
                "contract_symbol": contract,
                "vendor_symbol": f"{contract[:-2]}{contract[-1]}",
                "open": price - 0.01,
                "high": price + 0.02,
                "low": price - 0.02,
                "close": price,
                "settlement": price,
                "volume": 100_000 + index,
                "bar_volume": 100_000 + index,
                "cleared_volume": 100_000 + index,
                "open_interest": 500_000 + index,
                "bar_ts_event": f"{bar_date.isoformat()}T00:00:00+00:00",
                "settlement_available_at": available_at,
                "cleared_volume_available_at": available_at,
                "open_interest_available_at": available_at,
            }
        )
    return {
        "schema_version": "1.0",
        "provider": "databento",
        "dataset": "GLBX.MDP3",
        "data_scope": "delivery_specific",
        "license_scope": "internal_testing_only",
        "requested_symbol": contract,
        "vendor_symbol": f"{contract[:-2]}{contract[-1]}",
        "start_date": kwargs["start_date"],
        "end_date": kwargs["end_date"],
        "retrieved_at": "2026-07-30T22:00:00+00:00",
        "price_unit": "USD_per_bushel",
        "volume_unit": "contracts",
        "definition": None,
        "bars": bars,
        "source_record_counts": {
            "ohlcv-1d": len(bars),
            "statistics": len(bars) * 3,
            "definition": 1,
        },
        "warnings": [],
    }


def test_market_data_command_writes_normalized_json(tmp_path):
    output_path = tmp_path / "market-data.json"
    payload = {
        "provider": "databento",
        "requested_symbol": "ZCZ26",
        "license_scope": "internal_testing_only",
        "bars": [{"date": "2026-07-29", "close": 4.12}],
    }

    with mock.patch("cli.grain.contract_history_tool") as tool:
        tool.invoke.return_value = json.dumps(payload)
        result = runner.invoke(
            app,
            [
                "market-data",
                "--contract",
                "ZCZ26",
                "--start",
                "2026-07-20",
                "--end",
                "2026-07-29",
                "--output",
                str(output_path),
            ],
        )

    assert result.exit_code == 0, result.output
    assert json.loads(output_path.read_text(encoding="utf-8")) == payload
    tool.invoke.assert_called_once_with(
        {
            "contract_symbol": "ZCZ26",
            "start_date": "2026-07-20",
            "end_date": "2026-07-29",
        }
    )


def test_analyze_command_writes_verified_technical_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAIN_DATA_PROVIDER", "databento")
    with mock.patch.dict(
        CONTRACT_HISTORY_PROVIDERS,
        {"databento": _fixture_history},
        clear=False,
    ):
        result = runner.invoke(
            app,
            [
                "analyze",
                "--commodity",
                "corn",
                "--contract",
                "ZCZ26",
                "--as-of",
                "2026-07-30",
                "--horizons",
                "5,20,60",
                "--output",
                "newsletter",
                "--no-official-data",
                "--results-dir",
                str(tmp_path),
            ],
        )

    assert result.exit_code == 0, result.output
    run_dir = tmp_path / "corn" / "ZCZ26" / "2026-07-30"
    evidence = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    report = (run_dir / "technical_report.md").read_text(encoding="utf-8")
    forecast_report = (run_dir / "forecast_report.md").read_text(encoding="utf-8")
    market_data = pd.read_parquet(run_dir / "market_data.parquet")

    assert evidence["instrument"]["symbol"] == "ZCZ26"
    assert evidence["technical"]["bar_count"] == 230
    assert evidence["curve"]["status"] == "ready"
    assert evidence["facts"]
    assert manifest["asset_type"] == "commodity_future"
    assert manifest["status"] == "forecast_baseline_ready_publication_blocked"
    assert manifest["human_approval_required"] is True
    assert manifest["publication_ready"] is False
    assert len(market_data) == 230
    assert "does not substitute" in report
    assert "fact_zcz26_settlement" in report
    assert "`regression_tree`" in forecast_report
    assert "zero weight" in forecast_report
    assert (run_dir / "market_data_provider.json").exists()
    assert (run_dir / "source_audit.csv").exists()
    assert (run_dir / "supply_demand_report.md").exists()
    assert (run_dir / "positioning_report.md").exists()
    assert (run_dir / "demand_report.md").exists()
    assert (run_dir / "weather_report.md").exists()
    assert (run_dir / "forecast_report.md").exists()
    assert (run_dir / "quantitative_forecast.json").exists()
    assert (run_dir / "forecast_performance.json").exists()
    assert (run_dir / "scenario_report.json").exists()


def test_foundation_command_rejects_stock_analysts(tmp_path):
    result = runner.invoke(
        app,
        [
            "analyze",
            "--commodity",
            "corn",
            "--contract",
            "ZCZ26",
            "--as-of",
            "2026-07-30",
            "--analysts",
            "fundamentals",
            "--results-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "only --analysts technical" in result.output


def test_newsletter_output_writes_blocked_publication_bundle(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("GRAIN_DATA_PROVIDER", "databento")

    def official_run(base):
        evidence = replace(
            base,
            supply_demand=freeze_evidence_value({"status": "ready"}),
            demand=freeze_evidence_value(
                {
                    "status": "partial",
                    "ethanol": {"status": "ready"},
                    "missing": ["export_sales", "export_inspections"],
                }
            ),
            positioning=freeze_evidence_value({"status": "ready"}),
            weather=freeze_evidence_value(
                {"status": "partial", "values": {"fixture": True}}
            ),
        )
        return SimpleNamespace(evidence=evidence, archives=())

    bundle = PublicationBundle(
        final_outlook={"status": "blocked"},
        publication_status={
            "status": "blocked",
            "publication_ready": False,
        },
        newsletter="# DRAFT — NOT APPROVED FOR PUBLICATION\n",
        bull_bear_debate="# Bull and bear case — DRAFT\n",
        risk_report="# Risk review — DRAFT\n",
        news_report="# Grain news and macro — UNAVAILABLE\n",
    )
    with (
        mock.patch.dict(
            CONTRACT_HISTORY_PROVIDERS,
            {"databento": _fixture_history},
            clear=False,
        ),
        mock.patch(
            "cli.grain.build_official_evidence",
            side_effect=official_run,
        ),
        mock.patch(
            "cli.grain.build_publication_bundle",
            return_value=bundle,
        ),
        mock.patch(
            "cli.grain.find_prior_approved_outlook",
            return_value=None,
        ),
        mock.patch(
            "cli.grain.build_prior_report_comparison",
            return_value={
                "comparison_version": "fixture-comparison-v1",
                "status": "unavailable",
            },
        ),
        mock.patch(
            "cli.grain.generate_publication_charts",
            return_value={
                "chart_version": "fixture-charts-v1",
                "charts": [],
            },
        ),
        mock.patch(
            "cli.grain.render_supply_demand_report",
            return_value="# Supply and demand\n",
        ),
        mock.patch(
            "cli.grain.render_demand_report",
            return_value="# Demand\n",
        ),
        mock.patch(
            "cli.grain.render_positioning_report",
            return_value="# Positioning\n",
        ),
        mock.patch(
            "cli.grain.render_weather_report",
            return_value="# Weather\n",
        ),
    ):
        result = runner.invoke(
            app,
            [
                "analyze",
                "--commodity",
                "corn",
                "--contract",
                "ZCZ26",
                "--as-of",
                "2026-07-30",
                "--output",
                "newsletter",
                "--results-dir",
                str(tmp_path),
            ],
        )

    assert result.exit_code == 0, result.output
    run_dir = tmp_path / "corn" / "ZCZ26" / "2026-07-30"
    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    assert result.output.strip().endswith("newsletter.md")
    assert manifest["publication_status"] == "blocked"
    assert manifest["publication_ready"] is False
    assert "newsletter.md" in manifest["artifacts"]
    assert "charts/charts_manifest.json" in manifest["artifacts"]
    assert "prior_report_comparison.json" in manifest["artifacts"]
    assert (run_dir / "final_outlook.json").exists()
    assert (run_dir / "publication_status.json").exists()
    assert (run_dir / "bull_bear_debate.md").exists()
    assert (run_dir / "risk_report.md").exists()
    assert (run_dir / "news_report.md").exists()


def test_foundation_command_rejects_expired_contract(tmp_path):
    result = runner.invoke(
        app,
        [
            "analyze",
            "--commodity",
            "corn",
            "--contract",
            "ZCZ25",
            "--as-of",
            "2026-07-30",
            "--results-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "expired" in result.output.lower()


def test_approval_gate_refuses_blocked_publication(tmp_path):
    (tmp_path / "publication_status.json").write_text(
        json.dumps(
            {
                "run_id": "fixture-run",
                "status": "blocked",
                "blockers": [
                    {
                        "code": "missing_core_data:weather",
                        "message": "Weather is incomplete.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "run_manifest.json").write_text(
        json.dumps({"artifacts": ["newsletter.md"]}),
        encoding="utf-8",
    )
    (tmp_path / "newsletter.md").write_text(
        "# DRAFT — NOT APPROVED FOR PUBLICATION\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "approve-publication",
            "--run-dir",
            str(tmp_path),
            "--approved-by",
            "Fixture Editor",
        ],
    )

    assert result.exit_code == 2
    assert "publication remains blocked" in result.output
    assert not (tmp_path / "publication_approval.json").exists()


def test_approval_gate_records_human_and_exact_artifact_hash(tmp_path):
    (tmp_path / "publication_status.json").write_text(
        json.dumps(
            {
                "run_id": "fixture-run",
                "status": "awaiting_human_approval",
                "publication_ready": False,
                "human_approval_recorded": False,
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "run_manifest.json").write_text(
        json.dumps(
            {
                "publication_status": "awaiting_human_approval",
                "publication_ready": False,
                "artifacts": ["newsletter.md"],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "final_outlook.json").write_text(
        json.dumps({"status": "draft", "publication_ready": False}),
        encoding="utf-8",
    )
    (tmp_path / "newsletter.md").write_text(
        "# DRAFT — NOT APPROVED FOR PUBLICATION\n\nExact fixture body.\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "approve-publication",
            "--run-dir",
            str(tmp_path),
            "--approved-by",
            "Fixture Editor",
            "--note",
            "Reviewed",
        ],
    )

    assert result.exit_code == 0, result.output
    approval = json.loads(
        (tmp_path / "publication_approval.json").read_text()
    )
    status = json.loads(
        (tmp_path / "publication_status.json").read_text()
    )
    manifest = json.loads((tmp_path / "run_manifest.json").read_text())
    approved = (tmp_path / "newsletter_approved.md").read_text()
    assert approval["approved_by"] == "Fixture Editor"
    assert approval["external_publication_performed"] is False
    assert len(approval["approved_artifact_sha256"]) == 64
    assert approved.startswith("# APPROVED FOR PUBLICATION")
    assert status["human_approval_recorded"] is True
    assert manifest["publication_ready"] is True
    assert "publication_approval.json" in manifest["artifacts"]
