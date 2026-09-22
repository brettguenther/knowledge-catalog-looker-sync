import os
from pathlib import Path
import sys
import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from looker_kc_sync.orchestrator import SyncOrchestrator

console = Console()


@click.group()
def main():
    """Looker & Google Knowledge Catalog (Dataplex) Sync CLI."""
    pass


@main.command()
@click.option("--config", "-c", default="config/sync_config.yaml", help="Path to sync_config.yaml")
@click.option("--deploy/--no-deploy", default=True, help="Deploy machine-managed base views and base explores to Looker project via Looker SDK (or looker-cli fallback)")
@click.option("--pr", is_flag=True, default=False, help="Create or update a GitHub Pull Request with the LookML base view and base explore changes")
@click.option("--scaffold", is_flag=True, default=False, help="Generate starter curated refinements and model file locally if not present")
@click.option("--explore-table", "-e", "explore_tables", multiple=True, help="Optional table(s) to scope base explore generation")
def sync(config: str, deploy: bool, pr: bool, scaffold: bool, explore_tables: tuple[str, ...]):
    """Synchronize Knowledge Catalog metadata to Looker LookML base views and base explores."""
    console.print(Panel(
        f"[bold cyan]Starting Knowledge Catalog & Looker Synchronization[/bold cyan]\n"
        f"Config: {config}\nPR Mode: {pr}\nScaffold Local: {scaffold}",
        border_style="cyan"
    ))

    try:
        overrides = {}
        if explore_tables:
            overrides["explore_tables"] = list(explore_tables)
        orchestrator = SyncOrchestrator(config, **overrides)
        with console.status("[bold green]Extracting metadata, generating LookML, and processing...[/bold green]"):
            results = orchestrator.run(deploy=deploy, create_pr=pr, scaffold=scaffold)

        table = Table(title="Sync Execution Summary", border_style="green")
        table.add_column("Metric / Stage", style="bold white")
        table.add_column("Result", style="green")

        table.add_row("Tables Processed", str(results["tables_processed"]))
        table.add_row("Views Generated", ", ".join(results["views_generated"]))
        if results.get("explores_generated"):
            table.add_row("Base Explores Generated", ", ".join(results["explores_generated"]))
        table.add_row("Local Files Written", str(len(results["files_written"])))
        table.add_row("Looker Deployed (Base Layer)", "Yes" if results["looker_deployed"] else "No (skipped)")

        if results["looker_deployed"]:
            val_status = "[bold green]PASS[/bold green]" if results["validation_valid"] else "[bold red]FAIL[/bold red]"
            table.add_row("LookML Validation", val_status)
            if results["validation_message"]:
                table.add_row("Validation Output", results["validation_message"])

        if results.get("pr_result"):
            pr_res = results["pr_result"]
            if pr_res.get("pr_created") or pr_res.get("pr_updated"):
                action_str = "Updated" if pr_res.get("pr_updated") else "Created"
                table.add_row(f"GitHub PR ({action_str})", f"[bold green]{pr_res['pr_url']}[/bold green]")
                table.add_row("PR Branch", pr_res.get("branch", ""))
            else:
                table.add_row("GitHub PR", pr_res.get("message", "No PR created"))

        if results.get("errors"):
            table.add_row("Skipped Tables / Errors", "\n".join(results["errors"]), style="yellow")

        console.print(table)

    except Exception as e:
        console.print(f"[bold red]Sync Failed:[/bold red] {e}")
        raise click.Abort()


@main.command()
@click.option("--config", "-c", default="config/sync_config.yaml", help="Path to sync_config.yaml")
def scaffold(config: str):
    """Scaffold initial local curated refinements and model file without remote deployment."""
    console.print(Panel(f"[bold cyan]Scaffolding Curated Views & Model[/bold cyan]\nConfig: {config}", border_style="cyan"))
    try:
        orchestrator = SyncOrchestrator(config)
        with console.status("[bold green]Extracting metadata and scaffolding starter files...[/bold green]"):
            results = orchestrator.run(deploy=False, create_pr=False, scaffold=True)
        console.print(f"[bold green]✓ Scaffolding complete! Local files written: {len(results['files_written'])}[/bold green]")
    except Exception as e:
        console.print(f"[bold red]Scaffolding Failed:[/bold red] {e}")
        raise click.Abort()


@main.command()
@click.option("--project", "-p", default=lambda: os.environ.get("LOOKER_PROJECT_ID", ""), help="Looker project ID (defaults to LOOKER_PROJECT_ID env var)")
def validate(project: str):
    """Run LookML validation against a Looker project using Looker SDK (or looker-cli fallback)."""
    if not project:
        console.print("[bold red]Error:[/bold red] Please provide --project or set LOOKER_PROJECT_ID environment variable.")
        raise click.Abort()

    from looker_kc_sync.clients.looker import LookerClient
    client = LookerClient()
    with console.status(f"[bold green]Validating project {project}...[/bold green]"):
        is_valid, msg = client.validate_project(project)
    if is_valid:
        console.print(f"[bold green]✓ Project '{project}' is valid![/bold green]")
    else:
        console.print(f"[bold red]✗ Project '{project}' validation failed:[/bold red]\n{msg}")


if __name__ == "__main__":
    main()
