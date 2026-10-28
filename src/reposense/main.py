"""CLI entry point for RepoSense."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from reposense import __version__
from reposense.config import settings

console = Console()


@click.group()
@click.version_option(version=__version__, prog_name="reposense")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
def cli(verbose: bool) -> None:
    """RepoSense — get from a GitHub issue to your first merged PR."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING,
                        format="%(name)s %(levelname)s: %(message)s")


@cli.command()
def check() -> None:
    """Verify that all required API keys are configured."""
    missing = settings.validate()
    if missing:
        console.print(Panel("\n".join(f"  [red]✗[/red] {k} is not set" for k in missing),
                            title="[bold red]Missing Configuration[/bold red]", expand=False))
        console.print("\nCopy [cyan].env.example[/cyan] to [cyan].env[/cyan] and fill in your keys.")
        raise SystemExit(1)
    console.print(Panel(f"  [green]✓[/green] GITHUB_TOKEN\n  [green]✓[/green] DEEPSEEK_API_KEY ({settings.deepseek_model})",
                        title="[bold green]Configuration OK[/bold green]", expand=False))


def _print_event(e: dict) -> None:
    t = e["type"]
    if t == "start":
        i = e["issue"]
        console.print(Panel(f"[bold]#{i['number']} {i['title']}[/bold]\n{i['url']}",
                            title=f"{e['repo']} · {e['model']}", expand=False))
    elif t == "tool_call":
        args = ", ".join(f"{k}={v!r}" for k, v in e["args"].items() if k not in ("changes", "summary"))
        console.print(f"[dim]{e['step']:>2}[/dim] [cyan]{e['name']}[/cyan]({args[:110]})"
                      + (f"  [dim]— {e['reason']}[/dim]" if e.get("reason") else ""))
    elif t == "tool_result":
        color = "green" if e["ok"] else "red"
        console.print(f"     [{color}]→ {e['summary']}[/{color}]")
    elif t == "plan_rejected":
        console.print("[yellow]   plan rejected by verifier:[/yellow]")
        for err in e["errors"]:
            console.print(f"[yellow]     - {err}[/yellow]")


def _print_plan(result: dict) -> None:
    plan = result["plan"]
    run = result["run"]
    if not plan:
        console.print(f"[red]No plan produced ({run['stop_reason']}).[/red]")
        return
    console.print(Panel(plan["summary"], title="Summary", expand=False))
    table = Table(title="Changes", show_lines=True)
    for col in ("Where", "What", "Why", "Conf."):
        table.add_column(col)
    for c in plan["changes"]:
        table.add_row(f"{c['file']}:{c['start_line']}-{c['end_line']}", c["what"], c["why"], c["confidence"])
    console.print(table)
    for p in plan.get("related_prs", []):
        console.print(f"[bold]PR #{p['number']}[/bold] {p['title']} — {p['relevance']}")
        for n in p.get("review_notes", []):
            console.print(f"   · {n}")
    for key, label in (("tests", "Tests"), ("conventions", "Conventions"), ("open_questions", "Open questions")):
        for item in plan.get(key) or []:
            text = item if isinstance(item, str) else " ".join(str(item.get(k, "")) for k in ("command", "file", "why")).strip()
            console.print(f"[bold]{label}:[/bold] {text}")
    for r in plan.get("reviewers", []):
        console.print(f"[bold]Reviewer:[/bold] @{r['login']} — {r['why']}")
    if plan.get("dropped"):
        console.print(f"[yellow]Dropped by verifier: {plan['dropped']}[/yellow]")
    console.print(f"[dim]{run['steps']} rounds · {run['tool_calls']} tool calls · {run['tokens']:,} tokens "
                  f"({run.get('cached_tokens', 0):,} cached) · "
                  f"{run['seconds']}s · {run['stop_reason']}[/dim]")


@cli.command()
@click.argument("issue_url")
@click.option("--repo-path", type=click.Path(exists=True, file_okay=False),
              help="Use an existing local checkout instead of cloning.")
@click.option("--no-history", is_flag=True, help="Disable project-history tools (baseline mode).")
@click.option("--max-steps", default=25, show_default=True)
@click.option("--lang", default="Chinese", show_default=True, help="Language of the plan text.")
@click.option("--out", type=click.Path(dir_okay=False), help="Write the result JSON to this file.")
@click.option("--jsonl", is_flag=True, help="Stream events as JSON lines on stdout (used by the VS Code extension).")
def plan(issue_url: str, repo_path: str | None, no_history: bool, max_steps: int, lang: str,
         out: str | None, jsonl: bool) -> None:
    """Explore the code and its history, then produce a verified contribution plan for ISSUE_URL."""
    from reposense.agent.contribution import AgentConfig, ContributionAgent
    from reposense.core.workspace import Workspace, parse_issue_url

    def emit_json(e: dict) -> None:
        sys.stdout.write(json.dumps(e, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    emit = emit_json if jsonl else _print_event
    try:
        missing = settings.validate()
        if missing:
            raise RuntimeError(f"Missing configuration: {', '.join(missing)}")
        repo, number = parse_issue_url(issue_url)
        ws = Workspace.from_path(repo_path) if repo_path else Workspace.clone(repo)
        if ws.repo.lower() != repo.lower():
            raise RuntimeError(f"Issue belongs to {repo}, but the workspace is {ws.repo}")
        agent = ContributionAgent(ws, AgentConfig(use_history=not no_history, max_steps=max_steps, lang=lang), emit)
        result = agent.run(number)
    except Exception as exc:
        if jsonl:
            emit_json({"type": "error", "message": str(exc)})
            raise SystemExit(1)
        console.print(f"[red]Error:[/red] {exc}")
        raise SystemExit(1)

    if out:
        Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not jsonl:
        _print_plan(result)


@cli.command()
def serve() -> None:
    """Run the JSON-RPC backend on stdio (started by the VS Code extension)."""
    from reposense.server import main as serve_main

    serve_main()


@cli.command()
@click.argument("method")
@click.option("--repo-path", type=click.Path(exists=True, file_okay=False), default=".", show_default=True)
@click.option("--params", default="{}", help='JSON object, e.g. \'{"path": "src/app.py"}\'.')
@click.option("--lang", default="Chinese", show_default=True)
def call(method: str, repo_path: str, params: str, lang: str) -> None:
    """Call one backend method directly and print its JSON result (for debugging)."""
    from reposense.server import METHODS, Session

    err = Console(stderr=True)
    session = Session(lambda m, p: err.print(f"[dim]{m}: {p}[/dim]", highlight=False))
    if method not in ("repo.clone", "config.check"):
        session.repo_open(repo_path, lang=lang)
    if method not in METHODS:
        raise click.BadParameter(f"Unknown method. Choose from: {', '.join(METHODS)}")
    result = getattr(session, METHODS[method])(**json.loads(params))
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    cli()
