"""A local git checkout of a GitHub repository: locate it, or clone it."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from reposense.core.git import run

CACHE_DIR = Path(os.path.expanduser("~")) / ".cache" / "reposense" / "repos"

_REPO_RE = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
_ISSUE_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)")
_PROGRESS_RE = re.compile(r"(Receiving objects|Resolving deltas|Updating files):\s+(\d+)%")


def parse_repo_url(url: str) -> str:
    """'https://github.com/pallets/click' (or .git / SSH form) -> 'pallets/click'."""
    m = _REPO_RE.search(url.strip())
    if not m:
        raise ValueError(f"Not a GitHub repository URL: {url}")
    return f"{m.group(1)}/{m.group(2)}"


def parse_issue_url(url: str) -> tuple[str, int]:
    """'https://github.com/pallets/click/issues/123' -> ('pallets/click', 123)."""
    m = _ISSUE_RE.search(url)
    if not m:
        raise ValueError(f"Not a GitHub issue URL: {url}")
    return f"{m.group(1)}/{m.group(2)}", int(m.group(3))


@dataclass
class Workspace:
    repo: str  # "owner/name"
    root: Path

    @classmethod
    def from_path(cls, path: str | Path) -> "Workspace":
        root = Path(path).resolve()
        remote = run(root, "remote", "get-url", "origin", check=False).strip()
        m = _REPO_RE.search(remote)
        if not m:
            raise ValueError(f"{root} has no GitHub 'origin' remote")
        return cls(repo=f"{m.group(1)}/{m.group(2)}", root=root)

    @classmethod
    def clone_to(cls, url: str, parent: str | Path,
                 on_progress: Callable[[str, int], None] | None = None) -> "Workspace":
        """Full clone into parent/<name>; reuse it if that folder is already a clone of the same repo."""
        repo = parse_repo_url(url)
        root = Path(parent).expanduser().resolve() / repo.split("/")[1]
        if (root / ".git").exists():
            ws = cls.from_path(root)
            if ws.repo.lower() != repo.lower():
                raise ValueError(f"{root} already contains a different repository ({ws.repo})")
            return ws
        root.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            ["git", "clone", "--progress", f"https://github.com/{repo}.git", str(root)],
            stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
        )
        tail = ""
        assert proc.stderr is not None
        for chunk in iter(lambda: proc.stderr.read(256), ""):  # git rewrites progress lines with \r
            tail = (tail + chunk)[-2000:]
            for m in _PROGRESS_RE.finditer(chunk):
                if on_progress:
                    on_progress(m.group(1), int(m.group(2)))
        if proc.wait() != 0:
            raise RuntimeError(f"git clone failed: {tail.strip()[-300:]}")
        return cls(repo=repo, root=root)

    @classmethod
    def clone(cls, repo: str) -> "Workspace":
        """Blobless clone into the cache (used by the CLI when no local checkout is given)."""
        root = CACHE_DIR / repo.replace("/", "__")
        if not (root / ".git").exists():
            root.parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(
                ["git", "clone", "--filter=blob:none", "--quiet", f"https://github.com/{repo}.git", str(root)],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"git clone failed: {proc.stderr.strip()[:300]}")
        return cls(repo=repo, root=root)

    def checkout(self, ref: str) -> None:
        """Detach at a commit (used by evaluation to reproduce the code before a fix)."""
        run(self.root, "checkout", "--quiet", "--force", "--detach", ref)

    def head(self) -> str:
        return run(self.root, "rev-parse", "HEAD").strip()

    def resolve(self, rel_path: str) -> Path:
        """Resolve a repo-relative path, refusing anything outside the workspace."""
        p = (self.root / rel_path.strip().lstrip("/\\")).resolve()
        if p != self.root and self.root not in p.parents:
            raise ValueError(f"Path escapes the repository: {rel_path}")
        return p

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    def read_text(self, rel_path: str) -> str | None:
        p = self.resolve(rel_path)
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
