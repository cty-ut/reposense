"""Router node — determines user intent and parses the repo URL."""

from __future__ import annotations

import re

from reposense.graph.state import RepoSenseState

_GITHUB_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/\s#?]+)"
)


def parse_repo_name(url: str) -> str | None:
    """Extract 'owner/repo' from a GitHub URL or owner/repo string."""
    m = _GITHUB_RE.search(url)
    if m:
        return f"{m.group('owner')}/{m.group('repo')}"
    if re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", url.strip()):
        return url.strip()
    return None


def router_node(state: RepoSenseState) -> dict:
    """Decide what the user wants: scan / ask / contribute."""
    repo_url = state.get("repo_url", "")
    user_query = state.get("user_query", "")

    repo_name = parse_repo_name(repo_url) if repo_url else None

    if not repo_name:
        return {"error": f"Cannot parse GitHub repo from: {repo_url!r}"}

    query_lower = user_query.lower()

    if any(kw in query_lower for kw in ("贡献", "contribute", "issue", "good first issue", "help wanted")):
        intent = "contribute"
    elif user_query.strip():
        intent = "ask"
    else:
        intent = "scan"

    return {
        "repo_name": repo_name,
        "intent": intent,
    }
