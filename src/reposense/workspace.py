"""Local git workspace that the agent explores (a clone in the cache or the user's own checkout)."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

CACHE_DIR = Path(os.path.expanduser("~")) / ".cache" / "reposense" / "repos"

_REPO_RE = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
_ISSUE_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)")


def parse_issue_url(url: str) -> tuple[str, int]:
    """'https://github.com/pallets/click/issues/123' -> ('pallets/click', 123)."""
    m = _ISSUE_RE.search(url)
    if not m:
        raise ValueError(f"Not a GitHub issue URL: {url}")
    return f"{m.group(1)}/{m.group(2)}", int(m.group(3))


def git(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


@dataclass
class Workspace:
    repo: str  # "owner/name"
    root: Path

    @classmethod
    def from_path(cls, path: str | Path) -> "Workspace":
        root = Path(path).resolve()
        remote = git(root, "remote", "get-url", "origin", check=False).strip()
        m = _REPO_RE.search(remote)
        if not m:
            raise ValueError(f"{root} has no GitHub 'origin' remote")
        return cls(repo=f"{m.group(1)}/{m.group(2)}", root=root)

    @classmethod
    def clone(cls, repo: str) -> "Workspace":
        """Clone (or reuse) a blobless clone: full history for log/blame, file contents fetched lazily."""
        root = CACHE_DIR / repo.replace("/", "__")
        if not (root / ".git").exists():
            root.parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(
                ["git", "clone", "--filter=blob:none", "--quiet", f"https://github.com/{repo}.git", str(root)],
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"git clone failed: {proc.stderr.strip()[:300]}")
        return cls(repo=repo, root=root)

    def checkout(self, ref: str) -> None:
        """Detach at a commit (used by evaluation to reproduce the code before a fix)."""
        git(self.root, "checkout", "--quiet", "--force", "--detach", ref)

    def head(self) -> str:
        return git(self.root, "rev-parse", "HEAD").strip()

    def resolve(self, rel_path: str) -> Path:
        """Resolve a repo-relative path, refusing anything outside the workspace."""
        p = (self.root / rel_path.strip().lstrip("/\\")).resolve()
        if p != self.root and self.root not in p.parents:
            raise ValueError(f"Path escapes the repository: {rel_path}")
        return p

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()
