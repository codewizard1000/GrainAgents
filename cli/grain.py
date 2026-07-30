"""Non-interactive GrainAgents commodity-foundation CLI."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated

import typer

from tradingagents.commodities.analysis import build_technical_evidence
from tradingagents.commodities.artifacts import (
    write_json,
    write_market_parquet,
    write_source_audit,
)
from tradingagents.commodities.evidence import (
    build_evidence_package,
    normalize_horizons,
)
from tradingagents.commodities.reporting import render_technical_report
from tradingagents.commodities.tools import (
    get_contract_history as contract_history_tool,
)
from tradingagents.dataflows.errors import VendorError

app = typer.Typer(
    help="Contract-specific grain-market research and forecasting.",
    no_args_is_help=True,
)
DEFAULT_RESULTS_DIR = Path("results")


@app.callback()
def main() -> None:
    """GrainAgents command group."""


def parse_horizons(value: str) -> tuple[int, ...]:
    try:
        raw = [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise typer.BadParameter(
            "horizons must be comma-separated positive integers, for example 5,20,60"
        ) from exc
    try:
        return normalize_horizons(raw)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _project_version() -> str:
    try:
        return version("tradingagents")
    except PackageNotFoundError:
        return "uninstalled"


@app.command("market-data")
def market_data(
    contract: str = typer.Option(
        ...,
        help="Delivery-specific symbol such as ZCZ26",
    ),
    start_date: str = typer.Option(
        ...,
        "--start",
        help="Inclusive start date in YYYY-MM-DD format",
    ),
    end_date: str = typer.Option(
        ...,
        "--end",
        help="Inclusive end date in YYYY-MM-DD format",
    ),
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            help="Optional JSON output path; otherwise print to standard output",
        ),
    ] = None,
) -> None:
    """Fetch normalized history for one exact delivery contract."""
    try:
        raw = contract_history_tool.invoke(
            {
                "contract_symbol": contract,
                "start_date": start_date,
                "end_date": end_date,
            }
        )
    except (ValueError, VendorError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    rendered = json.dumps(json.loads(raw), indent=2, sort_keys=True) + "\n"
    if output is None:
        typer.echo(rendered, nl=False)
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    typer.echo(str(output.resolve()))


@app.command()
def analyze(
    commodity: str = typer.Option(..., help="corn, soybeans, or wheat_srw"),
    contract: str = typer.Option(..., help="Delivery-specific symbol such as ZCZ26"),
    as_of: str = typer.Option(
        date.today().isoformat(),
        "--as-of",
        help="Point-in-time analysis date in YYYY-MM-DD format",
    ),
    horizons: str = typer.Option(
        "5,20,60",
        help="Comma-separated trading-day forecast horizons",
    ),
    analysts: str = typer.Option(
        "technical",
        help="The current vertical slice supports only the technical analyst",
    ),
    research_depth: int = typer.Option(
        1,
        min=1,
        help="Reserved for later debate depth; recorded for reproducibility",
    ),
    output: str = typer.Option(
        "newsletter",
        help="Output selector: newsletter/markdown returns the technical report; json returns evidence",
    ),
    results_dir: Annotated[
        Path,
        typer.Option(help="Root directory for generated artifacts"),
    ] = DEFAULT_RESULTS_DIR,
) -> None:
    """Build exact-contract market evidence and a deterministic technical report."""
    if analysts.strip().lower() != "technical":
        typer.echo(
            "Error: the current commodity run supports only --analysts technical",
            err=True,
        )
        raise typer.Exit(code=2)
    if output not in {"newsletter", "markdown", "json"}:
        typer.echo(
            "Error: --output must be newsletter, markdown, or json",
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        base_evidence = build_evidence_package(
            commodity=commodity,
            contract_symbol=contract,
            as_of=as_of,
            forecast_horizons=parse_horizons(horizons),
        )
        run = build_technical_evidence(base_evidence)
    except (ValueError, VendorError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    evidence = run.evidence
    payload = evidence.to_dict()
    run_dir = (
        results_dir
        / evidence.instrument.commodity.value
        / evidence.instrument.symbol
        / evidence.as_of.date().isoformat()
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    evidence_path = run_dir / "evidence.json"
    write_json(evidence_path, payload)
    market_path = run_dir / "market_data.parquet"
    write_market_parquet(market_path, run.primary_history)
    provider_path = run_dir / "market_data_provider.json"
    write_json(
        provider_path,
        {
            "schema_version": "1.0",
            "primary_contract": run.primary_history,
            "curve_contracts": list(run.curve_histories),
        },
    )
    report_path = run_dir / "technical_report.md"
    report_path.write_text(render_technical_report(payload), encoding="utf-8")
    audit_path = run_dir / "source_audit.csv"
    write_source_audit(audit_path, payload)

    manifest = {
        "schema_version": "1.0",
        "run_id": evidence.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "code_version": _project_version(),
        "prompt_version": "deterministic-commodity-technical-v1",
        "data_versions": {
            "market_provider": run.primary_history["provider"],
            "market_dataset": run.primary_history["dataset"],
            "technical_methodology": payload["technical"]["methodology_version"],
        },
        "asset_type": "commodity_future",
        "commodity": evidence.instrument.commodity.value,
        "contract": evidence.instrument.symbol,
        "as_of": evidence.as_of.isoformat(),
        "forecast_horizons": list(evidence.forecast_horizons),
        "analysts": ["technical"],
        "research_depth": research_depth,
        "status": payload["quality"]["status"],
        "human_approval_required": True,
        "publication_ready": False,
        "artifacts": [
            "evidence.json",
            "market_data.parquet",
            "market_data_provider.json",
            "technical_report.md",
            "source_audit.csv",
        ],
    }
    manifest_path = run_dir / "run_manifest.json"
    write_json(manifest_path, manifest)

    selected_path = evidence_path if output == "json" else report_path
    typer.echo(str(selected_path.resolve()))


if __name__ == "__main__":
    app()
