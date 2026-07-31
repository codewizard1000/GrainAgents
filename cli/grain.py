"""Non-interactive GrainAgents commodity-foundation CLI."""

from __future__ import annotations

import hashlib
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
    write_official_archives,
    write_source_audit,
)
from tradingagents.commodities.charts import generate_publication_charts
from tradingagents.commodities.comparison import (
    build_prior_report_comparison,
    find_prior_approved_outlook,
)
from tradingagents.commodities.evidence import (
    build_evidence_package,
    normalize_horizons,
)
from tradingagents.commodities.forecasting import build_quantitative_forecast
from tradingagents.commodities.official import build_official_evidence
from tradingagents.commodities.publication import build_publication_bundle
from tradingagents.commodities.reporting import (
    render_demand_report,
    render_forecast_report,
    render_positioning_report,
    render_supply_demand_report,
    render_technical_report,
    render_weather_report,
)
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
        help="Output selector: newsletter returns the publication draft, markdown the technical report, json the evidence",
    ),
    results_dir: Annotated[
        Path,
        typer.Option(help="Root directory for generated artifacts"),
    ] = DEFAULT_RESULTS_DIR,
    official_data: bool = typer.Option(
        True,
        "--official-data/--no-official-data",
        help="Fetch point-in-time USDA WASDE and CFTC COT evidence",
    ),
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
        official_run = (
            build_official_evidence(run.evidence)
            if official_data
            else None
        )
        forecast_run = build_quantitative_forecast(
            official_run.evidence if official_run is not None else run.evidence,
            history=run.primary_history,
        )
    except (ValueError, VendorError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    evidence = forecast_run.evidence
    payload = evidence.to_dict()
    run_dir = (
        results_dir
        / evidence.instrument.commodity.value
        / evidence.instrument.symbol
        / evidence.as_of.date().isoformat()
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    publication_prerequisites = (
        official_run is not None
        and evidence.instrument.commodity.value == "corn"
        and evidence.supply_demand.get("status") == "ready"
        and (
            evidence.demand.get("ethanol", evidence.demand).get("status")
            == "ready"
        )
        and evidence.positioning.get("status") == "ready"
        and bool(evidence.weather.get("values"))
    )
    publication_bundle = None
    prior_comparison = None
    charts_manifest = None
    chart_paths: list[str] = []
    comparison_path: Path | None = None
    if publication_prerequisites:
        prior_outlook = find_prior_approved_outlook(
            results_dir,
            commodity=evidence.instrument.commodity.value,
            contract_symbol=evidence.instrument.symbol,
            current_date=evidence.as_of.date(),
        )
        preliminary_bundle = build_publication_bundle(
            payload,
            quantitative=forecast_run.quantitative_forecast,
            scenarios=forecast_run.scenarios,
        )
        prior_comparison = build_prior_report_comparison(
            preliminary_bundle.final_outlook,
            prior_outlook,
        )
        charts_dir = run_dir / "charts"
        charts_dir.mkdir(parents=True, exist_ok=True)
        charts_manifest = generate_publication_charts(
            charts_dir,
            evidence=payload,
            history=run.primary_history,
            quantitative=forecast_run.quantitative_forecast,
            scenarios=forecast_run.scenarios,
            prior_outlook=prior_outlook,
        )
        charts_manifest_path = charts_dir / "charts_manifest.json"
        write_json(charts_manifest_path, charts_manifest)
        chart_paths = [
            "charts/charts_manifest.json",
            *[
                f"charts/{chart['filename']}"
                for chart in charts_manifest["charts"]
            ],
        ]
        comparison_path = run_dir / "prior_report_comparison.json"
        write_json(comparison_path, prior_comparison)
        publication_bundle = build_publication_bundle(
            payload,
            quantitative=forecast_run.quantitative_forecast,
            scenarios=forecast_run.scenarios,
            prior_comparison=prior_comparison,
            charts_manifest=charts_manifest,
        )

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
    official_archive_paths = write_official_archives(
        run_dir / "official_data",
        official_run.archives if official_run is not None else (),
    )
    report_path = run_dir / "technical_report.md"
    report_path.write_text(render_technical_report(payload), encoding="utf-8")
    supply_demand_path = run_dir / "supply_demand_report.md"
    supply_demand_path.write_text(
        render_supply_demand_report(payload),
        encoding="utf-8",
    )
    positioning_path = run_dir / "positioning_report.md"
    positioning_path.write_text(
        render_positioning_report(payload),
        encoding="utf-8",
    )
    demand_path = run_dir / "demand_report.md"
    demand_path.write_text(render_demand_report(payload), encoding="utf-8")
    weather_path = run_dir / "weather_report.md"
    weather_path.write_text(render_weather_report(payload), encoding="utf-8")
    forecast_report_path = run_dir / "forecast_report.md"
    forecast_report_path.write_text(
        render_forecast_report(payload),
        encoding="utf-8",
    )
    quantitative_path = run_dir / "quantitative_forecast.json"
    write_json(quantitative_path, forecast_run.quantitative_forecast)
    performance_path = run_dir / "forecast_performance.json"
    write_json(
        performance_path,
        forecast_run.quantitative_forecast["performance_registry"],
    )
    scenario_path = run_dir / "scenario_report.json"
    write_json(scenario_path, forecast_run.scenarios)
    publication_paths: list[Path] = []
    if publication_bundle is not None:
        final_outlook_path = run_dir / "final_outlook.json"
        write_json(final_outlook_path, publication_bundle.final_outlook)
        publication_status_path = run_dir / "publication_status.json"
        write_json(
            publication_status_path,
            publication_bundle.publication_status,
        )
        newsletter_path = run_dir / "newsletter.md"
        newsletter_path.write_text(
            publication_bundle.newsletter,
            encoding="utf-8",
        )
        debate_path = run_dir / "bull_bear_debate.md"
        debate_path.write_text(
            publication_bundle.bull_bear_debate,
            encoding="utf-8",
        )
        risk_path = run_dir / "risk_report.md"
        risk_path.write_text(
            publication_bundle.risk_report,
            encoding="utf-8",
        )
        news_path = run_dir / "news_report.md"
        news_path.write_text(
            publication_bundle.news_report,
            encoding="utf-8",
        )
        publication_paths = [
            final_outlook_path,
            publication_status_path,
            newsletter_path,
            debate_path,
            risk_path,
            news_path,
        ]
    audit_path = run_dir / "source_audit.csv"
    write_source_audit(audit_path, payload)

    manifest = {
        "schema_version": "1.0",
        "run_id": evidence.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "code_version": _project_version(),
        "prompt_version": "deterministic-commodity-evidence-v3",
        "data_versions": {
            "market_provider": run.primary_history["provider"],
            "market_dataset": run.primary_history["dataset"],
            "technical_methodology": payload["technical"]["methodology_version"],
            "forecast_methodology": payload["forecast"]["model_version"],
            "forecast_performance_methodology": payload["forecast"][
                "performance_registry"
            ]["methodology_version"],
            "chart_methodology": (
                charts_manifest["chart_version"]
                if charts_manifest is not None
                else None
            ),
            "prior_report_comparison_methodology": (
                prior_comparison["comparison_version"]
                if prior_comparison is not None
                else None
            ),
            "official_sources": [
                source["dataset"]
                for source in payload["sources"]
                if source.get("provider")
                in {
                    "USDA",
                    "CFTC",
                    "EIA",
                    "NOAA/NIDIS, NWS, and USDA NASS",
                    "NOAA Climate Prediction Center",
                    "USDA FAS",
                    "USDA AMS/FGIS",
                    "Federal Reserve Bank of St. Louis",
                    "Office of the Federal Register / GPO",
                }
            ],
        },
        "asset_type": "commodity_future",
        "commodity": evidence.instrument.commodity.value,
        "contract": evidence.instrument.symbol,
        "as_of": evidence.as_of.isoformat(),
        "forecast_horizons": list(evidence.forecast_horizons),
        "analysts": [
            "technical",
            "official_macro_context",
            "official_regulatory_events",
        ],
        "research_depth": research_depth,
        "status": payload["quality"]["status"],
        "publication_status": (
            publication_bundle.publication_status["status"]
            if publication_bundle is not None
            else "not_generated"
        ),
        "human_approval_required": True,
        "publication_ready": False,
        "artifacts": [
            "evidence.json",
            "market_data.parquet",
            "market_data_provider.json",
            "technical_report.md",
            "supply_demand_report.md",
            "positioning_report.md",
            "demand_report.md",
            "weather_report.md",
            "forecast_report.md",
            "quantitative_forecast.json",
            "forecast_performance.json",
            "scenario_report.json",
            *chart_paths,
            *([comparison_path.name] if comparison_path is not None else []),
            *[path.name for path in publication_paths],
            "source_audit.csv",
            *[
                str(Path(path).relative_to(run_dir).as_posix())
                for path in official_archive_paths
            ],
        ],
    }
    manifest_path = run_dir / "run_manifest.json"
    write_json(manifest_path, manifest)

    if output == "json":
        selected_path = evidence_path
    elif output == "newsletter" and publication_bundle is not None:
        selected_path = run_dir / "newsletter.md"
    else:
        selected_path = report_path
    typer.echo(str(selected_path.resolve()))


@app.command("approve-publication")
def approve_publication(
    run_dir: Annotated[
        Path,
        typer.Option(
            help="Existing run directory containing publication_status.json",
        ),
    ],
    approved_by: Annotated[
        str,
        typer.Option(
            help="Name or identifier of the human editor approving the exact draft",
        ),
    ],
    note: Annotated[
        str,
        typer.Option(help="Optional editorial approval note"),
    ] = "",
) -> None:
    """Approve an unblocked newsletter draft without publishing it externally."""
    status_path = run_dir / "publication_status.json"
    manifest_path = run_dir / "run_manifest.json"
    newsletter_path = run_dir / "newsletter.md"
    required = (status_path, manifest_path, newsletter_path)
    missing = [path.name for path in required if not path.exists()]
    if missing:
        typer.echo(
            "Error: run directory is missing " + ", ".join(missing),
            err=True,
        )
        raise typer.Exit(code=2)

    status = json.loads(status_path.read_text(encoding="utf-8"))
    blockers = status.get("blockers", [])
    if blockers:
        codes = ", ".join(blocker["code"] for blocker in blockers)
        typer.echo(
            f"Error: publication remains blocked: {codes}",
            err=True,
        )
        raise typer.Exit(code=2)
    if not approved_by.strip():
        typer.echo("Error: --approved-by cannot be blank", err=True)
        raise typer.Exit(code=2)

    approved_at = datetime.now(timezone.utc).isoformat()
    draft = newsletter_path.read_text(encoding="utf-8")
    approved_newsletter = draft.replace(
        "# DRAFT — NOT APPROVED FOR PUBLICATION",
        "# APPROVED FOR PUBLICATION",
        1,
    )
    approved_path = run_dir / "newsletter_approved.md"
    approved_path.write_text(approved_newsletter, encoding="utf-8")
    newsletter_sha256 = hashlib.sha256(
        approved_newsletter.encode("utf-8")
    ).hexdigest()
    approval = {
        "schema_version": "1.0",
        "run_id": status["run_id"],
        "approved_at": approved_at,
        "approved_by": approved_by.strip(),
        "note": note.strip(),
        "approved_artifact": approved_path.name,
        "approved_artifact_sha256": newsletter_sha256,
        "external_publication_performed": False,
    }
    approval_path = run_dir / "publication_approval.json"
    write_json(approval_path, approval)

    status.update(
        {
            "status": "approved",
            "publication_ready": True,
            "human_approval_recorded": True,
            "approval_artifact": approval_path.name,
        }
    )
    write_json(status_path, status)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["publication_status"] = "approved"
    manifest["publication_ready"] = True
    artifacts = list(manifest.get("artifacts", []))
    for name in (approved_path.name, approval_path.name):
        if name not in artifacts:
            artifacts.append(name)
    manifest["artifacts"] = artifacts
    write_json(manifest_path, manifest)

    final_outlook_path = run_dir / "final_outlook.json"
    if final_outlook_path.exists():
        final_outlook = json.loads(
            final_outlook_path.read_text(encoding="utf-8")
        )
        final_outlook["status"] = "approved"
        final_outlook["publication_ready"] = True
        final_outlook["approval_artifact"] = approval_path.name
        write_json(final_outlook_path, final_outlook)

    typer.echo(str(approved_path.resolve()))


if __name__ == "__main__":
    app()
