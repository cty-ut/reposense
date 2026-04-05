"""CLI entry point for RepoSense."""

from __future__ import annotations

import click
from rich.console import Console
from rich.panel import Panel

from reposense import __version__
from reposense.config import settings

console = Console()


@click.group()
@click.version_option(version=__version__, prog_name="reposense")
def cli() -> None:
    """RepoSense — Understand any open-source project in minutes."""


@cli.command()
def check() -> None:
    """Verify that all required API keys are configured."""
    missing = settings.validate()
    if missing:
        console.print(
            Panel(
                "\n".join(f"  [red]✗[/red] {k} is not set" for k in missing),
                title="[bold red]Missing Configuration[/bold red]",
                expand=False,
            )
        )
        console.print("\nCopy [cyan].env.example[/cyan] to [cyan].env[/cyan] and fill in your keys.")
        raise SystemExit(1)

    console.print(
        Panel(
            "  [green]✓[/green] GITHUB_TOKEN\n"
            "  [green]✓[/green] GOOGLE_API_KEY",
            title="[bold green]Configuration OK[/bold green]",
            expand=False,
        )
    )


@cli.command()
@click.argument("repo_url")
def analyze(repo_url: str) -> None:
    """Analyze a GitHub repository (placeholder — Sprint 2)."""
    console.print(f"[bold]Target:[/bold] {repo_url}")
    console.print("[dim]Full analysis will be implemented in Sprint 2.[/dim]")


if __name__ == "__main__":
    cli()
