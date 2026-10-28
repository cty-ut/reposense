"""Project-history tools: who changed this code, in which PR, and what reviewers said.

This is what a general coding agent rarely looks at, and what a first-time contributor lacks most.

`cutoff` (ISO timestamp) hides everything created after it. Evaluation sets it to the issue's
creation time so the agent cannot peek at the PR that actually fixed the issue.
"""

from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache

import httpx

from reposense.config import settings
from reposense.workspace import Workspace, git

_PR_IN_SUBJECT = re.compile(r"\(#(\d+)\)\s*$|Merge pull request #(\d+)")
_SEP = "\x1f"


def _pr_from_subject(subject: str) -> int | None:
    m = _PR_IN_SUBJECT.search(subject)
    return int(m.group(1) or m.group(2)) if m else None


def _clip(text: str | None, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + " …(truncated)"


# ---------------------------------------------------------------- git (local)

def git_log(ws: Workspace, path: str, max_count: int = 10) -> dict:
    """Recent commits touching a file or directory."""
    out = git(ws.root, "log", f"-n{max(1, min(int(max_count), 30))}", "--date=short",
              f"--format=%H{_SEP}%an{_SEP}%ad{_SEP}%s", "--", path, check=False)
    commits = []
    for ln in out.splitlines():
        sha, author, date, subject = ln.split(_SEP, 3)
        commits.append({"sha": sha[:10], "author": author, "date": date, "subject": subject[:160],
                        "pr": _pr_from_subject(subject)})
    if not commits:
        return {"error": "not_found", "message": f"No history for {path}"}
    return {"path": path, "commits": commits}


def git_blame(ws: Workspace, path: str, start: int, end: int) -> dict:
    """Which commit/PR last touched each block of lines in [start, end]."""
    start, end = max(1, int(start)), max(1, int(end))
    if end - start > 200:
        end = start + 200
    out = git(ws.root, "blame", "--line-porcelain", f"-L{start},{end}", "--", path, check=False)
    if not out:
        return {"error": "not_found", "message": f"Cannot blame {path}:{start}-{end}"}

    blocks: list[dict] = []
    info: dict = {}
    line_no = start - 1
    for ln in out.splitlines():
        head = ln.split(" ")
        if len(head) >= 3 and len(head[0]) == 40 and head[1].isdigit():
            info = {"sha": head[0][:10]}
            line_no = int(head[2])
        elif ln.startswith("author "):
            info["author"] = ln[7:]
        elif ln.startswith("author-time "):
            info["date"] = datetime.fromtimestamp(int(ln[12:])).strftime("%Y-%m-%d")
        elif ln.startswith("summary "):
            info["summary"] = ln[8:][:160]
        elif ln.startswith("\t"):
            if blocks and blocks[-1]["sha"] == info["sha"] and blocks[-1]["end"] == line_no - 1:
                blocks[-1]["end"] = line_no
            else:
                blocks.append({"start": line_no, "end": line_no, **info,
                               "pr": _pr_from_subject(info.get("summary", ""))})
    return {"path": path, "blocks": blocks}


# ---------------------------------------------------------------- GitHub (remote)

class GitHubHistory:
    def __init__(self, repo: str, cutoff: str | None = None, token: str | None = None):
        self.repo = repo
        self.cutoff = cutoff
        headers = {"Accept": "application/vnd.github+json"}
        if token or settings.github_token:
            headers["Authorization"] = f"Bearer {token or settings.github_token}"
        self.http = httpx.Client(base_url="https://api.github.com", headers=headers, timeout=20)

    def _get(self, url: str, **params) -> dict | list:
        r = self.http.get(url, params=params or None)
        if r.status_code == 404:
            raise LookupError("not_found")
        r.raise_for_status()
        return r.json()

    def _after_cutoff(self, ts: str | None) -> bool:
        return bool(self.cutoff and ts and ts > self.cutoff)

    def get_issue(self, number: int) -> dict:
        i = self._get(f"/repos/{self.repo}/issues/{number}")
        comments = self._get(f"/repos/{self.repo}/issues/{number}/comments", per_page=20) if i["comments"] else []
        # evaluation: only what was visible when the issue was opened
        comments = [c for c in comments if not self._after_cutoff(c["created_at"])]
        return {
            "number": i["number"], "title": i["title"], "state": i["state"],
            "author": i["user"]["login"], "created_at": i["created_at"],
            "labels": [lb["name"] for lb in i.get("labels", [])],
            "body": _clip(i.get("body"), 4000),
            "comments": [{"author": c["user"]["login"], "body": _clip(c["body"], 800)} for c in comments],
            "url": i["html_url"],
        }

    def search(self, query: str, kind: str = "pr") -> dict:
        q = f"repo:{self.repo} is:{'pr' if kind == 'pr' else 'issue'} {query}"
        if self.cutoff:
            q += f" created:<{self.cutoff[:19]}"
        data = self._get("/search/issues", q=q, per_page=8)
        return {"query": query, "kind": kind, "results": [
            {"number": it["number"], "title": it["title"], "state": it["state"],
             "created_at": it["created_at"][:10], "url": it["html_url"]}
            for it in data.get("items", [])
        ]}

    @lru_cache(maxsize=64)
    def get_pr(self, number: int) -> dict:
        pr = self._get(f"/repos/{self.repo}/pulls/{number}")
        if self._after_cutoff(pr["created_at"]):
            return {"error": "not_available", "message": f"PR #{number} was created after the issue; hidden in this run"}
        files = self._get(f"/repos/{self.repo}/pulls/{number}/files", per_page=30)
        reviews = self._get(f"/repos/{self.repo}/pulls/{number}/reviews", per_page=30)
        comments = self._get(f"/repos/{self.repo}/pulls/{number}/comments", per_page=30)
        return {
            "number": number, "title": pr["title"], "author": pr["user"]["login"],
            "state": "merged" if pr.get("merged_at") else pr["state"],
            "merged_at": (pr.get("merged_at") or "")[:10], "url": pr["html_url"],
            "body": _clip(pr.get("body"), 1500),
            "files": [{"path": f["filename"], "additions": f["additions"], "deletions": f["deletions"],
                       "patch": _clip(f.get("patch"), 1200)} for f in files[:15]],
            "reviews": [{"reviewer": r["user"]["login"], "state": r["state"], "body": _clip(r["body"], 600)}
                        for r in reviews if r.get("body")],
            "review_comments": [{"reviewer": c["user"]["login"], "path": c["path"],
                                 "line": c.get("line") or c.get("original_line"), "body": _clip(c["body"], 500)}
                                for c in comments],
        }

    def close(self) -> None:
        self.http.close()
