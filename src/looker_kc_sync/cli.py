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
@click.option("--deploy/--no-deploy", default=True, help="Deploy files to Looker project via looker-cli")
@click.option("--pr", is_flag=True, default=False, help="Create a GitHub Pull Request with the LookML changes")
def sync(config: str, deploy: bool, pr: bool):
    """Synchronize Knowledge Catalog metadata to Looker LookML."""
    console.print(Panel(f"[bold cyan]Starting Knowledge Catalog & Looker Synchronization[/bold cyan]\nConfig: {config}\nPR Mode: {pr}", border_style="cyan"))

    try:
        orchestrator = SyncOrchestrator(config)
        with console.status("[bold green]Extracting metadata, generating LookML, and processing...[/bold green]"):
            results = orchestrator.run(deploy=deploy, create_pr=pr)

        table = Table(title="Sync Execution Summary", border_style="green")
        table.add_column("Metric / Stage", style="bold white")
        table.add_column("Result", style="green")

        table.add_row("Tables Processed", str(results["tables_processed"]))
        table.add_row("Views Generated", ", ".join(results["views_generated"]))
        table.add_row("Local Files Written", str(len(results["files_written"])))
        table.add_row("Looker Deployed", "Yes" if results["looker_deployed"] else "No (skipped)")

        if results["looker_deployed"]:
            val_status = "[bold green]PASS[/bold green]" if results["validation_valid"] else "[bold red]FAIL[/bold red]"
            table.add_row("LookML Validation", val_status)
            if results["validation_message"]:
                table.add_row("Validation Output", results["validation_message"])

        if results.get("pr_result"):
            pr_res = results["pr_result"]
            if pr_res.get("pr_created"):
                table.add_row("GitHub PR", f"[bold green]{pr_res['pr_url']}[/bold green]")
                table.add_row("PR Branch", pr_res.get("branch", ""))
            else:
                table.add_row("GitHub PR", pr_res.get("message", "No PR created"))

        console.print(table)

    except Exception as e:
        console.print(f"[bold red]Sync Failed:[/bold red] {e}")
        raise click.Abort()


@main.command()
@click.option("--project", "-p", default=lambda: os.environ.get("LOOKER_PROJECT_ID", ""), help="Looker project ID (defaults to LOOKER_PROJECT_ID env var)")
def validate(project: str):
    """Run LookML validation against a Looker project using looker-cli."""
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
