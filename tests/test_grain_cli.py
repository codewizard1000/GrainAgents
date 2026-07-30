import json

from typer.testing import CliRunner

from cli.grain import app

runner = CliRunner()


def test_foundation_command_writes_contract_scoped_artifacts(tmp_path):
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
            "--results-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    run_dir = tmp_path / "corn" / "ZCZ26" / "2026-07-30"
    evidence = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    report = (run_dir / "technical_report.md").read_text(encoding="utf-8")

    assert evidence["instrument"]["symbol"] == "ZCZ26"
    assert manifest["asset_type"] == "commodity_future"
    assert manifest["status"] == "blocked_missing_core_market_data"
    assert manifest["human_approval_required"] is True
    assert "cannot substitute" in report
    assert "No values have been estimated or fabricated" in report


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
    assert "expired" in str(result.exception).lower()
