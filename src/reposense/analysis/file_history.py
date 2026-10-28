"""History of one file: its latest commits, which current lines each commit left behind, and PR details.

Everything here comes from git and the GitHub API; no model calls.
"""

from __future__ import annotations

from reposense.core.git import BlameLine, Git, pr_from_subject
from reposense.core.github import GitHubClient
from reposense.core.workspace import Workspace


def _ranges(lines: list[int]) -> list[list[int]]:
    """[3, 4, 5, 9] -> [[3, 5], [9, 9]]"""
    out: list[list[int]] = []
    for n in sorted(lines):
        if out and out[-1][1] == n - 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return out


def _pr_number(sha: str, subject: str, gh: GitHubClient | None) -> int | None:
    pr = pr_from_subject(subject)
    if pr is None and gh is not None:
        try:
            pr = gh.pr_for_commit(sha)
        except Exception:  # GitHub unavailable: history still works without PR links
            pr = None
    return pr


def file_history(ws: Workspace, path: str, gh: GitHubClient | None, limit: int = 10) -> dict:
    """Latest `limit` commits of `path`, each with the line ranges it still owns in the current file."""
    git = Git(ws.root)
    if ws.read_text(path) is None:
        raise FileNotFoundError(path)
    commits = git.file_log(path, limit)
    blame: list[BlameLine] = git.blame(path)
    owned: dict[str, list[int]] = {}
    for b in blame:
        owned.setdefault(b.sha, []).append(b.line)

    recent = {c.sha for c in commits}
    items = []
    for i, c in enumerate(commits):
        pr = _pr_number(c.sha, c.subject, gh)
        lines = owned.get(c.sha, [])
        items.append({
            "sha": c.sha, "short": c.sha[:8], "author": c.author, "email": c.email, "date": c.date,
            "subject": c.subject, "additions": c.additions, "deletions": c.deletions,
            "pr": {"number": pr, "url": f"https://github.com/{ws.repo}/pull/{pr}"} if pr else None,
            "url": f"https://github.com/{ws.repo}/commit/{c.sha}",
            "ranges": _ranges(lines), "owned_lines": len(lines), "index": i,
        })
    return {
        "path": path, "total_lines": len(blame), "limit": limit, "commits": items,
        "older_lines": sum(1 for b in blame if b.sha not in recent),
    }


def commit_detail(ws: Workspace, sha: str, path: str, gh: GitHubClient | None) -> dict:
    """Diff of `path` in commit `sha`, plus the PR description and review feedback on this file."""
    git = Git(ws.root)
    commit = next((c for c in git.log(path, 200) if c.sha.startswith(sha)), None)
    detail = {
        "sha": sha, "path": path, "diff": git.file_diff(sha, path),
        "body": git.commit_body(sha), "subject": commit.subject if commit else "",
        "author": commit.author if commit else "", "date": commit.date if commit else "",
        "url": f"https://github.com/{ws.repo}/commit/{sha}", "pr": None,
    }
    pr_number = _pr_number(sha, detail["subject"], gh)
    if pr_number and gh is not None:
        try:
            pr = gh.get_pr(pr_number)
        except Exception as e:
            detail["pr_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        else:
            if "error" not in pr:
                detail["pr"] = {
                    "number": pr["number"], "title": pr["title"], "url": pr["url"], "author": pr["author"],
                    "state": pr["state"], "merged_at": pr["merged_at"], "body": pr["body"],
                    "reviews": pr["reviews"],
                    # inline review comments on this file first, then the rest of the PR
                    "review_comments": sorted(pr["review_comments"], key=lambda c: c["path"] != path),
                    "files": [f["path"] for f in pr["files"]],
                }
    return detail
