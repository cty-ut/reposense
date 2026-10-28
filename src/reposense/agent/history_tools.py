"""Project-history tools for the agent: git log / blame shaped as tool results.

GitHub lookups (PRs, issues, search) go through `core.github.GitHubClient` directly.
"""

from __future__ import annotations

from reposense.core.git import Git, group_blocks
from reposense.core.workspace import Workspace


def git_log(ws: Workspace, path: str, max_count: int = 10) -> dict:
    """Recent commits touching a file or directory."""
    commits = Git(ws.root).log(path, max(1, min(int(max_count), 30)))
    if not commits:
        return {"error": "not_found", "message": f"No history for {path}"}
    return {"path": path, "commits": [{"sha": c.sha[:10], "author": c.author, "date": c.date,
                                       "subject": c.subject[:160], "pr": c.pr} for c in commits]}


def git_blame(ws: Workspace, path: str, start: int, end: int) -> dict:
    """Which commit/PR last touched each block of lines in [start, end]."""
    start, end = max(1, int(start)), max(1, int(end))
    end = min(end, start + 200)
    lines = Git(ws.root).blame(path, start, end)
    if not lines:
        return {"error": "not_found", "message": f"Cannot blame {path}:{start}-{end}"}
    blocks = group_blocks(lines)
    for b in blocks:
        b["sha"] = b["sha"][:10]
        b["summary"] = b["summary"][:160]
    return {"path": path, "blocks": blocks}
