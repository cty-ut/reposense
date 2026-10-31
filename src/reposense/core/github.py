"""GitHub REST API access: issues, pull requests, reviews, search.

`cutoff` (ISO timestamp) hides anything created after it. The evaluation uses the issue's
creation time so the agent cannot see the PR that actually fixed the issue.
"""

from __future__ import annotations

import httpx

from reposense.config import settings
from reposense.core.cache import JsonCache


def clip(text: str | None, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + " …(truncated)"


class GitHubClient:
    def __init__(self, repo: str, *, cutoff: str | None = None, token: str | None = None,
                 cache: JsonCache | None = None):
        self.repo = repo
        self.cutoff = cutoff
        self.cache = cache
        headers = {"Accept": "application/vnd.github+json"}
        if token or settings.github_token:
            headers["Authorization"] = f"Bearer {token or settings.github_token}"
        self.http = httpx.Client(base_url="https://api.github.com", headers=headers, timeout=20)

    # ------------------------------------------------------------- plumbing

    def _get(self, url: str, **params) -> dict | list:
        r = self.http.get(url, params=params or None)
        if r.status_code == 404:
            raise LookupError(f"Not found on GitHub: {url}")
        r.raise_for_status()
        return r.json()

    def _cached(self, namespace: str, key: str, compute):
        # Cutoff runs (evaluation) must not share cached results with normal runs.
        if self.cache is None or self.cutoff:
            return compute()
        return self.cache.get_or_compute(namespace, key, compute)

    def _after_cutoff(self, ts: str | None) -> bool:
        return bool(self.cutoff and ts and ts > self.cutoff)

    # ------------------------------------------------------------- issues & search

    def get_issue(self, number: int) -> dict:
        i = self._get(f"/repos/{self.repo}/issues/{number}")
        comments = self._get(f"/repos/{self.repo}/issues/{number}/comments", per_page=20) if i["comments"] else []
        comments = [c for c in comments if not self._after_cutoff(c["created_at"])]
        return {
            "number": i["number"], "title": i["title"], "state": i["state"],
            "author": i["user"]["login"], "created_at": i["created_at"],
            "labels": [lb["name"] for lb in i.get("labels", [])],
            "body": clip(i.get("body"), 4000),
            "comments": [{"author": c["user"]["login"], "body": clip(c["body"], 800)} for c in comments],
            "url": i["html_url"],
        }

    def search(self, query: str, kind: str = "pr", state: str | None = None, limit: int = 8) -> list[dict]:
        q = f"repo:{self.repo} is:{'pr' if kind == 'pr' else 'issue'} {query}"
        if state:
            q += f" is:{state}"
        if self.cutoff:
            q += f" created:<{self.cutoff[:19]}"
        data = self._get("/search/issues", q=q, per_page=limit)
        return [{"number": it["number"], "title": it["title"], "state": it["state"],
                 "created_at": it["created_at"][:10], "url": it["html_url"],
                 "labels": [lb["name"] for lb in it.get("labels", [])], "comments": it.get("comments", 0)}
                for it in data.get("items", [])]

    # ------------------------------------------------------------- pull requests

    def get_pr(self, number: int) -> dict:
        def compute() -> dict:
            pr = self._get(f"/repos/{self.repo}/pulls/{number}")
            if self._after_cutoff(pr["created_at"]):
                return {"error": "not_available", "message": f"PR #{number} was created after the issue; hidden"}
            files = self._get(f"/repos/{self.repo}/pulls/{number}/files", per_page=30)
            reviews = self._get(f"/repos/{self.repo}/pulls/{number}/reviews", per_page=30)
            comments = self._get(f"/repos/{self.repo}/pulls/{number}/comments", per_page=50)
            return {
                "number": number, "title": pr["title"], "author": pr["user"]["login"],
                "state": "merged" if pr.get("merged_at") else pr["state"],
                "merged_at": (pr.get("merged_at") or "")[:10], "url": pr["html_url"],
                "body": clip(pr.get("body"), 1500),
                "files": [{"path": f["filename"], "additions": f["additions"], "deletions": f["deletions"],
                           "patch": clip(f.get("patch"), 1200)} for f in files[:15]],
                "reviews": [{"reviewer": r["user"]["login"], "state": r["state"], "body": clip(r["body"], 600)}
                            for r in reviews if r.get("body")],
                "review_comments": [{"reviewer": c["user"]["login"], "path": c["path"],
                                     "line": c.get("line") or c.get("original_line"), "body": clip(c["body"], 500)}
                                    for c in comments],
            }
        return self._cached("pr", str(number), compute)

    def pr_for_commit(self, sha: str) -> int | None:
        """The PR that introduced a commit (for repos that merge with merge commits or rebase)."""
        def compute():
            try:
                prs = self._get(f"/repos/{self.repo}/commits/{sha}/pulls")
            except (LookupError, httpx.HTTPError):
                return {"pr": None}
            merged = [p for p in prs if p.get("merged_at")] or prs
            return {"pr": merged[0]["number"] if merged else None}
        return self._cached("commit_pr", sha, compute)["pr"]

    def prs_for_commits(self, shas: list[str]) -> dict[str, int | None]:
        """`pr_for_commit` for many commits with one GraphQL request instead of one REST call each.

        Shares the `commit_pr` cache with `pr_for_commit`. Falls back to REST when GraphQL is unavailable
        (it needs a token) or fails.
        """
        found: dict[str, int | None] = {}
        missing = []
        for sha in dict.fromkeys(shas):
            hit = self.cache.get("commit_pr", sha) if self.cache is not None and not self.cutoff else None
            if hit is not None:
                found[sha] = hit["pr"]
            else:
                missing.append(sha)
        if not missing:
            return found
        try:
            fetched = self._graphql_commit_prs(missing)
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return {**found, **{sha: self.pr_for_commit(sha) for sha in missing}}
        for sha, pr in fetched.items():
            if self.cache is not None and not self.cutoff:
                self.cache.set("commit_pr", sha, {"pr": pr})
        return {**found, **fetched}

    def _graphql_commit_prs(self, shas: list[str]) -> dict[str, int | None]:
        owner, name = self.repo.split("/")
        # shas come from `git log`, so they are plain hex and safe to inline
        fields = "\n".join(
            f'c{i}: object(oid: "{sha}") {{ ... on Commit {{ associatedPullRequests(first: 5) '
            f'{{ nodes {{ number mergedAt }} }} }} }}' for i, sha in enumerate(shas))
        query = f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ {fields} }} }}"
        r = self.http.post("/graphql", json={"query": query, "variables": {"owner": owner, "name": name}})
        r.raise_for_status()
        repo = r.json()["data"]["repository"]
        out: dict[str, int | None] = {}
        for i, sha in enumerate(shas):
            nodes = ((repo.get(f"c{i}") or {}).get("associatedPullRequests") or {}).get("nodes") or []
            merged = [n for n in nodes if n.get("mergedAt")] or nodes  # same choice as pr_for_commit
            out[sha] = merged[0]["number"] if merged else None
        return out

    def close(self) -> None:
        self.http.close()
