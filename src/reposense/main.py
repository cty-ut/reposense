"""CLI entry point for RepoSense."""

from __future__ import annotations

import logging
import time

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.status import Status

from reposense import __version__
from reposense.config import settings

console = Console()


@click.group()
@click.version_option(version=__version__, prog_name="reposense")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
def cli(verbose: bool) -> None:
    """RepoSense — Understand any open-source project in minutes."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(name)s %(levelname)s: %(message)s")


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

    lines = ["  [green]✓[/green] GITHUB_TOKEN"]
    if settings.google_api_key:
        lines.append("  [green]✓[/green] GOOGLE_API_KEY")
    else:
        lines.append("  [dim]○[/dim] GOOGLE_API_KEY (not set)")
    if settings.poe_api_key:
        lines.append(f"  [green]✓[/green] POE_API_KEY (fallback: {settings.poe_model})")
    else:
        lines.append("  [dim]○[/dim] POE_API_KEY (not set, no fallback)")
    console.print(
        Panel(
            "\n".join(lines),
            title="[bold green]Configuration OK[/bold green]",
            expand=False,
        )
    )


@cli.command()
@click.argument("repo_url")
def analyze(repo_url: str) -> None:
    """Analyze a GitHub repository and generate a panorama report."""
    missing = settings.validate()
    if missing:
        console.print(f"[red]Missing config: {', '.join(missing)}[/red]")
        raise SystemExit(1)

    from reposense.graph.builder import build_scan_graph

    graph = build_scan_graph()
    initial_state = {"repo_url": repo_url, "user_query": ""}

    console.print(f"\n[bold]Target:[/bold] {repo_url}\n")

    t0 = time.time()
    with Status("[bold cyan]Analyzing repository...[/bold cyan]", console=console):
        final_state = graph.invoke(initial_state)
    elapsed = time.time() - t0

    if final_state.get("error"):
        console.print(f"\n[red]Error:[/red] {final_state['error']}")
        raise SystemExit(1)

    report = final_state.get("report", "No report generated.")
    console.print()
    console.print(Markdown(report))

    from reposense.metrics import get_collector
    collector = get_collector()
    collector.github_api_calls = 0  # TODO: wire up from GitHubClient
    console.print(f"\n[dim]{collector.summary_line()}[/dim]")


@cli.command()
@click.argument("repo_url")
def ask(repo_url: str) -> None:
    """Start an interactive Q&A session about a GitHub repository."""
    missing = settings.validate()
    if missing:
        console.print(f"[red]Missing config: {', '.join(missing)}[/red]")
        raise SystemExit(1)

    import uuid
    from reposense.graph.qa_graph import QAEngine
    from reposense.graph.nodes.verifier import verify_answer, annotate_answer
    from reposense.graph.nodes.router import parse_repo_name
    from reposense.tools.github_api import GitHubClient
    from dataclasses import asdict

    repo_name = parse_repo_name(repo_url)
    if not repo_name:
        console.print(f"[red]Cannot parse repo from: {repo_url}[/red]")
        raise SystemExit(1)

    console.print(f"\n[bold]Repository:[/bold] {repo_name}")

    engine = QAEngine()

    with console.status("[bold cyan]Loading search index...[/bold cyan]"):
        with GitHubClient(token=settings.github_token) as client:
            meta = client.get_repo_meta(repo_name)
            tree_nodes = client.get_file_tree(repo_name)
            file_tree = [{"path": n.path, "type": n.type, "size": n.size} for n in tree_nodes]

        stats = engine.build_index(repo_name, file_tree)

    file_count = sum(1 for n in file_tree if n["type"] == "blob")
    if stats.get("cached"):
        console.print(
            f"[green]Cached index loaded:[/green] {stats.get('total_chunks', 0)} chunks "
            f"({file_count} files in repo)\n"
        )
    else:
        console.print(
            f"[green]Index built:[/green] {stats.get('total_chunks', 0)} chunks "
            f"from {file_count} files\n"
        )

    thread_id = str(uuid.uuid4())[:8]
    console.print("[dim]Type your questions (Ctrl+C to exit).[/dim]\n")

    meta_dict = asdict(meta)

    while True:
        try:
            question = console.input("[bold blue]You:[/bold blue] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye![/dim]")
            break

        if not question:
            continue
        if question.lower() in ("exit", "quit", "q"):
            console.print("[dim]Goodbye![/dim]")
            break

        with console.status("[bold cyan]Thinking...[/bold cyan]"):
            result = engine.ask(question, thread_id=thread_id, repo_meta=meta_dict)

        answer = result["answer"]
        verification = verify_answer(answer, file_tree)
        answer = annotate_answer(answer, verification)

        console.print()
        console.print(Markdown(answer))

        # Show stats
        stats_parts = [f"iterations={result['iterations']}"]
        if verification["total_refs"] > 0:
            stats_parts.append(
                f"refs={verification['valid_refs']}/{verification['total_refs']} valid"
            )
        console.print(f"\n[dim]({', '.join(stats_parts)})[/dim]\n")


@cli.command()
@click.argument("repo_url")
def contribute(repo_url: str) -> None:
    """Analyze issues and generate a contribution roadmap for a GitHub repository."""
    missing = settings.validate()
    if missing:
        console.print(f"[red]Missing config: {', '.join(missing)}[/red]")
        raise SystemExit(1)

    import re
    import uuid
    from reposense.graph.contribute import ContributeEngine
    from reposense.graph.nodes.router import parse_repo_name
    from reposense.graph.nodes.verifier import verify_answer, annotate_answer

    repo_name = parse_repo_name(repo_url)
    if not repo_name:
        console.print(f"[red]Cannot parse repo from: {repo_url}[/red]")
        raise SystemExit(1)

    console.print(f"\n[bold]Repository:[/bold] {repo_name}")

    engine = ContributeEngine()

    # Step 1: Fetch repo data
    with console.status("[bold cyan]Fetching repository metadata...[/bold cyan]"):
        meta = engine.fetch_repo_data(repo_name)
    console.print(f"[green]✓[/green] Repository: {meta.get('description', 'N/A')}")

    # Step 2: Fetch issues
    with console.status("[bold cyan]Fetching issues...[/bold cyan]"):
        issues = engine.fetch_issues()
    if not issues:
        console.print("[yellow]No open issues found in this repository.[/yellow]")
        return
    console.print(f"[green]✓[/green] Found {len(issues)} issues")

    # Step 3: Build code index
    with console.status("[bold cyan]Building code search index...[/bold cyan]"):
        stats = engine.build_index()
    cached = "loaded from cache" if stats.get("cached") else "built"
    console.print(f"[green]✓[/green] Index {cached}: {stats.get('total_chunks', 0)} chunks")

    # Step 4: Analyze issues with code association
    with console.status("[bold cyan]Analyzing issues (this may take a minute)...[/bold cyan]"):
        analyses = engine.analyze_issues()
    easy = sum(1 for a in analyses if a["difficulty"] == "easy")
    medium = sum(1 for a in analyses if a["difficulty"] == "medium")
    hard = sum(1 for a in analyses if a["difficulty"] == "hard")
    console.print(
        f"[green]✓[/green] Analyzed {len(analyses)} issues: "
        f"[green]{easy} easy[/green], [yellow]{medium} medium[/yellow], [red]{hard} hard[/red]"
    )

    # Step 5: Generate roadmap
    with console.status("[bold cyan]Generating contribution roadmap...[/bold cyan]"):
        roadmap = engine.generate_roadmap()

    console.print()
    console.print(Markdown(roadmap))

    from reposense.metrics import get_collector
    collector = get_collector()
    console.print(f"\n[dim]Stats: {collector.summary_line()}[/dim]")

    # Step 6: Interactive follow-up
    console.print(
        "\n[dim]You can now ask follow-up questions about any issue "
        "(e.g. 'Tell me more about issue #123'). Type 'q' to exit.[/dim]\n"
    )

    thread_id = str(uuid.uuid4())[:8]

    while True:
        try:
            question = console.input("[bold blue]You:[/bold blue] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye![/dim]")
            break

        if not question:
            continue
        if question.lower() in ("exit", "quit", "q"):
            console.print("[dim]Goodbye![/dim]")
            break

        issue_match = re.search(r"#?(\d+)", question)
        issue_num = int(issue_match.group(1)) if issue_match else None

        with console.status("[bold cyan]Thinking...[/bold cyan]"):
            if issue_num:
                result = engine.ask_about_issue(
                    issue_num, question, thread_id=thread_id,
                )
            else:
                result = engine.qa_engine.ask(
                    question, thread_id=thread_id, repo_meta=meta,
                )

        answer = result["answer"]
        file_tree = engine._file_tree
        verification = verify_answer(answer, file_tree)
        answer = annotate_answer(answer, verification)

        console.print()
        console.print(Markdown(answer))

        stats_parts = [f"iterations={result['iterations']}"]
        if verification["total_refs"] > 0:
            stats_parts.append(
                f"refs={verification['valid_refs']}/{verification['total_refs']} valid"
            )
        console.print(f"\n[dim]({', '.join(stats_parts)})[/dim]\n")


@cli.command("mcp-serve")
def mcp_serve() -> None:
    """Start a Model Context Protocol (MCP) server over stdio."""
    import asyncio
    from reposense.mcp_server import main as mcp_main
    console.print("[bold]Starting MCP server...[/bold]")
    asyncio.run(mcp_main())


if __name__ == "__main__":
    cli()
